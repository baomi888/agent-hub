"""反馈会话视图的回归（2026-11-18）。

后台从"按条平铺"改成"按人会话"之后，有四处不会报错、只会悄悄错的地方，
这个文件逐个钉住：

  1. **同一个人多条反馈 = 一个会话**。GROUP BY uid 写错（比如按 username）会把
     同一人的两条拆成两个会话，看起来"有 20 个人在反馈"，实际只有 10 个。
  2. **未处理数只数 open**。把 doing 也算进红点，管理员会以为积压比实际多一倍。
  3. **对话消息必须全局按时间排**。按"一条反馈 + 它的回复"成组输出，会得到
     「A 提问 → 回 A → B 提问」，而 B 明明早于那条回复 —— 读起来像管理员在
     回答 B 时还没看到 B。这条是实测出来的，不是假想。
  4. **会话回复挂在最新一条反馈下**。挂错了，用户打开「我提过的」会看到一条
     三天前的旧反馈突然多了回复。

另有两条口径：状态积压用全量、分类分布用区间（`stats()` 的三个口径不同），
以及非管理员一律 404（与看板同一套鉴权）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from core import account, config, quota, session, user_feedback
from core.auth import create_token

ADMIN_NAME = "fbadmin"
USER_A = "fbalice"
USER_B = "fbbob"
PASSWORD = "secret123"
DAY = 86400


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 清空模块级缓存，绝不碰 data/sessions.db。"""
    path = str(tmp_path / "fbthread.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables(force=True)
    quota._ensure_tables()
    quota._ensure_rate_table()
    user_feedback._READY.clear()
    user_feedback.ensure()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [ADMIN_NAME])
    monkeypatch.setattr(config, "ADMIN_UIDS", [])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    yield path
    user_feedback._READY.clear()


@pytest.fixture()
def client(env):
    import main

    return TestClient(main.app)


def _login(client: TestClient, username: str) -> str:
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def _uid_of(name: str) -> str:
    if not account.get_user_by_username(name):
        account.create_user(name, PASSWORD)
    return account.get_user_by_username(name)["id"]


def _add(name: str, content: str, category: str = "bug", at: int | None = None) -> int:
    """写一条反馈，可指定时间（回拨时间只能直接改库，`add` 用 now）。"""
    uid = _uid_of(name)
    fid = user_feedback.add(uid, name, category, content, "", "/library")
    if at is not None:
        from core import session as store

        with store._get_conn() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE user_feedback SET created_at = ?, updated_at = ? WHERE id = ?",
                (at, at, fid),
            )
    return fid


# ==================== 会话聚合 ====================


def test_same_user_becomes_one_thread(env):
    """一个人提三条 = 一个会话，不是三个。"""
    _add(USER_A, "第一条，页面卡住了")
    _add(USER_A, "第二条，还是卡")
    _add(USER_B, "我也遇到卡顿，在资料库")

    data = user_feedback.threads()
    assert data["total"] == 2, "两个人才对，不是三条"
    by_name = {it["name"]: it for it in data["items"]}
    assert by_name[USER_A]["n"] == 2
    assert by_name[USER_B]["n"] == 1


def test_open_count_only_counts_open(env):
    """未处理数只数 open —— 把 doing 也算进去，红点会比实际大一倍。"""
    fid_a = _add(USER_A, "第一个问题，打不开")
    _add(USER_A, "第二个问题，还是打不开")
    user_feedback.set_status(fid_a, "doing")

    data = user_feedback.threads()
    item = next(i for i in data["items"] if i["name"] == USER_A)
    assert item["n"] == 2
    assert item["open_n"] == 1, "一条已推进到处理中，不该再算未处理"
    assert item["doing_n"] == 1


def test_thread_preview_follows_last_message(env):
    """列表预览是"最后一句"，管理员回过话就该显示回复，而不是用户的原话。"""
    fid = _add(USER_A, "原始问题描述，很长很长的一句")
    user_feedback.add_reply(fid, "", "冯靖豪", "已经修好了，刷新看看")

    item = next(i for i in user_feedback.threads()["items"] if i["name"] == USER_A)
    assert item["last_from"] == "admin"
    assert "已经修好了" in item["preview"]


def test_threads_total_is_people_not_page_size(env):
    """`total` 是人数，不是本页条数（列表头会写「N 人」）。"""
    for i in range(5):
        _add(f"user{i}", f"第 {i} 个人的反馈内容")
    data = user_feedback.threads(limit=2)
    assert len(data["items"]) == 2, "limit 只截取列表"
    assert data["total"] == 5, "但总数必须是 5 个人"


# ==================== 消息排序 ====================


