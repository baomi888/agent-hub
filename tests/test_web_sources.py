"""联网来源的编号、去重与消息持久化回归。"""

import threading
from concurrent.futures import ThreadPoolExecutor

import agent.tools as tools
from core import config, session


def _fresh_db(monkeypatch, tmp_path) -> None:
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(path))
    monkeypatch.setattr(session, "_LOCAL", threading.local())
    session._init_db()


def _fake_search(payloads, barrier=None):
    def _search(query, count=10):
        if barrier is not None:
            barrier.wait(timeout=5)
        return payloads[query]

    return _search


def test_web_sources_numbered_prefixed_and_url_deduped(monkeypatch):
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")
    monkeypatch.setattr(
        "core.serper.search_serper",
        _fake_search(
            {
                "q1": {
                    "organic": [
                        {"title": "坏链", "link": "javascript:alert(1)"},
                        {"title": "A", "link": "https://a.example/x", "date": "2026-01-01"},
                        {"title": "B", "link": "https://b.example/y"},
                    ]
                },
                "q2": {
                    "organic": [
                        {"title": "A again", "link": "https://a.example/x"},
                        {"title": "C", "link": "https://c.example/z"},
                    ]
                },
            }
        ),
    )
    bag: list[dict] = []
    cfg = {"configurable": {"web_sources": bag}}

    out1 = tools.web_search.func(query="q1", max_results=5, config=cfg)
    out2 = tools.web_search.func(query="q2", max_results=5, config=cfg)

    assert "[W1]" in out1 and "[W2]" in out1
    assert "javascript:" not in out1
    # q2 的第一条与 q1 重复：复用 W1；新网址继续 W3。
    assert "[W1]" in out2 and "[W3]" in out2
    assert [x["id"] for x in bag] == [1, 2, 3]
    assert [x["url"] for x in bag] == [
        "https://a.example/x",
        "https://b.example/y",
        "https://c.example/z",
    ]
    assert bag[0]["key"] == "W1" and bag[0]["date"] == "2026-01-01"


def test_parallel_web_search_never_reuses_ids(monkeypatch):
    """同一轮多个工具并发搜，Wn 也必须唯一。"""
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")
    barrier = threading.Barrier(2)
    monkeypatch.setattr(
        "core.serper.search_serper",
        _fake_search(
            {
                "left": {
                    "organic": [
                        {"title": "L1", "link": "https://left.example/1"},
                        {"title": "L2", "link": "https://left.example/2"},
                    ]
                },
                "right": {
                    "organic": [
                        {"title": "R1", "link": "https://right.example/1"},
                        {"title": "R2", "link": "https://right.example/2"},
                    ]
                },
            },
            barrier,
        ),
    )
    bag: list[dict] = []
    cfg = {"configurable": {"web_sources": bag}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda q: tools.web_search.func(query=q, max_results=5, config=cfg),
                ["left", "right"],
            )
        )

    ids = sorted(x["id"] for x in bag)
    assert ids == [1, 2, 3, 4]
    assert sorted(x["key"] for x in bag) == ["W1", "W2", "W3", "W4"]


def test_message_round_trips_web_sources(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    conv = session.create_conversation("u1", "联网来源")
    items = [
        {
            "id": 1,
            "key": "W1",
            "title": "中文来源",
            "url": "https://example.com/a",
            "domain": "example.com",
            "snippet": "摘要",
            "date": "2026-11-03",
        }
    ]
    mid = session.add_message(
        "u1",
        conv["id"],
        "assistant",
        "正文[W1]",
        web_sources=items,
    )

    msgs = session.get_messages("u1", conv["id"], limit_turns=None)
    assert msgs[-1]["id"] == mid
    assert msgs[-1]["web_sources"] == items
    assert session.get_message("u1", conv["id"], mid)["web_sources"] == items


def test_old_messages_table_migrates_web_sources(monkeypatch, tmp_path):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(path))
    monkeypatch.setattr(session, "_LOCAL", threading.local())

    with session._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, owner_id TEXT, title TEXT NOT NULL,
                kb_id TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                refs TEXT,
                created_at INTEGER NOT NULL
            );
            INSERT INTO conversations VALUES ('c1', 'u1', '旧会话', NULL, 1, 1);
            INSERT INTO messages
                (conversation_id, role, content, refs, created_at)
            VALUES ('c1', 'assistant', '旧回答', '', 1);
            """
        )

    session._init_db()
    msg = session.get_messages("u1", "c1", limit_turns=None)[0]
    assert msg["web_sources"] == []

    # 坏 JSON 也不能把历史接口打崩。
    with session._get_conn() as conn:
        conn.execute("UPDATE messages SET web_sources = ? WHERE id = 1", ("not-json",))
    assert session.get_messages("u1", "c1", limit_turns=None)[0]["web_sources"] == []
