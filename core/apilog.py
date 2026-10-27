"""API 调用日志采集（开发者数据看板的数据源）。

口径见 `docs/数据看板_2026-10-27/口径与数据字典.md` §3/§5 —— 代码与文档必须同步改，
任何在本模块里改了采集范围/字段的改动，都要回头改那一节。

为什么不在请求里同步写库
------------------------
2 核 2G 单机 + SQLite：每个请求一次 INSERT，等于每条请求抢一次写锁。
压测里 `/api/auth/me` 能到 1000+ RPS，同步写库会把这把锁变成吞吐上限（掉一个数量级）。
所以采两段式：

  ① 中间件只往进程内 deque 塞一条元组（微秒级、零 IO、不碰数据库）
  ② 到阈值（50 条）或超时（3 秒）再 `executemany` 批量落库

多 worker 的口径
----------------
每个 worker 各攒各的，flush 时各自批量写。SQLite 是 WAL + `busy_timeout=5000`，
能承受这种低频批量写；所有行最终都在**同一张表**里，所以「合计」是对的。
进程被 `kill -9` / 断电时，deque 里未落库的最多 50 条会丢 —— 这是**统计**不是**账本**，
丢几十条不影响"哪个接口调用最多"这类判断。真正的账（配额用量）在 `quota_usage` 表，与这里无关。

路径为什么要归一化
------------------
真实路径 `/api/sessions/9f3c...` 每次不同，直接 GROUP BY 会得到上千个"只调用过 1 次"的接口，
排行彻底失去意义（高基数）。所以优先取 Starlette 匹配到的**路由模板**
（`request.scope["route"].path` → `/api/sessions/{sid}`）；
只有在拿不到模板时（404 之类没匹配到路由）才退化成按段正则归一化。
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

# ==================== 采集范围（改这里必须同步改文档 §3.2）====================

# 只记业务接口。`/health` 被探活和监控每 15 秒打一次，一天 5760 条纯噪声；
# 静态资源与 Next 的前端路由更不是业务调用。
PREFIX = "/api/"

# 前缀匹配即排除（不是精确匹配：`/api/admin/apis` 要能被 `/api/admin/` 排掉）
EXCLUDE = (
    "/api/metrics",  # Prometheus 采集器按秒抓，会把排行全部占满
    "/api/admin/",  # 看板自己的请求 —— 不排除会形成观测者效应（打开看板就推高数字）
)

# 批与超时：50 条 / 3 秒。选这两个数的依据：
#   - 50 条：1000 RPS 下每 50ms 写一次批量，写锁占用不到 1%
#   - 3 秒：低流量时（比如一天只有 20 次调用）不会攒到 50 条，靠超时兜底，
#     保证"准实时"的 3 秒上界（文档 §5 承诺的就是这个数）
BATCH = 50
INTERVAL = 3.0

# 保留天数。超过 90 天没人看，而且会让备份变慢（文档 §8）
KEEP_DAYS = 90

_BUF: deque[tuple] = deque(maxlen=1000)  # 上限兜底：flush 连续失败时不无限涨内存
_LOCK = threading.Lock()
_LAST_FLUSH = time.monotonic()
_READY: set[str] = set()  # 已建过表的库路径（测试会换路径，所以按路径记）
_LAST_PURGE_DAY = ""  # 上次清理是哪一天（每天最多清一次）


# ==================== 建表（惰性）====================


def ensure() -> None:
    """确保表与索引存在（幂等、惰性）。

    为什么惰性而不是模块级建表：测试会在 import 之后把 `session._db_path`
    patch 到临时目录。模块级建表会把表建到**真实库**上，测试就跑脏了生产数据。
    这里按"库路径"记已初始化集合，路径一变自然重建一次。
    """
    from core import session as store

    path = store._db_path()  # noqa: SLF001 —— 同项目内既定用法（account.py 同）
    if path in _READY:
        return
    with store._get_conn() as conn:  # noqa: SLF001
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_call_logs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                path    TEXT NOT NULL,
                method  TEXT NOT NULL,
                status  INTEGER NOT NULL,
                dur_ms  INTEGER NOT NULL,
                uid     TEXT
            );
            -- 三个索引对应看板的三类查询：时间范围 / 按接口排行 / 成功失败统计。
            -- 600k 行下无索引是秒级全表扫，有索引是毫秒级（文档 §8）
            CREATE INDEX IF NOT EXISTS idx_apilog_ts ON api_call_logs(ts);
            CREATE INDEX IF NOT EXISTS idx_apilog_path_ts ON api_call_logs(path, ts);
            CREATE INDEX IF NOT EXISTS idx_apilog_status_ts ON api_call_logs(status, ts);
        """)
    _READY.add(path)


