"""邮件提醒的 HTTP 端点。

  GET    /api/mail   当前状态（地址、验没验、开关、服务端有没有配发信账号）
  POST   /api/mail   登记地址并发一封验证码
  POST   /api/mail/verify  填验证码
  PATCH  /api/mail   开 / 关
  DELETE /api/mail   彻底删掉

## 为什么状态里要返回 `ready`

服务端没配 SMTP 时，前端如果还摆着输入框，用户会认真填一遍地址、点发送、
等一封永远不来的邮件 —— 然后怀疑是自己地址写错了。所以 `ready=false` 时
界面必须直接说「服务端还没配发信账号」，把输入框收起来。

同一条原则也适用于 `verified`：填了但没验的地址不能显示成「已开启」，
否则用户会以为已经生效，而实际上派发时会被跳过（见 `mail_store.verified_address`）。

## 为什么发码是 400 而不是 500

`MailCooldown` / `MailQuota` / `MailInvalid` 都是「你操作得太快」或「地址不对」，
是用户能自己解决的，转成 400/429 带中文说明；只有 SMTP 服务器那边真的坏了
（授权码错、连不上）才是 502 —— 那是我们这边要修的事，不该让用户改自己的输入。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import config, mail_store, mailer
from core.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


class AddressIn(BaseModel):
    address: str = Field(..., max_length=254)


class CodeIn(BaseModel):
    code: str = Field(..., max_length=12)


class EnabledIn(BaseModel):
    enabled: bool


def _view(owner_id: str) -> dict[str, Any]:
    state = mail_store.get(owner_id) or {}
    return {
        "ready": config.mail_ready(),
        "address": state.get("address"),
        "verified": bool(state.get("verified")),
        "enabled": bool(state.get("enabled")),
        "pending": bool(state.get("pending")),
        "code_expires_in": int(state.get("code_expires_in") or 0),
        "attempts_left": int(state.get("attempts_left") or 0),
        # 发件人是谁：用户要能在自己邮箱里搜到「这封信是哪来的」。
        # 只给地址不给密码，SMTP_PASSWORD 永远不出这个进程。
        "from": config.SMTP_FROM if config.mail_ready() else None,
    }


@router.get("")
async def get_mail(uid: str = Depends(get_current_user)) -> dict[str, Any]:
    return await asyncio.to_thread(_view, uid)


@router.post("")
async def start(body: AddressIn, uid: str = Depends(get_current_user)) -> dict[str, Any]:
    """登记地址并发验证码。

    顺序刻意是「先发信、后算成功」：`start_verification` 先把码写进库并占住
    冷却窗口，发信失败时 `rollback` 把计数和冷却都退回去 —— 用户不该为我们
    这边的网络抖动白等 60 秒。
    """
    if not config.mail_ready():
        raise HTTPException(status_code=400, detail="服务端还没配发信账号，邮件提醒暂时用不了")

    try:
        address = mail_store.normalize(body.address)
        code = await asyncio.to_thread(mail_store.start_verification, uid, address)
    except mail_store.MailQuota as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except mail_store.MailCooldown as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except mail_store.MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    text = (
        f"你的验证码是 {code}，10 分钟内有效。\n\n"
        "把它填到「提醒 → 邮件提醒」里就能开启。\n\n"
        f"这个验证码是用来验证 {address} 的。不是你操作的，忽略这封邮件即可，"
        "地址不会被启用。\n"
    )
    res = await asyncio.to_thread(mailer.send, address, "苞米 · 验证你的邮箱", text)

    if not res.get("ok"):
        await asyncio.to_thread(mail_store.rollback, uid)
        # 配置类错误直接告诉用户「不是你的问题」，网络类错误说「稍后再试」。
        if res.get("error") in ("auth_failed", "sender_refused"):
            logger.error("mail config broken: %s", res.get("error"))
            raise HTTPException(status_code=502, detail="发信账号验证失败，请联系管理员检查配置")
        if res.get("retryable"):
            raise HTTPException(status_code=503, detail="信件服务暂时连不上，稍后再试")
        raise HTTPException(status_code=400, detail=str(res.get("detail") or "这封邮件发不出去"))

    return {"ok": True, "address": address, "expires_in": mail_store.CODE_TTL, **_view(uid)}


@router.post("/verify")
async def verify(body: CodeIn, uid: str = Depends(get_current_user)) -> dict[str, Any]:
    try:
        await asyncio.to_thread(mail_store.verify, uid, body.code)
    except mail_store.MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(uid)


@router.patch("")
async def toggle(body: EnabledIn, uid: str = Depends(get_current_user)) -> dict[str, Any]:
    try:
        await asyncio.to_thread(mail_store.set_enabled, uid, body.enabled)
    except mail_store.MailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(uid)


@router.delete("")
async def remove(uid: str = Depends(get_current_user)) -> dict[str, Any]:
    await asyncio.to_thread(mail_store.clear, uid)
    return _view(uid)
