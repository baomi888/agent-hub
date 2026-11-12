"""上课提醒 —— 把课表变成**真正的定时提醒**，不是日历文件。

## 为什么不再只靠日历

`courses.ics`（`service/calendar.py`）里有 VALARM，订阅后手机日历会响，那是安卓
目前唯一可靠的路：安卓的网页推送底层走 Google FCM，国内连不通。

但 iOS 不一样。iPhone 装到主屏之后走的是苹果 APNs，**能收到网页推送**，
而且用户要的是「一条能点开的通知」，不是「日历里多了一件事」。
所以对 iOS 这条路必须做成真正的提醒，走 `reminder_tick` 现有的派发链。

## 为什么是「滚动重建」而不是 weekly

课表的周期不是 `weekly` 能表达的：

* `weeks = null` 是每周都上 —— 这个 `freq='weekly'` 能覆盖；
* `weeks = [2, 4, 6]` 是隔周上、或者跳着上 —— `RRULE` 里除了 `INTERVAL=2` 没有
  别的表达方式，而「第 2、4、6 周」连隔周都不算。

用 `weekly` 硬凑，结果是**没课的那一周照样提前 20 分钟叫你起床**，
而且从提醒列表上看不出哪里错了。所以每一门课只维护**下一次**那条
（`freq='once'` + `fire_at`），响过之后由 tick 的每分钟同步重新算下一次。

`once` 触发后 `advance()` 会自动 `enabled=0`（见 `reminder_core.advance`），
所以「重建」这件事必须由外部驱动 —— 就是 `due_subjects()` 里那次
`sync_all()`。链条断一次就再也不响，这比算错时间更难发现。

## 和课表快照的关系

数据源是云端快照（`campus_store.load_snapshot`），不读前端。用户在课程表里
改了课，最多晚一分钟就反映到提醒上 —— 因为每分钟都重算一次。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Iterable

from core import session, timeutil

logger = logging.getLogger(__name__)

# 提前量的可选档位。0 是「正点」；45/60 留给要提前占座的人。
# 默认 20 分钟：这是用户 2026-11-12 明确要的，也是「走到教室刚好」的经验值。
LEADS = (0, 5, 10, 15, 20, 30, 45, 60)
DEFAULT_LEAD = 20

# 算「下一次」时往前看多少天。18 周的课表在第 1 周时，下一次也可能在 6 天后，
# 21 天足够覆盖「两周一次」的极端课表；再长就没意义了 —— 那门课这学期已经结束了。
_LOOKAHEAD_DAYS = 21

_SCHEMA = """
CREATE TABLE IF NOT EXISTS course_reminder_prefs (
    owner_id     TEXT PRIMARY KEY,
    enabled      INTEGER NOT NULL DEFAULT 0,
    lead_minutes INTEGER NOT NULL DEFAULT 20,
    updated_at   INTEGER NOT NULL
);
"""


def ensure() -> None:
    with session._get_conn() as conn:
        conn.executescript(_SCHEMA)


def _conn():
    return session._get_conn()


def get_prefs(owner_id: str) -> dict[str, Any]:
    """没有行 = 没开过。不要给新用户预插行，否则「关掉」和「没开过」分不清。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT enabled, lead_minutes FROM course_reminder_prefs WHERE owner_id=?",
            (str(owner_id),),
        ).fetchone()
    if row is None:
        return {"enabled": False, "lead_minutes": DEFAULT_LEAD}
    return {"enabled": bool(row[0]), "lead_minutes": int(row[1])}


