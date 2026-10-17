"""统一任务上下文回归（混合架构要求 4）：不联网、不依赖真实 LLM。

覆盖三项：
  1. snapshot/restore 往返：全量字段（含 shared / 私有段 / mode_trace）完整还原；
  2. record_switch：追加切换记录、翻转 current_mode、版本号 +1；
  3. 前向兼容：旧快照缺新增字段时 restore 用默认值兜住（pydantic 默认值）。

可从任意位置运行：python tests/test_hybrid_context.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hybrid.context import TaskContext  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


# ==================== 1. snapshot / restore 往返 ====================


def test_1_snapshot_restore() -> None:
    """全量状态快照后可完整还原：跨模式传递不丢数据。"""
    print("\n[1] snapshot / restore 往返")
    ctx = TaskContext(
        owner_id="u1", goal="先查天气再写报告", current_mode="workflow", workflow_id="wf1"
    )
    ctx.shared["plan"] = "1.查 2.写"
    ctx.shared["score"] = 0.9
    ctx.workflow_state["next_node"] = "write"
    ctx.workflow_state["completed"] = ["plan"]
    ctx.agent_state["steps"] = 3
    ctx.agent_state["kb_id"] = "kb9"
    ctx.record_switch("agent", "wf_requires_agent", "节点声明需要自主执行")
    ctx.error = None

    snap = ctx.snapshot()
    check(isinstance(snap, dict), "snapshot 返回 dict（可 JSON 序列化落库）")

    import json

    snap2 = json.loads(json.dumps(snap, ensure_ascii=False))  # 过一遍 JSON 模拟落库回读
    back = TaskContext.restore(snap2)

    check(back.run_id == ctx.run_id and back.task_id == ctx.task_id, "run_id / task_id 一致")
    check(back.owner_id == "u1" and back.goal == "先查天气再写报告", "owner / goal 一致")
    check(back.current_mode == "agent", "切换后的 current_mode 被还原")
    check(back.workflow_id == "wf1", "workflow_id 一致")
    check(back.shared == {"plan": "1.查 2.写", "score": 0.9}, "shared 中间结果完整还原")
    check(back.workflow_state["next_node"] == "write", "工作流断点锚点还原（续跑依据）")
    check(back.workflow_state["completed"] == ["plan"], "已完成节点列表还原")
    check(back.agent_state["steps"] == 3 and back.agent_state["kb_id"] == "kb9", "Agent 私有段还原")
    check(back.version == ctx.version and back.version > 0, "版本号还原")
    check(len(back.mode_trace) == 1, "切换链还原")
    sw = back.mode_trace[0]
    check(sw.from_mode == "workflow" and sw.to_mode == "agent", "切换记录 from/to 正确")
    check(sw.rule == "wf_requires_agent" and "自主" in sw.reason, "触发规则与决策依据还原")
    check(isinstance(sw.ts, int) and sw.ts > 0, "切换时间戳存在")


# ==================== 2. record_switch / bump ====================


def test_2_record_switch_and_bump() -> None:
    """每次切换：mode_trace +1、current_mode 翻转、version +1。"""
    print("\n[2] record_switch / bump 版本号")
    ctx = TaskContext(current_mode="workflow")
    v0 = ctx.version
    check(v0 == 0, "初始版本号 0")

    sw = ctx.record_switch("agent", "manual", "人工干预切换")
    check(ctx.current_mode == "agent", "current_mode 翻转到 agent")
    check(ctx.version == v0 + 1, "切换后版本号 +1")
    check(len(ctx.mode_trace) == 1 and ctx.mode_trace[0] is sw, "返回对象即链上记录")
    check(sw.from_mode == "workflow" and sw.to_mode == "agent", "from/to 记录正确")
    check(sw.rule == "manual" and sw.reason == "人工干预切换", "规则名与理由落记录")

    ctx.bump()
    check(ctx.version == v0 + 2, "bump 单独再 +1（普通状态变更）")

    ctx.record_switch("workflow", "agent_goal_satisfied", "子目标完成")
    check(len(ctx.mode_trace) == 2 and ctx.current_mode == "workflow", "第二次切换链式追加")
    check(ctx.mode_trace[1].from_mode == "agent", "第二次 from_mode 与当前模式衔接")


# ==================== 3. 前向兼容 ====================


def test_3_restore_forward_compat() -> None:
    """旧快照缺字段时 restore 用默认值兜住（新增字段带默认值才能演进）。"""
    print("\n[3] restore 前向兼容")
    minimal = {
        "task_id": "t1",
        "run_id": "r1",
        "owner_id": "u1",
        "goal": "旧任务",
        # 缺 status / current_mode / workflow_state / agent_state / shared / mode_trace / version
    }
    ctx = TaskContext.restore(minimal)
    check(ctx.status == "running", "缺 status 默认 running")
    check(ctx.current_mode == "agent", "缺 current_mode 默认 agent")
    check(
        ctx.shared == {} and ctx.workflow_state == {} and ctx.agent_state == {}, "缺各段默认空 dict"
    )
    check(ctx.mode_trace == [] and ctx.version == 0, "缺切换链/版本号默认空/0")

    extra = dict(minimal, future_field="未来版本加的字段")
    ctx2 = TaskContext.restore(extra)
    check(ctx2.task_id == "t1", "多出来的未知字段不炸（pydantic 忽略）")


def main() -> int:
    print("=" * 60)
    print("统一任务上下文回归（快照 / 切换记录 / 兼容性）")
    print("=" * 60)
    test_1_snapshot_restore()
    test_2_record_switch_and_bump()
    test_3_restore_forward_compat()
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
