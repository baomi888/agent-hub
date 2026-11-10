"""校园工具集 · 专注记录的 HTTP 端点（B6）。

  POST  /api/campus/focus/sessions        批量上报会话（按 id 幂等，重复上报不重复计）
  GET   /api/campus/focus/sessions?from&to 拉取区间会话
  GET   /api/campus/focus/tasks           拉取任务库
  POST  /api/campus/focus/tasks           批量上报任务（updatedAt 后写胜）
  PATCH /api/campus/focus/tasks/{id}      改单个任务

约定与项目其余端点一致：
  - 整个 router 挂 `Depends(get_current_user)`，每个端点也显式取 uid；
  - **不带尾斜杠**（避免 Next.js 去尾斜杠 + FastAPI 补尾斜杠的 308 黑洞）；
  - 业务逻辑全在 `service/focus.py`，这里只做参数校验与线程切换。

`summary` 端点没做：统计在前端已经能算（`logic/focus.ts`），服务端再算一遍
等于同一个口径维护两份实现，哪天对不上没人说得清谁是对的。等有了「跨用户对比」
之类的需求再加。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from core.auth import get_current_user
from service import focus

router = APIRouter(dependencies=[Depends(get_current_user)])


def _rows(body: dict, key: str) -> list:
    rows = body.get(key)
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail=f"{key} 必须是数组")
    if len(rows) > focus.MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {focus.MAX_BATCH} 条，请分批上报",
        )
    return rows


@router.post("/sessions")
async def post_sessions(body: dict, uid: str = Depends(get_current_user)) -> dict:
    rows = _rows(body, "sessions")
    result = await asyncio.to_thread(focus.upsert_sessions, uid, rows)
    return {"ok": True, **result}


@router.get("/sessions")
async def get_sessions(
    # 查询串里的参数名叫 `from`，但那是 Python 关键字，所以用 alias 接上
    from_: int = Query(0, alias="from"),
    to: int = 0,
    limit: int = 5000,
    uid: str = Depends(get_current_user),
) -> dict:
    rows = await asyncio.to_thread(focus.list_sessions, uid, from_, to, limit)
    return {"sessions": rows}


@router.get("/tasks")
async def get_tasks(uid: str = Depends(get_current_user)) -> dict:
    tasks = await asyncio.to_thread(focus.list_tasks, uid)
    return {"tasks": tasks}


@router.post("/tasks")
async def post_tasks(body: dict, uid: str = Depends(get_current_user)) -> dict:
    rows = _rows(body, "tasks")
    result = await asyncio.to_thread(focus.upsert_tasks, uid, rows)
    return {"ok": True, **result}


@router.patch("/tasks/{task_id}")
async def patch_task(task_id: str, body: dict, uid: str = Depends(get_current_user)) -> dict:
    task = await asyncio.to_thread(focus.patch_task, uid, task_id, body)
    if task is None:
        raise HTTPException(status_code=404, detail="没有这个任务，或没有可更新的字段")
    return {"ok": True, "task": task}
