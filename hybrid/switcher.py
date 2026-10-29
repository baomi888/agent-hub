"""切换引擎（要求 1/5：自动切换规则 + 防抖 + 上限）。

规则数据驱动（存 switch_rules 表，这里只收生效规则列表）：
  signal    —— 引擎上报的事件名（requires_agent / node_failed / budget_exhausted …）
  condition —— 可选，对 {shared, signal, ctx} 的受限表达式（expr.eval_condition）
  action    —— to_agent / to_workflow / terminate
  priority  —— 数字小者先求值；第一个 signal 匹配且 condition 成立的规则胜出

防抖设计（防规则打架导致的乒乓，比"不出事"更重要的是"出事能兜底"）：
  1. 规则级：同名规则 cooldown_seconds 内不重复触发（显式 0 = 结构型规则豁免，
     如 requires_agent/goal_satisfied 是流程语义的一部分，不该被防抖吃掉）；
  2. 全局级：同方向（from→to）HYBRID_SWITCH_COOLDOWN 秒内只切一次，
     同样豁免显式 0 冷却的规则；
  3. 硬上限：HYBRID_MAX_SWITCHES 次切换后强制 terminate，宁可收工也不乒乓。

人工切换不经过这里：编排器直处（优先级 0，无条件立即生效）。
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from core import config
from hybrid.context import TaskContext
from hybrid.expr import eval_condition

RuleAction = Literal["to_agent", "to_workflow", "terminate"]


class SwitchRule(BaseModel):
    name: str
    signal: str
    condition: str = ""
    action: RuleAction
    reason: str = ""
    priority: int = 100
    cooldown_seconds: int = 30


class SwitchDecision(BaseModel):
    """判定结果。continue = 没有规则命中，由编排器按引擎结果默认处理。"""

    action: Literal["to_agent", "to_workflow", "terminate", "continue"]
    rule: str = ""
    reason: str = ""


class SwitchEngine:
    def __init__(self, rules: list[dict | SwitchRule]):
        parsed = [r if isinstance(r, SwitchRule) else SwitchRule.model_validate(r) for r in rules]
        self.rules = sorted(parsed, key=lambda r: r.priority)

    def decide(
        self, ctx: TaskContext, signal: str, signal_payload: dict | None = None
    ) -> SwitchDecision:
        # 硬上限最先查：已经切了 N 次还在出信号，说明规则在打架
        if len(ctx.mode_trace) >= config.HYBRID_MAX_SWITCHES:
            return SwitchDecision(
                action="terminate",
                rule="__max_switches__",
                reason=f"模式切换已达上限（{config.HYBRID_MAX_SWITCHES} 次），强制收工防乒乓",
            )
        now = int(time.time())
        for rule in self.rules:
            if rule.signal != signal:
                continue
            if rule.cooldown_seconds > 0:
                if self._rule_fired_within(ctx, rule.name, rule.cooldown_seconds, now):
                    continue
                target = {"to_agent": "agent", "to_workflow": "workflow"}.get(rule.action)
                if target and self._direction_fired_within(
                    ctx, target, config.HYBRID_SWITCH_COOLDOWN, now
                ):
                    continue
            if not rule.condition or self._eval(rule.condition, ctx, signal_payload):
                return SwitchDecision(
                    action=rule.action, rule=rule.name, reason=rule.reason or rule.name
                )
        return SwitchDecision(action="continue")

    @staticmethod
    def _eval(condition: str, ctx: TaskContext, signal_payload: dict | None) -> bool:
        scope = {
            "shared": ctx.shared,
            "signal": signal_payload or {},
            "ctx": {"goal": ctx.goal, "status": ctx.status, "current_mode": ctx.current_mode},
        }
        return eval_condition(condition, scope)

    @staticmethod
    def _rule_fired_within(ctx: TaskContext, name: str, seconds: int, now: int) -> bool:
        return any(sw.rule == name and now - sw.ts < seconds for sw in ctx.mode_trace)

    @staticmethod
    def _direction_fired_within(ctx: TaskContext, target: str, seconds: int, now: int) -> bool:
        return any(sw.to_mode == target and now - sw.ts < seconds for sw in ctx.mode_trace)


# ==================== 入口任务分类（决定初始模式）====================
# 2026-10-29：词表与打分逻辑搬到 `core/router.py`。
#
# 为什么搬走：原来只有 mode=hybrid 会调用它，规则错了也只影响手动选混合的人；
# 现在 auto 也要用它（"规则直接走工作流"），影响面变成全部提问，所以
#   ① 加固了词表（删单字弱词、加顺序配对、按强弱分档、阈值提到 2 分）；
#   ② 归位到 core，避免 core → hybrid 的反向依赖（hybrid 本来就依赖 core）。
#
# 这里保留 `classify_task` 这个名字：api/chat.py 的混合分支与既有测试都用它，
# 改名只会制造无谓的改动面。新代码请直接用 core.router。


def classify_task(goal: str) -> Literal["flow", "open"]:
    """把用户目标粗分为 流程型(flow→工作流起跑) / 开放型(open→Agent 起跑)。

    实现见 `core.router.classify_flow_open`：高精度取向，拿不准就判 open
    交给执行前路由的模型判断，避免给闲聊/单步任务背工作流壳。
    """
    from core.router import classify_flow_open

    return classify_flow_open(goal)
