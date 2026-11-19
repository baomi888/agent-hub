"""回归：同一账号两台设备并发提问时，历史边界要用"本轮 user 消息 id"（2026-11-19）。

背景（线上问题：两台设备同时用同一会话，有一轮回答"被吞"/答非所问）：
`api/chat.py` 以前所有读历史的地方都是

    history = (await asyncio.to_thread(store.get_messages, uid, sid,
                                       include_system=False))[:-1]

`[:-1]` 赌的是"表里最后一条就是我刚插入的 user 消息"。两台设备几乎同时发问时：

    A: add_message(UA) = 100   DB: [..., UA(100)]
    B: add_message(UB) = 101   DB: [..., UA(100), UB(101)]
    A 读 [..., UA, UB][:-1] → 含自己的 UA（同一问题被喂两遍）
    B 读 [..., UA, UB][:-1] → 把 A 的 UA 当历史（模型答的是 A 的问题）

修法：`add_message` 本来就返回了本轮 user 消息的 id，直接拿它当硬边界
（`id < user_msg_id`），不再"猜最后一条"。

本文件锁死：
  1. `store.get_messages(before_id=N)` 只返回 id < N 的消息（不含 N 及其后）；
  2. 两设备竞态复现：A / B 各自的历史边界互不影响，谁都读不到"自己这条"；
  3. chat 薄层确实把 add_message(user) 的 id 传给了读历史处（before_id），
     而不是另拍一次 count/max_id 快照；
  4. 没传 before_id 时行为不变（老调用方、压缩等仍然拿全量）。
"""

from __future__ import annotations

import asyncio
import os

import pytest

import api.chat as chat_mod
import core.account as account_mod
import core.session as session_mod


@pytest.fixture(autouse=True)
def _ensure_tables():
    """同 test_sse_disconnect：建表只在 import 时跑过一次，这里补齐（幂等）。"""
    session_mod._init_db()
    account_mod._ensure_tables()
    yield


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """每个用例一套独立库，避免互相看到对方的会话。"""
    db = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(session_mod, "_db_path", lambda: db)
    session_mod._init_db()
    return db


def _conv(owner: str = "u-dev") -> str:
    return session_mod.create_conversation(owner, "多设备")["id"]


# ---------- 1. store 层的边界语义 ----------


def test_before_id_excludes_boundary_and_later(fresh_db):
    sid = _conv()
    a = session_mod.add_message("u-dev", sid, "user", "第一问")
    session_mod.add_message("u-dev", sid, "assistant", "第一答")
    b = session_mod.add_message("u-dev", sid, "user", "第二问")
    session_mod.add_message("u-dev", sid, "assistant", "第二答")

    ids = [m["id"] for m in session_mod.get_messages("u-dev", sid, before_id=b)]
    assert b not in ids and a in ids, "边界那条及之后的消息都不该进历史"
    assert ids == sorted(ids), "顺序必须仍是正序"

    # 不传 before_id：行为不变（压缩、导出等仍拿全量）
    assert len(session_mod.get_messages("u-dev", sid)) == 4


def test_before_id_none_keeps_all(fresh_db):
    sid = _conv()
    session_mod.add_message("u-dev", sid, "user", "问")
    assert len(session_mod.get_messages("u-dev", sid, before_id=None)) == 1


# ---------- 2. 两台设备并发提问的竞态 ----------


def test_two_devices_do_not_leak_own_question_into_history(fresh_db):
    """A 与 B 先后插入（窗口内互相可见），各自按自己的 id 划线。"""
    sid = _conv()
    session_mod.add_message("u-dev", sid, "user", "历史问题")
    session_mod.add_message("u-dev", sid, "assistant", "历史回答")

    ua = session_mod.add_message("u-dev", sid, "user", "帮我写一份考研计划表")
    ub = session_mod.add_message("u-dev", sid, "user", "今天的天气怎么样")

    # A 的视野：自己的提问之前
    a_hist = session_mod.get_messages("u-dev", sid, before_id=ua, include_system=False)
    assert [m["content"] for m in a_hist] == ["历史问题", "历史回答"]

    # B 的视野：能看见 A 的提问（它确实先落库），但绝不能看见自己这条
    b_hist = session_mod.get_messages("u-dev", sid, before_id=ub, include_system=False)
    assert [m["content"] for m in b_hist] == ["历史问题", "历史回答", "帮我写一份考研计划表"]
    assert all(m["content"] != "今天的天气怎么样" for m in b_hist)

    # 旧写法 `[:-1]` 在同样状态下会干什么：A 会把自己那条读进历史
    old = session_mod.get_messages("u-dev", sid, include_system=False)[:-1]
    assert any(m["content"] == "帮我写一份考研计划表" for m in old), (
        "这个用例的意义就是证明 [:-1] 在并发下会污染 A 的历史"
    )


# ---------- 3. chat 薄层把 user_msg_id 传下去 ----------


@pytest.fixture
def _quiet_chat(monkeypatch):
    monkeypatch.setattr(chat_mod, "get_llm", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod.config, "USER_TAG_HOOK_ENABLED", False)
    monkeypatch.setattr(chat_mod.quota, "consume", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod, "_schedule_compress", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod.store, "rename_conversation", lambda *a, **k: None)
    monkeypatch.setattr(chat_mod, "_generate_title", lambda q: "标题")


def test_chat_passes_user_msg_id_as_history_boundary(monkeypatch, _quiet_chat):
    """读历史时必须带 before_id=<add_message 返回的 user id>。"""
    seen: list[dict] = []

    monkeypatch.setattr(chat_mod.store, "get_conversation", lambda uid, sid: {"kb_id": None})
    monkeypatch.setattr(
        chat_mod.store,
        "add_message",
        lambda owner, sid, role, content, *a, **k: 777 if role == "user" else 778,
    )

    def _fake_get_messages(owner, sid, **kw):
        seen.append(kw)
        return []

    monkeypatch.setattr(chat_mod.store, "get_messages", _fake_get_messages)
    monkeypatch.setattr(chat_mod.store, "count_messages", lambda uid, sid: 10)

    async def main_() -> None:
        resp = await chat_mod._chat_stream_impl(
            chat_mod.ChatRequest(sid="s-1", question="问题", mode="llm"),
            _NoDisconnect(),
            "u-1",
            _Ticket(),
        )
        async for _ in resp.body_iterator:  # noqa: F841
            pass
        await resp.background()

    asyncio.run(main_())

    assert seen, "llm 分支必须读一次历史"
    assert seen[0].get("before_id") == 777, f"历史边界应是本轮 user 消息 id，实际：{seen[0]}"


class _NoDisconnect:
    async def is_disconnected(self) -> bool:
        return False


class _Ticket:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1
