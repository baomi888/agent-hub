"""第7步联调专用后端：临时数据、本机假邮箱、回环监听、关闭启动预热。"""

import hashlib
import re
import sys
import time
from pathlib import Path

from run_isolated_tests import configure

root = Path(__file__).resolve().parents[1]
sandbox = configure(root)
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
import uvicorn
from fake_smtp import FakeSMTP
from fastapi import HTTPException
from pydantic import BaseModel

from core import config

smtp = FakeSMTP().start()
config.SECURITY_EMAIL_ENABLED = True
config.MAIL_ENABLED = True
config.SMTP_HOST = smtp.host
config.SMTP_PORT = smtp.port
config.SMTP_SECURITY = "none"
config.SMTP_USER = config.SMTP_FROM = "synthetic@example.com"
config.SMTP_PASSWORD = "test-only-secret"
config.SMTP_TIMEOUT = 1
config.SMTP_FROM_NAME = "知穗 Agent"
import api.security_email as security_api
import main
from core import account, session
from core.auth import _secret
from core.security_email import SecurityEmailService
from core.verification import VerificationStore

offset = [0]
store = VerificationStore(
    session._db_path(),
    hashlib.sha256(b"security-email-verification:" + _secret()).digest(),
    clock=lambda: time.time() + offset[0],
)
store.migrate()
service = SecurityEmailService(store)
original = security_api.get_service
main.app.dependency_overrides[original] = lambda: service
security_api.get_service = lambda: service
account.create_user("journey_legacy_1440", "Historical_pass_9")
account.create_user("journey_legacy_390", "Historical_pass_9")


class Control(BaseModel):
    advance: int = 0
    failure: bool = False


@main.app.get("/__test__/mail")
def mailbox(address: str):
    result = []
    for item in smtp.messages:
        if address in item["rcpt"]:
            code = re.search(r"验证码是：([0-9]{6})", item["text"])
            if code:
                result.append({"code": code.group(1), "subject": item["subject"]})
    return {"messages": result}


@main.app.post("/__test__/control")
def control(body: Control):
    if not 0 <= body.advance <= 600:
        raise HTTPException(400, "仅允许受控测试时钟")
    offset[0] += body.advance
    smtp.behavior = "slow" if body.failure else "ok"
    return {"ok": True}


if __name__ == "__main__":
    print("ISOLATED_BACKEND_READY", flush=True)
    try:
        uvicorn.run(main.app, host="127.0.0.1", port=43131, lifespan="off", access_log=False, log_level="error")
    finally:
        smtp.stop()
