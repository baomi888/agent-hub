# -*- coding: utf-8 -*-
"""统一任务上下文（要求 4 的核心载体）。

TaskContext 是两种执行模式之间**唯一**的数据交换通道：
  - 工作流节点产物、Agent 最终产出都必须写入 shared，另一方只从这里读；
  - 每次模式切换整体序列化落库（workflow_runs.context 快照），崩溃可续跑；
  - version 乐观锁：切换移交时校验，防止并发变更导致的状态撕裂。

三个模型各司其职：
  TaskContext  任务全量状态（跨模式传递）
  ModeSwitch   一次切换的完整记录（谁触发、为什么、什么时候）
  ExecEvent    执行监控事件（要求 6 的最小记录单元，同时进 exec_logs 与 SSE）
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

ModeName = Literal["workflow", "agent"]

# 执行状态机：running 只往这四个终态走，引擎返回结果时由编排器落锤
TaskStatus = Literal["running", "completed", "failed", "timeout", "stopped"]


class ModeSwitch(BaseModel):
    """一次模式切换记录：from/to + 触发规则 + 决策依据（监控日志直接落它）。"""

    from_mode: str
    to_mode: str
    rule: str  # 触发规则名；"manual" 表示人工干预
    reason: str  # 人类可读的决策依据，前端轨迹面板直接展示
    ts: int = Field(default_factory=lambda: int(time.time()))


class ExecEvent(BaseModel):
    """执行事件：node 进出 / 工具调用 / token / 切换 / 错误 / 人工干预。

    payload 只允许放摘要级数据（字符串截断在 store 层兜底），
    大文本直接落库会把 exec_logs 撑爆。
    """

    event_type: Literal[
        "node_enter", "node_exit", "tool_call", "token",
        "switch", "error", "status", "human_intervention",
    ]
    mode: str  # 事件发生时的模式：workflow / agent
    node_id: str | None = None  # 工作流事件必填，Agent 事件为空
    decision: dict | None = None  # switch 事件必填：规则名 + 求值输入 + 理由
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: int = Field(default_factory=lambda: int(time.time()))


class TaskContext(BaseModel):
    """统一任务上下文。

    workflow_state / agent_state 是各模式的**私有段**：
    对方模式可以读（用于决策），但只能写自己的段；
    跨模式递数据一律走 shared。
    """

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    owner_id: str = ""
    goal: str = ""
    status: TaskStatus = "running"
    error: str | None = None

    current_mode: ModeName = "agent"
    workflow_id: str | None = None

    # 工作流私有段：current_node（断点续跑锚点）、completed、node_outputs
    workflow_state: dict[str, Any] = Field(default_factory=dict)
    # Agent 私有段：steps / tool_calls / chars（近似 token 用量）/ started_ts / consec_failures
    agent_state: dict[str, Any] = Field(default_factory=dict)

    # 跨模式共享区：唯一数据交换通道
    shared: dict[str, Any] = Field(default_factory=dict)

    mode_trace: list[ModeSwitch] = Field(default_factory=list)
    version: int = 0

    def bump(self) -> int:
        """每次状态变更 +1，切换移交时双方比对，防止并发撕裂。"""
        self.version += 1
        return self.version

    def record_switch(self, to_mode: ModeName, rule: str, reason: str) -> ModeSwitch:
        """记录一次切换：追加 mode_trace、翻转 current_mode、版本号 +1。"""
        sw = ModeSwitch(from_mode=self.current_mode, to_mode=to_mode, rule=rule, reason=reason)
        self.mode_trace.append(sw)
        self.current_mode = to_mode
        self.bump()
        return sw

    def snapshot(self) -> dict:
        """整体快照（落 workflow_runs.context）；restore 可完整还原。"""
        return self.model_dump()

    @classmethod
    def restore(cls, data: dict) -> "TaskContext":
        """从快照还原。字段新增带默认值，旧快照可前向兼容（pydantic 兜住缺字段）。"""
        return cls.model_validate(data)
