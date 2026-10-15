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

import os
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from core import config

ModeName = Literal["workflow", "agent"]

# 执行状态机：running 只往这四个终态走，引擎返回结果时由编排器落锤。
# awaiting_human 是"挂起"（human 节点等人确认），不是终态——恢复后回到 running。
TaskStatus = Literal["running", "completed", "failed", "timeout", "stopped", "awaiting_human"]

# ==================== shared 的三条防线（为什么）====================
# shared 是两种模式之间**唯一**的数据交换通道。它出问题不是"某个功能坏了"，
# 而是全链路静默错误，所以在这里硬拦：
#
#   1. 值上限：节点产物与 Agent 产出都往里写，长文一进去，"切换即快照"
#      会把整份 context 落库（workflow_runs.context），库会无声膨胀。
#      超限值改为"落盘 + 存预览指针"，与 exec_logs 的 payload 截断同一思路。
#   2. 键命名空间：shared 是扁平 dict，两个写入方可能撞名。
#      约定前缀 `node:` / `agent:` / `wf:`；裸键视为共享输入，允许存在，
#      但同一逻辑名被不同前缀重复写入时抛错，避免静默覆盖。
#   3. goal 只读：事后改 goal 会让 mode_trace 失去可复现性
#      （回放时不知道当时的任务目标是什么）。
# ================================================================

SHARED_MAX_VALUE_CHARS = int(os.getenv("HYBRID_SHARED_MAX_VALUE_CHARS", "8000"))
SHARED_SPILL_SUBDIR = "shared_spill"
_KEY_PREFIXES = ("node:", "agent:", "wf:")


class SharedKeyError(ValueError):
    """shared 键的命名空间冲突。"""


def _spill_dir() -> str:
    d = os.path.join(getattr(config, "DATA_DIR", ".") or ".", SHARED_SPILL_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def spill_value(value: Any, key: str) -> str:
    """把超限值落盘，返回预览指针。落盘失败也不抛——退化为纯截断预览。"""
    text = value if isinstance(value, str) else str(value)
    n = len(text)
    try:
        safe = "".join(c if c.isalnum() else "_" for c in str(key))[:60]
        path = os.path.join(_spill_dir(), f"{safe}_{int(time.time() * 1000)}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return f"[值 {n} 字符已落盘：{path} — 需要时读取该文件]"
    except Exception:  # noqa: BLE001 —— 落盘失败也不能让执行中断
        return f"[值 {n} 字符超限且落盘失败，已截断：{text[:200]}...]"


def shared_set(shared: dict[str, Any], key: str, value: Any, *, strict: bool = True) -> None:
    """写 shared 的推荐入口：校验键 + 超限落盘。strict=False 用于恢复历史快照。"""
    if not isinstance(key, str) or not key.strip():
        raise SharedKeyError("shared 的键不能为空")
    if strict:
        for exist in shared:
            if exist == key:
                continue
            if exist.split(":", 1)[-1] == key.split(":", 1)[-1]:
                raise SharedKeyError(
                    f"shared 键命名空间冲突：{exist!r} 与 {key!r} 指向同一逻辑名；"
                    f"规范前缀为 {_KEY_PREFIXES}"
                )
    if isinstance(value, str) and len(value) > SHARED_MAX_VALUE_CHARS:
        value = spill_value(value, key)
    shared[key] = value


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
    跨模式递数据一律走 shared（见文件顶部三条防线）。
    """

    # 开启赋值校验，才能对 goal 做"起跑后只读"
    model_config = {"validate_assignment": True}

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

    # goal 只读的实现：第一次成功赋值后置位，之后只允许赋同一个值。
    # 为什么不用 property 覆盖字段——那会让 pydantic 丢掉字段定义，
    # 是**错的**做法（实测会把字段从 model_fields 里抹掉）。
    _goal_locked: bool = PrivateAttr(default=False)

    def set_goal(self, value: str) -> None:
        """设定任务目标（只允许在起跑时设一次）。

        之后想改必须显式调用 `override_goal()`，那会记一条 mode_trace，
        让"目标被改过"这件事在回放里可见——而不是静默漂移。
        """
        if self._goal_locked and value != self.goal:
            raise ValueError(
                "goal 在任务起跑后只读：改目标会让 mode_trace 失去可复现性；"
                "确需变更请用 override_goal() 并说明理由"
            )
        self.goal = value
        self._goal_locked = True

    def __setattr__(self, name: str, value: Any) -> None:
        """goal 的只读硬闸：所有赋值路径（含直接赋值 `ctx.goal = ...`）都经过这里。

        为什么必须在这一层拦：只靠 `set_goal()` 的检查拦不住直接赋值，
        而 pydantic 的 validate_assignment 仍然会走 `__setattr__`，
        所以这里是唯一真正封死的地方。
        """
        if name == "goal" and getattr(self, "_goal_locked", False):
            if value != self.__dict__.get("goal"):
                raise ValueError(
                    "goal 在任务起跑后只读：改目标会让 mode_trace 失去可复现性；"
                    "确需变更请用 override_goal(reason=...)"
                )
        super().__setattr__(name, value)

    def override_goal(self, value: str, reason: str) -> None:
        """显式改目标：留痕（写进 mode_trace），使回放仍可解释。"""
        self._goal_locked = False
        object.__setattr__(self, "goal", value)
        self._goal_locked = True
        self.mode_trace.append(
            ModeSwitch(from_mode=self.current_mode, to_mode=self.current_mode,
                       rule="goal_override", reason=reason)
        )
        self.bump()

    def set_shared(self, key: str, value: Any, *, strict: bool = True) -> None:
        """写入 shared：校验命名空间 + 超限落盘。见文件顶部三条防线。"""
        shared_set(self.shared, key, value, strict=strict)
        self.bump()

    def get_shared(self, key: str, default: Any = None) -> Any:
        return self.shared.get(key, default)

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
