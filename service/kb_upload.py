"""资料库上传的入库编排（P1 提速 + P2 异步共用的核心）。

2026-11-18 从 api/kb.py 的 _build 闭包抽出来，三件事收在一处：
  1. 多文件合批 embedding —— 逐文件 prepare（保留失败隔离），一次 store（合批 embed）；
  2. 同名覆盖去重 —— append 模式下先按 source 删旧片段，避免重传叠加；
  3. 主题抽取异步化 —— 入库时 defer_topics 跳过 LLM，返回后由 backfill 补抽。

P2 异步上传（worker）走 index_sources。同步端点（api/kb.py /upload）保留原来的
逐文件 build_index 循环——tests/test_baseline_kb_api.py 用只有 build_index 方法的
假管道钉死了这条路径（test_upload_partial_failure_keeps_first_record），换实现会
 AttributeError。它已通过 defer_topics=True + backfill 拿到 P1-1 提速；合批与去重
只在异步路径生效（前端两个入口都已改走 upload-async）。

P2 任务表 kb_upload_jobs 落在 scheduler.db（与 job_runs 同库），由本模块自建：
job_runs 记录调度状态（pending/running/succeeded/failed + 重试分级），
kb_upload_jobs 记录业务参数与进度（uid/kb_id/路径/参数/chunks/error）。
"""
from __future__ import annotations

import json
import logging
import os
import time

from core import quota
from core import scheduler as sched
from kb import pipelines

_log = logging.getLogger(__name__)

JOB_KEY = "kb.upload"

