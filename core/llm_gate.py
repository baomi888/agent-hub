from __future__ import annotations

"""LLM 并发闸：把「一起打上游」变成「排队打上游」。

为什么需要它
------------
`core/llm.py` 只缓存客户端，**不限制同时打到上游的请求数**。200 人同时提问 =
200 条 SSE 流同时挂在上游，撞账号的并发/TPM 限制，表现是「转圈很久才出字」，
严重时整批失败。上游的限制是物理事实，服务端能做的是**体面地排队**：
让多出来的人等一会儿，而不是一起把上游打爆。

两把闸，语义不同
----------------
1. **前台（异步闸）**：`/api/chat/stream` 一轮问答占一个名额，抢不到就等，
   等满 `LLM_QUEUE_TIMEOUT` 秒后抛 `Busy` → 路由返回 429 + Retry-After。
   闸必须抢在「开始吐 SSE」之前 —— 一旦发出 200 响应头，就再也没机会回 429 了。

2. **后台（同步闸）**：标题生成 / 画像标签 / 上下文压缩这类 fire-and-forget 的活，
   **抢不到就跳过**（`try_ticket()` 返回 None），绝不排队。
   原因：它们跑在 anyio 线程池里（默认 40 个线程），一旦排队就会把线程占满，
   连带把前台正常业务的 `asyncio.to_thread(...)` 全堵死 —— 那是比"少个标题"严重得多的故障。

作用域（务必知道）
------------------
闸是**进程内**的。uvicorn 起 N 个 worker 时，全局上限 = N × `LLM_MAX_CONCURRENCY`。
跨进程的共享闸需要 Redis 之类的共享状态，本次不做；要收紧就调小每进程的值。

为什么不把闸做在 httpx transport 层（那样连 LangGraph 内部调用都能覆盖）
----------------------------------------------------------------------
transport 层拿不到「这是哪个用户的哪一轮」，只能按 HTTP 请求计数；而且一旦
在 transport 里抛超时，openai SDK 会**自动重试**（`LLM_MAX_RETRIES` 次），
队尾用户实际要等 `(重试次数+1) × 排队超时`。更麻烦的是共享 httpx.AsyncClient
会绑定事件循环，测试里每个 TestClient 一个 loop，很容易踩「attached to a
different loop」。所以闸放在业务层：一轮问答一个名额，语义清楚、失败可控。
"""

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Iterator

from core import config

# ---------------------------------------------------------------- 计数器
# 只做观测用（/health 的 llm_gate 字段）。加锁是因为同步闸在任意线程里改。
_LOCK = threading.Lock()
_STATS = {
    "inflight": 0,   # 当前占着名额的数量
    "waiting": 0,    # 当前在排队的数量（仅异步闸会排队）
    "peak": 0,       # 历史最高同时占用
    "rejected": 0,   # 排队超时 或 后台抢不到 的次数
    "acquired": 0,   # 累计成功拿到名额的次数
}


def _limit() -> int:
    """每进程允许的上游并发数。<=0 表示关闭闸门（不限）。"""
    if not getattr(config, "LLM_GATE_ENABLED", True):
        return 0
    try:
        return int(config.LLM_MAX_CONCURRENCY)
    except (TypeError, ValueError):
        return 0


def _bump(**delta: int) -> None:
    with _LOCK:
        for k, v in delta.items():
            _STATS[k] = _STATS.get(k, 0) + v
        if _STATS["inflight"] > _STATS["peak"]:
            _STATS["peak"] = _STATS["inflight"]


class Busy(Exception):
    """排队超时：上游并发已占满，且等不到位置。

    设计成普通 Exception（不继承 httpx / openai 的异常）：
    它是我们自己的业务判断，不该被 SDK 当成网络错误去重试。
    """

    def __init__(self, waited: float, limit: int, retry_after: int) -> None:
        super().__init__(
            f"当前提问人数较多，已排队 {waited:.0f} 秒仍未轮到，请约 {retry_after} 秒后再试"
        )
        self.waited = waited
        self.limit = limit
        self.retry_after = retry_after


class Ticket:
    """一个名额。`release()` 幂等，重复调用不会多还。"""

    __slots__ = ("_sem", "_released")

    def __init__(self, sem: asyncio.Semaphore | threading.BoundedSemaphore | None) -> None:
        self._sem = sem
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._sem is not None:
            try:
                self._sem.release()
            except ValueError:
                # BoundedSemaphore 被多还一次会抛，正常流程走不到这里；
                # 宁可少还一次也别让收尾逻辑炸掉（收尾里抛异常会吞掉原始错误）
                pass
        if self._sem is not None:
            _bump(inflight=-1)


