"""行为基线（characterization tests）：RAG 核心纯函数与格式化输出。

这一组只测不依赖网络、不依赖 Chroma、不调真 LLM 的部分，作用是给后续的
"等价重构"留下可逐字节比对的凭据：断言里的期望值都是从**重构前的实现**里
逐字抄下来的，不是"设计上更合理"的理想值。哪条优化让它们变了，就说明那条
优化动到了行为，必须回退。

覆盖：RRF 融合、画像偏置重排、候选选择透传、切片边界、主题注入、来源卡片与
参考片段格式化、topic_where。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from langchain_core.documents import Document

import core.config as config
import kb.rag_core as rc


def _doc(text: str, **meta) -> Document:
    m = {"source": "sample.txt"}
    m.update(meta)
    return Document(page_content=text, metadata=m)


def _doc_id(text: str, did: str) -> Document:
    return Document(page_content=text, metadata={"source": "sample.txt"}, id=did)


# ==================== RRF 融合 ====================


def test_rrf_merge_order_dist_and_rank():
    """向量 2 条 + 关键词 2 条（其中 C 只被关键词召回）→ 顺序/距离/归一 rank 固定。"""
    vec_cands = [(_doc_id("A", "a"), 0.1), (_doc_id("B", "b"), 0.2)]
    kw_docs = [_doc_id("B", "b"), _doc_id("C", "c")]
    kw_pairs = [(1, 3.0), (0, 2.0)]  # (kw_docs 下标, bm25 分数)

    out = rc._rrf_merge(vec_cands, kw_pairs, kw_docs, rrf_k=config.HYBRID_RRF_K)

    # B 在两条通道里都出现 → 分最高排第一；A 只走向量、C 只走关键词
    assert [d.page_content for d, _, _ in out] == ["B", "A", "C"]
    # 距离原样带出；C 是关键词独有召回 → distance 为 None（下游必须容忍 None）
    assert [dist for _, dist, _ in out] == [0.2, 0.1, None]
    # rank = score / top_score，top 归一为 1.0；A、C 同为 1/61，A/C 的先后由稳定排序决定
    assert out[0][2] == 1.0
    assert out[1][2] == pytest.approx(31 / 61)
    assert out[2][2] == pytest.approx(31 / 61)


def test_rrf_merge_empty_returns_empty():
    assert rc._rrf_merge([], [], [], rrf_k=config.HYBRID_RRF_K) == []


# ==================== 画像偏置重排（方案 C · D 块）====================


def test_bias_rerank_reorders_hits_and_returns_original_distance():
    """命中主题的片段被减距离提前，但返回给调用方的 distance 仍是原始值。"""
    # 距离刻意排得很近：默认 alpha 只有 0.08（config.RETRIEVAL_BIAS_ALPHA），
    # 拉开距离就观察不到偏置，必须让减距后的值越过邻位才测得出重排
    cands = [
        (_doc("英语片段", topics="|英语|"), 1.0),
        (_doc("数学片段", topics="|数学|"), 1.05),
        (_doc("数学分析片段", topics="|数学分析|"), 1.06),
    ]
    out = rc._bias_rerank(cands, {"数学"}, config.RETRIEVAL_BIAS_ALPHA)

    # 双向包含匹配：|数学| 与 |数学分析| 都算命中「数学」；
    # 减距后 1.05-a=0.97 < 1.06-a=0.98 < 1.0，两条数学片段因此排到英语前面
    assert [d.page_content for d, _ in out] == ["数学片段", "数学分析片段", "英语片段"]
    # 但返回给调用方的仍是**原始** distance（下游换算相似度/阈值不受偏置影响）
    assert [dist for _, dist in out] == [1.05, 1.06, 1.0]


def test_bias_rerank_no_overlap_keeps_order():
    cands = [(_doc("A", topics="|语文|"), 0.5), (_doc("B", topics="|历史|"), 0.9)]
    out = rc._bias_rerank(cands, {"数学"}, config.RETRIEVAL_BIAS_ALPHA)
    assert [(d.page_content, dist) for d, dist in out] == [("A", 0.5), ("B", 0.9)]


def test_bias_rerank_none_distance_keeps_slot_and_does_not_raise():
    """★ 阶段 4 修复后的行为：关键词独有召回（distance=None）不再抛异常。

    修复前这里会 TypeError（"用户有画像标签 + 混合检索命中关键词独有片段"必炸，
    线上是一条静默 500）。现在：该片段既不减距也不挪位，保持原来那一格；
    其余拿到向量距离的条目照常按偏置重排。
    """
    cands = [
        (_doc("仅关键词召回", topics="|数学|"), None),
        (_doc("英语片段", topics="|英语|"), 1.0),
        (_doc("数学片段", topics="|数学|"), 1.05),
    ]
    out = rc._bias_rerank(cands, {"数学"}, config.RETRIEVAL_BIAS_ALPHA)

    assert out[0][0].page_content == "仅关键词召回"
    assert out[0][1] is None
    # 后两格仍是数值距离条目：数学减距后 0.97 < 英语 1.0，于是换位
    assert [d.page_content for d, _ in out[1:]] == ["数学片段", "英语片段"]
    assert [dist for _, dist in out[1:]] == [1.05, 1.0]


# ==================== 候选选择：不足 top_k 时透传 ====================


def test_select_diverse_passthrough_when_candidates_fit():
    cands = [(_doc("A"), 0.1), (_doc("B"), 0.2)]
    out = rc.select_diverse(cands, 3)
    assert [(d.page_content, dist) for d, dist in out] == [("A", 0.1), ("B", 0.2)]


def test_select_diverse_keeps_three_tuple_shape():
    """带 rank 的三元组入参也要能透传，且只吐出 (doc, dist)。"""
    cands = [(_doc("A"), 0.1, 0.9), (_doc("B"), None, 0.8)]
    out = rc.select_diverse(cands, 5)
    assert [(d.page_content, dist) for d, dist in out] == [("A", 0.1), ("B", None)]


# ==================== 切片边界 ====================

_SPLIT_TEXT = (
    "第一段讲的是检索增强生成的基本流程。\n"
    "第二段说明向量数据库如何存储与召回片段。\n"
    "第三段讨论关键词检索与向量检索的差异。\n"
    "第四段给出融合排序的常见做法。\n"
)


def test_split_documents_chunk_shape():
    docs = [_doc(_SPLIT_TEXT)]
    chunks = rc.split_documents(docs, chunk_size=50, chunk_overlap=10)

    # 实际值（重构前逐字抄下）：按 "\n" 分隔符贪心合并到 ≤50 字，恰好切两片；
    # 分隔符留在前一片末尾，overlap 在这条路径上不产生前缀回补
    assert [c.page_content for c in chunks] == [
        "第一段讲的是检索增强生成的基本流程。\n第二段说明向量数据库如何存储与召回片段。",
        "第三段讨论关键词检索与向量检索的差异。\n第四段给出融合排序的常见做法。",
    ]
    # 切片不改 metadata，source 原样带过去
    assert all(c.metadata["source"] == "sample.txt" for c in chunks)


# ==================== 主题注入（方案 C · A 块）====================


def test_inject_topics_groups_by_source_and_truncates_preview():
    """每个来源只抽一次标签，且喂给 LLM 的预览是"该来源切片全文拼接后的前 4000 字"。"""
    seen: list[str] = []

    def _fake_extract(text):
        seen.append(text)
        return ["线性代数", "考研数学"]

    saved = rc._extract_topics_one
    saved_flag = config.TOPIC_EXTRACT_ENABLED
    rc._extract_topics_one = _fake_extract
    config.TOPIC_EXTRACT_ENABLED = True
    try:
        long_doc = _doc("甲" * 5000, source="长文档.txt")
        other = _doc("乙" * 10, source="短文档.txt")
        chunks = [long_doc, other]

        rc._inject_topics(chunks)

        # 两个来源各抽一次；长文档那一份恰好被截到 4000 字
        assert sorted(len(t) for t in seen) == [10, 4000]
        assert long_doc.metadata["topics"] == "|线性代数|考研数学|"
        assert other.metadata["topics"] == "|线性代数|考研数学|"
    finally:
        rc._extract_topics_one = saved
        config.TOPIC_EXTRACT_ENABLED = saved_flag


def test_inject_topics_skips_when_disabled_or_empty():
    saved_flag = config.TOPIC_EXTRACT_ENABLED
    config.TOPIC_EXTRACT_ENABLED = False
    try:
        c = _doc("内容")
        rc._inject_topics([c])
        assert "topics" not in c.metadata
        config.TOPIC_EXTRACT_ENABLED = True
        rc._inject_topics([])  # 空列表不炸
    finally:
        config.TOPIC_EXTRACT_ENABLED = saved_flag


# ==================== topic_where ====================


def test_topic_where_literal():
    assert rc.topic_where("数学") == {"topics": {"$contains": "|数学|"}}
    assert rc.topic_where(" |数学| ") == {"topics": {"$contains": "|数学|"}}
    assert rc.topic_where("") == {}
    assert rc.topic_where(None) == {}


# ==================== 来源卡片 / 参考片段格式化 ====================

_CARDS_INPUT = [
    (_doc("强相关片段内容", source="资料.txt", page=2), 0.4),
    (_doc("沾边片段内容", source="资料.txt"), 1.4),
    (_doc("不沾边片段内容", source="别的.txt"), 1.9),
]


def test_build_sources_exact_shape():
    out = rc.RagPipeline.build_sources(_CARDS_INPUT)
    assert out[0] == {
        "id": 1,
        "source": "资料.txt",
        "page": 3,
        "score": 0.8,
        "strength": "high",
        "preview": "强相关片段内容",
        "text": "强相关片段内容",
    }
    assert [c["strength"] for c in out] == ["high", "medium", "low"]
    # page 缺失时给 None（而不是 0），前端据此隐藏页码；相似度换算不了时给 None
    assert out[1]["page"] is None
    assert all(c["id"] == i + 1 for i, c in enumerate(out))


def test_format_refs_exact_string():
    out = rc.RagPipeline._format_refs(_CARDS_INPUT)
    assert out == (
        "**[1]** 资料.txt 第3页 ｜ score=0.800 ｜ 强相关片段内容...\n\n"
        "**[2]** 资料.txt ｜ score=0.300 ｜ 沾边片段内容...\n\n"
        "**[3]** 别的.txt ｜ score=0.050 ｜ 不沾边片段内容..."
    )


def test_format_refs_empty_literal():
    assert rc.RagPipeline._format_refs([]) == "（无检索结果）"