def test_thread_messages_are_globally_time_ordered(env):
    """★ 回归：回复不能因为挂在旧反馈下就排到新反馈前面。

    构造：A 提问(1 天前) → 管理员回 A(今天) → 用户又提了 B(2 天前)
    正确顺序按时间：A(3天前) → B(2天前) → 回复(今天)
    错误（成组输出）：A → 回复 → B
    """
    now = int(time.time())
    fid_a = _add(USER_A, "三天前的问题啊啊", at=now - 3 * DAY)
    _add(USER_A, "两天前的第二个问题", at=now - 2 * DAY)
    user_feedback.add_reply(fid_a, "", "苞米呀", "今天的回复")

    th = user_feedback.thread(_uid_of(USER_A))
    kinds = [(m["kind"], m["at"]) for m in th["messages"]]
    assert [k for k, _ in kinds] == ["user", "user", "admin"], kinds
    assert kinds[-1][1] > kinds[1][1], "回复的时间必须晚于第二条反馈"


def test_thread_contains_both_sides(env):
    """对话里用户消息与管理员消息都要有，且管理员那条带署名。"""
    fid = _add(USER_A, "提个建议，能加导出吗")
    user_feedback.add_reply(fid, "", "冯靖豪", "可以，下周做")

    th = user_feedback.thread(_uid_of(USER_A))
    assert th["total"] == 1
    assert len(th["messages"]) == 2
    admin = [m for m in th["messages"] if m["kind"] == "admin"][0]
    assert admin["author"] == "冯靖豪"
    assert admin["fid"] == fid


# ==================== 会话回复 ====================


def test_reply_lands_on_latest_feedback(env):
    """会话里回一句，必须挂到**最新**那条反馈下。

    挂到第一条的后果：用户打开「我提过的」，一条三天前的旧反馈突然多了回复，
    而他刚提的那条仍然显示"已收到"。
    """
    now = int(time.time())
    old = _add(USER_A, "很旧的反馈内容", at=now - 3 * DAY)
    new = _add(USER_A, "刚刚才提的反馈", at=now - 60)

    ok, err = user_feedback.reply_thread(_uid_of(USER_A), "", "苞米呀", "收到，这就看")
    assert ok, err

    th = user_feedback.thread(_uid_of(USER_A))
    replies = [m for m in th["messages"] if m["kind"] == "admin"]
    assert len(replies) == 1
    assert replies[0]["fid"] == new, "应该挂在最新那条"
    assert replies[0]["fid"] != old


def test_reply_to_unknown_user_is_rejected(env):
    """给不存在的用户回复要明确失败，不能静默写一条挂不上的回复。"""
    ok, err = user_feedback.reply_thread("no-such-uid", "", "苞米呀", "喂")
    assert not ok
    assert "没有反馈" in err


# ==================== 图表口径 ====================


def test_stats_category_is_windowed_but_status_is_all(env):
    """分类只看区间、状态看全量 —— 两个口径不同，混了数字就对不上。"""
    now = int(time.time())
    _add(USER_A, "很久以前的一条旧反馈", category="bug", at=now - 60 * DAY)
    _add(USER_B, "最近的一条新反馈", category="feature", at=now - DAY)

    s = user_feedback.stats(30)
    assert s["total_all"] == 2, "全量是两条"
    assert s["in_range"] == 1, "30 天窗口里只有一条"

    cat = {x["value"]: x["n"] for x in s["by_category"]}
    assert cat["feature"] == 1
    assert cat["bug"] == 0, "旧的那条不在窗口内"

    st = {x["value"]: x["n"] for x in s["by_status"]}
    assert st["open"] == 2, "状态是全量，两条都还没处理"


def test_stats_series_covers_every_day(env):
    """折线要有连续的 30 个点（缺天会让折线把两段连成一条直线）。"""
    _add(USER_A, "今天提的一条反馈")
    s = user_feedback.stats(30)
    assert len(s["series"]) == 30
    assert s["in_range"] == 1
    assert sum(x["n"] for x in s["series"]) == 1


# ==================== 鉴权 ====================


def test_threads_requires_admin(client):
    """普通用户拿不到会话列表（与看板同一套 404 约定）。"""
    _login(client, USER_A)
    r = client.get("/api/admin/feedback/threads")
    assert r.status_code == 404


def test_thread_endpoint_shape(client):
    """管理员拿到的对话结构：uid/name/messages，消息带 kind。"""
    _add(USER_A, "接口形状测试用反馈")
    _login(client, ADMIN_NAME)
    r = client.get(f"/api/admin/feedback/thread/{_uid_of(USER_A)}")
    assert r.status_code == 200
    d = r.json()
    assert d["uid"] == _uid_of(USER_A)
    assert d["name"] == USER_A
    assert d["messages"][0]["kind"] == "user"
    assert "status_label" in d["messages"][0]


