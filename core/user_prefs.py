"""用户时间偏好 + 来源开关（`user_time_prefs` / `reminder_source_prefs`）。

## 为什么单独一个文件

这两张表管的是「什么时候别打扰我」和「允许哪些来源打扰我」，
而 `reminder_core` 管的是「这条提醒本身是什么」。混在一起读的人会以为
「静默时段」是某条提醒的属性 —— 它是账号级的，一条提醒改不了它。

建表仍在 `reminder_core._SCHEMA` 里（规格要求四张表一次定死、不许中途加列），
这里只调它的 `ensure()`。方向是单向的：`user_prefs → reminder_core`，
反过来没有依赖，不会成环。

## 静默时段为什么必须支持跨零点

最常见的配置就是 23:00–07:00。写 `start <= now < end` 那种判断在这个配置下
永远为假 —— 表现是「设了静默，夜里照样被吵醒」，而且测试如果只用
09:00–18:00 这种不跨零点的样本，会一路绿灯。所以跨零点要当**主路径**测，
不是边界情况。

## 每日上限的计数口径

数的是「今天真正送达了几条」，不是「配了几条」。所以：

* 只数 `status='sent'`；
* 同刻合并且被并入别人的那几条**不重复计数**（detail 以 `merged_into:` 开头），
  否则一个合并通知会被算成三条，上限会提前用光，用户会觉得「明明没收到几条就不响了」。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core import reminder_core, timeutil

DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "07:00"
DEFAULT_DAILY_CAP = 6

# 上限的硬边界：0 表示「今天彻底不发」，30 是给「我就想多收几条」的人留的天花板。
MIN_CAP = 0
MAX_CAP = 30


def ensure() -> None:
    reminder_core.ensure()


# ---------- 时间偏好 ----------


def default_prefs() -> dict[str, Any]:
    return {
        "quiet_start": DEFAULT_QUIET_START,
        "quiet_end": DEFAULT_QUIET_END,
        "active_windows": None,
        "daily_cap": DEFAULT_DAILY_CAP,
    }


def get_time_prefs(owner_id: str) -> dict[str, Any]:
    """没设置过的人拿到默认值，**不写库**（同 reminder_store.get 的思路）。"""
    if not owner_id:
        return default_prefs()
    ensure()
    with reminder_core._conn() as conn:
        row = conn.execute(
            "SELECT quiet_start, quiet_end, active_windows, daily_cap "
            "FROM user_time_prefs WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
    if row is None:
        return default_prefs()
    prefs = default_prefs()
    prefs.update(
        {
            "quiet_start": str(row[0] or DEFAULT_QUIET_START),
            "quiet_end": str(row[1] or DEFAULT_QUIET_END),
            "active_windows": reminder_core._loads(row[2]),
            "daily_cap": int(row[3]),
        }
    )
    return prefs


def _norm_hhmm(value: Any, field: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise reminder_core.ReminderInvalid(f"{field} 要写成 HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise reminder_core.ReminderInvalid(f"{field} 超出范围")
    return f"{hour:02d}:{minute:02d}"


def set_time_prefs(owner_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """局部更新。空字符串表示「不设静默时段」（quiet_start == quiet_end 即视为关）。"""
    current = get_time_prefs(owner_id)
    quiet_start = _norm_hhmm(patch.get("quiet_start", current["quiet_start"]), "静默开始")
    quiet_end = _norm_hhmm(patch.get("quiet_end", current["quiet_end"]), "静默结束")

    cap = patch.get("daily_cap", current["daily_cap"])
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        # 端点把 ReminderInvalid 统一转 400；不接住的话 "abc" 会变成 500
        raise reminder_core.ReminderInvalid("每日上限必须是整数") from None
    if not MIN_CAP <= cap <= MAX_CAP:
        raise reminder_core.ReminderInvalid(f"每日上限要在 {MIN_CAP}–{MAX_CAP} 条之间")

    windows = patch.get("active_windows", current["active_windows"])
    if windows is not None:
        if not isinstance(windows, list):
            raise reminder_core.ReminderInvalid("active_windows 必须是数组")
        cleaned: list[dict[str, Any]] = []
        for item in windows:
            if not isinstance(item, dict):
                raise reminder_core.ReminderInvalid("active_windows 的每一项要是对象")
            days = item.get("weekday") or []
            if not isinstance(days, (list, tuple)):
                raise reminder_core.ReminderInvalid("active_windows.weekday 要是数组")
            try:
                weekdays = sorted({int(d) for d in days if 0 <= int(d) <= 6})
            except (TypeError, ValueError):
                raise reminder_core.ReminderInvalid(
                    "active_windows.weekday 只能是 0–6 的整数"
                ) from None
            cleaned.append(
                {
                    "weekday": weekdays,
                    "start": _norm_hhmm(item.get("start"), "活跃时段开始"),
                    "end": _norm_hhmm(item.get("end"), "活跃时段结束"),
                }
            )
        windows = cleaned or None

    ensure()
    now = timeutil.now_ts()
    with reminder_core._conn() as conn:
        conn.execute(
            """INSERT INTO user_time_prefs
                   (owner_id, quiet_start, quiet_end, active_windows, daily_cap, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(owner_id) DO UPDATE SET
                   quiet_start=excluded.quiet_start,
                   quiet_end=excluded.quiet_end,
                   active_windows=excluded.active_windows,
                   daily_cap=excluded.daily_cap,
                   updated_at=excluded.updated_at""",
            (
                owner_id,
                quiet_start,
                quiet_end,
                json.dumps(windows, ensure_ascii=False) if windows else None,
                cap,
                now,
            ),
        )
    return get_time_prefs(owner_id)


def _minutes(hhmm: str) -> int:
    hour, minute = (int(part) for part in str(hhmm).split(":"))
    return hour * 60 + minute


def in_quiet_hours(now_local: datetime, prefs: dict[str, Any]) -> bool:
    """现在是不是静默时段。**跨零点是主路径**（23:00–07:00 就是最常见配置）。

    区间取左闭右开：23:00 整算静默，07:00 整已经算醒了（用户设 07:00 就是想起床）。
    起止相同 = 没设静默。只把结束判成「恰好等于」会让 07:00 那条永远不响。
    """
    start = str(prefs.get("quiet_start") or "")
    end = str(prefs.get("quiet_end") or "")
    if not start or not end or start == end:
        return False
    start_m = _minutes(start)
    end_m = _minutes(end)
    now_m = now_local.hour * 60 + now_local.minute
    if start_m < end_m:
        return start_m <= now_m < end_m
    # 跨零点：23:00–07:00 → 今天 >= 23:00 或 今天 < 07:00
    return now_m >= start_m or now_m < end_m


# ---------- 来源开关 ----------


def source_prefs(owner_id: str) -> dict[str, bool]:
    """这个人的来源开关。**没有行 = 关**，所以这里只返回显式写过的那些。"""
    ensure()
    with reminder_core._conn() as conn:
        rows = conn.execute(
            "SELECT source, enabled FROM reminder_source_prefs WHERE owner_id=?", (owner_id,)
        ).fetchall()
    return {str(r[0]): bool(r[1]) for r in rows}


def source_enabled(owner_id: str, source: str) -> bool:
    return bool(source_prefs(owner_id).get(source))


def set_source_pref(owner_id: str, source: str, enabled: bool) -> dict[str, bool]:
    """开 / 关一个来源。

    关掉**不删行**：`enabled=0` 才留得下「他什么时候关的」，
    排查「为什么这个来源不响了」时第一时间要看这个。
    """
    source = str(source or "").strip()
    if source not in reminder_core.SOURCES:
        raise reminder_core.ReminderInvalid(f"未知来源：{source}")
    if source == "system":
        # system 是老「每日回顾」的内部来源，不该出现在来源开关里：
        # 用户看得见的开关只有七个（四六级 / 打卡 / 番茄钟 / 课表 / DDL / 单词 / 委托）。
        raise reminder_core.ReminderInvalid("system 不是可配置的来源")
    ensure()
    now = timeutil.now_ts()
    with reminder_core._conn() as conn:
        conn.execute(
            "INSERT INTO reminder_source_prefs (owner_id, source, enabled, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(owner_id, source) DO UPDATE SET "
            "enabled=excluded.enabled, updated_at=excluded.updated_at",
            (owner_id, source, 1 if enabled else 0, now),
        )
    return source_prefs(owner_id)


# ---------- 每日上限计数 ----------


def day_count(owner_id: str, day: str | None = None) -> int:
    """今天已经送达几条（合并通知算一条）。"""
    day = day or timeutil.day_str()
    start, end = timeutil.day_bounds(day)
    ensure()
    with reminder_core._conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM reminder_fires f
                 JOIN reminders r ON r.id = f.reminder_id
                WHERE r.owner_id = ?
                  AND f.status = 'sent'
                  AND f.planned_at >= ? AND f.planned_at < ?
                  AND COALESCE(f.detail, '') NOT LIKE 'merged_into:%'""",
            (owner_id, start, end),
        ).fetchone()
    return int(row[0])
