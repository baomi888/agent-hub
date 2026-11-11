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


class TestIcon:
    """自选图标：新建能带上、PATCH 能改、显式 null 能清回"自动"。

    这三条覆盖的是三种**会静默出错**的形状：
      - 建表加了列但 INSERT 没写 → 传了 icon 回来是 null（用户以为没保存）
      - PATCH 用 exclude_none 生成 patch → 清空永远失败（点"自动"没反应）
      - 不校验字符集 → 脏字符串进 JSON，前端渲染成玉米且看不出来

    ★ 编码只挡字符集、不枚举图标名：`icon` 是展示字段，前端认不出会自己退回
      清单图标。枚举会让"前端加个图标"变成"后端也得发版"。
    """

    def test_create_with_icon(self, client):
        task = _create(client, icon="star")
        assert task["icon"] == "star"
        # 回读也要有，不然只有创建响应带着它、列表页照样是玉米
        assert client.get(f"/api/tasks/{task['id']}").json()["icon"] == "star"

    def test_create_without_icon_is_null(self, client):
        assert _create(client)["icon"] is None

    def test_patch_sets_and_clears_icon(self, client):
        task = _create(client)
        assert client.patch(f"/api/tasks/{task['id']}", json={"icon": "bell"}).json()["icon"] == "bell"
        # 显式 null = 回到自动（落回清单/标题兜底）
        assert client.patch(f"/api/tasks/{task['id']}", json={"icon": None}).json()["icon"] is None

    def test_rejects_dirty_icon_name(self, client):
        res = client.post("/api/tasks", json={"title": "x", "icon": "star; drop table"})
        assert res.status_code == 400
        assert "图标名" in res.json()["detail"]

    def test_icon_shows_up_in_list(self, client):
        _create(client, title="带图标的", icon="book")
        items = client.get("/api/tasks?scope=all").json()["items"]
        assert items[0]["icon"] == "book"


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
        client.post("/api/reminders", json={"title": "手工提醒", "freq": "daily", "at": "22:00"})
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


# ==================== 排序与批量 ====================


class TestReorder:
    def test_reorder_changes_list_order(self, client):
        """排序生效的判据是"前后翻过来了"，不是"初始必须是 A、B"。

        ★ 这里原来断言初始顺序 `== ["A", "B"]`，但它**不确定**：两条在同一个
          Unix 秒里创建，`created_at` 相同，`ORDER BY ... created_at DESC` 之后
          没有任何 tiebreaker，剩下的顺序由查询计划决定 —— 四个测试文件一起跑时
          偶尔会以 B、A 返回，于是这条测试随机变红（2026-11-11 实测：单跑绿、
          组合偶尔红，同一秒内两次创建）。测试断言"先记的在前"等于在断言
          SQLite 的隐式行为，不是在断言 reorder。
        """
        a = _create(client, title="A")
        b = _create(client, title="B")
        before = [i["title"] for i in client.get("/api/tasks?scope=all").json()["items"]]
        assert sorted(before) == ["A", "B"]

        res = client.post("/api/tasks/reorder", json={"ids": [b["id"], a["id"]]})
        assert res.status_code == 200
        assert res.json() == {"ok": True, "changed": 2}
        after = [i["title"] for i in client.get("/api/tasks?scope=all").json()["items"]]
        # 显式排过之后顺序就是确定的：B 在前、A 在后
        assert after == ["B", "A"]

    def test_reorder_is_not_swallowed_by_task_id_route(self, client):
        """`POST /reorder` 若被 `/{task_id}` 抢走，会返回 404「待办不存在」。"""
        res = client.post("/api/tasks/reorder", json={"ids": []})
        assert res.status_code == 200
        assert res.json()["changed"] == 0

    def test_patch_rejects_negative_sort_order(self, client):
        task = _create(client)
        assert client.patch(f"/api/tasks/{task['id']}", json={"sort_order": -5}).status_code == 422
        assert client.patch(f"/api/tasks/{task['id']}", json={"sort_order": 30}).json()["sort_order"] == 30


class TestBulk:
    def test_bulk_complete(self, client):
        a = _create(client, title="A")
        b = _create(client, title="B")
        res = client.post("/api/tasks/bulk", json={"ids": [a["id"], b["id"]], "action": "complete"})
        assert res.status_code == 200
        assert res.json()["changed"] == 2
        assert client.get("/api/tasks?scope=all").json()["items"] == []
        assert len(client.get("/api/tasks?scope=completed").json()["items"]) == 2

    def test_bulk_reopen(self, client):
        a = _create(client, title="A")
        client.post(f"/api/tasks/{a['id']}/complete")
        assert client.post("/api/tasks/bulk", json={"ids": [a["id"]], "action": "reopen"}).json()["changed"] == 1
        assert client.get("/api/tasks?scope=all").json()["items"][0]["id"] == a["id"]

    def test_bulk_delete(self, client):
        a = _create(client, title="A")
        b = _create(client, title="B")
        assert client.post("/api/tasks/bulk", json={"ids": [a["id"]], "action": "delete"}).json()["changed"] == 1
        assert client.get(f"/api/tasks/{a['id']}").status_code == 404
        assert client.get(f"/api/tasks/{b['id']}").status_code == 200

    def test_bulk_move(self, client):
        a = _create(client, title="A")
        res = client.post("/api/tasks/bulk", json={"ids": [a["id"]], "action": "move", "list_key": "life"})
        assert res.json()["changed"] == 1
        assert client.get(f"/api/tasks/{a['id']}").json()["list_key"] == "life"

    def test_bulk_move_without_list_key_is_400(self, client):
        a = _create(client)
        res = client.post("/api/tasks/bulk", json={"ids": [a["id"]], "action": "move"})
        assert res.status_code == 400
        assert "清单" in res.json()["detail"]
        # 没有静默搬进 inbox
        assert client.get(f"/api/tasks/{a['id']}").json()["list_key"] == "homework"

    def test_bulk_unknown_action_is_400(self, client):
        a = _create(client)
        res = client.post("/api/tasks/bulk", json={"ids": [a["id"]], "action": "archive"})
        assert res.status_code == 400

    def test_bulk_cross_user_changes_nothing(self, client):
        theirs = _create(client, title="别人的")
        _as("u2")
        assert client.post("/api/tasks/bulk", json={"ids": [theirs["id"]], "action": "complete"}).json()["changed"] == 0
        _as("u1")
        assert client.get(f"/api/tasks/{theirs['id']}").json()["status"] == "open"

    def test_bulk_ids_capped_at_200(self, client):
        """上限写在 pydantic 上：201 个 id 直接 422，不靠核心层截断。"""
        ids = [f"tk_{i}" for i in range(task_core.MAX_BULK + 1)]
        res = client.post("/api/tasks/bulk", json={"ids": ids, "action": "complete"})
        assert res.status_code == 422
        ok = client.post("/api/tasks/bulk", json={"ids": ids[: task_core.MAX_BULK], "action": "complete"})
        assert ok.status_code == 200

    def test_reorder_ids_capped_at_200(self, client):
        ids = [f"tk_{i}" for i in range(task_core.MAX_BULK + 1)]
        assert client.post("/api/tasks/reorder", json={"ids": ids}).status_code == 422