# 伴生表：任务参数 + 业务进度。job_id 同时是 job_runs.subject_id，两边靠它 join。
_UPLOAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_upload_jobs (
    job_id      TEXT PRIMARY KEY,
    uid         TEXT NOT NULL,
    kb_id       TEXT NOT NULL,
    payload     TEXT NOT NULL,          -- JSON：{paths, names, mode, chunk_size, overlap}
    status      TEXT NOT NULL DEFAULT 'queued',
    chunks      INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_upload_jobs_kb ON kb_upload_jobs(kb_id, created_at DESC);
"""


def _ensure_upload_table() -> None:
    sched.ensure_tables()
    with sched._get_conn() as conn:  # noqa: SLF001 —— 同项目内复用调度库连接（WAL/busy_timeout 已配好）
        conn.executescript(_UPLOAD_SCHEMA)


# ==================== 共用的入库核心 ====================


def index_sources(
    uid: str,
    kb_id: str,
    saved: list[tuple[str, str]],
    mode: str,
    chunk_size: int,
    chunk_overlap: int,
    now: int | None = None,
) -> tuple[int, list[str]]:
    """合批 + 去重 + defer_topics 入库。saved 是 (落盘路径, 原始文件名) 列表。

    返回 (总片段数, 文件记录名列表)。内部持 kb_lock：同库的上传/rebuild/删文件
    串行，避免两个任务并发写同一 collection。

    ★ 失败隔离：逐文件 prepare（load+split），某个文件解析失败时保留已成功文件；
    finally 里一次性 store 已成功 chunks + 批量登记记录（与旧 _build 语义一致）。
    """
    if now is None:
        now = int(time.time())
    with pipelines.kb_lock(kb_id):
        pipe = pipelines.get_or_create(uid, kb_id)
        all_chunks: list = []
        pending: list[dict] = []
        try:
            for path, name in saved:
                # P1-3 同名覆盖：append 模式先删该来源旧片段，避免重传叠加。
                # rebuild 模式会整库清空，无需单个删。
                if mode == "append" and pipe.has_source(name):
                    pipe.delete_by_source(name)
                chunks, _counter = pipe.prepare_chunks(
                    chunk_size, chunk_overlap, [path], [name]
                )
                all_chunks.extend(chunks)
                pending.append(
                    {"name": name, "chunks": len(chunks), "added_at": now, "origin": "upload"}
                )
        finally:
            # P1-2 合批：所有成功文件只做一次 embedding（不满 10 片的小文件不再
            # 各发一次半空请求）；P1-1 主题异步：defer_topics 跳过同步 LLM 抽取。
            if all_chunks:
                pipe.store_chunks(
                    all_chunks, mode, chunk_size, chunk_overlap, defer_topics=True
                )
            if pending:
                pipelines.add_file_records(uid, kb_id, pending)
        if mode == "rebuild":
            pipelines.retain_file_records(uid, kb_id, {name for _, name in saved})
        return sum(r["chunks"] for r in pending), pipelines.get_file_names(uid, kb_id)


def backfill_after_upload(uid: str, kb_id: str, names: list[str]) -> int:
    """入库返回后补抽主题标签（可后台调用）。失败静默，返回补抽来源数。"""
    return pipelines.backfill_topics(uid, kb_id, names)


# ==================== P2：任务入队 / 状态 / worker 执行 ====================


def submit_upload(
    uid: str,
    kb_id: str,
    saved: list[tuple[str, str]],
    mode: str,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[str, str]:
    """落盘完成后调用：登记任务 + 写 pending 行，立即返回 (job_id, run_id)。

    job_id 用 uuid4（全局唯一），避免同一秒两次上传被 job_runs 的幂等键合并。
    run_id 为 None 表示幂等命中（理论上 uuid4 不会），此时回退用 job_id 查。
    """
    _ensure_upload_table()
    job_id = f"kbup_{os.urandom(12).hex()}"
    payload = json.dumps(
        {
            "paths": [p for p, _ in saved],
            "names": [n for _, n in saved],
            "mode": mode,
            "chunk_size": int(chunk_size),
            "chunk_overlap": int(chunk_overlap),
        },
        ensure_ascii=False,
    )
    ts = int(time.time())
    with sched._get_conn() as conn:
        conn.execute(
            "INSERT INTO kb_upload_jobs (job_id, uid, kb_id, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, uid, kb_id, payload, ts, ts),
        )
    run_id, _key = sched.enqueue_job(JOB_KEY, job_id, ts, trigger="upload")
    return job_id, run_id or job_id


def get_job(job_id: str, uid: str) -> dict | None:
    """查任务状态（按 uid 归属过滤，不是自己的返回 None）。

    合并调度状态（job_runs）与业务进度（kb_upload_jobs），前端轮询这一条即可。
    """
    _ensure_upload_table()
    with sched._get_conn() as conn:
        row = conn.execute(
            "SELECT j.*, r.status AS run_status, r.attempts, r.error AS run_error, "
            "r.next_attempt_at, r.finished_at "
            "FROM kb_upload_jobs j LEFT JOIN job_runs r ON r.subject_id = j.job_id "
            "WHERE j.job_id = ? AND j.uid = ? ORDER BY r.planned_at DESC LIMIT 1",
            (job_id, uid),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    # 以业务表 status 为准（run_one 直接写，最直接）；job_runs 只做兜底：
    # 业务表还停在 queued/running 但调度已 failed（如 handler 没挂上被判 fatal）时，
    # 用 run_status 兜住，避免前端永远 processing。
    biz = d.get("status")
    run_status = d.get("run_status")
    if biz in ("succeeded", "done"):
        status = "done"
    elif biz in ("failed", "error"):
        status = "error"
    elif run_status == "succeeded":
        status = "done"
    elif run_status == "failed":
        status = "error"
    else:
        status = "processing"
    return {
        "job_id": d["job_id"],
        "kb_id": d["kb_id"],
        "status": status,
        "chunks": int(d.get("chunks") or 0),
        "error": d.get("error") or d.get("run_error"),
        "attempts": int(d.get("attempts") or 0),
        "created_at": int(d.get("created_at") or 0),
        "updated_at": int(d.get("updated_at") or 0),
    }


def _set_job(job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = int(time.time())
    sets = ", ".join(f"{k} = ?" for k in fields)
    with sched._get_conn() as conn:
        conn.execute(
            f"UPDATE kb_upload_jobs SET {sets} WHERE job_id = ?",
            (*fields.values(), job_id),
        )


def run_one(job_key: str, subject_id: str, planned_at: int) -> dict:
    """worker 执行入口：读参数 → index_sources → quota 记账 → 补抽主题。

    由 worker/scheduler.py 的 _execute_one 调用；异常向上抛，由它统一做重试分级
    （CLASS_FATAL 不重试，其余按 1/5/15 分钟退避）。业务侧只更新进度字段。
    """
    job_id = str(subject_id)
    _ensure_upload_table()
    with sched._get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_upload_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if not row:
        _log.warning("kb.upload job not found: %s", job_id)
        return {}
    d = dict(row)
    try:
        p = json.loads(d["payload"])
        paths = list(p["paths"])
        names = list(p["names"])
        saved = [(paths[i], names[i]) for i in range(min(len(paths), len(names)))]
        _set_job(job_id, status="running")

        # 逐文件校验原始文件还在（清理任务可能删过），缺了就当失败重试也无力回天 → FATAL
        for path, _name in saved:
            if not os.path.exists(path):
                raise FileNotFoundError(f"上传的临时文件已不存在，请重新上传：{path}")

        total, _names = index_sources(
            d["uid"], d["kb_id"], saved, p["mode"],
            int(p["chunk_size"]), int(p["chunk_overlap"]),
        )
        if total:
            quota.bump_usage(d["uid"], "embed", total)
        # 主题补抽：worker 本来就在后台，直接同步跑（失败静默，不影响任务成功）
        backfill_after_upload(d["uid"], d["kb_id"], names)
        _set_job(job_id, status="succeeded", chunks=int(total), error=None)
        _log.info("kb.upload done job=%s kb=%s chunks=%s", job_id, d["kb_id"], total)
        return {"tokens_out": int(total)}
    except Exception as e:  # noqa: BLE001 —— 转成任务失败，让 worker 做重试分级
        _set_job(job_id, status="failed", error=f"{type(e).__name__}: {e}"[:500])
        raise
