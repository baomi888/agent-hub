"""设备级 vs 账号级：设置页那三个状态里错一个，用户就会以为「提醒发到别的设备」。

**真实事故**（2026-11-08）：手机上开了推送，电脑上那张卡却显示"已开启"，
并给出「发一条测试 / 关掉」两个按钮——两个都不做本机的事：

  · 点测试 → 服务端按账号发给所有设备 → 通知弹在手机上，用户以为"发错了设备"；
  · 点关掉 → 本机 `getSubscription()` 是 null，`disablePush` 直接 return true，
    什么都没删，界面却变成"打开提醒"（更假）。

根因就一句：拿**账号级**的 `subscribed`（任意一台订阅了就是 true）渲染
**本机**状态。修法是把设备列表下发、前端用 `ep_key` 认出本机。这里锁住四条：

1. `endpoint_key` 的算法（必须和前端 `crypto.subtle` 算出同一个值）；
2. 设备列表**不泄露** endpoint 与 p256dh/auth（拿到就能往那台设备推伪造通知）；
3. `remove_by_id` 只能删自己名下的（猜中别人 sub_id 也不行）；
4. 停用的设备仍在列表里（用户问"我手机怎么不响了"时要看得见"已失效"）。
"""

import hashlib
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="baomi_push_dev_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session as session  # noqa: E402

# ---- 必须在 import main 之前把建表落到临时库（理由同 test_account_profile）----
_ORIG_DB_PATH = session._db_path
session._db_path = lambda: os.path.join(_TMP, "boot.db")
session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import core.config as config  # noqa: E402
import main  # noqa: E402
from core import push_store  # noqa: E402
from core.auth import get_current_user  # noqa: E402

# 导入期结束，把全局路径还给别的模块（每个用例自己切库）
session._db_path = _ORIG_DB_PATH

_KEY = "B" * 87  # 真实 p256dh 约 87 字符，auth 约 22


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """每个用例一个独立库。`_READY` 按路径缓存，不清的话第二个用例拿到空壳库。"""
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    push_store._READY.clear()
    session._init_db()
    yield path
    push_store._READY.clear()


def _add(owner: str, ep: str, platform: str = "ios", ua: str = "") -> str:
    res = push_store.upsert(owner, ep, _KEY, "a" * 22, ua=ua or f"UA-{platform}", platform=platform)
    return res["sub_id"]


# ==================== 指纹 ====================


class TestEndpointKey:
    def test_matches_frontend_algorithm(self):
        """前端 `lib/push.ts#endpointKey` 用 crypto.subtle 算 SHA-256 取前 12 位 hex。

        两边一旦不一致，`findMyDevice()` 永远返回 null → 界面又退回
        "账号级冒充本机"的糊涂状态，而且**没有任何报错**。所以这里钉死算法。
        """
        ep = "https://fcm.googleapis.com/wp/abc123"
        expect = hashlib.sha256(ep.encode("utf-8")).hexdigest()[:12]
        assert push_store.endpoint_key(ep) == expect
        assert len(expect) == 12
        assert expect == expect.lower(), "前端用 toString(16) 生成，必须是小写"

    def test_different_endpoints_differ(self):
        a = push_store.endpoint_key("https://a.example/1")
        b = push_store.endpoint_key("https://a.example/2")
        assert a != b

    def test_stable(self):
        ep = "https://wns2.example/xyz"
        assert push_store.endpoint_key(ep) == push_store.endpoint_key(ep)


# ==================== 设备列表 ====================


class TestListForOwner:
    def test_does_not_leak_secrets(self, db):
        """★ 设置页只需要"认得出自己"，不需要投递地址。

        下发 endpoint 等于把"往这台设备推任意消息"的地址交出去；
        p256dh/auth 更不用说。只给 sha256 前 12 位，够比对、不可逆。
        """
        _add("u1", "https://x.example/1")
        rows = push_store.list_for_owner("u1")
        assert rows, "至少应当有一行"
        for bad in ("endpoint", "p256dh", "auth"):
            assert bad not in rows[0], f"设备列表泄露了 {bad}"

    def test_only_own_rows(self, db):
        _add("u1", "https://x.example/1", platform="ios")
        _add("u2", "https://x.example/2", platform="desktop")
        rows = push_store.list_for_owner("u1")
        assert len(rows) == 1
        assert rows[0]["platform"] == "ios"

    def test_includes_disabled_rows_but_count_excludes_them(self, db):
        """停用（410）的设备要留在列表里给用户看，但不该算进"几台设备"。

        删掉就查不出"我手机怎么不响了"，界面上它会凭空消失。
        """
        sub_id = _add("u1", "https://x.example/1")
        push_store.mark_fail(sub_id, "410 Gone", gone=True)

        rows = push_store.list_for_owner("u1")
        assert len(rows) == 1
        assert rows[0]["disabled"] is True
        assert push_store.count_active("u1") == 0

    def test_ep_key_matches_endpoint(self, db):
        ep = "https://x.example/abc"
        _add("u1", ep)
        assert push_store.list_for_owner("u1")[0]["ep_key"] == push_store.endpoint_key(ep)

    def test_multi_device_ordered_by_created(self, db):
        _add("u1", "https://x.example/1", platform="ios")
        _add("u1", "https://x.example/2", platform="desktop")
        rows = push_store.list_for_owner("u1")
        assert [r["platform"] for r in rows] == ["ios", "desktop"]


