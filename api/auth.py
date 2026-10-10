# -*- coding: utf-8 -*-
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

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from core import account, config, quota
from core.auth import (
    create_token,
    get_current_user,
    clear_session_cookie,
    set_session_cookie,
)

router = APIRouter()


class Credentials(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserOut(BaseModel):
    id: str
    username: str


class MeOut(BaseModel):
    user: UserOut
    quota: dict


def _reject(msg: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail=msg)


def _auth_rate_bucket(name: str) -> str:
    """登录/注册单独限流：按用户名分桶，避免整站共用限流把正常问答也挡了。"""
    return "auth:%s" % (name or "-")


# ==================== 公开端点 ====================

@router.get("/config/")
def auth_config() -> dict:
    """前端登录页要用的开关：这站还开不开自助注册。"""
    return {"allow_signup": config.ALLOW_SIGNUP}


@router.post("/register/", response_model=UserOut)
def register(body: Credentials, response: Response):
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

    set_session_cookie(response, create_token(user["id"]))
    return UserOut(id=user["id"], username=user["username"])


@router.post("/login/", response_model=UserOut)
def login(body: Credentials, response: Response):
    try:
        quota.hit(_auth_rate_bucket(body.username), limit=10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    user = account.authenticate(body.username, body.password)
    if not user:
        # 用户名不存在与密码错返回同一句话，不提供枚举线索
        _reject("用户名或密码不正确", 401)

    set_session_cookie(response, create_token(user["id"]))
    return UserOut(id=user["id"], username=user["username"])


@router.post("/logout/")
def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"ok": True}


# ==================== 需登录 ====================

@router.get("/me/", response_model=MeOut)
async def me(uid: str = Depends(get_current_user)):
    """当前身份。前端用它判断"要不要弹登录"，以及展示今日用量。"""
    row = await asyncio.to_thread(account.get_user, uid)
    if not row:
        # 账号被删了但 token 还有效（7 天内）——别当作系统错误，直接判未登录
        raise HTTPException(status_code=401, detail="账号不存在，请重新登录")
    snap = await asyncio.to_thread(quota.snapshot, uid)
    return MeOut(user=UserOut(id=uid, username=row["username"]), quota=snap)
