"""Single-process background chat ownership and bounded, gap-free SSE replay.

Each subscriber reads its own position in one append-only journal. Publishing and
reading contain no awaits, so an event-loop task cannot slip between replay and
subscription. Slow connections retain a file position rather than a token queue.
The journal spills to a temporary file at the configured memory threshold.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable


def _frame(event: str, data: dict, cursor: int | None = None) -> str:
    prefix = "" if cursor is None else f"id: {cursor}\n"
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatRun:
    def __init__(self, registry: RunRegistry, uid: str, sid: str) -> None:
        self.registry = registry
        self.uid = uid
        self.sid = sid
        self.message_id: int | None = None
        self.started_at = time.time()
        self.state = "running"
        self.task: asyncio.Task | None = None
        self.preparation: asyncio.Task | None = None
        self.cursor = 0
        self.closed = False
        self.spilled = False
        self.stop_requested = False
        self._stop_sent = False
        self._finishing = False
        self._ready = asyncio.Event()
        self._entered = asyncio.Event()
        self._changed = asyncio.Event()
        self._expiry: asyncio.TimerHandle | None = None
        self._journal = tempfile.SpooledTemporaryFile(max_size=registry.buffer_limit, mode="w+b")
        self._size = 0

    def publish(self, event: str, data: dict) -> None:
        self.cursor += 1
        data = {**data, "cursor": self.cursor}
        # One JSON line per record makes independent subscriber offsets cheap.
        record = json.dumps([event, data], ensure_ascii=False).encode("utf-8") + b"\n"
        self._journal.seek(0, 2)
        self._journal.write(record)
        self._size += len(record)
        self.spilled = self._size > self.registry.buffer_limit
        self._changed.set()

    def start(
        self,
        source: AsyncIterator[str],
        cleanup: Callable[[], Awaitable[None]],
        snapshot: Callable[[], dict],
        *,
        message_id: int,
    ) -> None:
        self.message_id = message_id
        self.task = asyncio.create_task(self._drive(source, cleanup, snapshot))
        self._ready.set()

    async def _drive(self, source, cleanup, snapshot) -> None:
        self._entered.set()
        terminal = None
        reason = "completed"

        async def consume():
            nonlocal terminal
            async with contextlib.aclosing(source):
                async for frame in source:
                    event = "message"
                    data = None
                    for line in frame.splitlines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line[5:])
                    if data is None:
                        continue
                    if event == "done":
                        terminal = data
                    else:
                        self.publish(event, data)

        try:
            if self.stop_requested:
                reason = "stopped"
            else:
                await asyncio.wait_for(consume(), timeout=self.registry.timeout)
        except asyncio.TimeoutError:
            reason = "timeout"
        except asyncio.CancelledError:
            reason = "stopped"
        except Exception:
            reason = "error"
            self.publish("error", {"detail": "生成失败，请稍后重试"})
        finally:
            self._finishing = True
            try:
                await cleanup()
                final = snapshot()
            except Exception:
                reason = "persistence_error"
                final = {"status": "interrupted"}
                self.publish("error", {"detail": "回答保存失败，请稍后刷新检查"})
            self.state = final.get("status", "done" if terminal is not None else "interrupted")
            if self.state not in ("done", "interrupted"):
                self.state = "interrupted"
            # A stop arriving after the successful final DB commit cannot undo it.
            if self.state == "done":
                reason = "completed"
            elif reason == "completed":
                reason = "error"
            try:
                self.publish("done", {
                    **(terminal or {}), **final,
                    "message_id": self.message_id,
                    "status": self.state,
                    "finish_reason": reason,
                })
            finally:
                self.registry.finished(self)

    async def subscribe(self, after: int = 0) -> AsyncIterator[str]:
        await self._ready.wait()
        yield _frame("meta", {"message_id": self.message_id, "started_at": self.started_at})
        position = 0
        replay_end = self._size
        replay_announced = False
        while not self.closed:
            if not replay_announced and position >= replay_end:
                replay_announced = True
                yield _frame("replay_end", {})
            self._journal.seek(position)
            record = self._journal.readline()
            if record:
                position = self._journal.tell()
                event, data = json.loads(record)
                if data["cursor"] > after:
                    yield _frame(event, data, data["cursor"])
                continue
            if self.state != "running":
                return
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=self.registry.heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    async def stop(self) -> None:
        self.stop_requested = True
        await self._ready.wait()
        if self.task is not None:
            await self._entered.wait()
            # Multiple stop callers must not inject a second cancellation into cleanup.
            if not self.task.done() and not self._stop_sent and not self._finishing:
                self._stop_sent = True
                self.task.cancel()
            await asyncio.shield(self.task)

    def close(self) -> None:
        self.closed = True
        self._changed.set()
        self._journal.close()
        if self._expiry is not None:
            self._expiry.cancel()


class RunRegistry:
    def __init__(self, *, buffer_limit=512 * 1024, heartbeat=15, timeout=600, ttl=60) -> None:
        self.buffer_limit = max(1, buffer_limit)
        self.heartbeat = max(0.001, heartbeat)
        self.timeout = max(0.001, timeout)
        self.ttl = max(0, ttl)
        self._runs: dict[tuple[str, str], ChatRun] = {}

    def reserve(self, uid: str, sid: str) -> ChatRun:
        if any(run.uid == uid and run.state == "running" for run in self._runs.values()):
            raise ValueError("已有回答正在生成，请先停止或等待完成")
        old = self.get(uid, sid)
        if old is not None:
            self.expire(old)
        run = ChatRun(self, uid, sid)
        self._runs[uid, sid] = run
        return run

    def get(self, uid: str, sid: str) -> ChatRun | None:
        return self._runs.get((uid, sid))

    def abandon(self, run: ChatRun) -> None:
        run.state = "interrupted"
        run._ready.set()
        self.expire(run)

    def finished(self, run: ChatRun) -> None:
        run._expiry = asyncio.get_running_loop().call_later(self.ttl, self.expire, run)

    def expire(self, run: ChatRun) -> None:
        if self.get(run.uid, run.sid) is run:
            del self._runs[run.uid, run.sid]
        run.close()

    async def stop(self, uid: str, sid: str) -> None:
        run = self.get(uid, sid)
        if run is not None and run.state == "running":
            await run.stop()

    async def stop_user(self, uid: str) -> None:
        await asyncio.gather(*(run.stop() for run in list(self._runs.values())
                               if run.uid == uid and run.state == "running"))

    async def shutdown(self) -> None:
        runs = list(self._runs.values())
        await asyncio.gather(*(run.stop() for run in runs
                               if run.task is not None or run.preparation is not None))
        for run in runs:
            self.abandon(run)


_registry: RunRegistry | None = None


def get_registry() -> RunRegistry:
    global _registry
    if _registry is None:
        from core import config

        _registry = RunRegistry(
            buffer_limit=config.CHAT_RUN_BUFFER_BYTES,
            heartbeat=config.CHAT_RUN_HEARTBEAT_SECONDS,
            timeout=config.CHAT_RUN_TIMEOUT_SECONDS,
            ttl=config.CHAT_RUN_TERMINAL_TTL_SECONDS,
        )
    return _registry
