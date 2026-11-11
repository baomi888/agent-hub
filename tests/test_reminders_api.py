"""提醒中心的 HTTP 层：路由、归属隔离、老接口兼容。

三条最容易错、错了又最难发现的：

1. **路由顺序**：`/summary` 一旦被 `/{reminder_id}` 抢走，返回的是 404 而不是概览，
   只有前端报「拿不到数据」时才会被发现；
2. **归属隔离**：猜中别人的 id 必须 404（不是 403，不泄露「存在但不属于你」）；
3. **老接口 `/api/remind`**：它现在读写的是 `source='system'` 那一行，
   但返回结构一个字段都不能变 —— 老前端是照着旧结构写的，日历服务端也在读。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

_TMP = tempfile.mkdtemp(prefix="baomi_reminders_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session as session  # noqa: E402

# 必须在 import main 之前把建表落到临时库（理由同 test_push_devices）
_ORIG_DB_PATH = session._db_path
session._db_path = lambda: os.path.join(_TMP, "boot.db")
session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import core.config as config  # noqa: E402
import main  # noqa: E402
from core import push_store, reminder_core, reminder_store  # noqa: E402
from core.auth import get_current_user  # noqa: E402

session._db_path = _ORIG_DB_PATH

_KEY = "B" * 87


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    reminder_core.reset_cache()
    reminder_store._READY.clear()
    push_store._READY.clear()
    session._init_db()
    yield path
    reminder_core.reset_cache()
    reminder_store._READY.clear()
    push_store._READY.clear()


@pytest.fixture()
def client(db):
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _as(name: str) -> None:
    """切当前登录用户（同一个 app，改依赖覆盖即可）。"""
    main.app.dependency_overrides[get_current_user] = lambda: name


def _create(client, **kwargs) -> dict:
    body = {"title": "复盘今天", "freq": "daily", "at": "22:00"}
    body.update(kwargs)
    res = client.post("/api/reminders", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ==================== CRUD ====================


class TestCrud:
    def test_create_and_read_back(self, client):
        item = _create(client)
        assert item["id"].startswith("rm_")
        assert item["source"] == "manual"
        assert item["next_fire_at"]

        got = client.get(f"/api/reminders/{item['id']}")
        assert got.status_code == 200
        assert got.json()["title"] == "复盘今天"

    def test_list_scopes(self, client):
        a = _create(client, title="今天")
        paused = _create(client, title="暂停的")
        client.patch(f"/api/reminders/{paused['id']}", json={"enabled": False})

        assert [i["id"] for i in client.get("/api/reminders?scope=all").json()["items"]].count(a["id"]) == 1
        assert [i["title"] for i in client.get("/api/reminders?scope=paused").json()["items"]] == ["暂停的"]

    def test_bad_scope(self, client):
        assert client.get("/api/reminders?scope=year").status_code == 400

    def test_patch_title(self, client):
        item = _create(client)
        res = client.patch(f"/api/reminders/{item['id']}", json={"title": "改成复盘"})
        assert res.status_code == 200
        assert res.json()["title"] == "改成复盘"

    def test_patch_empty_body(self, client):
        item = _create(client)
        assert client.patch(f"/api/reminders/{item['id']}", json={}).status_code == 400

    def test_delete(self, client):
        item = _create(client)
        assert client.delete(f"/api/reminders/{item['id']}").status_code == 200
        assert client.get(f"/api/reminders/{item['id']}").status_code == 404

    def test_validation_error_is_400_not_500(self, client):
        res = client.post("/api/reminders", json={"title": "x", "freq": "weekly", "at": "08:00"})
        assert res.status_code == 400
        assert "weekday" in res.json()["detail"]

    def test_limit_maps_to_400(self, client, monkeypatch):
        monkeypatch.setattr(reminder_core, "MAX_PER_OWNER", 1)
        _create(client, title="第一条")
        res = client.post("/api/reminders", json={"title": "第二条", "at": "08:00"})
        assert res.status_code == 400
        assert "删掉不用的" in res.json()["detail"]

    def test_override_title_visible_in_single_get(self, client):
        """override 必须在**所有**出口生效：列表里改了名、点进去还是原名，
        用户会以为刚改的没保存。这是只在列表里做 resolve 的典型漏点。"""
        item = _create(client, source="delegate", source_ref="dl_x")
        patched = client.patch(
            f"/api/reminders/{item['id']}", json={"override": {"title": "我自己的叫法"}}
        )
        assert patched.json()["title"] == "我自己的叫法"
        assert client.get(f"/api/reminders/{item['id']}").json()["title"] == "我自己的叫法"
        listed = client.get("/api/reminders").json()["items"][0]
        assert listed["title"] == "我自己的叫法"


class TestOwnership:
    def test_other_user_gets_404_everywhere(self, client):
        item = _create(client)
        _as("u2")
        assert client.get(f"/api/reminders/{item['id']}").status_code == 404
        assert client.patch(f"/api/reminders/{item['id']}", json={"title": "改"}).status_code == 404
        assert client.delete(f"/api/reminders/{item['id']}").status_code == 404
        assert client.post(f"/api/reminders/{item['id']}/snooze", json={"minutes": 5}).status_code == 404

    def test_list_is_scoped(self, client):
        _create(client, title="我的")
        _as("u2")
        assert client.get("/api/reminders").json()["items"] == []


# ==================== 概览 / 来源 / 偏好 / 流水 ====================


class TestSideEndpoints:
    def test_summary_is_not_swallowed_by_id_route(self, client):
        """路由顺序的回归断言：/summary 被 /{id} 抢走时这里是 404。"""
        _create(client)
        res = client.get("/api/reminders/summary")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert "today" in body and "paused_count" in body

    def test_sources_default_all_off(self, client):
        body = client.get("/api/reminders/sources").json()
        assert [s["key"] for s in body["sources"]] == [
            "cet",
            "checkin",
            "focus",
            "timetable",
            "deadline",
            "words",
            "delegate",
        ]
        assert all(s["enabled"] is False for s in body["sources"])

    def test_sources_toggle(self, client):
        res = client.put("/api/reminders/sources", json={"source": "timetable", "enabled": True})
        assert res.status_code == 200
        # PUT 与 GET 必须同形状（都是 list），否则前端切完开关再渲染会拿到 dict
        assert isinstance(res.json()["sources"], list)
        body = client.get("/api/reminders/sources").json()
        assert {s["key"]: s["enabled"] for s in body["sources"]}["timetable"] is True

    def test_sources_reject_unknown(self, client):
        assert client.put("/api/reminders/sources", json={"source": "wechat", "enabled": True}).status_code == 400

    def test_prefs_roundtrip(self, client):
        assert client.get("/api/reminders/prefs").json()["daily_cap"] == 6
        res = client.put("/api/reminders/prefs", json={"daily_cap": 4, "quiet_start": "22:00"})
        assert res.status_code == 200
        assert res.json()["daily_cap"] == 4

    def test_prefs_reject_bad_cap(self, client):
        assert client.put("/api/reminders/prefs", json={"daily_cap": 99}).status_code == 400

    def test_prefs_reject_non_integer_cap(self, client):
        """字符串/浮点混进来要是 400，不是 500 —— 500 会被前端当成「后端挂了」。"""
        assert client.put("/api/reminders/prefs", json={"daily_cap": "abc"}).status_code == 400

    def test_channels_accepts_list(self, client):
        """core 本来就支持数组；HTTP 层只收 str 会让第二波前端直接 422。"""
        item = _create(client, channels=["push", "inapp"])
        assert item["channels"] == "push,inapp"

    def test_fires_endpoint(self, client):
        item = _create(client)
        reminder_core.record_fire(item["id"], 1_760_000_000, reminder_core.STATUS_EMPTY, "no_content")
        body = client.get(f"/api/reminders/fires?reminder_id={item['id']}").json()
        assert body["items"][0]["status"] == "skipped_empty"

    def test_snooze(self, client):
        """推迟的是「从现在起 5 分钟」，不是「今天 22:00 之前」—— 用户点就是想在 5 分钟后看到。"""
        item = _create(client)
        res = client.post(f"/api/reminders/{item['id']}/snooze", json={"minutes": 5})
        assert res.status_code == 200
        delta = res.json()["next_fire_at"] - int(time.time())
        assert 290 <= delta <= 310

    def test_snooze_needs_integer(self, client):
        item = _create(client)
        res = client.post(f"/api/reminders/{item['id']}/snooze", json={"minutes": "soon"})
        assert res.status_code == 400


# ==================== 老接口 /api/remind 兼容 ====================


class TestLegacyRemind:
    def test_shape_unchanged(self, client):
        """字段一个都不能少：老前端照这个结构写的，少一个就是空白下拉。"""
        body = client.get("/api/remind").json()
        assert set(body) == {
            "hour",
            "minute",
            "time",
            "default_hour",
            "default_minute",
            "steps",
            "push_enabled",
        }
        assert body["steps"] == list(reminder_store.MINUTE_STEPS)

    def test_post_writes_both_stores(self, client):
        res = client.post("/api/remind", json={"hour": 7, "minute": 30})
        assert res.status_code == 200
        assert res.json()["time"] == "07:30"

        # 新表：提醒中心看到的是同一条
        assert reminder_core.system_time("u1") == (7, 30)
        # 老表：日历 ICS 还在读它
        assert reminder_store.get("u1") == (7, 30)

    def test_post_creates_system_row_for_new_user(self, client):
        reminder_core.ensure()
        with session._conn() as conn:
            before = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        assert before == 0
        client.post("/api/remind", json={"hour": 21, "minute": 0})
        item = reminder_core.get_system("u1")
        assert item is not None
        assert item["action"] == "agent_deliver"  # 内容还要走六级/打卡的口径

    def test_minute_still_snaps(self, client):
        """老接口挂着 select 下拉，放开到 1–59 会让它变成空下拉（第二波再改）。"""
        res = client.post("/api/remind", json={"hour": 7, "minute": 7})
        assert res.json()["minute"] == 0

    def test_get_does_not_create_row(self, client):
        client.get("/api/remind")
        assert reminder_core.get_system("u1") is None


# ==================== 订阅推送时补一条每日回顾 ====================


class TestSubscribeHook:
    def test_subscribe_creates_system_reminder(self, client, monkeypatch):
        """迁移只覆盖升级那一刻的老用户；之后新开推送的人必须也有一条。"""
        monkeypatch.setattr(config, "PUSH_ENABLED", True)
        monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", "pub-key")
        monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", "priv-key")
        res = client.post(
            "/api/push/subscribe",
            json={
                "endpoint": "https://fcm.googleapis.com/wp/abc123",
                "keys": {"p256dh": _KEY, "auth": "a" * 22},
            },
        )
        assert res.status_code == 200, res.text
        assert reminder_core.get_system("u1") is not None


# ==================== 一次性提醒的绝对时刻（fire_at）====================
#
# AI 整理要能表达「下周五 18:00 开会」这种**指定日期只响一次**的提醒。
# 表里（`reminders.fire_at`）和 `core` 里早就有，缺的只是 HTTP 这一层，
# 所以这一组测的是：字段进得来、过去的时间被拦住、非 once 传了也拦住。
#
# 为什么"过去的时间"必须在 HTTP 层拦：`next_fire()` 会照原样算出那个过去的时刻，
# 轻则当场响一声（用户正在睡觉），重则被 `catch_up()` 记一条 failed 流水 ——
# 而用户在界面上什么都看不到。


class TestFireAt:
    def test_once_with_fire_at_lands_on_that_moment(self, client):
        """`once + fire_at` 就是那一个时刻，不能被「几点」规则改写成别的日子。"""
        target = int(time.time()) + 3 * 3600
        item = _create(client, title="开会", freq="once", at=None, fire_at=target)
        assert item["fire_at"] == target
        assert item["next_fire_at"] == target
        assert item["freq"] == "once"

    def test_past_fire_at_is_rejected(self, client):
        res = client.post(
            "/api/reminders",
            json={"title": "开会", "freq": "once", "fire_at": int(time.time()) - 60},
        )
        assert res.status_code == 400, res.text
        assert "已经过去" in res.json()["detail"]

    def test_fire_at_with_repeating_freq_is_rejected(self, client):
        """给「每天 22:00」钉死一个日期，第二天起就再也不响，而界面上还写着每天。"""
        res = client.post(
            "/api/reminders",
            json={
                "title": "背单词",
                "freq": "daily",
                "at": "22:00",
                "fire_at": int(time.time()) + 3600,
            },
        )
        assert res.status_code == 400, res.text
        assert "仅一次" in res.json()["detail"]

    def test_patch_fire_at_moves_next_fire(self, client):
        later = int(time.time()) + 5 * 3600
        item = _create(client, title="开会", freq="once", at=None, fire_at=int(time.time()) + 3600)
        res = client.patch(f"/api/reminders/{item['id']}", json={"fire_at": later})
        assert res.status_code == 200, res.text
        assert res.json()["next_fire_at"] == later

    def test_patch_past_fire_at_is_rejected(self, client):
        item = _create(client, title="开会", freq="once", at=None, fire_at=int(time.time()) + 3600)
        res = client.patch(
            f"/api/reminders/{item['id']}", json={"fire_at": int(time.time()) - 60}
        )
        assert res.status_code == 400, res.text
        assert "已经过去" in res.json()["detail"]
