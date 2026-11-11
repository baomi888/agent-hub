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

import re
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

# 图标名的字符集上限。**不做枚举白名单**：icon 是纯展示字段，
# 前端认不出的名字会自己退回清单图标（见 `taskMark`），所以多存一个
# 陌生名字的代价是"这颗图标不显示"，而不是坏数据。
# 反过来说，如果这里写死一张表，前端想加个图标就得同步改后端 ——
# 展示层的迭代不该被持久层的白名单卡住。
_ICON_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")

# 排序用的「无穷大」：SQLite 的 ORDER BY 里 NULL 排最前，
# 而「没有截止日期」应当排最后，所以先把 NULL 换成一个大到不会撞车的整数。
_FAR = 4102444800  # 2100-01-01，够用到这个产品不再存在

# 手排序号参与排序的前缀。`(sort_order = 0)` 在 SQLite 里是 0/1，
# 升序后「排过的手动位（>0）」在前、「没排过（0）」在后 —— 两者各自保持内部顺序。
# ★ 0 必须解释成「未参与排序」而不是「第 0 位」：老数据全是 0，
#   一旦 0 算最前，所有历史待办会瞬间挤到用户亲手排过的那几条上面。
_MANUAL_FIRST = "(sort_order = 0) ASC, sort_order ASC, "

# 批量动作。放在 core 里而不是端点里：worker / 脚本也可能要批量补账。
BULK_ACTIONS = ("complete", "reopen", "delete", "move")

# 一次批量最多处理多少条。200 是 list 的上限，保持"选中范围 ⊆ 一屏能看到的范围"。
MAX_BULK = 200

# 优先级四档：0 普通 / 1 日常 / 2 重要 / 3 紧急。
# ★ 这是**有序**值，不是标签集合：ORDER BY priority DESC 直接靠它排序，
#   所以任何改动都要连着迁移数据一起做（见 `_widen_priority`）。
PRIORITY_LABELS = {0: "普通", 1: "日常", 2: "重要", 3: "紧急"}
PRIORITY_MAX = max(PRIORITY_LABELS)

_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    title         TEXT NOT NULL,
    note          TEXT,
    list_key      TEXT NOT NULL DEFAULT 'inbox',
    icon          TEXT,
    priority      INTEGER NOT NULL DEFAULT 0 CHECK (priority IN (0, 1, 2, 3)),
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
"""

# 索引单独一段：重建表时要先把旧索引连同旧表一起清掉，再按这里补回来。
# 混在 `_TABLE` 里会让 `CREATE INDEX IF NOT EXISTS` 因为**旧索引名字还占着**
# 而静默跳过，最后 DROP 旧表把索引一起带走 —— 表没了索引，查询全表扫。
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tasks_owner_status_due ON tasks(owner_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_remind ON tasks(owner_id, remind_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_created ON tasks(owner_id, created_at DESC);
"""

_SCHEMA = _TABLE + _INDEXES

# 老库的 CHECK 旧约束。命中就说明三档时代建的库，要整表重建。
_LEGACY_PRIORITY_CHECK = "priority IN (0, 1, 2)"


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
        # 老库补列（幂等）：`CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不做，
        # 所以新加字段必须单独 ALTER，否则升级部署后线上库永远少这一列，
        # 症状是 SELECT * 拿不到 icon、前端每颗图标都退回清单默认值。
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "icon" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN icon TEXT")
        _widen_priority(conn)
    _READY.add(path)


