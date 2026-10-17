"""混合编排器集成回归（要求 1/4/5/6/8）：假引擎注入 + 临时库，不联网。

覆盖六项：
  1. 端到端混合执行：工作流失败 → 降级接管 → Agent 完成 → 收编回归 → 续跑收工，
     全程快照 + exec_logs + switch 事件三方留痕一致；
  2. 人工切换：执行中 API 登记指令 → 引擎安全点让出 → 编排器应用（优先级 0）；
     人工终止立即收工；
  3. 无规则命中：按引擎结果直接落终态；
  4. 无工作流可收编：Agent 完成信号到来但没有流程定义 → 直接收工不切换；
  5. 乒乓护栏：规则打架来回切，撞 HYBRID_MAX_SWITCHES 强制 terminate；
  6. 边界容错：引擎不返回结果（result=None）按失败兜底，不炸编排循环。

可从任意位置运行：python tests/test_hybrid_orchestrator.py
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402
from hybrid import orchestrator, store  # noqa: E402
from hybrid.context import ExecEvent, TaskContext  # noqa: E402
from hybrid.engine import EngineResult  # noqa: E402
from hybrid.workflow import validate_definition  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


# ==================== 测试基建 ====================


class _TmpStore:
    """把 hybrid store 指到临时库：不碰真实 hybrid.db。"""

    def __enter__(self):
        self._orig = store._db_path
        self._dir = tempfile.mkdtemp(prefix="hybrid_orch_")
        store._db_path = lambda: os.path.join(self._dir, "hybrid.db")
        store._init_db()
        return self

    def __exit__(self, *a):
        store._db_path = self._orig


class _FakeRegistry:
    """把 ENGINE_REGISTRY 的两个引擎换成假引擎，退出恢复。"""

    def __init__(self, mapping: dict):
        self._m = mapping

    def __enter__(self):
        self._orig = dict(orchestrator.ENGINE_REGISTRY)
        orchestrator.ENGINE_REGISTRY.update(self._m)
        return self

    def __exit__(self, *a):
        orchestrator.ENGINE_REGISTRY.clear()
        orchestrator.ENGINE_REGISTRY.update(self._orig)


class _Seg:
    """一段执行脚本：事件列表 + 段结束结果 + 要写进 shared 的数据。"""

    def __init__(self, events, result, shared=None):
        self.events, self.result, self.shared = events, result, shared or {}


class _SegEngine:
    """按脚本演出的假引擎。每个事件后轮询人工指令（模仿真实引擎的安全点）。"""

    def __init__(self, name: str, seg: _Seg):
        self.name = name
        self._seg = seg
        self.result: EngineResult | None = None

    async def stream(self, ctx, control, **_):
        for k, v in self._seg.shared.items():
            ctx.shared[k] = v
        for ev in self._seg.events:
            yield ev
            if control():
                self.result = EngineResult(status="switched", signal="manual")
                return
        self.result = self._seg.result


class _Factory:
    """每段造一个新引擎实例（result 挂实例），脚本循环取用。"""

    def __init__(self, name: str, segs: list[_Seg]):
        self.name, self.segs, self.idx = name, segs, 0

    def __call__(self, **_kw) -> _SegEngine:
        seg = self.segs[min(self.idx, len(self.segs) - 1)]
        self.idx += 1
        return _SegEngine(self.name, seg)


def _ev(t: str, mode: str, node: str | None = None, **payload) -> ExecEvent:
    return ExecEvent(event_type=t, mode=mode, node_id=node, payload=payload)


def _wf() -> object:
    return validate_definition(
        {
            "id": "t_orch",
            "entry": "only",
            "nodes": [{"id": "only", "type": "llm", "config": {}}],
            "edges": [],
        }
    )


def _collect(orch) -> list[ExecEvent]:
    async def _go():
        out = []
        async for ev in orch.stream():
            out.append(ev)
        return out

    return asyncio.run(_go())


def _switches(events: list[ExecEvent]) -> list[ExecEvent]:
    return [e for e in events if e.event_type == "switch"]


# ==================== 1. 端到端混合执行 ====================


def test_1_end_to_end() -> None:
    print("\n[1] 端到端：降级接管 → 收编回归 → 续跑收工")
    with _TmpStore():
        wf_fac = _Factory(
            "workflow",
            [
                _Seg(
                    [
                        _ev("node_enter", "workflow", "x"),
                        _ev("error", "workflow", "x", error="节点坏了"),
                    ],
                    EngineResult(
                        status="failed", signal="node_failed", node_id="x", error="节点坏了"
                    ),
                ),
                _Seg(
                    [_ev("node_enter", "workflow", "y"), _ev("node_exit", "workflow", "y")],
                    EngineResult(status="completed"),
                ),
            ],
        )
        agent_fac = _Factory(
            "agent",
            [
                _Seg(
                    [_ev("token", "agent", text="产出")],
                    EngineResult(
                        status="completed", signal="goal_satisfied", output_key="agent_result"
                    ),
                    shared={"agent_result": "产出"},
                ),
            ],
        )
        with _FakeRegistry({"workflow": wf_fac, "agent": agent_fac}):
            ctx = TaskContext(
                owner_id="u1", goal="先A后B", current_mode="workflow", workflow_id="t_orch"
            )
            orch = orchestrator.HybridOrchestrator(
                ctx, workflow_def=_wf(), rules=store.DEFAULT_RULES
            )
            events = _collect(orch)

            check(ctx.status == "completed", "全程跑完：completed")
            check(len(ctx.mode_trace) == 2, "发生两次切换")
            check(
                ctx.mode_trace[0].rule == "wf_node_failure"
                and ctx.mode_trace[0].to_mode == "agent",
                "第一次：节点失败降级到 Agent",
            )
            check(
                ctx.mode_trace[1].rule == "agent_goal_satisfied"
                and ctx.mode_trace[1].to_mode == "workflow",
                "第二次：子目标完成收编回工作流",
            )

            sw = _switches(events)
            check(
                len(sw) == 2 and all(e.decision and e.decision.get("rule") for e in sw),
                "SSE 侧两次 switch 事件都带决策依据",
            )

            run = store.get_run("u1", ctx.run_id)
            check(run is not None and run["status"] == "completed", "执行快照落库且状态一致")
            check(len(run["context"]["mode_trace"]) == 2, "快照里切换链完整（崩溃续跑依据）")
            check(
                run["context"]["shared"].get("agent_result") == "产出", "Agent 产出让快照进 shared"
            )

            logs = store.list_logs("u1", ctx.run_id)
            types = [log["event_type"] for log in logs]
            check(
                "node_enter" in types and "error" in types and "token" in types,
                "exec_logs 覆盖节点/错误/token 事件",
            )
            check(types.count("switch") == 2, "switch 事件也落 exec_logs（回放时间轴不缺切换点）")
            sw_log = [log for log in logs if log["event_type"] == "switch"][0]
            check(
                sw_log["decision"]["rule"] == "wf_node_failure"
                and sw_log["decision"]["to"] == "agent",
                "日志里的决策依据与切换链一致",
            )
            check(store.list_logs("别人", ctx.run_id) == [], "日志按归属隔离")


# ==================== 2. 人工切换 ====================


def test_2_manual_switch() -> None:
    print("\n[2] 人工切换 / 人工终止")
    with _TmpStore():
        wf_fac = _Factory(
            "workflow",
            [
                _Seg(
                    [
                        _ev("node_enter", "workflow", "x"),
                        _ev("status", "workflow", "x", text="跑着呢"),
                    ],
                    EngineResult(status="completed"),
                ),
            ],
        )
        agent_fac = _Factory(
            "agent",
            [
                _Seg([_ev("token", "agent", text="接手")], EngineResult(status="completed")),
            ],
        )
        with _FakeRegistry({"workflow": wf_fac, "agent": agent_fac}):
            ctx = TaskContext(
                owner_id="u2", goal="人工演示", current_mode="workflow", workflow_id="t_orch"
            )
            orch = orchestrator.HybridOrchestrator(ctx, workflow_def=_wf(), rules=[])

            async def _go():
                out = []
                agen = orch.stream()
                first = await agen.__anext__()
                out.append(first)
                # 收到第一个事件后，模拟 API 线程登记人工切换
                orchestrator.request_manual_switch(ctx.run_id, "to_agent")
                async for ev in agen:
                    out.append(ev)
                return out

            events = asyncio.run(_go())
            check(ctx.status == "completed", "人工切换后任务跑完")
            check(ctx.current_mode == "agent", "当前模式已切到 agent")
            check(
                len(ctx.mode_trace) == 1 and ctx.mode_trace[0].rule == "manual",
                "切换链记录 manual（优先级 0，不过规则链）",
            )
            sw = _switches(events)
            check(
                bool(sw) and sw[0].decision["rule"] == "manual" and sw[0].decision["to"] == "agent",
                "switch 事件带人工去向",
            )

        # 人工终止：起跑前登记，第一段都不该跑
        wf_fac2 = _Factory(
            "workflow",
            [
                _Seg([_ev("node_enter", "workflow", "x")], EngineResult(status="completed")),
            ],
        )
        with _FakeRegistry(
            {
                "workflow": wf_fac2,
                "agent": _Factory("agent", [_Seg([], EngineResult(status="completed"))]),
            }
        ):
            ctx2 = TaskContext(
                owner_id="u2", goal="终止演示", current_mode="workflow", workflow_id="t_orch"
            )
            orch2 = orchestrator.HybridOrchestrator(ctx2, workflow_def=_wf(), rules=[])
            orchestrator.request_manual_switch(ctx2.run_id, "terminate")
            events2 = _collect(orch2)
            check(ctx2.status == "stopped", "人工终止：stopped")
            check(
                len(events2) == 1
                and events2[0].event_type == "switch"
                and events2[0].decision["rule"] == "manual",
                "只产出一条人工终止 switch 事件",
            )
            check(wf_fac2.idx == 0, "工作流引擎根本没起跑")
            check(len(ctx2.mode_trace) == 0, "终止不是切换，不进切换链")


# ==================== 3. 无规则命中 ====================


def test_3_no_rule_matched() -> None:
    print("\n[3] 无规则命中：按引擎结果落终态")
    with _TmpStore():
        wf_fac = _Factory(
            "workflow",
            [
                _Seg(
                    [], EngineResult(status="failed", signal="node_failed", node_id="x", error="坏")
                ),
            ],
        )
        with _FakeRegistry({"workflow": wf_fac}):
            ctx = TaskContext(
                owner_id="u3", goal="g", current_mode="workflow", workflow_id="t_orch"
            )
            _collect(orchestrator.HybridOrchestrator(ctx, workflow_def=_wf(), rules=[]))
            check(
                ctx.status == "failed" and "坏" in (ctx.error or ""),
                "failed 结果 + 无规则 → failed",
            )

        agent_fac = _Factory(
            "agent",
            [
                _Seg([], EngineResult(status="completed", signal="goal_satisfied")),
            ],
        )
        with _FakeRegistry({"agent": agent_fac}):
            ctx2 = TaskContext(owner_id="u3", goal="g", current_mode="agent")
            _collect(orchestrator.HybridOrchestrator(ctx2, workflow_def=None, rules=[]))
            check(ctx2.status == "completed", "completed 结果 + 无规则 → completed 收工")


# ==================== 4. 无工作流可收编 ====================


def test_4_no_workflow_to_resume() -> None:
    print("\n[4] Agent 裸跑完成：没有流程定义不硬切")
    with _TmpStore():
        agent_fac = _Factory(
            "agent",
            [
                _Seg(
                    [_ev("token", "agent", text="答案")],
                    EngineResult(
                        status="completed", signal="goal_satisfied", output_key="agent_result"
                    ),
                ),
            ],
        )
        with _FakeRegistry({"agent": agent_fac}):
            ctx = TaskContext(owner_id="u4", goal="开放问题", current_mode="agent")
            events = _collect(
                orchestrator.HybridOrchestrator(ctx, workflow_def=None, rules=store.DEFAULT_RULES)
            )
            check(ctx.status == "completed", "无工作流可收编：直接 completed")
            check(
                len(ctx.mode_trace) == 0 and not _switches(events),
                "goal_satisfied 规则命中但没流程可回：不发生切换",
            )


# ==================== 5. 乒乓护栏 ====================


def test_5_pingpong_guard() -> None:
    print("\n[5] 规则打架乒乓：撞上限强制 terminate")
    rules = [
        {
            "name": "fail_to_agent",
            "signal": "node_failed",
            "action": "to_agent",
            "priority": 10,
            "cooldown_seconds": 0,
        },
        {
            "name": "budget_to_wf",
            "signal": "budget_exhausted",
            "action": "to_workflow",
            "priority": 10,
            "cooldown_seconds": 0,
        },
    ]
    with _TmpStore():
        wf_fac = _Factory(
            "workflow",
            [
                _Seg(
                    [], EngineResult(status="failed", signal="node_failed", node_id="x", error="坏")
                ),
            ],
        )
        agent_fac = _Factory(
            "agent",
            [
                _Seg(
                    [], EngineResult(status="switched", signal="budget_exhausted", error="预算尽")
                ),
            ],
        )
        with _FakeRegistry({"workflow": wf_fac, "agent": agent_fac}):
            ctx = TaskContext(
                owner_id="u5", goal="乒乓", current_mode="workflow", workflow_id="t_orch"
            )
            events = _collect(orchestrator.HybridOrchestrator(ctx, workflow_def=_wf(), rules=rules))
            check(ctx.status == "stopped", "强制收工：stopped")
            check(len(ctx.mode_trace) == config.HYBRID_MAX_SWITCHES, "切换次数恰好撞上限")
            sw = _switches(events)
            check(len(sw) == config.HYBRID_MAX_SWITCHES + 1, "每次切换 + 最终终止都有事件")
            check(sw[-1].decision["rule"] == "__max_switches__", "最后一条是上限终止")
            run = store.get_run("u5", ctx.run_id)
            check(run["status"] == "stopped", "落库状态一致")


# ==================== 6. 边界容错 ====================


class _NoResultEngine:
    """不设置 result 的坏引擎：模拟引擎异常中断没来得及落锤。"""

    def __init__(self, name):
        self.name = name
        self.result = None

    async def stream(self, ctx, control, **_):
        yield _ev("token", self.name, text="半截")


def test_6_engine_no_result() -> None:
    print("\n[6] 引擎不返回结果：按失败兜底")
    with _TmpStore():
        with _FakeRegistry({"agent": lambda **kw: _NoResultEngine("agent")}):
            ctx = TaskContext(owner_id="u6", goal="g", current_mode="agent")
            events = _collect(orchestrator.HybridOrchestrator(ctx, workflow_def=None, rules=[]))
            check(
                ctx.status == "failed" and "未返回结果" in (ctx.error or ""),
                "result=None 合成 failed，不炸编排循环",
            )
            check(any(e.event_type == "token" for e in events), "中断前的事件正常透传")
            run = store.get_run("u6", ctx.run_id)
            check(run is not None and run["status"] == "failed", "失败状态也落库留痕")


def main() -> int:
    print("=" * 60)
    print("混合编排器集成回归（切换 / 人工 / 护栏 / 容错 / 留痕）")
    print("=" * 60)
    test_1_end_to_end()
    test_2_manual_switch()
    test_3_no_rule_matched()
    test_4_no_workflow_to_resume()
    test_5_pingpong_guard()
    test_6_engine_no_result()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for m in FAILED:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
