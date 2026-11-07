"""Web Push 订阅表（一张表，`push_subscriptions`）。

为什么单独一个模块而不是塞进 `core/session.py`：那份文件是四块共用的热点，
每加一张表都要动它。这里照抄 `core/sediment_store.py` / `core/user_feedback.py`
的范式——**共用一个 sqlite 文件、各管各的表、`ensure()` 惰性幂等**。
`core/session._db_path()` 是唯一事实源，测试 monkeypatch 它之后这里自动跟着切库。

三个设计决定，都是踩过或想过事故才定下来的：

1. **endpoint 全局唯一，换账号时改 owner，不新增行。**
   浏览器的 push 订阅是**设备级**的，跟登录态无关。用户在 A 手机上退出、
   换 B 账号登录，如果插第二行，A 账号就会继续收到 B 的任务提醒——这是
   隐私事故，不是小 bug。所以 upsert 的冲突键是 endpoint，
   命中时把 owner_id 改成当前用户（顺带清掉失败计数）。

2. **410 / 404 不是失败，是「这个订阅死了」。**
   推送服务方明确说 Gone/Not Found（用户清了浏览器数据、卸载了 PWA、
   换了手机），再重试一万次也不会成功。这时置 `disabled=1` 直接停用，
   而不是像网络超时那样累计 fail_count 等着重试。

3. **`disabled` 而不是删行。**
   「为什么这个人收不到推送」是运营会问的问题。删掉就查不出来了。
   停用的行留着，last_err 里写着原因，`list_active` 不返回它而已。

全部是同步函数（sqlite3 是同步的）。async 端点里请 `asyncio.to_thread`。
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from core import session as store

# 已确保建表的库路径集合。键必须带路径：测试会在同一进程里反复切库，
# 只用布尔量的话第二次起就不建表了，表现为 "no such table"。
_READY: set[str] = set()

_SCHEMA = """
-- 一台设备一行。endpoint 是推送服务方给的 URL，天然唯一且很长（150~300 字符），
-- 不能拿来当主键（sqlite 主键会进所有二级索引），所以给它一个 16 位随机 sub_id。
CREATE TABLE IF NOT EXISTS push_subscriptions (
    sub_id     TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    p256dh     TEXT NOT NULL,   -- 浏览器给的公钥，加密消息体用
    auth       TEXT NOT NULL,   -- 16 字节认证密钥
    ua         TEXT NOT NULL DEFAULT '',
    platform   TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    seen_at    INTEGER,          -- 最近一次确认订阅还活着（重新 subscribe 时更新）
    last_ok_at INTEGER,
    last_err   TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0,
    disabled   INTEGER NOT NULL DEFAULT 0   -- 1 = 推送服务方说这条订阅没了
);
-- ★ 冲突键就是它：同一台设备换账号时走 upsert 改 owner，不新增行（见模块注释 1）
CREATE UNIQUE INDEX IF NOT EXISTS uq_push_endpoint ON push_subscriptions(endpoint);
CREATE INDEX IF NOT EXISTS idx_push_owner ON push_subscriptions(owner_id, disabled);

-- 每人每天到底发过没有。
-- 为什么不靠 worker 的 job_runs 判重：那个库是 data/scheduler.db，
-- 这张表在 data/sessions.db —— 跨库查表在 sqlite 里要 ATTACH，且两个库的
-- 生命周期不同（job_runs 会在 housekeeping 里被清理）。而「今天有没有打扰过他」
-- 是产品承诺，不该依赖另一张会被清理的表。
-- 主键 (owner_id, day)：同一个人同一天只可能有一行，重跑自然写不进去。
CREATE TABLE IF NOT EXISTS push_log (
    owner_id   TEXT NOT NULL,
    day        TEXT NOT NULL,
    sub_count  INTEGER NOT NULL DEFAULT 0,
    sent_at    INTEGER NOT NULL,
    PRIMARY KEY (owner_id, day)
);
"""


def _db_path() -> str:
    return store._db_path()  # noqa: SLF001


def _conn():
    return store._get_conn()  # noqa: SLF001


def ensure() -> None:
    """建表（幂等、惰性）。"""
    path = _db_path()
    if path in _READY:
        return
    with _conn() as conn:
        conn.executescript(_SCHEMA)
    _READY.add(path)


def _new_id() -> str:
    return secrets.token_hex(8)


def upsert(
    owner_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    ua: str = "",
    platform: str = "",
) -> dict[str, Any]:
    """登记一个订阅。已存在（同 endpoint）则改 owner 并复活。

    返回值里的 `reassigned` 表示这行原本属于别人——发通知时值得记一笔日志，
    因为「同一台设备换了账号」是排查「他为什么收到了别人的提醒」的第一现场。
    """
    ensure()
    now = int(time.time())
    with _conn() as conn:
        row = conn.execute(
            "SELECT sub_id, owner_id FROM push_subscriptions WHERE endpoint=?",
            (endpoint,),
        ).fetchone()
        if row:
            sub_id, prev_owner = row[0], row[1]
            conn.execute(
                """UPDATE push_subscriptions
                      SET owner_id=?, p256dh=?, auth=?, ua=?, platform=?,
                          seen_at=?, disabled=0, fail_count=0, last_err=NULL
                    WHERE sub_id=?""",
                (owner_id, p256dh, auth, ua, platform, now, sub_id),
            )
            return {"sub_id": sub_id, "created": False, "reassigned": prev_owner != owner_id}
        sub_id = _new_id()
        conn.execute(
            """INSERT INTO push_subscriptions
                  (sub_id, owner_id, endpoint, p256dh, auth, ua, platform,
                   created_at, seen_at, fail_count, disabled)
               VALUES (?,?,?,?,?,?,?,?,?,0,0)""",
            (sub_id, owner_id, endpoint, p256dh, auth, ua, platform, now, now),
        )
        return {"sub_id": sub_id, "created": True, "reassigned": False}


def remove(owner_id: str, endpoint: str) -> bool:
    """退订。**只删自己名下的那行**：传别人的 endpoint 不该能删掉别人的订阅。"""
    ensure()
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM push_subscriptions WHERE owner_id=? AND endpoint=?",
            (owner_id, endpoint),
        )
        return cur.rowcount > 0


def list_active(owner_id: str) -> list[dict[str, Any]]:
    """某人所有还能用的订阅（发通知用）。"""
    ensure()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT sub_id, endpoint, p256dh, auth, platform
                 FROM push_subscriptions
                WHERE owner_id=? AND disabled=0
                ORDER BY created_at""",
            (owner_id,),
        ).fetchall()
    return [
        {"sub_id": r[0], "endpoint": r[1], "p256dh": r[2], "auth": r[3], "platform": r[4]}
        for r in rows
    ]


