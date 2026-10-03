# -*- coding: utf-8 -*-
"""Agent checkpointer 回归测试（纯本地、不联网、不需要 API Key）。

背景（2026-10-02 线上事故）：
    新建会话正常，但一发消息就报
        TypeError: Invalid checkpointer provided. Expected an instance of
        `BaseCheckpointSaver`, `True`, `False`, or `None`. Received _GeneratorContextManager.
    原因是 `agent/builder.py` 里写了 `return SqliteSaver.from_conn_string(path)` ——
    那是个 @contextmanager，返回上下文管理器而不是 saver 实例。

本测试锁死三件事，防止有人照直觉改回去：
    1. `_make_checkpointer()` 必须返回 None（不用检查点）
    2. `create_agent(checkpointer=None)` 能正常构造并跑通
    3. 如果用 MemorySaver + "每轮重放全量历史"（= api/chat.py 的做法），消息会**重复累积**
       —— 这条是"为什么不能挂检查点"的活证据

直接跑：python tests/test_agent_checkpointer.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


def test_builder_checkpointer_is_none():
    """builder 里的 checkpointer 必须是 None，且不再是上下文管理器。"""
    from agent.builder import _make_checkpointer, get_checkpointer

    cp = _make_checkpointer()
    assert cp is None, f"_make_checkpointer() 应返回 None，实际 {type(cp).__name__}：{cp!r}"
    assert get_checkpointer() is None, "get_checkpointer() 也应返回 None"
    print("PASS 1: _make_checkpointer() / get_checkpointer() 都是 None")


def test_create_agent_accepts_none_checkpointer():
    """create_agent(checkpointer=None) 能构造；astream 能跑（api/chat.py 走的就是 astream）。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    model = GenericFakeChatModel(messages=iter([AIMessage(content="你好，我是助手。")] * 8))
    agent = create_agent(model=model, tools=[], system_prompt="你是助手", checkpointer=None)

    async def run():
        events = []
        async for ev in agent.astream(
            {"messages": [HumanMessage(content="你好")]},
            config={"configurable": {"thread_id": "regression"}},
            stream_mode=["updates", "messages"],
        ):
            events.append(ev)
        return events

    events = asyncio.run(run())
    assert events, "astream 一个事件都没产出"
    kinds = {e[0] for e in events if isinstance(e, tuple) and len(e) == 2}
    assert "messages" in kinds, f"没收到 messages 流式事件，实际 {kinds}"
    print(f"PASS 2: create_agent(checkpointer=None) + astream 正常，事件 {len(events)} 个，类型 {kinds}")


class _S(TypedDict):
    messages: Annotated[list, add_messages]


def _echo(state: _S):
    return {"messages": [AIMessage(content="回复:" + str(state["messages"][-1].content))]}


def _graph(cp):
    g = StateGraph(_S)
    g.add_node("echo", _echo)
    g.add_edge(START, "echo")
    g.add_edge("echo", END)
    return g.compile(checkpointer=cp)


def test_full_history_replay_duplicates_with_checkpointer():
    """活证据：chat.py 每轮重放全量历史 + 挂检查点 = 消息重复累积。

    修复前这种膨胀是真实发生的（只是当时还没跑到这一步就先 TypeError 了）。
    """
    turns = [
        [HumanMessage(content="Q1")],
        [HumanMessage(content="Q1"), AIMessage(content="回复:Q1"), HumanMessage(content="Q2")],
    ]

    async def run_with_cp():
        app = _graph(MemorySaver())
        cfg = {"configurable": {"thread_id": "dup"}}
        sizes = []
        for msgs in turns:
            await app.ainvoke({"messages": msgs}, cfg)
            snap = await app.aget_state(cfg)
            sizes.append(len(snap.values.get("messages", [])))
        return sizes

    async def run_without_cp():
        app = _graph(None)
        sizes = []
        for msgs in turns:
            out = await app.ainvoke({"messages": msgs}, {"configurable": {"thread_id": "dup"}})
            sizes.append(len(out["messages"]))
        return sizes

    with_cp = asyncio.run(run_with_cp())
    without_cp = asyncio.run(run_without_cp())

    # 第 2 轮：带了检查点会变成 6 条（Q1/A1 各出现两次），不带就是正确的 4 条
    assert with_cp == [2, 6], f"预期带检查点第2轮膨胀到 6 条，实际 {with_cp}"
    assert without_cp == [2, 4], f"预期不带检查点是 2/4 条，实际 {without_cp}"
    print(f"PASS 3: 带检查点 {with_cp}（重复累积）vs 不带 {without_cp}（正确）")


if __name__ == "__main__":
    test_builder_checkpointer_is_none()
    test_create_agent_accepts_none_checkpointer()
    test_full_history_replay_duplicates_with_checkpointer()
    print("\n全部通过：Agent checkpointer 回归测试 OK")
