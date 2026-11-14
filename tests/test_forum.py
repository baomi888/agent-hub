"""论坛中心（/forum）。

这里锁的是五件最容易静默坏掉的事：

  1. **`cat` 是论坛与留言板的分界线**。库内留言（cat 为空）绝不能出现在
     广场列表里 —— 一旦漏了 `cat != ''`，首页会瞬间被"说一句"型留言淹没，
     而接口不会报任何错。
  2. **板块白名单**。前端把板块拼错一个字，落库之后那条帖就永久查不到
     （按板块筛永远筛不出来），所以非法 key 必须在写库之前拒掉。
  3. **两级楼**。回复回复仍然挂在同一个顶层帖下，`reply_to_id` 只负责"回复 @谁"。
     做成无限树的话手机缩进三次就没正文宽度了。
  4. **权限**：只有作者 / 所属库的库主 / 管理员能删；只有楼主能标「已解决」。
     路人觉得"这题答上了"不算数。
  5. **排序与筛选**：`hot` 按回复数、`unsolved` 过滤已解决、`q` 命中标题或正文。

不启真 chroma：库的归属与公开状态用 monkeypatch 顶掉，只测论坛自己的读写。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import account, config, forum, kb_board, quota, session
from core.auth import create_token
from kb import pipelines

KB = "kbforumtest01"
KB_OWNER = "forumkbowner"
PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 干净进程内状态（不碰真实 data/sessions.db）。"""
    path = str(tmp_path / "forum.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._ensure_rate_table()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["devadmin"])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    # 建表是"按库路径记已初始化"的：换库必须清掉，否则临时库里没有 kb_posts
    kb_board._READY.clear()
    # 默认关掉冷却，让"连发两条"的用例只测它想测的东西
    monkeypatch.setattr(kb_board, "COOLDOWN", 0)
    yield path
    kb_board._READY.clear()


@pytest.fixture()
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def kb(monkeypatch):
    """把一个公开库顶掉，不启 chroma。返回可变 state，用例可以改库主 / 公开状态。"""
    state = {"owner": KB_OWNER, "public": True}
    monkeypatch.setattr(pipelines, "owner_of", lambda k: state["owner"] if k == KB else None)
    monkeypatch.setattr(pipelines, "is_public", lambda k: bool(state["public"]) and k == KB)
    monkeypatch.setattr(pipelines, "is_official", lambda k: False)
    monkeypatch.setattr(pipelines, "name_of", lambda k: "测试课程库" if k == KB else None)
    return state


def _as(client: TestClient, username: str) -> str:
    """造用户 + 直接注入 Cookie（不走注册接口，避免依赖 ALLOW_SIGNUP 开关）。"""
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def _topic(client: TestClient, title: str, body: str = "正文", cat: str = "ask", **kw) -> dict:
    r = client.post(
        "/api/forum/topics",
        json={"title": title, "body": body, "cat": cat, **kw},
    )
    assert r.status_code == 200, r.text
    return r.json()["topic"]


def _titles(client: TestClient, **params) -> list[str]:
    r = client.get("/api/forum/topics", params=params)
    assert r.status_code == 200, r.text
    return [p["title"] for p in r.json()["posts"]]


# ==================== ① cat 是分界线 ====================


def test_board_messages_never_show_up_in_the_forum(env, client, kb):
    """库内留言不进论坛。

    这是整个设计的地基：留言是"说一句"（300 字、没有标题），搬上广场只能拿
    正文前几个字当标题。漏掉 `cat != ''` 不会报错，只会让首页变成垃圾场。
    """
    _as(client, "alice")
    r = client.post(f"/api/kb/public/{KB}/board", json={"body": "这条只是库里的留言"})
    assert r.status_code == 200, r.text

    assert _titles(client) == []

    # 同一个库，这次明确带板块 → 才进论坛
    _topic(client, "第三章中值定理那道题", kb_id=KB)
    assert _titles(client) == ["第三章中值定理那道题"]


def test_topic_without_kb_still_works(env, client, kb):
    """不挂库的纯话题（"周三晚图书馆缺两人"挂不到任何库上）。"""
    _as(client, "alice")
    t = _topic(client, "周三晚图书馆缺两人")
    assert t["kb"] is None
    assert t["cat"] == "ask"
    detail = client.get(f"/api/forum/topics/{t['id']}").json()
    assert detail["topic"]["kb"] is None


# ==================== ② 板块白名单 ====================


def test_unknown_cat_is_rejected_before_write(env, client, kb):
    """拼错板块必须 400。

    放过去的话，这条帖落库了、也能按 id 打开，但按板块筛永远筛不出来 ——
    用户会觉得"我发的帖不见了"，而日志里一条错误都没有。
    """
    _as(client, "alice")
    r = client.post(
        "/api/forum/topics",
        json={"title": "t", "body": "b", "cat": "asking"},
    )
    assert r.status_code == 400
    assert "板块" in r.json()["detail"]
    assert _titles(client) == []


def test_topic_requires_cat_and_title(env, client, kb):
    _as(client, "alice")
    assert client.post("/api/forum/topics", json={"title": "t", "body": "b"}).status_code == 400
    assert (
        client.post("/api/forum/topics", json={"body": "b", "cat": "ask"}).status_code == 400
    )
    assert (
        client.post("/api/forum/topics", json={"title": "t", "cat": "ask"}).status_code == 400
    )


def test_meta_cat_is_admin_only(env, client, kb):
    """站务公告只有管理员能发：这个板块在用户眼里是"官方说的话"。"""
    _as(client, "alice")
    r = client.post(
        "/api/forum/topics", json={"title": "公告", "body": "b", "cat": "meta"}
    )
    assert r.status_code == 403

    _as(client, "devadmin")
    r = client.post(
        "/api/forum/topics", json={"title": "公告", "body": "b", "cat": "meta"}
    )
    assert r.status_code == 200, r.text


def test_every_cat_key_is_in_the_whitelist(env):
    """CATS 与 CAT_KEYS 不能漂（手写两份的时候最容易）。"""
    assert {k for k, _, _ in forum.CATS} == set(forum.CAT_KEYS)
    assert all(label for _, label, _ in forum.CATS)


# ==================== ③ 两级楼 ====================


def test_reply_to_a_reply_stays_in_the_same_thread(env, client, kb):
    """回复"某条回复"时，新回复仍挂在同一个顶层帖下，只标「回复 @谁」。

    做成无限树的话，手机上缩进三次正文就没宽度了；而"回复某条回复"的真实
    诉求只是点名，一句 @ 就够。
    """
    _as(client, "alice")
    t = _topic(client, "楼中楼")
    r1 = client.post(f"/api/forum/topics/{t['id']}/replies", json={"body": "第一层"})
    assert r1.status_code == 200, r1.text
    kid = r1.json()["post"]["id"]

    r2 = client.post(
        f"/api/forum/topics/{t['id']}/replies", json={"body": "回第一层", "reply_to": kid}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["post"]["reply_to"]["name"] == "alice"

    detail = client.get(f"/api/forum/topics/{t['id']}").json()
    assert len(detail["posts"]) == 3  # 楼主 + 两条回复，不是三层树
    assert detail["posts"][0]["id"] == t["id"]
    assert detail["posts"][2]["reply_to"]["id"] == kid


def test_own_reply_can_be_replied_to(env, client, kb):
    """自己的楼层也能被回复 —— 论坛没有"不能回自己"的说法。"""
    _as(client, "alice")
    t = _topic(client, "自己回自己")
    r = client.post(f"/api/forum/topics/{t['id']}/replies", json={"body": "补一句"})
    assert r.status_code == 200
    # 直接回复楼主（不带 reply_to）也算成功
    r2 = client.post(f"/api/forum/topics/{t['id']}/replies", json={"body": "再补一句"})
    assert r2.status_code == 200


def test_reply_to_a_non_topic_is_rejected(env, client, kb):
    """拿留言板上的留言 id 来回复论坛话题要被挡掉。"""
    _as(client, "alice")
    client.post(f"/api/kb/public/{KB}/board", json={"body": "库内留言"})
    with kb_board.store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT id FROM kb_posts ORDER BY id DESC LIMIT 1").fetchone()
    board_id = int(row[0])

    t = _topic(client, "论坛话题")
    r = client.post(
        f"/api/forum/topics/{t['id']}/replies", json={"body": "串楼", "reply_to": board_id}
    )
    assert r.status_code == 400


def test_reply_target_must_belong_to_this_thread(env, client, kb):
    """不能拿隔壁楼的回复 id 往这栋楼里回。

    同属一个库时 `add_post` 的"同一个 kb_id"校验是过的，于是新的那条会被挂到
    隔壁楼下面 —— 用户在自己的帖子里永远看不到自己的回复，而日志一条错都没有。
    """
    _as(client, "alice")
    t1 = _topic(client, "第一栋楼")
    t2 = _topic(client, "第二栋楼")
    kid = client.post(
        f"/api/forum/topics/{t2['id']}/replies", json={"body": "隔壁楼的回复"}
    ).json()["post"]["id"]

    r = client.post(
        f"/api/forum/topics/{t1['id']}/replies", json={"body": "串楼", "reply_to": kid}
    )
    assert r.status_code == 400
    assert "这栋楼" in r.json()["detail"]


# ==================== ④ 权限 ====================


def test_only_author_or_owner_can_delete(env, client, kb):
    _as(client, "alice")
    t = _topic(client, "alice 的帖", kb_id=KB)

    _as(client, "bob")
    assert client.delete(f"/api/forum/topics/{t['id']}").status_code == 403

    # 库主删得掉（和留言板同一条规则，不然会出现"库主能在留言板删、在论坛删不掉"）
    #
    # ★ 库主的 uid 是随机 id，不是用户名 —— 直接拿用户名当 uid 比，库主本人
    #   会被自己的权限挡在 403 外面。留言板那批测试就踩过同一个坑，这里先把
    #   账号建出来、取到真 uid 再喂给 owner_of。
    kb["owner"] = _as(client, KB_OWNER)
    assert client.delete(f"/api/forum/topics/{t['id']}").status_code == 200
    assert _titles(client) == []


def test_only_author_can_mark_solved(env, client, kb):
    _as(client, "alice")
    t = _topic(client, "谁的帖")

    _as(client, "bob")
    r = client.patch(f"/api/forum/topics/{t['id']}", json={"solved": True})
    assert r.status_code == 403

    _as(client, "alice")
    r = client.patch(f"/api/forum/topics/{t['id']}", json={"solved": True})
    assert r.status_code == 200
    assert client.get(f"/api/forum/topics/{t['id']}").json()["topic"]["solved"] is True


def test_deleting_a_reply_keeps_the_thread(env, client, kb):
    """删单条回复不能把整栋楼带走 —— 接口同时支持两种粒度。"""
    _as(client, "alice")
    t = _topic(client, "整栋楼")
    kid = client.post(f"/api/forum/topics/{t['id']}/replies", json={"body": "一条回复"}).json()[
        "post"
    ]["id"]

    r = client.delete(f"/api/forum/topics/{kid}")
    assert r.status_code == 200
    assert r.json()["root_id"] == t["id"]

    detail = client.get(f"/api/forum/topics/{t['id']}").json()
    assert len(detail["posts"]) == 1  # 只剩楼主
    assert detail["topic"]["reply_n"] == 0


# ==================== ⑤ 排序 / 筛选 / 搜索 ====================


def test_solved_topic_leaves_the_unsolved_feed(env, client, kb):
    _as(client, "alice")
    a = _topic(client, "已经解决的那题")
    _topic(client, "还没解决的那题")

    client.patch(f"/api/forum/topics/{a['id']}", json={"solved": True})
    assert _titles(client, sort="unsolved") == ["还没解决的那题"]
    assert set(_titles(client)) == {"已经解决的那题", "还没解决的那题"}


def test_hot_sorts_by_reply_count(env, client, kb):
    _as(client, "alice")
    cold = _topic(client, "没人理的")
    hot = _topic(client, "有人接话的")
    for i in range(2):
        client.post(f"/api/forum/topics/{hot['id']}/replies", json={"body": f"回复{i}"})
    # 让"没人理的"更新一点，证明 hot 不是顺带按时间排的
    _topic(client, "更晚发的但没人理")
    titles = _titles(client, sort="hot")
    assert titles[0] == "有人接话的"
    assert cold["title"] not in titles[:1]


def test_search_hits_title_or_body(env, client, kb):
    _as(client, "alice")
    _topic(client, "拉格朗日中值定理", body="完全无关的一段话")
    _topic(client, "随便一个标题", body="正文里提到泰勒展开")

    assert _titles(client, q="拉格朗日") == ["拉格朗日中值定理"]
    assert _titles(client, q="泰勒") == ["随便一个标题"]
    assert _titles(client, q="不存在的词") == []


def test_cat_filter(env, client, kb):
    _as(client, "alice")
    _topic(client, "问问题", cat="ask")
    _topic(client, "求真题", cat="res")
    assert _titles(client, cat="res") == ["求真题"]
    assert _titles(client, cat="ask") == ["问问题"]


def test_has_more_only_for_time_order(env, client, kb):
    """hot / unsolved 不假装能翻页（分数排序下 id 游标会失效）。"""
    _as(client, "alice")
    for i in range(3):
        _topic(client, f"话题{i}")

    r = client.get("/api/forum/topics", params={"limit": 2}).json()
    assert len(r["posts"]) == 2 and r["has_more"] is True
    last = r["posts"][-1]["id"]
    r2 = client.get("/api/forum/topics", params={"limit": 2, "before": last}).json()
    assert len(r2["posts"]) == 1 and r2["has_more"] is False

    hot = client.get("/api/forum/topics", params={"sort": "hot", "limit": 2}).json()
    assert hot["has_more"] is False


# ==================== 兜底逻辑 ====================


def test_title_falls_back_to_body_when_missing(env, client, kb):
    """老数据 / 直接写库的帖子没有标题时，列表不能出现空白行。

    走 API 是进不来的（POST 要求 title），所以这里直接落一行验证展示兜底。
    """
    _as(client, "alice")
    kb_board.add_post("", "alice", "这是一段没有标题的正文，前二十四个字会被拿来当标题用", cat="ask")
    titles = _titles(client)
    assert titles and titles[0].startswith("这是一段没有标题的正文")
    assert len(titles[0]) <= 24


def test_cat_counts_and_stats(env, client, kb):
    _as(client, "alice")
    _topic(client, "a", cat="ask")
    _topic(client, "b", cat="ask")
    _topic(client, "c", cat="res")
    client.post(
        f"/api/forum/topics/{_topic(client, 'd', cat='ask')['id']}/replies", json={"body": "r"}
    )
    d = client.get("/api/forum/cats").json()
    by = {c["key"]: c["n"] for c in d["cats"]}
    assert by["ask"] == 3 and by["res"] == 1 and by["exp"] == 0
    assert d["total"] == 4
    assert d["replies"] == 1
    # 站务公告对普通用户不可发
    assert {c["key"]: c["postable"] for c in d["cats"]}["meta"] is False


def test_private_kb_cannot_be_used_as_source(env, client, kb, monkeypatch):
    """收回公开之后不该还能往它的讨论区里发东西；私有库 id 也不该被验证存在。"""
    _as(client, "alice")
    monkeypatch.setattr(pipelines, "is_public", lambda k: False)
    r = client.post(
        "/api/forum/topics", json={"title": "t", "body": "b", "cat": "ask", "kb_id": KB}
    )
    assert r.status_code == 404
