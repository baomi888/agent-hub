# -*- coding: utf-8 -*-
"""human 节点真挂起 / 恢复 的回归测试。

验四件事：
  1. config.suspend=true 的 human 节点会**让出执行**（不自动通过），
     并把续跑锚点 next_node 写进工作流状态
  2. 状态落 awaiting_human，且快照可完整往返（落库→读回→续跑）
  3. 恢复后从**下一个节点**继续，不重跑已完成的节点
  4. config.suspend 缺省 = 老行为（自动通过），不破坏既有流程

不联网、不调模型：工作流只含 human 与 condition 节点。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# ⚠️ 本测试**不改任何全局配置**（不改 config.DATA_DIR、不改 os.environ）：
# 一旦改了，同进程内别的测试会看到被污染的路径——
# 实测踩过：改 DATA_DIR 后 tests/test_scheduler.py 的默认路径断言直接失败。
# 隔离办法是下面把 spill_value 换成"不落盘"的假实现（本测试只验控制流，不验落盘）。
from hybrid import context as _hctx  # noqa: E402

_ORIG_SPILL = _hctx.spill_value
_hctx.spill_value = lambda value, key: f"[TEST-SPILL {len(str(value))}]"

import atexit  # noqa: E402

atexit.register(lambda: setattr(_hctx, "spill_value", _ORIG_SPILL))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAILS.append(name)


def build_wf(suspend: bool):
    from hybrid.workflow import EdgeDef, NodeDef, WorkflowDef

    return WorkflowDef(
        id="wf_human", name="人工确认流程", version=1, timeout_seconds=60,
        nodes=[
            NodeDef(id="start", type="condition", config={}),
            NodeDef(
                id="gate", type="human",
                config={
                    "suspend": suspend,
                    "prompt": "请确认是否继续（目标：{goal}）",
                    "output_key": "node:gate:approved",
                },
            ),
            NodeDef(id="after", type="condition", config={}),
        ],
        edges=[
            EdgeDef(source="start", target="gate"),
            EdgeDef(source="gate", target="after"),
        ],
    )


async def drive(wf, ctx):
    """跑一次工作流，收集事件与结果。"""
    from hybrid.workflow import WorkflowEngine

    eng = WorkflowEngine(wf)
    events = []
    async for ev in eng.stream(ctx, lambda: None):
        events.append(ev)
    return events, eng.result


def main() -> int:
    from hybrid.context import TaskContext

    print("[1] suspend=true：应挂起而不是自动通过")
    wf = build_wf(True)
    ctx = TaskContext(owner_id="u1", goal="验证挂起", current_mode="workflow", workflow_id=wf.id)
    events, result = asyncio.run(drive(wf, ctx))
    human_events = [e for e in events if e.event_type == "human_intervention"]
    check("产生了 human_intervention 事件", len(human_events) >= 1, len(human_events))
    check("结果状态为 awaiting_human", result.status == "awaiting_human", result.status)
    check("ctx.status 落 awaiting_human", ctx.status == "awaiting_human", ctx.status)
    check("续跑锚点指向被挂起的节点（恢复时重新进入它才能记录批准）",
          ctx.workflow_state.get("next_node") == "gate", ctx.workflow_state.get("next_node"))
    check("挂起点已记录", ctx.workflow_state.get("suspended_at") == "gate")
    check("批准值**未**被写入（挂起时不该假装通过）",
          "node:gate:approved" not in ctx.shared, ctx.shared)
    check("事件里给了人工提示语全文",
          "请确认是否继续" in str(human_events[0].payload.get("prompt", "")),
          str(human_events[0].payload.get("prompt", ""))[:60])

    print("\n[2] 快照往返：挂起态可完整恢复")
    snap = ctx.snapshot()
    restored = TaskContext.restore(snap)
    check("恢复后状态仍是 awaiting_human", restored.status == "awaiting_human", restored.status)
    check("恢复后锚点仍是 gate（恢复接口据此重新进入）",
          restored.workflow_state.get("next_node") == "gate", restored.workflow_state.get("next_node"))
    check("挂起中的节点**未**被计入 completed（它还没完成）",
          "gate" not in (restored.workflow_state.get("completed") or []),
          restored.workflow_state.get("completed"))
    check("goal 完整保留", restored.goal == "验证挂起", restored.goal)

    print("\n[3] 恢复：从下一个节点继续，不重跑 gate")
    restored.workflow_state["human_resumed"] = "gate"   # 恢复接口会写这个标记
    restored.status = "running"
    events2, result2 = asyncio.run(drive(build_wf(True), restored))
    enters = [e.node_id for e in events2 if e.event_type == "node_enter"]
    check("没有重跑 gate（gate 未出现在 node_enter 里）", "gate" not in enters, enters)
    check("恢复了人工批准值", restored.shared.get("node:gate:approved") == "approved",
          restored.shared.get("node:gate:approved"))
    check("本轮正常完成", result2.status == "completed", result2.status)
    resumed_event = [e for e in events2 if e.event_type == "human_intervention"]
    check("事件标注为恢复来源",
          any((e.decision or {}).get("rule") == "human_resume" for e in resumed_event),
          [e.decision for e in resumed_event])

    print("\n[4] suspend 缺省：保持老行为（自动通过），不破坏既有流程")
    wf2 = build_wf(False)
    ctx2 = TaskContext(owner_id="u1", goal="老流程", current_mode="workflow", workflow_id=wf2.id)
    events3, result3 = asyncio.run(drive(wf2, ctx2))
    check("未挂起，直接完成", result3.status == "completed", result3.status)
    check("自动写入 approved", ctx2.shared.get("node:gate:approved") == "approved", ctx2.shared)
    auto = [e for e in events3 if e.event_type == "human_intervention"]
    check("事件标注为自动通过",
          any((e.payload or {}).get("decision") == "auto_approve" for e in auto),
          [(e.payload or {}).get("decision") for e in auto])

    print()
    if FAILS:
        print(f"SELF-CHECK FAILED：{len(FAILS)} 项 -> {FAILS}")
        return 1
    print("SELF-CHECK PASSED：human 挂起 / 恢复 / 兼容老行为 全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
