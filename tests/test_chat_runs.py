"""Background execution, bounded replay and lifecycle regression tests."""

import asyncio
import json
import unittest

from core.chat_runs import RunRegistry


def frame(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class ChatRunsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = RunRegistry(buffer_limit=32, heartbeat=0.01, timeout=1, ttl=60)

    async def asyncTearDown(self):
        await self.registry.shutdown()

    async def test_disconnect_keeps_producer_and_replays_without_gap(self):
        resume = asyncio.Event()
        cleaned = []

        async def source():
            yield frame("token", {"text": "你好😀"})
            await resume.wait()
            yield frame("token", {"text": "世界"})
            yield frame("done", {"answer": "你好😀世界"})

        async def cleanup():
            cleaned.append(True)

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {"answer": "你好😀世界", "status": "done"}, message_id=7)
        stream = run.subscribe()
        self.assertIn("meta", await anext(stream))
        first = await anext(stream)
        while "replay_end" in first or first.startswith(":"):
            first = await anext(stream)
        self.assertIn("token", first)
        await stream.aclose()
        self.assertFalse(run.task.done())
        resume.set()
        replay = [item async for item in run.subscribe(after=1)]
        self.assertTrue(any("世界" in item for item in replay))
        self.assertFalse(any("你好" in item and "token" in item for item in replay))
        self.assertEqual(cleaned, [True])
        self.assertTrue(run.spilled)

    async def test_one_active_run_per_user_and_release_after_completion(self):
        run = self.registry.reserve("u", "s")
        with self.assertRaises(ValueError):
            self.registry.reserve("u", "other")
        self.registry.abandon(run)
        self.registry.reserve("u", "other")

    async def test_stop_is_idempotent_and_emits_one_terminal_after_cleanup(self):
        cleaned = []

        async def source():
            yield frame("token", {"text": "半句"})
            await asyncio.Event().wait()

        async def cleanup():
            cleaned.append(True)

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {"answer": "半句"}, message_id=7)
        await asyncio.gather(run.stop(), run.stop())
        items = [item async for item in run.subscribe()]
        self.assertEqual(cleaned, [True])
        self.assertEqual(sum("event: done" in item for item in items), 1)
        self.assertTrue(any('"status": "interrupted"' in item for item in items))

    async def test_timeout_and_expiry(self):
        self.registry.timeout = 0.01

        async def source():
            await asyncio.Event().wait()
            yield ""

        async def cleanup():
            pass

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {}, message_id=7)
        items = [item async for item in run.subscribe()]
        self.assertIn('"finish_reason": "timeout"', items[-1])
        self.registry.expire(run)
        self.assertIsNone(self.registry.get("u", "s"))
        self.assertTrue(run.closed)

    async def test_subscribe_after_final_cursor_terminates(self):
        async def source():
            yield frame("done", {"answer": "完成"})

        async def cleanup():
            pass

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {"status": "done"}, message_id=7)
        await run.task
        items = [item async for item in run.subscribe(after=run.cursor)]
        self.assertEqual(len(items), 2)  # meta and replay_end, no duplicate terminal event

    async def test_stop_after_final_commit_keeps_persisted_done(self):
        entered = asyncio.Event()

        async def source():
            yield frame("token", {"text": "完成"})
            entered.set()
            await asyncio.Event().wait()

        async def cleanup():
            pass

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {"status": "done", "answer": "完成"}, message_id=7)
        await entered.wait()
        await run.stop()
        self.assertEqual(run.state, "done")
        items = [item async for item in run.subscribe()]
        self.assertTrue(any('"finish_reason": "completed"' in item for item in items))

    async def test_stop_during_cleanup_waits_without_cancelling_finalization(self):
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def source():
            yield frame("done", {"answer": "完成"})

        async def cleanup():
            cleaning.set()
            await release.wait()

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {"status": "done"}, message_id=7)
        await cleaning.wait()
        stopping = asyncio.create_task(run.stop())
        await asyncio.sleep(0)
        self.assertFalse(run.task.cancelled())
        release.set()
        await stopping
        self.assertEqual(run.state, "done")
        self.assertFalse(run.task.cancelled())
        self.registry.reserve("u", "next")

    async def test_cleanup_failure_does_not_report_successful_terminal(self):
        async def source():
            yield frame("done", {"answer": "完成"})

        async def cleanup():
            raise RuntimeError("database unavailable")

        run = self.registry.reserve("u", "s")
        run.start(source(), cleanup, lambda: {"status": "done"}, message_id=7)
        await run.task
        self.assertEqual(run.state, "interrupted")
        items = [item async for item in run.subscribe()]
        self.assertTrue(any('"finish_reason": "persistence_error"' in item for item in items))


if __name__ == "__main__":
    unittest.main()
