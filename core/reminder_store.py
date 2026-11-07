"""每人自己设的提醒时刻（一张表，`reminder_prefs`）。

## 为什么要独立一张表

提醒时刻同时决定两件事：**Web Push 什么时候发**、**日历里那条事件落在几点**。
所以它不能挂在 `calendar_tokens` 上——没订阅日历（下载导入过、或者只用推送）的人
一样有权改时间，而那些人在 `calendar_tokens` 里根本没有行。
反过来把偏好挂在日历表上，就会出现「先改时间、后生成链接」时偏好丢失的怪事。

## 为什么必须落库而不是放前端

推送是 worker 进程发的，worker 读不到浏览器里的设置。日历那边更是服务端生成 ICS，
客户端只会拿着 URL 反复来拉。两处都只能从库里读。

## 分钟为什么只允许 0/15/30/45

调度侧是 `*/15 * * * *`（见 core/scheduler.py 的 push.daily）。如果允许写 07 分，
那这个人的提醒永远等不到匹配的 tick，表现为「设了时间但从来不响」——
这是最难查的一类 bug（设置页显示得好好的）。所以写入时就把分钟夹到四档。

## 和 `config.PUSH_HOUR` 的关系

`config.PUSH_HOUR/PUSH_MINUTE` 是**默认值**，只对没设置过的人生效。
这个设计让人可以只改默认值就调整全站新用户，而不用回填所有人的行。
"""

from __future__ import annotations

import time

from core import config
from core import session as store

# 键必须带库路径：测试在同一进程里反复切库时，只用布尔量第二次就不建表了
_READY: set[str] = set()

# 合法的分钟档位，必须与 scheduler 里 push.daily 的 cron 步长一致
MINUTE_STEPS = (0, 15, 30, 45)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminder_prefs (
    owner_id   TEXT PRIMARY KEY,
    hour       INTEGER NOT NULL,
    minute     INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _conn():
    return store._conn()


def ensure() -> None:
    """幂等建表（键用库路径，理由同 calendar_store.ensure）。"""
    path = str(store._db_path())
    if path in _READY:
        return
    with _conn() as c:
        c.executescript(_SCHEMA)
    _READY.add(path)


def default_time() -> tuple[int, int]:
    """没设置过的人用的时刻，来自环境变量。"""
    hour = max(0, min(23, int(config.PUSH_HOUR)))
    minute = int(config.PUSH_MINUTE)
    # 环境里写了非四档的值也要夹回来，否则这批人永远不响
    if minute not in MINUTE_STEPS:
        minute = min(MINUTE_STEPS, key=lambda m: abs(m - minute))
    return hour, minute


def normalize(hour: int, minute: int) -> tuple[int, int]:
    """把任意输入夹到合法范围。分钟就近吸附到 0/15/30/45。"""
    hour = max(0, min(23, int(hour)))
    minute = int(minute)
    if minute not in MINUTE_STEPS:
        minute = min(MINUTE_STEPS, key=lambda m: abs(m - minute))
    return hour, minute


def get(owner_id: str) -> tuple[int, int]:
    """这个人的提醒时刻；没设置过返回默认值（不写库，避免"打开设置页就建行"）。"""
    if not owner_id:
        return default_time()
    ensure()
    with _conn() as c:
        row = c.execute(
            "SELECT hour, minute FROM reminder_prefs WHERE owner_id=?", (owner_id,)
        ).fetchone()
    if not row:
        return default_time()
    return normalize(row[0], row[1])


def set_time(owner_id: str, hour: int, minute: int) -> tuple[int, int]:
    """设置时刻，返回夹取后的实际值（调用方拿它回显，别自己再算一遍）。"""
    ensure()
    hour, minute = normalize(hour, minute)
    with _conn() as c:
        c.execute(
            """INSERT INTO reminder_prefs (owner_id, hour, minute, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(owner_id) DO UPDATE SET
                   hour=excluded.hour,
                   minute=excluded.minute,
                   updated_at=excluded.updated_at""",
            (owner_id, hour, minute, int(time.time())),
        )
    return hour, minute


def all_prefs() -> dict[str, tuple[int, int]]:
    """全量偏好，给推送调度用：一次查完，别在循环里逐个 SELECT。"""
    ensure()
    with _conn() as c:
        rows = c.execute("SELECT owner_id, hour, minute FROM reminder_prefs").fetchall()
    return {str(r[0]): normalize(r[1], r[2]) for r in rows}
