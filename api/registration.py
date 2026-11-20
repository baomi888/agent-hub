"""公开的注册邮箱验证；随机 HttpOnly Cookie 将证明绑定到当前浏览器。"""

import re
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from api.auth import SignupService, _client_ip
from api.security_email import call
from core import config
from core.auth import _is_secure_request

router = APIRouter()


def nonce_from_request(request):
    nonce = request.cookies.get("zhisui_signup", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", nonce):
        raise HTTPException(400, "注册验证已失效，请重新发送验证码")
    return nonce


def check_open():
    if not config.ALLOW_SIGNUP:
        raise HTTPException(403, "本站已关闭自助注册，请联系管理员开通账号")


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)


class Code(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=1, max_length=12)


@router.post("/register/email/send")
def send(body: Address, request: Request, response: Response, service: SignupService):
    check_open()
    try:
        nonce = nonce_from_request(request)
    except HTTPException:
        nonce = secrets.token_urlsafe(32)
    result = call(service.send_signup, nonce, body.address, _client_ip(request))
    response.set_cookie(
        "zhisui_signup",
        nonce,
        max_age=1800,
        path="/api/auth",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    return result


@router.post("/register/email/verify")
def verify(body: Code, request: Request, service: SignupService):
    check_open()
    return call(service.verify_signup, nonce_from_request(request), body.request_id, body.code)
