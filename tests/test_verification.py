"""验证码基础的临时库集成测试：迁移、事务、并发、时间和发送失败。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from core.verification import Context, Limits, VerificationError, VerificationStore


@pytest.fixture
def env(tmp_path):
    clock = [1800000000]
    store = VerificationStore(tmp_path / "test.db", b"x" * 32, clock=lambda: clock[0])
    store.migrate()
    ctx = Context("register_email", "alice@example.com", "visitor", "flow-one")
    return store, ctx, clock


def sent(store, ctx, ip="127.0.0.1"):
    r = store.reserve(ctx, ip)
    assert store.finish(r.request_id, True)
    return r


def test_migration_preserves_synthetic_legacy_data_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, password_hash TEXT)")
        db.execute("INSERT INTO users VALUES ('old', 'unchanged')")
    s = VerificationStore(path, b"x" * 32)
    s.migrate()
    s.migrate()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM users").fetchall() == [("old", "unchanged")]
        assert db.execute("SELECT COUNT(*) FROM security_emails").fetchone()[0] == 0
        db.execute("INSERT INTO security_emails VALUES ('a','one@example.com',1,1)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO security_emails VALUES ('b','one@example.com',1,1)")


def test_success_and_one_time_consume(env):
    s, ctx, _ = env
    r = sent(s, ctx)
    grant = s.verify(r.request_id, r.code, ctx)
    assert r.code not in repr(r)
    with sqlite3.connect(s.path) as db:
        rows = repr(db.execute("SELECT * FROM verification_requests").fetchall())
        assert r.code not in rows
        assert grant not in repr(db.execute("SELECT * FROM verification_grants").fetchall())
    assert s.consume(grant, ctx, lambda db: "done") == "done"
    with pytest.raises(VerificationError):
        s.consume(grant, ctx, lambda db: "again")


@pytest.mark.parametrize(
    "change",
    [
        {"purpose": "reset_password"},
        {"address": "other@example.com"},
        {"subject": "other"},
        {"flow": "other"},
        {"auth_version": 1},
        {"binding_version": 1},
    ],
)
def test_context_and_versions_cannot_be_reused(env, change):
    s, ctx, _ = env
    r = sent(s, ctx)
    changed = replace(ctx, **change)
    with pytest.raises(VerificationError):
        s.verify(r.request_id, r.code, changed)
    grant = s.verify(r.request_id, r.code, ctx)
    with pytest.raises(VerificationError):
        s.consume(grant, changed, lambda db: None)


def test_wrong_attempts_commit_and_lock(env):
    s, ctx, _ = env
    r = sent(s, ctx)
    wrong = "999999" if r.code != "999999" else "000000"
    for _ in range(5):
        with pytest.raises(VerificationError):
            s.verify(r.request_id, wrong, ctx)
    with pytest.raises(VerificationError):
        s.verify(r.request_id, r.code, ctx)
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT attempts FROM verification_requests").fetchone()[0] == 5


def test_expiration_boundaries(env):
    s, ctx, clock = env
    r = sent(s, ctx)
    clock[0] += 600
    with pytest.raises(VerificationError):
        s.verify(r.request_id, r.code, ctx)
    r = sent(s, ctx)
    grant = s.verify(r.request_id, r.code, ctx)
    clock[0] += 300
    with pytest.raises(VerificationError):
        s.consume(grant, ctx, lambda db: None)


def test_failed_resend_and_late_completion(env):
    s, ctx, clock = env
    old = sent(s, ctx)
    clock[0] += 60
    pending = s.reserve(ctx, "127.0.0.1")
    assert s.finish(pending.request_id, False)
    with pytest.raises(VerificationError):
        s.verify(pending.request_id, pending.code, ctx)
    assert not s.finish(pending.request_id, True)
    grant = s.verify(old.request_id, old.code, ctx)
    clock[0] += 60
    late = s.reserve(ctx, "127.0.0.1")
    clock[0] += 60
    new = s.reserve(ctx, "127.0.0.1")
    assert not s.finish(late.request_id, True)
    assert s.finish(new.request_id, True)
    with pytest.raises(VerificationError):
        s.consume(grant, ctx, lambda db: None)


def test_send_runs_outside_transaction_and_failure_never_activates(env):
    s, ctx, _ = env

    def sender(address, code, purpose):
        with sqlite3.connect(s.path, timeout=0.1) as db:
            db.execute("BEGIN IMMEDIATE")
        raise TimeoutError()

    with pytest.raises(VerificationError):
        s.send(ctx, "127.0.0.1", sender)
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT state FROM verification_requests").fetchone()[0] == "failed"


def test_concurrent_reserve_and_verify_have_single_winner(env):
    s, ctx, _ = env

    def reserve(_):
        try:
            return s.reserve(ctx, "127.0.0.1")
        except VerificationError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [r for r in pool.map(reserve, range(4)) if r]
    assert len(winners) == 1
    r = winners[0]
    s.finish(r.request_id, True)

    def verify(_):
        try:
            return s.verify(r.request_id, r.code, ctx)
        except VerificationError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        grants = [g for g in pool.map(verify, range(4)) if g]
    assert len(grants) == 1


def test_consume_is_atomic_with_business_write_and_concurrent(env):
    s, ctx, _ = env
    r = sent(s, ctx)
    grant = s.verify(r.request_id, r.code, ctx)
    with sqlite3.connect(s.path) as db:
        db.execute("CREATE TABLE effects (value TEXT)")

    def fail(db):
        db.execute("INSERT INTO effects VALUES ('must rollback')")
        raise ValueError("synthetic failure")

    with pytest.raises(ValueError):
        s.consume(grant, ctx, fail)
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0

    def consume(_):
        try:
            s.consume(grant, ctx, lambda db: db.execute("INSERT INTO effects VALUES ('once')"))
            return True
        except VerificationError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(consume, range(4))) == 1
    with sqlite3.connect(s.path) as db:
        assert db.execute("SELECT value FROM effects").fetchall() == [("once",)]


def test_rate_limits_persist_after_reopen(env):
    s, ctx, clock = env
    for i in range(10):
        r = s.reserve(replace(ctx, flow=str(i)), "127.0.0.1")
        s.finish(r.request_id, False)
        clock[0] += 60
    reopened = VerificationStore(s.path, b"x" * 32, clock=lambda: clock[0])
    with pytest.raises(VerificationError):
        reopened.reserve(ctx, "127.0.0.2")


@pytest.mark.parametrize("limit", ["ip_short", "actor_short", "global_daily"])
def test_other_budgets_are_enforced(env, limit):
    s, ctx, clock = env
    s.limits = replace(Limits(), **{limit: 1})
    sent(s, ctx)
    clock[0] += 60
    with pytest.raises(VerificationError):
        s.reserve(replace(ctx, address="bob@example.com"), "127.0.0.1")


@pytest.mark.parametrize("address", ["bad", "a@b.com,c@d.com", "a@b.com\r\nBcc:x@y.com"])
def test_bad_addresses_never_create_requests(env, address):
    s, ctx, _ = env
    with pytest.raises(VerificationError):
        s.reserve(replace(ctx, address=address), "127.0.0.1")


def test_email_domain_normalization_and_successful_resend(env):
    s, ctx, clock = env
    ctx = replace(ctx, address="alice@EXAMPLE.COM")
    r = sent(s, ctx)
    clock[0] += 60
    new = sent(s, replace(ctx, address="alice@example.com"))
    with pytest.raises(VerificationError):
        s.verify(r.request_id, r.code, ctx)
    assert s.verify(new.request_id, new.code, ctx)


def test_bound_grant_requires_transactional_current_state(env):
    s, ctx, _ = env
    ctx = replace(ctx, purpose="reset_password", subject="synthetic-user")
    r = sent(s, ctx)
    grant = s.verify(r.request_id, r.code, ctx)
    with pytest.raises(VerificationError, match="事务内"):
        s.consume(grant, ctx, lambda db: None)
    with pytest.raises(VerificationError, match="已变化"):
        s.consume(
            grant,
            ctx,
            lambda db: pytest.fail("不得执行"),
            current_context=lambda db, expected: replace(expected, auth_version=1),
        )
    assert s.consume(grant, ctx, lambda db: "ok", current_context=lambda db, expected: expected) == "ok"


def test_two_proofs_consumed_together_or_not_at_all(env):
    s, ctx, _ = env
    old_ctx = replace(ctx, purpose="change_email_old", subject="synthetic-user")
    new_ctx = replace(ctx, purpose="change_email_new", address="new@example.com", subject="synthetic-user")
    old, new = sent(s, old_ctx), sent(s, new_ctx)
    old_grant = s.verify(old.request_id, old.code, old_ctx)
    new_grant = s.verify(new.request_id, new.code, new_ctx)

    def resolve(db, expected):
        return expected

    with pytest.raises(VerificationError):
        s.consume_many(
            [(old_grant, old_ctx), ("bad-proof", new_ctx)], lambda db: pytest.fail(), current_context=resolve
        )
    with pytest.raises(VerificationError):
        s.consume_many([(old_grant, old_ctx), (old_grant, old_ctx)], lambda db: pytest.fail(), current_context=resolve)
    assert (
        s.consume_many([(old_grant, old_ctx), (new_grant, new_ctx)], lambda db: "both", current_context=resolve)
        == "both"
    )
    with pytest.raises(VerificationError):
        s.consume(new_grant, new_ctx, lambda db: None, current_context=resolve)


def test_shanghai_daily_window_resets(env):
    from datetime import datetime, timedelta, timezone

    s, ctx, clock = env
    s.limits = replace(Limits(), daily=1)
    clock[0] = int(datetime(2026, 11, 19, 23, 59, 10, tzinfo=timezone(timedelta(hours=8))).timestamp())
    sent(s, ctx)
    clock[0] += 60
    assert sent(s, ctx)


def test_import_does_not_open_or_migrate_database(tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "must-not-exist.db"
    env = dict(os.environ, AGENT_HUB_DB=str(path))
    code = (
        "import os,sys; from pathlib import Path; import core.verification; "
        "assert not Path(os.environ['AGENT_HUB_DB']).exists(); "
        "assert 'core.account' not in sys.modules; assert 'core.session' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not path.exists()


def test_send_success_is_verifiable(env):
    s, ctx, _ = env
    captured = []

    def sender(address, code, purpose):
        captured.append((address, code, purpose))
        return {"ok": True}

    request_id = s.send(ctx, "127.0.0.1", sender)
    assert captured[0][0] == ctx.address and captured[0][2] == ctx.purpose
    assert s.verify(request_id, captured[0][1], ctx)


@pytest.mark.parametrize("change", [{"subject": "other"}, {"flow": "other"}, {"purpose": "register_email"}])
def test_combined_proofs_cannot_cross_accounts_or_flows(env, change):
    s, ctx, _ = env
    old_ctx = replace(ctx, purpose="change_email_old")
    new_ctx = replace(replace(ctx, purpose="change_email_new", address="new@example.com"), **change)
    with pytest.raises(VerificationError, match="同一账号"):
        s.consume_many(
            [("old-proof", old_ctx), ("new-proof", new_ctx)],
            lambda db: pytest.fail(),
            current_context=lambda db, expected: expected,
        )


def test_current_version_is_loaded_under_same_transaction(env):
    s, ctx, _ = env
    ctx = replace(ctx, purpose="reset_password", subject="synthetic-user")
    with sqlite3.connect(s.path) as db:
        db.execute("CREATE TABLE synthetic_accounts (id TEXT PRIMARY KEY, version INTEGER)")
        db.execute("INSERT INTO synthetic_accounts VALUES ('synthetic-user',0)")
    r = sent(s, ctx)
    grant = s.verify(r.request_id, r.code, ctx)
    with sqlite3.connect(s.path) as db:
        db.execute("UPDATE synthetic_accounts SET version=1")

    def resolve(db, expected):
        assert db.in_transaction
        version = db.execute("SELECT version FROM synthetic_accounts WHERE id=?", (expected.subject,)).fetchone()[0]
        return replace(expected, auth_version=version)

    with pytest.raises(VerificationError, match="已变化"):
        s.consume(grant, ctx, lambda db: pytest.fail("旧代数不允许写入"), current_context=resolve)
