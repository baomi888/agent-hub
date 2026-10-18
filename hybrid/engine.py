# -*- coding: utf-8 -*-
"""执行引擎契约：EngineResult + ExecutionEngine 协议（要求 7 可扩展性的落点）。

编排器只认这两个东西：
  - 每个引擎是一个 async generator（stream），产出 ExecEvent；
  - 迭代结束后读引擎实例的 .result 拿 EngineResult，决定下一步（续跑/切换/终止）。

新增执行模式（人工审批、批处理…）只要实现同一协议并注册进
orchestrator.ENGINE_REGISTRY，编排器与切换引擎完全无感知。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Literal, Protocol

from pydantic import BaseModel

from hybrid.context import ExecEvent, TaskContext

# 引擎跑完一段后的落锤状态：
#   completed 正常完成（工作流跑到底 / Agent 产出达标）
#   failed    失败（细节在 error；signal 通常带 node_failed 等供切换判定）
#   timeout   整体超时
#   switched  引擎主动请求切换（如工作流遇到 requires_agent 节点）
#   awaiting_human  人工节点挂起，等恢复（不是终态）
EngineStatus = Literal["completed", "failed", "timeout", "switched", "awaiting_human"]


class EngineResult(BaseModel):
    """一段执行的结论。signal 是给切换引擎的事件名（None = 无需切换判定）。"""

    status: EngineStatus
    signal: str | None = None
    node_id: str | None = None  # 触发信号的工作流节点
    error: str | None = None
    output_key: str | None = None  # 最终产出写在 shared 的哪个 key


# 人工切换轮询回调：引擎在循环节点处调用，返回 None = 无指令，
# 否则返回 "to_agent" / "to_workflow" / "terminate"，引擎收尾后让出。
ControlFn = Callable[[], str | None]

# Agent 节点执行器工厂：编排器把"造一个 AgentEngine"的能力注入工作流引擎，
# 避免两个引擎直接互相 import（也便于测试注入假引擎）。
AgentEngineFactory = Callable[[], "ExecutionEngine"]


class ExecutionEngine(Protocol):
    """执行引擎协议。stream 迭代完后必须设置 self.result。"""

    name: str
    result: EngineResult | None

    def stream(
        self, ctx: TaskContext, control: ControlFn, **kwargs: Any
    ) -> AsyncIterator[ExecEvent]: ...