def list_owners_with_active() -> list[str]:
    """所有还有活订阅的用户 id（定时任务枚举对象用）。

    DISTINCT 在 SQL 里做而不是拉回 Python 去重：一个人的三台设备会命中三行，
    推送发送侧只按人去发，多拿两行纯属浪费。
    """
    ensure()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT owner_id FROM push_subscriptions WHERE disabled=0"
        ).fetchall()
    return [r[0] for r in rows]


def count_active(owner_id: str) -> int:
    ensure()
    with _conn() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM push_subscriptions WHERE owner_id=? AND disabled=0",
                (owner_id,),
            ).fetchone()[0]
        )


def endpoint_key(endpoint: str) -> str:
    """endpoint 的短指纹（12 位 hex），给前端判断「这条是不是我这台设备」。

    为什么不直接把 endpoint 下发：endpoint 是推送服务方的投递地址，配合我们的
    VAPID 公钥就能往这台设备推任意内容（伪造通知）。设置页只需要「认得出自己」，
    sha256 前 12 位足够区分、且不可逆。碰撞概率对「一个人的几台设备」量级可忽略。
    """
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]


def list_for_owner(owner_id: str) -> list[dict[str, Any]]:
    """某人的全部订阅（含已停用），供设置页展示「哪几台设备」。

    ★ 刻意不返回 endpoint / p256dh / auth —— 设置页只要认设备、看状态、能移除。
    停用的行也要给：用户问「我手机怎么突然不响了」时，界面上得看得见「已失效」，
    而不是从列表里凭空消失。
    """
    ensure()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT sub_id, endpoint, ua, platform, created_at, seen_at,
                      last_ok_at, last_err, fail_count, disabled
                 FROM push_subscriptions
                WHERE owner_id=?
                ORDER BY created_at""",
            (owner_id,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "ep_key": endpoint_key(r[1]),
            "ua": r[2],
            "platform": r[3],
            "created_at": r[4],
            "seen_at": r[5],
            "last_ok_at": r[6],
            "last_err": r[7],
            "fail_count": r[8],
            "disabled": bool(r[9]),
        }
        for r in rows
    ]


def remove_by_id(owner_id: str, sub_id: str) -> bool:
    """按 sub_id 移除某台设备。**必须是自己名下的**，猜中别人的 id 也删不掉。"""
    ensure()
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM push_subscriptions WHERE owner_id=? AND sub_id=?",
            (owner_id, sub_id),
        )
        return cur.rowcount > 0


def mark_ok(sub_id: str) -> None:
    ensure()
    with _conn() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET last_ok_at=?, last_err=NULL, fail_count=0 WHERE sub_id=?",
            (int(time.time()), sub_id),
        )


def mark_fail(sub_id: str, error: str, gone: bool = False) -> None:
    """记一次发送失败。`gone=True` 表示推送服务方明确说订阅不存在（410/404）。"""
    ensure()
    with _conn() as conn:
        if gone:
            conn.execute(
                "UPDATE push_subscriptions SET disabled=1, last_err=? WHERE sub_id=?",
                (error[:300], sub_id),
            )
        else:
            conn.execute(
                """UPDATE push_subscriptions
                      SET fail_count=fail_count+1, last_err=?
                    WHERE sub_id=?""",
                (error[:300], sub_id),
            )


def sent_today(owner_id: str, day: str) -> bool:
    """这个人今天已经被提醒过了吗。

    这是「每人每天最多一条」的真正闸门。worker 的 job_runs 幂等键是
    (job_key, subject_id, planned_at)，而 planned_at 是触发的那一刻——
    手工补跑一次就是另一个 planned_at，键不同、照样会发。
    所以这条产品承诺必须自己兜，不能指望调度层。
    """
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM push_log WHERE owner_id=? AND day=?", (owner_id, day)
        ).fetchone()
    return row is not None


def mark_sent(owner_id: str, day: str, sub_count: int) -> None:
    """记一笔「今天发过了」。主键冲突就当已经记过，不覆盖（不改第一次的时间）。"""
    ensure()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO push_log (owner_id, day, sub_count, sent_at)
               VALUES (?,?,?,?)
               ON CONFLICT(owner_id, day) DO NOTHING""",
            (owner_id, day, int(sub_count), int(time.time())),
        )


def stats() -> dict[str, int]:
    """给 /api/schedule/health 之类的地方看的两行数。"""
    ensure()
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE disabled=0"
        ).fetchone()[0]
        owners = conn.execute(
            "SELECT COUNT(DISTINCT owner_id) FROM push_subscriptions WHERE disabled=0"
        ).fetchone()[0]
    return {"total": int(total), "active": int(active), "owners": int(owners)}
