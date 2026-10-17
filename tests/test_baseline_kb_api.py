"""行为基线（characterization tests）：知识库上传/入库接口。

只直接调用 `api.kb` 里的 async 端点函数（显式传 uid，绕过鉴权依赖），
不启 HTTP 服务、不联网、不调真 LLM：embedding 换确定性假实现，
chroma / 数据目录全部落在临时目录。

钉住的是"入库这条链路的可观测结果"：响应结构、文件记录内容、rebuild 的
retain 语义、校验失败的分支与状态码、以及**中途失败时前面已成功文件的记录
是否保留**（这条最容易在"批量写盘"类优化里被改坏）。
"""

import asyncio
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402

# 必须在 import pipelines / rag_core 之前把落盘位置换到临时目录
_TMP = tempfile.mkdtemp(prefix="baomi_kb_baseline_")
config.PROJECT_ROOT = os.path.join(_TMP, "proj")
config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
config.DATA_DIR = os.path.join(_TMP, "proj", "data")
config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(config.DATA_DIR, exist_ok=True)

import pytest  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

import api.kb as kb_api  # noqa: E402
import kb.pipelines as pipelines  # noqa: E402
import kb.rag_core as rc  # noqa: E402

rc.get_embeddings = lambda: DeterministicFakeEmbedding(size=64)

OWNER = "u_baseline"


def _uf(name: str, text: str) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(text.encode("utf-8")))


def _no_quota(monkeypatch):
    """配额只跟"能不能上传"有关，跟入库结果无关；这里让它一律放行且不记账。"""
    monkeypatch.setattr(kb_api.quota, "guard", lambda *a, **k: None)
    monkeypatch.setattr(kb_api.quota, "bump_usage", lambda *a, **k: None)
    monkeypatch.setattr(kb_api.quota, "consume", lambda *a, **k: None)


def _upload(kb_id, files, mode="append", chunk_size=500, chunk_overlap=50):
    return asyncio.run(
        kb_api.upload_and_build(kb_id, files, chunk_size, chunk_overlap, mode, OWNER)
    )


# ==================== 上传：响应结构与文件记录 ====================


def test_upload_response_shape_and_records(monkeypatch):
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "基线库")

    out = _upload(kb_id, [_uf("a.txt", "甲" * 300), _uf("b.txt", "乙" * 300)])

    assert set(out) == {"kb_id", "chunks", "files"}
    assert out["kb_id"] == kb_id
    assert out["files"] == ["a.txt", "b.txt"]
    assert out["chunks"] > 0

    recs = pipelines.get_files(OWNER, kb_id)
    assert [r["name"] for r in recs] == ["a.txt", "b.txt"]
    assert all(r["origin"] == "upload" for r in recs)
    assert all(r["chunks"] > 0 for r in recs)
    assert all(isinstance(r["added_at"], int) for r in recs)
    # 原始文件留档：rebuild 要靠它重新分片
    for name in ("a.txt", "b.txt"):
        assert os.path.exists(pipelines.source_path(kb_id, name))


def test_upload_same_name_replaces_record(monkeypatch):
    """同名再上传不产生重复记录（覆盖语义是 kb_files.json 既有约定）。"""
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "重名库")

    _upload(kb_id, [_uf("same.txt", "第一版内容" * 40)])
    _upload(kb_id, [_uf("same.txt", "第二版内容" * 40)])

    recs = pipelines.get_files(OWNER, kb_id)
    assert [r["name"] for r in recs] == ["same.txt"]


def test_upload_rebuild_retains_only_uploaded(monkeypatch):
    """rebuild 模式下，本次没上传的旧记录要一并清掉。"""
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "重建库")

    _upload(kb_id, [_uf("old.txt", "老内容" * 40)])
    _upload(kb_id, [_uf("new.txt", "新内容" * 40)], mode="rebuild")

    assert [r["name"] for r in pipelines.get_files(OWNER, kb_id)] == ["new.txt"]


# ==================== 上传：校验分支 ====================


def test_upload_validation_errors(monkeypatch):
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "校验库")

    with pytest.raises(HTTPException) as e1:
        _upload(kb_id, [])
    assert (e1.value.status_code, e1.value.detail) == (400, "未上传任何文件")

    with pytest.raises(HTTPException) as e2:
        _upload(kb_id, [_uf("a.txt", "内容")], mode="replace")
    assert (e2.value.status_code, e2.value.detail) == (400, "mode 只能是 append 或 rebuild")

    with pytest.raises(HTTPException) as e3:
        _upload(kb_id, [_uf("a.docx", "内容")])
    assert e3.value.status_code == 400
    assert e3.value.detail.startswith("不支持的文件格式：.docx")

    # 校验失败一个字节都不该落盘
    assert os.listdir(pipelines.ensure_source_dir(kb_id)) == []


def test_upload_not_owner_is_404(monkeypatch):
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "别人库")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            kb_api.upload_and_build(kb_id, [_uf("a.txt", "x")], 500, 50, "append", "u_other")
        )
    assert ei.value.status_code == 404


# ==================== 上传：中途失败时已成功文件的记录要保留 ====================


def test_upload_partial_failure_keeps_first_record(monkeypatch):
    """★ 第 2 个文件建索引抛错时，第 1 个文件已经登记进 kb_files.json 的记录必须还在。

    这是"文件记录批量化写盘"类优化最容易改坏的地方：把多次写合并成一次时，
    若把写盘放到循环之外且没有 finally 兜底，异常路径会把第一条记录一起丢掉。
    """
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "半途失败库")

    class _FlakyPipe:
        def __init__(self):
            self.calls = 0

        def build_index(self, *a, **k):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("模拟第二个文件入库失败")
            return 3

    monkeypatch.setattr(kb_api.pipelines, "get_or_create", lambda owner, kid: _FlakyPipe())

    with pytest.raises(HTTPException) as ei:
        _upload(kb_id, [_uf("ok.txt", "第一个" * 40), _uf("bad.txt", "第二个" * 40)])
    assert ei.value.status_code == 500

    assert [r["name"] for r in pipelines.get_files(OWNER, kb_id)] == ["ok.txt"]


# ==================== 同步问答端点 ====================


def test_query_kb_returns_answer_and_refs(monkeypatch):
    """★ 阶段 4 修复后的行为：`/api/kb/{kb_id}/query` 正常返回 {answer, refs}。

    修复前这里必然 500——端点调用的是已在死代码清理中删掉的 `RagPipeline.answer`，
    AttributeError 被端点末尾的 `except Exception` 兜成了 500。
    现在按删除前的实现恢复了该方法，用假 LLM 顶掉真实调用，断言返回结构与 refs 文案。
    """
    _no_quota(monkeypatch)
    kb_id, _ = pipelines.create_kb(OWNER, "问答库")
    _upload(kb_id, [_uf("a.txt", "苞米的种植密度一般每亩 3500 到 4000 株。" * 15)])

    class _Resp:
        content = "每亩 3500 到 4000 株。"

    class _FakeLLM:
        def invoke(self, messages):
            # 与 build_sources 同口径的组装：system + user 两条
            assert isinstance(messages, list) and len(messages) == 2
            return _Resp()

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: _FakeLLM())

    out = asyncio.run(kb_api.query_kb(kb_id, "苞米种植密度", 3, OWNER))

    assert out["answer"] == "每亩 3500 到 4000 株。"
    assert out["refs"].startswith("**[1]** a.txt ｜ ")
    # 假 embedding 的向量未归一化 → 距离越界、相似度换算不了 → 整段 score 省掉（既有约定）
    assert "score=" not in out["refs"].split("\n")[0]
