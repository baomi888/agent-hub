"""提醒中心的内核：提醒项本体 + 触发流水 + 老数据迁移。

## 为什么新开一套表，而不是扩 `core/reminder_store.py`

`reminder_store` 管的是「每人每天一个时刻」——一张只有 hour/minute 的表，同时喂
Web Push 和日历 ICS。这次要的是「一人 N 条、每条有自己的重复规则和内容」，
形状完全不同。硬塞进那张表只能加列，而加列会让「日历只认一个时间」这条既有语义
说不清（一张表里既有「那一个时刻」又有「一堆别的时刻」，读的人没法判断该取哪行）。

所以新开一套表，`reminder_store` 原地不动（规格要求保留 6 个月回滚余地），
老数据由 `_migrate()` 单向搬过来。搬的时候有两条不能破：

1. **老用户行为不变**：同一时刻、同一内容、没内容照样不发；
2. **`/api/remind` 返回结构不变**：老前端和日历服务端还在读它。

## 时间口径

落库一律 Unix 秒，解释一律 `core/timeutil.TZ`（禁止裸 `datetime.now()`）。
`at` 存「本地 HH:MM」字符串而不是偏移量：全站一个业务时区，存字符串能让
「每天 22:00」在任何时候读出来都还是 22:00。

## `next_fire_at` 为什么是冗余列

它完全可以从 freq/at 现算。但 tick 每分钟都要问一次「谁到点了」，现算就得把
整张表拉回 Python 逐条判断；冗余一列 + `(enabled, next_fire_at)` 索引把这件事
变成一次索引扫描。写少读多，这笔交易划算。

★ 它也是**唯一**允许在 tick 里写的业务字段（规格禁止清单第 9 条）：
tick 写得越少，SQLite 写竞争越小（`delegate.retry` 就在旁边跑）。

## 为什么 due() 要卡一个滞后窗口

worker 重启、机器休眠会让某一分钟的 tick 压根没跑。没有窗口的话，恢复后会把
「三小时前课前十分钟该响的提醒」一次性补发出来——这比不响更糟。所以：

* `due()` 只返回 `now - next_fire_at <= MAX_LAG_SECONDS` 的；
* 更老的由 `catch_up()` 把 `next_fire_at` 推到下一次（并留一行 failed 流水），
  让「今天为什么没响」在库里查得到。

`reminder_fires.status` 的取值里没有 `missed`（规格把枚举定死了，中途加值要写迁移），
所以迟到的那些记成 `failed` + `detail='missed:...'`。这是**借用**，不是分类错误，
`detail` 一看就知道不是发送失败。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from core import config, timeutil
from core import session as store

# 已建表的库路径集合。键必须带路径：测试会在同一进程里反复切库，
# 只用布尔量的话第二个用例起就不建表了，表现为 "no such table"。
_READY: set[str] = set()

# 一人最多 50 条。没有上限的提醒列表三个月后一定是一坨没人敢删的垃圾。
MAX_PER_OWNER = 50

# 迟到多久还算「该补发」。15 分钟 = 丢 15 个 tick 仍然补，
# 再久就只推进不补发（理由见模块注释最后一段）。
MAX_LAG_SECONDS = 15 * 60

# 一次性迁移的完成标记，写进 SQLite 的 `PRAGMA user_version`（库头里的整数，
# 不需要新表）。为什么需要它：迁移的判据是「有没有 source='system' 的行」，
# 用户把那条删掉后，下一次进程重启会把它当成「还没迁移」再建回来 ——
# 「删了又自己回来」比不迁移更像 bug。标记之后，重启不再重跑迁移；
# 迁移后新开推送的人由 api/push.py 的 ensure_system 单独兜住。
_MIGRATION_VERSION = 20261109

# 来源枚举。`system` 是老「每日提醒」迁移过来的那一类，不对用户展示。
# `task` 是**待办联动提醒**的内部来源：一个待办最多一条，由 core/task_core.py
# 在同一个事务里 upsert / disable。它不出现在七个来源开关里，也不出现在
# `GET /api/reminders` 的列表里 —— 否则用户会看到两条一模一样的「交高数作业」。
SOURCES = (
    "manual",
    "cet",
    "checkin",
    "delegate",
    "focus",
    "timetable",
    "deadline",
    "words",
    "system",
    "task",
)

# 前四个必须与 api/delegate.py 的 FREQS 完全一致（规格要求直接对齐，不发明新枚举），
# 只多一个 once：委托没有「只此一次」的需求，手动提醒有。
FREQS = ("once", "daily", "weekdays", "weekly", "interval")

ACTIONS = ("notify", "agent_deliver", "suggest_focus", "open_tool")
CHANNELS = ("push", "inapp", "calendar", "local")
TRIGGER_KINDS = ("at", "relative")
ANCHORS = ("class_start", "ddl_due", "focus_end")

STATUS_SENT = "sent"
STATUS_QUIET = "skipped_quiet"
STATUS_CAP = "skipped_cap"
STATUS_EMPTY = "skipped_empty"
STATUS_FAILED = "failed"

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_SCHEMA = """
-- 提醒项本体
CREATE TABLE IF NOT EXISTS reminders (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    title          TEXT NOT NULL,
    note           TEXT,
    source         TEXT NOT NULL DEFAULT 'manual',
    source_ref     TEXT,
    override_json  TEXT,
    freq           TEXT NOT NULL DEFAULT 'daily',
    at             TEXT,
    weekday        INTEGER,
    every_minutes  INTEGER,
    trigger_kind   TEXT NOT NULL DEFAULT 'at',
    anchor         TEXT,
    offset_minutes INTEGER,
    action         TEXT NOT NULL DEFAULT 'notify',
    action_json    TEXT,
    channels       TEXT NOT NULL DEFAULT 'push',
    enabled        INTEGER NOT NULL DEFAULT 1,
    next_fire_at   INTEGER,
    -- 绝对触发时刻（Unix 秒）。`once` + `fire_at` = 「2026-11-24 20:00 只响一次」，
    -- 补上 `once` 只能表达 HH:MM 的缺口。空值 = 仍按 at/weekday 推算。
    fire_at        INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(enabled, next_fire_at);
CREATE INDEX IF NOT EXISTS idx_reminders_owner ON reminders(owner_id, enabled);

-- 触发流水。主键就是幂等键：同一 (提醒, 计划时刻) 跑两遍也只会有一行。
CREATE TABLE IF NOT EXISTS reminder_fires (
    reminder_id TEXT NOT NULL,
    planned_at  INTEGER NOT NULL,
    fired_at    INTEGER,
    status      TEXT NOT NULL,
    detail      TEXT,
    clicked_at  INTEGER,
    PRIMARY KEY (reminder_id, planned_at)
);
CREATE INDEX IF NOT EXISTS idx_fires_time ON reminder_fires(planned_at);

-- 用户时间偏好：「专属时间」落在这里（静默时段 / 活跃时段 / 每日上限）。
-- 没有行 = 全用默认值，所以注册时不写库（同 reminder_store.get 的思路）。
CREATE TABLE IF NOT EXISTS user_time_prefs (
    owner_id       TEXT PRIMARY KEY,
    quiet_start    TEXT NOT NULL DEFAULT '23:00',
    quiet_end      TEXT NOT NULL DEFAULT '07:00',
    active_windows TEXT,
    daily_cap      INTEGER NOT NULL DEFAULT 6,
    updated_at     INTEGER NOT NULL
);

-- 来源开关：**没有行 = 关**。默认全关，所以不必给新用户预插行；
-- 「关掉」写成 enabled=0 而不是删行，是为了让「他什么时候关的」查得到。
CREATE TABLE IF NOT EXISTS reminder_source_prefs (
    owner_id   TEXT NOT NULL,
    source     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (owner_id, source)
);

-- 校园工具集上云（第一波只建表，写入在第二波）。
CREATE TABLE IF NOT EXISTS campus_state (
    owner_id     TEXT NOT NULL,
    tool         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (owner_id, tool)
);
"""

# 「一人只能有一条 system 提醒」的唯一索引。
#
# 为什么不能直接写进 `_SCHEMA`：web 与 worker 同时首次迁移是真实存在的交错
# （两个进程各自 SELECT「有没有 system 行」，都得到没有，然后各插一条）。
# 历史上已经落下的重复行要先清掉，否则建唯一索引会直接失败、而且每次启动都失败。
# 保留 rowid 最小的那条：它就是最早迁移出来的那条，`/api/remind` 也一直读它。
_UNIQUE_SYSTEM_SQL = """
DELETE FROM reminders
 WHERE source='system'
   AND rowid NOT IN (
       SELECT MIN(rowid) FROM reminders WHERE source='system' GROUP BY owner_id
   );
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_one_system
    ON reminders(owner_id) WHERE source='system';
"""

# 「一个待办最多一条 linked reminder」的唯一索引。
#
# ★ 它必须**独立于 `_MIGRATION_VERSION`**：老库升上来时 `_migrate()` 早就跑过了，
#   版本号不会再前进，把建索引塞进迁移里，老库永远等不到这一条 ——
#   表现是同一个待办偶尔出现两条提醒（两个请求并发 upsert 时），极难复现。
_UNIQUE_TASK_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_one_task
    ON reminders(owner_id, source_ref) WHERE source='task';
"""


class ReminderInvalid(ValueError):
    """输入不合法。端点据此回 400（而不是 500）。"""


class ReminderLimit(ValueError):
    """条数超上限。单独立一个类，是为了让端点给出「先删掉不用的」这句提示。"""


# ---------- 建表与迁移 ----------


def _db_path() -> str:
    return store._db_path()  # noqa: SLF001


def _conn():
    return store._get_conn()  # noqa: SLF001


def ensure() -> None:
    """幂等建表 + 一次性迁移（键用库路径，理由同 reminder_store.ensure）。"""
    path = str(_db_path())
    if path in _READY:
        return
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        # 老库补列必须早于建索引：`CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列，
        # 而下面的 UPDATE 语句里已经写死了 fire_at。
        _ensure_fire_at(conn)
        # 去重必须早于建唯一索引；顺序反了会在有重复行的库上直接抛错
        conn.executescript(_UNIQUE_SYSTEM_SQL)
        conn.executescript(_UNIQUE_TASK_SQL)
        _migrate(conn)
    # ★ 放在迁移之后：迁移中途抛异常时不进缓存，下一次调用会重试，
    #   而不是留下「表建好了、数据没搬」的半截状态还当成已完成。
    _READY.add(path)


def _ensure_fire_at(conn: sqlite3.Connection) -> None:
    """老库补 `fire_at` 列（幂等）。**独立于迁移版本号**，理由见 `_UNIQUE_TASK_SQL`。"""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(reminders)").fetchall()}
    if "fire_at" not in cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN fire_at INTEGER")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _snap_time(hour: Any, minute: Any) -> tuple[int, int]:
    """把任意输入夹到合法范围，分钟就近吸附到 0/15/30/45。

    吸附是为了和 `reminder_store.MINUTE_STEPS` 保持一致：`/api/remind` 的返回里
    还把 steps 当契约给老前端。新的提醒项自己支持 1–59 分钟，不走这里。
    """
    from core import reminder_store  # 局部 import：只要常量，不在 import 期建表

    h = max(0, min(23, int(hour)))
    m = int(minute)
    if m not in reminder_store.MINUTE_STEPS:
        m = min(reminder_store.MINUTE_STEPS, key=lambda step: abs(step - m))
    return h, m


def default_time() -> tuple[int, int]:
    """没设置过的人用的时刻（来自环境变量，与 reminder_store.default_time 同口径）。"""
    return _snap_time(
        getattr(config, "PUSH_HOUR", 20),
        getattr(config, "PUSH_MINUTE", 0),
    )


def _legacy_prefs(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """老表 `reminder_prefs` 的全量偏好，一次查完（迁移里按人循环用）。"""
    if not _table_exists(conn, "reminder_prefs"):
        return {}
    rows = conn.execute("SELECT owner_id, hour, minute FROM reminder_prefs").fetchall()
    return {str(r[0]): _snap_time(r[1], r[2]) for r in rows}


def _insert_system_row(
    conn: sqlite3.Connection, owner_id: str, hour: int, minute: int, now: int
) -> None:
    """给一个人建「每日回顾」那条系统提醒（迁移专用，直接走 SQL 不绕 create）。

    action 用 `agent_deliver` 而不是 `notify`（规格字面写的是 notify）：
    这条的语义是「接住原来 push.daily 那一条」，内容必须还是
    「六级今天还剩几个任务 / 今天还没打卡」，没内容就不发。
    写成 notify 会给「今天本来无话可说」的人凭空多一条「每日回顾」，
    直接违背风险表里「没内容不发是最不能丢的一条」。
    """
    at = f"{hour:02d}:{minute:02d}"
    rid = _new_id()
    row = {
        "freq": "daily",
        "at": at,
        "weekday": None,
        "every_minutes": None,
        "next_fire_at": None,
    }
    # OR IGNORE：唯一索引兜底。并发迁移时两个进程都走到这里，后到的这条
    # 会被索引挡掉，而不是给用户落下两条「每日回顾」。
    conn.execute(
        """INSERT OR IGNORE INTO reminders
              (id, owner_id, title, note, source, source_ref, override_json,
               freq, at, weekday, every_minutes, trigger_kind, anchor, offset_minutes,
               action, action_json, channels, enabled, next_fire_at, created_at, updated_at)
           VALUES (?,?,?,NULL,'system',NULL,NULL,'daily',?,NULL,NULL,'at',NULL,NULL,
                   'agent_deliver',NULL,'push',1,?,?,?)""",
        (rid, owner_id, "每日回顾", at, next_fire(row, now), now, now),
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """把老「每日提醒」搬成新表里的系统提醒（幂等，可反复执行）。

    搬两类人：
    1. `reminder_prefs` 里有行的（自己设过时间的）；
    2. 有活推送订阅的（可能在用默认 20:00 那条）。

    第 2 类不能漏：升级后 `push.daily` 就静默了，只搬第 1 类的话，
    「开着推送但从没进过设置页」的那批人会突然再也收不到提醒，
    而他们的订阅还在、服务端显示一切正常 —— 这种静默失效最难查。
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if version >= _MIGRATION_VERSION:
        return

    now = timeutil.now_ts()
    prefs = _legacy_prefs(conn)
    owners: set[str] = set(prefs)
    sub_owners: list[str] = []
    if _table_exists(conn, "push_subscriptions"):
        sub_owners = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT owner_id FROM push_subscriptions WHERE disabled=0"
            ).fetchall()
        ]
        owners.update(sub_owners)

    has_system = {
        str(r[0])
        for r in conn.execute("SELECT owner_id FROM reminders WHERE source='system'").fetchall()
    }
    for owner in owners:
        if not owner or owner in has_system:
            continue
        hour, minute = prefs.get(owner) or default_time()
        _insert_system_row(conn, owner, hour, minute, now)

    # 来源开关：四六级 / 打卡默认开 —— 这是「默认全关」唯一一次破例，
    # 理由是这两条他们本来就在收，不算新增打扰（规格迁移规则 2）。
    for owner in sub_owners:
        for source in ("cet", "checkin"):
            conn.execute(
                "INSERT INTO reminder_source_prefs (owner_id, source, enabled, updated_at) "
                "VALUES (?,?,1,?) ON CONFLICT(owner_id, source) DO NOTHING",
                (owner, source, now),
            )

    # 最后一次写：中途抛异常时版本不前进，下一次启动会重试（写入本身都幂等）
    conn.execute(f"PRAGMA user_version={int(_MIGRATION_VERSION)}")


# ---------- 行 → 字典 ----------


def _loads(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        # 坏 JSON 不该让整条提醒不可用：当作没写 override，原样展示来源内容
        return None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["override"] = _loads(d.pop("override_json", None))
    d["action_payload"] = _loads(d.pop("action_json", None))
    d["enabled"] = bool(d.get("enabled"))
    return d


def _new_id() -> str:
    return "rm_" + uuid.uuid4().hex[:12]


# ---------- 校验 ----------


def _hhmm(value: Any) -> str:
    text = str(value or "").strip()
    if not _HHMM.match(text):
        raise ReminderInvalid("时间要写成 HH:MM（00:00–23:59）")
    return text


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    """把输入整形成一行的列值。**全量**校验，不做局部合并（合并由 update 负责）。"""
    out: dict[str, Any] = {}

    title = str(data.get("title") or "").strip()
    if not title:
        raise ReminderInvalid("标题不能为空")
    if len(title) > 60:
        raise ReminderInvalid("标题最长 60 个字")
    out["title"] = title

    note = data.get("note")
    note = None if note is None else str(note).strip()
    if note and len(note) > 500:
        raise ReminderInvalid("备注最长 500 个字")
    out["note"] = note or None

    source = str(data.get("source") or "manual")
    if source not in SOURCES:
        raise ReminderInvalid(f"未知来源：{source}")
    out["source"] = source
    ref = data.get("source_ref")
    out["source_ref"] = None if ref in (None, "") else str(ref)[:64]

    override = data.get("override")
    if override is not None and not isinstance(override, dict):
        raise ReminderInvalid("override 必须是对象")
    if isinstance(override, dict) and override:
        unknown = set(override) - {"title", "at", "enabled"}
        if unknown:
            raise ReminderInvalid(f"override 里不认识的字段：{','.join(sorted(unknown))}")
        if "title" in override:
            text = str(override.get("title") or "").strip()
            if not text or len(text) > 60:
                raise ReminderInvalid("override.title 要是 1–60 个字")
            override = {**override, "title": text}
        if "at" in override:
            override = {**override, "at": _hhmm(override.get("at"))}
    # 同时留一份解析后的对象：next_fire 要读 override.at（见 _scheduled），
    # 只留 JSON 字符串会让「改时间」只改展示、不改调度。
    out["override"] = override or None
    out["override_json"] = json.dumps(override, ensure_ascii=False) if override else None

    freq = str(data.get("freq") or "daily")
    if freq not in FREQS:
        raise ReminderInvalid(f"未知重复方式：{freq}")
    out["freq"] = freq

    # 绝对时刻（Unix 秒）。只有 `once` 能用：给一条「每天 22:00」写死一个日期，
    # 第二天起就再也不响，而界面上还写着每天 —— 这种假承诺必须在写入时就拒掉。
    fire_at = data.get("fire_at")
    if fire_at in (None, ""):
        out["fire_at"] = None
    else:
        try:
            fire_at = int(fire_at)
        except (TypeError, ValueError):
            raise ReminderInvalid("fire_at 必须是 Unix 秒") from None
        if fire_at <= 0:
            raise ReminderInvalid("fire_at 必须是正数")
        out["fire_at"] = fire_at
    if out["fire_at"] and freq != "once":
        raise ReminderInvalid("只有「只一次」的提醒可以指定具体日期和时间")

    if freq == "interval":
        every = data.get("every_minutes")
        if every in (None, ""):
            raise ReminderInvalid("按间隔提醒要给出 every_minutes")
        every = int(every)
        if not 60 <= every <= 1440:
            # 下限 60 与委托侧一致：比一小时更密的提醒在这个产品里没有场景，
            # 却会把每日上限和推送配额一起打穿。
            raise ReminderInvalid("间隔要在 60–1440 分钟之间")
        out["every_minutes"] = every
        out["at"] = _hhmm(data["at"]) if data.get("at") else None
    else:
        out["every_minutes"] = None
        # 带绝对时间的 once 允许没有 at（它只响一次，不需要「几点」这条规则）
        if freq == "once" and out["fire_at"]:
            out["at"] = _hhmm(data["at"]) if data.get("at") else None
        else:
            out["at"] = _hhmm(data.get("at"))

    if freq == "weekly":
        weekday = data.get("weekday")
        if weekday is None:
            raise ReminderInvalid("每周提醒要给出 weekday（0=周一）")
        weekday = int(weekday)
        if not 0 <= weekday <= 6:
            raise ReminderInvalid("weekday 只能是 0–6（0=周一）")
        out["weekday"] = weekday
    else:
        out["weekday"] = None

    kind = str(data.get("trigger_kind") or "at")
    if kind not in TRIGGER_KINDS:
        raise ReminderInvalid(f"未知触发方式：{kind}")
    if kind == "relative":
        # ★ 第一波明确不支持：锚点（课前 / DDL 到期 / 专注结束）的数据还在
        #   localStorage 里，服务端拿不到。先收下再静默不响，就是最典型的
        #   「设了时间但从来不响」——宁可现在 400 拒绝，等课表上云再放开。
        raise ReminderInvalid("「课前 / 到期前」这类相对时间要等课表上云后再开放")
    out["trigger_kind"] = kind
    out["anchor"] = None
    out["offset_minutes"] = None

    action = str(data.get("action") or "notify")
    if action not in ACTIONS:
        raise ReminderInvalid(f"未知动作：{action}")
    out["action"] = action
    payload = data.get("action_payload")
    if payload is not None and not isinstance(payload, (dict, list, str)):
        raise ReminderInvalid("action_payload 必须是对象或字符串")
    out["action_json"] = (
        json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, (dict, list))
        else (str(payload) if payload else None)
    )

    raw_channels = data.get("channels", "push")
    if isinstance(raw_channels, (list, tuple)):
        parts = [str(x).strip() for x in raw_channels]
    else:
        parts = [p.strip() for p in str(raw_channels or "").split(",")]
    parts = [p for p in parts if p]
    bad = [p for p in parts if p not in CHANNELS]
    if bad:
        raise ReminderInvalid(f"未知通知方式：{','.join(bad)}")
    if not parts:
        raise ReminderInvalid("至少选一种通知方式")
    out["channels"] = ",".join(dict.fromkeys(parts))

    out["enabled"] = 0 if data.get("enabled") is False else 1
    return out


# ---------- 时间推算 ----------


def next_fire(reminder: dict[str, Any], now: int | None = None) -> int | None:
    """算下一次该响的时刻（Unix 秒）；算不出来返回 None。

    三条容易写错的：

    * `interval` 必须**从原来的 next_fire_at 递推**，不能从 now 起算 ——
      从 now 起算的话，每分钟一跳就会把下一次一直往后推，永远响不了；
    * `weekdays` 要跨过周六周日，`weekly` 要落到指定的星期几；
    * 非 interval 一律只取 **严格大于 now** 的时刻，"等于 now" 留给这一次触发。
    """
    ts = timeutil.now_ts() if now is None else int(now)
    freq = str(reminder.get("freq") or "daily")

    # 绝对时刻优先：`once` + `fire_at` 就是「这一天这一刻只响一次」。
    # 过去的时刻返回 None（不补发）—— 任务接口在写入前就会拒绝过去时间，
    # 走到这里说明是历史数据或时钟回拨，宁可算不出下一次，也不要立刻炸一条。
    if freq == "once" and reminder.get("fire_at"):
        fire_at = int(reminder["fire_at"])
        return fire_at if fire_at > ts else None

    if freq == "interval":
        every = int(reminder.get("every_minutes") or 0)
        if every <= 0:
            return None
        anchor = reminder.get("next_fire_at")
        base = int(anchor) if anchor else ts
        if base > ts:
            return base
        step = every * 60
        return base + ((ts - base) // step + 1) * step

    at = reminder.get("at")
    if not at:
        return None
    try:
        hour, minute = (int(part) for part in str(at).split(":"))
    except (TypeError, ValueError):
        return None

    now_local = datetime.fromtimestamp(ts, timeutil.TZ)
    weekday = reminder.get("weekday")
    for offset in range(0, 8):
        # 8 天足够覆盖 weekly（最多等 7 天）和「今天已过点 → 明天」
        day = (now_local + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if freq == "weekdays" and day.weekday() >= 5:
            continue
        if freq == "weekly" and day.weekday() != int(weekday if weekday is not None else 0):
            continue
        if int(day.timestamp()) > ts:
            return int(day.timestamp())
    return None


def _override_at(row: dict[str, Any]) -> str | None:
    """override 里的时刻（没有就是 None）。"""
    override = row.get("override")
    if isinstance(override, dict):
        at = override.get("at")
        if at:
            return str(at)
    return None


def _scheduled(row: dict[str, Any]) -> dict[str, Any]:
    """调度用的视图：`override.at` 必须生效。

    数据模型里的 override 包含 `at`。如果只让 `resolve()`（展示）用它、
    `next_fire()` 仍读原行的 `at`，就会出现「列表写着 22:00，实际 23:00 响」——
    用户会直接把它当成提醒不准，而且从界面上完全看不出来。
    """
    out = dict(row)
    at = _override_at(row)
    if at:
        out["at"] = at
    return out


# ---------- CRUD ----------


def count(owner_id: str, include_task: bool = True) -> int:
    """这个人的提醒条数。

    `include_task=False` 给限额检查用：**待办的联动提醒不占 50 条额度**。
    否则一个有 50 个带提醒的待办的用户，从此再也建不了普通提醒，
    而界面上只写着「最多 50 条提醒」—— 他数不出来这 50 条在哪。
    """
    ensure()
    sql = "SELECT COUNT(*) FROM reminders WHERE owner_id=?"
    if not include_task:
        sql += " AND source <> 'task'"
    with _conn() as conn:
        row = conn.execute(sql, (owner_id,)).fetchone()
    return int(row[0])


def create(owner_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """新建一条提醒。超上限抛 `ReminderLimit`（端点转 400）。"""
    ensure()
    if count(owner_id, include_task=False) >= MAX_PER_OWNER:
        raise ReminderLimit(f"最多 {MAX_PER_OWNER} 条提醒，先删掉不用的")

    row = _validate(data)
    now = timeutil.now_ts()
    rid = _new_id()
    nxt = next_fire(_scheduled({**row, "next_fire_at": None}), now)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO reminders
                  (id, owner_id, title, note, source, source_ref, override_json,
                   freq, at, weekday, every_minutes, trigger_kind, anchor, offset_minutes,
                   action, action_json, channels, enabled, next_fire_at, fire_at,
                   created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                owner_id,
                row["title"],
                row["note"],
                row["source"],
                row["source_ref"],
                row["override_json"],
                row["freq"],
                row["at"],
                row["weekday"],
                row["every_minutes"],
                row["trigger_kind"],
                row["anchor"],
                row["offset_minutes"],
                row["action"],
                row["action_json"],
                row["channels"],
                row["enabled"],
                nxt,
                row["fire_at"],
                now,
                now,
            ),
        )
    return get(owner_id, rid) or {}


def get(owner_id: str, reminder_id: str) -> dict[str, Any] | None:
    """按 id 取。**归属校验写在 SQL 里**：猜中别人的 id 也只能拿到 None → 404。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM reminders WHERE id=? AND owner_id=?", (reminder_id, owner_id)
        ).fetchone()
    return _row_to_dict(row) if row else None


def _get_any(reminder_id: str) -> dict[str, Any] | None:
    ensure()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_reminders(owner_id: str, scope: str = "all", now: int | None = None) -> list[dict[str, Any]]:
    """按 scope 取列表：today / week / all / paused。

    排序把「还没算下一次的」放最后：`next_fire_at IS NULL` 在 SQLite 的
    ASC 排序里本来就是最前，显式写成 `IS NULL` 才符合「越近越前」的直觉。

    ★ `source='task'` 一律排除。待办的联动提醒由 `/api/tasks` 自己带出，
    在这里再列一遍，用户会看到两条一模一样的「交高数作业」，
    而且点开其中一条改时间，另一条不会变 —— 看起来像同步坏了。
    """
    ensure()
    ts = timeutil.now_ts() if now is None else int(now)
    start, end = timeutil.day_bounds(timeutil.day_str(ts))
    where = ["owner_id = ?", "source <> 'task'"]
    params: list[Any] = [owner_id]
    if scope == "paused":
        where.append("enabled = 0")
    elif scope == "today":
        where += ["enabled = 1", "next_fire_at IS NOT NULL", "next_fire_at >= ?", "next_fire_at < ?"]
        params += [start, end]
    elif scope == "week":
        where += ["enabled = 1", "next_fire_at IS NOT NULL", "next_fire_at >= ?", "next_fire_at < ?"]
        params += [start, end + 6 * 86400]
    sql = (
        "SELECT * FROM reminders WHERE "
        + " AND ".join(where)
        + " ORDER BY (next_fire_at IS NULL), next_fire_at ASC, created_at ASC"
    )
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [resolve(_row_to_dict(r)) for r in rows]


def update(owner_id: str, reminder_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """局部更新。**先合并再整体校验**：校验函数只认完整的一份，局部校验会漏组合错。

    例如只把 freq 改成 weekly 而没给 weekday：合并后校验才会报错；
    只校验传入字段的话，这一行会变成「weekly 但没有 weekday」，next_fire 永远算不出。
    """
    current = get(owner_id, reminder_id)
    if current is None:
        return None
    merged = {
        "title": current["title"],
        "note": current["note"],
        "source": current["source"],
        "source_ref": current["source_ref"],
        "override": current.get("override"),
        "freq": current["freq"],
        "at": current["at"],
        "weekday": current["weekday"],
        "every_minutes": current["every_minutes"],
        "trigger_kind": current["trigger_kind"],
        "action": current["action"],
        "action_payload": current.get("action_payload"),
        "channels": current["channels"],
        "enabled": current["enabled"],
        "fire_at": current.get("fire_at"),
    }
    for key, value in patch.items():
        if key == "action_json":
            key = "action_payload"
        if key in merged:
            merged[key] = value
    row = _validate(merged)

    now = timeutil.now_ts()
    # 时间规则变了就必须重算下一次；没变则保留原值 —— 否则每次改标题都会把
    # 「今天 22:00」推到明天（next_fire 只取严格大于 now 的时刻）。
    schedule_keys = ("freq", "at", "weekday", "every_minutes", "fire_at")
    changed_schedule = any(
        # 用 `k in patch` 而不是 `patch.get(k) is not None`：把 at 显式改成 None
        # （例如 daily → interval）也是一次规则变更，漏判会让 next_fire_at 沿用旧值。
        k in patch and str(patch.get(k)) != str(current.get(k))
        for k in schedule_keys
    )
    # override.at 也是调度输入：只改 override 时上面那组键一个都没动，
    # 不单独判就会保留旧的 next_fire_at（展示与实际再次分叉）。
    override_changed = "override" in patch and _override_at(
        {"override": patch.get("override")}
    ) != _override_at(current)
    nxt = current["next_fire_at"]
    if changed_schedule or override_changed or nxt is None or int(nxt) <= now:
        nxt = next_fire(_scheduled({**row, "next_fire_at": current["next_fire_at"]}), now)

    with _conn() as conn:
        conn.execute(
            """UPDATE reminders SET
                   title=?, note=?, source=?, source_ref=?, override_json=?,
                   freq=?, at=?, weekday=?, every_minutes=?,
                   trigger_kind=?, anchor=?, offset_minutes=?,
                   action=?, action_json=?, channels=?, enabled=?,
                   next_fire_at=?, fire_at=?, updated_at=?
               WHERE id=? AND owner_id=?""",
            (
                row["title"],
                row["note"],
                row["source"],
                row["source_ref"],
                row["override_json"],
                row["freq"],
                row["at"],
                row["weekday"],
                row["every_minutes"],
                row["trigger_kind"],
                row["anchor"],
                row["offset_minutes"],
                row["action"],
                row["action_json"],
                row["channels"],
                row["enabled"],
                nxt,
                row["fire_at"],
                now,
                reminder_id,
                owner_id,
            ),
        )
    return get(owner_id, reminder_id)


def set_enabled(owner_id: str, reminder_id: str, enabled: bool) -> dict[str, Any] | None:
    """暂停 / 恢复。恢复时重算下一次：停了一个月再打开，原来的 next_fire_at 早就过去了。"""
    return update(owner_id, reminder_id, {"enabled": bool(enabled)})


def delete(owner_id: str, reminder_id: str) -> bool:
    """删提醒。**流水不删**：它是「这条到底响过没有」的唯一证据，删了就没法追。"""
    ensure()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id=? AND owner_id=?", (reminder_id, owner_id))
    return cur.rowcount > 0


def snooze(owner_id: str, reminder_id: str, minutes: int) -> dict[str, Any] | None:
    """推迟 N 分钟。只管这一次：响过之后按原规则继续。"""
    minutes = int(minutes)
    if not 1 <= minutes <= 1440:
        raise ReminderInvalid("推迟时间要在 1–1440 分钟之间")
    current = get(owner_id, reminder_id)
    if current is None:
        return None
    now = timeutil.now_ts()
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET next_fire_at=?, enabled=1, updated_at=? WHERE id=? AND owner_id=?",
            (now + minutes * 60, now, reminder_id, owner_id),
        )
    return get(owner_id, reminder_id)


# ---------- 待办联动提醒（source='task'）----------
#
# 三个 helper 都**接收现成的连接、不 commit**：调用方（core/task_core.py）要把
# 「改 tasks 行」和「改 linked reminder」放进同一个事务。要是这里自己 `with _conn()`
# 提交，中途抛异常就会留下「待办改了、提醒还是旧的」——这种半截状态查起来极痛苦。
#
# 也刻意**不走 `create()`**：它有条数上限、而且单独提交。待办的提醒不该占
# 「最多 50 条提醒」的额度，也不该把任务的事务劈成两半。


def _task_reminder_id(conn: sqlite3.Connection, owner_id: str, task_id: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM reminders WHERE owner_id=? AND source='task' AND source_ref=?",
        (owner_id, task_id),
    ).fetchone()
    return str(row[0]) if row else None


def get_task_reminder(owner_id: str, task_id: str) -> dict[str, Any] | None:
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM reminders WHERE owner_id=? AND source='task' AND source_ref=?",
            (owner_id, task_id),
        ).fetchone()
    return _row_to_dict(row) if row else None


def upsert_task_reminder(
    conn: sqlite3.Connection,
    owner_id: str,
    task_id: str,
    title: str,
    note: str | None,
    fire_at: int | None,
    now: int | None = None,
    allow_enable: bool = True,
) -> dict[str, Any] | None:
    """把某个待办的联动提醒对齐到 `fire_at`（或停用）。

    * `fire_at` 在未来 → `enabled=1, next_fire_at=fire_at`；
    * `fire_at` 为空或已过去 → `enabled=0, next_fire_at=NULL`（**不删**：
      送达历史还要能查，见 `reminder_fires` 的外键语义）；
    * 原本没有这条、且 `fire_at` 为空 → 什么都不建（只设截止日期不会响）。

    `title` / `note` 每次都同步：任务标题是唯一真值，不允许提醒行单独长出一个名字。

    `allow_enable=False` 用于「已完成的待办被改了提醒时间」：时刻照样记下来
    （`fire_at` 更新），但保持停用，等 reopen 再决定要不要真的启用。
    """
    ts = timeutil.now_ts() if now is None else int(now)
    fire = int(fire_at) if fire_at else None
    live = bool(fire and fire > ts) and allow_enable
    enabled = 1 if live else 0
    nxt = fire if live else None
    rid = _task_reminder_id(conn, owner_id, task_id)
    if rid is None:
        if fire is None:
            return None
        rid = _new_id()
        conn.execute(
            """INSERT OR IGNORE INTO reminders
                  (id, owner_id, title, note, source, source_ref, override_json,
                   freq, at, weekday, every_minutes, trigger_kind, anchor, offset_minutes,
                   action, action_json, channels, enabled, next_fire_at, fire_at,
                   created_at, updated_at)
               VALUES (?,?,?,?,'task',?,NULL,'once',NULL,NULL,NULL,'at',NULL,NULL,
                       'notify',NULL,'push',?,?,?,?,?)""",
            (rid, owner_id, title, note, task_id, enabled, nxt, fire, ts, ts),
        )
        rid = _task_reminder_id(conn, owner_id, task_id) or rid
    else:
        conn.execute(
            """UPDATE reminders SET title=?, note=?, freq='once', at=NULL, weekday=NULL,
                   every_minutes=NULL, trigger_kind='at', anchor=NULL, offset_minutes=NULL,
                   action='notify', channels='push', enabled=?, next_fire_at=?, fire_at=?,
                   updated_at=?
               WHERE id=? AND owner_id=?""",
            (title, note, enabled, nxt, fire, ts, rid, owner_id),
        )
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    return _row_to_dict(row) if row else None


def disable_task_reminder(
    conn: sqlite3.Connection, owner_id: str, task_id: str, now: int | None = None
) -> None:
    """停用（完成待办 / 清空提醒时间）。不删行，理由同上。"""
    ts = timeutil.now_ts() if now is None else int(now)
    conn.execute(
        "UPDATE reminders SET enabled=0, next_fire_at=NULL, updated_at=? "
        "WHERE owner_id=? AND source='task' AND source_ref=?",
        (ts, owner_id, task_id),
    )


def delete_task_reminder(conn: sqlite3.Connection, owner_id: str, task_id: str) -> None:
    """删除待办时一起删。**流水不删**：与 `delete()` 同一条口径。"""
    conn.execute(
        "DELETE FROM reminders WHERE owner_id=? AND source='task' AND source_ref=?",
        (owner_id, task_id),
    )


# ---------- 到期查询 ----------


def _due_rows(
    now: int | None = None,
    max_lag: int = MAX_LAG_SECONDS,
    owner_id: str | None = None,
) -> list[dict[str, Any]]:
    ensure()
    ts = timeutil.now_ts() if now is None else int(now)
    sql = (
        "SELECT * FROM reminders WHERE enabled=1 AND next_fire_at IS NOT NULL "
        "AND next_fire_at <= ? AND next_fire_at >= ?"
    )
    params: list[Any] = [ts, ts - int(max_lag)]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    sql += " ORDER BY next_fire_at ASC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def due(now: int | None = None, max_lag: int = MAX_LAG_SECONDS) -> list[tuple[str, int]]:
    """本轮到期的 (reminder_id, planned_at)。只读，不写库。"""
    return [(r["id"], int(r["next_fire_at"])) for r in _due_rows(now, max_lag)]


def due_for_owner(
    owner_id: str, now: int | None = None, max_lag: int = MAX_LAG_SECONDS
) -> list[dict[str, Any]]:
    """某个人的到期提醒（tick 的 run_one 用）。返回整行，run_one 要算内容。"""
    return [resolve(r) for r in _due_rows(now, max_lag, owner_id)]


def due_owners(now: int | None = None, max_lag: int = MAX_LAG_SECONDS) -> list[str]:
    """本轮有到期提醒的用户（去重、保持顺序）。

    同一分钟有两个提醒的只算一个人：worker 的幂等键是
    (job_key, subject_id, planned_at)，subject 就是人。这样「同刻合并」
    不需要额外逻辑 —— 一个人一轮只发一条，天然就是合并。
    """
    seen: dict[str, None] = {}
    for row in _due_rows(now, max_lag):
        seen.setdefault(row["owner_id"], None)
    return list(seen)


def catch_up(now: int | None = None, max_lag: int = MAX_LAG_SECONDS, limit: int = 200) -> int:
    """把迟到的提醒推进到下一次，返回处理条数。

    超过 `max_lag` 的不补发（补发等于半夜被三小时前的提醒砸醒），但也**不能不管**：
    不推进的话它会一直躺在「已到期」里，每次 tick 都被扫出来，永远响不了。
    留一行 failed + detail='missed:...' 是为了让「今天为什么没响」查得到。
    """
    ensure()
    ts = timeutil.now_ts() if now is None else int(now)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE enabled=1 AND next_fire_at IS NOT NULL "
            "AND next_fire_at < ? ORDER BY next_fire_at ASC LIMIT ?",
            (ts - int(max_lag), max(1, int(limit))),
        ).fetchall()
    done = 0
    for raw in rows:
        row = _row_to_dict(raw)
        planned = int(row["next_fire_at"])
        record_fire(
            row["id"],
            planned,
            STATUS_FAILED,
            f"missed:{ts - planned}s",
            fired_at=ts,
        )
        advance(row["id"], ts)
        done += 1
    return done