def set_prefs(owner_id: str, *, enabled: bool | None = None, lead: int | None = None) -> dict[str, Any]:
    """改设置。`lead` 会夹到 `LEADS` 里最近的一档 —— 允许任意整数的话，
    日历那份 ICS 和这份提醒的提前量会对不上（一个 17 分钟、一个 20 分钟）。"""
    ensure()
    cur = get_prefs(owner_id)
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if lead is not None:
        cur["lead_minutes"] = _nearest_lead(lead)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO course_reminder_prefs (owner_id, enabled, lead_minutes, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(owner_id) DO UPDATE SET
                 enabled=excluded.enabled,
                 lead_minutes=excluded.lead_minutes,
                 updated_at=excluded.updated_at""",
            (str(owner_id), 1 if cur["enabled"] else 0, int(cur["lead_minutes"]), timeutil.now_ts()),
        )
        if not cur["enabled"]:
            # ★ 关掉必须**当场**清掉已建的行，不能等下一轮 sync。
            #
            # `sync_all()` 只遍历 `owners()`（enabled=1 的人），所以「刚关掉的人」
            # 不在里面 —— 那些行会一直 `enabled=1` 地躺在库里，到点照响。
            # 用户看到的是「我关了，但它还在提醒我」，而且再点开会发现行还在，
            # 完全无法解释。测试 `test_disabled_owner_is_not_synced_back` 抓的是这个。
            conn.execute(
                "UPDATE reminders SET enabled=0, next_fire_at=NULL, updated_at=? "
                "WHERE owner_id=? AND source='timetable' AND enabled=1",
                (timeutil.now_ts(), str(owner_id)),
            )
    return cur


def _nearest_lead(value: object) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LEAD
    return min(LEADS, key=lambda x: (abs(x - n), x))


# ---------- 从课表快照算下一次 ----------


def _parse(snapshot: dict[str, Any]) -> tuple[list[dict], list[str], int, str, int]:
    """快照 → (课程, 每节开始时刻, 单节分钟数, 第 1 周周一, 学期周数)。

    和 `calendar.build_courses_ics` 读同一份结构。这里**不重算作息**：
    `periods.starts` 已经是前端按学校作息算好的 `HH:MM`，服务端只取下标。
    """
    raw: object = snapshot.get("courses")
    course_list: list[dict] = []
    per: dict = {}
    term: dict = {}

    if isinstance(raw, dict):
        items = raw.get("courses")
        course_list = [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []
        if isinstance(raw.get("periods"), dict):
            per = raw["periods"]
        if isinstance(raw.get("term"), dict):
            term = raw["term"]
    elif isinstance(raw, list):
        course_list = [c for c in raw if isinstance(c, dict)]

    if isinstance(snapshot.get("term"), dict) and snapshot["term"]:
        term = snapshot["term"]
    if isinstance(snapshot.get("periods"), dict) and snapshot["periods"]:
        per = snapshot["periods"]

    starts = [s for s in per.get("starts", []) if isinstance(s, str)] if isinstance(per.get("starts"), list) else []
    try:
        lesson = int(per.get("duration") or 45)
    except (TypeError, ValueError):
        lesson = 45
    if lesson <= 0:
        lesson = 45

    term_start = str(term.get("start") or "")
    try:
        weeks = int(term.get("weeks") or 0)
    except (TypeError, ValueError):
        weeks = 0
    if weeks <= 0:
        weeks = 20

    return course_list, starts, lesson, term_start, weeks


def _week_dates(term_start: str, day: int, weeks: Iterable[int]) -> list[str]:
    """第 1 周周一 + 周次 → 这门课每个上课日的 `YYYYMMDD`（升序）。

    和 `calendar._week_dates` 同一套算法，刻意复制而不是 import：
    那边是私有函数、且允许越界周次被静默跳过；这里需要**按日期排序**
    才能取「下一次」，语义已经不同，共用会逼着两边一起改。
    """
    try:
        first = datetime.strptime(term_start, "%Y-%m-%d")
    except (TypeError, ValueError):
        return []
    out: list[str] = []
    for w in weeks:
        if isinstance(w, float) and not w.is_integer():
            continue
        try:
            n = int(w)
        except (TypeError, ValueError):
            continue
        if n < 1 or n > 60:
            continue
        out.append((first + timedelta(days=(n - 1) * 7 + day)).strftime("%Y%m%d"))
    return sorted(out)


def _hhmm(raw: object) -> tuple[int, int] | None:
    try:
        hh, mm = (int(x) for x in str(raw).split(":"))
    except (TypeError, ValueError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def next_occurrences(
    snapshot: dict[str, Any], lead: int, now: int | None = None
) -> list[dict[str, Any]]:
    """每门课**下一次**上课（还没提醒过的那次）。

    返回的 `fire_at` 是提醒时刻（上课时间减提前量）。已经过去的跳过 ——
    「8:00 的课在 7:58 打开页面」不该立刻补一条「20 分钟后上课」。
    """
    ts = timeutil.now_ts() if now is None else int(now)
    course_list, starts, _lesson, term_start, term_weeks = _parse(snapshot)
    if not term_start or not course_list:
        return []

    lead = _nearest_lead(lead)
    lead_sec = lead * 60
    horizon = ts + _LOOKAHEAD_DAYS * 86400
    out: list[dict[str, Any]] = []

    for course in course_list:
        sections = course.get("sections")
        if not isinstance(sections, list) or not sections:
            continue
        try:
            first = min(int(s) for s in sections)
            day = int(course.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if not (0 <= day <= 6):
            continue

        index = first - 1
        clock = _hhmm(starts[index]) if 0 <= index < len(starts) else None
        if clock is None:
            clock = (8, 0)
        hh, mm = clock

        raw_weeks = course.get("weeks")
        weeks: Iterable[int]
        if isinstance(raw_weeks, list) and raw_weeks:
            weeks = [w for w in raw_weeks if isinstance(w, (int, float))]
        else:
            weeks = range(1, term_weeks + 1)

        for ymd in _week_dates(term_start, day, weeks):
            try:
                start = datetime.strptime(ymd, "%Y%m%d").replace(
                    hour=hh, minute=mm, second=0, microsecond=0, tzinfo=timeutil.TZ
                )
            except (TypeError, ValueError):
                continue
            start_ts = int(start.timestamp())
            fire_ts = start_ts - lead_sec
            if fire_ts <= ts:
                continue
            if fire_ts > horizon:
                break  # 后面的只会更远
            out.append(
                {
                    "course_id": str(course.get("id") or f"{day}-{first}"),
                    "title": str(course.get("name") or "上课").strip() or "上课",
                    "classroom": str(course.get("classroom") or "").strip(),
                    "teacher": str(course.get("teacher") or "").strip(),
                    "start_at": start_ts,
                    "fire_at": fire_ts,
                    "lead": lead,
                }
            )
            break  # 每门课只要下一次

    out.sort(key=lambda x: x["fire_at"])
    return out


# ---------- 落库：reminders 表里的 timetable 行 ----------


def _reminder_id(owner_id: str, course_id: str) -> str:
    """稳定 id：同一门课在库里永远是同一行。

    随机 id 每次同步都新建一行，`reminder_fires` 的 (reminder_id, planned_at)
    主键就挡不住重复派发 —— 同一节课会响两次。
    """
    raw = f"{owner_id}|{course_id}".encode("utf-8")
    return "rm_c_" + hashlib.sha1(raw).hexdigest()[:12]


def _title(item: dict[str, Any]) -> str:
    return f"{item['title']} · {item['classroom']}" if item["classroom"] else item["title"]


def _note(item: dict[str, Any]) -> str:
    head = f"{item['lead']} 分钟后上课" if item["lead"] else "现在上课"
    parts = [head]
    if item["classroom"]:
        parts.append(item["classroom"])
    if item["teacher"]:
        parts.append(f"老师：{item['teacher']}")
    return "；".join(parts)


def sync(owner_id: str, now: int | None = None) -> dict[str, Any]:
    """让库里的 timetable 行跟课表一致。**幂等**，每分钟调一次。

    三件事：
      1. 没开 = 把这个人所有 timetable 行停用（不是删 —— 关了再开要能接上流水）；
      2. 开着的课：更新成「下一次」，值没变就不写库（每分钟 20 门课全量 UPDATE
         会把这个小库写热，而且没有任何收益）；
      3. 课表里已经没有的课（被删掉/换了）：停用。

    返回统计，给测试和 `/api/course-reminder` 用。
    """
    ts = timeutil.now_ts() if now is None else int(now)
    prefs = get_prefs(owner_id)
    course_reminders = 0

    if not prefs["enabled"]:
        with _conn() as conn:
            cur = conn.execute(
                "UPDATE reminders SET enabled=0, next_fire_at=NULL, updated_at=? "
                "WHERE owner_id=? AND source='timetable' AND enabled=1",
                (ts, str(owner_id)),
            )
            course_reminders = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return {"enabled": False, "written": 0, "disabled": course_reminders, "next": []}

    from core import campus_store  # 循环导入：campus_store 依赖 session，这里放函数内

    snapshot = campus_store.load_snapshot(owner_id)
    items = next_occurrences(snapshot, prefs["lead_minutes"], ts)

    with _conn() as conn:
        existing: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            "SELECT id, source_ref, fire_at, next_fire_at, title, enabled "
            "FROM reminders WHERE owner_id=? AND source='timetable'",
            (str(owner_id),),
        ).fetchall():
            existing[str(row["id"])] = {
                "source_ref": row["source_ref"],
                "fire_at": row["fire_at"],
                "next_fire_at": row["next_fire_at"],
                "title": row["title"],
                "enabled": row["enabled"],
            }

        keep: set[str] = set()
        written = 0
        for item in items:
            rid = _reminder_id(str(owner_id), item["course_id"])
            keep.add(rid)
            title = _title(item)
            old = existing.get(rid)
            # 值没变 → 不写。这条判断是「每分钟全量同步」能不能承受的关键。
            if (
                old is not None
                and int(old["fire_at"] or 0) == int(item["fire_at"])
                and int(old["next_fire_at"] or 0) == int(item["fire_at"])
                and int(old["enabled"] or 0) == 1
                and str(old["title"]) == title
            ):
                continue
            conn.execute(
                """INSERT INTO reminders
                       (id, owner_id, title, note, source, source_ref, freq,
                        at, fire_at, next_fire_at, trigger_kind, action, channels,
                        enabled, created_at, updated_at)
                   VALUES (?,?,?,?,'timetable',?,'once',?,?,?,'at','notify','push',1,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title,
                     note=excluded.note,
                     source_ref=excluded.source_ref,
                     at=excluded.at,
                     fire_at=excluded.fire_at,
                     next_fire_at=excluded.next_fire_at,
                     enabled=1,
                     updated_at=excluded.updated_at""",
                (
                    rid,
                    str(owner_id),
                    title,
                    _note(item),
                    item["course_id"],
                    _at_label(item["start_at"], item["lead"]),
                    int(item["fire_at"]),
                    int(item["fire_at"]),
                    ts,
                    ts,
                ),
            )
            written += 1

        stale = [rid for rid in existing if rid not in keep]
        for rid in stale:
            conn.execute(
                "UPDATE reminders SET enabled=0, next_fire_at=NULL, updated_at=? WHERE id=?",
                (ts, rid),
            )

    return {
        "enabled": True,
        "written": written,
        "disabled": len(stale),
        "next": items[:5],
    }


def disable_all(owner_id: str) -> int:
    """用户取消勾选时调用（`set_prefs(enabled=False)` 之后走一次 sync 也行，
    但那条路要求「先保证 prefs 已落库」，这个函数让调用方少一个隐含顺序）。"""
    return int(sync(owner_id).get("disabled") or 0)


def owners() -> list[str]:
    """开了上课提醒的人。`due_subjects` 每分钟拿它做一次全量同步 ——
    只同步「这一分钟有到期提醒的人」是不够的：课表里没有谁的提醒正好每分钟到期，
    而 `once` 触发后会被 `advance()` 停用，不重建就永久沉默。"""
    ensure()
    with _conn() as conn:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT owner_id FROM course_reminder_prefs WHERE enabled=1"
            ).fetchall()
        ]


def sync_all(now: int | None = None) -> int:
    """给 tick 用：把每个开着的账号都同步一次。返回写入行数。"""
    written = 0
    for owner_id in owners():
        try:
            written += int(sync(owner_id, now).get("written") or 0)
        except Exception:  # noqa: BLE001 —— 一个人的课表坏了不该拖垮整轮 tick
            logger.exception("course_reminder.sync failed owner=%s", owner_id)
    return written


def _at_label(start_at: int, lead: int) -> str:
    """`at` 列填的是**上课时刻**（HH:MM），提醒时刻由 `fire_at` 决定。

    为什么不全填 fire_at 的时刻：列表里给用户看「08:00 高等数学」比
    「07:40 高等数学」更像课表；而 `next_fire` 对 `once` 只读 `fire_at`，
    `at` 不参与调度，填真实上课时间不会影响什么时候响。
    """
    return datetime.fromtimestamp(int(start_at), timeutil.TZ).strftime("%H:%M")


def upcoming(owner_id: str, limit: int = 5, now: int | None = None) -> list[dict[str, Any]]:
    """接下来几次课（含提醒时刻），给接口和界面预览用。"""
    from core import campus_store

    prefs = get_prefs(owner_id)
    snapshot = campus_store.load_snapshot(owner_id)
    return [
        {
            "title": _title(it),
            "start_at": it["start_at"],
            "fire_at": it["fire_at"],
            "lead": it["lead"],
        }
        for it in next_occurrences(snapshot, prefs["lead_minutes"], now)[:limit]
    ]
