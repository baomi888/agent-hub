"""定时任务库层：幂等登记 / 重试分级 / 心跳。

设计取舍（为什么不把这些直接交给 APScheduler 的 JobStore）
--------------------------------------------------------
APScheduler 负责「什么时候跑」，本模块负责「跑没跑过、失败算谁的、worker 还活着吗」。
两者刻意分开，三个理由：

1. **可测**：本文件只依赖标准库 + SQLite，**不 import apscheduler**。
   幂等、重试、心跳三件事全部由单元测试覆盖，不需要起调度器、不需要等真实 cron 到点。
2. **共享**：web 进程要读心跳判健康、要写「立即执行」的一行；worker 进程要执行并回写状态。
   两个进程必须看到同一份事实，所以状态落 `data/scheduler.db`
   （与 sessions.db 同目录、不同文件——同文件会让聊天写入和调度写入互相抢锁）。
3. **幂等靠约束而不是靠查**：`job_runs.idem_key` 上建 UNIQUE 索引，
   登记一律 `INSERT ... ON CONFLICT(idem_key) DO NOTHING` + 判 rowcount。
   「先 SELECT 看有没有、没有再 INSERT」在并发下必然漏（web 的 run-now 与
   worker 的扫描可能同时到，两个都查到「没有」就都插），
   这与 core/quota.py 的 upsert 记账是同一套思路。

时间口径
--------
* 落库一律 Unix 秒（UTC），与 sessions / messages 表一致；
* 解释一律 `SCHEDULER_TZ`（默认 Asia/Shanghai），
  **禁止裸 `datetime.now()`**——带时区的 `datetime.now(TZ)` 是零成本的，
  而手工 +8 小时在跨时区迁移时没有任何防御力。

表结构见 `_SCHEMA`。`job_runs` 在规范给的 11 列之外多了两列，都是为了可运维：
`next_attempt_at`（下次该重试的时刻，重试扫描只认它，不靠反推）
与 `trigger`（schedule / manual —— 排查「为什么今天没发」时第一时间要看这个字段）。
"""

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import config, timeutil

# ---------- 常量 ----------

# worker 写进心跳的版本号，与 data/scheduler.db 里的行一起给运维看
WORKER_VERSION = "0.1.0"

# 时区与时间换算统一收在 core/timeutil.py（2026-10-20 审计 A2）：
# 原先这里自定义了一份 TZ + now_local + day_bounds，而 core/quota.py 与
# service/daily.py 又各自用 time.localtime()，三处两种答案。现在只有一个入口。
TZ_NAME = timeutil.TZ_NAME
TZ = timeutil.TZ
now_ts = timeutil.now_ts
now_local = timeutil.now_local
day_bounds = timeutil.day_bounds

# 心跳年龄超过它判 down（秒）。600s = 两个 dispatch 周期：
# 进程在但卡死（比如被一次 LLM 调用挂住）同样会被抓到，这就是不走进程表的原因。
HEARTBEAT_DOWN_SECONDS = 600
# 超过一个 dispatch 周期（300s）但还没到 600s：判 stalled，提前给运维一个黄灯
HEARTBEAT_STALL_SECONDS = 300

# 可重试失败最多跑几次；第 1/2/3 次重试前的等待（分钟）——指数退避 1 / 5 / 15
MAX_ATTEMPTS = 3
BACKOFF_MINUTES = (1, 5, 15)
# 配额触顶不按分钟退避（今天已经没额度了，15 分钟后再试还是超限），延后到次日同刻
QUOTA_DEFER_SECONDS = 86400

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

CLASS_RETRY = "retry"  # 可重试：超时 / 5xx / 429
CLASS_FATAL = "fatal"  # 不可重试：401 / 402、会话已删、库不存在
CLASS_QUOTA = "quota"  # 配额触顶：延后到次日同刻


