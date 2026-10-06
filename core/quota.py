# -*- coding: utf-8 -*-
"""配额与限流：别让别人用你的界面烧你的额度。

两件事，尺度不同所以实现也不同：

1. **每分钟请求数**（进程内滑动窗口）
   挡的是脚本式刷接口与手滑连点。放内存里就够了——单进程 uvicorn 下
   没有人可以绕过它，落库反而让"限流"自己变成写入热点。
   多副本部署时换成 Redis，函数签名不用变。

2. **每日用量**（落 SQLite）
   挡的是账单事故：一次建库几千个分片，embedding 是按 token 计费的。
   必须落库，否则重启一次就清零，等于没有限额。

调用注意：本文件的函数全是同步的（SQLite 写约 2ms），
在 async 端点里一律用 `asyncio.to_thread(...)` 包一层——这是本项目
既定规则，别因为它只有 2ms 就裸调。
"""

import threading
import time

from core import config
from core.session import _get_conn

_lock = threading.Lock()

# uid -> 最近一分钟内的请求时间戳（秒）。只留窗口内的数据，不会无限涨
_HITS: dict[str, list[float]] = {}

_WINDOW = 60.0


class RateLimited(Exception):
    """超过每分钟上限。"""

    def __init__(self, retry_after: float):
        super().__init__(f"操作过于频繁，请 {retry_after:.0f} 秒后再试")
        self.retry_after = retry_after


class QuotaExceeded(Exception):
    """超过每日上限。"""

    def __init__(self, kind: str, limit: int):
        label = {"ask": "每日问答", "embed": "每日向量化分片"}.get(kind, kind)
        super().__init__(f"{label}已达上限（{limit}），明天再试或联系管理员调整配额")
        self.kind = kind
        self.limit = limit


# ---------- 每分钟：进程内滑动窗口 ----------

def hit(uid: str, limit: int | None = None) -> int:
    """记一次请求，超限抛 RateLimited，否则返回本窗口内已用次数。

    limit=0 或 None 表示不限（沿用 config.RATE_PER_MINUTE）。
    管理员（config.ADMIN_USERNAMES）直接放行，不计数。
    """
    if _is_admin(uid):
        return 0
    n = config.RATE_PER_MINUTE if limit is None else limit
    now = time.monotonic()
    with _lock:
        stamps = _HITS.setdefault(uid, [])
        cut = now - _WINDOW
        # 就地截断而不是重新赋值，_HITS 里存的始终是同一个列表对象
        stamps[:] = [s for s in stamps if s > cut]
        if n and len(stamps) >= n:
            raise RateLimited(_WINDOW - (now - stamps[0]))
        stamps.append(now)
        return len(stamps)


# ---------- 每日：落库计数 ----------

def _ensure_tables() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usage (
                user_id  TEXT NOT NULL,
                day      TEXT NOT NULL,
                kind     TEXT NOT NULL,
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day, kind)
            );
        """)


_ensure_tables()


def day_key(ts: int | None = None) -> str:
    """当天日期键，形如 2026-10-06（本地时区）。

    这里用本地时间而非 UTC：限额是给人看的，按服务器当地日子解释最好讲清楚。
    """
    return time.strftime("%Y-%m-%d", time.localtime(ts if ts is not None else time.time()))


def usage_today(uid: str, kind: str) -> int:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM usage WHERE user_id = ? AND day = ? AND kind = ?",
            (uid, day_key(), kind),
        ).fetchone()
        return int(row["count"]) if row else 0


def bump_usage(uid: str, kind: str, n: int = 1) -> int:
    """累加当日用量并返回累加后的值。原子 upsert，不做"读-改-写"。"""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO usage (user_id, day, kind, count) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, day, kind) DO UPDATE SET count = count + excluded.count",
            (uid, day_key(), kind, n),
        )
        row = conn.execute(
            "SELECT count FROM usage WHERE user_id = ? AND day = ? AND kind = ?",
            (uid, day_key(), kind),
        ).fetchone()
        return int(row["count"])


# 每日上限表：kind -> config 里的限额
_LIMITS = {
    "ask": "DAILY_ASK_LIMIT",
    "embed": "DAILY_EMBED_LIMIT",
}


def _is_admin(uid: str) -> bool:
    """管理员（config.ADMIN_USERNAMES）不受配额与每分钟限流约束。

    按 uid 反查用户名再比对：名单写的是人能读懂的名字，
    而配额系统全程只拿得到 uid。每次多一次 users 表点查，
    频率极低（仅建库/问答这类用户主动操作），可接受。
    """
    names = getattr(config, "ADMIN_USERNAMES", None)
    if not names:
        return False
    from core import account

    u = account.get_user(uid)
    return bool(u) and u["username"] in names


def _limit_of(kind: str) -> int:
    return int(getattr(config, _LIMITS.get(kind, ""), 0) or 0)


def ensure(uid: str, kind: str) -> None:
    """只检查不记账：已经触顶就抛 QuotaExceeded。管理员直接放行。

    建库这类操作在动手前算不出会切出多少片段，没法先扣；
    所以用 ensure 在入口卡一道，建完再用 bump_usage 记真实数量。
    """
    if _is_admin(uid):
        return
    limit = _limit_of(kind)
    if not limit:
        return
    if usage_today(uid, kind) >= limit:
        raise QuotaExceeded(kind, limit)


def guard(uid: str, kind: str) -> None:
    """入口处的一整套检查：每分钟限流 + 当日剩余额度。

    kind="ask" 这类能预先知道是"一次"的，直接用 consume 更准；
    kind="embed" 这种按片段计费的，用 guard 先卡、事后 bump。
    """
    hit(uid)
    ensure(uid, kind)


def consume(uid: str, kind: str, n: int = 1) -> int:
    """扣一次配额，超限抛 QuotaExceeded（不实际扣），否则返回扣后的当日用量。

    管理员直接放行（返回 0，不记账）。
    "超限时不扣"很重要：否则重试的请求会继续把计数推高，
    用户等到第二天零点还是解不开。
    """
    if _is_admin(uid):
        return 0
    limit = _limit_of(kind)
    if not limit:
        return bump_usage(uid, kind, n)
    used = usage_today(uid, kind)
    if used + n > limit:
        raise QuotaExceeded(kind, limit)
    return bump_usage(uid, kind, n)


def snapshot(uid: str) -> dict:
    """给前端看的一日用量概览（额度用掉多少 / 还剩多少）。

    管理员附 unlimited=True，前端据此显示"无限制"。
    """
    out = {}
    unlimited = _is_admin(uid)
    for kind in _LIMITS:
        limit = _limit_of(kind)
        used = usage_today(uid, kind) if limit else 0
        d = {"used": used, "limit": limit}
        if unlimited:
            d["unlimited"] = True
        out[kind] = d
    return out
