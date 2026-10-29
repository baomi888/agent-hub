"""工作流引擎（要求 2：节点定义 / 条件分支 / 超时控制 / 重试 / 失败跳转）。

执行模型：
  - WorkflowDef 是静态定义（节点 + 边），TaskContext.workflow_state 是动态状态
    （next_node 断点锚点 / completed / node_outputs），两者分离才能断点续跑；
  - 节点是 async generator，执行中产出 ExecEvent（node_enter/node_exit/token/tool_call）；
  - 超时 = asyncio.wait_for 包住每次事件拉取（剩余时间做 timeout），
    节点整体超时而不是单次 I/O 超时；
  - 失败路径固定：先节点内 retry → 再 on_fail 跳转 → 都没有才上报
    signal="node_failed" 给编排器做切换判定（降级接管）。

与 Agent 的融合有两条通道：
  1. agent 类型节点：流程内直接内嵌一次 Agent 执行（一等公民，不走切换规则）；
  2. requires_agent=True 的普通节点：引擎跑到这里主动让出
     （EngineResult.switched + signal="requires_agent"），Agent 接管后回来
     从该节点的**出边**继续（收编回归，不重跑该节点）。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from core import config, tool_guard
from core.tool_registry import TOOL_META
from core.urltools import extract_urls
from hybrid.context import ExecEvent, TaskContext, shared_set
from hybrid.engine import AgentEngineFactory, ControlFn, EngineResult
from hybrid.expr import eval_condition


class _Suspend(Exception):
    """人工节点挂起信号：用异常穿透 _run_with_retry，避免被当成节点失败。

    为什么需要专门一条通道：`_run_with_retry` 把节点抛的异常统一转成 error 事件，
    而"挂起"不是失败——它是正常让出。用异常穿透后由 stream() 显式接住。
    """

    def __init__(self, node_id: str, next_node: str | None) -> None:
        self.node_id = node_id
        self.next_node = next_node
        super().__init__(f"人工节点 {node_id} 挂起")


# ==================== 定义模型 ====================


class NodeDef(BaseModel):
    id: str
    type: Literal["llm", "tool", "agent", "condition", "human"]
    name: str = ""  # 展示名，前端拓扑图用；空则显示 id
    config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None  # None 用全局默认 HYBRID_NODE_TIMEOUT
    retry: int = 0  # 节点内重试次数（先重试再谈失败）
    on_fail: str | None = None  # 失败跳转节点 id；None 才走切换规则
    requires_agent: bool = False  # 执行到此节点时让出给 Agent 模式


class EdgeDef(BaseModel):
    source: str
    target: str
    condition: str | None = None  # 对 shared 的受限表达式；None = 默认兜底边


class WorkflowDef(BaseModel):
    id: str = ""
    name: str = "未命名工作流"
    version: int = 1
    entry: str | None = None  # 入口节点 id；None = nodes 第一个
    nodes: list[NodeDef]
    edges: list[EdgeDef] = Field(default_factory=list)
    timeout_seconds: int = 0  # 整体超时；0 = 用全局默认 HYBRID_WORKFLOW_TIMEOUT

    def node_map(self) -> dict[str, NodeDef]:
        return {n.id: n for n in self.nodes}

    def entry_id(self) -> str:
        return self.entry or self.nodes[0].id


def validate_definition(data: dict) -> WorkflowDef:
    """把 dict 校验成 WorkflowDef（API 层落库前必过这道）。

    除了 pydantic 结构校验，还要查图完整性：
    id 唯一、边/失败跳转指向存在的节点、入口存在——这些错了运行期才炸就晚了。
    """
    try:
        wf = WorkflowDef.model_validate(data)
    except Exception as e:
        raise ValueError(f"工作流定义结构非法：{e}") from e
    if not wf.nodes:
        raise ValueError("工作流至少需要一个节点")
    ids = [n.id for n in wf.nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("节点 id 重复")
    known = set(ids)
    for e in wf.edges:
        if e.source not in known or e.target not in known:
            raise ValueError(f"边 {e.source} -> {e.target} 指向不存在的节点")
    for n in wf.nodes:
        if n.on_fail and n.on_fail not in known:
            raise ValueError(f"节点 {n.id} 的 on_fail 指向不存在的节点 {n.on_fail}")
    if wf.entry and wf.entry not in known:
        raise ValueError(f"入口节点 {wf.entry} 不存在")
    return wf


# ==================== 模板渲染 ====================

_TEMPLATE_RE = re.compile(r"\{(question|shared\.([a-zA-Z0-9_\.]+))\}")


def render_template(template: str, ctx: TaskContext) -> str:
    """渲染 {question} 与 {shared.xxx} 占位符。

    不用 str.format：模板来自用户定义，format 遇到未知占位符直接 KeyError，
    而且 format spec 有属性访问注入面。正则只认这两类，缺失的 shared key
    渲染为空串（宁可空着也不让流程炸掉）。
    """

    def _sub(m: re.Match) -> str:
        if m.group(1) == "question":
            return ctx.goal
        cur: Any = ctx.shared
        for part in m.group(2).split("."):
            if not isinstance(cur, dict):
                return ""
            cur = cur.get(part)
            if cur is None:
                return ""
        return str(cur)

    return _TEMPLATE_RE.sub(_sub, template)


# ==================== 工作流引擎 ====================


class WorkflowEngine:
    """一次工作流执行。实例随执行段创建（result 挂在实例上，不共享状态）。"""

    name = "workflow"

    def __init__(
        self,
        wf: WorkflowDef,
        llm: Any = None,
        agent_engine_factory: AgentEngineFactory | None = None,
    ):
        self.wf = wf
        self._llm = llm  # 测试注入假 LLM；None 时惰性取真客户端
        self._agent_factory = agent_engine_factory
        self.result: EngineResult | None = None

    def _get_llm(self) -> Any:
        if self._llm is None:
            from core.llm import get_llm

            # 内部推理调用必须显式 max_tokens（project memory 硬约束）
            self._llm = get_llm(temperature=0.3, max_tokens=config.HYBRID_LLM_MAX_TOKENS)
        return self._llm

    # ---------- 主循环 ----------

    async def stream(
        self, ctx: TaskContext, control: ControlFn, **_: Any
    ) -> AsyncIterator[ExecEvent]:
        wf_state = ctx.workflow_state
        wf_state.setdefault("completed", [])
        wf_state.setdefault("node_outputs", {})
        nodes = self.wf.node_map()

        # 整体超时护栏：def 配了用 def 的，否则用全局默认
        total_timeout = self.wf.timeout_seconds or config.HYBRID_WORKFLOW_TIMEOUT
        deadline = time.monotonic() + total_timeout

        current = wf_state.get("next_node") or self.wf.entry_id()
        try:
            while current:
                if time.monotonic() > deadline:
                    self.result = EngineResult(status="timeout", error="工作流整体超时")
                    return

                node = nodes.get(current)
                if node is None:
                    self.result = EngineResult(
                        status="failed",
                        signal="node_failed",
                        node_id=current,
                        error=f"节点 {current} 不存在（定义可能在执行中被改）",
                    )
                    return

                # 人工切换指令轮询：节点边界是让出的安全点
                manual = control()
                if manual:
                    self.result = EngineResult(status="switched", signal="manual", node_id=current)
                    return

                # requires_agent 让出：Agent 接管过（agent_handled 标记）则跳过执行直接路由
                if node.requires_agent and wf_state.get("agent_handled") != current:
                    wf_state["next_node"] = current
                    self.result = EngineResult(
                        status="switched",
                        signal="requires_agent",
                        node_id=current,
                        output_key=node.config.get("output_key"),
                    )
                    return
                agent_already_handled = wf_state.pop("agent_handled", None) == current

                # 人工节点恢复：拿到 human_resumed 标记就**不重新执行节点体**，
                # 直接记录批准值 + 留一条恢复事件 + 路由到下一节点。
                # 这样既不会重复挂起，也保证不重跑已完成的节点。
                if node.type == "human" and wf_state.get("human_resumed") == current:
                    wf_state.pop("human_resumed")
                    wf_state.pop("suspended_at", None)
                    _key = node.config.get("output_key")
                    if _key:
                        shared_set(ctx.shared, _key, node.config.get("resume_value", "approved"))
                    yield ExecEvent(
                        event_type="human_intervention",
                        mode="workflow",
                        node_id=current,
                        decision={"rule": "human_resume", "reason": "人工已确认，从断点继续"},
                        payload={"decision": "approved", "resumed": True},
                    )
                    nxt, _ = self._route(current, ctx)
                    wf_state["completed"].append(current)
                    current = nxt
                    wf_state["next_node"] = current
                    continue

                if not agent_already_handled:
                    yield ExecEvent(
                        event_type="node_enter",
                        mode="workflow",
                        node_id=current,
                        payload={"type": node.type, "name": node.name or node.id},
                    )
                    # 直接消费节点生成器：事件原样透传给编排器，
                    # error 事件既透传（留痕）又作为失败信号落锤
                    error: str | None = None
                    try:
                        async for ev in self._run_with_retry(ctx, node, control, wf_state):
                            if ev.event_type == "error":
                                error = ev.payload.get("error", "节点执行失败")
                            yield ev
                    except _Suspend as sp:
                        # 人工挂起：正常让出，不是失败。
                        # 锚点指向**被挂起的节点本身**（不是它的下一节点）——
                        # 恢复时要重新进入这个节点，才能在那里记录"人工已批准"。
                        # 若锚点直接指向下游，恢复后就直接跑下游了，批准值永远写不进去。
                        # 注意：**不把该节点计入 completed** —— 它还没完成，恢复后才算数。
                        wf_state["next_node"] = sp.node_id
                        wf_state["suspended_at"] = sp.node_id
                        ctx.status = "awaiting_human"
                        self.result = EngineResult(
                            status="awaiting_human",
                            node_id=sp.node_id,
                            output_key=node.config.get("output_key"),
                            signal="human_suspend",
                        )
                        return
                    if error is not None:
                        # 失败路径：on_fail 跳转优先，否则上报切换判定
                        if node.on_fail:
                            yield ExecEvent(
                                event_type="node_exit",
                                mode="workflow",
                                node_id=current,
                                payload={
                                    "status": "failed",
                                    "on_fail": node.on_fail,
                                    "error": error,
                                },
                            )
                            wf_state["completed"].append(current)
                            current = node.on_fail
                            wf_state["next_node"] = current
                            continue
                        self.result = EngineResult(
                            status="failed",
                            signal="node_failed",
                            node_id=current,
                            error=error,
                        )
                        return
                    # output_key 一起带出去：万一后续节点失败，上层才知道
                    # "最后一个真正跑完的节点把产出写在了哪个键"，而不是
                    # 从 shared 里盲取最后一个值（那会取到 plan 的规划稿）。
                    yield ExecEvent(
                        event_type="node_exit",
                        mode="workflow",
                        node_id=current,
                        payload={"status": "ok", "output_key": node.config.get("output_key")},
                    )
                    wf_state["completed"].append(current)

                # 路由：先按序求值条件边，无命中再落无条件默认边
                nxt, no_route = self._route(current, ctx)
                if no_route:
                    err = f"节点 {current} 的条件分支无匹配"
                    yield ExecEvent(
                        event_type="error", mode="workflow", node_id=current, payload={"error": err}
                    )
                    self.result = EngineResult(
                        status="failed", signal="node_failed", node_id=current, error=err
                    )
                    return
                current = nxt
                wf_state["next_node"] = current

            # 走到没有出边 = 正常跑完
            wf_state.pop("next_node", None)
            self.result = EngineResult(status="completed")
        finally:
            if self.result is None:  # 生成器被提前关闭（客户端断开等）
                self.result = EngineResult(status="failed", error="执行被中断")

    async def _run_with_retry(
        self, ctx: TaskContext, node: NodeDef, control: ControlFn, wf_state: dict
    ) -> AsyncIterator[ExecEvent]:
        """包一层重试 + 超时；成功则无 error 事件，失败则以 error 事件收尾。"""
        attempts = 1 + max(0, node.retry)
        timeout = node.timeout_seconds or config.HYBRID_NODE_TIMEOUT
        last_error = "未知错误"
        for attempt in range(1, attempts + 1):
            agen = self._exec_node(ctx, node, control)
            deadline = time.monotonic() + timeout
            err: str | None = None
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        err = f"节点超时（>{timeout}s）"
                        break
                    try:
                        ev = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
                    except StopAsyncIteration:
                        break
                    if ev.event_type == "error":
                        err = ev.payload.get("error", "节点执行失败")
                        break
                    yield ev
            except TimeoutError:
                err = f"节点超时（>{timeout}s）"
            except _Suspend:
                # 挂起必须原样穿透：它不是失败！若落到下面的 except Exception，
                # 会被转成 error 事件 + 重试，挂起就静默失效了（实测踩过）。
                raise
            except Exception as e:  # 节点内部异常兜底：不炸整个流程
                err = f"{type(e).__name__}: {e}"
            finally:
                await agen.aclose()
            if err is None:
                return
            last_error = err
            if attempt < attempts:
                yield ExecEvent(
                    event_type="status",
                    mode="workflow",
                    node_id=node.id,
                    payload={"text": f"节点 {node.id} 第 {attempt} 次失败，重试中：{err}"},
                )
        yield ExecEvent(
            event_type="error", mode="workflow", node_id=node.id, payload={"error": last_error}
        )

    # ---------- 节点执行器 ----------

    async def _exec_node(
        self, ctx: TaskContext, node: NodeDef, control: ControlFn
    ) -> AsyncIterator[ExecEvent]:
        """按类型分发。产出写 shared（唯一数据交换通道）。"""
        if node.type == "llm":
            async for ev in self._exec_llm(ctx, node):
                yield ev
        elif node.type == "tool":
            async for ev in self._exec_tool(ctx, node):
                yield ev
        elif node.type == "agent":
            async for ev in self._exec_agent(ctx, node, control):
                yield ev
        elif node.type == "condition":
            # 条件节点不做事：它只是路由汇合点，分支逻辑在边的 condition 上
            return
        elif node.type == "human":
            # 人工节点。两种行为，由 config.suspend 决定：
            #   suspend=true  —— **真挂起**：存续跑锚点、让出执行、等人工恢复
            #   suspend 缺省  —— 自动通过（保持既有行为，避免老流程突然卡住）
            #
            # 挂起不是"停止"：ctx.status 落 awaiting_human，工作流状态（含 next_node）
            # 已写进快照，恢复时从**下一个节点**继续，不重跑已完成的节点。
            # 这条让出路径与 requires_agent 完全同构，复用同一套断点机制。
            key = node.config.get("output_key")
            should_suspend = bool(node.config.get("suspend", False))
            # 注意：这里没有 stream() 里的 wf_state 局部名，工作流状态要经 ctx 取
            wf_state = ctx.workflow_state
            resumed = wf_state.pop("human_resumed", None) == node.id

            if should_suspend and not resumed:
                nxt, _ = self._route(node.id, ctx)
                wf_state["next_node"] = nxt
                wf_state["suspended_at"] = node.id
                # 记下挂起时的上下文，恢复接口要按它校验归属与判断是否已恢复
                ctx.status = "awaiting_human"
                yield ExecEvent(
                    event_type="human_intervention",
                    mode="workflow",
                    node_id=node.id,
                    decision={
                        "rule": "human_suspend",
                        "reason": f"节点 {node.id} 需要人工确认后才继续",
                        "next_node": nxt,
                    },
                    payload={
                        "prompt": render_template(node.config.get("prompt", ""), ctx),
                        "decision": "awaiting_human",
                    },
                )
                # ⚠️ 必须用异常穿透，不能用 return：
                # `_exec_human` 是**异步生成器**，生成器里的 return 只是"正常结束"，
                # 调用方（_run_with_retry）会当成节点执行成功，接着跑下一个节点——
                # 挂起就静默失效了（实测踩过）。
                raise _Suspend(node.id, nxt)

            # 恢复后（或未要求挂起）走这里：把批准结果写进 shared
            if key:
                shared_set(ctx.shared, key, node.config.get("resume_value", "approved"))
            yield ExecEvent(
                event_type="human_intervention",
                mode="workflow",
                node_id=node.id,
                decision={
                    "rule": "human_resume" if resumed else "human_auto",
                    "reason": "人工已确认，继续执行" if resumed else "未要求挂起，自动通过",
                },
                payload={
                    "prompt": render_template(node.config.get("prompt", ""), ctx),
                    "decision": "approved" if resumed else "auto_approve",
                },
            )
        else:
            yield ExecEvent(
                event_type="error",
                mode="workflow",
                node_id=node.id,
                payload={"error": f"未知节点类型：{node.type}"},
            )

    async def _exec_llm(self, ctx: TaskContext, node: NodeDef) -> AsyncIterator[ExecEvent]:
        """LLM 节点：config = {prompt, output_key, stream}。

        产出直接写 `ctx.shared[key]`，**刻意不走 `shared_set()`**：
          1. 节点产出（全文）要被下游 `render_template` 逐字取出，而 `shared_set`
             对超长值会落盘、只在 shared 里留一个指针字符串，模板无法解析该指针；
          2. `shared_set` 的命名空间校验会把 `plan` / `agent_result` 这类正常输出键
             与 `node_*` 前缀判成冲突。
        因此只有 `resume_value` 这类有界字面量才走 `shared_set`。细节见 hybrid/context.py。
        """
        # 跨轮记忆：把对话历史作为前缀拼进 prompt（无历史时为空串，退化为单轮）。
        # 惰性导入避免模块级循环依赖。
        from hybrid.agent_engine import history_prefix

        prompt = history_prefix(ctx) + render_template(node.config.get("prompt", "{question}"), ctx)
        key = node.config.get("output_key") or f"node_{node.id}"
        llm = self._get_llm()
        if node.config.get("stream"):
            # 流式：token 事件直接进 SSE，前端看到的是工作流在"逐字说话"
            chunks: list[str] = []
            async for chunk in llm.astream(prompt):
                text = chunk.content if isinstance(chunk.content, str) else ""
                if text:
                    chunks.append(text)
                    yield ExecEvent(
                        event_type="token", mode="workflow", node_id=node.id, payload={"text": text}
                    )
            ctx.shared[key] = "".join(chunks)
        else:
            resp = await llm.ainvoke(prompt)
            content = resp.content if isinstance(resp.content, str) else str(resp.content)
            ctx.shared[key] = content

    async def _exec_tool(self, ctx: TaskContext, node: NodeDef) -> AsyncIterator[ExecEvent]:
        """工具节点：config = {tool, args{模板}, output_key}。

        owner_id/kb_id 由服务端注入 RunnableConfig（与 chat.py agent 分支同一套
        安全边界：工具查谁的库不由配置说了算）。
        """
        from langchain_core.runnables import RunnableConfig

        from agent.tools import AGENT_TOOLS

        tool_name = node.config.get("tool", "")
        tool = next((t for t in AGENT_TOOLS if t.name == tool_name), None)
        if tool is None:
            yield ExecEvent(
                event_type="error",
                mode="workflow",
                node_id=node.id,
                payload={"error": f"未知工具：{tool_name}"},
            )
            return
        meta = TOOL_META.get(tool_name)

        # ① 危险动作审批：工作流没有"问用户"的交互位，直接拒绝，而不是静默放行。
        #    Agent 路径的 download_file 会先回确认串；工作流里没有这条回路，
        #    所以唯一安全的做法是不执行（见 2026-10-29 安全评估 #4）。
        if meta is not None and meta.needs_approval and config.TOOL_APPROVAL_ENABLED:
            yield ExecEvent(
                event_type="error",
                mode="workflow",
                node_id=node.id,
                payload={
                    "error": (
                        f"工具 {tool_name} 需要人工确认，工作流模式不支持。"
                        "请改用 Agent 模式，或改用 /api/kb/download 由用户主动下载。"
                    )
                },
            )
            return

        # ② 单次运行的 tool 节点调用次数上限：防"一个工作流里塞循环节点"
        #    把服务器当下载器用（每次 download_file 最多 200MB）。
        used = int(ctx.workflow_state.get("tool_calls") or 0) + 1
        if used > config.HYBRID_MAX_TOOL_CALLS:
            yield ExecEvent(
                event_type="error",
                mode="workflow",
                node_id=node.id,
                payload={
                    "error": (
                        f"本次工作流工具调用已达上限（{config.HYBRID_MAX_TOOL_CALLS} 次），"
                        "后续调用被拒绝"
                    )
                },
            )
            return
        ctx.workflow_state["tool_calls"] = used

        args = {
            k: render_template(str(v), ctx) if isinstance(v, str) else v
            for k, v in (node.config.get("args") or {}).items()
        }
        yield ExecEvent(
            event_type="tool_call",
            mode="workflow",
            node_id=node.id,
            payload={"name": tool_name, "input": args},
        )
        cfg: RunnableConfig = {
            "configurable": {
                "owner_id": ctx.owner_id,
                "kb_id": ctx.agent_state.get("kb_id"),
                # 与 Agent 路径同一套下载闸门：只认用户目标里原样出现的链接。
                # 即使 TOOL_APPROVAL_ENABLED=0，模型/配置也编不出新的外带地址。
                "download_allowlist": [],
                "user_urls": extract_urls(ctx.goal),
            }
        }
        out = await tool.ainvoke(args, config=cfg)
        # ③ 输出截断：与 Agent 路径同一个上限，防单次工具返回吃满上下文
        if meta is not None and isinstance(out, str):
            out = tool_guard.truncate_output(tool_name, out, meta.max_output_chars)
        key = node.config.get("output_key") or f"node_{node.id}"
        ctx.shared[key] = str(out)

    async def _exec_agent(
        self, ctx: TaskContext, node: NodeDef, control: ControlFn
    ) -> AsyncIterator[ExecEvent]:
        """Agent 节点：内嵌一次 Agent 执行（config = {goal, output_key}）。"""
        if self._agent_factory is None:
            yield ExecEvent(
                event_type="error",
                mode="workflow",
                node_id=node.id,
                payload={"error": "Agent 引擎未装配"},
            )
            return
        goal = render_template(node.config.get("goal", "{question}"), ctx)
        key = node.config.get("output_key") or "agent_result"
        eng = self._agent_factory()
        async for ev in eng.stream(ctx, control, goal=goal, output_key=key, node_id=node.id):
            yield ev
        r = eng.result
        if r is None or r.status != "completed":
            yield ExecEvent(
                event_type="error",
                mode="workflow",
                node_id=node.id,
                payload={"error": (r.error if r else "Agent 执行异常")},
            )

    # ---------- 路由 ----------

    def _route(self, node_id: str, ctx: TaskContext) -> tuple[str | None, bool]:
        """返回 (下一节点 id, 是否"有边但全不匹配")。

        规则：条件边按定义顺序求值，第一个成立的走；都不成立落第一条无条件边；
        没有任何出边 = None（流程终点）。
        """
        outs = [e for e in self.wf.edges if e.source == node_id]
        if not outs:
            return None, False
        defaults = [e for e in outs if not e.condition]
        for e in outs:
            if e.condition and eval_condition(e.condition, {"shared": ctx.shared}):
                return e.target, False
        if defaults:
            return defaults[0].target, False
        return None, True
