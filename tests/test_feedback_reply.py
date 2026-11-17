"""用户反馈「回复」链路（`core/user_feedback.add_reply` + `POST /api/admin/feedback/{fid}/reply`）。

背景（2026-10-28 用户反馈）：后台看板上每条反馈只有「→ 不处理 / → 已处理」两个按钮，
管理员没法回一句话。但**只加一个输入框是没用的** —— 得让提意见的人真能看到，
否则回复等于写进自己的日志里。所以这条链路的验收重点是「两端都通」。

**2026-11-17 改语义**（加了第二个管理员之后）：回复从"一格可改写"变成**追加式线程**。
用户的原话是「自己发送的回复不能被修改，别人只能在我的回复下面增加」，
并且「要能看出是哪个管理员发的」。所以这里守的是：

  1. 追加不覆盖：两条回复并存、各自带作者、按时间正序
  2. **没有改写路径**：源码里不许出现 UPDATE / DELETE `user_feedback_replies`，
     也不许再有 `set_reply` 这个名字（它是"可编辑"的入口）
  3. 回复顺手把 open 推到 doing，但不许把 done / closed 改写回 doing
  4. 回复**不受状态流转表约束** —— 拿 `_TRANSITIONS` 去卡回复会得到
     「已处理 → 已处理」这种无意义 409，管理员就回不了话
  5. 长度上限、空文本拒绝、不存在的 id
  6. 老库那条单独 `reply` 能搬进线程，且重复跑不会搬两遍

不联网。每个测试 monkeypatch `core.session._db_path` 到独立临时库；
`user_feedback._READY` 是模块级「这张库已建表」标志，切库必须清掉，
否则新库一条 DDL 都不跑（`tests/test_cet.py` 记过这个坑的同类问题）。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session  # noqa: E402
from core import user_feedback as uf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prep_db(monkeypatch, tag: str) -> str:
    tmp = tempfile.mkdtemp(prefix=f"uf_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    uf._READY.clear()  # ← 不清这个，新库不会建表
    core.session._init_db()
    uf.ensure()
    return db


def _add(uid: str = "u1", content: str = "单词卡点了不认识就卡住") -> int:
    return uf.add(uid, "tester", "bug", content, "", "/settings")


def test_reply_written_and_visible_to_owner(monkeypatch):
    """管理员回复后，用户侧 `list_mine` 必须能读到 —— 这是整件事的意义所在。"""
    _prep_db(monkeypatch, "write")
    fid = _add()

    before = uf.get(fid)
    assert before is not None
    assert before["replies"] == []
    assert before["reply"] == ""
    assert before["replied_at"] is None

    ok, err = uf.add_reply(fid, "admin-a", "苞米呀", "  已经修好了，明天上线  ")
    assert ok, err

    after = uf.get(fid)
    assert after is not None
    # 前后空白要去掉：否则前端展示时会多出一截，也浪费长度上限
    assert [r["content"] for r in after["replies"]] == ["已经修好了，明天上线"]
    assert isinstance(after["replied_at"], int) and after["replied_at"] > 0

    # 用户侧那条路（/api/user-feedback/mine 走的就是 list_mine）
    mine, total = uf.list_mine("u1")
    assert total == 1
    assert [r["content"] for r in mine[0]["replies"]] == ["已经修好了，明天上线"]


def test_second_admin_appends_instead_of_overwriting(monkeypatch):
    """两个人先后回，两条都要在，谁写的要分得清。

    这是本批的核心：老实现只有一格 `reply`，第二个管理员一发就把第一句
    静默覆盖掉了 —— 而且看库里只剩一句话，事后谁也不知道被覆盖过。
    """
    _prep_db(monkeypatch, "append")
    fid = _add()

    assert uf.add_reply(fid, "admin-a", "苞米呀", "已定位，是索引问题")[0]
    assert uf.add_reply(fid, "admin-b", "小李", "我这边补一个修复时间：周四")[0]
    assert uf.add_reply(fid, "admin-a", "苞米呀", "补一句：周四晚上线")[0]

    row = uf.get(fid)
    assert [r["content"] for r in row["replies"]] == [
        "已定位，是索引问题",
        "我这边补一个修复时间：周四",
        "补一句：周四晚上线",
    ]
    # 作者按条记录，不是"最后一个人覆盖整条线程"
    assert [r["admin_uid"] for r in row["replies"]] == ["admin-a", "admin-b", "admin-a"]
    assert [r["admin_name"] for r in row["replies"]] == ["苞米呀", "小李", "苞米呀"]
    # id 递增 = 线程顺序稳定（前端按它做 key，顺序不能每查一次就变）
    ids = [r["id"] for r in row["replies"]]
    assert ids == sorted(ids)

    # 兼容别名指向**最新**一条，老前端（只认 it.reply）不会显示成第一句
    assert row["reply"] == "补一句：周四晚上线"


def test_replies_are_append_only():
    """结构守卫：这条链路里不许存在"改"和"删"。

    「不能修改」不是靠按钮藏起来实现的 —— 前端藏了，接口还在，下次有人
    顺手写个脚本就能改。所以直接钉住源码：没有 UPDATE/DELETE，也没有
    `set_reply` 这个曾经存在的可改写入口（留着它，早晚会有人调回去）。
    """
    src = open(os.path.join(ROOT, "core", "user_feedback.py"), encoding="utf-8").read()
    assert "UPDATE user_feedback_replies" not in src, "出现了改写回复的 SQL"
    assert "DELETE FROM user_feedback_replies" not in src, "出现了删除回复的 SQL"
    assert "def set_reply" not in src, "set_reply 又回来了：那是可改写的老入口"

    api = open(os.path.join(ROOT, "api", "admin.py"), encoding="utf-8").read()
    # 只允许 POST 追加这一个写路径；不能有 PATCH/DELETE 指向 reply(s)
    assert '@router.delete("/feedback' not in api
    assert 'feedback/{fid}/replies' not in api
    assert "user_feedback.add_reply" in api


def test_reply_promotes_open_to_doing(monkeypatch):
    """回复 = 已接手。状态还停在「待处理」会让用户以为回复是自动模板。"""
    _prep_db(monkeypatch, "promote")
    fid = _add()
    assert uf.get(fid)["status"] == "open"

    ok, _ = uf.add_reply(fid, "admin-a", "苞米呀", "已定位，是复习队列的索引问题")
    assert ok
    assert uf.get(fid)["status"] == "doing"


def test_reply_does_not_downgrade_done_or_closed(monkeypatch):
    """已处理 / 不处理的反馈再补一句回复，状态不能被改写回「处理中」。

    这条是负向断言：正向路径（open → doing）走通，不代表没把别的状态带坏。
    """
    _prep_db(monkeypatch, "nodowngrade")

    fid_done = _add("u1", "这条已经解决了")
    assert uf.set_status(fid_done, "done")[0]
    assert uf.add_reply(fid_done, "admin-a", "苞米呀", "补充：下个版本还会优化文案")[0]
    assert uf.get(fid_done)["status"] == "done"
    assert uf.get(fid_done)["reply"] == "补充：下个版本还会优化文案"

    fid_closed = _add("u1", "这个我们不做")
    assert uf.set_status(fid_closed, "closed")[0]
    assert uf.add_reply(fid_closed, "admin-a", "苞米呀", "原因：与课表 App 定位重复")[0]
    assert uf.get(fid_closed)["status"] == "closed"


def test_reply_not_blocked_by_transition_table(monkeypatch):
    """回复不受 `_TRANSITIONS` 约束。

    曾经的写法是把回复塞进 `set_status` 的同一套校验里，结果「已处理 → 已处理」
    被判成非法流转，管理员怼着一条已处理的反馈就再也回不了话。
    """
    _prep_db(monkeypatch, "noblock")
    fid = _add()
    uf.set_status(fid, "done")  # 先走完整流程到 done（done 的下一步只有 closed）

    ok, err = uf.add_reply(fid, "admin-a", "苞米呀", "顺手补一句")
    assert ok, f"回复不该被状态流转表拦住：{err}"


def test_reply_length_and_missing_id(monkeypatch):
    """超长 400 对应的服务层行为；不存在的 id 要能被识别出来（API 层转 409）。"""
    _prep_db(monkeypatch, "len")
    fid = _add()

    ok, err = uf.add_reply(fid, "admin-a", "苞米呀", "长" * (uf.MAX_REPLY + 1))
    assert not ok and "最多" in err
    # 失败的写入不能留下半条数据
    assert uf.get(fid)["replies"] == []

    # 极端：刚好等于上限要放行（边界不能写成 < 而不是 <=）
    ok, err = uf.add_reply(fid, "admin-a", "苞米呀", "长" * uf.MAX_REPLY)
    assert ok, err

    ok, err = uf.add_reply(999999, "admin-a", "苞米呀", "回复一条不存在的反馈")
    assert not ok and "不存在" in err


def test_empty_reply_is_rejected(monkeypatch):
    """空文本不许发。

    老接口里空串 = 撤回；不可撤回之后，一条空白既不会显示在用户侧、
    又会占后台一行，是纯噪声。所以从"能发"变成"400"。
    """
    _prep_db(monkeypatch, "empty")
    fid = _add()

    ok, err = uf.add_reply(fid, "admin-a", "苞米呀", "   ")
    assert not ok and "不能为空" in err
    assert uf.get(fid)["replies"] == []
    # 被拒的空回复不该顺手把状态推到 doing
    assert uf.get(fid)["status"] == "open"


def test_legacy_reply_migrates_into_thread(monkeypatch):
    """老库：没有回复表、只有单独的 `reply` 列，历史回复要搬进线程且只搬一次。

    线上真实情况：`user_feedback` 表 10-27 建的，`reply` 是 10-28 加的列，
    回复线程是 11-17 才有的。不搬的话，库里回过话的工单在用户侧会突然变空白。
    """
    tmp = tempfile.mkdtemp(prefix="uf_migrate_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    uf._READY.clear()
    core.session._init_db()

    # 手工造一张「回复线程之前」的表，并写一行带老回复的数据
    with core.session._get_conn() as conn:  # noqa: SLF001
        conn.executescript("""
            CREATE TABLE user_feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                uid        TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                category   TEXT NOT NULL,
                content    TEXT NOT NULL,
                contact    TEXT NOT NULL DEFAULT '',
                page       TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'open',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                reply      TEXT NOT NULL DEFAULT '',
                replied_at INTEGER
            );
        """)
        conn.execute(
            "INSERT INTO user_feedback (uid, username, category, content, status, "
            "created_at, updated_at, reply, replied_at) "
            "VALUES ('old', 'olduser', 'bug', '线程之前回过的一条', 'doing', 1, 1, "
            "'旧的单条回复', 1234)"
        )

    uf.ensure()
    # 第二遍：幂等，不能因为表已存在而炸，也不能把同一条回复搬成两条
    uf._READY.clear()
    uf.ensure()

    old = uf.get(1)
    assert old is not None
    assert old["content"] == "线程之前回过的一条"  # 旧数据还在
    assert len(old["replies"]) == 1, "老回复没搬进来，或者被搬了两遍"
    migrated = old["replies"][0]
    assert migrated["content"] == "旧的单条回复"
    # 老数据没记是谁回的，不许编一个名字
    assert migrated["admin_uid"] == ""
    assert migrated["admin_name"] == ""
    assert migrated["created_at"] == 1234  # 时间保留，不能变成迁移那一刻

    # 老行也能继续追加
    assert uf.add_reply(1, "admin-a", "苞米呀", "历史反馈也能回")[0]
    assert [r["content"] for r in uf.get(1)["replies"]] == ["旧的单条回复", "历史反馈也能回"]
