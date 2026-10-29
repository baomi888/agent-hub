"""回归（2026-10-29，M-01）：混合执行的"答案"不能是 plan 的规划稿。

背景
----
改造前 chat 层取答案用的是 `next(reversed(ctx.shared.values()))` —— shared
里**最后一个值**。内置工作流是 plan → execute 两个节点，一旦 plan 成功、
execute 失败，最后一个值就是 plan 写进去的规划稿，于是：

  1. 用户看到的是「1. 2. 3.」的计划而不是答案（如果状态恰好不是 failed）；
  2. 更糟的是 acc_text 因为拿到了这段文字被判定为"有产出"，
     新加的 LLM 兜底永远不触发，最后还是回一句「混合执行失败」+ 空白。

现在改成两层：
  · 工作流在 node_exit 里带出 output_key（hybrid/workflow.py）；
  · chat 层按 last_ok_key → agent_result → 非中间产物 的优先级取
    （`_pick_hybrid_answer`），plan / history / node:* 这些键一律不认。

三条用例分别锁：优先级函数本身、引擎真的带 output_key、
以及"plan 成功 + execute 失败"时 chat 走兜底而不是把规划稿回给用户。
"""

from __future__ import annotations

import asyncio

import pytest

import api.chat as chat_mod
import core.account as account_mod
import core.session as session_mod
from hybrid.context import ExecEvent, TaskContext
from hybrid.engine import EngineResult
from hybrid.store import DEFAULT_WORKFLOW_DEF
from hybrid.workflow import WorkflowEngine, validate_definition


@pytest.fixture(autouse=True)
def _ensure_tables():
    """同 test_sse_disconnect：建表在 import 时只跑一次，
    别的用例把 _db_path 改到临时库后，这里要补齐（幂等）。"""
    session_mod._init_db()
    account_mod._ensure_tables()
    yield


# ==================== 1. 取答案的优先级 ====================


class TestPickAnswer:
    def test_plan_only_is_not_an_answer(self):
        """只有 plan（规划稿）→ 空串。这是本 bug 的核心。"""
        assert chat_mod._pick_hybrid_answer({"plan": "1. 打开文档\n2. 提取要点"}) == ""

    def test_agent_result_wins_over_plan(self):
        shared = {"plan": "三步走", "agent_result": "整理好的考点"}
        assert chat_mod._pick_hybrid_answer(shared) == "整理好的考点"

    def test_last_key_wins(self):
        """自定义工作流的末节点未必叫 agent_result，节点自己声明的键才权威。"""
        shared = {"plan": "三步走", "ka": "节点 a 的产出", "agent_result": "旧的"}
        assert chat_mod._pick_hybrid_answer(shared, "ka") == "节点 a 的产出"

    def test_last_key_pointing_at_plan_is_ignored(self):
        """末节点是 plan（execute 没跑）时，last_key 也救不回来 —— 仍判空。"""
        assert chat_mod._pick_hybrid_answer({"plan": "三步走"}, "plan") == ""

    def test_runtime_state_is_ignored(self):
        """history（list）、node:gate:approved、score 都不是答案。"""
        shared = {
            "history": [{"role": "user", "content": "上一轮"}],
            "node:gate:approved": "approved",
            "score": 0.9,
            "retries": 2,
        }
        assert chat_mod._pick_hybrid_answer(shared, "node:gate:approved") == ""

    def test_blank_string_is_not_an_answer(self):
        shared = {"plan": "三步走", "agent_result": "   "}
        assert chat_mod._pick_hybrid_answer(shared) == ""


# ==================== 2. 引擎真的带 output_key ====================


class _PlanLLM:
    """规划节点用的假模型（非流式 → ainvoke）。"""

    async def ainvoke(self, _prompt):
        return type("_Resp", (), {"content": "1. 打开文档\n2. 提取要点"})()


class _FailAgentEngine:
    """执行节点：一上来就报错，模拟 agent 崩了。"""

    name = "agent"
    result = None

    async def stream(self, ctx, control, **_kw):  # noqa: ARG002
        yield ExecEvent(
            event_type="error", mode="agent", payload={"error": "agent 崩了"}
        )
        self.result = EngineResult(status="failed", error="agent 崩了")


async def _collect(agen):
    return [ev async for ev in agen]


