"""Background chat API ownership and durable finalization regressions."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api.chat as cm
from core.chat_runs import RunRegistry


def test_second_session_conflicts_before_gate_or_debit(monkeypatch):
    registry = RunRegistry()
    run = registry.reserve("u", "first")
    monkeypatch.setattr(cm, "get_registry", lambda: registry)
    def unexpected(*a, **kw):
        pytest.fail("conflicting request must not acquire a slot or debit quota")
    monkeypatch.setattr(cm.llm_gate, "acquire", unexpected)
    monkeypatch.setattr(cm.quota, "consume", unexpected)
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(cm.chat_stream(cm.ChatRequest(sid="second", question="q"), None, "u"))
        assert error.value.status_code == 409
    finally:
        registry.abandon(run)


def test_resume_checks_conversation_ownership_before_registry(monkeypatch):
    calls = []
    monkeypatch.setattr(cm.store, "get_conversation", lambda uid, sid: calls.append((uid, sid)))
    monkeypatch.setattr(cm, "get_registry", lambda: pytest.fail("unauthorized registry lookup"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(cm.resume_stream("private", after=0, message_id=None, uid="intruder"))
    assert error.value.status_code == 404
    assert calls == [("intruder", "private")]


def test_resume_without_process_run_returns_no_content(monkeypatch):
    monkeypatch.setattr(cm.store, "get_conversation", lambda *a: {"id": "s"})
    monkeypatch.setattr(cm, "get_registry", lambda: RunRegistry())
    response = asyncio.run(cm.resume_stream("s", after=0, message_id=None, uid="u"))
    assert response.status_code == 204


@pytest.mark.parametrize("after,message_id,status", [(2, 7, 400), (0, 8, 409)])
def test_resume_rejects_stale_message_and_future_cursor(monkeypatch, after, message_id, status):
    monkeypatch.setattr(cm.store, "get_conversation", lambda *a: {"id": "s"})
    run = SimpleNamespace(message_id=7, cursor=1)
    monkeypatch.setattr(cm, "get_registry", lambda: SimpleNamespace(get=lambda *a: run))
    with pytest.raises(HTTPException) as error:
        asyncio.run(cm.resume_stream("s", after=after, message_id=message_id, uid="u"))
    assert error.value.status_code == status


@pytest.mark.parametrize("failure", [False, RuntimeError("locked")])
def test_failed_flush_keeps_retry_markers(monkeypatch, failure):
    draft = cm._StreamDraft("u", "s", 7)
    draft.text = "x" * draft._EVERY_CHARS
    before = draft._written_at
    def fail(*a):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(cm.store, "update_message_content", fail)
    asyncio.run(draft.flush())
    assert draft._written == 0
    assert draft._written_at == before
    assert draft.wants_flush()
    monkeypatch.setattr(cm.store, "update_message_content", lambda *a: True)
    asyncio.run(draft.seal())
    assert draft.sealed
    assert draft.status == "done"


def test_cancelled_flush_finishes_write_before_terminal_seal(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    writes = []
    def write(*args, **kwargs):
        status = args[4]
        if status == "streaming":
            entered.set()
            assert release.wait(5), "test did not release pending write"
        writes.append(status)
        return True
    monkeypatch.setattr(cm.store, "update_message_content", write)
    draft = cm._StreamDraft("u", "s", 7)
    draft.text = "partial"
    async def main():
        task = asyncio.create_task(draft.flush())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        draft.seal_sync()
    asyncio.run(main())
    assert writes == ["streaming", "interrupted"]
    assert draft.sealed


def test_stop_during_terminal_commit_preserves_committed_done(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    writes = []
    def write(*args, **kwargs):
        assert args[4] == "done"
        entered.set()
        assert release.wait(5)
        writes.append(args[4])
        return True
    monkeypatch.setattr(cm.store, "update_message_content", write)
    draft = cm._StreamDraft("u", "s", 7)
    draft.text = "complete"
    async def main():
        task = asyncio.create_task(draft.seal())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert draft.sealed
        assert draft.status == "done"
        draft.seal_sync()
    asyncio.run(main())
    assert writes == ["done"]


def test_failed_terminal_write_retries_original_status_and_metadata(monkeypatch):
    writes = []

    def write(*args, **kwargs):
        writes.append((args, kwargs))
        return len(writes) > 1

    monkeypatch.setattr(cm.store, "update_message_content", write)
    draft = cm._StreamDraft("u", "s", 7)
    draft.text = "complete"
    asyncio.run(draft.seal("done", refs="[1]", truncated=True))
    assert not draft.sealed
    draft.seal_sync()
    assert draft.status == "done"
    assert draft.sealed
    assert writes[-1][0][4:7] == ("done", "[1]", True)


@pytest.mark.parametrize("state,message_id", [("done", 7), ("running", None)])
def test_session_detail_retains_replay_for_snapshot_or_pending_run(monkeypatch, state, message_id):
    import api.sessions as sessions
    monkeypatch.setattr(sessions.store, "get_conversation", lambda *a: {"id": "s"})
    monkeypatch.setattr(sessions.store, "get_messages", lambda *a: [{"id": 7, "status": "streaming"}])
    monkeypatch.setattr(sessions.store, "count_messages", lambda *a: 1)
    run = SimpleNamespace(state=state, message_id=message_id, started_at=123)
    monkeypatch.setattr(sessions, "get_registry", lambda: SimpleNamespace(get=lambda *a: run))
    detail = sessions.get_detail("s", "u")
    assert detail["active_run"] == {"message_id": message_id, "started_at": 123}


def test_session_detail_rereads_stale_streaming_without_retained_run(monkeypatch):
    import api.sessions as sessions
    monkeypatch.setattr(sessions.store, "get_conversation", lambda *a: {"id": "s"})
    snapshots = iter([[{"id": 7, "status": "streaming"}], [{"id": 7, "status": "done"}]])
    monkeypatch.setattr(sessions.store, "get_messages", lambda *a: next(snapshots))
    monkeypatch.setattr(sessions.store, "count_messages", lambda *a: 1)
    monkeypatch.setattr(sessions, "get_registry", lambda: SimpleNamespace(get=lambda *a: None))
    detail = sessions.get_detail("s", "u")
    assert detail["active_run"] is None
    assert detail["messages"][0]["status"] == "done"


def test_request_cancel_during_preparation_preserves_run_ownership(monkeypatch):
    registry = RunRegistry()
    monkeypatch.setattr(cm, "get_registry", lambda: registry)
    ticket = SimpleNamespace(released=0)
    def release():
        ticket.released += 1
    ticket.release = release
    async def acquire():
        return ticket
    monkeypatch.setattr(cm.llm_gate, "acquire", acquire)
    async def main():
        entered = asyncio.Event()
        allow_commit = asyncio.Event()
        async def prepare(req, request, uid, acquired, run):
            entered.set()
            await allow_commit.wait()
            async def source():
                yield 'event: done\ndata: {"answer": "complete"}\n\n'
            async def cleanup():
                acquired.release()
            run.start(source(), cleanup, lambda: {"status": "done"}, message_id=7)
            return object()
        monkeypatch.setattr(cm, "_chat_stream_impl", prepare)
        task = asyncio.create_task(cm.chat_stream(cm.ChatRequest(sid="s", question="q"), None, "u"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        run = registry.get("u", "s")
        assert run is not None and run.state == "running"
        assert ticket.released == 0
        allow_commit.set()
        await run.preparation
        await run.task
        assert run.state == "done"
        assert ticket.released == 1
        await registry.shutdown()
    asyncio.run(main())
