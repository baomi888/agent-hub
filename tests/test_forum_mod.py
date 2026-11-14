"""先发后审 + 免登录身份（2026-11-14）。

锁的都是"漏了不报错、只会静默坏"的事：

  1. **匿名身份**：没登录的请求要拿到一枚 `baomi_anon`，昵称是「玉米NNNN」；
     同一个 cookie 两次请求是同一个 uid（否则删不掉自己刚发的帖）。
  2. **登录优先**：登录着的人发帖必须署真名，不能被浏览器里残留的匿名
     cookie 顶掉 —— 这个错的症状是"我明明登录了，帖子却叫玉米4217"。
  3. **下架即消失**：列表 / 板块计数 / 统计 / 热榜都看不到。少一处，
     管理员点了下架，内容还挂在首页，等于没删。
  4. **原因只给作者**：别人打开原链接只看到"已下架"，拿不到原因。
  5. **待审队列**：只出没看过的；通过或下架之后不再出现。
  6. **管理接口**：没登录 401、不是管理员 403。
  7. **降权窗口**：新帖 30 分钟内不进热榜 —— 先发后审的配套，
     少了它脏内容在被删之前已经拿到推荐位。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from core import account, config, forum, kb_board, quota, session
from core.auth import create_token

PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 干净进程内状态（不碰真实 data/sessions.db）。"""
    path = str(tmp_path / "forum_mod.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._ensure_rate_table()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["devadmin"])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    # 建表是"按库路径记已初始化"的：换库必须清掉，否则临时库里没有 kb_posts
    kb_board._READY.clear()
    monkeypatch.setattr(kb_board, "COOLDOWN", 0)
    yield path
    kb_board._READY.clear()


@pytest.fixture()
def app():
    import main

    return main.app


def _as(client: TestClient, username: str) -> str:
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def _topic(client: TestClient, title: str, body: str = "正文", cat: str = "team") -> dict:
    r = client.post(
        "/api/forum/topics", json={"title": title, "body": body, "cat": cat}
    )
    assert r.status_code == 200, r.text
    return r.json()["topic"]


# ==================== ① 匿名身份 ====================


def test_anonymous_visitor_gets_a_cookie_and_a_stable_uid(env, app):
    """没登录也能打开论坛，并且拿到一枚设备 cookie。"""
    with TestClient(app) as c:
        r = c.get("/api/forum/cats")
        assert r.status_code == 200, r.text
        token = r.cookies.get("baomi_anon")
        assert token and re.fullmatch(r"[0-9a-f]{32}", token)

        # 同一个 client 再请求一次：uid 不能变（cookie 复用），
        # 否则"我发的帖"每次刷新都是空的。
        first = _topic(c, "四楼靠窗整排空着")["author"]["name"]
        second = c.get("/api/forum/mine").json()["posts"]
        assert len(second) == 1
        assert second[0]["author"]["name"] == first


def test_anonymous_author_is_signed_corn_plus_digits(env, app):
    with TestClient(app) as c:
        name = _topic(c, "谁有多的充电宝")["author"]["name"]
    assert re.fullmatch(r"玉米\d{4}", name), name


def test_two_devices_get_different_names(env, app):
    """两台设备不能撞同一个昵称 —— 撞了就等于匿名失效。"""
    with TestClient(app) as a, TestClient(app) as b:
        na = _topic(a, "设备 A 的帖")["author"]["name"]
        nb = _topic(b, "设备 B 的帖")["author"]["name"]
    assert na != nb


def test_logged_in_user_still_signs_real_name(env, app):
    """登录优先：不能被匿名 cookie 顶掉。"""
    with TestClient(app) as c:
        _as(c, "alice")
        assert _topic(c, "登录用户发的")["author"]["name"] == "alice"


# ==================== ② 下架即消失 ====================


def test_removed_topic_disappears_from_every_reader_facing_number(env, app):
    with TestClient(app) as c:
        pid = _topic(c, "出个闲置台灯", cat="ask")["id"]
        assert len(c.get("/api/forum/topics").json()["posts"]) == 1

        forum.review(pid, "remove", "devadmin", "ad")

        assert c.get("/api/forum/topics").json()["posts"] == []
        assert c.get(f"/api/forum/topics/{pid}").json()["removed"] is True
        cats = c.get("/api/forum/cats").json()
        assert cats["total"] == 0
        assert all(x["n"] == 0 for x in cats["cats"])
        assert cats["today"] == 0


def test_mine_still_lists_the_removed_one(env, app):
    """「我发的帖」要留着它 —— 那是作者唯一能看到原因的地方。"""
    with TestClient(app) as c:
        pid = _topic(c, "出个闲置台灯", cat="ask")["id"]
        forum.review(pid, "remove", "devadmin", "ad")
        d = c.get("/api/forum/mine").json()
        assert [p["id"] for p in d["posts"]] == [pid]
        assert d["posts"][0]["status"] == "removed"
        assert d["removed_n"] == 1


# ==================== ③ 原因只给作者 ====================


def test_removed_reason_is_visible_to_author_only(env, app):
    with TestClient(app) as author, TestClient(app) as stranger:
        pid = _topic(author, "出个闲置台灯", cat="ask")["id"]
        forum.review(pid, "remove", "devadmin", "ad")

        mine = author.get(f"/api/forum/topics/{pid}").json()
        other = stranger.get(f"/api/forum/topics/{pid}").json()

    assert mine["removed"] and other["removed"]
    assert mine["reason"] == forum.REMOVE_LABEL["ad"]
    assert other["reason"] == ""
    assert mine["can_appeal"] is True
    assert other["can_appeal"] is False
    # 下架后不能回复：前端据此收掉输入框，而不是让人写完再报错
    assert mine["max_reply"] == 0


def test_removed_detail_hides_the_body(env, app):
    with TestClient(app) as c:
        pid = _topic(c, "出个闲置台灯", body="加微信 138", cat="ask")["id"]
        forum.review(pid, "remove", "devadmin", "ad")
        d = c.get(f"/api/forum/topics/{pid}").json()
    assert d["body"] == ""
    assert d["posts"] == []


# ==================== ④ 申诉 ====================


def test_appeal_only_once_and_only_for_removed(env, app):
    with TestClient(app) as c:
        pid = _topic(c, "出个闲置台灯", cat="ask")["id"]
        # 没下架时不能申诉
        assert c.post(f"/api/forum/topics/{pid}/appeal").status_code == 400
        forum.review(pid, "remove", "devadmin", "ad")
        assert c.post(f"/api/forum/topics/{pid}/appeal").status_code == 200
        assert c.post(f"/api/forum/topics/{pid}/appeal").status_code == 400


def test_stranger_cannot_appeal_someone_elses_topic(env, app):
    with TestClient(app) as author, TestClient(app) as stranger:
        pid = _topic(author, "出个闲置台灯", cat="ask")["id"]
        forum.review(pid, "remove", "devadmin", "ad")
        assert stranger.post(f"/api/forum/topics/{pid}/appeal").status_code == 400


# ==================== ⑤ 待审队列 ====================


def test_queue_only_shows_unreviewed(env, app):
    with TestClient(app) as c:
        p1 = _topic(c, "第一条")["id"]
        p2 = _topic(c, "第二条")["id"]
        assert {r["id"] for r in forum.review_queue()} == {p1, p2}

        forum.review(p1, "approve", "devadmin")
        assert [r["id"] for r in forum.review_queue()] == [p2]

        forum.review(p2, "remove", "devadmin", "ad")
        assert forum.review_queue() == []


def test_approved_topic_stays_visible(env, app):
    """"通过"只记账，不能顺带把帖改坏 —— 通过之后它必须还在列表里。"""
    with TestClient(app) as c:
        pid = _topic(c, "四楼靠窗空着")["id"]
        forum.review(pid, "approve", "devadmin")
        assert [p["id"] for p in c.get("/api/forum/topics").json()["posts"]] == [pid]
        assert c.get(f"/api/forum/topics/{pid}").json()["removed"] is False


def test_restore_puts_it_back(env, app):
    with TestClient(app) as c:
        pid = _topic(c, "四楼靠窗空着")["id"]
        forum.review(pid, "remove", "devadmin", "ad")
        assert c.get("/api/forum/topics").json()["posts"] == []
        forum.review(pid, "restore", "devadmin")
        assert [p["id"] for p in c.get("/api/forum/topics").json()["posts"]] == [pid]


def test_queue_puts_reported_and_busy_topics_first(env, app):
    """队列不是按时间排：有举报、有回复的排前面，因为它们已经在影响别人。"""
    with TestClient(app) as c:
        quiet = _topic(c, "没人搭理的")["id"]
        busy = _topic(c, "已经在聊的")["id"]
        c.post(f"/api/forum/topics/{busy}/replies", json={"body": "我也想去"})
        order = [r["id"] for r in forum.review_queue()]
    assert order.index(busy) < order.index(quiet)


def test_remove_requires_a_known_reason(env, app):
    with TestClient(app) as c:
        pid = _topic(c, "随便一条")["id"]
    with pytest.raises(ValueError):
        forum.review(pid, "remove", "devadmin", "")
    with pytest.raises(ValueError):
        forum.review(pid, "remove", "devadmin", "我编的原因")
    with pytest.raises(ValueError):
        forum.review(pid, "banish", "devadmin")


# ==================== ⑥ 管理接口门禁 ====================


def test_mod_queue_requires_login(env, app):
    with TestClient(app) as c:
        assert c.get("/api/forum/mod/queue").status_code == 401


def test_mod_queue_rejects_non_admin(env, app):
    with TestClient(app) as c:
        _as(c, "alice")
        assert c.get("/api/forum/mod/queue").status_code == 403


def test_admin_can_read_queue_and_remove(env, app):
    with TestClient(app) as user:
        pid = _topic(user, "出个闲置台灯", cat="ask")["id"]
    with TestClient(app) as admin:
        _as(admin, "devadmin")
        q = admin.get("/api/forum/mod/queue").json()
        assert q["stats"]["pending"] == 1
        assert [p["id"] for p in q["pending"]] == [pid]
        assert {r["key"] for r in q["reasons"]} == set(forum.REMOVE_KEYS)

        r = admin.post(f"/api/forum/mod/{pid}", json={"action": "remove", "reason": "ad"})
        assert r.status_code == 200, r.text
        assert r.json()["topic"]["status"] == "removed"
        assert admin.get("/api/forum/mod/queue").json()["stats"]["pending"] == 0


def test_mod_rejects_unknown_action(env, app):
    with TestClient(app) as user:
        pid = _topic(user, "随便一条")["id"]
    with TestClient(app) as admin:
        _as(admin, "devadmin")
        r = admin.post(f"/api/forum/mod/{pid}", json={"action": "explode"})
        assert r.status_code == 400


# ==================== ⑦ 降权窗口 ====================


def _age(pid: int, seconds: int) -> None:
    """把一条帖的 created_at 往前拨，造"已经过了降权窗口"的样本。

    等 30 分钟不可能，用 sleep 会让这套测试跑得像蜗牛；而"把窗口设成 0"
    又会把要测的东西一起关掉。
    """
    import time

    with session._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE kb_posts SET created_at = ? WHERE id = ?",
            (int(time.time()) - seconds, pid),
        )


