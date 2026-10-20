"""回归：SSE 断连要能回收上游 + 中断的回答不落库（2026-10-20 P0-3，issue #15）。

锁死这些行为：
  1. 客户端断开时 `_stream_llm` 立刻 aclose 上游 —— 跳出 `async for` **不会**自动
     关闭异步生成器，靠 GC 收尾会留下"幽灵请求"继续烧 token；
  2. 正常结束 / 上游异常两条出口也都会 aclose（三条出口一个不落）；
  3. 断连中断时 `_Acc.aborted=True`，且**不把半截回答当完整答案 add_message 落库**
     （否则用户刷新后看到一条没有任何中断痕迹的残缺回答）；
  4. `_Upstreams` 收尾幂等，且单个生成器关闭失败不影响其它生成器与名额归还；
  5. `_finish_turn` 既关上游也还名额 —— 挂在 StreamingResponse 的 background 上，
     是整条链路里唯一 100% 会跑到的收尾点。
"""

from __future__ import annotations

import asyncio

import pytest

import api.chat as chat_mod
import core.account as account_mod
import core.session as session_mod
from core import llm_gate


@pytest.fixture(autouse=True)
def _ensure_tables():
    """同 test_jwt_login_flow：别的测试会把 _db_path 改到临时库，
    而建表只在 import 时跑过一次，这里补齐（幂等）。"""
    session_mod._init_db()
    account_mod._ensure_tables()
    yield


# ---------- 替身 ----------


class _Chunk:
    def __init__(self, text: str) -> None:
        self.content = text


class _FakeLLM:
    """记录"上游生成器有没有被关闭"的假模型。"""

    def __init__(self, texts: list[str], closed: list[bool], boom: bool = False) -> None:
        self.texts = texts
        self.closed = closed
        self.boom = boom

    def astream(self, messages):  # noqa: ARG002
        async def gen():
            try:
                for t in self.texts:
                    yield _Chunk(t)
                if self.boom:
                    raise RuntimeError("upstream exploded")
            finally:
                self.closed.append(True)

        return gen()


class _Req:
    def __init__(self, disconnect: bool = False) -> None:
        self._disconnect = disconnect
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self._disconnect


class _Ticket:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


async def _drain(agen) -> list[str]:
    frames = []
    async for f in agen:
        frames.append(f)
    return frames


# ---------- 1. 三条出口都要 aclose ----------


def test_disconnect_marks_aborted_and_closes_upstream():
    closed: list[bool] = []
    acc = chat_mod._Acc()
    llm = _FakeLLM(["你", "好"], closed)

    async def main_() -> list[str]:
        return await _drain(chat_mod._stream_llm(llm, [], _Req(disconnect=True), acc))

    frames = asyncio.run(main_())
    assert acc.aborted is True
    assert acc.error is None
    assert closed == [True], "断连后上游必须被 aclose"
    assert frames == [], "断连时不应再往下吐 token"


def test_normal_finish_closes_upstream():
    closed: list[bool] = []
    acc = chat_mod._Acc()
    llm = _FakeLLM(["你", "好"], closed)

    async def main_() -> list[str]:
        return await _drain(chat_mod._stream_llm(llm, [], _Req(), acc))

    frames = asyncio.run(main_())
    assert acc.aborted is False
    assert acc.text == "你好"
    assert len(frames) == 2
    assert closed == [True]


def test_upstream_error_closes_upstream_and_reports():
    closed: list[bool] = []
    acc = chat_mod._Acc()
    llm = _FakeLLM(["半句"], closed, boom=True)

    async def main_() -> list[str]:
        return await _drain(chat_mod._stream_llm(llm, [], _Req(), acc))

    frames = asyncio.run(main_())
    assert acc.error, "上游异常要记进 acc.error"
    assert closed == [True]
    assert any("error" in f for f in frames)


# ---------- 2. 收尾登记处 ----------


