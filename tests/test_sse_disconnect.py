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


# ---------- 3. 中断的回答：留半截 + 标 interrupted，不许伪装成完整答案 ----------
#
# 2026-11-19 改了这里的契约。原来锁的是"断连就整条不落库"，代价是回答直接消失
# （手机锁屏 / 切网 / 两设备切换时 SSE 一断，用户以为被吞了）。
# 现在 assistant 行在流开始前就 INSERT 空壳（status='streaming'），断连时
# 把已生成的部分 UPDATE 成 status='interrupted'：内容留得住，且**永远不是 done**，
# 前端据此挂"已停止生成"，历史组装时也会标注"只有前半段"。


class _ReqAfter(_Req):
    """前 n-1 次回答"还连着"，第 n 次起回答已断开。

    `_stream_llm` 每 16 帧查一次 disconnection（首帧也查），所以 n=2 表示
    "第 16 帧时发现客户端没了"——正好用来造一段真实的半截回答。
    """

    def __init__(self, after: int = 2) -> None:
        super().__init__(disconnect=False)
        self._after = after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls >= self._after


def _llm_path_env(monkeypatch) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """把 llm 模式的依赖全换成替身，返 (inserts, updates, deletes) 三个记录表。"""
    import api.chat as cm

    inserts: list[tuple] = []
    updates: list[tuple] = []
    deletes: list[tuple] = []

    monkeypatch.setattr(cm.store, "get_conversation", lambda uid, sid: {"kb_id": None})
    monkeypatch.setattr(cm.quota, "consume", lambda uid, kind: 0)
    monkeypatch.setattr(cm.store, "get_messages", lambda *a, **kw: [])
    monkeypatch.setattr(cm.store, "count_messages", lambda *a, **kw: 10)
    monkeypatch.setattr(
        cm.store, "add_message", lambda *a, **kw: inserts.append((a, kw)) or 7
    )
    monkeypatch.setattr(
        cm.store,
        "update_message_content",
        lambda *a, **kw: updates.append((a, kw)) or True,
    )
    monkeypatch.setattr(
        cm.store, "delete_messages", lambda *a, **kw: deletes.append((a, kw)) or True
    )
    monkeypatch.setattr(cm.store, "rename_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(cm, "_schedule_compress", lambda uid, sid: None)
    monkeypatch.setattr(cm.config, "USER_TAG_HOOK_ENABLED", False)
    return inserts, updates, deletes


def test_aborted_answer_keeps_partial_text_as_interrupted(monkeypatch):
    """断连时已生成的部分必须留库，状态 interrupted（不是 done）。"""
    import api.chat as cm

    inserts, updates, deletes = _llm_path_env(monkeypatch)
    # 关掉出站泄露过滤：它为了判断"是否在逐字复述提示词"会压住最后几十个字
    # （短流几乎一个字都放不出去），这里要验的是落库而不是过滤器。
    monkeypatch.setattr(cm, "_new_leak_guard", lambda **kw: None)
    chunks = [f"第{i}段" for i in range(40)]
    monkeypatch.setattr(cm, "get_llm", lambda **kw: _FakeLLM(chunks, []))

    async def main_() -> None:
        ticket = _Ticket()
        resp = await cm._chat_stream_impl(
            cm.ChatRequest(sid="s-abort2", question="问题", mode="llm"),
            _ReqAfter(after=2),  # 第 16 帧才发现客户端断了
            "u-abort2",
            ticket,
        )
        await _drain(resp.body_iterator)
        await resp.background()

    asyncio.run(main_())

    # 空壳：assistant + 空内容 + streaming
    assert any(a[2] == "assistant" and a[3] == "" for a, _ in inserts if len(a) >= 4)
    asst_kw = [kw for a, kw in inserts if len(a) >= 3 and a[2] == "assistant"][0]
    assert asst_kw.get("status") == "streaming"

    # 收尾：正文是前 15 段，状态 interrupted，且没有一条 done
    assert updates, "断连后必须把半截回答写回库里"
    final = updates[-1]
    text, status = final[0][3], final[0][4]
    assert status == "interrupted", f"中断的回答不能标成 {status}"
    assert "第0段" in text and "第39段" not in text, text
    assert all(u[0][4] != "done" for u in updates if len(u[0]) >= 5)


def test_aborted_before_any_token_removes_empty_shell(monkeypatch):
    """一个字都没生成就断了：空壳删掉，不能留一个空气泡。"""
    import api.chat as cm

    inserts, updates, deletes = _llm_path_env(monkeypatch)
    monkeypatch.setattr(cm, "get_llm", lambda **kw: _FakeLLM(["答"], []))

    async def main_() -> None:
        ticket = _Ticket()
        resp = await cm._chat_stream_impl(
            cm.ChatRequest(sid="s-abort3", question="问题", mode="llm"),
            _Req(disconnect=True),
            "u-abort3",
            ticket,
        )
        await _drain(resp.body_iterator)
        await resp.background()

    asyncio.run(main_())

    assert any(d[0][2] == [7] for d in deletes), f"空壳应当被删：{deletes}"
    assert not any(len(a) >= 5 and a[4] == "done" for a, _ in updates)


def test_normal_answer_seals_the_same_row(monkeypatch):
    """对照组：正常结束时**不新增** assistant 行，只把空壳定稿成 done。

    这条锁的是"消息顺序 = 提问时刻顺序"：回答如果等流结束才 INSERT，
    两台设备并发时晚落地的回答会排到别人的提问之后（U1,U2,A1）。
    """
    import api.chat as cm

    inserts, updates, _deletes = _llm_path_env(monkeypatch)
    monkeypatch.setattr(cm, "get_llm", lambda **kw: _FakeLLM(["完整答案"], []))

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

    asst_inserts = [a for a, _ in inserts if len(a) >= 3 and a[2] == "assistant"]
    assert len(asst_inserts) == 1, f"只能插一条空壳，实际 {asst_inserts}"
    text, status = updates[-1][0][3], updates[-1][0][4]
    assert "完整答案" in text and status == "done"


def test_sse_headers_forbid_buffering() -> None:
    """回归：SSE 响应头必须禁止中间层做内容编码。

    Next 的 `compression` 中间件遇到不带 `no-transform` 的响应会给它加 gzip，
    而 gzip 流要攒满 16KB 或等连接结束才 flush —— 浏览器拿不到逐帧 token，
    用户看到的是「卡很久 → 突然全文」，此时点「停止生成」也停不下来
    （网络流早已结束，只剩前端还在渲染积压的帧）。
    """
    assert "no-transform" in chat_mod.SSE_HEADERS["Cache-Control"]
    assert chat_mod.SSE_HEADERS["X-Accel-Buffering"] == "no"
