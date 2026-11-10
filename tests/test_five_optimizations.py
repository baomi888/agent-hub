"""五项优化的回归测试（pytest 风格）。

覆盖：
  ① 画像注入：格式 / 90 天过滤 / 无标签 / 开关关闭 / 只追加到最后一条 user message
  ② 路由 Fast Path：两个开关都关、显式 workflow_id 时都不调路由模型
  ③ 压缩段锚点 + refs 回读：正常路径、未命中触发词、无压缩段、超限截断
  ④ 重复调用 Watchdog：第 3 次拦下、参数变化不拦、作用域隔离、指标点
  ⑤ 任务对象层：upsert / 活跃列表 / 空任务 / 开关关闭 / 摘要截断

所有用例用临时 SQLite，不碰真实 data/。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import config  # noqa: E402
import core.session as store  # noqa: E402
from core import context_compress as cc  # noqa: E402
from core import metrics, tool_guard  # noqa: E402


@pytest.fixture
def db(monkeypatch, tmp_path):
    path = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(store, "_db_path", lambda: path)
    store._init_db()
    return path


# ==================== ① 画像注入 ====================


def test_profile_note_formats_recent_tags(db, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_INJECT_ENABLED", True)
    store.add_user_tags("u1", ["考研备考", "考研数学"])
    note = store.profile_note("u1")
    assert note.startswith(store.PROFILE_NOTE_HEAD)
    assert "用户长期关注：" in note
    assert "考研备考" in note and "考研数学" in note


def test_profile_note_filters_stale_tags(db, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_INJECT_ENABLED", True)
    monkeypatch.setattr(config, "PROFILE_INJECT_DAYS", 90)
    store.add_user_tags("u1", ["新鲜标签", "过期标签"])
    old = int(time.time()) - 120 * 86400
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE user_tags SET updated_at = ? WHERE owner_id = ? AND tag = ?",
            (old, "u1", "过期标签"),
        )
    note = store.profile_note("u1")
    assert "新鲜标签" in note
    assert "过期标签" not in note


def test_profile_note_empty_and_disabled(db, monkeypatch):
    # 无标签
    monkeypatch.setattr(config, "PROFILE_INJECT_ENABLED", True)
    assert store.profile_note("nobody") == ""
    # 开关关闭（默认值也应为关闭）
    monkeypatch.setattr(config, "PROFILE_INJECT_ENABLED", False)
    store.add_user_tags("u1", ["数学"])
    assert store.profile_note("u1") == ""


def test_context_notes_combines_profile_and_tasks(db, monkeypatch):
    import api.chat as chat

    monkeypatch.setattr(config, "PROFILE_INJECT_ENABLED", True)
    monkeypatch.setattr(config, "TASK_INJECT_ENABLED", True)
    store.add_user_tags("u1", ["考研数学"])
    store.upsert_task("u1", "s1", "考研复习计划", summary="第一周先过完高数第一章")
    note = asyncio.run(chat._context_notes("u1"))
    assert "用户长期关注：" in note
    assert "近期任务" in note and "考研复习计划" in note


def test_context_notes_off_returns_empty_without_query(db, monkeypatch):
    import api.chat as chat

    monkeypatch.setattr(config, "PROFILE_INJECT_ENABLED", False)
    monkeypatch.setattr(config, "TASK_INJECT_ENABLED", False)
    assert asyncio.run(chat._context_notes("u1")) == ""


def test_plain_llm_note_only_on_last_user_message(db, monkeypatch):
    import api.chat as chat

    monkeypatch.setattr(
        chat.ctxcompress, "assemble_history", lambda *a, **kw: [{"role": "user", "content": "旧问题"}]
    )
    note = store.PROFILE_NOTE_HEAD + "用户长期关注：数学"
    msgs = asyncio.run(chat._plain_llm_messages("u1", "s1", "本轮问题", None, note))
    assert msgs[-1].content == "本轮问题\n\n" + note
    assert note not in msgs[0].content  # 绝不进 system prompt
    assert note not in msgs[1].content  # 也不进历史里的旧消息


def test_build_messages_note_only_on_last_user_message(monkeypatch):
    import api.chat as chat

    note = "【长期兴趣画像】用户长期关注：Python"
    msgs = asyncio.run(
        chat._build_messages(
            "u1", "s1", "本轮问题", None, [], None, None, history=[], note=note
        )
    )
    assert msgs[-1].content == "本轮问题\n\n" + note
    assert note not in msgs[0].content


# ==================== ② 路由 Fast Path ====================


def test_fastpath_no_tools_skips_router(monkeypatch):
    import api.chat as chat

    called = {"n": 0}

    async def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("两个开关都关时不应调路由模型")

    monkeypatch.setattr(chat.route_engine, "decide", _boom)
    monkeypatch.setattr(config, "ROUTER_FASTPATH_ENABLED", True)
    req = chat.ChatRequest(sid="s", question="你好", use_kb=False, use_web=False)
    plan = asyncio.run(chat._plan_route(req, req.question, None, False))
    assert called["n"] == 0
    assert plan.mode == "llm" and plan.route == "agent"


def test_fastpath_workflow_id_skips_router(monkeypatch):
    import api.chat as chat

    called = {"n": 0}

    async def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("显式 workflow_id 时不应调路由模型")

    monkeypatch.setattr(chat.route_engine, "decide", _boom)
    monkeypatch.setattr(config, "ROUTER_FASTPATH_ENABLED", True)
    monkeypatch.setattr(config, "HYBRID_MODE_ENABLED", True)
    req = chat.ChatRequest(
        sid="s", question="按流程走", use_kb=True, use_web=True, workflow_id="wf_x"
    )
    plan = asyncio.run(chat._plan_route(req, req.question, "kb", True))
    assert called["n"] == 0
    assert plan.mode == "hybrid" and plan.route == "workflow"
    assert "fastpath:workflow_id" in plan.source


def test_fastpath_can_be_disabled(monkeypatch):
    import api.chat as chat

    called = {"n": 0}

    async def _decide(*a, **kw):
        called["n"] += 1
        return chat.route_engine.RouteDecision("agent", "model", "test")

    monkeypatch.setattr(chat.route_engine, "decide", _decide)
    monkeypatch.setattr(config, "ROUTER_FASTPATH_ENABLED", False)
    req = chat.ChatRequest(sid="s", question="你好", use_kb=False, use_web=False)
    plan = asyncio.run(chat._plan_route(req, req.question, None, False))
    assert called["n"] == 1
    assert plan.mode == "llm"


# ==================== ③ 锚点 + 回读 ====================


def _fake_plan(block: cc.SummaryBlock):
    return cc.ContextPlan(
        summary_text=block.render(),
        keep_message_id_from=0,
        keep_turns=0,
        kept=[],
        anchored_text=block.render_anchored(),
        block=block,
    )


def test_render_anchored_is_stable_and_bounded(db, monkeypatch):
    block = cc.SummaryBlock(
        ["早前讨论了二次函数"], [cc.SegmentMeta(refs=[1201, 1240])]
    )
    text = block.render_anchored()
    assert "[C1 refs=1201-1240]" in text
    assert text == block.render_anchored()  # 纯函数，可重复
    # 没有 refs 的旧数据不编造范围
    old = cc.SummaryBlock(["旧段"], [cc.SegmentMeta()])
    assert "[C1]" in old.render_anchored()


def test_recall_appends_refs_only_when_hinted(db, monkeypatch):
    sid = store.create_conversation("u1", title="数学")["id"]
    id1 = store.add_message("u1", sid, "user", "二次函数顶点公式是 -b/2a")
    id2 = store.add_message("u1", sid, "assistant", "对，纵坐标是 (4ac-b²)/4a")
    block = cc.SummaryBlock(["早前讲了二次函数"], [cc.SegmentMeta(refs=[id1, id2])])
    monkeypatch.setattr(cc, "plan_context", lambda *a, **kw: _fake_plan(block))

    hit = cc.assemble_history("u1", sid, [], question="刚才那个公式具体是什么？")
    assert "[C1 refs=" in hit[0]["content"]
    assert "-b/2a" in hit[0]["content"]
    assert "【压缩段原文回读" in hit[0]["content"]

    miss = cc.assemble_history("u1", sid, [], question="今天天气怎么样")
    assert "-b/2a" not in miss[0]["content"]
    assert "[C1 refs=" in miss[0]["content"]  # 锚点始终在，只是不回读


def test_recall_truncates_over_limit(db, monkeypatch):
    sid = store.create_conversation("u1", title="长文")["id"]
    mid = store.add_message("u1", sid, "user", "长内容" * 500)
    block = cc.SummaryBlock(["早前长文"], [cc.SegmentMeta(refs=[mid])])
    monkeypatch.setattr(cc, "plan_context", lambda *a, **kw: _fake_plan(block))
    monkeypatch.setattr(cc, "RECALL_MAX_CHARS", 20)
    out = cc.assemble_history("u1", sid, [], question="之前那一段")
    assert "已截断" in out[0]["content"]
    assert len(out[0]["content"]) < 400


def test_recall_noop_without_summary(db, monkeypatch):
    sid = store.create_conversation("u1", title="新会话")["id"]
    monkeypatch.setattr(
        cc,
        "plan_context",
        lambda *a, **kw: cc.ContextPlan(
            summary_text="", keep_message_id_from=0, keep_turns=0, kept=[]
        ),
    )
    out = cc.assemble_history("u1", sid, [{"role": "user", "content": "hi"}], question="刚才")
    assert out == [{"role": "user", "content": "hi"}]


def test_get_messages_by_ids_owner_isolated(db):
    sid = store.create_conversation("u1", title="会话")["id"]
    id1 = store.add_message("u1", sid, "user", "甲的消息")
    id2 = store.add_message("u1", sid, "assistant", "甲的回答")
    got = store.get_messages_by_ids("u1", sid, [id1, id2])
    assert [m["id"] for m in got] == [id1, id2]
    # 别人的 owner 读不到
    assert store.get_messages_by_ids("u2", sid, [id1, id2]) == []
    # 空 / 非法 id 不查库
    assert store.get_messages_by_ids("u1", sid, []) == []
    assert store.get_messages_by_ids("u1", sid, ["x", -1]) == []


# ==================== ④ 重复调用 Watchdog ====================


def test_repeat_guard_blocks_third_identical_call(monkeypatch):
    monkeypatch.setattr(config, "TOOL_REPEAT_GUARD_ENABLED", True)
    calls = {"n": 0}

    def _run(**kw):
        calls["n"] += 1
        return "ok"

    before = metrics.snapshot()["counters"].get("tool_guard_repeat", {}).get("tool=demo", 0)
    with tool_guard.guard_scope():
        assert tool_guard.guarded_run("demo", _run, {"q": "same"}) == "ok"
        assert tool_guard.guarded_run("demo", _run, {"q": "same"}) == "ok"
        third = tool_guard.guarded_run("demo", _run, {"q": "same"})
    assert calls["n"] == 2, "第 3 次不应真的执行"
    assert "已连续 3 次相同调用" in third
    after = metrics.snapshot()["counters"].get("tool_guard_repeat", {}).get("tool=demo", 0)
    assert after == before + 1


def test_repeat_guard_param_change_not_blocked(monkeypatch):
    monkeypatch.setattr(config, "TOOL_REPEAT_GUARD_ENABLED", True)
    calls = {"n": 0}

    def _run(**kw):
        calls["n"] += 1
        return "ok"

    with tool_guard.guard_scope():
        for q in ("a", "a", "b"):
            tool_guard.guarded_run("demo", _run, {"q": q})
    assert calls["n"] == 3


def test_repeat_guard_scope_isolated_and_switch_off(monkeypatch):
    def _run(**kw):
        return "ok"

    with tool_guard.guard_scope():
        for _ in range(3):
            tool_guard.guarded_run("demo", _run, {"q": "x"})
    with tool_guard.guard_scope() as st2:
        assert st2.call_counts == {}
        assert tool_guard.guarded_run("demo", _run, {"q": "x"}) == "ok"

    monkeypatch.setattr(config, "TOOL_REPEAT_GUARD_ENABLED", False)
    with tool_guard.guard_scope():
        for _ in range(3):
            assert tool_guard.guarded_run("demo", _run, {"q": "y"}) == "ok"


# ==================== ⑤ 任务对象层 ====================


def test_task_upsert_list_and_empty(db, monkeypatch):
    monkeypatch.setattr(config, "TASK_INJECT_ENABLED", True)
    assert store.list_active_tasks("u1") == []
    assert store.task_note("u1") == ""

    store.upsert_task("u1", "s1", "考研复习", summary="第一章 极限")
    store.upsert_task("u1", "s2", "英语单词", summary="每天 50 个")
    tasks = store.list_active_tasks("u1", 3)
    assert len(tasks) == 2
    assert {t["task_id"] for t in tasks} == {"s1", "s2"}
    # owner 隔离
    assert store.list_active_tasks("u2") == []

    note = store.task_note("u1")
    assert "近期任务" in note
    assert "考研复习" in note and "第一章 极限" in note


def test_task_note_limit_and_summary_truncation(db, monkeypatch):
    monkeypatch.setattr(config, "TASK_INJECT_ENABLED", True)
    monkeypatch.setattr(config, "TASK_SUMMARY_CHARS", 10)
    for i in range(5):
        store.upsert_task("u1", f"s{i}", f"任务{i}", summary="很长很长的摘要内容" * 3)
    note = store.task_note("u1", limit=3)
    assert note.count("\n- ") == 3
    assert "摘要已截断" in note


def test_task_note_switch_off(db, monkeypatch):
    monkeypatch.setattr(config, "TASK_INJECT_ENABLED", False)
    store.upsert_task("u1", "s1", "任务", summary="摘要")
    assert store.task_note("u1") == ""


def test_task_table_and_indexes_exist(db):
    with store._get_conn() as conn:
        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        idx = {
            r["name"]
            for r in conn.execute("PRAGMA index_list(tasks)").fetchall()
        }
    assert {"owner_id", "task_id", "title", "status", "last_sid", "summary", "updated_at"} == cols
    assert "idx_task_active" in idx
