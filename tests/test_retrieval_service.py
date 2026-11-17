"""kb/retrieval.py：三条路径共用的检索编排层。

守四件事（都是 RAG / B4 历史上踩过的坑）：

1. **空结果是空产物，不是 None** —— 调用方只判 ``is_empty``，不用到处写 ``if docs:``；
2. **四件整形只算一次** —— refs / sources / grounding / references 口径唯一；
3. **异常原样抛** —— 三条路径的降级方式不同（图片只按图片答 / 智能体无资料照答 /
   RAG 中断发 error），服务层不替调用方决定；
4. **top_k 与 owner 原样透传** —— 历史上出现过"前端调的 top_k 被默认值悄悄覆盖"。

不碰 Chroma、不调 LLM：pipe 用注入的假实现，build_query 直接打桩。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-dummy-for-import")

import pytest  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

import kb.retrieval as kbret  # noqa: E402
from kb.rag_core import RagPipeline  # noqa: E402

OWNER = "u_retr"
KB = "kb_test"

DOCS = [
    (Document(page_content="强相关片段", metadata={"source": "资料.txt"}), 0.4),
    (Document(page_content="沾边片段", metadata={"source": "别的.txt"}), 1.4),
]


class _Pipe:
    """假 pipeline：记录调用参数，可注入异常。"""

    def __init__(self, docs):
        self.docs = docs
        self.calls: list[tuple] = []

    def retrieve_with_score(self, query, top_k, owner=None):
        self.calls.append((query, top_k, owner))
        if isinstance(self.docs, Exception):
            raise self.docs
        return self.docs

    # 服务层只依赖公开方法；假实现必须提供同一组名字
    format_refs = staticmethod(RagPipeline.format_refs)
    build_sources = staticmethod(RagPipeline.build_sources)


def _run(coro):
    """不起 pytest-asyncio：项目其它测试也都是同步跑协程。"""
    return asyncio.run(coro)


def test_empty_result_is_empty_product():
    """没命中时四件产物都是空值，而不是 None —— 调用方少一层判空。"""
    r = _run(kbret.retrieve(OWNER, KB, "q", 3, pipe=_Pipe([])))

    assert r.is_empty
    assert r.docs_scores == []
    assert r.refs_md == ""
    assert r.sources == []
    assert r.grounding == {}
    assert r.references == ""


def test_full_result_shapes_all_four_products():
    """命中时四件产物一次算齐，且与前端的口径一致。"""
    r = _run(kbret.retrieve(OWNER, KB, "q", 2, pipe=_Pipe(DOCS)))

    assert not r.is_empty
    assert [s["source"] for s in r.sources] == ["资料.txt", "别的.txt"]
    assert r.refs_md.startswith("**[1]** 资料.txt"), "refs 是落库用的 Markdown 清单"
    assert "[1] （来源：资料.txt）" in r.references
    assert "[2] （来源：别的.txt）" in r.references
    assert r.grounding["strength"] == "high"
    assert r.grounding["weak"] is False


def test_query_topk_and_owner_passthrough():
    """top_k / owner 必须原样传给 pipeline（这条曾被默认值覆盖过）。"""
    p = _Pipe(DOCS)
    _run(kbret.retrieve(OWNER, KB, "增强后的词", 7, pipe=p))

    assert p.calls == [("增强后的词", 7, OWNER)]


def test_exception_is_not_swallowed():
    """服务层不吞异常：降级与否由调用方决定。"""
    with pytest.raises(RuntimeError, match="知识库为空"):
        _run(kbret.retrieve(OWNER, KB, "q", 3, pipe=_Pipe(RuntimeError("知识库为空"))))


def test_retrieve_for_turn_carries_query_plan(monkeypatch):
    """一条龙版本：用 build_query 的结果去检索，并把提示要用的字段带回来。"""
    monkeypatch.setattr(
        kbret,
        "build_query",
        lambda q, h: kbret.QueryPlan(
            "增强后的词", [{"raw": "高数", "canonical": "高等数学"}], True
        ),
    )
    p = _Pipe(DOCS)
    r = _run(
        kbret.retrieve_for_turn(
            uid=OWNER, kb_id=KB, question="高数怎么复习", history=[], top_k=3, pipe=p
        )
    )

    assert p.calls[0][0] == "增强后的词", "没有用改写+增强后的检索词"
    assert r.query == "增强后的词"
    assert r.changes == [{"raw": "高数", "canonical": "高等数学"}]
    assert r.rewritten is True


def test_pipe_injection_skips_pipeline_lookup(monkeypatch):
    """注入 pipe 时不应该再去建/取 pipeline（测试隔离的关键）。"""

    def _boom(*a, **k):
        raise AssertionError("注入 pipe 后不该调用 pipelines.get_or_create")

    monkeypatch.setattr(kbret.pipelines, "get_or_create", _boom)
    r = _run(kbret.retrieve(OWNER, KB, "q", 3, pipe=_Pipe(DOCS)))
    assert not r.is_empty
