"""`/api/tasks` 的 HTTP 层：路由顺序、归属 404、due/remind 分开、与提醒中心的边界。

最值得钉的三条：

1. **`/summary` 不能被 `/{task_id}` 抢走** —— 抢走之后返回的是「待办不存在」，
   而前端只会显示一片空列表，没有任何报错。
2. **PATCH 必须能传 `null`** —— 用 `exclude_none=True` 的话「清空截止时间」
   永远失败，而且静默失败（前端看到 200，值却没变）。
3. **linked reminder 的写接口必须 400** —— 放行就会出现「待办写 20:00、
   提醒写 21:00」，两边各说一套。
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="baomi_tasks_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session as session  # noqa: E402

_ORIG_DB_PATH = session._db_path
session._db_path = lambda: os.path.join(_TMP, "boot.db")
session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import push_store, reminder_core, reminder_store, task_core  # noqa: E402
from core.auth import get_current_user  # noqa: E402

session._db_path = _ORIG_DB_PATH


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    reminder_core.reset_cache()
    task_core.reset_cache()
    reminder_store._READY.clear()
    push_store._READY.clear()
    session._init_db()
    yield path
    reminder_core.reset_cache()
    task_core.reset_cache()
    reminder_store._READY.clear()
    push_store._READY.clear()


@pytest.fixture()
def client(db):
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _as(name: str) -> None:
    main.app.dependency_overrides[get_current_user] = lambda: name


def _create(client, **kwargs) -> dict:
    body = {"title": "交高数作业", "list_key": "homework"}
    body.update(kwargs)
    res = client.post("/api/tasks", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ==================== CRUD ====================


class TestCrud:
    def test_create_returns_full_view(self, client):
        task = _create(client, due_at=1_800_000_000, note="第 3 章")
        assert task["id"].startswith("tk_")
        assert task["list_key"] == "homework"
        assert task["status"] == "open"
        assert task["due_at"] == 1_800_000_000
        assert task["note"] == "第 3 章"
        assert task["reminder"] is None

    def test_get_missing_is_404(self, client):
        res = client.get("/api/tasks/tk_nope")
        assert res.status_code == 404
        assert res.json()["detail"] == "待办不存在"

    def test_list_scopes(self, client):
        _create(client, title="无日期")
        for scope in ("today", "upcoming", "all", "completed"):
            res = client.get(f"/api/tasks?scope={scope}")
            assert res.status_code == 200, res.text
            assert res.json()["scope"] == scope
        assert len(client.get("/api/tasks?scope=all").json()["items"]) == 1
        assert len(client.get("/api/tasks?scope=today").json()["items"]) == 0

    def test_bad_scope_is_400(self, client):
        assert client.get("/api/tasks?scope=week").status_code == 400

    def test_summary_route_not_shadowed(self, client):
        """`/summary` 若被 `/{task_id}` 抢走，这里会是 404「待办不存在」。"""
        res = client.get("/api/tasks/summary")
        assert res.status_code == 200
        body = res.json()
        assert set(("today", "overdue", "upcoming", "all_open", "completed", "by_list")) <= set(body)
        assert body["next"] is None

    def test_summary_counts(self, client):
        _create(client, title="a")
        task = _create(client, title="b")
        client.post(f"/api/tasks/{task['id']}/complete")
        body = client.get("/api/tasks/summary").json()
        assert body["all_open"] == 1
        assert body["completed"] == 1
        assert body["by_list"]["homework"] == 1


class TestPatch:
    def test_patch_title_and_priority(self, client):
        task = _create(client)
        res = client.patch(f"/api/tasks/{task['id']}", json={"title": "改个名", "priority": 2})
        assert res.status_code == 200
        assert res.json()["title"] == "改个名"
        assert res.json()["priority"] == 2

    def test_explicit_null_clears_due_at(self, client):
        """回归：用 exclude_none 的话这里会静默失败。"""
        task = _create(client, due_at=1_800_000_000)
        res = client.patch(f"/api/tasks/{task['id']}", json={"due_at": None})
        assert res.status_code == 200
        assert res.json()["due_at"] is None

    def test_explicit_null_clears_remind_at(self, client):
        import time

        remind = int(time.time()) + 3600
        task = _create(client, remind_at=remind)
        assert task["reminder"]["enabled"] is True
        body = client.patch(f"/api/tasks/{task['id']}", json={"remind_at": None}).json()
        assert body["remind_at"] is None
        assert body["reminder"]["enabled"] is False

    def test_empty_patch_is_400(self, client):
        task = _create(client)
        assert client.patch(f"/api/tasks/{task['id']}", json={}).status_code == 400

    def test_past_remind_is_400(self, client):
        task = _create(client)
        res = client.patch(f"/api/tasks/{task['id']}", json={"remind_at": 1_600_000_000})
        assert res.status_code == 400
        assert res.json()["detail"] == "提醒时间要晚于现在"

    def test_patch_missing_is_404(self, client):
        assert client.patch("/api/tasks/tk_nope", json={"title": "x"}).status_code == 404


class TestLifecycle:
    def test_complete_then_reopen(self, client):
        import time

        task = _create(client, remind_at=int(time.time()) + 7200)
        done = client.post(f"/api/tasks/{task['id']}/complete").json()
        assert done["status"] == "done"
        assert done["completed_at"] is not None
        assert done["reminder"]["enabled"] is False
        again = client.post(f"/api/tasks/{task['id']}/complete")
        assert again.status_code == 200
        assert again.json()["completed_at"] == done["completed_at"]

        reopened = client.post(f"/api/tasks/{task['id']}/reopen").json()
        assert reopened["status"] == "open"
        assert reopened["completed_at"] is None
        assert reopened["reminder"]["enabled"] is True

    def test_delete(self, client):
        task = _create(client)
        res = client.delete(f"/api/tasks/{task['id']}")
        assert res.status_code == 200
        assert res.json() == {"ok": True, "id": task["id"]}
        assert client.get(f"/api/tasks/{task['id']}").status_code == 404
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 404


class TestIsolation:
    def test_cross_user_404(self, client):
        task = _create(client)
        _as("u2")
        assert client.get(f"/api/tasks/{task['id']}").status_code == 404
        assert client.patch(f"/api/tasks/{task['id']}", json={"title": "x"}).status_code == 404
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 404
        assert client.post(f"/api/tasks/{task['id']}/complete").status_code == 404
        assert client.post(f"/api/tasks/{task['id']}/reopen").status_code == 404
        # 别人的列表里也看不到
        assert client.get("/api/tasks?scope=all").json()["items"] == []


# ==================== 与提醒中心的边界 ====================


class TestReminderBoundary:
    def test_reminders_list_hides_task_reminders(self, client):
        import time

        _create(client, title="带提醒的待办", remind_at=int(time.time()) + 3600)
        client.post(
            "/api/reminders", json={"title": "手工提醒", "freq": "daily", "at": "22:00"}
        )
        items = client.get("/api/reminders?scope=all").json()["items"]
        assert [i["title"] for i in items] == ["手工提醒"]

    def test_reminder_summary_ignores_task_reminders(self, client):
        import time

        _create(client, remind_at=int(time.time()) + 3600)
        body = client.get("/api/reminders/summary").json()
        assert body["total"] == 0
        assert body["today_count"] == 0

    def test_cannot_patch_linked_reminder_directly(self, client):
        import time

        task = _create(client, remind_at=int(time.time()) + 3600)
        rid = task["reminder"]["id"]
        res = client.patch(f"/api/reminders/{rid}", json={"title": "偷改"})
        assert res.status_code == 400
        assert "待办提醒" in res.json()["detail"]
        # 待办那边一个字都没变
        assert client.get(f"/api/tasks/{task['id']}").json()["title"] == "交高数作业"

    def test_cannot_delete_or_snooze_linked_reminder(self, client):
        import time

        task = _create(client, remind_at=int(time.time()) + 3600)
        rid = task["reminder"]["id"]
        assert client.delete(f"/api/reminders/{rid}").status_code == 400
        assert client.post(f"/api/reminders/{rid}/snooze", json={"minutes": 10}).status_code == 400
        assert client.get(f"/api/tasks/{task['id']}").json()["reminder"]["enabled"] is True

    def test_source_switch_rejects_task(self, client):
        res = client.put("/api/reminders/sources", json={"source": "task", "enabled": False})
        assert res.status_code == 400
