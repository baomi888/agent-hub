"""公开的邮箱验证码登录接口；匿名发码与找回同口径，验证通过即签会话。

不复用 api/auth.py 的登录端点文件是因为注入依赖：api/security_email.py 顶层
`from api.auth import _client_ip`，auth.py 若反向 import Service 就成环。
这里与 password_reset.py 一样单向依赖两者，无环。
"""

import re
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from api.auth import _client_ip, _issue_session
from api.security_email import Service, call
from core.auth import _is_secure_request

router = APIRouter()

_NONCE_COOKIE = "zhisui_login_email"


def nonce_from_request(request):
    value = request.cookies.get(_NONCE_COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
        raise HTTPException(400, "登录验证已失效，请重新发送验证码")
    return value


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)


class LoginCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=1, max_length=12)


@router.post("/login/email/send")
def send(body: Address, request: Request, response: Response, service: Service):
    """匿名发码：邮箱没绑定账号时响应长得一模一样（防账号枚举）。"""
    try:
        nonce = nonce_from_request(request)
    except HTTPException:
        nonce = secrets.token_urlsafe(32)
    result = call(service.send_login, nonce, body.address, _client_ip(request))
    # nonce 绑 HttpOnly Cookie：发码与验证必须同一浏览器上下文，验证码邮件
    # 被转发给别人时对方没有这个 nonce，验证不了。
    response.set_cookie(
        _NONCE_COOKIE,
        nonce,
        max_age=1800,
        path="/api/auth",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    return result


@router.post("/login/email")
def login(body: LoginCode, request: Request, response: Response, service: Service):
    """验证码 + nonce 换会话；成功后清掉 nonce，一次性流程不留尾巴。"""
    nonce = nonce_from_request(request)
    user = call(service.login_by_email, nonce, body.request_id, body.code)
    response.delete_cookie(_NONCE_COOKIE, path="/api/auth")
    return _issue_session(user, request, response)
