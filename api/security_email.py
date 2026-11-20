"""已登录账号的安全邮箱设置接口；不自动迁移数据库，不开放注册或找回用途。"""

import hashlib
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import _client_ip
from core import config, session
from core.auth import _secret, get_current_user
from core.security_email import SecurityEmailError, SecurityEmailService
from core.verification import VerificationError, VerificationStore

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_service():
    if not config.SECURITY_EMAIL_ENABLED:
        raise HTTPException(503, "安全邮箱服务尚未启用")
    path = session._db_path()
    # 发布时需另行授权显式迁移；请求不会偷偷建表或补绑真实账号。
    try:
        with session._get_conn() as db:
            required = {
                "security_emails",
                "verification_requests",
                "verification_slots",
                "verification_grants",
                "verification_rate_limits",
                "verification_cooldowns",
            }
            present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= present:
                raise HTTPException(503, "安全邮箱服务尚未准备完成")
    except sqlite3.Error:
        raise HTTPException(503, "安全邮箱服务暂不可用") from None
    key = hashlib.sha256(b"security-email-verification:" + _secret()).digest()
    return SecurityEmailService(VerificationStore(path, key))


Service = Annotated[SecurityEmailService, Depends(get_service)]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SendBody(StrictBody):
    purpose: Literal["bind_email", "change_email_old", "change_email_new"]
    address: str | None = Field(None, max_length=254)
    flow: str | None = Field(None, min_length=16, max_length=128)


class VerifyBody(StrictBody):
    request_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=1, max_length=12)


class BindBody(StrictBody):
    grant: str = Field(min_length=16, max_length=128)


class ChangeBody(StrictBody):
    old_grant: str = Field(min_length=16, max_length=128)
    new_grant: str = Field(min_length=16, max_length=128)


def call(operation, *args):
    try:
        return operation(*args)
    except SecurityEmailError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except VerificationError as exc:
        status = 429 if "频繁" in str(exc) or "发送次数" in str(exc) else 400
        raise HTTPException(status, str(exc)) from None
    except sqlite3.IntegrityError:
        raise HTTPException(409, "邮箱已被绑定，请重新验证其他邮箱") from None


@router.get("/security-email")
def state(service: Service, uid: str = Depends(get_current_user)):
    return call(service.state, uid)


@router.post("/email/code/send")
def send(body: SendBody, request: Request, service: Service, uid: str = Depends(get_current_user)):
    return call(service.send, uid, body.purpose, body.address, body.flow, _client_ip(request))


@router.post("/email/code/verify")
def verify(body: VerifyBody, service: Service, uid: str = Depends(get_current_user)):
    return call(service.verify, uid, body.request_id, body.code)


@router.post("/security-email/bind")
def bind(body: BindBody, service: Service, uid: str = Depends(get_current_user)):
    return call(service.bind, uid, body.grant)


@router.post("/security-email/change")
def change(body: ChangeBody, service: Service, uid: str = Depends(get_current_user)):
    return call(service.change, uid, body.old_grant, body.new_grant)
