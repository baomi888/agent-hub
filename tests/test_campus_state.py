"""校园快照上云的 HTTP 层（规格 2.3）。

四件事必须钉住：
1. **整份覆盖**：不做字段级合并，冲突时云端更新的一方赢；
2. **409 要带云端那份**：前端据此直接采用，不必再拉一次；
3. **白名单是穷举的**：番茄钟必须被明确拒绝（它有自己的同步，接两遍会丢记录）；
4. **归属隔离**：猜别人的数据拿不到（表主键是 (owner_id, tool)，天然隔离）。
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="baomi_campus_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session as session  # noqa: E402

_ORIG_DB_PATH = session._db_path
session._db_path = lambda: os.path.join(_TMP, "boot.db")
session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import campus_store, timeutil  # noqa: E402
from core.auth import get_current_user  # noqa: E402

session._db_path = _ORIG_DB_PATH

COURSES = "campus.schedule.courses.v2"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    campus_store.reset_cache()
    session._init_db()
    yield path
    campus_store.reset_cache()


@pytest.fixture()
def client(db):
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _put(client, tool: str, payload, updated_at: int):
    return client.put(f"/api/campus/state/{tool}", json={"payload": payload, "updated_at": updated_at})


class TestRoundTrip:
    def test_empty_state(self, client):
        body = client.get("/api/campus/state").json()
        assert body["items"] == []
        # 白名单也一起下发：前端不必自己维护第二份键名表
        assert COURSES in body["tools"]
        # owner 用来判断「本机缓存属于哪个账号」，换账号时前端要靠它清缓存
        assert body["owner"] == "u1"

    def test_put_then_get(self, client):
        snap = {"courses": [{"id": 1, "name": "高数"}], "term": {"start": "2026-09-07", "weeks": 18}}
        assert _put(client, COURSES, snap, 1_700_000_000_000).status_code == 200
        body = client.get("/api/campus/state").json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["tool"] == COURSES
        assert item["payload"] == snap
        assert item["updated_at"] == 1_700_000_000_000

    def test_whole_replacement_not_merge(self, client):
        """第二次上报少了字段，云端也必须跟着少 —— 整份覆盖，不是合并。"""
        _put(client, COURSES, {"courses": [1, 2], "term": None}, 1000)
        _put(client, COURSES, {"courses": [1]}, 2000)
        payload = client.get("/api/campus/state").json()["items"][0]["payload"]
        assert payload == {"courses": [1]}, payload

    def test_same_timestamp_is_allowed(self, client):
        """相等不算冲突：网络重试会带同一个 updated_at。"""
        assert _put(client, COURSES, {"a": 1}, 5000).status_code == 200
        assert _put(client, COURSES, {"a": 2}, 5000).status_code == 200
        assert client.get("/api/campus/state").json()["items"][0]["payload"] == {"a": 2}


class TestConflict:
    def test_cloud_newer_returns_409_with_cloud_payload(self, client):
        _put(client, COURSES, {"cloud": True}, 3000)
        res = _put(client, COURSES, {"cloud": False}, 2000)
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["reason"] == "cloud_newer"
        assert detail["payload"] == {"cloud": True}
        assert detail["updated_at"] == 3000

    def test_conflict_does_not_overwrite(self, client):
        _put(client, COURSES, {"keep": "cloud"}, 3000)
        _put(client, COURSES, {"lose": "local"}, 1000)
        assert client.get("/api/campus/state").json()["items"][0]["payload"] == {"keep": "cloud"}


class TestValidation:
    def test_unknown_tool_rejected(self, client):
        assert _put(client, "campus.unknown.v1", {"x": 1}, 1000).status_code == 400

    def test_pomodoro_explicitly_refused(self, client):
        """番茄钟不是"忘了加白名单"，是刻意拒绝 —— 文案要能让人看懂原因。"""
        res = _put(client, "campus.pomodoro.sessions.v1", {"x": 1}, 1000)
        assert res.status_code == 400
        assert "番茄钟" in res.json()["detail"]

    def test_payload_must_be_object_or_array(self, client):
        assert _put(client, COURSES, "a string", 1000).status_code == 400

    def test_updated_at_must_be_positive_int(self, client):
        assert _put(client, COURSES, {"a": 1}, 0).status_code == 400
        assert _put(client, COURSES, {"a": 1}, -5).status_code == 400

    def test_missing_updated_at_rejected(self, client):
        res = client.put(f"/api/campus/state/{COURSES}", json={"payload": {"a": 1}})
        assert res.status_code == 400


class TestClockSkew:
    """客户端时钟不可信，但也不能让一台坏表把某一格永久锁死。"""

    def test_future_timestamp_is_clamped(self, db, monkeypatch):
        base = 1_800_000_000
        monkeypatch.setattr(timeutil, "now_ts", lambda: base)
        res = campus_store.put("u1", COURSES, {"a": 1}, (base + 3600) * 1000)
        assert res["ok"] is True
        assert res["updated_at"] == (base + 60) * 1000

    def test_after_clock_catches_up_writes_work_again(self, db, monkeypatch):
        base = 1_800_000_000
        monkeypatch.setattr(timeutil, "now_ts", lambda: base)
        campus_store.put("u1", COURSES, {"old": 1}, (base + 3600) * 1000)
        # 两分钟后，正常设备的 now 已经追过被夹过的水位
        monkeypatch.setattr(timeutil, "now_ts", lambda: base + 120)
        res = campus_store.put("u1", COURSES, {"new": 2}, (base + 120) * 1000)
        assert res["ok"] is True
        assert campus_store.get("u1", COURSES)["payload"] == {"new": 2}

    def test_legacy_poisoned_row_can_be_overwritten(self, db, monkeypatch):
        """修复前写进去的 2099 时间戳，不能让这一格永远 409。"""
        base = 1_800_000_000
        monkeypatch.setattr(timeutil, "now_ts", lambda: base)
        campus_store.ensure()
        with session._conn() as conn:
            conn.execute(
                "INSERT INTO campus_state (owner_id, tool, payload_json, updated_at) "
                "VALUES (?,?,?,?)",
                ("u1", COURSES, '{"bad": true}', 4_102_444_800_000),
            )
        res = campus_store.put("u1", COURSES, {"good": True}, (base + 1) * 1000)
        assert res["ok"] is True
        assert campus_store.get("u1", COURSES)["payload"] == {"good": True}


class TestIsolation:
    def test_other_owner_data_invisible(self, client, db):
        _put(client, COURSES, {"mine": 1}, 1000)
        main.app.dependency_overrides[get_current_user] = lambda: "u2"
        assert client.get("/api/campus/state").json()["items"] == []

    def test_other_owner_put_does_not_touch_mine(self, client, db):
        _put(client, COURSES, {"mine": 1}, 1000)
        main.app.dependency_overrides[get_current_user] = lambda: "u2"
        assert _put(client, COURSES, {"theirs": 2}, 999).status_code == 200  # 不撞 u1 的 1000
        main.app.dependency_overrides[get_current_user] = lambda: "u1"
        assert client.get("/api/campus/state").json()["items"][0]["payload"] == {"mine": 1}


class TestSnapshot:
    def test_load_snapshot_splits_and_joins(self, db):
        campus_store.put("u1", COURSES, {"courses": [1, 2]}, 1000)
        campus_store.put("u1", "campus.schedule.term.v1", {"start": "2026-09-07"}, 1000)
        snap = campus_store.load_snapshot("u1")
        assert snap["courses"] == {"courses": [1, 2]}
        assert snap["term"] == {"start": "2026-09-07"}

    def test_load_snapshot_missing_is_none(self, db):
        snap = campus_store.load_snapshot("nobody")
        assert snap["courses"] is None and snap["term"] is None
