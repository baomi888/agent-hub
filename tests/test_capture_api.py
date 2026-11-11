"""`POST /api/capture/parse` 的 HTTP 契约（Phase B）。

## 为什么单独一份

`tests/test_capture_parse.py` 管的是"模型答得不好时我们怎么收拾"，
这一分管的是**前端能看到的四种状态**：
200 / 400 / 429（两种来源）/ 502。前端的失败文案是按状态码分叉的，
状态码错了，界面就会把"稍后再试"说成"系统坏了"。

## 两条纪律

1. **不碰真模型**：`capture_parse.parse` 整个替换掉。这里要验的是路由的分叉，
   不是模型的水平 —— 混在一起会让这个文件又慢又不稳。
2. **不写库**：这个端点最容易被"顺手"加上写库。下面专门有一条断言
   `tasks` 表在调用前后条数不变。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from core import capture_parse, config, llm_gate, session
from core.auth import get_current_user

_ORIG_DB_PATH = session._db_path


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    yield path
    monkeypatch.setattr(session, "_db_path", _ORIG_DB_PATH)


@pytest.fixture()
def client(db):
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
    main.app.dependency_overrides.clear()


async def _ok(text, now):
    return {
        "items": [
            {
                "id": "p1",
                "kind": "task",
                "title": "交高数作业",
                "note": None,
                "list_key": "homework",
                "priority": 0,
                "due_at": 1762876740,
                "remind_at": None,
                "recurrence": None,
                "source_text": "明天交高数作业",
                "source_start": 0,
                "source_end": 7,
                "needs_confirm": False,
                "questions": [],
            }
        ],
        "unresolved": [],
        "model": "fake",
        "latency_ms": 12,
    }


class TestAuth:
    def test_requires_login(self, db, monkeypatch):
        """没登录必须 401。这个端点是花 token 的，绝不能匿名可打。"""
        monkeypatch.setattr(capture_parse, "parse", _ok)
        r = TestClient(main.app, follow_redirects=False).post(
            "/api/capture/parse", json={"text": "明天交作业"}
        )
        assert r.status_code == 401


class TestBodyChecks:
    def test_whitespace_only_text_is_400(self, client):
        """空白不是"内容"，400 比 200 + 空结果诚实：重试也一样。"""
        r = client.post("/api/capture/parse", json={"text": "   \n  "})
        assert r.status_code == 400
        assert "先说一句" in r.json()["detail"]

    def test_text_too_long_is_422(self, client):
        r = client.post("/api/capture/parse", json={"text": "好" * 2001})
        assert r.status_code == 422

    def test_route_has_no_trailing_slash(self):
        """端点注册时**不带**尾斜杠。

        项目里有一条 `normalize_api_slash` 中间件，所以 `/parse/` 也能通 ——
        但那是中间件在兜底，不是路由本身。这里钉的是路由的真实形态：
        谁把装饰器写成 `@router.post("/parse/")`，不带斜杠的调用就会多一次
        307（浏览器跟随时会丢 body/cookie，是本项目的一类历史事故）。
        """
        # 走 OpenAPI 而不是 app.routes：include_router 进来的是 _IncludedRouter，
        # 它的 .path 是 None，直接扫 app.routes 会一条都找不到（main.py 里踩过）。
        paths = set(main.app.openapi().get("paths", {}))
        assert "/api/capture/parse" in paths
        assert "/api/capture/parse/" not in paths


class TestHappyPath:
    def test_returns_proposal_shape(self, client, monkeypatch):
        monkeypatch.setattr(capture_parse, "parse", _ok)
        r = client.post("/api/capture/parse", json={"text": "明天交高数作业"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"items", "unresolved", "model", "latency_ms"}
        assert body["items"][0]["kind"] == "task"

    def test_zero_items_is_still_200(self, client, monkeypatch):
        """0 条是**成功**：前端要显示"没有整理出安排"+ 输入框，不是失败页。"""
        async def _empty(text, now):
            return {"items": [], "unresolved": ["都是口头语"], "model": "fake", "latency_ms": 5}

        monkeypatch.setattr(capture_parse, "parse", _empty)
        r = client.post("/api/capture/parse", json={"text": "呃，然后那个"})
        assert r.status_code == 200 and r.json()["items"] == []

    def test_does_not_write_any_task(self, client, monkeypatch):
        """规格 §5.6：这个端点一个字节都不写库。"""
        monkeypatch.setattr(capture_parse, "parse", _ok)
        before = _task_count()
        client.post("/api/capture/parse", json={"text": "明天交高数作业"})
        assert _task_count() == before


def _task_count() -> int:
    import sqlite3

    conn = sqlite3.connect(session._db_path())
    try:
        row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0  # 表还没建 = 0 条
    finally:
        conn.close()


class TestFailureMapping:
    def test_parse_failed_is_502_with_speakable_detail(self, client, monkeypatch):
        async def _boom(text, now):
            raise capture_parse.ParseFailed("模型返回的不是 JSON")

        monkeypatch.setattr(capture_parse, "parse", _boom)
        r = client.post("/api/capture/parse", json={"text": "明天交作业"})
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "没整理出来" in detail, "详情要能让界面直接展示，不能是英文栈"

    def test_upstream_error_is_502_without_leaking_key(self, client, monkeypatch):
        """上游报错常带 base_url 甚至 key 片段，一律不许透给前端。"""
        async def _boom(text, now):
            raise RuntimeError("Connection to https://api.deepseek.com failed, key=sk-abc123")

        monkeypatch.setattr(capture_parse, "parse", _boom)
        r = client.post("/api/capture/parse", json={"text": "明天交作业"})
        assert r.status_code == 502
        assert "sk-abc123" not in r.json()["detail"]
        assert "deepseek" not in r.json()["detail"]

    def test_gate_busy_is_429_with_retry_after(self, client, monkeypatch):
        async def _busy(text, now):
            raise llm_gate.Busy(waited=8.0, limit=2, retry_after=15)

        monkeypatch.setattr(capture_parse, "parse", _busy)
        r = client.post("/api/capture/parse", json={"text": "明天交作业"})
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "15"
        assert "有点多" in r.json()["detail"]
        # 时长只在 retry_after 字段里，detail 不带 —— 否则前端拼出来是「（约 15 秒）（约 15 秒）」
        assert r.json()["retry_after"] == 15
        assert "15 秒" not in r.json()["detail"]

    def test_per_minute_limit_hits_429(self, db, monkeypatch):
        """同一个人一分钟内连打：第二次就是 429。
        整理比聊天更该限流 —— 聊天卡住用户会自己停，整理卡住用户会连按发送。

        ★ 用一个**专属 uid**：`quota._HITS` 是模块级内存窗口，同一进程里
          跨测试不清零。共用 "u1" 的话，前面几条测试已经把这一分钟的额度用掉了，
          这条会变成"第一次就 429"，看起来像限流写错了。
        """
        monkeypatch.setattr(capture_parse, "parse", _ok)
        monkeypatch.setattr(config, "CAPTURE_PARSE_PER_MINUTE", 1)
        main.app.dependency_overrides[get_current_user] = lambda: "u-rate-limit"
        try:
            c = TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
            assert c.post("/api/capture/parse", json={"text": "第一句"}).status_code == 200
            r = c.post("/api/capture/parse", json={"text": "第二句"})
            assert r.status_code == 429
            assert "频繁" in r.json()["detail"]
        finally:
            main.app.dependency_overrides.clear()
