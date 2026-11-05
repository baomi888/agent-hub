"""用户偏好端点（板块 D 的「自动沉淀」开关）。

  GET /api/prefs   读偏好（无行时返回默认值，不 404）
  PUT /api/prefs   改偏好（只改传入项）

为什么单独一个路由而不是塞进 /api/profile：`/api/profile` 管的是"我是谁"
（兴趣标签、画像），这里管的是"我愿意让系统自动做什么"。两者生命周期不同
——画像每天聚合一次，偏好是用户一点就生效的行为开关。

默认值只有一处定义（`core/sediment_store._DEFAULTS`）：端点不兜默认、
不在建表时给每个用户插一行，否则"默认值"会变成两处，改一处就会不一致。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

from core import sediment_store
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


class PrefsIn(BaseModel):
    """只用 StrictBool：字符串 "false" 不该被悄悄当成 False。

    前端发的是真正的 JSON 布尔，这里严格一点可以挡住"把开关值当文案传上来"
    这类错误（否则用户以为自己关了自动沉淀，实际上一直开着）。
    """

    auto_sediment: StrictBool | None = None
    auto_create_kb: StrictBool | None = None


@router.get("")
async def read_prefs(uid: str = Depends(get_current_user)) -> dict:
    """读偏好。没有行长这样：`{"auto_sediment": true, "auto_create_kb": true}`。"""
    return await asyncio.to_thread(sediment_store.get_prefs, uid)


@router.put("")
async def update_prefs(body: PrefsIn, uid: str = Depends(get_current_user)) -> dict:
    """改偏好。只改传入的项，返回改完的完整对象。"""
    fields = {k: bool(v) for k, v in body.model_dump(exclude_none=True).items()}
    if not fields:
        # 空请求体：不是错误，把当前值原样返回（幂等语义）
        return await asyncio.to_thread(sediment_store.get_prefs, uid)
    return await asyncio.to_thread(sediment_store.set_prefs, uid, **fields)
