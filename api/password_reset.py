"""公开的邮箱找回接口；浏览器绑定证明，成功后清除会话，不自动登录。"""

import re
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from api.auth import _client_ip
from api.security_email import Service, call
from core.auth import _is_secure_request, clear_session_cookie

router = APIRouter()


def nonce_from_request(request):
    value = request.cookies.get("zhisui_reset", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
        raise HTTPException(400, "找回验证已失效，请重新发送验证码")
    return value


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)


class Code(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=1, max_length=12)


class Reset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant: str = Field(min_length=16, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


@router.post("/password/reset/email/send")
def send(body: Address, request: Request, response: Response, service: Service):
    try:
        nonce = nonce_from_request(request)
    except HTTPException:
        nonce = secrets.token_urlsafe(32)
    result = call(service.send_reset, nonce, body.address, _client_ip(request))
    response.set_cookie(
        "zhisui_reset",
        nonce,
        max_age=1800,
        path="/api/auth",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    return result


@router.post("/password/reset/email/verify")
def verify(body: Code, request: Request, service: Service):
    return call(service.verify_reset, nonce_from_request(request), body.request_id, body.code)


@router.post("/password/reset")
def reset(body: Reset, request: Request, response: Response, service: Service):
    result = call(
        service.reset_password, nonce_from_request(request), body.grant, body.new_password, body.confirm_password
    )
    clear_session_cookie(response)
    response.delete_cookie("zhisui_reset", path="/api/auth")
    return result