def test_thread_reply_endpoint_appends(client):
    """POST 会话回复后，返回的对话里多一条 admin 消息。"""
    _add(USER_A, "先提一条，等着被回复")
    _login(client, ADMIN_NAME)
    uid = _uid_of(USER_A)
    before = client.get(f"/api/admin/feedback/thread/{uid}").json()
    r = client.post(
        f"/api/admin/feedback/thread/{uid}/reply", json={"text": "我们看到了，正在处理"}
    )
    assert r.status_code == 200
    after = r.json()
    assert len(after["messages"]) == len(before["messages"]) + 1
    assert after["messages"][-1]["kind"] == "admin"
    assert after["messages"][-1]["text"] == "我们看到了，正在处理"


def test_empty_thread_reply_is_400(client):
    """空回复拒绝 —— 不可撤回之后，空白回复只是数据噪声。"""
    _add(USER_A, "随便提一条反馈内容")
    _login(client, ADMIN_NAME)
    r = client.post(
        f"/api/admin/feedback/thread/{_uid_of(USER_A)}/reply", json={"text": "   "}
    )
    assert r.status_code == 400


# ==================== 单条定位（2026-11-19）====================
#
# 用户的原话：「想把某条标记为已处理时只能按反馈时间统一操作，误点后也无法更正」。
# 下面这组用例守的就是"精确作用到所选那一条"，以及"改错了能改回来"。


def test_reply_lands_on_the_chosen_feedback(env):
    """按 fid 回复只落在指定那条，另外两条一个字节都不动。"""
    f1 = _add(USER_A, "第一条：设置页按钮点不动")
    f2 = _add(USER_A, "第二条：想要日历视图")
    f3 = _add(USER_A, "第三条：论坛能加 AI 总结吗")
    uid = _uid_of(USER_A)

    ok, err = user_feedback.reply_thread(uid, "admin", "冯靖豪", "第二条已排期", fid=f2)
    assert ok, err

    items = {t["fid"]: t for t in user_feedback.thread(uid)["items"]}
    assert [r["text"] for r in items[f2]["replies"]] == ["第二条已排期"]
    assert items[f1]["replies"] == [], "回复串到第一条了"
    assert items[f3]["replies"] == [], "回复串到第三条了"


def test_reply_without_fid_still_goes_to_latest(env):
    """不传 fid 的老行为保留：挂最新一条（老前端与脚本还在用）。"""
    early = _add(USER_A, "早一点的问题，先提的", at=int(time.time()) - 3 * DAY)
    late = _add(USER_A, "晚一点的问题，后提的")
    uid = _uid_of(USER_A)

    ok, _ = user_feedback.reply_thread(uid, "admin", "冯靖豪", "回最新那条")
    assert ok
    items = {t["fid"]: t for t in user_feedback.thread(uid)["items"]}
    assert items[late]["replies"][0]["text"] == "回最新那条"
    assert items[early]["replies"] == []


def test_reply_rejects_someone_elses_fid(env):
    """传别人的 fid 必须失败：回复不可撤回，写进别人的反馈收不回来。"""
    fid_b = _add(USER_B, "B 的问题，不该被 A 的会话污染")
    uid_a = _uid_of(USER_A)

    ok, err = user_feedback.reply_thread(uid_a, "admin", "冯靖豪", "越权写入", fid=fid_b)
    assert not ok and "不属于" in err

    items = {t["fid"]: t for t in user_feedback.thread(_uid_of(USER_B))["items"]}
    assert items[fid_b]["replies"] == [], "失败就不能留半条数据"


def test_user_reply_only_on_own_feedback(env):
    """用户 A 不能往 B 的反馈里写字（改个 id 就能写，而回复不可撤回）。"""
    fid_b = _add(USER_B, "B 的反馈")
    uid_a = _uid_of(USER_A)

    ok, err = user_feedback.reply_own(uid_a, fid_b, "我是 A，我要写进 B 那条")
    assert not ok, err
    assert user_feedback.thread(_uid_of(USER_B))["items"][0]["replies"] == []


def test_user_reply_does_not_touch_status(env):
    """用户自己补充一句不是"已接手"：状态必须原样不动。"""
    fid = _add(USER_A, "想补充一下，是设置页那个按钮")
    uid = _uid_of(USER_A)

    before = user_feedback.get(fid)["status"]
    ok, err = user_feedback.reply_own(uid, fid, "补充：只在 390 宽下出现")
    assert ok, err
    assert before == "open"
    assert user_feedback.get(fid)["status"] == "open", "用户补充把状态推成 doing 了"


