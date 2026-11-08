"""激活线（2026-11-08）：首屏便签直达 + 首问埋点。

钉住的不变量（每条都写了"不这样会怎样"，别只留下断言）
----------------------------------------------------
1. `count_user_messages` 是**账号级**口径：跨会话累计、不串号；
   system / assistant 不算；老库 owner_id 为 NULL 的行不算任何人。
   口径错了整条激活率就是错的，而它本身不会报错。
2. `_note_first_ask` 只在"该账号恰好 1 条 user 消息"时 +1；第二次提问不再 +1。
   判据写成"每条都 +1"的话，这个数会退化成 chat_total 的别名。
3. 上游报错、或埋点自己抛异常时：回答照常结束，计数不动。
   埋点埋在收尾，一旦它抛上去，用户拿到的是"答案已经生成好了却报错"。
4. 计数器名字在 Prometheus 文本与 `/metrics/json` 里是**同一个字符串**
   `first_ask_total` —— 交办单里那行 `curl ... | grep first_ask_total` 能命中，
   靠的就是这一条（`snapshot()` 用的是原始名，不补 `_total`）。
5. 首屏三条问题的文案不含占位符 / 指代："（你的课程名）""这份笔记"这类写法
   会让用户点完还得打字，激活线又断在最后一步。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.chat as chat  # noqa: E402
import core.session as session  # noqa: E402
import main as m  # noqa: E402
from core import config, metrics  # noqa: E402
from core.auth import get_current_user  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OWNER = "u_first_ask"
SID = "conv_first_ask"


@pytest.fixture(autouse=True)
def _clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


# ==================== 1. 账号级口径 ====================


@pytest.fixture
def db(monkeypatch, tmp_path):
    """独立临时库 + 建表。必须在查询之前换路径，否则会算到真实库里。"""
    path = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    return path


def _seed(owner: str, *roles: str) -> str:
    """给 owner 建一个会话，按顺序写入指定角色的消息，返回会话 id。"""
    conv = session.create_conversation(owner, title="t")
    for role in roles:
        session.add_message(owner, conv["id"], role, f"{role}-内容")
    return conv["id"]


def test_count_user_messages_is_account_level(db):
    """跨会话累计，且只数 user；system / assistant 不参与。"""
    _seed(OWNER, "system", "user", "assistant")
    second = session.create_conversation(OWNER, title="第二会")
    session.add_message(OWNER, second["id"], "user", "第二问")
    session.add_message(OWNER, second["id"], "assistant", "答")

    # 两个会话各 1 条 user → 账号级应是 2（不是"最后那个会话的 1"）
    assert session.count_user_messages(OWNER) == 2


def test_count_user_messages_does_not_leak_across_owners(db):
    _seed(OWNER, "user", "assistant")
    _seed("u_other", "user", "assistant")

    assert session.count_user_messages(OWNER) == 1
    assert session.count_user_messages("u_other") == 1
    # 没有这个账号 → 0，不是抛异常（否则新号第一问直接 500）
    assert session.count_user_messages("u_nobody") == 0


def test_count_user_messages_ignores_orphan_rows(db):
    """改造前的老会话 owner_id 是 NULL：它不属于任何人，不能被算进首问。"""
    legacy = _seed(OWNER, "user")
    with session._get_conn() as conn:
        conn.execute("UPDATE conversations SET owner_id = NULL WHERE id = ?", (legacy,))

    # 只剩 NULL 那一行时，这个账号的首问仍然是"没有"
    assert session.count_user_messages(OWNER) == 0


# ==================== 2/3. 埋点判据与静默 ====================


def _counted() -> float:
    return metrics.snapshot()["counters"].get("first_ask_total", {}).get("", 0.0)


def test_note_first_ask_only_counts_the_first(monkeypatch):
    monkeypatch.setattr(session, "count_user_messages", lambda owner: 1)
    asyncio.run(chat._note_first_ask(OWNER))
    assert _counted() == 1

    # 第二次提问（库里已经有 2 条 user）→ 不再加
    monkeypatch.setattr(session, "count_user_messages", lambda owner: 2)
    asyncio.run(chat._note_first_ask(OWNER))
    assert _counted() == 1


def test_note_first_ask_swallows_errors(monkeypatch):
    """埋点失败必须静默：它跑在收尾，抛上去等于把成功的回答变成失败。"""

    def _boom(owner: str) -> int:
        raise RuntimeError("db is gone")

    monkeypatch.setattr(session, "count_user_messages", _boom)
    asyncio.run(chat._note_first_ask(OWNER))  # 不抛
    assert _counted() == 0


# ==================== 4. 计数器名字与导出格式 ====================


def test_counter_name_is_identical_in_both_exports():
    metrics.inc("first_ask_total")

    text = metrics.render_prometheus()
    assert "# HELP first_ask_total 首次提问的账号数" in text
    assert "# TYPE first_ask_total counter" in text
    assert re.search(r"^first_ask_total 1$", text, re.M), text

    snap = metrics.snapshot()
    # 键名与 Prometheus 文本一致（snapshot 不补 _total 后缀）——
    # 这是交办单里 `grep first_ask_total` 能命中 /metrics/json 的前提
    assert "first_ask_total" in snap["counters"]
    assert snap["counters"]["first_ask_total"][""] == 1.0
    assert "first_ask_total" in snap["help"]


# ==================== 5. 端到端：一次真 HTTP 轮次 ====================


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    async def astream(self, messages):
        for t in ("你", "好"):
            yield _Chunk(t)


class _BoomLLM:
    async def astream(self, messages):
        raise RuntimeError("上游挂了")
        yield  # pragma: no cover


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "_db_path", lambda: os.path.join(str(tmp_path), "s.db"))
    session._init_db()
    monkeypatch.setattr(chat.store, "get_conversation", lambda uid, sid: {"id": sid, "kb_id": None})
    monkeypatch.setattr(chat.store, "count_messages", lambda uid, sid: 1)
    monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: [])
    monkeypatch.setattr(
        chat.store, "add_message", lambda o, s, role, c, *a, **k: 1000 if role == "user" else 1001
    )
    monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _FakeLLM())
    monkeypatch.setattr(chat, "_generate_title", lambda q: "标题")
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", False)
    monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)

    m.app.dependency_overrides[get_current_user] = lambda: OWNER
    with TestClient(m.app) as c:
        yield c
    m.app.dependency_overrides.clear()


def _ask(client) -> list[str]:
    """跑一轮纯 LLM 问答，返回事件名序列（只关心序列，不解析 data）。"""
    r = client.post(
        "/api/chat/stream", json={"sid": SID, "question": "你好", "top_k": 3, "mode": "llm"}
    )
    assert r.status_code == 200, (r.status_code, r.text)
    names = []
    for raw in r.text.split("\n\n"):
        for line in raw.strip().split("\n"):
            if line.startswith("event:"):
                names.append(line[len("event:") :].strip())
    return names


def test_first_turn_increments_once(client, monkeypatch):
    monkeypatch.setattr(session, "count_user_messages", lambda owner: 1)
    names = _ask(client)
    assert names[-1] == "done"
    assert _counted() == 1

    # 同一个人再问一轮（库里已有 2 条 user）→ 不再加
    monkeypatch.setattr(session, "count_user_messages", lambda owner: 2)
    assert _ask(client)[-1] == "done"
    assert _counted() == 1


def test_upstream_error_does_not_count(client, monkeypatch):
    """上游报错时不会走到收尾，计数器不动 —— 且客户端收到 error 帧，不是断流。"""
    monkeypatch.setattr(session, "count_user_messages", lambda owner: 1)
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _BoomLLM())

    names = _ask(client)
    assert "error" in names
    assert "done" not in names
    assert _counted() == 0


def test_metrics_failure_does_not_break_the_answer(client, monkeypatch):
    """埋点抛异常时，回答必须照常结束（done 帧在），只是没计数。"""

    def _boom(owner: str) -> int:
        raise RuntimeError("db is gone")

    monkeypatch.setattr(session, "count_user_messages", _boom)
    assert _ask(client)[-1] == "done"
    assert _counted() == 0


# ==================== 6. 首屏文案：点了就能发，不用补字 ====================

_PLACEHOLDER = ("（", "）", "【", "】", "XX", "xx", "你的课程", "这份", "这个", "**")


def test_starter_prompts_need_no_follow_up():
    src = (ROOT / "frontend" / "src" / "lib" / "starterPrompts.ts").read_text(encoding="utf-8")
    questions = re.findall(r'question:\s*"([^"]+)"', src)
    hints = re.findall(r'hint:\s*"([^"]+)"', src)

    assert len(questions) == 3, questions
    assert hints == ["六级", "考研", "期末"]
    for q in questions:
        assert len(q) >= 12, f"太短，问不出东西：{q}"
        for bad in _PLACEHOLDER:
            assert bad not in q, f"文案里有占位符/指代「{bad}」，用户还得补字：{q}"
