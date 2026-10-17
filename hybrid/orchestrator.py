# -*- coding: utf-8 -*-
"""混合编排器（要求 1/4/5/6/7 的汇合点）。

职责：
  - 持有引擎注册表 ENGINE_REGISTRY（新模式实现 engine.ExecutionEngine 协议
    并 register 即接入，编排器无感知 ← 要求 7）；
  - 驱动执行循环：当前引擎产出事件 → 落日志 + 透传 → 引擎段结束读 result
    → 快照落库 → 切换判定 → record_switch → 换引擎续跑；
  - 人工切换通道：request_manual_switch() 写入模块级指令；引擎经 control
    回调**偷看**（不消费）后在安全点让出，由编排器统一消费并应用
    （人工指令优先级最高，不过 SwitchEngine 的规则链）。

上下文一致性（要求 4）：
  每段结束与每次切换都 save_run() 整体快照，record_switch 内部 bump 版本号，
  崩溃后可用最近快照 restore 续跑（快照本期先落库，恢复入口后置）。

迭代护栏：max_switches + 2 次引擎段上限。SwitchEngine 的 max_switches 判定
正常会先落锤 terminate，这层只是兜底的兜底。
"""

from __future__ import annotations

import threading
from typing import Any, AsyncIterator, Callable

from core import config
from hybrid import store
from hybrid.agent_engine import AgentEngine
from hybrid.context import ExecEvent, TaskContext
from hybrid.engine import EngineResult, ExecutionEngine
from hybrid.switcher import SwitchEngine
from hybrid.workflow import WorkflowDef, WorkflowEngine

# ==================== 引擎注册表 ====================
# 值是"工厂"而非实例：每个执行段要独立实例（result 挂在实例上）
ENGINE_REGISTRY: dict[str, Callable[..., ExecutionEngine]] = {}


def register_engine(mode: str, factory: Callable[..., ExecutionEngine]) -> None:
    ENGINE_REGISTRY[mode] = factory


def _default_factories() -> None:
    if "agent" not in ENGINE_REGISTRY:
        register_engine("agent", lambda **kw: AgentEngine(**kw))
    if "workflow" not in ENGINE_REGISTRY:
        register_engine("workflow", lambda **kw: WorkflowEngine(**kw))


# ==================== 人工切换通道 ====================
# run_id -> 目标动作。模块级 dict + 锁：API 线程写、执行线程读。
# 单机单进程够用；多进程部署时换成 Redis 即可，接口不用变。
_MANUAL: dict[str, str] = {}
_MANUAL_LOCK = threading.Lock()


def request_manual_switch(run_id: str, action: str) -> None:
    """API 层调用：登记一条人工切换指令（to_agent / to_workflow / terminate）。"""
    if action not in ("to_agent", "to_workflow", "terminate"):
        raise ValueError(f"非法的人工切换动作：{action}")
    with _MANUAL_LOCK:
        _MANUAL[run_id] = action


def _peek_manual(run_id: str) -> str | None:
    """偷看人工指令（不消费）：引擎轮询用这个，消费权只在编排器。"""
    with _MANUAL_LOCK:
        return _MANUAL.get(run_id)


def _take_manual(run_id: str) -> str | None:
    """消费人工指令（一次性）：只有编排器能调。"""
    with _MANUAL_LOCK:
        return _MANUAL.pop(run_id, None)


# ==================== 编排器 ====================