def test_node_exit_carries_output_key():
    ctx = TaskContext(owner_id="u-pick", goal="整理笔记", current_mode="workflow")
    eng = WorkflowEngine(
        wf=validate_definition(DEFAULT_WORKFLOW_DEF),
        llm=_PlanLLM(),
        agent_engine_factory=lambda: _FailAgentEngine(),
    )
    events = asyncio.run(_collect(eng.stream(ctx, lambda: None)))

    ok_exits = [
        e for e in events if e.event_type == "node_exit" and e.payload.get("status") == "ok"
    ]
    assert [e.node_id for e in ok_exits] == ["plan"], "只有 plan 节点成功"
    assert ok_exits[0].payload.get("output_key") == "plan", "成功的 node_exit 必须带 output_key"

    assert ctx.shared.get("plan", "").startswith("1. 打开文档")
    assert "agent_result" not in ctx.shared, "execute 失败，不该凭空出现 agent_result"
    assert eng.result is not None and eng.result.status == "failed"


# ==================== 3. chat 层：plan 成功 + execute 失败 → LLM 兜底 ====================


class _Chunk:
    def __init__(self, text: str) -> None:
        self.content = text


class _FakeLLM:
    """兜底用的假模型。"""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts

    def astream(self, _messages):
        async def gen():
            for t in self.texts:
                yield _Chunk(t)

        return gen()


class _Req:
    async def is_disconnected(self) -> bool:
        return False


class _Ticket:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _PlanOnlyOrch:
    """假编排器：plan 节点跑完（声明 output_key=plan），execute 直接失败。"""

    def __init__(self, ctx, **_kw) -> None:
        self.ctx = ctx
        ctx.shared["plan"] = "1. 打开文档\n2. 提取要点\n3. 汇总成表"

    async def stream(self):
        yield ExecEvent(
            event_type="node_enter", mode="workflow", node_id="plan", payload={"type": "llm"}
        )
        yield ExecEvent(
            event_type="node_exit",
            mode="workflow",
            node_id="plan",
            payload={"status": "ok", "output_key": "plan"},
        )
        yield ExecEvent(
            event_type="error", mode="workflow", node_id="execute", payload={"error": "agent 崩了"}
        )
        self.ctx.status = "failed"
        self.ctx.error = "agent 崩了"


def test_plan_only_falls_back_to_llm(monkeypatch):
    """规划稿不能当答案：必须走 LLM 兜底，且最终文本里没有规划稿。"""
    saved: list[tuple] = []

    monkeypatch.setattr(chat_mod.store, "get_conversation", lambda uid, sid: {"kb_id": None})
    monkeypatch.setattr(chat_mod.quota, "consume", lambda uid, kind: 0)
    monkeypatch.setattr(chat_mod.store, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(chat_mod.store, "count_messages", lambda *a, **kw: 10)
    monkeypatch.setattr(
        chat_mod.store, "add_message", lambda *a, **kw: saved.append((a, kw)) or 1
    )
    monkeypatch.setattr(chat_mod.store, "rename_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(chat_mod.ctxcompress, "assemble_history", lambda *a, **kw: [])
    monkeypatch.setattr(chat_mod, "get_llm", lambda **kw: _FakeLLM(["直接", "回答"]))
    monkeypatch.setattr(chat_mod, "_schedule_compress", lambda uid, sid: None)
    monkeypatch.setattr(chat_mod.config, "USER_TAG_HOOK_ENABLED", False)
    monkeypatch.setattr(chat_mod.config, "HYBRID_LLM_FALLBACK", True)

    import hybrid.orchestrator as orch_mod
    import hybrid.store as hstore

    monkeypatch.setattr(orch_mod, "HybridOrchestrator", _PlanOnlyOrch)
    monkeypatch.setattr(hstore, "list_rules", lambda uid: [])
    monkeypatch.setattr(hstore, "get_workflow", lambda uid, wid: None)

    frames: list[str] = []

    async def main_() -> None:
        ticket = _Ticket()
        resp = await chat_mod._chat_stream_impl(
            chat_mod.ChatRequest(sid="s-pick", question="帮我整理笔记", mode="hybrid"),
            _Req(),
            "u-pick",
            ticket,
        )
        async for frame in resp.body_iterator:
            frames.append(frame)
        await resp.background()

    asyncio.run(main_())

    body = "".join(frames)
    assert "直接" in body and "回答" in body, f"应该走 LLM 兜底，实际：{body}"
    assert "打开文档" not in body, "规划稿不能出现在回给用户的内容里"
    assert "混合执行失败" not in body, "兜底成功就不该再报失败"

    answers = [a[3] for a, _kw in saved if len(a) >= 4 and a[2] == "assistant"]
    assert answers and "直接回答" in answers[-1], f"落库的应是兜底答案，实际：{answers}"
