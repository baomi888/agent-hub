"""日历订阅令牌表（一张表，`calendar_tokens`）。

**为什么要有这张表**：安卓的 Web Push 走 FCM，国内连不通（见 lib/push.ts 顶部注释）。
唯一还能在安卓上做到「到点手机响」的通道是**系统日历**——它用本机 AlarmManager
定时，不依赖任何推送服务和网络长连接。代价是日历客户端必须能拉到一个
`.ics` 文件，而日历客户端**不携带 Cookie、不做登录**，所以这个 URL 必须自带凭证。

于是 token 就是凭证。三个设计决定：

1. **一人一 token，`owner_id` 做主键。**
   一个人只需要一个"知穗提醒"日历。用 owner_id 当主键意味着「重置」是换
   token 值而不是插新行——旧 URL 那一行被覆盖，立刻失效，不需要额外的清理逻辑。

2. **URL 里泄露 token ≠ 泄露数据。**
   这个端点返回的内容经过刻意设计：**不含任何任务标题、计划名、数字**，
   只有一句固定的"点开看看今天还剩什么"和一个链接（见 service/calendar.py
   的 `_DESCRIPTION`）。所以 token 泄露的后果只是"别人也能往自己日历里
   加一条每天 20:00 的提醒"，而不是"别人能看到我在备考六级"。
   这一点是刻意的：如果哪天有人想把任务数写进日历描述，请先回来读这段。

3. **`last_fetch_at` 是唯一的产品信号。**
   "有多少人真的把日历订上了"没法从别处知道——导入 .ics 也好、订阅 URL 也好，
   都是客户端私下完成的。所以每次拉取都记一笔，运营时看
   「有 token 的人数」vs「近 7 天有拉取的人数」就是订阅率。

全部是同步函数（sqlite3 是同步的）。async 端点里请 `asyncio.to_thread`。
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from core import session as store

# 已确保建表的库路径集合。键必须带路径：测试会在同一进程里反复切库，
# 只用布尔量的话第二次起就不建表了，表现为 "no such table"。
_READY: set[str] = set()

_SCHEMA = """
-- 一个账号一行。token 是 URL 里那段随机串，32 位 hex（128 bit）——
-- 猜不出来，也不会撞（128 bit 的空间里撞的概率可以当零）。
CREATE TABLE IF NOT EXISTS calendar_tokens (
    owner_id      TEXT PRIMARY KEY,
    token         TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    reset_at      INTEGER,       -- 最近一次换链接的时间，排查"链接怎么失效了"用
    last_fetch_at INTEGER,       -- 最后一次被日历客户端拉取的时间（订阅率就看它）
    fetch_count   INTEGER NOT NULL DEFAULT 0
);
-- 反查要靠它：端点拿到的是 token，要先找回 owner
CREATE UNIQUE INDEX IF NOT EXISTS uq_cal_token ON calendar_tokens(token);
"""


def _conn():
    return store._conn()


def ensure() -> None:
    """幂等建表。

    ★ 不能只在 import 时跑一次：测试会 monkeypatch `session._db_path` 换库，
       那时 import 早就跑完了，新库是空的（"no such table"）。
       键用库路径而不是布尔量，就是为了同一进程里能多次建。
    """
    path = str(store._db_path())
    if path in _READY:
        return
    with _conn() as c:
        c.executescript(_SCHEMA)
    _READY.add(path)


def _now() -> int:
    return int(time.time())


def get_or_create(owner_id: str) -> str:
    """取这个人的 token，没有就生成一个。返回 token。"""
    ensure()
    with _conn() as c:
        row = c.execute(
            "SELECT token FROM calendar_tokens WHERE owner_id=?", (owner_id,)
        ).fetchone()
        if row:
            return str(row[0])
        token = secrets.token_hex(16)  # 32 位 hex
        c.execute(
            "INSERT INTO calendar_tokens (owner_id, token, created_at) VALUES (?,?,?)",
            (owner_id, token, _now()),
        )
        return token


def reset(owner_id: str) -> str:
    """换一个新 token（旧链接立刻失效）。返回新 token。"""
    ensure()
    token = secrets.token_hex(16)
    with _conn() as c:
        c.execute(
            """INSERT INTO calendar_tokens (owner_id, token, created_at, reset_at, fetch_count)
               VALUES (?,?,?,?,0)
               ON CONFLICT(owner_id) DO UPDATE SET
                   token=excluded.token,
                   reset_at=excluded.created_at,
                   last_fetch_at=NULL,
                   fetch_count=0""",
            (owner_id, token, _now(), _now()),
        )
    return token


def owner_of(token: str) -> str | None:
    """token → owner_id。找不到返回 None（端点据此回 404，不透露"这个 token 存在过"）。"""
    if not token:
        return None
    ensure()
    with _conn() as c:
        row = c.execute(
            "SELECT owner_id FROM calendar_tokens WHERE token=?", (token,)
        ).fetchone()
        return str(row[0]) if row else None


def touch_fetch(token: str) -> None:
    """记一次拉取。日历客户端会周期性来，这就是"还在用"的唯一证据。"""
    ensure()
    with _conn() as c:
        c.execute(
            """UPDATE calendar_tokens
               SET last_fetch_at=?, fetch_count=fetch_count+1
               WHERE token=?""",
            (_now(), token),
        )


def stats() -> dict[str, Any]:
    """给运营看的两行数：一共多少人有链接、近 7 天有多少人真在拉。"""
    ensure()
    week = _now() - 7 * 86400
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        active = c.execute(
            "SELECT COUNT(*) FROM calendar_tokens WHERE last_fetch_at>=?", (week,)
        ).fetchone()[0]
    return {"total": int(total), "active_7d": int(active)}