def test_upstreams_close_all_and_idempotent():
    closed: list[bool] = []
    ups = chat_mod._Upstreams()
    a = ups.track(_FakeLLM(["a"], closed).astream([]))
    b = ups.track(_FakeLLM(["b"], closed).astream([]))

    async def main_() -> None:
        # 必须先真正启动：没启动过的异步生成器 aclose() 不会执行函数体里的
        # try/finally（Python 语义）。真实场景里"登记了"就意味着已经开始消费。
        await a.__anext__()
        await b.__anext__()
        await ups.aclose()
        await ups.aclose()  # 幂等：第二次不该炸

    asyncio.run(main_())
    assert closed.count(True) == 2, "登记的每个上游都必须被关闭"
    assert ups._items == []


def test_finish_turn_closes_upstream_then_releases_ticket():
    closed: list[bool] = []
    ups = chat_mod._Upstreams()
    gen = ups.track(_FakeLLM(["x"], closed).astream([]))
    ticket = _Ticket()

    async def main_() -> None:
        await gen.__anext__()  # 让上游真的开始吐字节
        await chat_mod._finish_turn(ups, ticket)

    asyncio.run(main_())
    assert closed == [True]
    assert ticket.released == 1


def test_finish_turn_releases_ticket_even_if_close_raises():
    class _Bad:
        async def aclose(self):
            raise RuntimeError("close failed")

    ups = chat_mod._Upstreams()
    ups.track(_Bad())
    ticket = _Ticket()

    async def main_() -> None:
        await chat_mod._finish_turn(ups, ticket)

    asyncio.run(main_())  # 不抛
    assert ticket.released == 1, "关上游失败也必须把名额还回去"


# ---------- 3. 中断的回答不落库 ----------


def test_aborted_answer_is_not_saved(monkeypatch):
    """断连中断时，assistant 消息不能落库（半截回答不该伪装成完整答案）。"""
    import api.chat as cm

    saved: list[tuple] = []

    monkeypatch.setattr(cm.store, "get_conversation", lambda uid, sid: {"kb_id": None})
    monkeypatch.setattr(cm.quota, "consume", lambda uid, kind: 0)
    monkeypatch.setattr(cm.store, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(cm.store, "count_messages", lambda *a, **kw: 10)
    monkeypatch.setattr(
        cm.store, "add_message", lambda *a, **kw: saved.append((a, kw)) or 1
    )
    monkeypatch.setattr(cm, "get_llm", lambda **kw: _FakeLLM(["答"], []))

    async def main_() -> None:
        ticket = _Ticket()
        resp = await cm._chat_stream_impl(
            cm.ChatRequest(sid="s-abort", question="问题", mode="llm"),
            _Req(disconnect=True),
            "u-abort",
            ticket,
        )
        await _drain(resp.body_iterator)
        await resp.background()

    asyncio.run(main_())

    roles = [a[2] for a, _kw in saved if len(a) >= 3]
    assert "assistant" not in roles, f"断连后不该落 assistant 消息，实际：{roles}"


def test_normal_answer_is_saved(monkeypatch):
    """对照组：连接正常时必须落库（证明上一个用例不是"永远不落库"）。"""
    import api.chat as cm

    saved: list[tuple] = []

    monkeypatch.setattr(cm.store, "get_conversation", lambda uid, sid: {"kb_id": None})
    monkeypatch.setattr(cm.quota, "consume", lambda uid, kind: 0)
    monkeypatch.setattr(cm.store, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(cm.store, "count_messages", lambda *a, **kw: 10)
    monkeypatch.setattr(
        cm.store, "add_message", lambda *a, **kw: saved.append((a, kw)) or 1
    )
    monkeypatch.setattr(cm.store, "rename_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(cm, "get_llm", lambda **kw: _FakeLLM(["完整答案"], []))
    monkeypatch.setattr(cm, "_schedule_compress", lambda uid, sid: None)
    monkeypatch.setattr(cm.config, "USER_TAG_HOOK_ENABLED", False)

    async def main_() -> None:
        ticket = _Ticket()
        resp = await cm._chat_stream_impl(
            cm.ChatRequest(sid="s-ok", question="问题", mode="llm"),
            _Req(),
            "u-ok",
            ticket,
        )
        await _drain(resp.body_iterator)
        await resp.background()

    asyncio.run(main_())

    assert any(a[2] == "assistant" and "完整答案" in a[3] for a, _kw in saved if len(a) >= 4)