def test_hot_puts_brand_new_topic_at_the_back(env, app):
    """新帖排末尾、不占曝光位 —— 先发后审的配套。

    注意是"排末尾"不是"踢出去"：踢出去会让刚起步的站热榜全空，
    看起来像排行榜坏了。这条同时钉住顺序和"仍然在榜上"。
    """
    with TestClient(app) as c:
        old = _topic(c, "半小时前发的")["id"]
        _age(old, forum.COOL_SECONDS + 60)
        fresh = _topic(c, "刚发出来的")["id"]
        # 给新帖刷两条回复：如果只按回复数排，它就该排第一
        for i in range(2):
            c.post(f"/api/forum/topics/{fresh}/replies", json={"body": f"回复{i}"})
        hot = [
            p["id"]
            for p in c.get("/api/forum/topics", params={"sort": "hot"}).json()["posts"]
        ]
    assert hot == [old, fresh], "老帖必须压过新帖，哪怕新帖回复更多"


def test_hot_is_never_empty_when_everything_is_new(env, app):
    """全是新帖时热榜不能空 —— 这正是上一版"直接排除"被既有用例打回来的原因。"""
    with TestClient(app) as c:
        _topic(c, "刚发的 A")
        _topic(c, "刚发的 B")
        hot = c.get("/api/forum/topics", params={"sort": "hot"}).json()["posts"]
    assert len(hot) == 2