# ==================== 路径归一化 ====================

# 兜底用的正则：拿不到路由模板时（404 / 未匹配），把"看起来像 id"的段换掉。
# 判据刻意保守 —— 宁可漏归一化（多几个条目），也不要把 `me`、`config` 这类
# 固定路径段误伤成 `{id}`（那会让所有接口并成一条，比不归一化更糟）。
_RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_RE_HEX = re.compile(r"^[0-9a-f]{8,}$", re.I)  # uuid4().hex 是 32 位 hex
_RE_NUM = re.compile(r"^\d+$")


def normalize_path(raw: str) -> str:
    """把真实路径归一化成模板形态（拿不到 route 时的兜底）。"""
    if not raw:
        return "/"
    parts = raw.split("/")
    for i, seg in enumerate(parts):
        if not seg:
            continue
        if _RE_UUID.match(seg) or _RE_NUM.match(seg) or (len(seg) >= 8 and _RE_HEX.match(seg)):
            parts[i] = "{id}"
    return "/".join(parts)


def should_record(path: str) -> bool:
    """这个路径要不要记（文档 §3.1/§3.2 的唯一实现）。"""
    if not path.startswith(PREFIX):
        return False
    return not any(path.startswith(x) for x in EXCLUDE)


# ==================== 写入缓冲 ====================


def record(
    method: str,
    path: str,
    status: int,
    dur_ms: int,
    uid: str | None = None,
) -> bool:
    """塞一条进缓冲。返回 True 表示「现在该 flush 了」，由调用方负责调度。

    为什么把调度交给调用方：本模块要能在**同步**测试里调用（没有事件循环），
    而生产路径在 async 中间件里、希望 `create_task(to_thread(...))` 不阻塞响应。
    把 asyncio 相关的东西留在中间件里，模块本身保持纯同步、零依赖。
    """
    if not should_record(path):
        return False
    row = (int(time.time()), path[:200], method[:10], int(status), max(0, int(dur_ms)), uid or None)
    _BUF.append(row)
    if len(_BUF) >= BATCH:
        return True
    return (time.monotonic() - _LAST_FLUSH) >= INTERVAL


def flush() -> int:
    """把缓冲批量落库，返回写入行数。任何异常都吞掉——统计不该影响业务。"""
    global _LAST_FLUSH
    _LAST_FLUSH = time.monotonic()
    if not _BUF:
        return 0
    with _LOCK:
        if not _BUF:
            return 0
        # 先整批取出再清空：即便下面写库失败，也只丢这一批（不会留在 deque 里反复重试、
        # 越攒越多把内存吃掉）。丢一批统计是可接受的，卡住主流程不是。
        rows = []
        while _BUF:
            try:
                rows.append(_BUF.popleft())
            except IndexError:  # 另一个线程刚好取空
                break
    if not rows:
        return 0
    try:
        ensure()
        from core import session as store

        with store._get_conn() as conn:  # noqa: SLF001
            conn.executemany(
                "INSERT INTO api_call_logs (ts, path, method, status, dur_ms, uid) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        _maybe_purge()
        return len(rows)
    except Exception:  # noqa: BLE001 —— 采集失败绝不能让请求 500
        return 0


def pending() -> int:
    """缓冲里还有多少条（排障 / 测试用）。"""
    return len(_BUF)


# ==================== 清理 ====================


def _maybe_purge() -> None:
    """每天最多清理一次（文档 §8 承诺"保留 90 天"）。

    为什么挂在 flush 里而不是 worker 的定时任务：调度体系要注册 job、写 job 表、
    配 cron，为了"删一批老日志"去改那套东西风险大于收益。这里只多一次字符串比较，
    且多 worker 下每个进程各清一次也无害（DELETE 是幂等的）。

    **失败必须吞掉**：清理是维护动作，绝不能因为它让 flush 报错、进而丢当前的统计。
    """
    global _LAST_PURGE_DAY
    try:
        from core import timeutil

        day = timeutil.day_str()
        if day == _LAST_PURGE_DAY:
            return
        _LAST_PURGE_DAY = day
        purge()
    except Exception:  # noqa: BLE001
        pass


def purge(keep_days: int = KEEP_DAYS) -> int:
    """删掉 `keep_days` 天之前的日志，返回删除行数。由 worker 每日调一次。

    没有这个清理，表会无限长：日增 2 万行的话一年 730 万行，
    备份变慢、COUNT 变慢，而 90 天前的调用记录没有任何人看。
    """
    ensure()
    from core import session as store

    cutoff = int(time.time()) - keep_days * 86400
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute("DELETE FROM api_call_logs WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0