def test_user_reply_marked_and_not_taken_as_admin_reply(env):
    """用户补充在对话里标 user_reply，且不能冒充「开发者回复」。"""
    fid = _add(USER_A, "原始描述在这里")
    uid = _uid_of(USER_A)
    user_feedback.add_reply(fid, "admin", "冯靖豪", "官方回复一句")
    user_feedback.reply_own(uid, fid, "用户补充一句")

    th = user_feedback.thread(uid)
    kinds = [m["kind"] for m in th["items"][0]["replies"]]
    assert kinds == ["admin", "user_reply"]
    # 兼容字段只认管理员那条：取到用户自己那句，用户侧会显示
    # 「开发者回复：<用户自己的话>」—— 语义直接反了
    assert user_feedback.get(fid)["reply"] == "官方回复一句"


def test_status_can_be_corrected_back(env):
    """点错「已处理」能改回来（旧流转表把 done → doing 直接拒掉）。"""
    fid = _add(USER_A, "这条被我误点了")
    assert user_feedback.set_status(fid, "done")[0]

    ok, err = user_feedback.set_status(fid, "doing")
    assert ok, f"应该允许更正，却报：{err}"
    assert user_feedback.get(fid)["status"] == "doing"
    # 再从「不处理」改回「待处理」也要能走通 —— 误点不分方向
    assert user_feedback.set_status(fid, "closed")[0]
    assert user_feedback.set_status(fid, "open")[0]


def test_status_change_hits_only_one_feedback(env):
    """改一条的状态，同一用户另外两条不受影响。"""
    f1 = _add(USER_A, "第一条，先放一放")
    f2 = _add(USER_A, "第二条，马上办")
    f3 = _add(USER_A, "第三条，再说")

    user_feedback.set_status(f2, "done")
    assert user_feedback.get(f1)["status"] == "open"
    assert user_feedback.get(f2)["status"] == "done"
    assert user_feedback.get(f3)["status"] == "open"


def test_thread_items_group_replies_per_feedback(env):
    """分组视图：每条反馈只带自己的回复链，不是整段会话的流水。"""
    f1 = _add(USER_A, "第一条")
    f2 = _add(USER_A, "第二条")
    uid = _uid_of(USER_A)
    user_feedback.add_reply(f1, "admin", "甲", "回第一条")
    user_feedback.add_reply(f2, "admin", "乙", "回第二条")

    items = {t["fid"]: t for t in user_feedback.thread(uid)["items"]}
    assert len(items) == 2
    assert [r["text"] for r in items[f1]["replies"]] == ["回第一条"]
    assert [r["text"] for r in items[f2]["replies"]] == ["回第二条"]
    # 每条自带状态，前端的状态控件才有东西可挂
    for t in items.values():
        assert t["status"] in ("open", "doing", "done", "closed")


def test_api_thread_reply_targets_fid(client):
    """接口层也要能指定 fid：前端传的是 body.fid，不是 URL。"""
    _login(client, ADMIN_NAME)
    f1 = _add(USER_A, "接口测试第一条")
    f2 = _add(USER_A, "接口测试第二条")
    uid = _uid_of(USER_A)

    # ★ 指定**第一条**（非最新）：如果后端忽略 fid、永远挂最新，这条必红。
    #   一开始这里写的是 f2（恰好是最新），反证时"忽略 fid"照样绿 —— 判据没力气。
    r = client.post(
        f"/api/admin/feedback/thread/{uid}/reply",
        json={"text": "回第一条", "fid": f1},
    )
    assert r.status_code == 200, r.text
    items = {t["fid"]: t for t in r.json()["items"]}
    assert [x["text"] for x in items[f1]["replies"]] == ["回第一条"]
    assert items[f2]["replies"] == [], "回复挂到最新那条去了"


def test_api_thread_reply_rejects_foreign_fid(client):
    """接口层同样拒绝越权 fid，且是 400（参数错，不是"这条不存在"）。"""
    _login(client, ADMIN_NAME)
    fid_b = _add(USER_B, "B 的反馈，接口层不该被写")
    uid_a = _uid_of(USER_A)

    r = client.post(
        f"/api/admin/feedback/thread/{uid_a}/reply",
        json={"text": "越权", "fid": fid_b},
    )
    assert r.status_code == 400, r.text


def test_api_user_reply_endpoint(client):
    """用户侧补一句：只影响自己那条，且不改状态。"""
    _add(USER_B, "B 的反馈，用来验证越权被拒")
    fid = _add(USER_A, "A 的反馈，准备补一句")
    _login(client, USER_A)
    before = user_feedback.get(fid)["status"]

    r = client.post(f"/api/user-feedback/{fid}/reply", json={"text": "补充：是 390 宽下"})
    assert r.status_code == 200, r.text

    items = {t["fid"]: t for t in user_feedback.thread(_uid_of(USER_A))["items"]}
    assert [x["kind"] for x in items[fid]["replies"]] == ["user_reply"]
    assert user_feedback.get(fid)["status"] == before
