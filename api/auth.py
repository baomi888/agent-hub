from __future__ import annotations

"""登录注册路由。

这是全站**唯一**的公开路由组（其它业务路由都在 main.py 里挂了
`Depends(get_current_user)`，默认要登录）。豁免面刻意压到最小，
将来新增端点时"忘了鉴权"这件事在结构上就不容易发生——
本项目历史上已经因为"同一规则这处遵守、那处漏了"反复踩坑。

路由清单：
  GET  /api/auth/config/    公开：是否开放注册
  POST /api/auth/register/  公开：注册并直接登录
  POST /api/auth/login/     公开：登录
  POST /api/auth/logout/    公开：退出（未登录时调用也返回 ok）
  GET  /api/auth/me/        需登录：当前身份 + 今日用量
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core import account, config, quota, token_store
from core.auth import (
    CLAIM_JTI,
    clear_session_cookie,
    create_token,
    decode_claims,
    extract_token,
    get_current_user,
    set_session_cookie,
)

router = APIRouter()


class Credentials(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserOut(BaseModel):
    id: str
    username: str
    # 只在 AUTH_RETURN_TOKEN=1 时回吐；浏览器侧靠 HttpOnly Cookie，不需要它
    token: str | None = None


class MeOut(BaseModel):
    user: UserOut
    quota: dict
    # 是否开发者（ADMIN_USERNAMES）。前端据此决定「数据看板」入口要不要显示。
    # 默认 False：老前端拿到不带这个字段的响应也不会崩，未知一律当"不是管理员"
    # —— 权限判定的默认值必须是拒绝的那一侧。
    is_admin: bool = False


def _reject(msg: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail=msg)


def _auth_rate_bucket(name: str) -> str:
    """登录/注册单独限流：按用户名分桶，避免整站共用限流把正常问答也挡了。"""
    return "auth:%s" % (name or "-")


def _issue_session(user: dict, request: Request, response: Response) -> UserOut:
    """签发令牌并落到两个地方：HttpOnly Cookie + （可选）响应体。

    两条通道一起给，是因为客户端形态不同：
      - 浏览器：Cookie 自动带，JS 读不到，XSS 顺不走；
      - App / 小程序 / 压测 / Swagger：没有 Cookie 罐子，用 `X-Auth-Token`
        或 `Authorization: Bearer` 把响应体里的 token 带回来。

    响应体里的 token 默认关闭（AUTH_RETURN_TOKEN=0）：给了 JS 就等于把
    XSS 的收益从"偷不到"变成"直接拿走 7 天会话"。
    """
    token = create_token(user["id"], username=user["username"])
    set_session_cookie(response, token, request)
    return UserOut(
        id=user["id"],
        username=user["username"],
        token=token if config.AUTH_RETURN_TOKEN else None,
    )


# ==================== 公开端点 ====================


@router.get("/config/")
def auth_config() -> dict:
    """前端登录页要用的开关：这站还开不开自助注册。"""
    return {"allow_signup": config.ALLOW_SIGNUP}


@router.post("/register/", response_model=UserOut)
def register(body: Credentials, request: Request, response: Response):
    if not config.ALLOW_SIGNUP:
        _reject("本站已关闭自助注册，请联系管理员开通账号", 403)

    err = account.check_name(body.username) or account.check_password(body.password)
    if err:
        _reject(err)

    # 注册/登录单独限流：撞库脚本最爱的就是这两个端点
    try:
        quota.hit(_auth_rate_bucket(body.username), limit=10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    try:
        user = account.create_user(body.username, body.password)
    except Exception:
        # 唯一索引冲突 = 重名。这里吞掉 sqlite 的具体类型，
        # 免得数据库的错误信息一路漏到前端
        _reject("该用户名已被占用", 409)

    return _issue_session(user, request, response)


@router.post("/login/", response_model=UserOut)
def login(body: Credentials, request: Request, response: Response):
    """登录：查账号 → 验口令 → 验状态 → 签令牌（对齐 sky-take-out 的四步）。

    与之的差异只有一处，写在下面 if 里：账号不存在与密码错误对外合并成一句话。
    """
    try:
        quota.hit(_auth_rate_bucket(body.username), limit=10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    user, reason = account.login(body.username, body.password)

    if reason == account.LOGIN_LOCKED:
        # 必须与"密码错"区分：用户要知道是号被停了，而不是自己记错密码
        _reject("账号已被停用，请联系管理员", 403)

    if not user:
        # no_user 与 bad_password 返回同一句话：自助注册的站点里，
        # 区分这两者等于免费提供一个"哪些用户名存在"的枚举接口
        _reject("用户名或密码不正确", 401)

    return _issue_session(user, request, response)


@router.post("/logout/")
def logout(request: Request, response: Response) -> dict:
    """退出登录：吊销当前令牌 + 清 Cookie。

    JWT 是无状态的，光删 Cookie 收不回已签出去的令牌（sky-take-out 的
    logout 就是空实现，令牌到期前一直有效）。这里把 jti 写进黑名单，
    让这枚令牌立刻失效——否则"退出登录"只是让浏览器不再自动带，
    换个客户端照样能进。
    """
    claims = decode_claims(extract_token(request)) or {}
    jti = str(claims.get(CLAIM_JTI) or "")
    if jti:
        token_store.revoke(jti, int(claims.get("exp", 0)))
    clear_session_cookie(response)
    return {"ok": True, "revoked": bool(jti)}


# ==================== 需登录 ====================


@router.get("/me/", response_model=MeOut)
async def me(uid: str = Depends(get_current_user)):
    """当前身份。前端用它判断"要不要弹登录"，以及展示今日用量。"""
    row = await asyncio.to_thread(account.get_user, uid)
    if not row:
        # 账号被删了但 token 还有效（7 天内）——别当作系统错误，直接判未登录
        raise HTTPException(status_code=401, detail="账号不存在，请重新登录")
    snap = await asyncio.to_thread(quota.snapshot, uid)
    return MeOut(
        user=UserOut(id=uid, username=row["username"]),
        quota=snap,
        is_admin=quota._is_admin(uid),
    )
