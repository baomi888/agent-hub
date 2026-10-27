"""回归：LLM 并发闸（2026-10-20 P0-2，issue #15）。

锁死这些行为：
  1. 并发超过 `LLM_MAX_CONCURRENCY` 时真的有排队（不是形同虚设）；
  2. 排队超时抛 `Busy`，且**不泄漏名额**（超时的那个没占到位置）；
  3. `Ticket.release()` 幂等：重复调用不会多还一个名额（否则闸会越跑越松）；
  4. 闸关闭（0 / LLM_GATE_ENABLED=0）时行为回到改造前，release 是空操作；
  5. 后台闸 `try_ticket()` **抢不到立刻返回 None**，绝不阻塞线程池；
  6. 路由层：闸满 → /api/chat/stream 直接 429 + Retry-After（SSE 发出 200 后就回不了 429）；
     没走到 StreamingResponse（如会话不存在 404）时名额当场还掉，不漏；
  7. /health 带 llm_gate 快照。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from core import config, llm_gate
from core.auth import get_current_user

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_stats() -> None:
    llm_gate.reset_stats()
    yield
    llm_gate.reset_stats()


@pytest.fixture
def limits(monkeypatch):
    """改闸参数的小工厂：返回 (并发上限, 排队超时) 设置器。"""

    def _set(n: int, wait: float = 2.0) -> None:
        monkeypatch.setattr(config, "LLM_MAX_CONCURRENCY", n)
        monkeypatch.setattr(config, "LLM_QUEUE_TIMEOUT", wait)
        monkeypatch.setattr(config, "LLM_GATE_ENABLED", True)

    return _set


# ---------- 1. 并发上限 ----------


def test_concurrency_never_exceeds_limit(limits):
    limits(2)

    async def main_() -> int:
        cur = peak = 0

        async def worker() -> None:
            nonlocal cur, peak
            async with llm_gate.slot():
                cur += 1
                peak = max(peak, cur)
                await asyncio.sleep(0.03)
                cur -= 1

        await asyncio.gather(*[worker() for _ in range(8)])
        return peak

    assert asyncio.run(main_()) == 2


def test_snapshot_reports_inflight(limits):
    limits(3)

    async def main_() -> dict:
        t1 = await llm_gate.acquire()
        t2 = await llm_gate.acquire()
        try:
            return llm_gate.snapshot()
        finally:
            t1.release()
            t2.release()

    snap = asyncio.run(main_())
    assert snap["enabled"] is True
    assert snap["max"] == 3
    assert snap["inflight"] == 2
    assert snap["peak"] >= 2
    assert snap["acquired"] == 2
    # 收尾之后不该有残留占用
    assert llm_gate.snapshot()["inflight"] == 0


# ---------- 2. 排队超时 ----------


def test_queue_timeout_raises_busy_without_leak(limits):
    limits(1, wait=0.1)

    async def main_():
        holder = await llm_gate.acquire()
        try:
            with pytest.raises(llm_gate.Busy) as ei:
                await llm_gate.acquire()
            return ei.value
        finally:
            holder.release()

    busy = asyncio.run(main_())
    assert busy.retry_after >= 1
    assert "排队" in str(busy) or "人数较多" in str(busy)
    snap = llm_gate.snapshot()
    assert snap["rejected"] == 1
    # 超时的那次没有占位，holder 释放后名额应回到 0 占用
    assert snap["inflight"] == 0


def test_release_is_idempotent(limits):
    """重复 release 不能多还名额 —— 否则闸会越跑越松，等于没有。"""
    limits(1)

    async def main_() -> None:
        t = await llm_gate.acquire()
        t.release()
        t.release()  # 第二次是空操作
        t.release()

        again = await llm_gate.acquire()
        try:
            # 名额只有 1 个：again 占着的时候再抢必须超时
            with pytest.raises(llm_gate.Busy):
                await llm_gate.acquire(timeout=0.1)
        finally:
            again.release()

    asyncio.run(main_())


# ---------- 3. 关闸 ----------


def test_gate_disabled_is_noop(limits):
    limits(0)

    async def main_():
        return await llm_gate.acquire()

    ticket = asyncio.run(main_())
    ticket.release()
    ticket.release()  # 空票，重复释放也不炸

    snap = llm_gate.snapshot()
    assert snap["enabled"] is False
    assert snap["inflight"] == 0
    # 后台闸同样直接放行（返回空票，而不是 None）
    assert llm_gate.try_ticket() is not None


def test_gate_can_be_killed_by_switch(limits, monkeypatch):
    limits(4)
    monkeypatch.setattr(config, "LLM_GATE_ENABLED", False)
    assert llm_gate.snapshot()["enabled"] is False
    assert llm_gate.try_ticket() is not None


# ---------- 4. 后台闸：抢不到必须立刻返回 ----------


def test_background_ticket_never_blocks(limits):
    limits(1)
    first = llm_gate.try_ticket()
    assert first is not None
    try:
        # 上限已占满：第二次必须立刻 None（阻塞的话会把 anyio 线程池堵死）
        assert llm_gate.try_ticket() is None
    finally:
        first.release()
    # 释放后又能拿到
    again = llm_gate.try_ticket()
    assert again is not None
    again.release()


def test_background_slot_reports_skipped(limits):
    limits(1)
    holder = llm_gate.try_ticket()
    try:
        with llm_gate.background_slot() as ok:
            assert ok is False
    finally:
        holder.release()
    with llm_gate.background_slot() as ok:
        assert ok is True


# ---------- 5. 路由层：429 与名额回收 ----------


class _FakeTicket:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


@pytest.fixture
def as_user():
    """跳过鉴权依赖，专注测闸与收尾逻辑。"""
    main.app.dependency_overrides[get_current_user] = lambda: "u-gate-test"
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def test_stream_route_returns_429_when_gate_busy(as_user, monkeypatch):
    async def _busy(*a, **kw):
        raise llm_gate.Busy(waited=20.0, limit=1, retry_after=7)

    monkeypatch.setattr(llm_gate, "acquire", _busy)
    r = client.post(
        "/api/chat/stream",
        json={"sid": "whatever", "question": "你好", "mode": "llm"},
    )
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "7"
    assert "人数较多" in r.text


def test_ticket_released_when_response_never_streams(as_user, monkeypatch):
    """会话不存在 → 404，但名额必须当场还掉，否则每来一个这种请求就漏一个位置。"""
    ticket = _FakeTicket()

    async def _ok(*a, **kw):
        return ticket

    monkeypatch.setattr(llm_gate, "acquire", _ok)
    r = client.post(
        "/api/chat/stream",
        json={"sid": "definitely-not-exists", "question": "你好", "mode": "llm"},
    )
    assert r.status_code == 404
    assert ticket.released == 1


# ---------- 6. /health 观测 ----------


def test_health_exposes_gate_snapshot():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    gate = body["llm_gate"]
    for key in ("enabled", "max", "inflight", "waiting", "peak", "rejected", "acquired"):
        assert key in gate
