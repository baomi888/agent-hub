from __future__ import annotations

"""执行前路由：决定这一轮以工作流起跑，还是交给统一智能体。

判断只有一级
------------
一次轻量 LLM 调用，输出 `workflow` / `agent`：

* `workflow` → 混合引擎 `plan → execute`（执行轨迹照旧上屏）
* `agent`    → 统一智能体，用哪些工具、用几次由模型自己决定

为什么删掉关键词规则层（2026-10-30 决策）
----------------------------------------
规则层是 2026-10-29 的产物：命中「第一步 / 然后 / 批量」等词就直接判
流程型，不问模型。当时的前提是"这类任务的意图写在字面上"，实测不成立：

* 中文不分词，「先生」「优先」「最后一个问题」都会被「先」「最后」按
  子串命中——24 条真实问句里误判 4 条，全是这个原因；
* 规则只认字面，认不出「帮我把这三篇论文对比着列个表」这种没有顺序词、
  但明确要多步执行的任务。

而误判的代价两边都不可接受：多步任务被当闲聊，用户得到一段泛泛而谈；
闲聊被当多步任务，白跑一次规划加一整轮智能体。与其反复调词表和阈值，
不如把判断权交给本来就在读这句话的模型。

`agent` 之所以是兜底而不是 `workflow`
------------------------------------
工作流要多跑一次规划 + 一串节点，兜底时最该省的就是这一段；智能体在
没有任何工具可用时就是一次普通问答，退化成本最低。这也让
`ROUTER_ENABLED=0` 的语义变得干净：关掉路由 = 永远走智能体。

安全边界
--------
路由只接受 `has_kb` / `use_web` 布尔值，绝不接受 `kb_id`。模型没有机会
通过路由影响"查哪个库"，与 Agent 侧 `configurable` 注入是同一条边界。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from core import config

log = logging.getLogger(__name__)

ROUTER_SYSTEM = """你是执行路径分诊器。判断用户这句话要「按顺序做几件事」，还是「一件事就能办完」，只输出 JSON。

可选值：
- workflow：多个子目标、有先后依赖。例如先查资料再整理成表、批量处理一批对象、产出一份有结构的报告。
- agent：一句话能办完，或虽要用工具但只需一步。例如查一份资料、搜一条新闻、算一个数、看天气、解释概念、润色改写。

拿不准就选 agent：多步任务被当单步只是少一层规划，单步被当多步会白跑一整套流程。

只输出一行 JSON，不要解释、不要代码块：
{"route":"workflow|agent","reason":"不超过20字"}"""

_USER_TMPL = "是否有可用知识库：{has_kb}\n是否允许联网：{use_web}\n用户问题：{question}"

_JSON_RE = re.compile(r"\{.*\}", re.S)

# 路由只在这两个值之间选。agent 是兜底，见模块文档最后一段。
VALID_ROUTES = ("workflow", "agent")


@dataclass(frozen=True)
class RouteDecision:
    """一次路由决策。

    route   : workflow（工作流起跑）| agent（统一智能体）
    source  : model（模型判断）| fallback（兜底回落）
    reason  : 服务端日志与排查用，**不下发前端**（沿用 2026-10-29 决定）
    """

    route: str
    source: str
    reason: str = ""


def _fallback_route() -> str:
    """**路由判断失败**（超时 / 网络异常 / 输出格式不对）时的回落：一律 agent。

    为什么不再「有库就 rag」（2026-10-30 改）：

    * agent **自带 `kb_search`**，而 `agent/builder.py` 在 `has_kb` 时会在提示词里写明
      「当前会话已绑定知识库，需要查资料时调用 kb_search」—— 绑库用户走 agent
      **仍然查得到库**，引用能力不会丢；
    * 反过来，rag **只会检索**：问天气、算数、要链接、闲聊时落进 rag 就是答非所问。
      这正是用户反馈「自动模式只能检索」的主因 —— 一旦模型判断慢或出错，
      绑库用户（也就是主力用户）全部被打进检索，且没有任何补救；
    * 两种回落失败的方向不对称：检索不到只有一种结局（空答案），
      而 agent 至少还能换工具、或退回直接作答。

    参数 `has_kb` 已经拿掉（10-30 统一入口改造后路由不再有 rag 分支，回落方向与
    有没有库无关）。关闭应急开关 `ROUTER_ENABLED=0` 走的是同一条路：没有路由 =
    永远走智能体，而绑库时的引用能力由"检索前置"保证，不再依赖 rag 分支。
    """
    return "agent"


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


async def _ask_model(question: str, has_kb: bool, use_web: bool, llm) -> str:
    """一次轻量调用。异常向上抛，由 decide() 统一兜底。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=ROUTER_SYSTEM),
        HumanMessage(
            content=_USER_TMPL.format(
                has_kb=str(bool(has_kb)).lower(),
                use_web=str(bool(use_web)).lower(),
                question=question,
            )
        ),
    ]
    resp = await asyncio.wait_for(llm.ainvoke(messages), timeout=config.ROUTER_TIMEOUT)
    content = getattr(resp, "content", "")
    if isinstance(content, list):
        # 兼容多模态 content list：只取文本段
        content = "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


