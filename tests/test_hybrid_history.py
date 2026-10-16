# -*- coding: utf-8 -*-
"""混合模式跨轮记忆（P0）+ 计划提示词服务端化（P1）回归测试。

不联网、不依赖真实 LLM / DB：
  1. 历史助手函数：条数上限、非法项过滤、关闭开关(0)退回空；
  2. AgentEngine：历史被前置进 Agent 的 messages（这才是"失忆"的修复点）；
  3. WorkflowEngine LLM 节点：历史以文本前缀拼进 prompt；
  4. api.chat._augment_plan：plan=True 才追加格式要求，且不改动原问题。

可从任意位置运行：pytest tests/test_hybrid_history.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from api.chat import _augment_plan  # noqa: E402
from core import config  # noqa: E402
from hybrid.agent_engine import (  # noqa: E402
    AgentEngine,
    history_items,
    history_messages,
    history_prefix,
)
from hybrid.context import TaskContext  # noqa: E402
from hybrid.workflow import NodeDef, WorkflowDef, WorkflowEngine  # noqa: E402

_HIST = [
    {"role": "user", "content": "我现在想备考六级"},
    {"role": "assistant", "content": "六级是系统工程，先说你四级多少分"},
    {"role": "system", "content": "这条应被过滤"},  # 非法角色
    {"role": "user", "content": ""},  # 空内容应被过滤
]


def _ctx(goal="当前问题", history=None):
    ctx = TaskContext(owner_id="u1", goal=goal)
    if history is not None:
        ctx.shared["history"] = history
    return ctx


def _no_control():
    return None


# ==================== 1. 历史助手函数 ====================

def test_history_items_filters_invalid():
    items = history_items(_ctx(history=_HIST))
    assert items == [
        {"role": "user", "content": "我现在想备考六级"},
        {"role": "assistant", "content": "六级是系统工程，先说你四级多少分"},
    ], items


def test_history_items_cap_keeps_latest(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_HISTORY_MAX_MESSAGES", 2)
    long_hist = [{"role": "user", "content": f"第{i}条"} for i in range(6)]
    items = history_items(_ctx(history=long_hist))
    assert [m["content"] for m in items] == ["第4条", "第5条"], items


def test_history_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_HISTORY_MAX_MESSAGES", 0)
    assert history_items(_ctx(history=_HIST)) == []
    assert history_messages(_ctx(history=_HIST)) == []
    assert history_prefix(_ctx(history=_HIST)) == ""


def test_history_prefix_and_messages_shape():
    msgs = history_messages(_ctx(history=_HIST))
    assert isinstance(msgs[0], HumanMessage) and msgs[0].content == "我现在想备考六级"
    assert isinstance(msgs[1], AIMessage) and "四级" in msgs[1].content

    prefix = history_prefix(_ctx(history=_HIST))
    assert "对话历史" in prefix and "六级" in prefix and prefix.endswith("\n\n")


# ==================== 2. AgentEngine 注入历史 ====================

class _RecordingAgent:
    """假 Agent：记录每次 astream 收到的 messages，不产出事件。"""

    def __init__(self):
        self.calls = []

    async def astream(self, inputs, config=None, stream_mode=None):  # noqa: A002
        self.calls.append(list(inputs["messages"]))
        for _ in ():  # 空循环：把本函数变成"不产出任何事件"的异步生成器
            yield _


def test_agent_engine_injects_history():
    fake = _RecordingAgent()
    ctx = _ctx(goal="那每天怎么安排？", history=_HIST)
    eng = AgentEngine(agent=fake)
    asyncio.run(_drain(eng.stream(ctx, _no_control)))

    assert len(fake.calls) == 1
    got = fake.calls[0]
    assert [type(m) for m in got] == [HumanMessage, AIMessage, HumanMessage]
    assert got[0].content == "我现在想备考六级"
    assert "四级" in got[1].content
    assert got[2].content == "那每天怎么安排？"


def test_agent_engine_without_history_single_turn():
    fake = _RecordingAgent()
    ctx = _ctx(goal="你好", history=[])
    eng = AgentEngine(agent=fake)
    asyncio.run(_drain(eng.stream(ctx, _no_control)))

    got = fake.calls[0]
    assert len(got) == 1 and got[0].content == "你好"


async def _drain(agen):
    async for _ in agen:
        pass


# ==================== 3. WorkflowEngine LLM 节点拼接历史前缀 ====================

class _RecordingLLM:
    def __init__(self):
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return type("R", (), {"content": "ok"})()


def _one_node_wf():
    return WorkflowDef(
        id="w", nodes=[NodeDef(id="n1", type="llm", config={"prompt": "请回答：{question}"})],
        entry="n1", edges=[],
    )


def test_workflow_llm_node_gets_history_prefix():
    fake = _RecordingLLM()
    ctx = _ctx(goal="再细一点", history=_HIST)
    eng = WorkflowEngine(_one_node_wf(), llm=fake)
    asyncio.run(_drain(eng.stream(ctx, _no_control)))

    assert len(fake.prompts) == 1
    prompt = fake.prompts[0]
    assert "对话历史" in prompt and "六级" in prompt  # 历史进了 prompt
    assert "请回答：再细一点" in prompt  # 本轮 goal 也在
    assert ctx.shared.get("node_n1") == "ok"


# ==================== 4. 计划提示词服务端化 ====================

def test_augment_plan_appends_hint_only_when_plan():
    raw = "给我列一份每日计划"
    assert _augment_plan(raw, False) == raw
    with_hint = _augment_plan(raw, True)
    assert with_hint.startswith(raw)
    assert "【格式要求】" in with_hint and "时间轴" in with_hint
