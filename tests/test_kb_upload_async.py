"""P1 提速 + P2 异步上传的行为守卫（2026-11-18）。

钉住三件最容易在"优化"里被改坏的事：
  1. 多文件合批：N 个文件只做一次 store_chunks（embedding 合批），且逐文件 chunks 记录正确；
  2. 同名覆盖去重：append 模式重传同名文件时先 delete_by_source，不让片段叠加；
  3. 异步任务：submit_upload 登记任务 → run_one 执行 → get_job 反映终态与错误。

不联网、不调真 LLM：embedding 换确定性假实现；用临时目录隔离数据。
"""
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="baomi_kb_async_")
config.PROJECT_ROOT = os.path.join(_TMP, "proj")
config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
config.DATA_DIR = os.path.join(_TMP, "proj", "data")
os.makedirs(config.DATA_DIR, exist_ok=True)

import pytest  # noqa: E402
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

import kb.pipelines as pipelines  # noqa: E402
import kb.rag_core as rc  # noqa: E402
from core import scheduler as sched  # noqa: E402
from service import kb_upload  # noqa: E402

rc.get_embeddings = lambda: DeterministicFakeEmbedding(size=64)
OWNER = "u_async"


@pytest.fixture(autouse=True)
def _isolate():
    """每个用例独立临时库路径，重建表缓存。

    ★ teardown 必须把 DATA_DIR 恢复成模块级默认（<tmp>/proj/data）：
    test_scheduler.py 的 test_cron_table_matches_spec 会断言默认库路径
    以 /data/scheduler.db 结尾，裸 case_xxx 目录泄漏过去就是假红。
    """
    d = tempfile.mkdtemp(prefix="case_")
    config.DATA_DIR = d
    sched._READY.clear()
    if hasattr(sched._LOCAL, "conn"):
        try:
            sched._LOCAL.conn.close()
        except Exception:
            pass
        sched._LOCAL.conn = None
    yield
    config.DATA_DIR = os.path.join(_TMP, "proj", "data")
    if hasattr(sched._LOCAL, "conn") and sched._LOCAL.conn:
        try:
            sched._LOCAL.conn.close()
        except Exception:
            pass
        sched._LOCAL.conn = None
    sched._READY.clear()


def _save(kb_id, name, text):
    import asyncio as _a

    from fastapi import UploadFile

    f = UploadFile(filename=name, file=BytesIO(text.encode("utf-8")))
    ext = os.path.splitext(name)[1].lower()
    safe = pipelines.safe_source_name(name, fallback_ext=ext)
    pipelines.ensure_source_dir(kb_id)
    path = pipelines.source_path(kb_id, safe)
    _a.run(_save_stream(f, path))
    return path, safe


async def _save_stream(f, path):
    from core import upload

    await upload.save_stream(f, path, 100 * 1024 * 1024)


def test_index_sources_batches_multiple_files():
    """多文件合批：一次 store_chunks 处理全部文件，per-file chunks 记录正确。"""
    kb_id, _ = pipelines.create_kb(OWNER, "合批库")
    saved = [
        _save(kb_id, "a.txt", "甲" * 200),
        _save(kb_id, "b.txt", "乙" * 200),
        _save(kb_id, "c.txt", "丙" * 200),
    ]
    calls = {"n": 0}
    orig = rc.RagPipeline.store_chunks

    def spy(self, chunks, *a, **k):
        calls["n"] += 1
        assert k.get("defer_topics") is True, "上传入库必须 defer_topics=True（主题异步化）"
        return orig(self, chunks, *a, **k)

    rc.RagPipeline.store_chunks = spy
    try:
        total, names = kb_upload.index_sources(OWNER, kb_id, saved, "append", 100, 20)
    finally:
        rc.RagPipeline.store_chunks = orig

    assert calls["n"] == 1, f"3 个文件应合批成 1 次 store_chunks，实际 {calls['n']}"
    assert total > 0
    assert set(names) == {"a.txt", "b.txt", "c.txt"}
    recs = {r["name"]: r["chunks"] for r in pipelines.get_files(OWNER, kb_id)}
    assert all(recs[n] > 0 for n in ("a.txt", "b.txt", "c.txt"))