# ==================== 移除 ====================


class TestRemoveById:
    def test_cannot_remove_someone_elses(self, db):
        """服务端会按 sub_id 删行，所以归属校验必须在 SQL 里。

        泄漏的只是 12 位指纹和 sub_id，但"猜中别人 sub_id 就能删掉他设备"
        是能让人再也收不到提醒的越权，不值得赌。
        """
        other = _add("u2", "https://x.example/2")
        assert push_store.remove_by_id("u1", other) is False
        assert len(push_store.list_for_owner("u2")) == 1

    def test_removes_own_device(self, db):
        sub_id = _add("u1", "https://x.example/1")
        assert push_store.remove_by_id("u1", sub_id) is True
        assert push_store.list_for_owner("u1") == []

    def test_removes_only_the_named_one(self, db):
        a = _add("u1", "https://x.example/1", platform="ios")
        _add("u1", "https://x.example/2", platform="desktop")
        assert push_store.remove_by_id("u1", a) is True
        rows = push_store.list_for_owner("u1")
        assert [r["platform"] for r in rows] == ["desktop"]


# ==================== HTTP 层 ====================


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(config, "PUSH_ENABLED", True)
    monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", "pub-key")
    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", "priv-key")
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


class TestStatusApi:
    def test_status_reports_device_list(self, client):
        """前端 `findMyDevice()` 全靠这个字段认本机；缺了它只能退回账号级。"""
        _add("u1", "https://x.example/1", platform="ios")
        body = client.get("/api/push/status").json()
        assert body["supported"] is True
        assert body["devices"] == 1
        assert len(body["device_list"]) == 1
        d = body["device_list"][0]
        assert d["ep_key"] == push_store.endpoint_key("https://x.example/1")
        assert d["platform"] == "ios"
        assert d["disabled"] is False
        assert "endpoint" not in d

    def test_status_no_device(self, client):
        body = client.get("/api/push/status").json()
        assert body["subscribed"] is False
        assert body["devices"] == 0
        assert body["device_list"] == []

    def test_status_hides_list_when_push_disabled(self, client, monkeypatch):
        """服务端没配密钥时不该给列表——前端会当成"不支持推送"整块隐藏。"""
        monkeypatch.setattr(config, "PUSH_ENABLED", False)
        _add("u1", "https://x.example/1")
        body = client.get("/api/push/status").json()
        assert body["supported"] is False
        assert body["device_list"] == []
        assert body["key"] == ""


class TestRemoveApi:
    def test_remove_own(self, client):
        sub_id = _add("u1", "https://x.example/1")
        body = client.post("/api/push/remove", json={"id": sub_id}).json()
        assert body["removed"] is True
        assert body["devices"] == 0

    def test_remove_other_user(self, client):
        """HTTP 层也要挡住：别人的 id 删不掉，返回 removed=false 而不是 500。"""
        other = _add("u2", "https://x.example/2")
        body = client.post("/api/push/remove", json={"id": other}).json()
        assert body["removed"] is False
        assert len(push_store.list_for_owner("u2")) == 1

    def test_remove_requires_id(self, client):
        assert client.post("/api/push/remove", json={}).status_code == 400
        assert client.post("/api/push/remove", json={"id": "   "}).status_code == 400


# ==================== 发送明细 ====================


