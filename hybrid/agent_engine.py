"""Agent 引擎：把 build_agent() 包装进混合架构的执行引擎契约（要求 3）。

复用点（与 chat.py agent 分支同一套，理由见原文件）：
  - build_agent(has_kb, location) 带编译缓存的集合式 Agent（5 工具）；
  - astream(stream_mode=["updates","messages"]) 事件解析；
  - owner_id/kb_id 走 RunnableConfig.configurable 注入，模型改不了。

混合架构加的护栏（三重预算，先到先触发收编）：
  1. 步数：recursion_limit = HYBRID_AGENT_MAX_STEPS（LangGraph 硬限制，
     超了抛 GraphRecursionError，这里接住转成 budget_exhausted 信号）；
  2. 时长：每段 monotonic 截止线，超时停止流并上报 budget_exhausted；
  3. token：chars//2 近似折算（比起真实 tokenizer 零开销，量级够用），
     消耗的是 token 事件 + 工具返回的字符数。

环境感知与自主决策调整是 Agent 框架自带的（ReAct 循环 + 工具结果回灌）；
这里额外记"工具连续失败计数"，连续失败说明模型在死磕，上报 agent_stuck
让编排器终止而不是烧穿预算。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core import config
from core.urltools import extract_urls
from hybrid.context import ExecEvent, TaskContext
from hybrid.engine import ControlFn, EngineResult

# 连续工具失败多少次算"卡死"：工具返回不可用后模型换个参数再试是合理的，
# 第三次还失败基本就是死循环，不如止损
_MAX_CONSEC_FAILURES = 3
# 判定工具失败的返回内容关键词（与 tools.py 的失败文案口径一致）
_FAIL_HINTS = ("不可用", "失败", "无法", "缺少", "错误")


def history_items(ctx: TaskContext) -> list[dict]:
    """取对话历史（跨轮记忆，来自 ctx.shared["history"]）。

    条数上限由 config.HYBRID_HISTORY_MAX_MESSAGES 控制（取最近 N 条）；
    <=0 表示关闭历史注入，退回旧的无记忆行为（出问题可一键回退）。
    """
    if config.HYBRID_HISTORY_MAX_MESSAGES <= 0:
        return []
    hist = ctx.shared.get("history") or []
    if not isinstance(hist, list):
        return []
    items = [
        m
        for m in hist
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return items[-config.HYBRID_HISTORY_MAX_MESSAGES :]


def history_messages(ctx: TaskContext) -> list:
    """对话历史 → LangChain 消息列表（Human/AI）。无历史返回 []。

    单条内容与 history_prefix 对齐做 [:500] 截断：历史上限放宽到 60 条后，无截断的
    agent 节点会把同一份历史送进去 2 次（工作流每个 llm 节点前缀一次 + agent 节点再注一次），
    贴着 64K 线跑。这里统一用同一个上限，避免越界。
    """
    out: list = []
    for m in history_items(ctx):
        content = str(m["content"])[:500]
        out.append(
            HumanMessage(content=content) if m["role"] == "user" else AIMessage(content=content)
        )
    return out


def history_prefix(ctx: TaskContext) -> str:
    """对话历史 → 文本前缀（供工作流 LLM 节点把上下文拼进 prompt）。无历史返回 ""。"""
    items = history_items(ctx)
    if not items:
        return ""
    lines = ["【对话历史（供理解上下文，勿复述）】"]
    for m in items:
        role = "用户" if m["role"] == "user" else "助手"
        lines.append(f"{role}：{str(m['content'])[:500]}")
    return "\n".join(lines) + "\n\n"


class AgentEngine:
    """一次 Agent 执行段。实例随段创建（result 挂在实例上）。"""

    name = "agent"

    def __init__(
        self,
        has_kb: bool = False,
        location: dict | None = None,
        agent: Any = None,
        activated: frozenset[str] = frozenset(),
    ):
        self._has_kb = has_kb
        self._location = location
        self._agent = agent  # 测试注入假 agent；None 时惰性 build_agent
        # 本用户已激活的延迟工具集（#7）：与 api/chat.py 的 Agent 路径同一来源
        # （tool_registry.activated_for(uid)，请求开始取定、整轮不变）。
        # 不传的话 hybrid 段 build_agent 拿到空集 → 已激活的延迟工具不进 bind_tools，
        # 甲用户明明 discover 过，混合模式里却"没学过"。
        self._activated = activated
        self.result: EngineResult | None = None

    async def _get_agent(self) -> Any:
        if self._agent is None:
            import asyncio

            from agent.builder import build_agent

            # 编译是 CPU 活，放线程里别堵事件循环
            self._agent = await asyncio.to_thread(
                build_agent, self._has_kb, self._location, None, self._activated
            )
        return self._agent

    async def stream(
        self,
        ctx: TaskContext,
        control: ControlFn,
        goal: str | None = None,
        output_key: str | None = None,
        node_id: str | None = None,
        **_: Any,
    ) -> AsyncIterator[ExecEvent]:
        goal = goal or ctx.goal
        key = output_key or "agent_result"
        st = ctx.agent_state
        st.setdefault("steps", 0)
        st.setdefault("chars", 0)
        st.setdefault("consec_failures", 0)
        st["started_ts"] = int(time.time())
        deadline = time.monotonic() + config.HYBRID_AGENT_MAX_SECONDS

        full_text = ""
        try:
            agent = await self._get_agent()
            config_ = {
                "recursion_limit": config.HYBRID_AGENT_MAX_STEPS,
                "configurable": {
                    # 每个执行段独立 thread：混合模式下 Agent 段是"子任务"，
                    # 不该继承别段的中间推理状态
                    "thread_id": f"hybrid-{ctx.run_id}-{len(ctx.mode_trace)}",
                    "owner_id": ctx.owner_id,
                    "kb_id": st.get("kb_id"),
                    # 与 api/chat.py 的 agent 分支同一套下载闸门：
                    # 只允许下载本段搜索返回的链接或用户目标里原样出现的链接。
                    "download_allowlist": [],
                    "user_urls": extract_urls(goal) + extract_urls(ctx.goal),
                },
            }
            # 跨轮记忆：把历史消息前置到本轮 goal 之前（历史为空则退化为单轮）。
            # thread_id 仍是 per-run（段隔离），跨轮上下文靠显式注入 messages 实现。
            msgs = history_messages(ctx)
            msgs.append(HumanMessage(content=goal))
            agen = agent.astream(
                {"messages": msgs},
                config=config_,
                stream_mode=["updates", "messages"],
            )
            try:
                async for event in agen:
                    # 人工指令与预算检查都在事件边界做（事件足够频繁，粒度够用）
                    if control():
                        self.result = EngineResult(status="switched", signal="manual")
                        return
                    if time.monotonic() > deadline:
                        self.result = EngineResult(
                            status="switched",
                            signal="budget_exhausted",
                            error=f"Agent 时长预算耗尽（>{config.HYBRID_AGENT_MAX_SECONDS}s）",
                        )
                        return
                    if st["chars"] // 2 >= config.HYBRID_AGENT_TOKEN_BUDGET:
                        self.result = EngineResult(
                            status="switched",
                            signal="budget_exhausted",
                            error=f"Agent token 预算耗尽（>{config.HYBRID_AGENT_TOKEN_BUDGET}）",
                        )
                        return

                    if not isinstance(event, tuple) or len(event) != 2:
                        continue
                    mode_name, payload = event

                    if mode_name == "messages":
                        chunk, _meta = payload
                        delta = getattr(chunk, "content", "")
                        if isinstance(delta, list):  # 多模态 content list 只取文本
                            delta = "".join(
                                p.get("text", "")
                                for p in delta
                                if isinstance(p, dict) and p.get("type") == "text"
                            )
                        if isinstance(delta, str) and delta:
                            full_text += delta
                            st["chars"] += len(delta)
                            yield ExecEvent(
                                event_type="token",
                                mode="agent",
                                node_id=node_id,
                                payload={"text": delta},
                            )

                    elif mode_name == "updates" and isinstance(payload, dict):
                        for node_output in payload.values():
                            if not (isinstance(node_output, dict) and "messages" in node_output):
                                continue
                            for msg in node_output["messages"]:
                                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                                    st["steps"] += 1
                                    for tc in msg.tool_calls:
                                        yield ExecEvent(
                                            event_type="tool_call",
                                            mode="agent",
                                            node_id=node_id,
                                            payload={
                                                "name": tc.get("name", "?"),
                                                "input": str(tc.get("args", {}))[:200],
                                            },
                                        )
                                elif isinstance(msg, ToolMessage):
                                    content = str(getattr(msg, "content", ""))
                                    st["chars"] += len(content)
                                    failed = any(h in content for h in _FAIL_HINTS)
                                    st["consec_failures"] = (
                                        st["consec_failures"] + 1 if failed else 0
                                    )
                                    yield ExecEvent(
                                        event_type="tool_call",
                                        mode="agent",
                                        node_id=node_id,
                                        payload={
                                            "name": getattr(msg, "name", "?"),
                                            "output": content[:300],
                                        },
                                    )
                                    if st["consec_failures"] >= _MAX_CONSEC_FAILURES:
                                        self.result = EngineResult(
                                            status="failed",
                                            signal="agent_stuck",
                                            error=(
                                                f"工具连续 {_MAX_CONSEC_FAILURES} 次失败，"
                                                "Agent 疑似死磕"
                                            ),
                                        )
                                        return
            finally:
                aclose = getattr(agen, "aclose", None)
                if aclose is not None:
                    await aclose()

        except Exception as e:
            # GraphRecursionError = 步数预算撞线（LangGraph 硬限制），转收编信号
            if type(e).__name__ == "GraphRecursionError":
                ctx.shared[key] = full_text
                self.result = EngineResult(
                    status="switched",
                    signal="budget_exhausted",
                    error=f"Agent 步数预算耗尽（>{config.HYBRID_AGENT_MAX_STEPS} 步）",
                    output_key=key,
                )
                return
            ctx.shared[key] = full_text
            self.result = EngineResult(
                status="failed", error=f"Agent 执行失败：{type(e).__name__}: {e}"
            )
            return

        # 正常跑完：产出落 shared，上报 goal_satisfied（编排器决定收编还是收工）。
        # 直接赋值而非 shared_set：产出全文要能被下游模板逐字读取，不能落盘成指针
        # （理由同 hybrid/workflow.py::_exec_llm）
        ctx.shared[key] = full_text
        self.result = EngineResult(status="completed", signal="goal_satisfied", output_key=key)
