# -*- coding: utf-8 -*-
"""每日委托的 HTTP 端点。

  GET    /api/delegate                        列出我的委托
  POST   /api/delegate                        新建委托
  GET    /api/delegate/{delegation_id}        取单条
  PATCH  /api/delegate/{delegation_id}        局部更新
  DELETE /api/delegate/{delegation_id}        删除
  POST   /api/delegate/{delegation_id}/run    立即执行一次（调试用，不改 next_run_at）
  GET    /api/delegate/{delegation_id}/runs   执行历史

路由一律不带尾斜杠，与项目其余端点（约 35 条）保持一致；带尾斜杠的旧写法
由 main.py 的 normalize_api_slash 中间件归一，仍能命中，不会 3xx（见
tests/test_slash_no_redirect.py）。

三条设计取舍：

1. **业务能力全在 `service/daily.py`，这里只做 HTTP 与校验。**
   worker 走的是同一个 `service.daily.run_one()`——手动与定时共用一条执行路径，
   才不会出现「手动能跑、定时不能跑」这类只在生产暴露的差异。

2. **配额异常不在这里 catch。** `quota.QuotaExceeded` / `RateLimited` 由 main.py 的全局
   处理器转成 429（带 kind/limit/retry_after）。端点自己 catch 容易顺手转成 500，
   前端就分不清"系统出错"和"你今天用超了"（main.py:173-190 的既有口径）。

3. **跨用户一律 404 而非 403**：不泄露「这个 id 存在但不属于你」（与 api/kb.py:48 同款）。
"""

from __future__ import annotations

import asyncio
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import quota, session as store
from core.auth import get_current_user
from service import daily

router = APIRouter(dependencies=[Depends(get_current_user)])

# 启用中的委托数量上限。定时任务是无人值守地烧 token，必须有硬闸；
# 5 条足够覆盖「早计划 + 晚回顾 + 每周几次」这类真实用法。
MAX_ENABLED = 5

KINDS = ("study_plan", "review", "encourage", "custom")
FREQS = ("daily", "weekdays", "weekly", "interval")
_TONE_MAX = 8
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _now() -> int:
    return int(time.time())


class ScheduleIn(BaseModel):
    """调度配置。结构化表单，不做自然语言解析（那是 20 号后的事）。"""

    freq: str = Field(default="daily", description="daily / weekdays / weekly / interval")
    at: str | None = Field(default=None, description="HH:MM，freq=interval 时可为空")
    weekday: int | None = Field(default=None, ge=0, le=6, description="freq=weekly 时必填，0=周一")
    every_minutes: int | None = Field(default=None, ge=60, le=1440, description="freq=interval 时必填")
    tz: str = Field(default="Asia/Shanghai", description="固定 Asia/Shanghai")


class DelegationIn(BaseModel):
    name: str = Field(..., min_length=3, max_length=32)
    kind: str = Field(default="study_plan")
    schedule: ScheduleIn = Field(default_factory=ScheduleIn)
    prompt: str = Field(default="", max_length=1000)
    deliver_sid: str = Field(..., description="投递到哪个会话（必须是本人的会话）")
    kb_id: str | None = Field(default=None)
    tone: str = Field(default="neutral", max_length=_TONE_MAX)
    max_chars: int = Field(default=600, ge=200, le=2000)
    enabled: bool = Field(default=True)


class DelegationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=32)
    kind: str | None = Field(default=None)
    schedule: ScheduleIn | None = Field(default=None)
    prompt: str | None = Field(default=None, max_length=1000)
    deliver_sid: str | None = Field(default=None)
    kb_id: str | None = Field(default=None)
    tone: str | None = Field(default=None, max_length=_TONE_MAX)
    max_chars: int | None = Field(default=None, ge=200, le=2000)
    enabled: bool | None = Field(default=None)


# ---------- 校验 ----------

def _validate_kind(kind: str) -> None:
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"kind 需为 {' / '.join(KINDS)} 之一")


