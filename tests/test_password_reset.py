from test_email_registration import env as env

"""密码找回：临时账号、一次性证明、会话吊销和邮箱存在性保护。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import main
from api.security_email import get_service
from core import account, config


@pytest.fixture
def reset_env(env):
    s, cli, messages, clock = env
    user = account.create_user("recover_user", "Original_pass_9")
    with sqlite3.connect(s.store.path) as db:
        db.execute("INSERT INTO security_emails VALUES (?,?,?,1)", (user["id"], "recover@example.com", clock[0]))
    main.app.dependency_overrides[get_service] = lambda: s
    yield s, cli, messages, clock, user["id"]
    main.app.dependency_overrides.pop(get_service, None)


def proof(cli, messages):
    response = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
    assert response.status_code == 200, response.text
    response = cli.post(
        "/api/auth/password/reset/email/verify",
        json={"request_id": response.json()["request_id"], "code": messages[-1][1]},
    )
    assert response.status_code == 200, response.text
    return response.json()["grant"]


def body(grant, first="Changed_pass_8", second="Changed_pass_8"):
    return {"grant": grant, "new_password": first, "confirm_password": second}


def test_reset_invalidates_cookie_and_bearer_and_does_not_login(reset_env):
    _, cli, messages, _, _ = reset_env
    old = TestClient(main.app)
    assert (
        old.post("/api/auth/login/", json={"username": "recover_user", "password": "Original_pass_9"}).status_code
        == 200
    )
    token = old.cookies.get("baomi_session")
    grant = proof(cli, messages)
    result = cli.post("/api/auth/password/reset", json=body(grant))
    assert result.status_code == 200, result.text
    assert "baomi_session" not in cli.cookies
    assert old.get("/api/auth/me/").status_code == 401
    assert TestClient(main.app).get("/api/auth/me/", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert (
        cli.post("/api/auth/login/", json={"username": "recover_user", "password": "Original_pass_9"}).status_code
        == 401
    )
    assert (
        cli.post("/api/auth/login/", json={"username": "recover_user", "password": "Changed_pass_8"}).status_code == 200
    )


@pytest.mark.parametrize(
    "first,second", [("Changed_pass_8", "Other_pass_9"), ("12345678", "12345678"), ("recover_user", "recover_user")]
)
def test_mismatch_and_weak_password_do_not_consume(reset_env, first, second):
    _, cli, messages, _, _ = reset_env
    grant = proof(cli, messages)
    assert cli.post("/api/auth/password/reset", json=body(grant, first, second)).status_code == 400
    assert cli.post("/api/auth/password/reset", json=body(grant)).status_code == 200


def test_unknown_email_and_known_email_have_identical_send_shape(reset_env):
    _, cli, messages, _, _ = reset_env
    a = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
    b = cli.post("/api/auth/password/reset/email/send", json={"address": "unknown@example.com"})
    assert a.status_code == b.status_code == 200
    assert a.json().keys() == b.json().keys()
    for key in a.json().keys() - {"request_id"}:
        assert a.json()[key] == b.json()[key]
    assert len(messages) == 1
    assert (
        cli.post(
            "/api/auth/password/reset/email/verify", json={"request_id": b.json()["request_id"], "code": "000000"}
        ).status_code
        == 400
    )


def test_wrong_code_expires_replay_and_other_browser(reset_env):
    _, cli, messages, clock, _ = reset_env
    grant = proof(cli, messages)
    other = TestClient(main.app)
    assert other.post("/api/auth/password/reset", json=body(grant)).status_code == 400
    clock[0] += 300
    assert cli.post("/api/auth/password/reset", json=body(grant)).status_code == 400
    grant = proof(cli, messages)
    assert cli.post("/api/auth/password/reset", json=body(grant)).status_code == 200
    assert cli.post("/api/auth/password/reset", json=body(grant)).status_code == 400


def test_email_binding_change_rejects_old_proof(reset_env):
    s, cli, messages, _, uid = reset_env
    grant = proof(cli, messages)
    with sqlite3.connect(s.store.path) as db:
        db.execute("UPDATE security_emails SET address='new@example.com',binding_version=2 WHERE user_id=?", (uid,))
    assert cli.post("/api/auth/password/reset", json=body(grant)).status_code == 400


def test_concurrent_reset_has_one_winner(reset_env):
    _, cli, messages, _, _ = reset_env
    grant = proof(cli, messages)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: cli.post("/api/auth/password/reset", json=body(grant)).status_code, range(2)))
    assert sorted(codes) == [200, 400]


def test_closed_signup_does_not_disable_password_recovery(reset_env, monkeypatch):
    _, cli, messages, _, _ = reset_env
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    assert cli.post("/api/auth/password/reset", json=body(proof(cli, messages))).status_code == 200


def test_smtp_failure_not_reported_as_delivery_success(reset_env):
    s, cli, _, _, _ = reset_env
    s.sender = lambda *args: {"ok": False}
    response = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
    assert response.status_code == 200
    assert "如果" in response.json()["message"]
    assert "成功" not in response.json()["message"]
    assert (
        cli.post(
            "/api/auth/password/reset/email/verify",
            json={"request_id": response.json()["request_id"], "code": "000000"},
        ).status_code
        == 400
    )


def test_other_pending_reset_grant_invalidated_after_success(reset_env):
    _, cli, messages, clock, _ = reset_env
    first = proof(cli, messages)
    other = TestClient(main.app, client=("127.0.0.2", 60000))
    clock[0] += 60
    second = proof(other, messages)
    assert cli.post("/api/auth/password/reset", json=body(first)).status_code == 200
    assert (
        other.post("/api/auth/password/reset", json=body(second, "Another_pass_7", "Another_pass_7")).status_code == 400
    )


@pytest.mark.parametrize("kind", ["disabled", "deleted", "unbound"])
def test_unavailable_accounts_never_send_or_recover(reset_env, kind):
    s, cli, messages, clock, uid = reset_env
    with sqlite3.connect(s.store.path) as db:
        if kind == "disabled":
            db.execute("UPDATE users SET status=0 WHERE id=?", (uid,))
        elif kind == "deleted":
            db.execute(
                "INSERT INTO deleted_users(uid,username,deleted_at) VALUES (?,?,?)", (uid, "recover_user", clock[0])
            )
        else:
            db.execute("DELETE FROM security_emails WHERE user_id=?", (uid,))
    response = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
    assert response.status_code == 200 and len(messages) == 0
    assert (
        cli.post(
            "/api/auth/password/reset/email/verify",
            json={"request_id": response.json()["request_id"], "code": "123456"},
        ).status_code
        == 400
    )


def test_wrong_attempts_and_code_expiration(reset_env):
    _, cli, messages, clock, _ = reset_env
    response = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
    request_id = response.json()["request_id"]
    wrong = "999999" if messages[-1][1] != "999999" else "000000"
    for _ in range(5):
        assert (
            cli.post(
                "/api/auth/password/reset/email/verify", json={"request_id": request_id, "code": wrong}
            ).status_code
            == 400
        )
    assert (
        cli.post(
            "/api/auth/password/reset/email/verify", json={"request_id": request_id, "code": messages[-1][1]}
        ).status_code
        == 400
    )
    clock[0] += 60
    response = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
    clock[0] += 600
    assert (
        cli.post(
            "/api/auth/password/reset/email/verify",
            json={"request_id": response.json()["request_id"], "code": messages[-1][1]},
        ).status_code
        == 400
    )


def test_disabled_service_refuses_recovery_without_migration(reset_env, monkeypatch):
    _, cli, _, _, _ = reset_env
    main.app.dependency_overrides.pop(get_service)
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", False)
    assert cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"}).status_code == 503
    assert cli.post("/api/auth/password/reset", json=body("unverified" * 8)).status_code == 503


def test_default_dependency_smtp_recovery_pipeline(reset_env, monkeypatch):
    import re

    from fake_smtp import FakeSMTP

    _, cli, _, _, _ = reset_env
    main.app.dependency_overrides.pop(get_service)
    with FakeSMTP() as server:
        for name, value in {
            "SECURITY_EMAIL_ENABLED": True,
            "MAIL_ENABLED": True,
            "SMTP_HOST": server.host,
            "SMTP_PORT": server.port,
            "SMTP_SECURITY": "none",
            "SMTP_USER": "test@example.com",
            "SMTP_FROM": "test@example.com",
            "SMTP_PASSWORD": "test-only-secret",
            "SMTP_TIMEOUT": 2,
        }.items():
            monkeypatch.setattr(config, name, value)
        response = cli.post("/api/auth/password/reset/email/send", json={"address": "recover@example.com"})
        assert response.status_code == 200 and server.wait_for(1)
        code = re.search(r"验证码是：([0-9]{6})", server.messages[0]["text"]).group(1)
        verified = cli.post(
            "/api/auth/password/reset/email/verify", json={"request_id": response.json()["request_id"], "code": code}
        )
        assert verified.status_code == 200, verified.text
        assert cli.post("/api/auth/password/reset", json=body(verified.json()["grant"])).status_code == 200