def advance(reminder_id: str, now: int | None = None) -> int | None:
    """把一条提醒推到下一次；`once` 触发后自动停用。返回新的 next_fire_at。

    ★ 基准时间取 `max(now, 原来的 next_fire_at)`，不是单纯的 now。
    因为 next_fire 只返回**严格大于基准**的时刻：如果有人用
    `POST /api/schedule/run-now` 提前跑了一次（planned_at 还在未来），
    基准用 now 就会算出「还是原来那个时刻」，下一次 tick 立刻再发一遍。
    取两者较大值，保证「触发过一次，next_fire_at 就一定往前走」。
    """
    row = _get_any(reminder_id)
    if row is None:
        return None
    ts = timeutil.now_ts() if now is None else int(now)
    if row["freq"] == "once":
        with _conn() as conn:
            conn.execute(
                "UPDATE reminders SET enabled=0, next_fire_at=NULL, updated_at=? WHERE id=?",
                (ts, reminder_id),
            )
        return None
    base = max(ts, int(row.get("next_fire_at") or 0))
    nxt = next_fire(_scheduled(row), base)
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET next_fire_at=?, updated_at=? WHERE id=?",
            (nxt, ts, reminder_id),
        )
    return nxt


# ---------- 触发流水 ----------


def record_fire(
    reminder_id: str,
    planned_at: int,
    status: str,
    detail: str | None = None,
    fired_at: int | None = None,
) -> None:
    """写一行流水。主键 (reminder_id, planned_at) 就是幂等键。

    冲突时只在「原来不是 sent」的情况下覆盖：先 failed 后重试成功要能改过来，
    但已经 sent 的那一行不能被后来的重跑改成别的（那会让「响过没有」失真）。
    """
    ensure()
    ts = timeutil.now_ts() if fired_at is None else int(fired_at)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO reminder_fires (reminder_id, planned_at, fired_at, status, detail)
               VALUES (?,?,?,?,?)
               ON CONFLICT(reminder_id, planned_at) DO UPDATE SET
                   fired_at=excluded.fired_at,
                   status=excluded.status,
                   detail=excluded.detail
               WHERE reminder_fires.status <> 'sent'""",
            (reminder_id, int(planned_at), ts, status, None if detail is None else str(detail)[:300]),
        )


def fires(owner_id: str, reminder_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """触发流水（新→旧）。join reminders 是为了做归属校验，不额外加 owner 列。"""
    ensure()
    sql = (
        "SELECT f.reminder_id, f.planned_at, f.fired_at, f.status, f.detail, f.clicked_at "
        "FROM reminder_fires f JOIN reminders r ON r.id = f.reminder_id WHERE r.owner_id = ?"
    )
    params: list[Any] = [owner_id]
    if reminder_id:
        sql += " AND f.reminder_id = ?"
        params.append(reminder_id)
    sql += " ORDER BY f.planned_at DESC LIMIT ?"
    params.append(max(1, min(int(limit or 20), 200)))
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- 来源合并 ----------


def _delegation_text(owner_id: str, ref: str) -> tuple[str, str] | None:
    """取委托的原文案（只读，**绝不回写** —— 规格禁止清单第 3 条）。"""
    with _conn() as conn:
        if not _table_exists(conn, "delegations"):
            return None
        row = conn.execute(
            "SELECT name, prompt FROM delegations WHERE delegation_id=? AND owner_id=?",
            (ref, owner_id),
        ).fetchone()
    if not row:
        return None
    return str(row[0] or ""), str(row[1] or "")


def resolve(reminder: dict[str, Any]) -> dict[str, Any]:
    """合并「来源值 + override 值」，返回展示/发送用的视图。

    override 只覆盖 title / at（`enabled` 写在我们自己的行上，不写 override）：
    改一条委托来源的提醒时，新标题落 override，`delegations` 一行都不动。
    """
    out = dict(reminder)
    override = out.get("override")
    if isinstance(override, dict):
        for key in ("title", "at"):
            value = override.get(key)
            if value not in (None, ""):
                out[key] = value
    if out.get("source") == "delegate" and out.get("source_ref"):
        found = _delegation_text(str(out.get("owner_id") or ""), str(out["source_ref"]))
        if found:
            out["source_title"] = found[0]
            out["source_note"] = found[1][:120]
    return out


# ---------- 老接口兼容（/api/remind） ----------


def get_system(owner_id: str) -> dict[str, Any] | None:
    """取这个人的「每日回顾」那条。没有就返回 None（GET 不写库）。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM reminders WHERE owner_id=? AND source='system' "
            "ORDER BY created_at ASC LIMIT 1",
            (owner_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def ensure_system(owner_id: str) -> dict[str, Any]:
    """取或建「每日回顾」那条。

    谁会走到「建」这条分支：迁移跑完之后才开推送订阅的人。老接口
    `/api/remind` 仍然要能读到、改到一条真实存在的提醒，否则用户改了时间
    却没有任何东西会响 —— 而且看不出来。
    """
    existing = get_system(owner_id)
    if existing:
        return existing
    hour, minute = default_time()
    try:
        return create(
            owner_id,
            {
                "title": "每日回顾",
                "source": "system",
                "freq": "daily",
                "at": f"{hour:02d}:{minute:02d}",
                "action": "agent_deliver",
                "channels": "push",
                "enabled": True,
            },
        )
    except sqlite3.IntegrityError:
        # 两个请求同时给同一个人开推送：唯一索引挡掉后到的 INSERT，
        # 这时把先到的那条读回来就行（500 会让前端以为「开提醒失败」）。
        existing = get_system(owner_id)
        if existing:
            return existing
        raise


def system_time(owner_id: str) -> tuple[int, int]:
    """「每日回顾」当前设在几点；没有这条就返回全站默认值。

    ★ 必须读 `_scheduled()` 而不是裸 `at`：override.at 一旦存在就是实际生效的
    时刻，读裸 `at` 会让 /api/remind 报一个「不是它真正会响」的时间。
    """
    item = get_system(owner_id)
    at = _scheduled(item or {}).get("at") or ""
    try:
        hour, minute = (int(part) for part in str(at).split(":"))
        return hour, minute
    except (TypeError, ValueError):
        return default_time()


def set_system_time(owner_id: str, hour: int, minute: int) -> dict[str, Any]:
    """改「每日回顾」的时刻（老接口 POST /api/remind 的落点）。

    老接口的语义是「把这条改到这个点」，所以落点必须**压过** override.at：
    只写 `at` 的话，override 里那份旧时刻仍在 `_scheduled()` 里赢下来，
    表现为「界面上改了、实际还按老时间响」—— 用户会直接放弃这个功能。
    这里保留 override 的其余字段（比如 title），只摘掉 `at`。
    """
    hour, minute = _snap_time(hour, minute)
    item = ensure_system(owner_id)
    patch: dict[str, Any] = {"at": f"{hour:02d}:{minute:02d}", "enabled": True}
    override = item.get("override")
    if isinstance(override, dict) and override.get("at"):
        rest = {k: v for k, v in override.items() if k != "at"}
        patch["override"] = rest or None
    updated = update(owner_id, item["id"], patch)
    return updated or item


# ---------- 概览 ----------


def summary(owner_id: str, now: int | None = None) -> dict[str, Any]:
    """今日概览（给首页 / today 用）。"""
    ts = timeutil.now_ts() if now is None else int(now)
    day = timeutil.day_str(ts)
    start, end = timeutil.day_bounds(day)
    items = list_reminders(owner_id, "all", now=ts)
    today = [
        item
        for item in items
        if item.get("enabled") and item.get("next_fire_at") and start <= int(item["next_fire_at"]) < end
    ]
    paused = [item for item in items if not item.get("enabled")]
    upcoming = [
        item
        for item in items
        if item.get("enabled") and item.get("next_fire_at") and int(item["next_fire_at"]) >= end
    ][:5]
    return {
        "day": day,
        "today": today,
        "today_count": len(today),
        "paused_count": len(paused),
        "upcoming": upcoming,
        "total": len(items),
    }


def reset_cache() -> None:
    """测试用：切库后清掉「这个库建过表了」的缓存。"""
    _READY.clear()
