"""旧回归测试的账号准备：走真实邮箱注册接口，不提供生产绕过开关。"""

import hashlib
import os
import secrets
from pathlib import Path

import main
from api.auth import signup_service
from core import account, config, session
from core.auth import _secret
from core.security_email import SecurityEmailService
from core.verification import VerificationStore


def register_verified(client, username, password):
    sandbox = os.environ.get("ZHISUI_TEST_SANDBOX")
    if not sandbox or not Path(session._db_path()).resolve().is_relative_to(Path(sandbox).resolve()):
        raise RuntimeError("测试账号准备只允许独立临时数据库")
    account._ensure_tables()
    email = secrets.token_hex(12) + "@example.com"
    store = VerificationStore(session._db_path(), hashlib.sha256(b"security-email-verification:" + _secret()).digest())
    store.migrate()
    codes = []

    def sender(address, code, purpose):
        codes.append(code)
        return {"ok": True}

    service = SecurityEmailService(store, sender=sender, ready=lambda: True)
    old = main.app.dependency_overrides.get(signup_service)
    main.app.dependency_overrides[signup_service] = lambda: service
    try:
        if not config.ALLOW_SIGNUP:
            return client.post(
                "/api/auth/register/",
                json={
                    "username": username,
                    "password": password,
                    "email": email,
                    "email_grant": "placeholder-not-used-when-closed",
                },
            )
        response = client.post(
            "/api/auth/register/email/send", json={"address": email}, headers={"X-Forwarded-For": "127.0.0.1"}
        )
        assert response.status_code == 200, response.text
        result = client.post(
            "/api/auth/register/email/verify", json={"request_id": response.json()["request_id"], "code": codes[-1]}
        )
        assert result.status_code == 200, result.text
        return client.post(
            "/api/auth/register/",
            json={"username": username, "password": password, "email": email, "email_grant": result.json()["grant"]},
        )
    finally:
        if old is None:
            main.app.dependency_overrides.pop(signup_service, None)
        else:
            main.app.dependency_overrides[signup_service] = old