class HybridOrchestrator:
    """一次混合执行。stream() 产出 ExecEvent，chat 层翻译成 SSE。"""

    def __init__(
        self,
        ctx: TaskContext,
        workflow_def: WorkflowDef | None = None,
        rules: list[dict] | None = None,
        **engine_kwargs: Any,
    ):
        _default_factories()
        self.ctx = ctx
        self.wf = workflow_def
        # 规则 None = 用库里的生效规则（含用户覆盖）；测试可注入固定列表
        self.rules = rules if rules is not None else store.list_rules(ctx.owner_id)
        self._engine_kwargs = engine_kwargs  # 测试注入假 llm / 假 agent 等

    def _make_engine(self, mode: str) -> ExecutionEngine:
        if mode == "workflow":
            if self.wf is None:
                raise RuntimeError("当前任务没有可用工作流定义")
            return ENGINE_REGISTRY["workflow"](
                wf=self.wf,
                llm=self._engine_kwargs.get("llm"),
                agent_engine_factory=self._engine_kwargs.get("agent_engine_factory")
                or (lambda: self._make_engine("agent")),
            )
        return ENGINE_REGISTRY["agent"](
            has_kb=self._engine_kwargs.get("has_kb", False),
            location=self._engine_kwargs.get("location"),
            agent=self._engine_kwargs.get("agent"),
            # #7：激活集透传给 AgentEngine（最终进 build_agent 的缓存键与 bind_tools），
            # 与 Agent 直连路径同源——请求开始由调用方 activated_for(uid) 取定。
            activated=self._engine_kwargs.get("activated", frozenset()),
        )

    async def stream(self) -> AsyncIterator[ExecEvent]:
        ctx = self.ctx
        switcher = SwitchEngine(self.rules)
        ctx.status = "running"
        # 起跑即落库：人工切换 API（POST /runs/{run_id}/switch）按 run_id 验归属，
        # 第一段跑完才落库的话，起跑阶段的人工指令会 404
        store.save_run(ctx)

        # aborted：正常收尾后才置 False。生成器被关闭（客户端断开，GeneratorExit
        # 从 yield 处抛出）或异常逃逸时，下面的正常收尾都到不了——没有 finally 兜底
        # run 会永远停在 running：/runs 列表出现"幽灵执行中"，人工切换还会对它误报 ok
        # （10-14 联调实测）。
        aborted = True
        try:
            for _segment in range(config.HYBRID_MAX_SWITCHES + 2):  # 迭代护栏
                if ctx.status != "running":
                    break

                # 段先消费人工指令：可能是在两段之间由 API 线程写入的
                manual = _take_manual(ctx.run_id)
                if manual:
                    yield self._apply_manual(manual)
                    if ctx.status != "running":
                        break
                    continue

                engine = self._make_engine(ctx.current_mode)
                async for ev in engine.stream(ctx, lambda: _peek_manual(ctx.run_id)):
                    store.add_log(ctx, ev)
                    yield ev
                result: EngineResult = engine.result or EngineResult(status="failed", error="引擎未返回结果")
                # 每段结束整体快照落库（崩溃续跑锚点）
                store.save_run(ctx)

                # ---- 人工指令优先于一切规则判定 ----
                if result.signal == "manual":
                    action = _take_manual(ctx.run_id)
                    if action is None:
                        # 指令已被消费但引擎还是让出了（理论上不该发生）：
                        # 按"反向切换"兜底，比直接卡死强
                        action = "to_workflow" if ctx.current_mode == "agent" else "to_agent"
                    yield self._apply_manual(action)
                    if ctx.status != "running":
                        break
                    continue

                # ---- 人工挂起：不是终态，也不是切换 ----
                # 工作流状态（含 next_node 续跑锚点）已在 ctx 里，这里只落盘并结束本轮。
                # 恢复靠 POST /api/hybrid/runs/{run_id}/resume：它把 human_resumed 标记
                # 写回快照再重新起跑，工作流从锚点的下一个节点继续（不重跑已完成的节点）。
                if result.status == "awaiting_human":
                    ctx.status = "awaiting_human"
                    ctx.workflow_state["suspended_at"] = result.node_id
                    store.save_run(ctx)  # 挂起即快照
                    yield ExecEvent(
                        event_type="human_intervention",
                        mode=ctx.current_mode,
                        node_id=result.node_id,
                        decision={
                            "rule": "human_suspend",
                            "reason": "任务已挂起，等待人工确认后恢复",
                        },
                        payload={"decision": "awaiting_human", "run_id": ctx.run_id},
                    )
                    break

                # ---- 无信号：按引擎结果直接落终态 ----
                if not result.signal:
                    if result.status == "completed":
                        ctx.status = "completed"
                    elif result.status == "timeout":
                        ctx.status = "timeout"
                    else:
                        ctx.status = "failed"
                        ctx.error = result.error
                    break

                # ---- 有信号：走切换规则 ----
                decision = switcher.decide(ctx, result.signal, {"error": result.error, "node_id": result.node_id})
                if decision.action == "continue":
                    # 无规则命中：按引擎结果落终态
                    if result.status == "completed":
                        ctx.status = "completed"
                    else:
                        ctx.status = "failed"
                        ctx.error = result.error or f"信号 {result.signal} 无匹配规则"
                    break
                if decision.action == "terminate":
                    ctx.status = "completed" if result.status == "completed" else "stopped"
                    yield self._switch_event(decision.rule, decision.reason)
                    break

                target = "agent" if decision.action == "to_agent" else "workflow"
                if target == ctx.current_mode or (target == "workflow" and self.wf is None):
                    # 已在目标模式 / 没有工作流可收编：按引擎结果收工
                    ctx.status = "completed" if result.status == "completed" else "stopped"
                    break

                ctx.record_switch(target, decision.rule, decision.reason)
                store.save_run(ctx)  # 切换即快照
                yield self._switch_event(decision.rule, decision.reason)
            else:
                # for 正常跑完 = 撞了迭代护栏（规则打架没触发 max_switches 的边角）
                ctx.status = "stopped"
                ctx.error = "执行段数超出护栏上限"

            if ctx.status == "running":  # 各分支没来得及落态的兜底
                ctx.status = "completed"
            aborted = False
        finally:
            # 落终态前最后一次快照，两条路径共用：
            #   - 正常路径：把最终状态（completed 等）持久化；
            #   - 中断路径（aborted=True，客户端断开 / 异常逃逸）：上面的收尾
            #     根本到不了，这里补落 stopped —— 否则 run 会永远停在 running
            #     （/runs 列表"幽灵执行中"，人工切换还会对它误报 ok；10-14 联调实测）。
            if aborted and ctx.status == "running":
                ctx.status = "stopped"
            ctx.bump()
            store.save_run(ctx)

    def _apply_manual(self, action: str) -> ExecEvent:
        """应用人工指令（优先级 0，不过规则链），返回 switch 事件。"""
        ctx = self.ctx
        if action == "terminate":
            ctx.status = "stopped"
            return self._switch_event("manual", "人工终止任务")
        target = "agent" if action == "to_agent" else "workflow"
        if target != ctx.current_mode and not (target == "workflow" and self.wf is None):
            ctx.record_switch(target, "manual", "人工干预切换")
            store.save_run(ctx)
        return self._switch_event("manual", "人工干预切换")

    def _switch_event(self, rule: str, reason: str) -> ExecEvent:
        """造切换事件并落日志：决策依据（规则+理由）是要求 6 的全链路数据，
        只发 SSE 不落库的话，exec_logs 回放时间轴会缺切换点。"""
        ctx = self.ctx
        last = ctx.mode_trace[-1] if ctx.mode_trace else None
        ev = ExecEvent(
            event_type="switch", mode=ctx.current_mode,
            decision={
                "rule": rule,
                "reason": reason,
                "from": last.from_mode if last else ctx.current_mode,
                "to": last.to_mode if last else ctx.current_mode,
            },
        )
        store.add_log(ctx, ev)
        return ev
