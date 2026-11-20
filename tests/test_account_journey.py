"""第7步跨流程联调及敏感日志守卫，所有账号均为合成临时数据。"""

import smtplib

import pytest
from test_email_registration import env as env
from test_email_registration import verified
from test_security_email_settings import proof as settings_proof

import main
from api.security_email import get_service
from core import account, config, security_mail


@pytest.fixture
def journey_env(env):
    main.app.dependency_overrides[get_service] = lambda: env[0]
    yield env
    main.app.dependency_overrides.pop(get_service, None)


def test_register_change_recover_login_journey(journey_env):
    _, cli, messages, clock = journey_env
    data = verified(cli, messages)
    assert cli.post("/api/auth/register/", json=data).status_code == 200
    old_token = cli.cookies.get("baomi_session")
    clock[0] += 61
    old, flow = settings_proof(cli, messages, "change_email_old")
    new, _ = settings_proof(cli, messages, "change_email_new", "changed@example.com", flow)
    assert cli.post("/api/auth/security-email/change", json={"old_grant": old, "new_grant": new}).status_code == 200
    cli.post("/api/auth/logout/")
    clock[0] += 61
    sent = cli.post("/api/auth/password/reset/email/send", json={"address": "changed@example.com"})
    assert sent.status_code == 200
    verified_response = cli.post(
        "/api/auth/password/reset/email/verify", json={"request_id": sent.json()["request_id"], "code": messages[-1][1]}
    )
    assert verified_response.status_code == 200
    assert (
        cli.post(
            "/api/auth/password/reset",
            json={
                "grant": verified_response.json()["grant"],
                "new_password": "Journey_pass_8",
                "confirm_password": "Journey_pass_8",
            },
        ).status_code
        == 200
    )
    assert cli.get("/api/auth/me/").status_code == 401
    assert cli.get("/api/auth/me/", headers={"Authorization": "Bearer " + old_token}).status_code == 401
    assert cli.post("/api/auth/login/", json={"username": "new_user", "password": "Journey_pass_8"}).status_code == 200


def test_historical_user_bind_then_recovery(journey_env):
    _, cli, messages, clock = journey_env
    account.create_user("historical_journey", "Historical_pass_9")
    assert (
        cli.post(
            "/api/auth/login/", json={"username": "historical_journey", "password": "Historical_pass_9"}
        ).status_code
        == 200
    )
    grant, _ = settings_proof(cli, messages, "bind_email", "historical@example.com")
    assert cli.post("/api/auth/security-email/bind", json={"grant": grant}).status_code == 200
    clock[0] += 61
    sent = cli.post("/api/auth/password/reset/email/send", json={"address": "historical@example.com"})
    assert sent.status_code == 200 and messages[-1][2] == "reset_password"


def test_smtp_error_echo_cannot_leak_code_or_credentials(monkeypatch, caplog):
    from core import mailer

    for name, value in {
        "SECURITY_EMAIL_ENABLED": True,
        "MAIL_ENABLED": True,
        "SMTP_HOST": "smtp.qq.com",
        "SMTP_PORT": 465,
        "SMTP_SECURITY": "ssl",
        "SMTP_TIMEOUT": 1,
        "SMTP_USER": "synthetic@example.com",
        "SMTP_PASSWORD": "synthetic-secret",
        "SMTP_FROM": "synthetic@example.com",
    }.items():
        monkeypatch.setattr(config, name, value)

    def fail(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"echo synthetic-secret 482619 synthetic@example.com")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", fail)
    result = security_mail.send_code("receiver@example.com", "482619", "reset_password")
    assert result["error"] == "auth_failed" and result["retryable"] is False
    for value in ("synthetic-secret", "482619", "synthetic@example.com", "receiver@example.com"):
        assert value not in caplog.text
        assert value not in str(result)


def test_live_recovery_probe_requires_explicit_hidden_input():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "check_account_recovery_live.py")],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "no mail sent" in result.stdout


def test_test_control_endpoints_are_not_production_routes():
    assert not any(getattr(route, "path", "").startswith("/__test__/") for route in main.app.routes)


def test_real_smtp_timeout_through_registration_endpoint(journey_env, monkeypatch):
    import time

    from fake_smtp import FakeSMTP

    s, cli, _, _ = journey_env
    with FakeSMTP(behavior="slow") as server:
        for name, value in {
            "SECURITY_EMAIL_ENABLED": True,
            "MAIL_ENABLED": True,
            "SMTP_HOST": server.host,
            "SMTP_PORT": server.port,
            "SMTP_SECURITY": "none",
            "SMTP_USER": "test@example.com",
            "SMTP_FROM": "test@example.com",
            "SMTP_PASSWORD": "synthetic-secret",
            "SMTP_TIMEOUT": 1,
        }.items():
            monkeypatch.setattr(config, name, value)
        s.sender = security_mail.send_code
        started = time.monotonic()
        response = cli.post("/api/auth/register/email/send", json={"address": "timeout@example.com"})
        assert response.status_code == 400, response.text
        assert "邮件未发送成功" in response.json()["detail"]
        assert time.monotonic() - started < 5