class TestSendDetailed:
    """`sent=2` 只说明推送服务方收下了两条请求，不代表两台系统都弹了通知。

    ★ 真实事故（2026-11-08）：手机看到了、电脑没看到，光看计数无法判断是
      服务端没发还是 Windows 把通知吞了。明细带 sub_id，设置页才能点名。
    """

    def _ready(self, monkeypatch):
        monkeypatch.setattr(config, "PUSH_ENABLED", True)
        monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", "k")
        monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", "k")

    def test_items_per_device(self, db, monkeypatch):
        from service import push as push_svc

        self._ready(monkeypatch)
        _add("u1", "https://x.example/1", platform="ios")
        _add("u1", "https://x.example/2", platform="desktop")

        def fake_send(sub, payload):
            ok = sub["platform"] == "ios"
            return ok, (None if ok else "500 boom"), False

        monkeypatch.setattr(push_svc, "_send_one", fake_send)
        res = push_svc.send_detailed("u1", {})

        assert res["sent"] == 1 and res["failed"] == 1
        assert len(res["items"]) == 2
        assert [i["ok"] for i in res["items"]] == [True, False]
        assert res["items"][0]["err"] is None
        assert res["items"][1]["err"] == "500 boom"
        # sub_id 要能对回 device_list，前端才点得出设备名
        ids = {d["id"] for d in push_store.list_for_owner("u1")}
        assert {i["sub_id"] for i in res["items"]} == ids

    def test_send_to_user_shapes_stay_compatible(self, db, monkeypatch):
        """`send_to_user` 的返回仍是那四个整数字段 —— worker 的统计口径别被改坏。"""
        from service import push as push_svc

        self._ready(monkeypatch)
        _add("u1", "https://x.example/1")
        monkeypatch.setattr(push_svc, "_send_one", lambda sub, payload: (True, None, False))
        res = push_svc.send_to_user("u1", {})
        assert set(res) == {"sent", "failed", "gone", "skipped"}
        assert res["sent"] == 1


# ==================== 自测限流 ====================


class TestTestQuota:
    def test_exhausted_message_is_human(self, client, monkeypatch):
        """点满后必须是「今天的测试次数用完了」，而不是全局处理器拼的
        「pushtest已达上限（10），明天再试或联系管理员调整配额」——
        英文 kind 泄漏给用户，而且"联系管理员"对一个自测按钮纯属废话。
        """
        from api import push as push_api
        from service import push as push_svc

        monkeypatch.setattr(
            push_svc,
            "send_detailed",
            lambda uid, payload: {"sent": 0, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )

        for _ in range(push_api._TEST_PER_DAY):
            assert client.post("/api/push/test", json={}).status_code == 200

        r = client.post("/api/push/test", json={})
        assert r.status_code == 429
        detail = r.json()["detail"]
        assert "测试次数用完" in detail
        assert "pushtest" not in detail, "英文 kind 不该出现在用户看到的文案里"
        assert "真提醒照常发" in detail, "要说清真提醒不受影响，否则用户以为功能坏了"

    def test_allowance_is_not_too_tight(self):
        """5 次太低：调试三端时几下就用完，然后看到"发送失败"以为是坏了。"""
        from api import push as push_api

        assert push_api._TEST_PER_DAY >= 10

    def test_test_returns_items(self, client, monkeypatch):
        from service import push as push_svc

        monkeypatch.setattr(
            push_svc,
            "send_detailed",
            lambda uid, payload: {
                "sent": 1,
                "failed": 1,
                "gone": 0,
                "skipped": 0,
                "items": [
                    {"sub_id": "aaa", "ok": True, "gone": False, "err": None},
                    {"sub_id": "bbb", "ok": False, "gone": False, "err": "500"},
                ],
            },
        )
        body = client.post("/api/push/test", json={}).json()
        assert body["ok"] is True
        assert body["sent"] == 1
        assert [i["ok"] for i in body["items"]] == [True, False]

    def test_test_payload_forces_notification(self, client, monkeypatch):
        """测试推送必须带 force，否则 SW 会在"用户正盯着页面"时把它压掉。

        用户报的就是这个：在电脑上当着设置页点「发一条测试」，手机上响了、
        电脑上什么都不出现，通知中心也没有——因为 SW 判断"人就在站里"，
        只 postMessage 给页面，而页面根本没监听这个事件。见 sw.js。
        """
        from service import push as push_svc

        seen = {}

        def fake(uid, payload):
            seen.update(payload)
            return {"sent": 1, "failed": 0, "gone": 0, "skipped": 0, "items": []}

        monkeypatch.setattr(push_svc, "send_detailed", fake)
        client.post("/api/push/test", json={})
        assert seen.get("force") is True, "测试推送必须带 force=True"
        assert seen.get("tag") == "test"