def _validate_schedule(s: ScheduleIn) -> None:
    if s.freq not in FREQS:
        raise HTTPException(status_code=400, detail=f"schedule.freq 需为 {' / '.join(FREQS)} 之一")
    if s.at is not None and not _HHMM.match(s.at):
        raise HTTPException(status_code=400, detail="schedule.at 需为 HH:MM 格式")
    if s.freq in ("daily", "weekdays", "weekly") and not s.at:
        raise HTTPException(status_code=400, detail=f"schedule.freq={s.freq} 时必须提供 at（HH:MM）")
    if s.freq == "weekly" and s.weekday is None:
        raise HTTPException(status_code=400, detail="schedule.freq=weekly 时必须提供 weekday（0=周一）")
    if s.freq == "interval" and s.every_minutes is None:
        raise HTTPException(status_code=400, detail="schedule.freq=interval 时必须提供 every_minutes")


def _require_own_session(uid: str, sid: str) -> None:
    """投递目标必须是本人的会话：别人的 sid 一律 404（不泄露存在性）。"""
    if not store.get_conversation(uid, sid):
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")


def _enabled_count(uid: str) -> int:
    return sum(1 for d in daily.list_delegations(uid) if d.get("enabled"))


def _must_own(uid: str, did: str) -> dict:
    d = daily.get_delegation(uid, did)
    if not d:
        raise HTTPException(status_code=404, detail="委托不存在")
    return d


