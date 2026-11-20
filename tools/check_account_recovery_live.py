"""第7步受控真实收件与找回验收：临时账号、仅发件邮箱自收，凭证只在内存。"""

import getpass
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import sys
import tempfile
import time
from pathlib import Path


def main():
    print("READY: hidden credentials; two self-addressed test emails only", flush=True)
    raw = getpass.getpass("Credentials JSON (hidden): ") if sys.stdin.isatty() else sys.stdin.readline()
    if not raw.strip():
        print("No credentials received; no mail sent.", flush=True)
        return 2
    payload = json.loads(raw)
    sender, password = payload.pop("username"), payload.pop("password")
    del raw
    if payload or not isinstance(sender, str) or not sender.endswith("@qq.com") or not password:
        raise ValueError("只允许受控QQ账号自收验收")
    del payload
    root = Path(__file__).resolve().parents[1]
    sandbox = Path(tempfile.mkdtemp(prefix="zhisui-live-recovery-")).resolve()
    for name in ("data", "chroma", "downloads", "tmp"):
        (sandbox / name).mkdir()
    os.environ.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "DATA_DIR": str(sandbox / "data"),
            "PERSIST_DIR": str(sandbox / "chroma"),
            "DOWNLOAD_DIR": str(sandbox / "downloads"),
            "AGENT_HUB_DB": str(sandbox / "data" / "sessions.db"),
            "AUTH_SECRET": secrets.token_urlsafe(48),
            "SMTP_HOST": "smtp.qq.com",
            "SMTP_PORT": "465",
            "SMTP_SECURITY": "ssl",
            "SMTP_TIMEOUT": "12",
            "SMTP_USER": sender,
            "SMTP_FROM": sender,
            "SMTP_PASSWORD": password,
            "SMTP_FROM_NAME": "知穗 Agent",
            "SECURITY_EMAIL_ENABLED": "1",
            "MAIL_ENABLED": "1",
            "ALLOW_SIGNUP": "1",
            "ADMIN_USERNAMES": "",
            "ADMIN_UIDS": "",
            "ANONYMIZED_TELEMETRY": "False",
            "DEEPSEEK_API_KEY": "",
            "EMBEDDING_API_KEY": "",
            "VISION_API_KEY": "",
        }
    )
    del password
    tempfile.tempdir = str(sandbox / "tmp")
    sys.dont_write_bytecode = True
    protected = [root / name for name in ("data", "data-staging", "chroma_db", "downloads", ".env")]
    allowed = {item[4][0] for item in socket.getaddrinfo("smtp.qq.com", 465, type=socket.SOCK_STREAM)}

    def audit(event, args):
        if event in ("open", "os.mkdir", "os.remove", "os.rmdir", "os.listdir", "os.scandir"):
            if isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).resolve()
                if any(path == base or base in path.parents for base in protected):
                    raise PermissionError("真实邮件验收禁止访问既有数据或配置")
        if event == "sqlite3.connect":
            value = str(args[0])
            if value != ":memory:" and not Path(value).resolve().is_relative_to(sandbox):
                raise PermissionError("仅允许临时验收数据库")
        if event == "socket.connect":
            target = args[1]
            if isinstance(target, tuple):
                host, port = target[0], target[1]
                try:
                    loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    loopback = host == "localhost"
                if not loopback and not (host in allowed and port == 465):
                    raise PermissionError("仅允许回环和指定SMTP TLS连接")

    sys.addaudithook(audit)
    sys.path.insert(0, str(root))
    from fastapi.testclient import TestClient

    import main as application
    from api.auth import signup_service
    from api.security_email import get_service
    from core import security_mail, session
    from core.auth import _secret
    from core.security_email import SecurityEmailService
    from core.verification import VerificationStore

    os.environ.pop("SMTP_PASSWORD", None)
    codes = []

    def send(address, code, purpose):
        if address != sender:
            raise ValueError("禁止发送到其他邮箱")
        result = security_mail.send_code(address, code, purpose)
        if result["ok"]:
            codes.append(code)
        return result

    store = VerificationStore(session._db_path(), hashlib.sha256(b"security-email-verification:" + _secret()).digest())
    store.migrate()
    service = SecurityEmailService(store, sender=send)
    application.app.dependency_overrides[signup_service] = lambda: service
    application.app.dependency_overrides[get_service] = lambda: service
    cli = TestClient(application.app, client=("127.0.0.1", 60001))

    def ok(response, expected=200):
        if response.status_code != expected:
            raise RuntimeError("受控流程未通过，HTTP状态=" + str(response.status_code))
        return response.json()

    user = "live_receipt_" + secrets.token_hex(5)
    old_password = "Live_A9_" + secrets.token_urlsafe(16)
    new_password = "Changed_A8_" + secrets.token_urlsafe(16)
    started = time.monotonic()
    sent = ok(cli.post("/api/auth/register/email/send", json={"address": sender}))
    verified = ok(
        cli.post("/api/auth/register/email/verify", json={"request_id": sent["request_id"], "code": codes[-1]})
    )
    ok(
        cli.post(
            "/api/auth/register/",
            json={"username": user, "password": old_password, "email": sender, "email_grant": verified["grant"]},
        )
    )
    ok(cli.get("/api/auth/me/"))
    print("REGISTER_API=passed; SMTP accepted; recipient confirmation pending", flush=True)
    print("Waiting for the real 60-second recipient cooldown before recovery", flush=True)
    while time.monotonic() - started < 61:
        time.sleep(1)
    ok(cli.post("/api/auth/logout/"))
    sent = ok(cli.post("/api/auth/password/reset/email/send", json={"address": sender}))
    if len(codes) != 2:
        raise RuntimeError("找回邮件未被SMTP接受")
    verified = ok(
        cli.post("/api/auth/password/reset/email/verify", json={"request_id": sent["request_id"], "code": codes[-1]})
    )
    ok(
        cli.post(
            "/api/auth/password/reset",
            json={"grant": verified["grant"], "new_password": new_password, "confirm_password": new_password},
        )
    )
    ok(cli.get("/api/auth/me/"), 401)
    ok(cli.post("/api/auth/login/", json={"username": user, "password": old_password}), 401)
    ok(cli.post("/api/auth/login/", json={"username": user, "password": new_password}))
    print("RECOVERY_API=passed; old password rejected; new password accepted; two receipts pending", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
