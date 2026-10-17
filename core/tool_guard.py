# -*- coding: utf-8 -*-
"""工具守卫：让 `TOOL_META` 的声明**真正生效**（P0-3）。

背景：`core/tool_registry.py` 已经把五个工具的元数据声明齐了（category /
retryable / needs_approval / max_output_chars / cost_hint），但**没有任何执行路径读它**——
声明躺在表里，模型调用时照旧全量返回、失败了照旧反复重试。
这份文件把声明接到执行上。

三条守卫，都**不改变模型看到的 schema**（模型不该知道守卫存在）：

1. **输出截断**（`max_output_chars`）
   超限则原文落盘、只回「前 N 字 + 落盘路径」。防单次工具调用吃满上下文窗口。
   与 mewcode 的 `MAX_OUTPUT_CHARS` 同一思路，常数按工具分档。

2. **不可重试工具封禁**（`retryable=False`）
   确定性失败（库不存在 / 无权）之后再调同一个工具，直接返回「别调了」，
   **不再真的执行**。这是 10-06 报告里「模型原地重试 2~3 次」的根治——
   之前靠返回文案劝它，现在是引擎确定性拒绝。

3. **危险动作审批**（`needs_approval=True`）
   首次调用不执行，回一条「需要确认 + 确认串」；模型带**同一个确认串**再调才执行。
   确认串是 `(工具名, 参数)` 的 hash —— 参数改了 hash 就变，等于**批准的是这一次**，
   不是「以后都可以」。这比"调用两次就放行"严格，也比等前端 UI 便宜。

为什么用 contextvars 而不是往 schema 里加字段：
schema 会发给模型，加 `confirm` 参数等于教它绕守卫。状态走 contextvar，
工具签名与 docstring 一字不改（见 `agent/tools.py` 的 `AGENT_TOOLS` 装配）。
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import inspect
import json
import os
import time
from typing import Any

from core import config

# ==================== 每次执行的守卫状态 ====================
# contextvars：同一进程内并发跑多个请求时互不污染（asyncio 下天然按任务隔离）。


class _GuardState:
    """一次 Agent 执行期间的守卫状态。"""

    def __init__(self) -> None:
        # 已确定失败且不可重试的工具名
        self.banned: set[str] = set()
        # 已发出待确认请求：工具名 -> 确认串
        self.pending: dict[str, str] = {}
        # 命中封禁/截断的次数，便于日志与测试观测
        self.blocked_calls = 0
        self.truncated = 0


_state_var: contextvars.ContextVar[_GuardState | None] = contextvars.ContextVar(
    "tool_guard_state", default=None
)


def current_state() -> _GuardState | None:
    return _state_var.get()


@contextlib.contextmanager
def guard_scope():
    """在一次 Agent 执行外面包一层，隔离封禁/待确认状态。

    用法（api/chat.py 的 agent 路径）：
        with tool_guard.guard_scope():
            async for chunk in agent.astream(...): ...

    不加也能工作（退化为进程级共享），但并发会话间会互相影响，
    所以调用方应尽量加上。
    """
    token = _state_var.set(_GuardState())
    try:
        yield _state_var.get()
    finally:
        _state_var.reset(token)


def _state() -> _GuardState:
    st = _state_var.get()
    if st is None:
        st = _GuardState()
        _state_var.set(st)
    return st


# ==================== 确认串 ====================

def approval_token(tool_name: str, args: dict[str, Any]) -> str:
    """(工具名, 参数) 的稳定 hash。参数变了 -> 串变了 -> 必须重新确认。"""
    payload = json.dumps(
        {"tool": tool_name, "args": args}, ensure_ascii=False, sort_keys=True, default=str
    )
    return "confirm:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ==================== 输出截断 ====================

def _spill_dir() -> str:
    d = os.path.join(getattr(config, "DATA_DIR", ".") or ".", "tool_outputs")
    os.makedirs(d, exist_ok=True)
    return d


def truncate_output(tool_name: str, text: str, limit: int) -> str:
    """超限则落盘 + 只回预览。落盘失败也要截断（不能因为 IO 失败把长文丢给模型）。"""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    n = len(text)
    try:
        path = os.path.join(_spill_dir(), f"{tool_name}_{int(time.time() * 1000)}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return (
            text[:limit]
            + f"\n\n... [输出已截断：共 {n} 字符，上限 {limit}；完整内容已落盘 {path}，"
            "需要时读取该文件]"
        )
    except Exception:  # noqa: BLE001
        return text[:limit] + f"\n\n... [输出已截断：共 {n} 字符，上限 {limit}]"


# ==================== 守卫主逻辑 ====================

def guarded_run(tool_name: str, original_run, args: dict[str, Any], run_manager=None,
                call_args: tuple = (), call_kwargs: dict | None = None):
    """包装一个工具的 `_run`：依次做 审批 / 封禁 / 执行 / 截断。

    `args` 是**工具入参**（用于审批 hash），`call_args`/`call_kwargs` 是
    LangChain 原样传进来的调用参数——执行时要用后者，否则 config 注入会丢。
    返回字符串（与原工具返回值同型）。
    """
    from core.tool_registry import TOOL_META

    meta = TOOL_META.get(tool_name)
    st = _state()

    # ---------- 1. 已封禁：直接拒绝，不执行 ----------
    if tool_name in st.banned:
        st.blocked_calls += 1
        return (
            f"⚠️ 工具 {tool_name} 之前已确定失败（不可重试），本轮不再执行。"
            "请改用其他途径，或直接告诉用户当前条件不具备。**不要重复调用本工具。**"
        )

    # ---------- 2. 需要审批：首次只回确认串 ----------
    if meta is not None and meta.needs_approval:
        token = approval_token(tool_name, args)
        if not config.TOOL_APPROVAL_ENABLED:
            pass  # 全局关闭审批（调试/演示用），直接执行
        elif st.pending.get(tool_name) != token:
            # 首次（或参数变了）：登记 + 不执行
            st.pending[tool_name] = token
            st.blocked_calls += 1
            return (
                f"⏸ 工具 {tool_name} 需要用户确认后才能执行（它会改动外部状态：写入/入库等）。\n"
                f"参数：{json.dumps(args, ensure_ascii=False, default=str)[:400]}\n"
                f"如果用户已确认，请带着确认串再调用一次：{token}\n"
                "如果用户尚未确认，请先向用户说明将要做什么并征求同意，不要自行重试。"
            )
        else:
            # 带着匹配的确认串回来：消费掉，继续执行
            st.pending.pop(tool_name, None)

    # ---------- 3. 执行 ----------
    try:
        # 用 LangChain 原样传进来的调用参数执行（保住 config / run_manager 注入）
        kw = dict(call_kwargs or {})
        if run_manager is not None:
            kw["run_manager"] = run_manager
        out = original_run(*call_args, **kw)
    except Exception as e:  # noqa: BLE001
        # 失败分类：不可重试的登记封禁，后续同类调用直接被拒
        if meta is not None and not meta.retryable:
            st.banned.add(tool_name)
        if meta is not None and not meta.retryable:
            return (
                f"工具 {tool_name} 执行失败（确定性失败，重试不会改变结果）：{type(e).__name__}: {e}\n"
                "请换用其他工具或直接告知用户当前条件不具备。**不要重复调用本工具。**"
            )
        return f"工具 {tool_name} 执行失败（临时性，可再试）：{type(e).__name__}: {e}"

    # ---------- 4. 输出截断 ----------
    if meta is not None and isinstance(out, str):
        new = truncate_output(tool_name, out, meta.max_output_chars)
        if new is not out and len(new) != len(out):
            st.truncated += 1
        out = new

    # ---------- 5. 失败态封禁（工具本身把错误写在字符串里，不抛异常的情况）----------
    if meta is not None and not meta.retryable and isinstance(out, str):
        if _looks_like_deterministic_failure(out):
            st.banned.add(tool_name)
    return out


# 工具把错误写成"返回值"而不是抛异常时，用文案特征识别确定性失败。
# 只用于 retryable=False 的工具，且只影响"后续是否允许再调"，不改变本次返回内容。
_FAILURE_HINTS = (
    "无法访问", "不存在", "没有绑定", "不可用", "无权", "未找到", "下载失败", "检索不可用",
)


def _looks_like_deterministic_failure(text: str) -> bool:
    return any(h in text for h in _FAILURE_HINTS)


# ==================== 装配：重建带守卫的工具 ====================


def build_guarded_tools(originals: list) -> list:
    """把原始工具列表重建为"带守卫"的工具列表。

    为什么用 `StructuredTool.from_function` 重建，而不是原地包装：
    LangChain 的 `StructuredTool` 会在两层做签名内省并注入框架参数——
    `_run` 层注入 `config`/`run_manager`，`func` 层再注入 `config`。
    从外面包 `_run` 或 `func` 都会破坏这件事（实测两次踩坑，详见 git 历史）。
    重建则让 LangChain 自己处理签名：我们只把"守卫后的业务函数"交给它，
    并**原样沿用** name / description / args_schema，模型观感不变。

    守卫后的业务函数用 `functools.wraps` 保留原签名，
    这样 `config` 这类 RunnableConfig 参数依旧会被 LangChain 正确注入。
    """
    from langchain_core.tools import StructuredTool

    out = []
    for t in originals:
        fn = getattr(t, "func", None)
        if fn is None:
            # 非 StructuredTool（不应出现）：原样返回，宁可没守卫也别弄坏
            out.append(t)
            continue
        guarded_fn = _make_guarded_fn(t.name, fn)
        out.append(
            StructuredTool.from_function(
                func=guarded_fn,
                name=t.name,
                description=t.description,
                args_schema=t.args_schema,
            )
        )
    return out


def _make_guarded_fn(tool_name: str, fn):
    """把业务函数包成"先过守卫再执行"的版本，并保留原签名。"""
    import functools

    @functools.wraps(fn)
    def _guarded(*args, **kwargs):
        # 工具入参：位置/关键字参数即模型给的参数（config/run_manager 由框架注入，
        # 不算用户可决定的部分，不进审批 hash）
        ui_kwargs = {k: v for k, v in kwargs.items() if k not in ("config", "run_manager")}
        if args:
            ui_args = dict(ui_kwargs)
            ui_args["_args"] = args
        else:
            ui_args = ui_kwargs
        return guarded_run(
            tool_name, fn, ui_args, kwargs.get("run_manager"), args, kwargs
        )

    return _guarded


class guard_scope_async:
    """`guard_scope` 的异步版：`async with tool_guard.guard_scope_async(): ...`。

    为什么需要：Agent 的执行体在 `async for` 里，套同步 `with` 会把整段缩进推深一层。
    异步上下文管理器让调用方零缩进改动。
    """

    def __init__(self) -> None:
        self._token = None

    async def __aenter__(self) -> _GuardState:
        self._token = _state_var.set(_GuardState())
        return _state_var.get()  # type: ignore[return-value]

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._token is not None:
            _state_var.reset(self._token)
            self._token = None
        return False


@contextlib.asynccontextmanager
async def aguard_scope():
    """异步作用域（供 async with 用）。"""
    token = _state_var.set(_GuardState())
    try:
        yield _state_var.get()
    finally:
        _state_var.reset(token)