def _out(d: dict) -> dict:
    import json as _json

    sched = d.get("schedule_json")
    if isinstance(sched, str) and sched:
        try:
            sched = _json.loads(sched)
        except ValueError:
            sched = None
    # 键名对齐前端契约：id / instruction / session_id 是前端在用的一组名，
    # delegation_id / prompt / deliver_sid 是内部与旧调用方沿用的原名（如 PATCH 的
    # merged 合并、service/daily.py 的落库），两套同时返回，避免任一侧被迫改动。
    return {
        "id": d["delegation_id"],
        "delegation_id": d["delegation_id"],
        "name": d["name"],
        "kind": d["kind"],
        "schedule": sched,
        "instruction": d.get("prompt") or "",
        "prompt": d.get("prompt") or "",
        "session_id": d.get("deliver_sid"),
        "deliver_sid": d.get("deliver_sid"),
        "kb_id": d.get("kb_id"),
        "tone": d.get("tone") or "neutral",
        "max_chars": d.get("max_chars"),
        "enabled": bool(d.get("enabled")),
        "next_run_at": d.get("next_run_at"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


# ---------- 端点 ----------

@router.get("")
async def list_delegations(enabled: bool | None = None, uid: str = Depends(get_current_user)) -> list[dict]:
    """列出我的委托。next_run_at 由服务端算好，前端不做 cron 推算。"""
    items = await asyncio.to_thread(daily.list_delegations, uid)
    out = [_out(d) for d in items]
    if enabled is not None:
        out = [d for d in out if d["enabled"] is enabled]
    return out


@router.post("", status_code=201)
async def create_delegation(req: DelegationIn, uid: str = Depends(get_current_user)) -> dict:
    """新建委托。"""
    await asyncio.to_thread(quota.hit, uid)
    _validate_kind(req.kind)
    _validate_schedule(req.schedule)
    await asyncio.to_thread(_require_own_session, uid, req.deliver_sid)

    existing = await asyncio.to_thread(daily.list_delegations, uid)
    if any(d["name"] == req.name for d in existing):
        raise HTTPException(status_code=409, detail="同名委托已存在")
    if req.enabled and sum(1 for d in existing if d.get("enabled")) >= MAX_ENABLED:
        raise HTTPException(status_code=400, detail=f"启用中的委托数量已达上限（{MAX_ENABLED}）")
    if req.kind == "custom" and not req.prompt.strip():
        raise HTTPException(status_code=400, detail="kind=custom 时必须提供 prompt")

    d = await asyncio.to_thread(
        daily.save_delegation,
        uid,
        req.name,
        req.kind,
        req.schedule.model_dump(),
        req.prompt,
        req.deliver_sid,
        req.kb_id,
        req.tone,
        req.max_chars,
        req.enabled,
    )
    return _out(d)


@router.get("/{delegation_id}")
async def get_delegation(delegation_id: str, uid: str = Depends(get_current_user)) -> dict:
    """取单条。返回 prompt 全文——委托的生成指令必须对用户可见可改。"""
    d = await asyncio.to_thread(_must_own, uid, delegation_id)
    return _out(d)


@router.patch("/{delegation_id}")
async def update_delegation(
    delegation_id: str, req: DelegationPatch, uid: str = Depends(get_current_user)
) -> dict:
    """局部更新。改 schedule / enabled 时 next_run_at 由服务端重算。"""
    await asyncio.to_thread(quota.hit, uid)
    cur = await asyncio.to_thread(_must_own, uid, delegation_id)

    data = req.model_dump(exclude_unset=True)
    if "kind" in data and data["kind"]:
        _validate_kind(data["kind"])
    if "schedule" in data and data["schedule"]:
        _validate_schedule(ScheduleIn(**data["schedule"]) if isinstance(data["schedule"], dict) else data["schedule"])
    if "deliver_sid" in data and data["deliver_sid"]:
        await asyncio.to_thread(_require_own_session, uid, data["deliver_sid"])
    if data.get("enabled") is True and not cur.get("enabled"):
        others = await asyncio.to_thread(daily.list_delegations, uid)
        if sum(1 for d in others if d.get("enabled")) >= MAX_ENABLED:
            raise HTTPException(status_code=400, detail=f"启用中的委托数量已达上限（{MAX_ENABLED}）")

    merged = {**_out(cur), **{k: v for k, v in data.items() if k != "schedule"}}
    if "schedule" in data and data["schedule"]:
        merged["schedule"] = data["schedule"]
    if "name" in data and data["name"] and data["name"] != cur["name"]:
        raise HTTPException(status_code=400, detail="name 不支持修改（它是委托的唯一标识）；请新建一条")

    d = await asyncio.to_thread(
        daily.save_delegation,
        uid,
        cur["name"],
        merged["kind"],
        merged["schedule"],
        merged["prompt"],
        merged["deliver_sid"],
        merged["kb_id"],
        merged["tone"],
        int(merged["max_chars"] or 600),
        bool(merged["enabled"]),
    )
    out = _out(d)
    return {
        "delegation_id": out["delegation_id"],
        "enabled": out["enabled"],
        "next_run_at": out["next_run_at"],
        "updated_at": out["updated_at"],
    }


@router.delete("/{delegation_id}")
async def delete_delegation(delegation_id: str, uid: str = Depends(get_current_user)) -> dict:
    """删除委托。历史执行记录保留（审计需要）。"""
    await asyncio.to_thread(_must_own, uid, delegation_id)
    await asyncio.to_thread(daily.delete_delegation, uid, delegation_id)
    return {"deleted": True, "delegation_id": delegation_id}


@router.post("/{delegation_id}/run")
async def run_delegation(
    delegation_id: str, dry_run: bool = False, uid: str = Depends(get_current_user)
) -> dict:
    """立即执行一次。

    dry_run=true：只生成内容、不投递、不改 next_run_at（前端预览用）。
    否则走 `service.daily.run_one()`——与 worker 定时触发的**同一条路径**，
    并消耗一次 kind="auto" 的自动调用额度（超限由全局处理器转 429）。
    """
    await asyncio.to_thread(quota.hit, uid)
    d = await asyncio.to_thread(_must_own, uid, delegation_id)
    started = _now()

    if dry_run:
        context = await asyncio.to_thread(daily.today_context, uid, quota.day_key())
        content = await asyncio.to_thread(daily.plan_today, d, context)
        return {
            "run_id": None,
            "status": "dry_run",
            "content": content,
            "delivered": False,
            "delivered_message_id": None,
            "ms": (_now() - started) * 1000,
            "tokens_in": 0,
            "tokens_out": 0,
        }

    res = await asyncio.to_thread(daily.run_one, "delegate.dispatch", delegation_id, _now())
    return {
        "run_id": res.get("run_id"),
        "status": res.get("status") or "succeeded",
        "content": res.get("content", ""),
        "delivered": bool(res.get("delivered")),
        "delivered_message_id": res.get("delivered_message_id"),
        "ms": (_now() - started) * 1000,
        "tokens_in": res.get("tokens_in", 0),
        "tokens_out": res.get("tokens_out", 0),
    }


@router.get("/{delegation_id}/runs")
async def delegation_runs(
    delegation_id: str,
    limit: int = 20,
    cursor: str | None = None,
    uid: str = Depends(get_current_user),
) -> dict:
    """执行历史（分页）。trigger 区分定时触发与手动触发——排查「今天为什么没收到」先看它。"""
    await asyncio.to_thread(_must_own, uid, delegation_id)
    limit = max(1, min(int(limit or 20), 100))
    items = await asyncio.to_thread(daily.list_runs, uid, delegation_id, limit, cursor)
    return items