def job_defs() -> dict[str, dict]:
    """任务定义表的默认值（job_key -> cron / 说明）。

    **只给默认值**：schedule_jobs 里已有的行不会被覆盖，
    所以运维改过 cron 之后重启 worker 不会被打回原样
    （「周期改这里，不改代码」是这张表存在的唯一理由）。

    report.daily 的 cron 由 REPORT_HOUR / REPORT_MINUTE 拼出来，
    默认 "30 23 * * *"（次日 23:30 北京时间）。
    """
    return {
        "delegate.dispatch": {
            "cron": "*/5 * * * *",
            "description": "扫描到点的每日委托并投递",
        },
        "delegate.retry": {
            "cron": "7,22,37,52 * * * *",
            "description": "重试失败的委托投递（错开 dispatch 的分钟，避免抢 SQLite 写锁）",
        },
        "report.daily": {
            "cron": f"{config.REPORT_MINUTE} {config.REPORT_HOUR} * * *",
            "description": "生成当日每日总结",
        },
        "report.backfill": {
            "cron": "0 4 * * *",
            "description": "补生成过去 7 天内 failed / empty 的总结",
        },
        "housekeeping": {
            "cron": "15 4 * * *",
            "description": "清理过期运行记录（>90 天）与历史上传件",
        },
        "profile.aggregate": {
            "cron": "30 2 * * *",
            "description": "聚合用户兴趣画像（基于 user_tags 权重，生成每日快照）",
        },
        # Prompt 自进化（GEPA · issue #12）。默认每日 4 次（方案 §待讨论的口径），
        # 稳定后可用 PROMPT_EVOLVE_CRON 提频——周期改表或改环境变量，不改代码。
        # 只产出 shadow 候选，上线仍要人工 promote（PROMPT_EVO_AUTO_PROMOTE 默认关）。
        "prompt.evolve": {
            "cron": os.getenv("PROMPT_EVOLVE_CRON", "20 */6 * * *"),
            "description": "Prompt 自进化一轮（采集→变异→评估→择优，产出 shadow 候选）",
        },
    }


JOB_DEFS = job_defs()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_jobs (
    job_key     TEXT PRIMARY KEY,
    cron        TEXT NOT NULL,
    tz          TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    enabled     INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    next_run_at INTEGER
);