# ---------------------------------------------------------------- 异步闸（前台）
# 每个事件循环一把信号量：httpx/asyncio 原语绑定 loop，测试里多个 TestClient
# 各自一个 loop，共用一把会报 "attached to a different loop"。
_ASYNC_LOCKS: dict[int, tuple[object, asyncio.Semaphore]] = {}
_ASYNC_GUARD = threading.Lock()


def _async_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = id(loop)
    with _ASYNC_GUARD:
        entry = _ASYNC_LOCKS.get(key)
        if entry is None or entry[0] is not loop:
            # 顺手清掉已关闭的 loop，避免长跑进程里字典只增不减
            for k, (lp, _sem) in list(_ASYNC_LOCKS.items()):
                if getattr(lp, "is_closed", lambda: False)():
                    _ASYNC_LOCKS.pop(k, None)
            entry = (loop, asyncio.Semaphore(_limit()))
            _ASYNC_LOCKS[key] = entry
        return entry[1]


async def acquire(timeout: float | None = None) -> Ticket:
    """抢一个前台名额；抢不到等 `LLM_QUEUE_TIMEOUT` 秒，超时抛 `Busy`。

    闸关闭（`LLM_MAX_CONCURRENCY<=0`）时返回一个"空票"，release 是空操作，
    调用方不必区分开关状态。
    """
    limit = _limit()
    if limit <= 0:
        return Ticket(None)

    sem = _async_sem()
    wait = config.LLM_QUEUE_TIMEOUT if timeout is None else timeout
    started = time.monotonic()
    _bump(waiting=1)
    try:
        await asyncio.wait_for(sem.acquire(), max(0.0, wait))
    except (asyncio.TimeoutError, TimeoutError):
        _bump(rejected=1)
        raise Busy(
            waited=time.monotonic() - started,
            limit=limit,
            retry_after=max(1, int(round(wait))),
        ) from None
    finally:
        _bump(waiting=-1)

    _bump(inflight=1, acquired=1)
    return Ticket(sem)


@asynccontextmanager
async def slot(timeout: float | None = None) -> Iterator[Ticket]:
    """`acquire` 的 with 写法，给"整段代码都要占名额"的场景用。"""
    ticket = await acquire(timeout)
    try:
        yield ticket
    finally:
        ticket.release()


# ---------------------------------------------------------------- 同步闸（后台）
_SYNC_LOCK = threading.Lock()
_SYNC_SEM: tuple[int, threading.BoundedSemaphore] | None = None


def _sync_sem() -> threading.BoundedSemaphore:
    global _SYNC_SEM
    limit = _limit()
    with _SYNC_LOCK:
        if _SYNC_SEM is None or _SYNC_SEM[0] != limit:
            _SYNC_SEM = (limit, threading.BoundedSemaphore(limit))
        return _SYNC_SEM[1]


def try_ticket() -> Ticket | None:
    """后台任务用：有位置就拿，没有立刻返回 None —— **绝不阻塞**。

    返回 None 时调用方应跳过这次后台调用（少一个标题 / 少一次压缩，
    比把线程池堵死划算得多）。
    """
    if _limit() <= 0:
        return Ticket(None)
    sem = _sync_sem()
    if not sem.acquire(blocking=False):
        _bump(rejected=1)
        return None
    _bump(inflight=1, acquired=1)
    return Ticket(sem)


@contextmanager
def background_slot() -> Iterator[bool]:
    """`with background_slot() as ok:` —— ok 为 False 表示闸满，本次跳过。"""
    ticket = try_ticket()
    if ticket is None:
        yield False
        return
    try:
        yield True
    finally:
        ticket.release()


# ---------------------------------------------------------------- 观测


def snapshot() -> dict:
    """当前进程的闸状态（多 worker 时每个进程各报各的）。"""
    limit = _limit()
    with _LOCK:
        stats = dict(_STATS)
    return {
        "enabled": limit > 0,
        "max": limit,
        "queue_timeout": config.LLM_QUEUE_TIMEOUT,
        **stats,
    }


def reset_stats() -> None:
    """只给测试用：把计数器清零。"""
    with _LOCK:
        for k in _STATS:
            _STATS[k] = 0
