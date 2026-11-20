"""设置安全邮箱接口：所有测试账号、SMTP 和数据库均隔离。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import main
from api.security_email import get_service
from core import account, config, quota, session
from core.security_email import SecurityEmailService
from core.verification import VerificationStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(path))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "AUTH_SECRET", "isolated-security-settings-secret-32bytes")
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", True)
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._HITS.clear()
    clock = [1800000000]
    store = VerificationStore(path, b"v" * 32, clock=lambda: clock[0])
    store.migrate()
    messages = []

    def sender(address, code, purpose):
        messages.append((address, code, purpose))
        return {"ok": True}

    service = SecurityEmailService(store, sender=sender, ready=lambda: True)
    main.app.dependency_overrides[get_service] = lambda: service
    clients = [TestClient(main.app, client=("127.0.0.1", 50000)), TestClient(main.app, client=("127.0.0.1", 50000))]
    for i, cli in enumerate(clients):
        # 合成升级前未绑定邮箱的历史账号，不再使用新注册接口准备历史状态。
        account.create_user(f"step4_user{i}", "Example_pass_9")
        response = cli.post("/api/auth/login/", json={"username": f"step4_user{i}", "password": "Example_pass_9"})
        assert response.status_code == 200, response.text
    yield service, clients, messages, clock
    main.app.dependency_overrides.pop(get_service, None)


def proof(cli, messages, purpose, address=None, flow=None):
    response = cli.post("/api/auth/email/code/send", json={"purpose": purpose, "address": address, "flow": flow})
    assert response.status_code == 200, response.text
    data = response.json()
    response = cli.post("/api/auth/email/code/verify", json={"request_id": data["request_id"], "code": messages[-1][1]})
    assert response.status_code == 200, response.text
    return response.json()["grant"], data["flow"]


def bind(cli, messages, address="first@example.com"):
    grant, _ = proof(cli, messages, "bind_email", address)
    response = cli.post("/api/auth/security-email/bind", json={"grant": grant})
    assert response.status_code == 200, response.text
    return grant


def test_historical_user_bind_and_masking(env):
    _, (cli, _), messages, _ = env
    assert cli.get("/api/auth/security-email").json()["bound"] is False
    bind(cli, messages)
    state = cli.get("/api/auth/security-email").json()
    assert state["bound"] is True and state["ready"] is True
    assert state["masked_email"] == "fi***@example.com"
    assert "first@example.com" not in str(state)
    assert cli.get("/api/auth/me/").status_code == 200


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/auth/email/code/send", {"purpose": "bind_email", "address": "a@b.com"}),
        ("/api/auth/email/code/verify", {"request_id": "x", "code": "000000"}),
        ("/api/auth/security-email/bind", {"grant": "x"}),
        ("/api/auth/security-email/change", {"old_grant": "x", "new_grant": "y"}),
    ],
)
def test_all_mutations_require_authentication(env, path, body):
    assert TestClient(main.app, client=("127.0.0.1", 50000)).post(path, json=body).status_code == 401


def test_bad_code_foreign_proof_and_no_delete(env):
    _, (alice, bob), messages, _ = env
    data = alice.post("/api/auth/email/code/send", json={"purpose": "bind_email", "address": "a@b.com"}).json()
    wrong = "999999" if messages[-1][1] != "999999" else "000000"
    assert (
        alice.post("/api/auth/email/code/verify", json={"request_id": data["request_id"], "code": wrong}).status_code
        == 400
    )
    assert (
        bob.post(
            "/api/auth/email/code/verify", json={"request_id": data["request_id"], "code": messages[-1][1]}
        ).status_code
        == 400
    )
    result = alice.post(
        "/api/auth/email/code/verify", json={"request_id": data["request_id"], "code": messages[-1][1]}
    ).json()
    assert bob.post("/api/auth/security-email/bind", json={"grant": result["grant"]}).status_code == 400
    assert alice.post("/api/auth/security-email/bind", json={"grant": result["grant"]}).status_code == 200
    assert alice.delete("/api/auth/security-email").status_code == 405


def test_change_old_destination_is_server_controlled_and_both_required(env):
    s, (cli, _), messages, clock = env
    bind(cli, messages)
    clock[0] += 60
    old, flow = proof(cli, messages, "change_email_old", "attacker@example.com")
    assert messages[-1][0] == "first@example.com"
    new, _ = proof(cli, messages, "change_email_new", "second@example.com", flow)
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old}).status_code == 422
    assert cli.post("/api/auth/security-email/change", json={"old_grant": new, "new_grant": old}).status_code == 400
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 200
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT address,binding_version FROM security_emails").fetchone() == ("second@example.com", 2)
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 400


def test_concurrent_unique_email_bind_has_one_winner(env):
    s, (alice, bob), messages, clock = env
    a, _ = proof(alice, messages, "bind_email", "shared@example.com")
    clock[0] += 60
    b, _ = proof(bob, messages, "bind_email", "shared@example.com")
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(
            pool.map(
                lambda pair: pair[0].post("/api/auth/security-email/bind", json={"grant": pair[1]}).status_code,
                [(alice, a), (bob, b)],
            )
        )
    assert sorted(codes) == [200, 409]
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM security_emails").fetchone()[0] == 1


def test_reminder_email_and_safety_are_independent(env):
    s, (cli, _), messages, _ = env
    bind(cli, messages)
    assert cli.delete("/api/mail").status_code == 404  # 目标分支不包含提醒邮件模块
    assert cli.delete("/api/auth/security-email").status_code == 405
    assert cli.get("/api/auth/security-email").json()["bound"] is True
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT address FROM security_emails").fetchone()[0] == "first@example.com"


def test_wrong_flow_and_expired_proofs_leave_old_binding(env):
    s, (cli, _), messages, clock = env
    bind(cli, messages)
    clock[0] += 60
    old, _ = proof(cli, messages, "change_email_old")
    new, _ = proof(cli, messages, "change_email_new", "next@example.com", "different-flow-123456")
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 400
    clock[0] += 300
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 400
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT address FROM security_emails").fetchone()[0] == "first@example.com"


def test_public_register_and_reset_purposes_not_opened_in_step4(env):
    _, (cli, _), _, _ = env
    for purpose in ("register_email", "reset_password"):
        assert cli.post("/api/auth/email/code/send", json={"purpose": purpose, "address": "a@b.com"}).status_code == 422


def test_disabled_service_and_schema_missing_never_migrate(env, monkeypatch):
    s, (cli, _), _, _ = env
    main.app.dependency_overrides.pop(get_service)
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", False)
    assert cli.get("/api/auth/security-email").status_code == 503
    assert TestClient(main.app).get("/api/auth/security-email").status_code == 401
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", True)
    with sqlite3.connect(s.store.path) as db:
        db.execute("ALTER TABLE security_emails RENAME TO synthetic_hold")
    assert cli.get("/api/auth/security-email").status_code == 503
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='security_emails'").fetchall() == []


def test_default_dependency_and_real_smtp_pipeline(env, monkeypatch):
    import re

    from fake_smtp import FakeSMTP

    _, (cli, _), _, _ = env
    main.app.dependency_overrides.pop(get_service)
    with FakeSMTP() as server:
        for name, value in {
            "MAIL_ENABLED": True,
            "SMTP_HOST": server.host,
            "SMTP_PORT": server.port,
            "SMTP_USER": "test@example.com",
            "SMTP_PASSWORD": "test-only-secret",
            "SMTP_FROM": "test@example.com",
            "SMTP_SECURITY": "none",
            "SMTP_TIMEOUT": 2,
        }.items():
            monkeypatch.setattr(config, name, value)
        response = cli.post(
            "/api/auth/email/code/send", json={"purpose": "bind_email", "address": "pipeline@example.com"}
        )
        assert response.status_code == 200, response.text
        assert server.wait_for(1)
        code = re.search(r"验证码是：([0-9]{6})", server.messages[0]["text"]).group(1)
        response = cli.post(
            "/api/auth/email/code/verify", json={"request_id": response.json()["request_id"], "code": code}
        )
        assert response.status_code == 200, response.text
        assert cli.post("/api/auth/security-email/bind", json={"grant": response.json()["grant"]}).status_code == 200
        assert cli.get("/api/auth/security-email").json()["masked_email"] == "pi***@example.com"


def test_duplicate_change_and_stale_version_keep_binding(env):
    s, (alice, bob), messages, clock = env
    bind(alice, messages)
    bind(bob, messages, "taken@example.com")
    assert (
        alice.post(
            "/api/auth/email/code/send",
            json={
                "purpose": "change_email_new",
                "address": "taken@example.com",
            },
        ).status_code
        == 409
    )
    clock[0] += 60
    old, flow = proof(alice, messages, "change_email_old")
    new, _ = proof(alice, messages, "change_email_new", "fresh@example.com", flow)
    with sqlite3.connect(s.store.path) as db:
        db.execute("UPDATE users SET token_valid_after=token_valid_after+1 WHERE username='step4_user0'")
    # 旧登录令牌先由鉴权层拒绝，既有绑定不变。
    assert alice.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 401
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT address FROM security_emails WHERE address='first@example.com'").fetchone()


def test_valid_change_proofs_expire_without_clearing_email(env):
    s, (cli, _), messages, clock = env
    bind(cli, messages)
    clock[0] += 60
    old, flow = proof(cli, messages, "change_email_old")
    new, _ = proof(cli, messages, "change_email_new", "valid-next@example.com", flow)
    clock[0] += 300
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 400
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT address FROM security_emails").fetchone()[0] == "first@example.com"


def test_client_cannot_supply_identity_or_versions(env):
    _, (cli, _), messages, _ = env
    grant, _ = proof(cli, messages, "bind_email", "safe@example.com")
    for extra in ({"user_id": "other"}, {"auth_version": 99}, {"binding_version": 99}):
        assert cli.post("/api/auth/security-email/bind", json={"grant": grant, **extra}).status_code == 422
    assert cli.post("/api/auth/security-email/bind", json={"grant": grant}).status_code == 200


def test_concurrent_change_consumes_pair_once(env):
    s, (cli, _), messages, clock = env
    bind(cli, messages)
    clock[0] += 60
    old, flow = proof(cli, messages, "change_email_old")
    new, _ = proof(cli, messages, "change_email_new", "once@example.com", flow)

    def submit(_):
        return cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [200, 400]
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT binding_version FROM security_emails").fetchone()[0] == 2


def test_smtp_unavailable_keeps_existing_email_visible(env):
    s, (cli, _), messages, _ = env
    bind(cli, messages)
    s.ready = lambda: False
    state = cli.get("/api/auth/security-email").json()
    assert state["bound"] is True and state["ready"] is False
    assert state["masked_email"] == "fi***@example.com"
    assert cli.post("/api/auth/email/code/send", json={"purpose": "change_email_old"}).status_code == 503
