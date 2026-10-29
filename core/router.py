from __future__ import annotations

"""执行前路由：`auto` 模式下决定这一轮走哪条路。

为什么需要它
------------
改造前 `auto` 只做一件事：有没有绑定知识库 → `rag` / `agent`，而「混合」
只能由用户手动点。实测下来「让用户选模式」这件事本身没有价值：用户不知道
该选哪个，选错还要自己承担后果。所以模式入口收敛成「自动」，由系统判断。

判断分两级
----------
1. **规则（高精度，宁可漏判）**：命中顺序/批量特征 → 直接以工作流起跑。
   规则**不问模型**，因为这类问题的答案本来就写在字面上。
2. **模型（兜底）**：一次轻量调用，输出 `rag` / `agent` / `llm`。

规则为什么不追求召回
--------------------
误判的代价不对称：

* 漏判（该走工作流却去问模型）→ 多一次小调用，几百毫秒；
* 误判（该问模型却走工作流）→ 多一次 LLM 规划 + 智能体执行，几秒 + 数倍 token。

所以规则只认强特征，拿不准就交出去。这一点是 2026-10-29 明确的设计取向
（"规则偏高精度、宁可漏判给模型"）。

安全边界
--------
路由**只接受 `has_kb` 布尔值**，绝不接受 `kb_id`。模型没有机会通过路由
影响"查哪个库"，与 Agent 侧 `configurable` 注入是同一条边界。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from core import config

log = logging.getLogger(__name__)

# ==================== 一、规则层 ====================

# 强特征（每个 2 分）：几乎只出现在"要你去做"的多步指令里
_STRONG = (
    "第一步",
    "第二步",
    "第三步",
    "依次",
    "批量",
    "并保存",
    "并下载",
    "然后",
    "接着",
)

# 中特征（每个 1 分）：常见于任务描述，但也常出现在"问某个流程"的疑问句里，
# 所以只给 1 分——单独命中不足以判 flow（阈值是 2 分）。
#   · "步骤" / "流程" 是名词：「这个算法的步骤有哪些」「流程是怎样的」是在问知识，
#     不是要你去执行，给 2 分会让这类问题凭空背上工作流壳（实测误判）。
#   · "汇总 / 生成报告 / 制成清单" 是交付物描述。
_MID = ("步骤", "流程", "汇总", "生成报告", "制成清单")

# 顺序配对：**「先」单独出现不算**。中文不分词，"优先 / 先生 / 先前"
# 都会被 "先" 子串命中——实测 24 条真实问题里，4 条误判全是这个原因。
# 只有与下面任一顺序词同时出现，才说明它是"先……再……"的句式。
_SEQ = ("然后", "接着", "最后", "再")

# 开放型（减分项）：问句特征，命中说明更可能是"解释/比较"而不是"执行"
_OPEN = (
    "调研",
    "研究",
    "分析",
    "比较",
    "怎么看",
    "如何",
    "为什么",
    "哪些",
    "建议",
    "聊聊",
    "介绍",
    "解释",
    "探索",
)

# 判定阈值：总分达到 2 分且高于开放分才算 flow。
# 定成 2 而不是 1，是因为 1 分档只有"汇总/生成报告"这类弱特征，
# 单凭一个词就背上工作流壳不划算。
FLOW_MIN_SCORE = 2


def score_flow(question: str) -> tuple[int, int]:
    """返回 (流程分, 开放分)。抽成独立函数是为了让"规则到底是什么"可测可看。"""
    q = question or ""
    score = sum(2 for w in _STRONG if w in q) + sum(1 for w in _MID if w in q)
    if "先" in q and any(w in q for w in _SEQ):
        score += 2
    open_score = sum(1 for w in _OPEN if w in q)
    return score, open_score


def classify_flow_open(question: str) -> str:
    """`flow` = 以工作流起跑；`open` = 交给模型判断。

    持平或都不命中归 `open`：闲聊不该背流程壳（沿用改造前的取向）。
    """
    score, open_score = score_flow(question)
    return "flow" if score >= FLOW_MIN_SCORE and score > open_score else "open"


# ==================== 二、模型判断层 ====================

_ROUTER_SYSTEM = """你是执行路径分诊器。根据用户问题，从三种执行方式里选一个，只输出 JSON。

可选值：
- rag：问题指向"我的资料 / 我上传的 / 这份笔记 / 之前那份文档"，需要从知识库检索
- agent：需要实时信息、联网搜索、数学计算、天气、下载文件
- llm：寒暄、身份、常识、写作、翻译、润色，既不需要外部信息也不需要工具