async def decide(
    question: str,
    *,
    has_kb: bool,
    use_web: bool = False,
    llm=None,
) -> RouteDecision:
    """判断这一轮走工作流还是智能体。任何失败都不抛异常，只降级。

    为什么不让异常穿透：路由只是"选路"，选路失败不该让整个提问失败。
    调用方（`api/chat.py`）是裸 await，这里漏一个异常出去，整个提问就 500，
    而额度往往已经扣了 —— 所以**连"建客户端"都必须在 try 里**：
    `get_llm()` 在缺 key、base_url 写错、被杀软拦截出口时会直接抛。
    """
    if not config.ROUTER_ENABLED:
        return RouteDecision(_fallback_route(), "fallback", "router_disabled")

    try:
        if llm is None:
            from core.llm import get_llm

            llm = get_llm(temperature=0.0, max_tokens=config.ROUTER_MAX_TOKENS)
        raw = await _ask_model(question, has_kb, use_web, llm)
    except TimeoutError:
        log.info("route=fallback reason=timeout has_kb=%s use_web=%s", has_kb, use_web)
        return RouteDecision(_fallback_route(), "fallback", "模型判断超时")
    except Exception as e:  # noqa: BLE001 —— 网络/鉴权/取消都在这里，一律降级
        log.info("route=fallback reason=%s has_kb=%s", type(e).__name__, has_kb)
        return RouteDecision(_fallback_route(), "fallback", f"模型判断失败：{type(e).__name__}")

    parsed = _parse(raw)
    if not parsed:
        log.info("route=fallback reason=bad_format raw=%r", raw[:80])
        return RouteDecision(_fallback_route(), "fallback", "模型输出非约定格式")

    decision = RouteDecision(parsed[0], "model", parsed[1])
    log.info(
        "route=%s source=%s has_kb=%s use_web=%s reason=%s",
        decision.route,
        decision.source,
        has_kb,
        use_web,
        decision.reason,
    )
    return decision


def warmup() -> None:
    """预建路由用的 LLM 客户端（启动时调用一次）。

    为什么值得单独做一次：`decide()` 里的 `from core.llm import get_llm` 是**首次
    导入**，实测 2.56s；构造 ChatOpenAI 再 1.73s。这两笔加起来 4.3s，原本一分不少地
    落在**第一个发起 auto 请求的用户**头上 —— 他比后来者多等 4 秒才看到第一个字。

    ⚠️ 这不是"超时失效"。当时看到首条 5.4s、而 ROUTER_TIMEOUT=2s，一度以为
    `asyncio.wait_for` 没生效；分离计时后确认：模型调用本身 400–950ms，
    `wait_for` 工作正常，慢的全部是导入与构造，而它们发生在 wait_for **外面**。

    失败只告警不抛：没有 key 时构造会异常，但启动不能因此失败 —— 路由自己会回落。
    """
    if not config.ROUTER_ENABLED:
        return
    try:
        from core.llm import get_llm

        # 参数必须与 decide() 里完全一致，否则 _LLM_CACHE 的 key 不同，等于没预热
        get_llm(temperature=0.0, max_tokens=config.ROUTER_MAX_TOKENS)
        log.info("router warmup ok")
    except Exception as e:  # noqa: BLE001 —— 预热失败不影响可用性，只在首次请求时补
        log.warning("router warmup 失败（首次请求会重新尝试）：%s", e)