def _widen_priority(conn) -> None:
    """把三档优先级的库升到四档。

    SQLite **不能 ALTER 一条 CHECK 约束**（`ALTER TABLE ... DROP CONSTRAINT` 不存在），
    唯一的办法是整表重建：改名 → 建新表 → 搬数据 → 删旧表 → 补索引。
    不做这件事的后果不是报错而是「静默降级」：新值 3 写进去会撞 CHECK，
    表现为用户选了「紧急」却存成别的，或者整条 PATCH 500。

    ★ 旧值要**平移**而不是原样搬：老库里 1=重要 2=紧急，新序里 1=日常、2=重要。
      原样搬会让每一条「紧急」悄悄降成「重要」、每一条「重要」降成「日常」。
      0 和 3 之后的语义没变，所以只动 1、2 两档。

    ★ 幂等：判据是 `sqlite_master.sql` 里还写着旧 CHECK。重建完这句话就没了，
      下次启动直接跳过；`_READY` 缓存只是省事，不是正确性依赖。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if row is None or _LEGACY_PRIORITY_CHECK not in str(row["sql"]):
        return
    conn.executescript(
        f"""
        ALTER TABLE tasks RENAME TO tasks_priority_old;
        DROP INDEX IF EXISTS idx_tasks_owner_status_due;
        DROP INDEX IF EXISTS idx_tasks_owner_remind;
        DROP INDEX IF EXISTS idx_tasks_owner_created;
        {_TABLE}
        INSERT INTO tasks
            (id, owner_id, title, note, list_key, icon, priority, status,
             due_at, remind_at, completed_at, recurrence, parent_id, sort_order,
             created_at, updated_at)
        SELECT id, owner_id, title, note, list_key, icon,
               CASE priority WHEN 1 THEN 2 WHEN 2 THEN 3 ELSE priority END,
               status, due_at, remind_at, completed_at, recurrence, parent_id,
               sort_order, created_at, updated_at
          FROM tasks_priority_old;
        DROP TABLE tasks_priority_old;
        {_INDEXES}
        """
    )


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


def _icon(value: Any) -> str | None:
    """图标名。空 / None = 不指定，由前端按清单挑一个默认的。

    宽松校验而不是枚举：见 `_ICON_RE` 上面的注释。这里只挡两种真正有害的输入 ——
    空串以外的乱码（会原样进 JSON 响应）和超长串（一屏 20 条每条都带 1KB 图标名）。
    """
    if value is None:
        return None
    name = str(value).strip().lower()
    if not name:
        return None
    if not _ICON_RE.match(name):
        raise TaskInvalid("图标名只能用英文字母、数字和下划线")
    return name


def _priority(value: Any) -> int:
    try:
        p = int(value or 0)
    except (TypeError, ValueError):
        raise TaskInvalid("优先级只能是 0 / 1 / 2 / 3") from None
    if p not in PRIORITY_LABELS:
        raise TaskInvalid("优先级只能是 0 / 1 / 2 / 3")
    return p


def _sort_order(value: Any) -> int:
    """手排序号。0 = 没排过（回落到按优先级/截止时间），正数才是用户排的位置。

    ★ 为什么 0 不当作「排在最前」：老数据全是 0，一旦 0 算最前，
      所有历史待办会瞬间挤到手动排过的那几条上面。0 必须是"未参与排序"。
    """
    if value is None or value == "":
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise TaskInvalid("排序号必须是整数") from None
    if n < 0:
        raise TaskInvalid("排序号不能是负数")
    return n


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
            # 手动排过的压过一切（那是用户亲手定的位置）；没排过的（0）落回自动规则：
            # 逾期优先 → 截止时间 → 提醒时间 → 优先级。
            f"ORDER BY {_MANUAL_FIRST} CASE WHEN due_at IS NOT NULL AND due_at < {start} "
            "THEN 0 ELSE 1 END, "
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
            f"ORDER BY {_MANUAL_FIRST} "
            f"MIN(COALESCE(due_at, {_FAR}), COALESCE(remind_at, {_FAR})) ASC, "
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
            f"ORDER BY {_MANUAL_FIRST} priority DESC, (due_at IS NULL), due_at ASC, "
            "created_at DESC"
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
    icon = _icon(data.get("icon"))
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
                  (id, owner_id, title, note, list_key, icon, priority, status,
                   due_at, remind_at, completed_at, recurrence, parent_id, sort_order,
                   created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,'open',?,?,NULL,?,NULL,0,?,?)""",
            (tid, owner_id, title, note, list_key, icon, priority, due_at, remind_at,
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
        "icon": current.get("icon"),
        "priority": current["priority"],
        "due_at": current["due_at"],
        "remind_at": current["remind_at"],
        "sort_order": current["sort_order"],
    }
    for key in (
        "title",
        "note",
        "list_key",
        "icon",
        "priority",
        "due_at",
        "remind_at",
        "sort_order",
    ):
        if key in patch:
            merged[key] = patch[key]

    title = _title(merged["title"])
    note = _note(merged["note"])
    list_key = _list_key(merged["list_key"])
    icon = _icon(merged["icon"])
    priority = _priority(merged["priority"])
    due_at = _ts(merged["due_at"], "截止时间")
    remind_at = _ts(merged["remind_at"], "提醒时间")
    sort_order = _sort_order(merged["sort_order"])
    _check_remind(remind_at, now)

    status = current["status"]
    with _conn() as conn:
        conn.execute(
            """UPDATE tasks SET title=?, note=?, list_key=?, icon=?, priority=?,
                   due_at=?, remind_at=?, sort_order=?, updated_at=?
               WHERE id=? AND owner_id=?""",
            (
                title,
                note,
                list_key,
                icon,
                priority,
                due_at,
                remind_at,
                sort_order,
                now,
                task_id,
                owner_id,
            ),
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


def reorder(owner_id: str, ordered_ids: list[str]) -> int:
    """按给定顺序重排一批待办，返回真正改动的行数。

    一次事务写完整组，而不是「上移一格就发一个请求」：拖一次发 N 个请求
    既慢又会在中途失败时留下半新半旧的顺序。

    编号从 10 开始、步长 10：留出空隙，将来要在两条之间插一条时
    不必把后面全部重写一遍（取中间值即可）。

    ★ 只在传进来的 id 上写值，不碰组外的行。用户看到的列表可能已经被
      scope 过滤过（今天 / 接下来），把过滤掉的项一起编号会把它们的
      sort_order 弄乱 —— 那些项在别的视图里还有自己的顺序。
    """
    ensure()
    if not ordered_ids:
        return 0
    now = timeutil.now_ts()
    changed = 0
    with _conn() as conn:
        for index, task_id in enumerate(ordered_ids):
            cur = conn.execute(
                "UPDATE tasks SET sort_order=?, updated_at=? WHERE id=? AND owner_id=?",
                ((index + 1) * 10, now, task_id, owner_id),
            )
            changed += max(0, cur.rowcount)
    return changed


def bulk(
    owner_id: str,
    ids: list[str],
    action: str,
    list_key: str | None = None,
) -> int:
    """批量动作，返回真正生效的条数。

    ★ 为什么锁在 owner_id + id IN (...) 上、而不是先 `get()` 再逐条改：
      多选列表里混进别人的 id 时（前端缓存过期、手改请求），
      `get()` 返回 None 就跳过 —— 这一步必须由 SQL 自己保证，
      不能让调用方"记得先校验"。跨用户的行在这里天然写不进去。

    ★ 批量完成要连带停用联动提醒（不变量 #6 的批量版），否则会出现
      「列表里都打勾了，到点还在响」—— 单条 complete 是这么做的，
      批量走另一条路的话很容易漏。
    """
    ensure()
    if action not in BULK_ACTIONS:
        raise TaskInvalid("批量动作只能是 " + " / ".join(BULK_ACTIONS))
    uniq = list(dict.fromkeys(ids))[:MAX_BULK]
    if not uniq:
        return 0
    now = timeutil.now_ts()
    marks = ",".join("?" * len(uniq))

    with _conn() as conn:
        owned = [
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM tasks WHERE owner_id=? AND id IN ({marks})",
                (owner_id, *uniq),
            ).fetchall()
        ]
        if not owned:
            return 0
        done_marks = ",".join("?" * len(owned))

        if action == "delete":
            conn.execute(
                f"DELETE FROM tasks WHERE owner_id=? AND id IN ({done_marks})",
                (owner_id, *owned),
            )
            for tid in owned:
                reminder_core.delete_task_reminder(conn, owner_id, tid)
            return len(owned)

        if action == "move":
            # `_list_key(None)` 会回落到 inbox —— 批量「改清单」漏传参数时
            # 把几十条静默搬进收件箱，比直接报错难查得多，所以这里要求显式给。
            if not list_key:
                raise TaskInvalid("批量改清单要说明改到哪个清单")
            target = _list_key(list_key)
            cur = conn.execute(
                f"UPDATE tasks SET list_key=?, updated_at=? "
                f"WHERE owner_id=? AND id IN ({done_marks})",
                (target, now, owner_id, *owned),
            )
            return max(0, cur.rowcount)

        # complete / reopen
        status = "done" if action == "complete" else "open"
        cur = conn.execute(
            f"UPDATE tasks SET status=?, completed_at=?, updated_at=? "
            f"WHERE owner_id=? AND id IN ({done_marks}) AND status<>?",
            (status, now if status == "done" else None, now, owner_id, *owned, status),
        )
        changed = max(0, cur.rowcount)

        for tid in owned:
            if status == "done":
                reminder_core.disable_task_reminder(conn, owner_id, tid, now=now)
                continue
            # reopen：提醒时间还在未来才恢复，过去的不要补发（与单条 reopen 同口径）
            row = conn.execute(
                "SELECT title, note, remind_at FROM tasks WHERE id=? AND owner_id=?",
                (tid, owner_id),
            ).fetchone()
            if row is None:
                continue
            remind_at = row["remind_at"]
            if remind_at and int(remind_at) > now:
                reminder_core.upsert_task_reminder(
                    conn, owner_id, tid, row["title"], row["note"], remind_at, now=now
                )
            else:
                reminder_core.disable_task_reminder(conn, owner_id, tid, now=now)
        return changed


def reset_cache() -> None:
    """测试用：切库后清掉「这个库建过表了」的缓存。"""
    _READY.clear()
