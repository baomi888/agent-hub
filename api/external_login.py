"""微信、支付宝网站授权共用的浏览器流程。平台由挂载路由时的配置固定。"""

import re
import secrets
import sqlite3
from collections.abc import Callable
from typing import Literal, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from api.auth import _client_ip, _issue_session
from core import account, config, external_accounts, quota
from core.auth import _is_secure_request, get_current_user
from core.external_identity import ExternalAuthError, ExternalIdentity

_NONCE = re.compile(r"[A-Za-z0-9_-]{32,128}")

# state 自包含编码：跳到支付宝 App 授权后，回调发生在支付宝内置 WebView，
# 本站的浏览器 cookie 和登录态都带不过去。把 browser nonce 与发起绑定的 uid
# 一起编进 state 由支付宝原样回传，回调时拆回来核验，既不依赖 cookie 也不
# 削弱 CSRF（state 仍是一次性、随机、600 秒过期）。
# token_urlsafe(32) 固定 43 字符，uuid4().hex 固定 32 字符，按定长切片。
_SLICE_STATE = 43
_SLICE_BROWSER = 43
_EMPTY_UID = "_" * 32  # hex 不含下划线，绝不会与真实 uid 撞
_COMBINED_LEN = _SLICE_STATE + _SLICE_BROWSER + 32


def _encode_state(state: str, browser: str, uid: str) -> str:
    return state + browser + (uid or _EMPTY_UID)


def _decode_state(combined: str) -> tuple[str, str, str]:
    if len(combined) != _COMBINED_LEN or not _NONCE.fullmatch(combined):
        raise ExternalAuthError("flow_invalid", "授权流程已失效")
    state = combined[:_SLICE_STATE]
    browser = combined[_SLICE_STATE : _SLICE_STATE + _SLICE_BROWSER]
    uid_field = combined[_SLICE_STATE + _SLICE_BROWSER :]
    return state, browser, "" if uid_field == _EMPTY_UID else uid_field


class WebsiteAdapter(Protocol):
    def enabled(self) -> bool: ...
    def authorize_url(self, state: str) -> str: ...
    def authenticate(self, code: str) -> ExternalIdentity: ...


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["login", "bind", "unlink"] = "login"
    # 支付宝/微信 App 内唤起授权时，回调脱离本站浏览器、cookie 带不过去。
    # 移动端显式开启后把 browser+uid 编进 state 自携带；PC 不传，走原 cookie 校验。
    app_flow: bool = False


def _current_uid(request: Request) -> str:
    try:
        return get_current_user(request)
    except HTTPException:
        return ""


