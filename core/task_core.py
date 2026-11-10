"""待办内核：`tasks` 表 + 与提醒的联动。

## 为什么不是给 `reminders` 加几列

提醒是**投递规则**（每 N 天、几点、走哪个通道），待办是**用户意图**
（这件事做完了没有）。生命周期完全不同：提醒可以永远重复、没有终点；
待办有完成态、有一天会被勾掉。合成一张表的直接后果是
「已完成」这个状态得挂在一条还要继续响的规则上，语义立刻说不清。

所以分开存，用 `reminders.source='task' + source_ref=task.id` 单向关联。
一个待办最多一条联动提醒，靠唯一索引兜底（见 `reminder_core._UNIQUE_TASK_SQL`）。

## `due_at` 和 `remind_at` 为什么必须分开

* `due_at` = 最晚什么时候交（「周五 23:59」）
* `remind_at` = 什么时候提醒我（「周五 20:00」）

这是 Things 3 的 Deadline / When。混成一个字段，用户一定会问
「我明明设了提醒，为什么显示今天是截止」。
**只设 `due_at` 不会响** —— 不变量 #1：没有 `remind_at` 就不建联动提醒。
界面上要把这句话写出来，而不是让用户设完等一夜。

## 时间口径

全线 Unix 秒（int），与 sessions / messages / usage 一致。
「今天」的边界一律走 `core.timeutil.day_bounds()`，不在 SQL 里用 `date('now')`
—— 那是 UTC，服务器换时区时会整体偏 8 小时，而且不报错。
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from core import reminder_core, timeutil
from core import session as store

# 已建表的库路径集合（理由同 reminder_core：测试会在一个进程里反复切库）
_READY: set[str] = set()

# 一人最多 300 条待办。待办比提醒容易堆积（一天记五条很正常），
# 所以额度给得比提醒宽；但也不能没有——列表接口要能一次拉完。
MAX_TASKS_PER_OWNER = 300

# 清单键。**英文键落库**，中文只在前端映射：
# 把「作业」写进 list_key，以后想改叫「课程作业」就得迁一次数据。
LIST_KEYS = ("inbox", "course", "homework", "club", "life", "exam")

SCOPES = ("today", "upcoming", "all", "completed")

# 排序用的「无穷大」：SQLite 的 ORDER BY 里 NULL 排最前，
# 而「没有截止日期」应当排最后，所以先把 NULL 换成一个大到不会撞车的整数。
_FAR = 4102444800  # 2100-01-01，够用到这个产品不再存在

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    title         TEXT NOT NULL,
    note          TEXT,
    list_key      TEXT NOT NULL DEFAULT 'inbox',
    priority      INTEGER NOT NULL DEFAULT 0 CHECK (priority IN (0, 1, 2)),
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    due_at        INTEGER,
    remind_at     INTEGER,
    completed_at  INTEGER,
    recurrence    TEXT NOT NULL DEFAULT 'none',
    parent_id     TEXT,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_status_due ON tasks(owner_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_remind ON tasks(owner_id, remind_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_created ON tasks(owner_id, created_at DESC);
"""


class TaskInvalid(ValueError):
    """输入不合法。端点据此回 400（而不是 500）。"""


class TaskLimit(ValueError):
    """条数超上限。单独立类是为了给「先完成或删除一些」这句提示。"""


# ---------- 建表 ----------


def _conn():
    return store._get_conn()  # noqa: SLF001


def ensure() -> None:
    """幂等建表。

    ★ 先把 `reminder_core.ensure()` 走完：本模块在同一个事务里写 linked reminder，
    不能假设「提醒页先加载过」。顺序放在 `with _conn()` **之前**，
    是因为两个 `with` 嵌套在同一条线程连接上会让内层提前 commit，
    把「待办写了一半」的事务劈开。
    """
    path = str(store._db_path())  # noqa: SLF001
    if path in _READY:
        return
    reminder_core.ensure()
    with _conn() as conn:
        conn.executescript(_SCHEMA)
    _READY.add(path)


def _new_id() -> str:
    return "tk_" + uuid.uuid4().hex[:12]


# ---------- 校验 ----------


