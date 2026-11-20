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


def _assert_isolated_db() -> None:
    """只允许对临时库做注册准备。

    两种测试入口都放行，语义一致（生产库在 <仓库>/data/ 下，绝不放行）：
    · tools/run_isolated_tests.py：ZHISUI_TEST_SANDBOX 指向本次临时目录，严格前缀校验；
    · 传统 pytest：各测试自己 monkeypatch _db_path 到 tempfile 目录 —— 只要求
      库路径不在仓库目录内（Windows 下 gettempdir() 与 mkdtemp 的盘符/大小写
      形态可能不一致，前缀比较不可靠，用「排除仓库根」这个不变式代替）。
    """
    path = Path(session._db_path()).resolve()
    sandbox = os.environ.get("ZHISUI_TEST_SANDBOX")
    if sandbox:
        ok = path.is_relative_to(Path(sandbox).resolve())
    else:
        ok = not path.is_relative_to(Path(__file__).resolve().parents[1])
    if not ok:
        raise RuntimeError("测试账号准备只允许独立临时数据库")


def register_verified(client, username, password):
    _assert_isolated_db()
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
