"""待办的 HTTP 端点。

  GET    /api/tasks?scope=today|upcoming|all|completed&limit=100
  GET    /api/tasks/summary
  POST   /api/tasks
  GET    /api/tasks/{id}
  PATCH  /api/tasks/{id}
  DELETE /api/tasks/{id}
  POST   /api/tasks/{id}/complete
  POST   /api/tasks/{id}/reopen

## 三条与提醒中心同一口径的硬约束

1. **路由顺序**：`/summary` 必须写在 `/{task_id}` 前面。写成后面的话
   FastAPI 会把它当成 id，返回「待办不存在」—— 而前端只会显示一片空列表。
2. **不带尾斜杠**：全局 `redirect_slashes=False`，带斜杠会 307。
3. **跨用户一律 404**：归属校验写在 `task_core` 的 SQL 里，猜中别人的 id
   也只能拿到 None。

这里只做 HTTP 与参数整形，业务全在 `core/task_core.py` ——
worker 那条路径（`service/reminder_tick.py`）不该为了改一个字段而重写一遍。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import task_core
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


class TaskIn(BaseModel):
    """新建待办。`due_at` 与 `remind_at` 是两个字段，别在前端合成一个。"""

    title: str = Field(..., min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
    list_key: str = Field(default="inbox")
    priority: int = Field(default=0, ge=0, le=2)
    due_at: int | None = None
    remind_at: int | None = None
    # Phase 1 只接受 none；给非空值是 400 而不是静默忽略
    recurrence: str = Field(default="none")
    parent_id: str | None = None


class TaskPatch(BaseModel):
    """局部更新。全部可选；**显式 null 表示清空**（端点用 exclude_unset）。"""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
    list_key: str | None = None
    priority: int | None = Field(default=None, ge=0, le=2)
    due_at: int | None = None
    remind_at: int | None = None


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_tasks(
    scope: str = "today", limit: int = 100, uid: str = Depends(get_current_user)
) -> dict:
    if scope not in task_core.SCOPES:
        raise HTTPException(
            status_code=400, detail="scope 只能是 " + " / ".join(task_core.SCOPES)
        )
    try:
        items = await asyncio.to_thread(task_core.list_tasks, uid, scope, limit)
    except task_core.TaskInvalid as exc:
        raise _bad(exc) from None
    return {"items": items, "scope": scope, "total": len(items)}


@router.get("/summary")
async def get_summary(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(task_core.summary, uid)


@router.post("")
async def create_task(body: TaskIn, uid: str = Depends(get_current_user)) -> dict:
    try:
        item = await asyncio.to_thread(task_core.create, uid, body.model_dump())
    except (task_core.TaskInvalid, task_core.TaskLimit) as exc:
        raise _bad(exc) from None
    return item


@router.get("/{task_id}")
async def get_task(task_id: str, uid: str = Depends(get_current_user)) -> dict:
    item = await asyncio.to_thread(task_core.get, uid, task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    return item


@router.patch("/{task_id}")
async def patch_task(
    task_id: str, body: TaskPatch, uid: str = Depends(get_current_user)
) -> dict:
    # ★ exclude_unset 而不是 exclude_none：`{"due_at": null}` 必须能传下去，
    #   否则「清空截止时间」在界面上永远失败，而且是静默失败。
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="没有要改的字段")
    try:
        item = await asyncio.to_thread(task_core.update, uid, task_id, patch)
    except task_core.TaskInvalid as exc:
        raise _bad(exc) from None
    if item is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    return item


@router.delete("/{task_id}")
async def delete_task(task_id: str, uid: str = Depends(get_current_user)) -> dict:
    ok = await asyncio.to_thread(task_core.delete, uid, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="待办不存在")
    return {"ok": True, "id": task_id}


@router.post("/{task_id}/complete")
async def complete_task(task_id: str, uid: str = Depends(get_current_user)) -> dict:
    item = await asyncio.to_thread(task_core.complete, uid, task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    return item


@router.post("/{task_id}/reopen")
async def reopen_task(task_id: str, uid: str = Depends(get_current_user)) -> dict:
    item = await asyncio.to_thread(task_core.reopen, uid, task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    return item
