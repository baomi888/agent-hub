"""对话 → 知识库的沉淀通路（报告 v2 §4.1 P0「开通路」）。

为什么要有这个文件
------------------
用户在对话里讲清楚的业务规则、约束、偏好，此前唯一的去处是上下文压缩块
（`conversations.compressed_summary`，4000 字符预算、会被折叠）——
唯一能永久保真存放知识的知识库，没有从对话进来的门。

本模块补两件事（都不新造能力，全部复用既有组件）：
1. `extract_candidates()`：零 LLM 分流，从会话消息里挑出"值得入库的知识句"，
   分类复用 `core/context_compress.py` 的规则信号（classify_text / salience 四信号）；
2. `build_documents()`：把勾选的候选构造成带溯源 metadata 的 LangChain Document，
   交给 `RagPipeline.add_chunks()`（自动建库 + 向量指纹）入库。

设计红线（报告 §4.1「不做的事」）：
**不做自动入库**。自动入库会把寒暄、试错、错误中间态一起写进去且不可逆污染，
必须人工确认 + 可预览 + 可勾选——所以这里只产"候选清单"，入库由 API 层
（`POST /api/kb/{kb_id}/import-dialogue`）在用户勾选后执行。
"""

from __future__ import annotations

import re

from langchain_core.documents import Document

from core.context_compress import (
    KIND_WEIGHTS,
    classify_text,
    compute_salience,
    jaccard_sim,
    outlier_score,
    singleton_ratio,
)

# 与 extractive_compress 同款切句口径：中英文句末 + 换行
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")

# 纠正抑制的相似度门槛：被纠正的前句与纠正句 Jaccard 超过该值才判"同一件事"
_SUPERSEDE_SIM = 0.4


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _suppress_superseded(cands: list[dict]) -> list[dict]:
    """纠正信号的后处理（报告 §5.2 的第一版实现）。

    纠正句（"不对，我说的是 X"）入库时，同批里与它相似的**更早**句子
    不再入库——否则新旧两个说法都在库里，检索时互相冲突且用户无法分辨。
    跨批次（更早压缩段里已入库的句子）的作废标记依赖检索侧过滤，
    本版不实现（如实标注，见文档"未做"清单）。
    """
    kept: list[dict] = []
    for c in cands:
        if c["kind"] == "纠正":
            for prev in kept:
                if jaccard_sim(prev["text"], c["text"]) >= _SUPERSEDE_SIM:
                    prev["superseded"] = True
        kept.append(c)
    return [c for c in kept if not c.get("superseded")]


def extract_candidates(messages: list[dict]) -> list[dict]:
    """零 LLM 分流：从会话消息里挑出知识候选句。

    判据（报告 §4.3 的规则表，命中任一即候选）：
      - kind != 其它：纠正 / 约束（绝对化或否定）/ 待办 / 偏好 / 目标 / 事实（硬实体）
    "显式记住"在 classify_text 里映射为 kind=事实 + emotion 顶格，天然是候选。

    salience 按全会话语料计算 singleton（"只出现一次但重要"）与离群度。
    返回字段：{text, kind, refs, role, ts, salience}，text 为**原句不改写**
    （抽象会不可逆损失原文，知识库的价值恰恰在可溯源）。
    """
    valid = [
        m
        for m in (messages or [])
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    if not valid:
        return []
    corpus = "\n".join(m.get("content") or "" for m in valid)
    all_texts = [m.get("content") or "" for m in valid]

    out: list[dict] = []
    for m in valid:
        role = m["role"]
        mid = int(m.get("id") or 0)
        ts = int(m.get("created_at") or 0)
        for sent in _split_sentences(m.get("content") or ""):
            kind, emotion = classify_text(sent)
            if kind == "其它":
                continue
            salience = compute_salience(
                kind,
                emotion,
                singleton_ratio(sent, corpus),
                outlier_score(sent, all_texts),
            )
            out.append(
                {
                    "text": sent,
                    "kind": kind,
                    "refs": [mid],
                    "role": role,
                    "ts": ts,
                    "salience": salience,
                }
            )
    return _suppress_superseded(out)


def build_documents(items: list[dict], sid: str, conv_title: str) -> list[Document]:
    """把勾选的候选构造成入库 Document（metadata 全部可溯源）。

    source 形如 `对话·<会话标题>`——沉淀后在新会话检索到时，
    用户能判断这条知识来自哪次对话（报告 §4.1 验收口径）。
    """
    docs: list[Document] = []
    for i, it in enumerate(items):
        text = (it.get("text") or "").strip()
        if not text:
            continue
        refs = [int(r) for r in (it.get("refs") or []) if r]
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "source": f"对话·{conv_title}",
                    "chunk_index": i,
                    "dialogue_sid": sid,
                    "kind": it.get("kind") or "事实",
                    "role": it.get("role") or "user",
                    "ts": int(it.get("ts") or 0),
                    "message_id": refs[0] if refs else 0,
                    "salience": float(it.get("salience") or 0.0),
                },
            )
        )
    return docs


def rank_for_preview(cands: list[dict]) -> list[dict]:
    """给预览列表排序：纠正 > 显式强调 > 类型权重 > salience。

    前端勾选页按这个顺序展示，默认勾选 salience 高于阈值的部分。
    """
    return sorted(
        cands,
        key=lambda c: (
            c.get("kind") == "纠正",
            KIND_WEIGHTS.get(c.get("kind") or "其它", 0.0),
            c.get("salience") or 0.0,
        ),
        reverse=True,
    )


__all__ = ["build_documents", "extract_candidates", "rank_for_preview"]
