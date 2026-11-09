"""提醒中心的 HTTP 端点。

  GET    /api/reminders?scope=today|week|all|paused
  POST   /api/reminders
  GET    /api/reminders/summary            今日概览（首页 / today 用）
  GET    /api/reminders/sources            七个来源开关 + 可选来源
  PUT    /api/reminders/sources            {source, enabled}
  GET    /api/reminders/prefs              静默时段 / 每日上限 / 活跃时段
  PUT    /api/reminders/prefs
  GET    /api/reminders/fires              ?reminder_id=&limit=
  GET    /api/reminders/{id}
  PATCH  /api/reminders/{id}               局部更新（含 enabled）
  DELETE /api/reminders/{id}
  POST   /api/reminders/{id}/snooze        {minutes}

## 三条硬约束

1. **路由顺序**：`summary` / `sources` / `prefs` / `fires` 必须写在 `/{reminder_id}`
   之前。FastAPI 按声明顺序匹配，`/{reminder_id}` 在前的话 `/summary` 会被当成 id，
   返回 404 —— 而且只有拼错的人才会发现。
2. **不带尾斜杠**：项目全局 `redirect_slashes=False`，带斜杠会 307，
   前端 fetch 跟着重定向时 cookie 在某些浏览器上会丢。
3. **跨用户一律 404**：归属校验写在 `reminder_core.get()` 的 SQL 里，
   猜中别人的 id 也只拿到 None（与 api/kb.py、api/delegate.py 同款口径）。

这里只做 HTTP 与参数整形，业务全在 `core/reminder_core.py` + `core/user_prefs.py`。
端点里写业务会让 worker 那条路径（`service/reminder_tick.py`）没法复用同一份逻辑。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import reminder_core, user_prefs
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

SCOPES = ("today", "week", "all", "paused")

# 来源开关的展示顺序就是设置页那一行的顺序（规格 2.2）：
# 四六级 打卡 番茄钟 课表 DDL 背单词 每日委托
SOURCE_LABELS = (
    ("cet", "四六级"),
    ("checkin", "打卡"),
    ("focus", "番茄钟"),
    ("timetable", "课表"),
    ("deadline", "DDL"),
    ("words", "背单词"),
    ("delegate", "每日委托"),
)


class ReminderIn(BaseModel):
    """新建。字段与表一一对应，不做花活（规格：不加附件/协作人/子任务/优先级）。"""

    title: str = Field(..., min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=500)
    source: str = Field(default="manual")
    source_ref: str | None = Field(default=None, max_length=64)
    override: dict | None = None
    freq: str = Field(default="daily")
    at: str | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    every_minutes: int | None = Field(default=None, ge=60, le=1440)
    trigger_kind: str = Field(default="at")
    anchor: str | None = None
    offset_minutes: int | None = None
    action: str = Field(default="notify")
    action_payload: dict | list | str | None = None
    # 列表也收：core 本来就支持数组，第二波前端更可能直接传 ["push","inapp"]。
    # 只声明 str 会让这种请求在 422 就死掉，看起来像「后端不支持多通道」。
    channels: str | list[str] = Field(default="push")
    enabled: bool = True


class ReminderPatch(BaseModel):
    """局部更新。全部可选，端点用 `exclude_unset` 只把真正传来的字段交下去。"""

    title: str | None = Field(default=None, min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=500)
    source: str | None = None
    source_ref: str | None = None
    override: dict | None = None
    freq: str | None = None
    at: str | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    every_minutes: int | None = Field(default=None, ge=60, le=1440)
    action: str | None = None
    action_payload: dict | list | str | None = None
    channels: str | list[str] | None = None
    enabled: bool | None = None


def _bad(exc: Exception) -> HTTPException:
    """业务校验失败 → 400。用 detail 直接回中文，前端原样展示。"""
    return HTTPException(status_code=400, detail=str(exc))


def _view(item: dict) -> dict:
    """单条响应也走 `resolve()`：override 的标题要在**所有**出口生效。

    只在列表里合并的话，会出现「列表显示我改的名字、点进去又是原来的名字」，
    而用户刚在抽屉里改过 —— 这类不一致比功能缺失更让人不敢用。
    """
    return reminder_core.resolve(item)


def _sources_view(prefs: dict[str, bool]) -> dict:
    """GET / PUT 共用一个形状：PUT 返回 dict、GET 返回 list 的话，
    前端「切完开关顺手刷新」就会拿到两种结构，第二波很容易写错。"""
    sources = [
        {"key": key, "label": label, "enabled": bool(prefs.get(key))}
        for key, label in SOURCE_LABELS
    ]
    return {"sources": sources, "selectable": sources}


@router.get("")
async def list_reminders(scope: str = "all", uid: str = Depends(get_current_user)) -> dict:
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope 只能是 {'/'.join(SCOPES)}")
    items = await asyncio.to_thread(reminder_core.list_reminders, uid, scope)
    return {"items": items, "scope": scope, "total": len(items)}


@router.post("")
async def create_reminder(body: ReminderIn, uid: str = Depends(get_current_user)) -> dict:
    try:
        item = await asyncio.to_thread(
            reminder_core.create, uid, body.model_dump(exclude_none=False)
        )
    except (reminder_core.ReminderInvalid, reminder_core.ReminderLimit) as exc:
        raise _bad(exc) from None
    return _view(item)


@router.get("/summary")
async def get_summary(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(reminder_core.summary, uid)


@router.get("/sources")
async def get_sources(uid: str = Depends(get_current_user)) -> dict:
    """七个来源的开关状态。

    `enabled` 用 `source_prefs` 的显式值；没有行 = 关（默认全关）。
    另外给 `selectable`，前端新建抽屉里就按这个顺序列，不再自己维护一份。
    """
    prefs = await asyncio.to_thread(user_prefs.source_prefs, uid)
    return _sources_view(prefs)


@router.put("/sources")
async def put_source(body: dict, uid: str = Depends(get_current_user)) -> dict:
    source = str(body.get("source") or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source 缺失")
    raw = body.get("enabled")
    if isinstance(raw, str):
        enabled = raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled = bool(raw)
    try:
        prefs = await asyncio.to_thread(user_prefs.set_source_pref, uid, source, enabled)
    except reminder_core.ReminderInvalid as exc:
        raise _bad(exc) from None
    return {**_sources_view(prefs), "source": source, "enabled": enabled}


@router.get("/prefs")
async def get_prefs(uid: str = Depends(get_current_user)) -> dict:
    prefs = await asyncio.to_thread(user_prefs.get_time_prefs, uid)
    prefs["cap_min"] = user_prefs.MIN_CAP
    prefs["cap_max"] = user_prefs.MAX_CAP
    return prefs


@router.put("/prefs")
async def put_prefs(body: dict, uid: str = Depends(get_current_user)) -> dict:
    try:
        prefs = await asyncio.to_thread(user_prefs.set_time_prefs, uid, body)
    except reminder_core.ReminderInvalid as exc:
        raise _bad(exc) from None
    # 与 GET 同一个形状：前端切完 tab 不该丢掉滑杆的上下界
    prefs["cap_min"] = user_prefs.MIN_CAP
    prefs["cap_max"] = user_prefs.MAX_CAP
    return prefs


@router.get("/fires")
async def get_fires(
    reminder_id: str | None = None,
    limit: int = 20,
    uid: str = Depends(get_current_user),
) -> dict:
    """触发流水。**排查「今天为什么没响」的第一现场**：
    skipped_quiet / skipped_cap / skipped_empty / failed 都在这里。"""
    items = await asyncio.to_thread(reminder_core.fires, uid, reminder_id, limit)
    return {"items": items, "reminder_id": reminder_id}


@router.get("/{reminder_id}")
async def get_reminder(reminder_id: str, uid: str = Depends(get_current_user)) -> dict:
    item = await asyncio.to_thread(reminder_core.get, uid, reminder_id)
    if item is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return _view(item)


@router.patch("/{reminder_id}")
async def patch_reminder(
    reminder_id: str, body: ReminderPatch, uid: str = Depends(get_current_user)
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="没有要改的字段")
    try:
        item = await asyncio.to_thread(reminder_core.update, uid, reminder_id, patch)
    except (reminder_core.ReminderInvalid, reminder_core.ReminderLimit) as exc:
        raise _bad(exc) from None
    if item is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return _view(item)


@router.delete("/{reminder_id}")
async def delete_reminder(reminder_id: str, uid: str = Depends(get_current_user)) -> dict:
    ok = await asyncio.to_thread(reminder_core.delete, uid, reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return {"ok": True, "id": reminder_id}


@router.post("/{reminder_id}/snooze")
async def snooze_reminder(
    reminder_id: str, body: dict, uid: str = Depends(get_current_user)
) -> dict:
    """推迟 N 分钟。只影响这一次；响过之后按原规则继续。"""
    try:
        minutes = int(body.get("minutes"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="minutes 必须是整数") from None
    try:
        item = await asyncio.to_thread(reminder_core.snooze, uid, reminder_id, minutes)
    except reminder_core.ReminderInvalid as exc:
        raise _bad(exc) from None
    if item is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return _view(item)
