"""建库建议：一句话回答「用户刚问完这一轮，要不要把这些东西存成一个库」。

两个触发场景（10-30 方案 D7 / D11）：

| source              | 条件                                             | 用户看到的理由                    |
|---------------------|--------------------------------------------------|-----------------------------------|
| `no_kb_retrieval`   | 开了「检索」但会话没绑库 —— 检索等于空转          | 这个会话还没绑库，查了也用不上     |
| `web_search`        | 这轮联网确实查到了外部信息                        | 本轮联网收集了 N 条外部信息        |

**为什么是"建议"而不是自动建库**：建库是可见的副作用（资料库里会多一个库），
而且一旦写进去就不可逆地影响之后的检索结果。自动做 = 用户某天发现资料库被
塞满不知道哪来的库。所以 D10 的自动沉淀只做「压缩时把知识句沉淀进**已有**的
主题库」，而"新开一个库"必须用户点一下确认。

这个模块只负责**生成**建议（落库 + 返回 SSE 数据），不负责执行 ——
执行在 `api/kb_proposals.py::confirm`（确认制），两边共用
`service.sediment.ingest_candidates` 这一条入库实现。
"""

from __future__ import annotations

import time

from core import config, sediment_store


def _count_candidates(owner: str, sid: str) -> int:
    """这段会话里能沉淀几块内容。**只数不入库**。

    走的是和手工沉淀完全相同的抽取器（`extract_blocks`：用户按句、助手按
    markdown 结构成块），所以卡片上写的"12 条"和点确认后实际进库的条数是
    同一个口径 —— 两套口径会让用户觉得"说 12 条怎么只进去 3 条"。
    """
    from core import session as store
    from kb import dialogue_import

    try:
        msgs = store.get_messages(owner, sid, limit_turns=None, include_system=False)
        return len(dialogue_import.extract_blocks(msgs))
    except Exception:  # noqa: BLE001
        return 0


def card_of(prop: dict) -> dict:
    """把一条建议行整成前端卡片要的形状。

    ★ SSE 那一帧和「刷新后恢复」用的必须是**同一个函数**产出的同一个形状。
      各写一份的话，用户会看到"刚问完显示可入库 9 条，刷一下就变成 2 条"，
      而这种不一致永远查不出来 —— 两边的单测都是绿的。
    """
    payload = prop.get("payload") or {}
    topic = prop.get("topic") or ""
    source = prop.get("source") or ""
    conv_n = int(payload.get("conv_candidates") or 0)
    web_n = len(payload.get("web_sources") or [])
    if source == "web_search":
        candidates = conv_n + web_n
        reason = f"本轮联网收集了 {web_n} 条外部信息"
    else:
        candidates = conv_n
        reason = "这个会话还没绑资料库，检索查不到东西"
    return {
        "proposal_id": prop.get("proposal_id", ""),
        "topic": topic,
        "source": source,
        "candidates": candidates,
        "kb_hint": f"对话沉淀·{topic}",
        "expires_at": int(prop.get("expires_at") or 0),
        "reason": reason[:40],
        # 前端靠它对上"该挂在哪条回答下面"：刷新后消息是重新拉的，
        # 没有这个 id 就只能瞎挂（挂最后一条 = 挂错人）
        "answer_message_id": int(payload.get("answer_message_id") or 0),
    }


def maybe_propose(
    owner: str,
    sid: str,
    *,
    use_kb: bool,
    use_web: bool,
    kb_bound: bool,
    web_sources: list[dict] | None = None,
    answer_message_id: int | None = None,
) -> dict | None:
    """生成一条建库建议。返回 SSE `kb_proposal` 的 data；None = 这轮不建议。

    调用方（`api/chat.py`）只需要在 `done` 之前调一次，有返回值就发一帧。

    参数都是"这轮发生了什么"，函数自己不回头看请求：
    - `use_kb` / `use_web`：能力开关的实际值（不是用户界面上的意图）；
    - `kb_bound`：会话当前有没有绑定库 —— 已绑库还建议建库是骚扰；
    - `web_sources`：本轮真实的联网来源，空列表等于没联网（这是**事实**，
      不是能力开关：开了联网但没搜到东西时不该建议沉淀）。
    """
    if not getattr(config, "SEDIMENT_ENABLED", True):
        return None

    prefs = sediment_store.get_prefs(owner)
    sources = [s for s in (web_sources or []) if isinstance(s, dict) and (s.get("link") or s.get("title"))]

    source = ""
    if use_kb and not kb_bound and prefs.get("auto_create_kb", True):
        # 优先报"检索用不上"：这是功能缺口（用户以为在查资料库，其实没库），
        # 比"顺便把这轮存下来"更该被处理。
        source = "no_kb_retrieval"
    elif use_web and sources:
        source = "web_search"
    if not source:
        return None

    # 冷却：同会话同来源 10 分钟内只发一次。没有它，用户每问一句都弹一次卡，
    # 弹到第三次就变成"这个产品老想让我建库"。
    prev = sediment_store.pending_proposal(owner, sid, source)
    if prev:
        age_min = (time.time() - int(prev.get("created_at") or 0)) / 60
        if age_min < config.PROPOSAL_COOLDOWN_MIN:
            return None

    topic = sediment_store.topic_of(owner, sid)
    conv_n = _count_candidates(owner, sid)
    # 条数只用来判"值不值得建议"；卡片上那份文案由 card_of 统一给出，
    # 不在这里另写一遍（写两遍就会在"刷新恢复"时出现两套措辞）
    candidates = conv_n if source == "no_kb_retrieval" else conv_n + len(sources)

    if candidates <= 0:
        # 一条都抽不出来（寒暄、追问）就不提建议 —— 建一个空库比不建更糟
        return None

    proposal = sediment_store.create_proposal(
        owner,
        sid,
        topic,
        source,
        {
            "conv_candidates": conv_n,
            "web_sources": sources,
            "answer_message_id": int(answer_message_id or 0),
            "sid": sid,
            "use_kb": bool(use_kb),
            "use_web": bool(use_web),
        },
    )

    return card_of(proposal)


__all__ = ["maybe_propose", "card_of"]
