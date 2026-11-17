"""一轮问答的检索编排：改写 → 术语增强 → 检索 → 结果整形。

为什么单独抽这一层
------------------
``api/chat.py`` 里图片理解、统一智能体、RAG 三条路径原来各写一遍
「``pipelines.get_or_create`` → 改写/增强 → ``retrieve_with_score`` → 四件整形」，
口径只能靠人工同步——历史上已经踩过「前端调的 top_k 被默认值悄悄覆盖」。

本模块把这一套收成一份，并守住三条边界：

1. **异常原样抛**。三条路径的降级方式本来就不同（图片模式只按图片答、
   智能体无参考资料照答、RAG 中断发 error 帧），服务层不替调用方决定；
2. **只返回数据，不发 SSE**。发帧是表现层的事，这里连 ``_sse`` 都不认识；
3. **不改用户原话**。``build_query`` 只产出「检索用文本」，展示与落库永远用原话。

测试缝
------
``retrieve(..., pipe=...)`` 允许注入一个假 pipeline（与 ``AGENT_HUB_DB`` 同一思路：
默认不传时行为与线上完全一致），这样检索口径第一次可以脱离 Chroma 断言。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from core import config, data_boundary
from kb import pipelines
from kb.rag_core import RagPipeline, assess_grounding, rewrite_query
from service import enhance as enhance_svc


@dataclass(frozen=True)
class QueryPlan:
    """检索前的文本处理结果。``text`` 是检索用的，不是给用户看的。"""

    text: str
    changes: list[dict] = field(default_factory=list)
    rewritten: bool = False


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的全部产物。三条路径各取所需，不再各自重新计算。"""

    docs_scores: list = field(default_factory=list)  # [(Document, distance)]
    refs_md: str = ""  # 落库用（带相似度的参考清单）
    sources: list[dict] = field(default_factory=list)  # 前端引用卡片
    grounding: dict = field(default_factory=dict)  # 依据强度（含 weak）
    references: str = ""  # 拼进 prompt 的【参考资料】正文
    query: str = ""  # 实际检索词（日志 / 提示条）
    changes: list[dict] = field(default_factory=list)  # 术语增强命中
    rewritten: bool = False  # 是否发生过指代改写

    @property
    def is_empty(self) -> bool:
        return not self.docs_scores


def build_query(question: str, history) -> QueryPlan:
    """把用户原话变成「检索用文本」：先补全指代，再追加规范术语。

    **顺序不能反**：``rewrite_query`` 用 LLM 补全指代（"那它呢" → "RRF 有什么缺点"），
    补出来的文本里可能又冒出新的口语词，所以增强必须排在它后面。
    反过来先换词再交给 LLM 改写，LLM 改写完还可能把术语说回口语。

    ★ 返回的是检索用文本，**绝不回写**调用方的 question。
    ★ 增强是**追加**（原话 + 术语）而不是替换：替换在真实短句上每 3 条坏 1 条。
    """
    text = rewrite_query(question, history)
    rewritten = text != question
    if not config.ENHANCE_CHAT_ENABLED:
        # 关掉开关时退回旧行为：只有改写，一个词都不换
        return QueryPlan(text=text, changes=[], rewritten=rewritten)
    enhanced, changes = enhance_svc.enhance_for_retrieval(text)
    return QueryPlan(text=enhanced, changes=changes, rewritten=rewritten)


def references_text(docs_scores, owner: str | None = None, kb_id: str | None = None) -> str:
    """把检索到的片段拼成【参考资料】块的正文。

    RAG 分支与「统一智能体的预检索」共用这一份口径：两处各写一遍的话，
    同一份资料在两条路径里的编号顺序、来源标注迟早会对不上，用户看到的就是
    "同一条问题，引用卡片和正文里的 [1] 指的不是同一段"。

    L0：每条片段按**入库来源**分别用不同标签包住（用户上传 / 联网导入 / 知识库），
    避免三类资料混成一个无边界的大块。标签只在【参考资料】区域内出现；
    调用方传 owner/kb_id 才能拿到来源，取不到时按「知识库」兜底。
    """
    if not docs_scores:
        return ""
    origin_map = data_boundary.kb_origin_map(owner, kb_id)
    blocks = []
    for i, (d, _) in enumerate(docs_scores):
        src = d.metadata.get("source", "?")
        kind = data_boundary.source_for_file(src, origin_map)
        blocks.append(
            data_boundary.wrap(
                kind,
                f"[{i + 1}] （来源：{src}）\n{d.page_content}",
            )
        )
    return "\n\n".join(blocks)


def _shape(
    pipe: RagPipeline, docs: list, *, uid: str, kb_id: str, query: str
) -> RetrievalResult:
    """把一次检索的原始结果整理成三条路径都能直接用的四个产物。

    空结果是常态（库没命中），这里给的是**空产物**而不是 None：
    调用方只需要判 ``is_empty``，不用到处写 ``if docs:`` 再分别取四件东西。
    """
    if not docs:
        return RetrievalResult(query=query)
    return RetrievalResult(
        docs_scores=docs,
        refs_md=pipe.format_refs(docs),
        sources=pipe.build_sources(docs),
        grounding=assess_grounding(docs),
        references=references_text(docs, uid, kb_id),
        query=query,
    )


async def retrieve(
    uid: str,
    kb_id: str,
    query: str,
    top_k: int,
    *,
    pipe: RagPipeline | None = None,
) -> RetrievalResult:
    """按给定检索词取一次结果。``pipe`` 只给测试注入用，线上永远传 None。"""

    def _run() -> RetrievalResult:
        p = pipe or pipelines.get_or_create(uid, kb_id)
        docs = p.retrieve_with_score(query, top_k, uid)
        return _shape(p, docs, uid=uid, kb_id=kb_id, query=query)

    # 检索是同步阻塞（embed 约 250ms + 向量查询），必须离开事件循环
    return await asyncio.to_thread(_run)


async def retrieve_for_turn(
    *,
    uid: str,
    kb_id: str,
    question: str,
    history,
    top_k: int,
    pipe: RagPipeline | None = None,
) -> RetrievalResult:
    """一条链走完：改词 → 增强 → 检索 → 整形。

    给「不需要单独展示改写提示」的路径用（图片理解 / 智能体预检索）。
    RAG 分支要先把提示帧发出去再检索，用 ``build_query`` + ``retrieve`` 分开调。
    """
    plan = await asyncio.to_thread(build_query, question, history)
    result = await retrieve(uid, kb_id, plan.text, top_k, pipe=pipe)
    return replace(result, changes=plan.changes, rewritten=plan.rewritten)
