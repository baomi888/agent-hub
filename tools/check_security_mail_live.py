"""受控真实邮件验收：只向发件账号自己发一封，不读 .env、不访问用户库。

输入为 stdin JSON，仅进程内使用，不接受命令行明文凭证。
SMTP 接受不代表实际收件，仍需用户确认；不要将真实输入保存为文件。
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import socket
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sandbox = Path(tempfile.mkdtemp(prefix="zhisui-live-mail-")).resolve()
    for name in ("data", "chroma", "downloads"):
        (sandbox / name).mkdir()
    os.environ.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "DATA_DIR": str(sandbox / "data"),
            "PERSIST_DIR": str(sandbox / "chroma"),
            "DOWNLOAD_DIR": str(sandbox / "downloads"),
            "AGENT_HUB_DB": str(sandbox / "forbidden.db"),
            "SMTP_HOST": "smtp.qq.com",
            "SMTP_PORT": "465",
            "SMTP_SECURITY": "ssl",
            "SMTP_TIMEOUT": "12",
            "SMTP_FROM_NAME": "知穗 Agent",
            "SECURITY_EMAIL_ENABLED": "1",
            "MAIL_ENABLED": "1",
        }
    )
    sys.dont_write_bytecode = True
    print("READY: credentials via stdin only", flush=True)
    raw = getpass.getpass("Credentials JSON (hidden): ") if sys.stdin.isatty() else sys.stdin.readline()
    if not raw.strip():
        print("No credentials received; no mail sent.", flush=True)
        return 2
    payload = json.loads(raw)
    del raw
    sender = payload.pop("username")
    password = payload.pop("password")
    if payload or not isinstance(sender, str) or not sender.endswith("@qq.com"):
        raise ValueError("只允许受控 QQ 发件账号自收验收")
    os.environ.update({"SMTP_USER": sender, "SMTP_FROM": sender, "SMTP_PASSWORD": password})
    del password, payload
    allowed = {entry[4][0] for entry in socket.getaddrinfo("smtp.qq.com", 465, type=socket.SOCK_STREAM)}
    protected = [root / p for p in ("data", "data-staging", "chroma_db", "downloads", ".env")]

    def audit(event: str, args: tuple) -> None:
        if event == "sqlite3.connect":
            raise PermissionError("真实邮件验收禁止访问数据库")
        if event in ("open", "os.mkdir", "os.remove", "os.rmdir", "os.listdir", "os.scandir"):
            if isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).resolve()
                if any(path == p or p in path.parents for p in protected):
                    raise PermissionError("真实邮件验收禁止访问既有用户数据和配置")
        if event in ("socket.connect", "socket.bind"):
            address = args[1]
            if event == "socket.bind" or not (
                isinstance(address, tuple) and address[0] in allowed and address[1] == 465
            ):
                raise PermissionError("真实邮件验收仅允许指定 SMTP TLS 连接")

    sys.addaudithook(audit)
    sys.path.insert(0, str(root))
    from core import security_mail

    # 删除环境中的副本；授权码只保留在本进程配置对象中。
    os.environ.pop("SMTP_PASSWORD", None)
    result = security_mail.send_code(sender, f"{secrets.randbelow(1000000):06d}", "reset_password")
    print(
        json.dumps(
            {
                "smtp_accepted": result["ok"],
                "error": result["error"],
                "retryable": result["retryable"],
                "receipt": "awaiting_user_confirmation",
            }
        ),
        flush=True,
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
