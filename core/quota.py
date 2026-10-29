from __future__ import annotations

"""配额与限流：别让别人用你的界面烧你的额度。

两件事，尺度不同所以实现也不同：

1. **每分钟请求数**（进程内滑窗 + 落库兜底）
   挡的是脚本式刷接口与手滑连点。单进程 uvicorn 下进程内滑窗就够了，
   落库反而让"限流"自己变成写入热点。

   ⚠️ 多 worker 的现实问题（2026-10-20 审计 A3）：
   `uvicorn --workers 4` 会 fork 出 4 个进程，各自持有一份滑窗，
   `RATE_PER_MINUTE=10` 的实际放行量变成 **40/min**——而且不报错、日志干净。
   而 `--workers 4` 恰恰是压测报告给出的 P0 建议，也就是说**不修这个问题，
   第一个 P0 优化就会静默拆掉一道护栏**。

   解法是"本地判 + 全局判，取更严的那道"：
     * 本地：进程内滑窗满了直接拒（单进程下这一条就是全部逻辑，行为与改造前一致）；
     * 全局：再查一次跨进程的窗口计数，达到上限也拒。**这次读带 200ms 进程内缓存**，
       所以每进程每秒最多 5 次索引求和，不是每请求一次；
     * 记账：按**秒桶**把本进程的放行数汇总落库，每进程每用户最多 1 次写/秒。

   为什么全局判不能"只在本地满了才查"：本地满意味着自己已经贡献了 N 个，
   全局数必然 ≥ N，于是那条路径只会拒、永远不会放宽——看起来修了，其实只是
   把限流收紧到"谁先填满谁先被拒"，多 worker 总量照样能到 N×worker
   （每个进程各自填满自己的窗口）。真正的收敛必须每次请求都看全局。

   读取失败时**退回单层**（本地未满就放行）：这次查询的作用是加严，
   读不到就不加严——限流器不该自己变成故障源。挡钱的是每日用量，那张表不依赖它。

   这是**护栏不是闸门**：秒桶写有 1s 的批处理延迟，多 worker 下实际放行量收敛到
   约 N+ε 而不是严格 N。想要严格 N 需要 Redis 或每请求一次 DB 原子自增，
   两者都比这个方案贵得多。如实标注这一点，比假装它是精确限流更有用。

2. **每日用量**（落 SQLite）
   挡的是账单事故：一次建库几千个分片，embedding 是按 token 计费的。
   必须落库，否则重启一次就清零，等于没有限额。

调用注意：本文件的函数全是同步的（SQLite 写约 2ms），
在 async 端点里一律用 `asyncio.to_thread(...)` 包一层——这是本项目
既定规则，别因为它只有 2ms 就裸调。
"""

import threading
import time

from core import config, metrics, timeutil
from core.session import _get_conn

_lock = threading.Lock()

# uid -> 最近一分钟内的请求时间戳（秒）。只留窗口内的数据，不会无限涨
_HITS: dict[str, list[float]] = {}

_WINDOW = 60.0

# ---------- 落库记账（多 worker 兜底用）----------
# 按「秒桶」汇总，而不是每次请求插一行：
#   每次插一行 → 限流自己变成写入热点（这正是当初选择不落库的理由）；
#   每进程每用户最多 1 写/秒 → 100 个活跃用户也只有 100 写/秒，SQLite 承受得住。
_BUCKET_TTL = 120  # 秒桶保留时长（窗口 60s 的两倍，留出时钟抖动的余量）

# 进程内待冲刷的放行数：uid -> [秒桶, 计数]
_pending: dict[str, list[int]] = {}
_FLUSH_MIN_INTERVAL = 1.0  # 同一用户两次冲刷的最小间隔（秒）
_last_flush: dict[str, float] = {}
_flush_ops = 0  # 顺便用来决定什么时候清理过期桶

# 全局计数缓存：uid -> (monotonic 时刻, DB 侧窗口内计数)
# TTL 200ms：让"每次请求查一次全局"不至于变成 SQLite 读热点，
# 同时把滞后控制在 60s 窗口的 1/300 以内。
_SHARED_CACHE: dict[str, tuple[float, int]] = {}
_SHARED_TTL = 0.2


class RateLimited(Exception):
    """超过每分钟上限。"""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"操作过于频繁，请 {retry_after:.0f} 秒后再试")
        self.retry_after = retry_after


class QuotaExceeded(Exception):
    """超过每日上限。"""

    def __init__(self, kind: str, limit: int) -> None:
        label = {"ask": "每日问答", "embed": "每日向量化分片", "auto": "每日自动任务"}.get(
            kind, kind
        )
        super().__init__(f"{label}已达上限（{limit}），明天再试或联系管理员调整配额")
        self.kind = kind
        self.limit = limit


# ---------- 每分钟：进程内滑窗（+ 多 worker 兜底）----------


