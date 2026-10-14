# -*- coding: utf-8 -*-
"""调度可观测与手动触发端点。

  GET  /api/schedule/health    worker 心跳 + 各 job 最近执行情况
  GET  /api/schedule/jobs      任务定义表（cron / 开关 / 下次触发）
  GET  /api/schedule/runs      执行历史（游标分页）
  POST /api/schedule/run-now   手动触发一个任务（管理员）
  POST /api/schedule/retry     手动重排一次失败执行（管理员）

三条口径：

1. **本文件不 import apscheduler**。web 进程只读调度表、写一行「待执行」，
   真正的执行在独立 worker 进程（`worker/scheduler.py`）。
   好处是 web 少一个重依赖，也不会因为 worker 没起就让接口 500——
   健康端点要能回答「worker 是不是挂了」，它自己就不能依赖 worker。
2. **同步 I/O 一律 `asyncio.to_thread`**：SQLite 写约 2ms，但这是本项目既定规则
   （`core/quota.py:15-18` 的 docstring 明写），别因为快就裸调。
3. **健康判定只看心跳年龄**（>600s 判 down），不看进程表：
   进程在但卡死（比如被一次 LLM 调用挂住）同样要被抓到，
   而「多久没心跳」这个绝对量对崩溃和卡死是同一个判据。
"""

from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import quota
from core import scheduler as sched
from core.auth import get_current_user

# 默认要登录：整组路由统一挂鉴权，新增端点时不会漏（照 api/chat.py:46）
router = APIRouter(dependencies=[Depends(get_current_user)])

# 心跳年龄超过它判 down。放这里是为了让「阈值」在端点层也看得见，
# 真正的比较在 core/scheduler.py::health()
HEARTBEAT_DOWN_SECONDS = sched.HEARTBEAT_DOWN_SECONDS


class RunNowRequest(BaseModel):
    job_key: str = Field(..., description="任务 key，见 GET /api/schedule/jobs")
    subject_id: str | None = Field(default=None, description="指定单个对象；缺省表示全量")
    dry_run: bool = Field(default=False, description="true 时只算 idem_key，不落库")


class RetryRequest(BaseModel):
    run_id: str = Field(..., description="job_runs.run_id")
    force: bool = Field(default=False, description="true 时清空重试计数强制重排")


def _require_admin(uid: str) -> None:
    """管理员判定：按用户名比对 config.ADMIN_USERNAMES。

    直接复用 `core/quota._is_admin`，不在这里重写一份：
    两处规则一旦漂移，就会出现「配额系统认为你是管理员、调度端点认为你不是」
    这种最难查的 bug（core/quota.py:133-146 是这套口径的唯一实现）。
    """
    if not quota._is_admin(uid):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/health")
async def schedule_health() -> dict:
    """调度健康状态：worker 心跳 + 各 job 最后执行情况。

    worker.status ∈ up / stalled / down：
      超过一个 dispatch 周期（300s）没心跳 → stalled（黄灯，提前给运维）；
      超过 HEARTBEAT_DOWN_SECONDS（600s）→ down。
    """
    return await asyncio.to_thread(sched.health, HEARTBEAT_DOWN_SECONDS)


@router.get("/jobs")
async def schedule_jobs(enabled: bool | None = None) -> list[dict]:
    """任务定义表。周期改这张表，不改代码。"""
    return await asyncio.to_thread(partial(sched.list_jobs, enabled=enabled))


@router.get("/runs")
async def schedule_runs(
    job_key: str | None = None,
    status: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> dict:
    """执行历史（新→旧，游标分页）。

    动作先于结果的排查入口：`status=failed` 一眼看到最近失败，
    配合 `error` / `attempts` / `next_attempt_at` 判断它还会不会再试。
    """
    try:
        return await asyncio.to_thread(
            partial(sched.list_runs, limit=limit, cursor=cursor, job_key=job_key, status=status)
        )
    except sched.InvalidCursor:
        raise HTTPException(status_code=400, detail="cursor 无效")


@router.post("/run-now", status_code=202)
async def schedule_run_now(req: RunNowRequest, uid: str = Depends(get_current_user)) -> dict:
    """手动触发一个调度任务（不等待执行结果）。

    不另开执行路径：写一行 status=pending、planned_at=now 的 job_runs，
    worker 下一轮扫到就执行——手动与定时走同一条路径，才不会出现
    「手动能跑、定时不能跑」这类只在生产暴露的差异。
    """
    await asyncio.to_thread(quota.hit, uid)
    await asyncio.to_thread(_require_admin, uid)
    if req.job_key not in sched.JOB_DEFS:
        raise HTTPException(status_code=400, detail="未知的 task key")

    planned_at = sched.now_ts()
    # 全量触发用 "*" 作 subject_id：它要表达的就是「这一轮的全部对象」，
    # 幂等键因此也稳定（同一秒内重复点两次只会排进一次）
    subject_id = req.subject_id or "*"
    if req.dry_run:
        return {
            "accepted": True,
            "job_key": req.job_key,
            "queued": 0,
            "idem_key": sched.idem_key(req.job_key, subject_id, planned_at),
        }
    run_id, idem = await asyncio.to_thread(
        partial(sched.enqueue_job, req.job_key, subject_id, planned_at, trigger="manual")
    )
    return {
        "accepted": True,
        "job_key": req.job_key,
        "queued": 1 if run_id else 0,  # 0 = 同一幂等键已排过（重复点击/重放）
        "idem_key": idem,
    }


@router.post("/retry")
async def schedule_retry(req: RetryRequest, uid: str = Depends(get_current_user)) -> dict:
    """手动重排一次失败执行（管理员）。

    不直接执行，只把 `next_attempt_at` 提前到现在，让 worker 的重试扫描立刻捡起来。
    force=true 时清空 attempts —— 给「3 次重试已用完但我想再试一次」留一个出口，
    没有这个出口，唯一的办法就是去改库。
    """
    await asyncio.to_thread(quota.hit, uid)
    await asyncio.to_thread(_require_admin, uid)
    res = await asyncio.to_thread(partial(sched.requeue_run, req.run_id, force=req.force))
    if not res["ok"]:
        if res["reason"] == "not_found":
            raise HTTPException(status_code=404, detail="执行记录不存在")
        if res["reason"] == "exhausted":
            raise HTTPException(
                status_code=409,
                detail="已达重试上限（%d），需 force=true 才能强制重排" % sched.MAX_ATTEMPTS,
            )
        raise HTTPException(status_code=409, detail="只有失败的执行才能重试")

    run = res["run"]
    return {
        "retried": True,
        "run_id": run["run_id"],
        "job_key": run["job_key"],
        "status": run["status"],
        "attempts": run["attempts"],
        "next_attempt_at": run["next_attempt_at"],
    }
