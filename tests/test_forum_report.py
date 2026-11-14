"""论坛举报 + 列表「楼中楼」预览（2026-11-14）。

举报锁的是几件「接口通了但语义不对」的事：

  1. **一台设备对一条帖只算一次**。免登录论坛没有账号成本，不挡就是刷：
     一个人狂点十次如果算十次，举报数就一点意义都没有。
  2. 自己的帖不能举报、已下架的不再收举报 —— 后者是为了让管理台不再
     收到「已经被处理过的东西」的噪音。
  3. **管理员判过之后 `report_n` 清零**。不清零，「举报」tab 永远不会空，
     管理员三次之后就学会无视那个 tab 了。真的历史在 forum_reports 表里。
  4. 举报理由和**下架理由**是两套词：`offtopic`（与板块无关）只属于下架，
     读者不会去举报这个。混成一套会让两个下拉框都变得不好用。

楼中楼锁的是「预览取的是不是最后那一条**可见**回复」—— 回复被下架后
必须退到上一条，否则列表上会展示一条已经被删掉的内容。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import account, config, forum, kb_board, quota, session
from core.auth import create_token

PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "forum_report.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._ensure_rate_table()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["devadmin"])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    kb_board._READY.clear()
    monkeypatch.setattr(kb_board, "COOLDOWN", 0)
    yield path
    kb_board._READY.clear()


@pytest.fixture()
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _as(c: TestClient, username: str) -> str:
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    c.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def _topic(c: TestClient, title: str, body: str = "正文一句") -> dict:
    r = c.post(
        "/api/forum/topics", json={"title": title, "body": body, "cat": "ask"}
    )
    assert r.status_code == 200, r.text
    return r.json()["topic"]


def _reply(c: TestClient, post_id: int, body: str, reply_to: int | None = None) -> dict:
    r = c.post(
        f"/api/forum/topics/{post_id}/replies",
        json={"body": body, **({"reply_to": reply_to} if reply_to else {})},
    )
    assert r.status_code == 200, r.text
    return r.json()["post"]


def _list_post(c: TestClient, post_id: int) -> dict:
    posts = c.get("/api/forum/topics").json()["posts"]
    hit = [p for p in posts if p["id"] == post_id]
    assert hit, "列表里找不到这条帖"
    return hit[0]


# ==================== ① 一设备一条一次 ====================


def test_report_counts_once_per_device(env, client):
    _as(client, "alice")
    t = _topic(client, "群里卖资料的那条")
    # 换个人来举报：作者自己报自己的帖会被后端拒（下一条用例专门验它）
    _as(client, "bob")

    r = client.post(f"/api/forum/topics/{t['id']}/report", json={"reason": "ad"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["dup"] is False
    assert body["count"] == 1

    # 同一台设备再报一次，换个理由也不算 —— 否则狂点十次就能把一条帖顶到
    # 队列最前，而那正是「举报」这个功能最容易被滥用的地方
    r2 = client.post(f"/api/forum/topics/{t['id']}/report", json={"reason": "abuse"})
    assert r2.status_code == 200
    assert r2.json()["dup"] is True, "重复举报要标 dup"
    assert r2.json()["count"] == 1, "重复举报不能抬数字"

    assert _list_post(client, t["id"])["report_n"] == 1


def test_two_devices_count_twice(env, client):
    """不同设备各算一次 —— 三个同学都举报了，和一个人点了三次，
    是两件完全不同的事，前者才是管理台要看的信号。"""
    _as(client, "alice")
    t = _topic(client, "被两个人举报的帖")

    assert forum.report(t["id"], "device-b", "ad")["count"] == 1
    r = forum.report(t["id"], "device-c", "abuse")
    assert r["count"] == 2
    assert r["dup"] is False


def test_author_cannot_report_own_post(env, client):
    """自己举报自己等于把「举报」当成一个通知自己的按钮。"""
    uid = _as(client, "alice")
    t = _topic(client, "我自己的帖")
    assert forum.report(t["id"], uid, "ad")["ok"] is False

    r = client.post(f"/api/forum/topics/{t['id']}/report", json={"reason": "ad"})
    assert r.status_code == 400


def test_removed_post_stops_taking_reports(env, client):
    """下架之后还继续收举报 = 管理台一直在收已经处理过的东西。"""
    _as(client, "alice")
    t = _topic(client, "会被下架的帖")
    _as(client, "devadmin")
    assert (
        client.post(
            f"/api/forum/mod/{t['id']}", json={"action": "remove", "reason": "ad"}
        ).status_code
        == 200
    )
    r = client.post(f"/api/forum/topics/{t['id']}/report", json={"reason": "ad"})
    assert r.status_code == 400


def test_report_rejects_unknown_reason(env, client):
    _as(client, "alice")
    t = _topic(client, "随便一条")
    _as(client, "bob")
    r = client.post(f"/api/forum/topics/{t['id']}/report", json={"reason": "hack"})
    assert r.status_code == 400


def test_report_missing_post(env, client):
    _as(client, "alice")
    r = client.post("/api/forum/topics/999999/report", json={"reason": "ad"})
    assert r.status_code == 400


# ==================== ② 两套理由不混 ====================


def test_cats_carries_report_reasons(env, client):
    """举报理由由服务端下发，前端不硬编码一份。

    断言 `offtopic` **不在**举报里：它是下架理由（管理员才判得了），
    读者看到「与板块无关」这个选项只会困惑 —— 我凭什么判断该发哪个板块。
    """
    d = client.get("/api/forum/cats").json()
    keys = [r["key"] for r in d["report_reasons"]]
    assert "ad" in keys and "abuse" in keys and "illegal" in keys
    assert "offtopic" not in keys, "下架理由不该出现在举报选项里"
    assert all(r["label"] for r in d["report_reasons"]), "每条都要有中文 label"


# ==================== ③ 判过就清空 ====================


def test_mod_queue_lists_reported_then_clears(env, client):
    _as(client, "alice")
    t = _topic(client, "被举报后判通过的帖")
    _as(client, "bob")
    client.post(f"/api/forum/topics/{t['id']}/report", json={"reason": "ad"})

    _as(client, "devadmin")
    q = client.get("/api/forum/mod/queue").json()
    assert q["stats"]["reported"] == 1
    assert [p["id"] for p in q["reported"]] == [t["id"]]

    r = client.post(f"/api/forum/mod/{t['id']}", json={"action": "approve"})
    assert r.status_code == 200, r.text

    q2 = client.get("/api/forum/mod/queue").json()
    assert q2["stats"]["reported"] == 0, "判过之后举报 tab 要能空掉"
    assert q2["reported"] == []
    assert _list_post(client, t["id"])["report_n"] == 0, "report_n 是待处理数，不是历史累计"


def test_mod_queue_requires_admin(env, client):
    """队列里是被举报内容，普通用户不该看到。"""
    _as(client, "alice")
    assert client.get("/api/forum/mod/queue").status_code == 403


# ==================== ④ 楼中楼预览 ====================


def test_no_replies_means_no_preview(env, client):
    """没有回复时给 `null` 而不是空 body 的对象：前端判一次真假就够。"""
    _as(client, "alice")
    t = _topic(client, "还没人回的帖")
    assert _list_post(client, t["id"])["last"] is None


def test_preview_shows_last_reply(env, client):
    _as(client, "alice")
    t = _topic(client, "有回复的帖")
    _as(client, "bob")
    _reply(client, t["id"], "第一条回复")
    _reply(client, t["id"], "最后这条")

    # 切回楼主视角再查：从 bob 自己的视角看，最后一条当然是「我」回的，
    # 那样断言 mine=False 就成了测反
    _as(client, "alice")
    last = _list_post(client, t["id"])["last"]
    assert last["body"] == "最后这条"
    assert last["by"] == "bob"
    assert last["mine"] is False, "别人回的不能被标成「我」"


def test_preview_marks_my_own_reply(env, client):
    _as(client, "alice")
    t = _topic(client, "自己回自己的帖")
    _reply(client, t["id"], "补充一句")
    last = _list_post(client, t["id"])["last"]
    assert last["mine"] is True
    assert last["by"] == "alice"


def test_preview_skips_removed_reply(env, client):
    """最后一条回复被删掉 → 预览要退到上一条可见的。

    不跳过的话，列表上会展示一条**已经被删掉**的内容 —— 而详情页里
    根本不存在这句话，用户点进去会以为页面漏了东西。

    ★ 这里用「作者自己删掉」而不是「管理员下架」：`forum.review` 只认
      `root_id = 0` 的顶层帖（管理台判的是整栋楼），回复那一层走的是
      `DELETE /topics/{id}`。写测试时差点当成 bug 去改 review ——
      实际是两套动作本来就该分开。
    """
    _as(client, "alice")
    t = _topic(client, "最后一条回复会被删")
    _as(client, "bob")
    _reply(client, t["id"], "还在的那条")
    gone = _reply(client, t["id"], "这条会被删")

    assert client.delete(f"/api/forum/topics/{gone['id']}").status_code == 200

    last = _list_post(client, t["id"])["last"]
    assert last["body"] == "还在的那条", "预览展示了已删除的回复"


def test_preview_flattens_multiline_reply(env, client):
    """多行回复在列表卡里必须压成一行。

    不压的话，一条带三个换行的回复会在卡片里撑出一个大洞 ——
    而那张卡片只有一行的高度预算。
    """
    _as(client, "alice")
    t = _topic(client, "多行回复")
    _as(client, "bob")
    _reply(client, t["id"], "第一行\n\n第二行")
    last = _list_post(client, t["id"])["last"]
    assert "\n" not in last["body"]
    assert "第一行" in last["body"] and "第二行" in last["body"]
