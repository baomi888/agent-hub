"""切换引擎回归（混合架构要求 1/5）：规则命中、优先级、防抖、上限、入口分类。

覆盖六项：
  1. 规则命中与优先级：signal 匹配 + 数字小者先求值；
  2. condition 求值：对 {shared, signal, ctx} 的受限表达式，不成立不触发；
  3. 规则级防抖：同名规则 cooldown 内不重复触发；显式 0 冷却豁免；
  4. 方向级防抖：同方向 HYBRID_SWITCH_COOLDOWN 内只切一次（防乒乓）；
  5. 硬上限：HYBRID_MAX_SWITCHES 次切换后强制 terminate；
  6. classify_task：流程型 / 开放型 / 持平归 open。

可从任意位置运行：python tests/test_hybrid_switcher.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402
from hybrid.context import ModeSwitch, TaskContext  # noqa: E402
from hybrid.switcher import SwitchEngine, classify_task  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


def _ctx() -> TaskContext:
    return TaskContext(owner_id="u1", goal="测试", current_mode="workflow")


def _sw(rule: str, to_mode: str = "agent", ago: int = 0, from_mode: str = "workflow") -> ModeSwitch:
    return ModeSwitch(
        from_mode=from_mode, to_mode=to_mode, rule=rule, reason="", ts=int(time.time()) - ago
    )


# ==================== 1. 命中与优先级 ====================


def test_1_match_and_priority() -> None:
    print("\n[1] 规则命中 / 优先级")
    eng = SwitchEngine(
        [
            {"name": "low", "signal": "node_failed", "action": "terminate", "priority": 50},
            {"name": "high", "signal": "node_failed", "action": "to_agent", "priority": 10},
            {"name": "other", "signal": "budget_exhausted", "action": "to_workflow", "priority": 5},
        ]
    )
    d = eng.decide(_ctx(), "node_failed")
    check(d.action == "to_agent" and d.rule == "high", "同 signal 时数字小者优先（10 胜 50）")

    d2 = eng.decide(_ctx(), "budget_exhausted")
    check(d2.action == "to_workflow" and d2.rule == "other", "按 signal 精确匹配")

    d3 = eng.decide(_ctx(), "没见过的信号")
    check(d3.action == "continue", "无匹配规则 → continue（交编排器默认处理）")

    eng2 = SwitchEngine(
        [
            {
                "name": "r",
                "signal": "node_failed",
                "action": "to_agent",
                "reason": "节点失败降级",
                "priority": 10,
            },
        ]
    )
    d4 = eng2.decide(_ctx(), "node_failed")
    check(d4.reason == "节点失败降级", "reason 透传为决策依据")
    eng3 = SwitchEngine([{"name": "r", "signal": "s", "action": "terminate"}])
    check(eng3.decide(_ctx(), "s").reason == "r", "reason 缺省回落规则名")


# ==================== 2. condition 求值 ====================


def test_2_condition() -> None:
    print("\n[2] condition 条件求值")
    eng = SwitchEngine(
        [
            {
                "name": "severe",
                "signal": "node_failed",
                "condition": "shared.retries > 2",
                "action": "terminate",
                "priority": 5,
            },
            {
                "name": "mild",
                "signal": "node_failed",
                "condition": "",
                "action": "to_agent",
                "priority": 10,
            },
        ]
    )
    ctx = _ctx()
    ctx.shared["retries"] = 1
    d = eng.decide(ctx, "node_failed")
    check(d.rule == "mild", "condition 不成立跳过，落到下一条规则")

    ctx.shared["retries"] = 5
    d2 = eng.decide(ctx, "node_failed")
    check(d2.rule == "severe" and d2.action == "terminate", "condition 成立按高优先级触发")

    eng2 = SwitchEngine(
        [
            {
                "name": "by_signal",
                "signal": "node_failed",
                "condition": "signal.node_id == '核心'",
                "action": "to_agent",
                "priority": 5,
            },
        ]
    )
    check(
        eng2.decide(_ctx(), "node_failed", {"node_id": "核心"}).rule == "by_signal",
        "condition 可读 signal payload",
    )
    check(
        eng2.decide(_ctx(), "node_failed", {"node_id": "边缘"}).action == "continue",
        "signal payload 不匹配不触发",
    )

    eng3 = SwitchEngine(
        [
            {
                "name": "bad",
                "signal": "s",
                "condition": "__import__('os')",
                "action": "terminate",
                "priority": 5,
            },
        ]
    )
    check(eng3.decide(_ctx(), "s").action == "continue", "非法 condition 按不成立处理（不炸）")


# ==================== 3. 规则级防抖 ====================


def test_3_rule_cooldown() -> None:
    print("\n[3] 规则级防抖（同名 cooldown 内不重复触发）")
    eng = SwitchEngine(
        [
            {
                "name": "cd",
                "signal": "node_failed",
                "action": "to_agent",
                "priority": 10,
                "cooldown_seconds": 30,
            },
        ]
    )
    ctx = _ctx()
    ctx.mode_trace.append(_sw("cd", ago=5))
    check(eng.decide(ctx, "node_failed").action == "continue", "5 秒前刚触发过：防抖拦截")

    ctx2 = _ctx()
    ctx2.mode_trace.append(_sw("cd", ago=120))
    check(eng.decide(ctx2, "node_failed").rule == "cd", "120 秒前触发过：超出冷却，放行")

    eng0 = SwitchEngine(
        [
            {
                "name": "free",
                "signal": "requires_agent",
                "action": "to_agent",
                "priority": 10,
                "cooldown_seconds": 0,
            },
        ]
    )
    ctx3 = _ctx()
    ctx3.mode_trace.append(_sw("free", ago=1))
    check(
        eng0.decide(ctx3, "requires_agent").rule == "free",
        "cooldown=0 的结构型规则豁免防抖（节点语义不该被吃掉）",
    )


# ==================== 4. 方向级防抖 ====================


def test_4_direction_cooldown() -> None:
    print("\n[4] 方向级防抖（同方向全局冷却，防规则打架乒乓）")
    eng = SwitchEngine(
        [
            {
                "name": "r1",
                "signal": "node_failed",
                "action": "to_agent",
                "priority": 10,
                "cooldown_seconds": 30,
            },
        ]
    )
    ctx = _ctx()
    # 另一条规则刚刚把模式切到 agent：同方向（→agent）全局冷却生效
    ctx.mode_trace.append(_sw("别的规则", to_mode="agent", ago=3))
    check(eng.decide(ctx, "node_failed").action == "continue", "同方向 3 秒前刚切过：拦截")

    ctx2 = _ctx()
    ctx2.mode_trace.append(_sw("别的规则", to_mode="agent", ago=config.HYBRID_SWITCH_COOLDOWN + 10))
    check(eng.decide(ctx2, "node_failed").rule == "r1", "超出方向冷却窗口：放行")

    ctx3 = _ctx()
    ctx3.mode_trace.append(_sw("别的规则", to_mode="workflow", ago=3))
    check(eng.decide(ctx3, "node_failed").rule == "r1", "反方向切换不算同方向：放行")

    eng0 = SwitchEngine(
        [
            {
                "name": "free",
                "signal": "requires_agent",
                "action": "to_agent",
                "priority": 10,
                "cooldown_seconds": 0,
            },
        ]
    )
    ctx4 = _ctx()
    ctx4.mode_trace.append(_sw("x", to_mode="agent", ago=1))
    check(eng0.decide(ctx4, "requires_agent").rule == "free", "0 冷却规则同样豁免方向防抖")


# ==================== 5. 硬上限 ====================


def test_5_max_switches() -> None:
    print("\n[5] 切换次数硬上限")
    eng = SwitchEngine(
        [
            {
                "name": "any",
                "signal": "node_failed",
                "action": "to_agent",
                "priority": 10,
                "cooldown_seconds": 0,
            },
        ]
    )
    ctx = _ctx()
    for i in range(config.HYBRID_MAX_SWITCHES):
        ctx.mode_trace.append(_sw(f"r{i}", ago=1000))
    d = eng.decide(ctx, "node_failed")
    check(d.action == "terminate" and d.rule == "__max_switches__", "达上限强制 terminate")
    check("上限" in d.reason, "reason 说明强制收工原因")

    ctx2 = _ctx()
    for i in range(config.HYBRID_MAX_SWITCHES - 1):
        ctx2.mode_trace.append(_sw(f"r{i}", ago=1000))
    check(eng.decide(ctx2, "node_failed").rule == "any", "未达上限正常判定")


# ==================== 6. classify_task ====================


def test_6_classify_task() -> None:
    print("\n[6] classify_task 入口分类")
    check(classify_task("先查一下天气，然后整理成周报") == "flow", "流程关键词多 → flow")
    check(classify_task("按照以下步骤批量处理并保存") == "flow", "批量/步骤/保存 → flow")
    check(classify_task("如何评价量子计算的发展前景") == "open", "开放关键词 → open")
    check(classify_task("帮我调研一下竞品") == "open", "调研 → open")
    check(classify_task("你好") == "open", "无关键词持平 → open（闲聊不背流程壳）")
    check(classify_task("先做这个，然后呢？") == "flow", "流程词多于开放词 → flow")


def main() -> int:
    print("=" * 60)
    print("切换引擎回归（命中 / 条件 / 防抖 / 上限 / 分类）")
    print("=" * 60)
    test_1_match_and_priority()
    test_2_condition()
    test_3_rule_cooldown()
    test_4_direction_cooldown()
    test_5_max_switches()
    test_6_classify_task()
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