判断不了时优先 agent：宁可多调一次工具，也不要凭空作答。

只输出一行 JSON，不要解释、不要代码块：
{"route":"rag|agent|llm","reason":"不超过20字"}"""

_USER_TMPL = "是否有可用知识库：{has_kb}\n用户问题：{question}"

_JSON_RE = re.compile(r"\{.*\}", re.S)
VALID_ROUTES = ("rag", "agent", "llm")


@dataclass(frozen=True)
class RouteDecision:
    """一次路由决策。

    route   : flow（工作流起跑）| rag | agent | llm
    source  : rule（规则命中）| model（模型判断）| fallback（兜底回落）
    reason  : 服务端日志与排查用，**不下发前端**（用户 2026-10-29 决定不展示）
    """

    route: str
    source: str
    reason: str = ""


def _fallback_route(has_kb: bool) -> str:
    """任何异常下的回落：有库先查库，没库放开工具。"""
    return "rag" if has_kb else "agent"


def _parse(raw: str) -> tuple[str, str] | None:
    """从模型输出里抠出 {route, reason}。非约定格式一律返回 None 交给兜底。"""
    match = _JSON_RE.search(raw or "")
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    route = str(obj.get("route", "")).strip().lower()
    if route not in VALID_ROUTES:
        return None
    return route, str(obj.get("reason", ""))[:40]


def _post_check(route: str, reason: str, has_kb: bool) -> RouteDecision:
    """模型结果的两条硬约束（与提示词无关，代码里兜住）。"""
    if route == "rag" and not has_kb:
        # 没有可用知识库却要检索：必然检索不到，直接改判 agent
        return RouteDecision("agent", "model", "无可用知识库，rag 改判 agent")
    if route == "llm" and has_kb:
        # 绑了库一律给引用（2026-10-29 决定："你好"也去检索也无所谓）。
        # 代价是闲聊会慢一点，收益是"绑库却无引用"这件事从结构上不会发生。
        return RouteDecision("rag", "model", "绑库强制检索（原判 llm）")
    return RouteDecision(route, "model", reason)


async def _ask_model(question: str, has_kb: bool, llm) -> str:
    """一次轻量调用。异常向上抛，由 decide() 统一兜底。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=_ROUTER_SYSTEM),
        HumanMessage(content=_USER_TMPL.format(has_kb=str(has_kb).lower(), question=question)),
    ]
    resp = await asyncio.wait_for(llm.ainvoke(messages), timeout=config.ROUTER_TIMEOUT)
    content = getattr(resp, "content", "")
    if isinstance(content, list):
        # 兼容多模态 content list：只取文本段
        content = "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


async def decide(question: str, *, has_kb: bool, llm=None) -> RouteDecision:
    """auto 模式的唯一入口。任何失败都不抛异常，只降级。

    为什么不让异常穿透：路由只是"选路"，选路失败不该让整个提问失败。
    最坏情况退回改造前的行为（有库 rag / 无库 agent），用户无感。
    """
    if not config.ROUTER_ENABLED:
        return RouteDecision(_fallback_route(has_kb), "fallback", "router_disabled")

    # 一级：规则。命中就不问模型（"规则直接走工作流"）
    if classify_flow_open(question) == "flow":
        score, open_score = score_flow(question)
        return RouteDecision("flow", "rule", f"规则命中（流程分 {score} / 开放分 {open_score}）")

    # 二级：模型
    if llm is None:
        from core.llm import get_llm

        llm = get_llm(temperature=0.0, max_tokens=config.ROUTER_MAX_TOKENS)

    try:
        raw = await _ask_model(question, has_kb, llm)
    except TimeoutError:
        log.info("route=fallback reason=timeout has_kb=%s", has_kb)
        return RouteDecision(_fallback_route(has_kb), "fallback", "模型判断超时")
    except Exception as e:  # noqa: BLE001 —— 网络/鉴权/取消都在这里，一律降级
        log.info("route=fallback reason=%s has_kb=%s", type(e).__name__, has_kb)
        return RouteDecision(_fallback_route(has_kb), "fallback", f"模型判断失败：{type(e).__name__}")

    parsed = _parse(raw)
    if not parsed:
        log.info("route=fallback reason=bad_format has_kb=%s raw=%r", has_kb, raw[:80])
        return RouteDecision(_fallback_route(has_kb), "fallback", "模型输出非约定格式")

    decision = _post_check(parsed[0], parsed[1], has_kb)
    log.info(
        "route=%s source=%s has_kb=%s reason=%s",
        decision.route,
        decision.source,
        has_kb,
        decision.reason,
    )
    return decision
