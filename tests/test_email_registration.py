"""邮箱注册：真实应用接口、临时库、受控模拟发信，不连接外部邮箱。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import main
from api.auth import signup_service
from core import account, config, quota, session
from core.security_email import SecurityEmailService
from core.verification import VerificationStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "signup.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(path))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    monkeypatch.setattr(config, "AUTH_SECRET", "signup-test-secret-over-32-bytes-long")
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._HITS.clear()
    clock, messages = [1800000000], []
    store = VerificationStore(path, b"s" * 32, clock=lambda: clock[0])
    store.migrate()

    def sender(address, code, purpose):
        messages.append((address, code, purpose))
        return {"ok": True}

    service = SecurityEmailService(store, sender=sender, ready=lambda: True)
    main.app.dependency_overrides[signup_service] = lambda: service
    cli = TestClient(main.app, client=("127.0.0.1", 60000))
    yield service, cli, messages, clock
    main.app.dependency_overrides.pop(signup_service, None)


def verified(cli, messages, email="signup@example.com"):
    response = cli.post("/api/auth/register/email/send", json={"address": email})
    assert response.status_code == 200, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    response = cli.post(
        "/api/auth/register/email/verify",
        json={
            "request_id": response.json()["request_id"],
            "code": messages[-1][1],
        },
    )
    assert response.status_code == 200, response.text
    return {
        "username": "new_user",
        "password": "New_user_pass_9",
        "email": email,
        "email_grant": response.json()["grant"],
    }


@pytest.mark.parametrize(
    "data",
    [
        {"username": "new_user", "password": "New_user_pass_9"},
        {"username": "new_user", "password": "New_user_pass_9", "email": "a@b.com"},
    ],
)
def test_missing_email_or_proof_is_rejected(env, data):
    s, cli, _, _ = env
    assert cli.post("/api/auth/register/", json=data).status_code == 422
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_registration_binds_and_logs_in(env):
    s, cli, messages, _ = env
    data = verified(cli, messages)
    response = cli.post("/api/auth/register/", json=data)
    assert response.status_code == 200, response.text
    assert response.json()["username"] == data["username"]
    assert "password_hash" not in response.json()
    assert cli.get("/api/auth/me/").status_code == 200
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT address FROM security_emails").fetchone()[0] == data["email"]
    cli.post("/api/auth/logout/")
    assert cli.post("/api/auth/login/", json={k: data[k] for k in ("username", "password")}).status_code == 200


def test_bad_code_changed_email_and_other_browser_cannot_register(env):
    _, cli, messages, _ = env
    data = verified(cli, messages)
    other = TestClient(main.app, client=("127.0.0.2", 60000))
    assert other.post("/api/auth/register/", json=data).status_code == 400
    assert cli.post("/api/auth/register/", json={**data, "email": "other@example.com"}).status_code == 400
    assert cli.post("/api/auth/register/", json=data).status_code == 200


def test_expired_proof_creates_no_partial_account(env):
    s, cli, messages, clock = env
    data = verified(cli, messages)
    clock[0] += 300
    assert cli.post("/api/auth/register/", json=data).status_code == 400
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM security_emails").fetchone()[0] == 0


def test_duplicate_username_rolls_back_and_proof_is_reusable(env):
    s, cli, messages, _ = env
    account.create_user("NEW_USER", "Existing_pass_9")
    data = verified(cli, messages)
    response = cli.post("/api/auth/register/", json=data)
    assert response.status_code == 409
    assert "登录名" in response.text
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM security_emails").fetchone()[0] == 0
    assert cli.post("/api/auth/register/", json={**data, "username": "renamed_user"}).status_code == 200


def test_same_email_race_creates_one_account(env):
    s, cli, messages, clock = env
    first = verified(cli, messages)
    second_cli = TestClient(main.app, client=("127.0.0.2", 60000))
    clock[0] += 60
    second = verified(second_cli, messages)
    second["username"] = "second_user"
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(
            pool.map(
                lambda pair: pair[0].post("/api/auth/register/", json=pair[1]).status_code,
                [(cli, first), (second_cli, second)],
            )
        )
    assert sorted(codes) == [200, 409]
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_closed_signup_blocks_send_verify_and_register(env, monkeypatch):
    _, cli, messages, _ = env
    data = verified(cli, messages)
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    assert cli.post("/api/auth/register/email/send", json={"address": "other@example.com"}).status_code == 403
    assert (
        cli.post("/api/auth/register/email/verify", json={"request_id": "x" * 24, "code": "123456"}).status_code == 403
    )
    assert cli.post("/api/auth/register/", json=data).status_code == 403


def test_disabled_email_never_restores_legacy_registration(env, monkeypatch):
    _, cli, _, _ = env
    main.app.dependency_overrides.pop(signup_service)
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", False)
    assert cli.post("/api/auth/register/", json={"username": "legacy", "password": "Legacy_pass_9"}).status_code != 200
    assert cli.post("/api/auth/register/email/send", json={"address": "a@b.com"}).status_code == 503


def test_invalid_username_password_and_forged_proof(env):
    _, cli, messages, _ = env
    data = verified(cli, messages)
    assert cli.post("/api/auth/register/", json={**data, "password": "12345678"}).status_code == 400
    assert cli.post("/api/auth/register/", json={**data, "username": "bad name"}).status_code == 400
    assert cli.post("/api/auth/register/", json={**data, "email_grant": "forged" * 8}).status_code == 400
    assert cli.post("/api/auth/register/", json=data).status_code == 200


def test_wrong_code_and_reused_proof_are_rejected(env):
    s, cli, messages, _ = env
    sent = cli.post("/api/auth/register/email/send", json={"address": "signup@example.com"})
    request_id = sent.json()["request_id"]
    nonce = cli.cookies.get("zhisui_signup")
    wrong = "999999" if messages[-1][1] != "999999" else "000000"
    assert (
        cli.post("/api/auth/register/email/verify", json={"request_id": request_id, "code": wrong}).status_code == 400
    )
    verified_response = cli.post(
        "/api/auth/register/email/verify", json={"request_id": request_id, "code": messages[-1][1]}
    )
    data = {
        "username": "new_user",
        "password": "New_user_pass_9",
        "email": "signup@example.com",
        "email_grant": verified_response.json()["grant"],
    }
    assert cli.post("/api/auth/register/", json=data).status_code == 200
    cli.cookies.set("zhisui_signup", nonce, path="/api/auth")
    assert cli.post("/api/auth/register/", json={**data, "username": "replay_user"}).status_code == 400
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_failed_sender_creates_no_account_or_usable_request(env):
    s, cli, _, _ = env
    s.sender = lambda *args: {"ok": False}
    assert cli.post("/api/auth/register/email/send", json={"address": "a@b.com"}).status_code == 400
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT state FROM verification_requests").fetchone()[0] == "failed"
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_default_dependency_uses_real_fake_smtp(env, monkeypatch):
    import re

    from fake_smtp import FakeSMTP

    _, cli, _, _ = env
    main.app.dependency_overrides.pop(signup_service)
    with FakeSMTP() as server:
        for key, value in {
            "SECURITY_EMAIL_ENABLED": True,
            "MAIL_ENABLED": True,
            "SMTP_HOST": server.host,
            "SMTP_PORT": server.port,
            "SMTP_SECURITY": "none",
            "SMTP_USER": "test@example.com",
            "SMTP_PASSWORD": "test-only-secret",
            "SMTP_FROM": "test@example.com",
            "SMTP_TIMEOUT": 2,
        }.items():
            monkeypatch.setattr(config, key, value)
        response = cli.post("/api/auth/register/email/send", json={"address": "pipeline@example.com"})
        assert response.status_code == 200, response.text
        assert server.wait_for(1)
        code = re.search(r"验证码是：([0-9]{6})", server.messages[0]["text"]).group(1)
        grant = cli.post(
            "/api/auth/register/email/verify", json={"request_id": response.json()["request_id"], "code": code}
        )
        assert grant.status_code == 200, grant.text
        result = cli.post(
            "/api/auth/register/",
            json={
                "username": "pipeline_user",
                "password": "Pipeline_pass_9",
                "email": "pipeline@example.com",
                "email_grant": grant.json()["grant"],
            },
        )
        assert result.status_code == 200, result.text
        assert cli.get("/api/auth/me/").status_code == 200


def test_email_conflict_keeps_email_message_and_rolls_back(env):
    s, cli, messages, clock = env
    first = verified(cli, messages)
    other = TestClient(main.app, client=("127.0.0.2", 60000))
    clock[0] += 60
    second = verified(other, messages)
    second["username"] = "another_user"
    assert cli.post("/api/auth/register/", json=first).status_code == 200
    response = other.post("/api/auth/register/", json=second)
    assert response.status_code == 409
    assert "邮箱" in response.text
    with sqlite3.connect(s.store.path) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM security_emails").fetchone()[0] == 1


def test_default_email_disabled_closes_registration_but_preserves_password_login(env, monkeypatch):
    _, cli, _, _ = env
    account.create_user("existing_user", "Existing_pass_9")
    main.app.dependency_overrides.pop(signup_service)
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", False)
    response = cli.post(
        "/api/auth/register/",
        json={
            "username": "new_user",
            "password": "New_user_pass_9",
            "email": "signup@example.com",
            "email_grant": "unused-proof-value-24chars",
        },
    )
    assert response.status_code == 503
    assert (
        cli.post(
            "/api/auth/login/",
            json={
                "username": "existing_user",
                "password": "Existing_pass_9",
            },
        ).status_code
        == 200
    )