def website_login_router(
    *,
    provider: str,
    label: str,
    adapter: WebsiteAdapter,
    app_id: Callable[[], str],
    redirect_uri: Callable[[], str],
    code_param: str,
    callback_app_param: str | None = None,
    allow_callback_probe: bool = False,
) -> APIRouter:
    router = APIRouter(prefix=f"/{provider}")
    browser_cookie = f"zhisui_{provider}_browser"

    @router.post("/start")
    def start(body: StartBody, request: Request, response: Response):
        if not adapter.enabled():
            raise HTTPException(503, f"{label}登录尚未配置完成")
        expected = urlsplit(redirect_uri())
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and origin != f"{expected.scheme}://{expected.netloc}"
        ):
            raise HTTPException(403, "请从本站发起操作")
        uid = _current_uid(request)
        if body.action == "login" and uid:
            raise HTTPException(409, f"你已登录，请在设置中绑定{label}")
        if body.action != "login" and not uid:
            raise HTTPException(401, "请先登录本站账号")
        try:
            quota.hit(f"{provider}:start:{_client_ip(request)}", limit=config.LOGIN_IP_PER_MINUTE)
            browser = request.cookies.get(browser_cookie, "")
            if not _NONCE.fullmatch(browser):
                browser = secrets.token_urlsafe(32)
            state = external_accounts.begin_flow(browser, app_id(), body.action, uid, provider=provider)
        except quota.RateLimited as exc:
            raise HTTPException(429, str(exc)) from None
        except ExternalAuthError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        response.set_cookie(
            browser_cookie,
            browser,
            max_age=600,
            httponly=True,
            secure=_is_secure_request(request),
            samesite="lax",
            path=f"/api/auth/{provider}",
        )
        response.headers["Cache-Control"] = "no-store"
        # App 唤起场景把 browser 与 uid 编进 state 一起回传：回调时本站
        # cookie 不在，靠 state 自带的信息核验。PC 不开启，保持 cookie 绑定。
        authorize_state = _encode_state(state, browser, uid) if body.app_flow else state
        return {"authorize_url": adapter.authorize_url(authorize_state)}

    @router.get("/callback")
    def callback(request: Request):
        # 平台保存回调地址时会直接访问此 URL；带参数的授权回调仍完整核验。
        if allow_callback_probe and not request.url.query:
            return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})
        failure_page = f"/login?{provider}_result=failed"
        app_flow = False
        handoff_state = ""
        try:
            if not adapter.enabled():
                raise ExternalAuthError("not_configured", f"{label}登录尚未配置完成", 503)
            query = request.query_params
            states, codes = query.getlist("state"), query.getlist(code_param)
            if len(states) != 1 or len(codes) > 1:
                raise ExternalAuthError("flow_invalid", "授权流程已失效")
            # 优先从 state 解出自带凭证（支付宝 App 内回调场景，无本站 cookie）。
            # 兼容旧流程：解不出来时回退到 cookie + 登录态，保证已发出的授权不被打断。
            try:
                state_value, browser, uid = _decode_state(states[0])
                app_flow = True
            except ExternalAuthError:
                state_value = states[0]
                browser = request.cookies.get(browser_cookie, "")
                uid = _current_uid(request)
            handoff_state = state_value
            if not _NONCE.fullmatch(state_value) or not _NONCE.fullmatch(browser):
                raise ExternalAuthError("flow_invalid", "授权流程已失效")
            app = app_id()
            if callback_app_param and callback_app_param in query and query.getlist(callback_app_param) != [app]:
                raise ExternalAuthError("flow_invalid", "授权应用不匹配")
            # 换号攻击防护：回调方带着本站登录态时，必须和 flow 发起人一致。
            # 支付宝 App 内没有本站登录态（uid 为空），此时信任 state 自带的 uid。
            current_uid = _current_uid(request)
            if current_uid and uid and current_uid != uid:
                raise ExternalAuthError("flow_invalid", "账号已切换，请重新发起")
            flow = external_accounts.consume_flow(state_value, browser, app, uid, provider=provider)
            action = flow["action"]
            if action != "login":
                failure_page = f"/settings?{provider}_result=failed"
            # 用户取消可能只带回 state。此时仍应结束本次流程并回到发起页面。
            if not codes or not codes[0] or len(codes[0]) > 512 or "error" in query:
                raise ExternalAuthError("provider_failed", "授权未完成")
            identity = adapter.authenticate(codes[0])
            if action == "login" and not external_accounts.identity_exists(identity):
                ip = _client_ip(request)
                quota.hit(f"signup:ip:{ip}", limit=config.SIGNUP_IP_PER_MINUTE)
                quota.consume_key(f"signup:ip:{ip}", "signup_ip", config.SIGNUP_IP_PER_DAY)
                quota.consume_key("signup:global", "signup_global", config.SIGNUP_GLOBAL_PER_DAY)
            user = external_accounts.complete(identity, flow)
            if app_flow:
                # 回调发生在支付宝内置 WebView：登录态若种在这里，用户切回
                # 原应用仍是未登录。改为把结果暂存，由原应用轮询领走并就地
                # 签发会话 —— 在哪里发起，就在哪里登录进去。
                external_accounts.put_handoff(
                    state_value, provider=provider, action=action, user_id=user["id"]
                )
                response = PlainTextResponse("授权成功，请返回应用继续。")
            else:
                success_url = "/"
                if action != "login":
                    result = "bound" if action == "bind" else "unlinked"
                    success_url = f"/settings?{provider}_result={result}"
                response = RedirectResponse(success_url, status_code=303)
                _issue_session(user, request, response)
        except ExternalAuthError as exc:
            if app_flow and handoff_state:
                external_accounts.put_handoff(
                    handoff_state, provider=provider, action="login",
                    result="failed", reason=exc.code,
                )
            response = RedirectResponse(f"{failure_page}&reason={exc.code}", status_code=303)
        except (quota.RateLimited, quota.QuotaExceeded):
            if app_flow and handoff_state:
                external_accounts.put_handoff(
                    handoff_state, provider=provider, action="login",
                    result="failed", reason="rate_limited",
                )
            response = RedirectResponse(f"{failure_page}&reason=rate_limited", status_code=303)
        except sqlite3.Error:
            if app_flow and handoff_state:
                external_accounts.put_handoff(
                    handoff_state, provider=provider, action="login",
                    result="failed", reason="service_unavailable",
                )
            response = RedirectResponse(f"{failure_page}&reason=service_unavailable", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.get("/poll")
    def poll(request: Request, response: Response, state: str = ""):
        """App 内授权完成后，原应用轮询领回结果并就地种上会话。

        凭证是 start 时签发的 state（原样回传）而不是支付宝给的 auth_code，
        因为 auth_code 只有回调那一次拿得到。领取是一次性的：拿到后交接记录
        即删除，别人拿到同一 state 也不能二次兑换。
        """
        try:
            state_value, browser, _ = _decode_state(state)
        except ExternalAuthError:
            raise HTTPException(400, "轮询参数无效") from None
        # 必须和发起方同一个浏览器 nonce：支付宝 WebView 里没有这个 cookie，
        # 所以只有真正发起授权的那台设备能领走登录态。
        if not secrets.compare_digest(request.cookies.get(browser_cookie, ""), browser):
            raise HTTPException(403, "授权流程不属于当前浏览器")
        record = external_accounts.take_handoff(state_value, provider=provider)
        if record is None:
            return {"status": "pending"}
        response.headers["Cache-Control"] = "no-store"
        if record["result"] != "ok":
            return {"status": "failed", "reason": record["reason"]}
        user = account.get_user(record["user_id"])
        if not user:
            return {"status": "failed", "reason": "account_unavailable"}
        _issue_session(user, request, response)
        return {"status": "ok", "action": record["action"]}

    @router.get("/status")
    def status(uid: str = Depends(get_current_user)):
        return {"enabled": adapter.enabled(), "bound": external_accounts.is_bound(uid, app_id(), provider=provider)}

    return router
