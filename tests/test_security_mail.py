"""安全验证码邮件：真 SMTP 字节、发送故障、用途隔离及提醒兼容。"""

import time

import pytest
from fake_smtp import FakeSMTP

from core import config, mailer, security_mail


@pytest.fixture
def smtp(monkeypatch):
    with FakeSMTP() as server:
        for name, value in {
            "SECURITY_EMAIL_ENABLED": True,
            "MAIL_ENABLED": True,
            "SMTP_HOST": server.host,
            "SMTP_PORT": server.port,
            "SMTP_USER": "test@example.com",
            "SMTP_PASSWORD": "test-only-auth",
            "SMTP_FROM": "test@example.com",
            "SMTP_FROM_NAME": "知穗 Agent",
            "SMTP_SECURITY": "none",
            "SMTP_TIMEOUT": 2,
        }.items():
            monkeypatch.setattr(config, name, value)
        yield server


@pytest.mark.parametrize(
    "purpose,label",
    [
        ("register_email", "注册账号"),
        ("bind_email", "绑定安全邮箱"),
        ("change_email_old", "验证原安全邮箱"),
        ("change_email_new", "验证新安全邮箱"),
        ("reset_password", "找回密码"),
    ],
)
def test_real_message_content_and_purpose(smtp, purpose, label, caplog):
    result = security_mail.send_code("receiver@example.com", "482619", purpose)
    assert result["ok"] is True
    assert smtp.wait_for(1)
    message = smtp.messages[0]
    assert message["rcpt"] == ["receiver@example.com"]
    assert "知穗 Agent" in message["subject"]
    assert label in message["text"]
    assert "482619" in message["text"]
    assert "10 分钟" in message["text"]
    assert "非本人操作" in message["text"]
    assert message["date"]
    assert "482619" not in caplog.text
    assert "482619" not in str(result)


@pytest.mark.parametrize(
    "address,code,purpose",
    [
        ("invalid", "482619", "register_email"),
        ("a@b.com\r\nBcc: c@d.com", "482619", "register_email"),
        ("a@b.com,c@d.com", "482619", "register_email"),
        ("a@b.com", "12345", "register_email"),
        ("a@b.com", "１２３４５６", "register_email"),
        ("a@b.com", "482619", "notification"),
    ],
)
def test_invalid_inputs_never_send(smtp, address, code, purpose):
    result = security_mail.send_code(address, code, purpose)
    assert result["ok"] is False and result["error"] == "invalid"
    assert smtp.messages == [] and smtp.credentials == []


def test_disabled_security_does_not_disable_reminders(smtp, monkeypatch):
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", False)
    assert security_mail.status() == {"ready": False}
    assert security_mail.send_code("a@b.com", "482619", "register_email")["error"] == "not_configured"
    assert smtp.credentials == []
    assert mailer.send("a@b.com", "原有提醒", "提醒内容")["ok"] is True
    assert smtp.wait_for(1)
    assert smtp.messages[0]["text"].strip() == "提醒内容"


@pytest.mark.parametrize(
    "field,value",
    [
        ("SMTP_PASSWORD", ""),
        ("SMTP_PORT", 0),
        ("SMTP_TIMEOUT", 0),
        ("SMTP_SECURITY", "invalid"),
        ("MAIL_ENABLED", False),
    ],
)
def test_incomplete_or_unsafe_configuration(smtp, monkeypatch, field, value):
    monkeypatch.setattr(config, field, value)
    assert security_mail.status() == {"ready": False}
    assert security_mail.send_code("a@b.com", "482619", "register_email")["ok"] is False
    assert smtp.credentials == []


@pytest.mark.parametrize(
    "behavior,error,retryable",
    [
        ("auth", "auth_failed", False),
        ("auth_close", "auth_failed", False),
        ("rcpt", "recipient_refused", False),
        ("greet", "network", True),
    ],
)
def test_transport_failures(smtp, monkeypatch, behavior, error, retryable):
    monkeypatch.setattr(smtp, "behavior", behavior)
    result = security_mail.send_code("a@b.com", "482619", "reset_password")
    assert result["ok"] is False and result["error"] == error
    assert result["retryable"] is retryable
    assert "482619" not in str(result) and config.SMTP_PASSWORD not in str(result)


def test_timeout_is_bounded(smtp, monkeypatch):
    monkeypatch.setattr(smtp, "behavior", "slow")
    monkeypatch.setattr(config, "SMTP_TIMEOUT", 1)
    started = time.monotonic()
    result = security_mail.send_code("a@b.com", "482619", "reset_password")
    assert result["error"] == "network" and result["retryable"] is True
    assert time.monotonic() - started < 5


def test_public_smtp_requires_encryption(smtp, monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.qq.com")
    assert security_mail.status() == {"ready": False}


def test_raw_transport_response_is_not_exposed(smtp, monkeypatch):
    monkeypatch.setattr(
        mailer,
        "send",
        lambda *args: {
            "ok": False,
            "error": "auth_failed",
            "retryable": False,
            "detail": "external reply with test-only-auth and 482619",
        },
    )
    result = security_mail.send_code("a@b.com", "482619", "reset_password")
    assert result["error"] == "auth_failed"
    assert "482619" not in str(result) and "test-only-auth" not in str(result)
    assert "外部" not in result["detail"]


def test_live_probe_without_credentials_sends_nothing():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "check_security_mail_live.py")],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 2
    assert "no mail sent" in completed.stdout
    assert "Traceback" not in completed.stderr