def _ensure_rate_table() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rate_buckets (
                user_id TEXT NOT NULL,
                sec     INTEGER NOT NULL,
                n       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, sec)
            );
            CREATE INDEX IF NOT EXISTS idx_rate_sec ON rate_buckets(sec);
        """)


_ensure_rate_table()


def _shared_count(uid: str, now_wall: int) -> int:
    """全局限额窗口内已放行数（跨进程）。返回 -1 表示读不到。

    两个代价控制：
      * **200ms 进程内缓存**：不走缓存的话每次请求一次索引求和；限流是热路径，
        不能为了正确性把它变成 SQLite 的读热点。200ms 的滞后在 60s 窗口里
        最多让放行量多出"20 分之一个窗口"的量级，可以忽略；
      * **把本进程的待冲刷量加上去**：DB 里的数是滞后的（每用户每秒最多写一次），
        只读 DB 会低估自己刚放行的那些，导致自己这一路放行过量。

    注意缓存只缓存 **DB 部分**，`_pending` 每次现加——否则自己连续请求时
    会一直看到同一个偏小的值。
    """
    cut = now_wall - int(_WINDOW)
    now_mono = time.monotonic()
    with _lock:
        cached = _SHARED_CACHE.get(uid)
    if cached is not None and now_mono - cached[0] < _SHARED_TTL:
        db_part = cached[1]
    else:
        try:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(n), 0) AS s FROM rate_buckets "
                    "WHERE user_id = ? AND sec > ?",
                    (uid, cut),
                ).fetchone()
                db_part = int(row["s"]) if row else 0
        except Exception:
            return -1
        with _lock:
            _SHARED_CACHE[uid] = (now_mono, db_part)
    with _lock:
        pend = _pending.get(uid)
        if pend and pend[0] > cut:
            db_part += int(pend[1])
    return db_part


def _flush_locked(uid: str, now_wall: int) -> int | None:
    """把待冲刷量落库。返回 (秒桶, 计数) 供调用方在锁外执行，None 表示不需要冲刷。

    **必须在持锁时调用**，因为它读写 `_pending` / `_last_flush`。
    真正的 SQL 由调用方在锁外执行——持锁做 IO 会让所有请求排队等 SQLite。
    """
    pend = _pending.get(uid)
    if not pend or pend[1] <= 0:
        return None
    last = _last_flush.get(uid, 0.0)
    if now_wall == last:
        return None  # 同一秒内不重复冲刷
    sec = pend[0]
    n = int(pend[1])
    _pending[uid] = [now_wall, 0]
    _last_flush[uid] = float(now_wall)
    return sec, n


def _write_bucket(uid: str, sec: int, n: int) -> None:
    """把 (秒桶, 计数) 累加落库。失败静默——限流记账不该影响业务请求。"""
    global _flush_ops
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO rate_buckets (user_id, sec, n) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, sec) DO UPDATE SET n = n + excluded.n",
                (uid, sec, n),
            )
        _flush_ops += 1
        # 把刚写进去的量补进缓存：否则"自己刚放行的那些"在缓存过期前（200ms）
        # 会被算漏，短暂地多放行几个。补 n 而不是清缓存，是因为缓存里那份
        # DB 数是"写入之前"的快照，加上 n 正好是最新值，还省掉一次查询。
        with _lock:
            cached = _SHARED_CACHE.get(uid)
            if cached is not None:
                _SHARED_CACHE[uid] = (cached[0], cached[1] + n)
        if _flush_ops % 60 == 0:
            with _get_conn() as conn:
                conn.execute("DELETE FROM rate_buckets WHERE sec < ?", (sec - _BUCKET_TTL,))
    except Exception:
        pass


def hit(uid: str, limit: int | None = None) -> int:
    """记一次请求，超限抛 RateLimited，否则返回本窗口内已用次数。

    limit=0 或 None 表示不限（沿用 config.RATE_PER_MINUTE）。
    管理员（config.ADMIN_USERNAMES）直接放行，不计数。

    判定顺序（两道，取更严的那道）：
      1. **本进程窗口**满了 → 拒。单进程下这一条就是全部逻辑；
      2. 开了 RATE_LIMIT_SHARED 时再看**全局**计数，达到上限 → 拒。
         这条才让多 worker 的放行量收敛到 N 而不是 N×worker。

    ⚠️ 第 2 条必须**每次请求都查**（带 200ms 缓存）。一个想当然的写法是
    "只在本地窗口满了才查全局"——那样全局数必然已经 ≥ N（自己就贡献了 N 个），
    于是确认路径永远只会拒绝，等于没修；而如果反过来只在本地满了才放宽，
    总放行量会变成 N×worker，因为每个进程都会各自填满自己的窗口。
    """
    if _is_admin(uid):
        return 0
    n = config.RATE_PER_MINUTE if limit is None else limit
    now = time.monotonic()
    now_wall = int(time.time())
    allowed = 0

    with _lock:
        stamps = _HITS.setdefault(uid, [])
        cut = now - _WINDOW
        # 就地截断而不是重新赋值，_HITS 里存的始终是同一个列表对象
        stamps[:] = [s for s in stamps if s > cut]
        local_full = bool(n) and len(stamps) >= n

    if local_full:
        metrics.inc("quota_rejected", {"kind": "rate"})
        raise RateLimited(_WINDOW - (now - stamps[0]))

    if n and getattr(config, "RATE_LIMIT_SHARED", True):
        global_count = _shared_count(uid, now_wall)
        if global_count < 0:
            # 账本读不到 → 退回单层（本地未满就放行，与改造前一致）。
            # 方向：全局查询的作用是**加严**，读不到就不加严，绝不让限流器
            # 自己变成故障源。挡钱的是每日用量，那张表不依赖这里。
            metrics.inc("quota_rejected", {"kind": "rate_shared_unavailable"})
        elif global_count >= n:
            metrics.inc("quota_rejected", {"kind": "rate"})
            raise RateLimited(_WINDOW)

    with _lock:
        stamps = _HITS.setdefault(uid, [])
        stamps.append(now)
        allowed = len(stamps)
        pend = _pending.get(uid)
        if pend is None or pend[0] != now_wall:
            _pending[uid] = [now_wall, 0]
        _pending[uid][1] += 1
        flush = _flush_locked(uid, now_wall)

    # 锁外做 IO：持锁写 SQLite 会让所有请求排队
    if flush:
        _write_bucket(uid, flush[0], flush[1])
    return allowed


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
    """当天日期键，形如 2026-10-06（**业务时区**，默认 Asia/Shanghai）。

    这里用业务时区而非 UTC：限额是给人看的，按业务当地日子解释才讲得清。

    ⚠️ 2026-10-20 审计 A2：这里原本是 `time.localtime()`，即**服务器本地时区**。
    服务器 TZ 恰好是上海时两者一致，所以问题一直没暴露；换成 TZ=UTC 的机器
    （容器镜像的默认值）后，北京时间 07:00 的请求会落到前一天的键上 →
    每日配额在北京时间 08:00 才重置，用户看到的是"我明明过零点了怎么还没恢复"。
    现在统一走 core/timeutil.py，与调度器用的是同一个时区常量。
    """
    return timeutil.day_str(ts)


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
# ⚠️ 这张表就是「有哪几种配额」的唯一声明：snapshot() 按它遍历、
# history() 按它建列。新增一种配额只改这里 + config 一行，别再往别处硬编码 kind。
_LIMITS = {
    "ask": "DAILY_ASK_LIMIT",
    "embed": "DAILY_EMBED_LIMIT",
    # 定时任务（委托投递 / 每日总结）烧的 token 原本不在任何配额里：
    # 那条链路无人值守，必须单独卡一道，否则写错就是静默烧钱。
    "auto": "DAILY_AUTO_LIMIT",
}


def _is_admin(uid: str) -> bool:
    """管理员（config.ADMIN_UIDS / ADMIN_USERNAMES）不受配额与每分钟限流约束。

    判定顺序（2026-10-29 安全加固）：
      1. **ADMIN_UIDS**（首选）——uid 是注册时生成的 uuid，不可猜、不可抢注；
      2. ADMIN_USERNAMES（旧配置兼容）——按 uid 反查用户名再比对。

    为什么不再只认用户名：用户名是用户可选的，默认管理员名若写在公开仓库里，
    账号尚未创建时第一个抢注的人就会拿到管理员。uid 没有这个问题。
    """
    if not uid:
        return False
    uids = getattr(config, "ADMIN_UIDS", None) or ()
    if uid in uids:
        return True
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
        metrics.inc("quota_rejected", {"kind": kind})
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
        metrics.inc("quota_rejected", {"kind": kind})
        raise QuotaExceeded(kind, limit)
    return bump_usage(uid, kind, n)


def history(uid: str, days: int = 7) -> list[dict]:
    """近 N 天逐日用量（缺失日零填充），给前端画趋势条。

    管理员不记账，这里自然返回全 0——语义上「管理员没额度」也说得通。
    """
    days = max(1, min(int(days), 31))
    # 用 recent_days 生成日期串而不是"现在减 86400×i"：
    # 后者在跨夏令时的时区会跳过/重复一天，且口径与 day_key 不一定一致。
    keys = timeutil.recent_days(days)
    since = keys[0]
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT day, kind, count FROM usage WHERE user_id = ? AND day >= ?",
            (uid, since),
        ).fetchall()
    out: list[dict[str, int]] = []
    for d in keys:
        # 零填充的列由 _LIMITS 派生：新增一种配额不必回来补这里，
        # 否则记账了却在趋势图里当 undefined（画不出线）。
        out.append({"day": d, **{k: 0 for k in _LIMITS}})
    by_day = {row["day"]: row for row in out}
    for r in rows:
        slot = by_day.get(r["day"])
        if slot is not None and r["kind"] in _LIMITS:
            slot[r["kind"]] = int(r["count"])
    return out


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