CREATE TABLE IF NOT EXISTS job_runs (
    run_id          TEXT PRIMARY KEY,
    job_key         TEXT NOT NULL,
    subject_id      TEXT NOT NULL,          -- delegation_id 或 date
    planned_at      INTEGER NOT NULL,       -- 计划触发时刻（Unix 秒）
    idem_key        TEXT NOT NULL,          -- job_key|subject_id|planned_at
    status          TEXT NOT NULL,          -- pending/running/succeeded/failed/skipped
    attempts        INTEGER NOT NULL DEFAULT 0,
    started_at      INTEGER,
    finished_at     INTEGER,
    error           TEXT,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER,                -- 只对 status=failed 有意义
    trigger         TEXT NOT NULL DEFAULT 'schedule'
);
-- 幂等的唯一依据：同一 (job_key, subject_id, planned_at) 只允许一行。
-- 有它在，「重跑调度器」和「重复扫描」才不可能变成重复打扰。
CREATE UNIQUE INDEX IF NOT EXISTS ux_job_runs_idem ON job_runs(idem_key);
CREATE INDEX IF NOT EXISTS idx_job_runs_planned ON job_runs(planned_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_runs_due ON job_runs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job_key, planned_at DESC);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id      INTEGER PRIMARY KEY CHECK (id = 1),   -- 只有一行，恒等于 1
    ts      INTEGER NOT NULL,
    pid     INTEGER,
    version TEXT
);
"""


# ---------- 连接管理 ----------
# 同 core/session.py：按线程复用连接（路径变了就重建），
# 一次调度会开好几个连接的话 WAL 设置会重复付几毫秒，没必要。
_LOCAL = threading.local()
_LOCK = threading.Lock()
# 已经建过表的库文件路径。用路径而不是布尔量：测试会把路径换到临时目录，
# 换过之后必须重新建表（同 core/session.py 的 _LOCAL.path 判断）。
_READY: set[str] = set()


def _db_path() -> str:
    """调度库文件路径。

    与 worker 的 APScheduler JobStore 是同一个文件（data/scheduler.db），
    表名不冲突：APScheduler 用 apscheduler_jobs，本模块用上面三张表。
    """
    d = getattr(config, "DATA_DIR", os.path.join(config.PROJECT_ROOT, "data"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "scheduler.db")


def _conn() -> sqlite3.Connection:
    path = _db_path()
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "path", None) == path:
        return conn
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL：web 与 worker 是两个进程，读写并发下不互相阻塞
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # 调度写入可能与聊天写入撞上，等一会儿比直接报错好
    conn.execute("PRAGMA busy_timeout=5000")
    _LOCAL.conn = conn
    _LOCAL.path = path
    return conn


@contextmanager
def _get_conn():
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_tables(seed: bool = True) -> None:
    """建表 + 把任务定义表补齐（幂等）。

    刻意**不在 import 时执行**：web 进程 import 本模块（api/schedule.py 引用）
    不应该顺手写出一个数据库文件，测试也不该因为 import 就碰真实 data/。
    第一次真正用到调度库时才建。
    """
    path = _db_path()
    if path in _READY:
        return
    with _LOCK:
        if path in _READY:
            return
        with _get_conn() as conn:
            conn.executescript(_SCHEMA)
            if seed:
                conn.executemany(
                    "INSERT INTO schedule_jobs (job_key, cron, tz, enabled, description) "
                    "VALUES (?, ?, ?, 1, ?) ON CONFLICT(job_key) DO NOTHING",
                    [(k, v["cron"], TZ_NAME, v["description"]) for k, v in JOB_DEFS.items()],
                )
        _READY.add(path)


# ---------- 时间 ----------
#
# now_ts / now_local / day_bounds 三个函数已经在文件头从 core/timeutil.py 引入并
# 直接复用（`from ... import` 的别名），这里不再重复定义。
# 保留这三个公开名字是为了不动既有调用方（worker/、api/schedule.py、service/daily.py
# 都在用），同时保证"时区口径只有一份"。


def new_run_id() -> str:
    """执行记录 id（12 位 hex，与 sessions 的 sid 同一形态，短、可读、不可枚举序号）。"""
    return "run_" + uuid.uuid4().hex[:12]


def idem_key(job_key: str, subject_id: str, planned_at: int) -> str:
    """幂等键 = job_key|subject_id|planned_at。

    为什么带 planned_at：同一委托今天 07:00 和明天 07:00 是两次不同的投递，都要发；
    但「今天 07:00 这一次」无论被触发几次（worker 重启、重复扫描、手动重跑）都只能发一次。
    """
    return f"{job_key}|{subject_id}|{int(planned_at)}"


# ---------- 任务定义表 ----------


def list_jobs(enabled: bool | None = None) -> list[dict]:
    """任务定义表（可按开关过滤）。enabled 是 INTEGER 落库，这里转回 bool 给前端。"""
    ensure_tables()
    sql = "SELECT job_key, cron, tz, enabled, description, next_run_at FROM schedule_jobs"
    params: list = []
    if enabled is not None:
        sql += " WHERE enabled = ?"
        params.append(1 if enabled else 0)
    sql += " ORDER BY job_key"
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def get_job(job_key: str) -> dict | None:
    for j in list_jobs():
        if j["job_key"] == job_key:
            return j
    return None


def update_next_run(job_key: str, next_run_at: int | None) -> None:
    """把 worker 算出的下次触发时刻写回表里（web 的 /jobs 与 /health 直接读它）。"""
    ensure_tables()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE schedule_jobs SET next_run_at = ? WHERE job_key = ?",
            (None if next_run_at is None else int(next_run_at), job_key),
        )


# ---------- 登记（幂等） ----------


def claim_job(
    job_key: str,
    subject_id: str,
    planned_at: int,
    run_id: str | None = None,
    trigger: str = "schedule",
) -> bool:
    """登记一次执行并直接置为 running；**同一幂等键已登记过则返回 False**。

    返回 False 就代表「这份活已经被别人（或上一轮）领走了」，调用方必须直接跳过——
    重跑调度器不能变成重复投递。attempts 从 1 起算：insert 成功即意味着这次执行已经开始。

    run_id 可以由调用方传入，这样调用方拿得到 id 去 finish_job（不必再查一次库）。
    """
    ensure_tables()
    rid = run_id or new_run_id()
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO job_runs (run_id, job_key, subject_id, planned_at, idem_key, "
            "status, attempts, started_at, trigger) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT(idem_key) DO NOTHING",
            (
                rid,
                job_key,
                str(subject_id),
                int(planned_at),
                idem_key(job_key, str(subject_id), planned_at),
                STATUS_RUNNING,
                now_ts(),
                trigger,
            ),
        )
        return cur.rowcount > 0


def enqueue_job(
    job_key: str, subject_id: str, planned_at: int, trigger: str = "manual"
) -> tuple[str | None, str]:
    """只登记不执行（status=pending），返回 (run_id | None, idem_key)。

    给 `POST /api/schedule/run-now` 用：web 进程写一行「立即到期」，
    worker 下一轮扫到再执行——手动触发因此和定时触发走同一条执行路径，
    不产生第二条代码分支（也就不会有「手动能跑、定时不能跑」这类差异）。
    """
    ensure_tables()
    rid = new_run_id()
    key = idem_key(job_key, str(subject_id), planned_at)
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO job_runs (run_id, job_key, subject_id, planned_at, idem_key, "
            "status, attempts, trigger) VALUES (?, ?, ?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(idem_key) DO NOTHING",
            (rid, job_key, str(subject_id), int(planned_at), key, STATUS_PENDING, trigger),
        )
        return (rid if cur.rowcount else None), key


def claim_pending(
    limit: int = 50, now: int | None = None, job_keys: list[str] | None = None
) -> list[dict]:
    """原子领取手动触发（pending）的执行行，置为 running、attempts+1。

    「先 SELECT 再 UPDATE」在这里是安全的：UPDATE 的 WHERE 里带了
    `status='pending'` 做 CAS，两个 worker 同时领同一条时只有一个 rowcount 是 1。

    job_keys 用来只领「有业务实现」的任务：业务模块还没写完时，
    run-now 写下的 pending 行应该**留在表里等实现**，而不是被领走再判成失败。
    """
    ensure_tables()
    ts = now_ts() if now is None else int(now)
    n = max(1, int(limit))
    where = "status = ?"
    params: list = [STATUS_PENDING]
    if job_keys is not None:
        keys = [str(k) for k in job_keys]
        if not keys:
            return []
        where += " AND job_key IN ({})".format(",".join("?" * len(keys)))
        params.extend(keys)
    with _get_conn() as conn:
        rows = conn.execute(
            "UPDATE job_runs SET status = ?, attempts = attempts + 1, started_at = ? "
            f"WHERE run_id IN (SELECT run_id FROM job_runs WHERE {where} "
            "ORDER BY planned_at ASC LIMIT ?) AND status = ? RETURNING *",
            (STATUS_RUNNING, ts, *params, n, STATUS_PENDING),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- 收尾与重试分级 ----------

_RETRYABLE_HINTS = (
    "timeout",
    "timed out",
    "超时",
    "connection",
    "连接",
    "429",
    "500",
    "502",
    "503",
    "504",
)
_FATAL_HINTS = ("401", "402", "会话不存在", "会话已删除", "知识库不存在", "库不存在")


def classify_error(
    exc: BaseException | None = None, status_code: int | None = None, message: str | None = None
) -> str:
    """把一次失败分成三类：CLASS_RETRY / CLASS_FATAL / CLASS_QUOTA。

    判定顺序：异常类型 > HTTP 状态码 > 文案关键词。
    类型最可靠，状态码其次（openai SDK 的 APIStatusError 带 status_code），
    文案是兜底——本模块不能假设调用方一定把状态码传出来了。

    判不出来时按 **不可重试** 处理：无人值守的链路里，
    「不知道能不能自愈」不该拿 token 去赌，失败落库由运维看。
    """
    # 局部 import：core.quota 会 import core.session，避免在 import 期形成环
    from core import quota as _quota

    if isinstance(exc, _quota.QuotaExceeded):
        return CLASS_QUOTA
    if isinstance(exc, TimeoutError):
        return CLASS_RETRY

    code = status_code
    if code is None and exc is not None:
        code = getattr(exc, "status_code", None)
        if code is None:
            code = getattr(exc, "status", None)
    if code is not None:
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = None
    if code is not None:
        if code in (401, 402):
            return CLASS_FATAL
        if code == 429 or code >= 500:
            return CLASS_RETRY

    text = " ".join(str(x) for x in (message, exc) if x)
    name = type(exc).__name__.lower() if exc is not None else ""
    if "timeout" in name or "connect" in name:
        return CLASS_RETRY
    if any(h in text for h in _FATAL_HINTS):
        return CLASS_FATAL
    if any(h in text.lower() for h in _RETRYABLE_HINTS):
        return CLASS_RETRY
    return CLASS_FATAL


def _backoff_target(attempts: int, finished_at: int) -> int | None:
    """第 attempts 次失败之后，下一次该什么时候跑；重试已用完返回 None。"""
    if attempts >= MAX_ATTEMPTS:
        return None
    idx = max(0, min(int(attempts) - 1, len(BACKOFF_MINUTES) - 1))
    return int(finished_at) + BACKOFF_MINUTES[idx] * 60


def finish_job(
    run_id: str,
    ok: bool = True,
    error: object | None = None,
    error_class: str | None = None,
    tokens_out: int = 0,
    now: int | None = None,
) -> dict | None:
    """收尾一次执行：成功置 succeeded；失败按分级决定还重不重试。

    重试**不是另起一行**，而是同一行改回「到点可重试」：
    同一 (job_key, subject_id, planned_at) 只允许一行（唯一索引），
    重试如果新插一行会直接撞约束——这也是为什么幂等键里必须有 planned_at 而不是 run_id。
    """
    ensure_tables()
    ts = now_ts() if now is None else int(now)
    row = get_run(run_id)
    if row is None:
        return None

    if ok:
        status, next_at, err = STATUS_SUCCEEDED, None, None
    else:
        cls = error_class or classify_error(
            exc=error if isinstance(error, BaseException) else None,
            message=None if error is None else str(error),
        )
        err = None if error is None else str(error)
        status = STATUS_FAILED
        if cls == CLASS_QUOTA:
            # 今天已经没额度了：延后到次日同刻（planned_at + 24h）。
            # 若这个点已经过去（比如补跑很久之后才失败），就顺延到从现在起 24h 后。
            next_at = int(row["planned_at"]) + QUOTA_DEFER_SECONDS
            if next_at <= ts:
                next_at = ts + QUOTA_DEFER_SECONDS
        elif cls == CLASS_RETRY:
            next_at = _backoff_target(int(row["attempts"]), ts)
        else:  # CLASS_FATAL：继续重试只会继续烧钱，就此打住
            next_at = None

    with _get_conn() as conn:
        conn.execute(
            "UPDATE job_runs SET status = ?, finished_at = ?, error = ?, tokens_out = ?, "
            "next_attempt_at = ? WHERE run_id = ?",
            (status, ts, err, max(0, int(tokens_out or 0)), next_at, run_id),
        )
    return get_run(run_id)


def due_retries(now: int | None = None, limit: int = 50) -> list[dict]:
    """到点的重试（含配额触顶延后到次日的那批），按到期时间升序。"""
    ensure_tables()
    ts = now_ts() if now is None else int(now)
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM job_runs WHERE status = ? AND next_attempt_at IS NOT NULL "
            "AND next_attempt_at <= ? ORDER BY next_attempt_at ASC LIMIT ?",
            (STATUS_FAILED, ts, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]


def claim_retry(run_id: str, now: int | None = None) -> bool:
    """把一行**到点的** failed 原子地重新置为 running 并 attempts+1；领不到返回 False。

    条件全写在 UPDATE 的 WHERE 里（status + attempts + 到期三条件）：
    两个 worker 同时领同一条时只有一个 rowcount 会是 1，不靠「先查再写」。

    `next_attempt_at IS NOT NULL` 这条不能少：不可重试的失败（401/402、依赖已失效）
    和重试已用完的失败都是 NULL，它们**不该被领走**——
    「手动重排」走 requeue_run，那条路径会显式把 next_attempt_at 设成现在。
    """
    ensure_tables()
    ts = now_ts() if now is None else int(now)
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE job_runs SET status = ?, attempts = attempts + 1, started_at = ?, "
            "finished_at = NULL, next_attempt_at = NULL "
            "WHERE run_id = ? AND status = ? AND attempts < ? AND next_attempt_at IS NOT NULL",
            (STATUS_RUNNING, ts, run_id, STATUS_FAILED, MAX_ATTEMPTS),
        )
        return cur.rowcount > 0


def requeue_run(run_id: str, force: bool = False, now: int | None = None) -> dict:
    """手动重排一次失败执行（POST /api/schedule/retry）。

    返回 {"ok": bool, "reason": str, "run": dict | None}，
    reason ∈ {"", "not_found", "not_failed", "exhausted"}——端点据此选 404 / 409。
    force=True 时清空 attempts：管理员显式覆盖「3 次重试已用完」的失败。
    """
    ensure_tables()
    ts = now_ts() if now is None else int(now)
    row = get_run(run_id)
    if row is None:
        return {"ok": False, "reason": "not_found", "run": None}
    if row["status"] == STATUS_SUCCEEDED:
        return {"ok": False, "reason": "not_failed", "run": row}
    if not force and row["status"] != STATUS_FAILED:
        # pending / running：要么马上就会跑，要么正在跑，重排没有意义
        return {"ok": False, "reason": "not_failed", "run": row}
    if not force and int(row["attempts"]) >= MAX_ATTEMPTS:
        return {"ok": False, "reason": "exhausted", "run": row}

    with _get_conn() as conn:
        if force:
            cur = conn.execute(
                "UPDATE job_runs SET status = ?, attempts = 0, next_attempt_at = ? "
                "WHERE run_id = ? AND status <> ?",
                (STATUS_FAILED, ts, run_id, STATUS_SUCCEEDED),
            )
        else:
            cur = conn.execute(
                "UPDATE job_runs SET next_attempt_at = ? "
                "WHERE run_id = ? AND status = ? AND attempts < ?",
                (ts, run_id, STATUS_FAILED, MAX_ATTEMPTS),
            )
        if cur.rowcount == 0:
            return {"ok": False, "reason": "exhausted", "run": get_run(run_id)}
    return {"ok": True, "reason": "", "run": get_run(run_id)}


# ---------- 查询 ----------


def get_run(run_id: str) -> dict | None:
    ensure_tables()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM job_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


class InvalidCursor(ValueError):
    """cursor 不是本模块给出的形态。端点据此回 400 而不是 500。"""


def _encode_cursor(row: dict) -> str:
    return f"{int(row['planned_at'])}:{row['run_id']}"


def _decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        planned, rid = str(cursor).split(":", 1)
        return int(planned), rid
    except Exception:
        raise InvalidCursor(str(cursor)) from None


def list_runs(
    limit: int = 20,
    cursor: str | None = None,
    job_key: str | None = None,
    status: str | None = None,
) -> dict:
    """执行历史，游标分页（新→旧）。

    游标取 (planned_at, run_id) 而不是 OFFSET：调度表随时在插新行，
    OFFSET 分页会漏行/重行（翻到第二页时前面又插了几条，就整体错位）。
    """
    ensure_tables()
    n = max(1, min(int(limit or 20), 100))
    where: list[str] = []
    params: list = []
    if job_key:
        where.append("job_key = ?")
        params.append(str(job_key))
    if status:
        where.append("status = ?")
        params.append(str(status))
    if cursor:
        planned_at, rid = _decode_cursor(cursor)
        where.append("(planned_at < ? OR (planned_at = ? AND run_id < ?))")
        params.extend([planned_at, planned_at, rid])
    sql = "SELECT * FROM job_runs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY planned_at DESC, run_id DESC LIMIT ?"
    with _get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, [*params, n + 1]).fetchall()]
    items = rows[:n]
    next_cursor = _encode_cursor(items[-1]) if (len(rows) > n and items) else None
    return {"items": items, "next_cursor": next_cursor}


def count_runs(status: str | None = None) -> int:
    ensure_tables()
    with _get_conn() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM job_runs WHERE status = ?", (status,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM job_runs").fetchone()
        return int(row["c"])


def purge_runs(before_ts: int, keep_failed: bool = True) -> int:
    """清理过期执行记录（housekeeping 用），返回删除行数。

    默认**保留 failed**：失败行是排查「为什么今天没发」的唯一线索，
    比磁盘空间值钱；真要清就把 keep_failed 显式关掉。
    """
    ensure_tables()
    sql = "DELETE FROM job_runs WHERE planned_at < ?"
    params: list = [int(before_ts)]
    if keep_failed:
        sql += " AND status <> ?"
        params.append(STATUS_FAILED)
    with _get_conn() as conn:
        return conn.execute(sql, params).rowcount


def job_stats(job_key: str, lookback: int = 20) -> dict:
    """单个 job 的最近执行情况：last_status / last_run_at / consecutive_failures。

    consecutive_failures 从最新一行往回数连续 failed 的条数，
    是第 5 问里「连续 3 次失败就外发告警」的输入。
    """
    ensure_tables()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT status, planned_at, started_at, finished_at FROM job_runs "
            "WHERE job_key = ? ORDER BY planned_at DESC, rowid DESC LIMIT ?",
            (job_key, max(1, int(lookback))),
        ).fetchall()
    last_status = None
    last_run_at = None
    if rows:
        last_status = rows[0]["status"]
        last_run_at = rows[0]["finished_at"] or rows[0]["started_at"] or rows[0]["planned_at"]
    streak = 0
    for r in rows:
        if r["status"] == STATUS_FAILED:
            streak += 1
        else:
            break
    return {
        "last_status": last_status,
        "last_run_at": None if last_run_at is None else int(last_run_at),
        "consecutive_failures": streak,
    }


# ---------- 心跳与健康 ----------


def heartbeat(pid: int | None = None, version: str = WORKER_VERSION) -> int:
    """写一次心跳（恒为 id=1 的一行 upsert），返回写入的时刻。

    心跳是「worker 还活着」的唯一凭据：进程表判不出「进程在但卡死」，
    而一个不再更新的时间戳既盖住了崩溃、也盖住了卡死。
    """
    ensure_tables()
    ts = now_ts()
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO worker_heartbeat (id, ts, pid, version) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET ts = excluded.ts, pid = excluded.pid, "
            "version = excluded.version",
            (ts, os.getpid() if pid is None else int(pid), version),
        )
    return ts


def heartbeat_info() -> dict:
    ensure_tables()
    with _get_conn() as conn:
        row = conn.execute("SELECT ts, pid, version FROM worker_heartbeat WHERE id = 1").fetchone()
    return dict(row) if row else {}


def heartbeat_age(now: int | None = None) -> float | None:
    """距上次心跳的秒数；**从未心跳过返回 None**（调用方应当判 down）。"""
    info = heartbeat_info()
    if not info:
        return None
    ts = now_ts() if now is None else int(now)
    return max(0.0, float(ts - int(info["ts"])))


def health(threshold: int = HEARTBEAT_DOWN_SECONDS, now: int | None = None) -> dict:
    """调度健康快照：worker 心跳 + 各 job 最近执行。

    三态而不是两态：
      up       —— 心跳新鲜（≤ 一个 dispatch 周期）
      stalled  —— 超过一个周期但还没到阈值：给运维一个黄灯，别等它彻底掉下去
      down     —— 超过 threshold（默认 600s）：`GET /api/schedule/health` 的判据，
                  不看进程表，进程在但卡死同样要被抓到
    """
    ensure_tables()
    age = heartbeat_age(now=now)
    if age is None or age > threshold:
        status = "down"
    elif age > HEARTBEAT_STALL_SECONDS:
        status = "stalled"
    else:
        status = "up"

    info = heartbeat_info()
    jobs = []
    for j in list_jobs():
        st = job_stats(j["job_key"])
        jobs.append(
            {
                "job_key": j["job_key"],
                "cron": j["cron"],
                "enabled": j["enabled"],
                "last_status": st["last_status"],
                "last_run_at": st["last_run_at"],
                "consecutive_failures": st["consecutive_failures"],
                "next_run_at": j["next_run_at"],
            }
        )
    return {
        "worker": {
            "status": status,
            "heartbeat_age_seconds": None if age is None else int(round(age)),
            "pid": info.get("pid"),
            "version": info.get("version"),
        },
        "jobs": jobs,
    }


# ---------- cron 估算（给「worker 挂了也要能看到下次该跑的时间」用） ----------


def _parse_field(field: str, fmin: int, fmax: int) -> list[int] | None:
    """解析一个 cron 字段为取值集合；不支持的写法返回 None。

    支持 `*`、`*/n`、`a`、`a,b,c`、`a-b`、`a-b/n`。
    """
    out: set[int] = set()
    for part in str(field).split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit():
                return None
            step = int(step_s)
            if step <= 0:
                return None
        if part == "*":
            lo, hi = fmin, fmax
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                return None
            lo, hi = int(a), int(b)
        elif part.isdigit():
            lo = hi = int(part)
        else:
            return None
        if lo < fmin or hi > fmax or lo > hi:
            return None
        out.update(range(lo, hi + 1, step))
    return sorted(out)


def estimate_next_run(
    cron: str, tz_name: str | None = None, after: int | None = None
) -> int | None:
    """按 5 段 cron 估算下一次触发时刻（Unix 秒），算不出来返回 None。

    只支持本项目用到的写法：分钟 / 小时支持 `*`、`*/n`、列表、区间、步长；
    日 / 月 / 周只支持 `*`（5 个任务都是「天天跑」，不需要日历规则）。
    不支持的写法返回 None —— 宁可显示「未知」，也不猜一个时间给运维看。

    真正的触发由 worker 里的 APScheduler 决定（它是权威）；
    这个函数只服务一个场景：**worker 挂了**，健康端点仍要能显示「下次该跑的时间」。
    """
    parts = str(cron).split()
    if len(parts) != 5:
        return None
    minutes = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    dom = _parse_field(parts[2], 1, 31)
    month = _parse_field(parts[3], 1, 12)
    dow = _parse_field(parts[4], 0, 7)
    if minutes is None or hours is None or dom is None or month is None or dow is None:
        return None
    # 日 / 月 / 周只认「不限制」，否则宁可返回 None
    if dom != list(range(1, 32)) or month != list(range(1, 13)) or dow != list(range(0, 8)):
        return None

    # 默认时区直接用 timeutil.TZ（唯一口径）；只有显式传了别的时区名才另建。
    # ZoneInfo 仍从标准库引入：它不是"第二份口径"，只是把名字换成时区对象。
    tz = TZ if (not tz_name or tz_name == TZ_NAME) else ZoneInfo(tz_name)
    base = datetime.fromtimestamp(now_ts() if after is None else int(after), tz)
    # 从下一分钟开始找：同一分钟内的调用不该被算成「下一次」
    start = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for day_off in range(0, 3):
        day = (start + timedelta(days=day_off)).date()
        for h in hours:
            for m in minutes:
                cand = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
                if cand >= start:
                    return int(cand.timestamp())
    return None
