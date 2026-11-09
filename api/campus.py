"""校园工具集云端快照的两个端点（规格 2.3）。

  GET  /api/campus/state               一次拉全部（挂载时用）
  PUT  /api/campus/state/{tool}        整份上报一个键（防抖 2 秒后调）

为什么是 PUT 而不是 POST：同一个 (人, 工具) 只有一个状态，重复调用必须收敛到同一结果，
这是 PUT 的语义。用 POST 会让「网络重试」变成两倍写入，还得自己去做幂等。

冲突（云端更新）返回 **409 + 云端那份**，让前端直接采用云端值。
不用 200 + `conflict: true`：前端拿到 2xx 就会当成"我写成功了"，
而这里的事实是"你的这次写入被丢弃了"，状态码要跟事实一致。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from core import campus_store
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/state")
async def get_state(uid: str = Depends(get_current_user)) -> dict:
    """这个人云端的全部快照。没同步过的工具直接不出现（不是空 payload）。"""
    items = await asyncio.to_thread(campus_store.list_all, uid)
    return {"items": items, "tools": list(campus_store.ALLOWED_TOOLS)}


@router.put("/state/{tool}")
async def put_state(tool: str, body: dict, uid: str = Depends(get_current_user)) -> dict:
    payload = body.get("payload")
    updated_at = body.get("updated_at")
    try:
        res = await asyncio.to_thread(campus_store.put, uid, tool, payload, updated_at)
    except campus_store.CampusInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not res.get("ok"):
        # 409 + 云端值：前端据此整份覆盖本地，不需要自己再拉一次
        raise HTTPException(status_code=409, detail={"reason": "cloud_newer", **res["cloud"]})
    return res