def _title(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskInvalid("待办要写一句话")
    if len(text) > 120:
        raise TaskInvalid("标题最长 120 个字")
    return text


def _note(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > 1000:
        raise TaskInvalid("备注最长 1000 个字")
    return text or None


def _list_key(value: Any) -> str:
    key = str(value or "inbox")
    if key not in LIST_KEYS:
        raise TaskInvalid("清单只能是 " + " / ".join(LIST_KEYS))
    return key


def _priority(value: Any) -> int:
    try:
        p = int(value or 0)
    except (TypeError, ValueError):
        raise TaskInvalid("优先级只能是 0 / 1 / 2") from None
    if p not in (0, 1, 2):
        raise TaskInvalid("优先级只能是 0 / 1 / 2")
    return p


def _ts(value: Any, field: str) -> int | None:
    """Unix 秒。空值 → None（PATCH 里显式 null 就是「清空」）。"""
    if value is None or value == "":
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        raise TaskInvalid(f"{field} 必须是 Unix 秒") from None
    if ts <= 0:
        raise TaskInvalid(f"{field} 必须是正数")
    return ts


def _recurrence(value: Any) -> str:
    # Phase 1 只允许 none：重复待办要么靠 linked reminder 的 freq，
    # 要么等 Phase 2 把 recurrence 做成「完成后自动生成下一条」。
    raw = str(value or "none")
    if raw != "none":
        raise TaskInvalid("重复待办将在下一阶段开放")
    return "none"


def _check_remind(remind_at: int | None, now: int) -> None:
    if remind_at is not None and remind_at <= now:
        raise TaskInvalid("提醒时间要晚于现在")


# ---------- 行 → 视图 ----------


def _reminder_states(owner_id: str, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """一次查出这个人的全部联动提醒 + 各自最近一条送达流水。

    **不为每条待办单独查一次**（N+1）：一屏 20 条就是 40 次查询，
    在 SQLite 上是 40 次游标往返，列表会肉眼可见地慢。
    """
    rows = conn.execute(
        """SELECT r.source_ref AS task_id, r.id, r.enabled, r.next_fire_at,
                  f.status AS last_status, f.fired_at AS last_fired_at,
                  f.detail AS last_detail
             FROM reminders r
             LEFT JOIN reminder_fires f
               ON f.reminder_id = r.id
              AND f.planned_at = (
                    SELECT MAX(planned_at) FROM reminder_fires WHERE reminder_id = r.id
                  )
            WHERE r.owner_id = ? AND r.source = 'task'""",
        (owner_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = row["task_id"]
        if not task_id:
            continue
        out[str(task_id)] = {
            "id": str(row["id"]),
            "enabled": bool(row["enabled"]),
            "next_fire_at": None if row["next_fire_at"] is None else int(row["next_fire_at"]),
            "last_status": row["last_status"],
            "last_fired_at": None if row["last_fired_at"] is None else int(row["last_fired_at"]),
            "last_detail": row["last_detail"],
        }
    return out


def _view(row: sqlite3.Row | dict[str, Any], states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    d = dict(row)
    d["reminder"] = states.get(str(d["id"]))
    return d


# ---------- 读 ----------


def get(owner_id: str, task_id: str) -> dict[str, Any] | None:
    """按 id 取。**归属校验写在 SQL 里**：猜中别人的 id 也只能拿到 None → 404。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id=? AND owner_id=?", (task_id, owner_id)
        ).fetchone()
        if row is None:
            return None
        return _view(row, _reminder_states(owner_id, conn))


def _today_clause(start: int, end: int) -> tuple[str, list[Any]]:
    """`today` 的判据。

    `due_at` 用 `< end`（含逾期）；`remind_at` 用 `[start, end)`。
    不对称是故意的：一个上个月的截止日期到现在仍然「今天得做」，
    而一个上个月的提醒时刻不该让这条待办永远挂在今天。
    """
    return (
        "((due_at IS NOT NULL AND due_at < ?)"
        " OR (remind_at IS NOT NULL AND remind_at >= ? AND remind_at < ?))",
        [end, start, end],
    )


def list_tasks(
    owner_id: str,
    scope: str = "today",
    limit: int = 100,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """按 scope 取列表：today / upcoming / all / completed。"""
    ensure()
    if scope not in SCOPES:
        raise TaskInvalid("scope 只能是 " + " / ".join(SCOPES))
    ts = timeutil.now_ts() if now is None else int(now)
    start, end = timeutil.day_bounds(timeutil.day_str(ts))
    today_sql, today_args = _today_clause(start, end)
    limit = max(1, min(int(limit or 100), 200))

    if scope == "today":
        where = f"owner_id=? AND status='open' AND {today_sql}"
        args: list[Any] = [owner_id, *today_args]
        order = (
            # 逾期优先（due_at 在昨天以前），再按截止时间，再按提醒时间
            f"ORDER BY CASE WHEN due_at IS NOT NULL AND due_at < {start} THEN 0 ELSE 1 END, "
            f"COALESCE(due_at, {_FAR}) ASC, COALESCE(remind_at, {_FAR}) ASC, "
            "priority DESC, created_at DESC"
        )
    elif scope == "upcoming":
        # 「不在今天」并且有未来的 due 或 remind
        where = (
            "owner_id=? AND status='open' "
            f"AND NOT {today_sql} "
            f"AND (COALESCE(due_at, 0) >= ? OR COALESCE(remind_at, 0) >= ?)"
        )
        args = [owner_id, *today_args, end, end]
        order = (
            f"ORDER BY MIN(COALESCE(due_at, {_FAR}), COALESCE(remind_at, {_FAR})) ASC, "
            "priority DESC, created_at DESC"
        )
    elif scope == "completed":
        where = "owner_id=? AND status='done'"
        args = [owner_id]
        order = "ORDER BY COALESCE(completed_at, 0) DESC, updated_at DESC"
    else:  # all
        where = "owner_id=? AND status='open'"
        args = [owner_id]
        order = (
            # 优先级高的在前；同优先级按截止时间，没有截止的垫底
            "ORDER BY priority DESC, (due_at IS NULL), due_at ASC, created_at DESC"
        )

    sql = f"SELECT * FROM tasks WHERE {where} {order} LIMIT ?"
    args.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, args).fetchall()
        states = _reminder_states(owner_id, conn)
        return [_view(r, states) for r in rows]


def summary(owner_id: str, now: int | None = None) -> dict[str, Any]:
    """四个 tab 的计数 + 「下一件」。

    `next` 是**今天 / 逾期里最早的一条待办**。事件提醒（课表、番茄钟…）
    的「下一件」由 `/api/reminders/summary` 提供，前端合并展示 ——
    在这里伪造事件会让两个接口各说一套。
    """
    ensure()
    ts = timeutil.now_ts() if now is None else int(now)
    start, end = timeutil.day_bounds(timeutil.day_str(ts))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE owner_id=? AND status='open'", (owner_id,)
        ).fetchall()
        states = _reminder_states(owner_id, conn)
        done = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE owner_id=? AND status='done'", (owner_id,)
        ).fetchone()

    items = [_view(r, states) for r in rows]
    by_list = {key: 0 for key in LIST_KEYS}
    today_items: list[dict[str, Any]] = []
    overdue = 0
    upcoming = 0
    for item in items:
        by_list[item["list_key"]] = by_list.get(item["list_key"], 0) + 1
        due = item["due_at"]
        remind = item["remind_at"]
        if due is not None and due < start:
            overdue += 1
            today_items.append(item)
            continue
        if (due is not None and start <= due < end) or (
            remind is not None and start <= remind < end
        ):
            today_items.append(item)
            continue
        if (due is not None and due >= end) or (remind is not None and remind >= end):
            upcoming += 1

    def when(item: dict[str, Any]) -> int:
        return min(
            (v for v in (item["due_at"], item["remind_at"]) if v is not None),
            default=_FAR,
        )

    next_item = min(
        today_items,
        key=lambda i: (0 if (i["due_at"] or _FAR) < start else 1, when(i), -i["priority"]),
        default=None,
    )
    return {
        "today": len(today_items) - overdue,
        "overdue": overdue,
        "upcoming": upcoming,
        "all_open": len(items),
        "completed": int(done[0]) if done else 0,
        "by_list": by_list,
        "next": next_item,
    }


# ---------- 写 ----------


def _count_open(conn: sqlite3.Connection, owner_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE owner_id=? AND status='open'", (owner_id,)
    ).fetchone()
    return int(row[0])


def create(owner_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """建一条待办；`remind_at` 有值就顺手建 linked reminder（同一事务）。"""
    ensure()
    now = timeutil.now_ts()
    title = _title(data.get("title"))
    note = _note(data.get("note"))
    list_key = _list_key(data.get("list_key"))
    priority = _priority(data.get("priority"))
    recurrence = _recurrence(data.get("recurrence"))
    due_at = _ts(data.get("due_at"), "截止时间")
    remind_at = _ts(data.get("remind_at"), "提醒时间")
    _check_remind(remind_at, now)
    if data.get("parent_id"):
        raise TaskInvalid("子任务将在下一阶段开放")

    tid = _new_id()
    with _conn() as conn:
        # 上限在事务里数：两个并发请求都先读到 299 的话，会一起写进第 300、301 条
        if _count_open(conn, owner_id) >= MAX_TASKS_PER_OWNER:
            raise TaskLimit(f"最多 {MAX_TASKS_PER_OWNER} 条待办，先完成或删除一些")
        conn.execute(
            """INSERT INTO tasks
                  (id, owner_id, title, note, list_key, priority, status,
                   due_at, remind_at, completed_at, recurrence, parent_id, sort_order,
                   created_at, updated_at)
               VALUES (?,?,?,?,?,?,'open',?,?,NULL,?,NULL,0,?,?)""",
            (tid, owner_id, title, note, list_key, priority, due_at, remind_at,
             recurrence, now, now),
        )
        if remind_at is not None:
            reminder_core.upsert_task_reminder(
                conn, owner_id, tid, title, note, remind_at, now=now
            )
    return get(owner_id, tid) or {}


def update(owner_id: str, task_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """局部更新。`patch` 里出现的字段才动；显式 `None` = 清空。

    ★ 端点必须用 `model_dump(exclude_unset=True)` 生成 patch。
    换成 `exclude_none=True` 的话，`{"due_at": null}` 会被吞掉，
    用户永远清不掉已经设过的日期（而且界面上看着像点了没反应）。
    """
    current = get(owner_id, task_id)
    if current is None:
        return None
    now = timeutil.now_ts()

    merged = {
        "title": current["title"],
        "note": current["note"],
        "list_key": current["list_key"],
        "priority": current["priority"],
        "due_at": current["due_at"],
        "remind_at": current["remind_at"],
    }
    for key in ("title", "note", "list_key", "priority", "due_at", "remind_at"):
        if key in patch:
            merged[key] = patch[key]

    title = _title(merged["title"])
    note = _note(merged["note"])
    list_key = _list_key(merged["list_key"])
    priority = _priority(merged["priority"])
    due_at = _ts(merged["due_at"], "截止时间")
    remind_at = _ts(merged["remind_at"], "提醒时间")
    _check_remind(remind_at, now)

    status = current["status"]
    with _conn() as conn:
        conn.execute(
            """UPDATE tasks SET title=?, note=?, list_key=?, priority=?,
                   due_at=?, remind_at=?, updated_at=?
               WHERE id=? AND owner_id=?""",
            (title, note, list_key, priority, due_at, remind_at, now, task_id, owner_id),
        )
        # 只有「标题 / 备注 / 提醒时间」变了才碰提醒。只改 due_at 时**一个字节都不动**
        # —— 不变量 #3：改截止时间不能把已经设好的提醒挪走。
        if any(k in patch for k in ("title", "note", "remind_at")):
            if status == "done":
                # 已完成的待办改了提醒时间也不自动重新启用，等 reopen 再决定
                reminder_core.upsert_task_reminder(
                    conn, owner_id, task_id, title, note, remind_at, now=now,
                    allow_enable=False,
                )
            else:
                reminder_core.upsert_task_reminder(
                    conn, owner_id, task_id, title, note, remind_at, now=now
                )
    return get(owner_id, task_id)


def complete(owner_id: str, task_id: str) -> dict[str, Any] | None:
    """完成。幂等：已经 done 就原样返回（重复点不该报错）。"""
    current = get(owner_id, task_id)
    if current is None:
        return None
    if current["status"] == "done":
        return current
    now = timeutil.now_ts()
    with _conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', completed_at=?, updated_at=? "
            "WHERE id=? AND owner_id=?",
            (now, now, task_id, owner_id),
        )
        # 不变量 #6：完成后联动提醒立刻停用，worker 的 due_for_owner 不再返回它
        reminder_core.disable_task_reminder(conn, owner_id, task_id, now=now)
    return get(owner_id, task_id)


def reopen(owner_id: str, task_id: str) -> dict[str, Any] | None:
    """取消完成。提醒时间还在未来才恢复，过去的**不要**补发一条。"""
    current = get(owner_id, task_id)
    if current is None:
        return None
    if current["status"] == "open":
        return current
    now = timeutil.now_ts()
    remind_at = current["remind_at"]
    with _conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='open', completed_at=NULL, updated_at=? "
            "WHERE id=? AND owner_id=?",
            (now, task_id, owner_id),
        )
        if remind_at and int(remind_at) > now:
            reminder_core.upsert_task_reminder(
                conn, owner_id, task_id, current["title"], current["note"], remind_at, now=now
            )
        else:
            reminder_core.disable_task_reminder(conn, owner_id, task_id, now=now)
    return get(owner_id, task_id)


def delete(owner_id: str, task_id: str) -> bool:
    """删待办 + 删联动提醒（同一事务）。送达流水保留，与 `reminder_core.delete` 同口径。"""
    ensure()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id=? AND owner_id=?", (task_id, owner_id))
        if cur.rowcount <= 0:
            return False
        reminder_core.delete_task_reminder(conn, owner_id, task_id)
    return True


def reset_cache() -> None:
    """测试用：切库后清掉「这个库建过表了」的缓存。"""
    _READY.clear()