def test_index_sources_dedup_overwrites_same_name():
    """同名覆盖：append 重传同名文件先 delete_by_source，片段不叠加。"""
    kb_id, _ = pipelines.create_kb(OWNER, "去重库")
    p1 = _save(kb_id, "same.txt", "第一版内容" * 100)
    kb_upload.index_sources(OWNER, kb_id, [p1], "append", 100, 20)
    n1 = pipelines.get_or_create(OWNER, kb_id).count()

    deleted = {"n": 0}
    orig = rc.RagPipeline.delete_by_source

    def spy(self, src):
        deleted["n"] += 1
        return orig(self, src)

    rc.RagPipeline.delete_by_source = spy
    try:
        p2 = _save(kb_id, "same.txt", "第二版全新的内容" * 100)
        kb_upload.index_sources(OWNER, kb_id, [p2], "append", 100, 20)
    finally:
        rc.RagPipeline.delete_by_source = orig

    assert deleted["n"] == 1, "append 重传同名必须先删旧片段"
    n2 = pipelines.get_or_create(OWNER, kb_id).count()
    assert n2 < n1 * 2, f"片段叠加了：首次 {n1}，重传后 {n2}"


def test_submit_run_get_job_roundtrip(monkeypatch):
    """异步任务闭环：submit → run_one → get_job 反映 done 与 chunks。"""
    monkeypatch.setattr(kb_upload.quota, "bump_usage", lambda *a, **k: None)
    kb_id, _ = pipelines.create_kb(OWNER, "异步库")
    saved = [_save(kb_id, "doc.txt", "异步上传的内容" * 100)]

    job_id, run_id = kb_upload.submit_upload(OWNER, kb_id, saved, "append", 100, 20)
    assert job_id.startswith("kbup_")
    assert run_id

    before = kb_upload.get_job(job_id, OWNER)
    assert before["status"] == "processing"

    # 模拟 worker 领取 + 执行
    run = sched.get_run(run_id)
    assert run and run["status"] == "pending"
    assert sched.claim_pending(job_keys=[kb_upload.JOB_KEY])
    kb_upload.run_one(kb_upload.JOB_KEY, job_id, run["planned_at"])

    after = kb_upload.get_job(job_id, OWNER)
    assert after["status"] == "done", after
    assert after["chunks"] > 0
    assert pipelines.get_or_create(OWNER, kb_id).count() > 0


def test_get_job_owner_isolation():
    """归属隔离：别人查我的任务返回 None。"""
    kb_id, _ = pipelines.create_kb(OWNER, "隔离库")
    saved = [_save(kb_id, "x.txt", "内容" * 50)]
    job_id, _ = kb_upload.submit_upload(OWNER, kb_id, saved, "append", 100, 20)
    assert kb_upload.get_job(job_id, "u_other") is None
    assert kb_upload.get_job(job_id, OWNER) is not None


def test_worker_registers_kb_upload_handler():
    """★ worker 必须挂上 kb.upload handler，否则 pending 任务永远不被执行（静默故障）。"""
    import worker.scheduler as ws

    ok, why = ws._load_handlers(force=True)
    assert ok, why
    assert kb_upload.JOB_KEY in ws._handlers, (
        f"kb.upload handler 没挂上：{sorted(ws._handlers)}（upload-jobs 会永远 processing）"
    )
    assert callable(getattr(ws._handlers[kb_upload.JOB_KEY], "run_one", None))


def test_run_one_reports_error():
    """worker 执行失败时：job status=error 且带具体原因（失败可见）。"""
    kb_id, _ = pipelines.create_kb(OWNER, "失败库")
    saved = [_save(kb_id, "gone.txt", "内容" * 50)]
    # 删掉落盘文件，run_one 会因文件不存在抛 FileNotFoundError
    for path, _name in saved:
        os.remove(path)
    job_id, run_id = kb_upload.submit_upload(OWNER, kb_id, saved, "append", 100, 20)
    with pytest.raises(FileNotFoundError):
        kb_upload.run_one(kb_upload.JOB_KEY, job_id, 0)
    job = kb_upload.get_job(job_id, OWNER)
    assert job["status"] == "error"
    assert "FileNotFoundError" in (job["error"] or "")
