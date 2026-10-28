"""用户反馈「回复」链路（`core/user_feedback.set_reply` + `POST /api/admin/feedback/{fid}/reply`）。

背景（2026-10-28 用户反馈）：后台看板上每条反馈只有「→ 不处理 / → 已处理」两个按钮，
管理员没法回一句话。但**只加一个输入框是没用的** —— 得让提意见的人真能看到，
否则回复等于写进自己的日志里。所以这条链路的验收重点是「两端都通」：

  1. `set_reply` 写进去、`reply` / `replied_at` 能被读出来（含 `list_mine` 这条路，
     用户侧就是靠它看到回复的）
  2. **回复顺手把 open 推到 doing**，但不许把 done / closed 改写回 doing
  3. 回复**不受状态流转表约束** —— 拿 `_TRANSITIONS` 去卡回复会得到
     「已处理 → 已处理」这种无意义 409，管理员就回不了话
  4. 长度上限、空串撤回、不存在的 id
  5. 老库迁移：没有 reply 列的老表能幂等补列

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
    assert before["reply"] == ""
    assert before["replied_at"] is None

    ok, err = uf.set_reply(fid, "  已经修好了，明天上线  ")
    assert ok, err

    after = uf.get(fid)
    assert after is not None
    # 前后空白要去掉：否则前端展示时会多出一截，也浪费长度上限
    assert after["reply"] == "已经修好了，明天上线"
    assert isinstance(after["replied_at"], int) and after["replied_at"] > 0

    # 用户侧那条路（/api/user-feedback/mine 走的就是 list_mine）
    mine, total = uf.list_mine("u1")
    assert total == 1
    assert mine[0]["reply"] == "已经修好了，明天上线"


def test_reply_promotes_open_to_doing(monkeypatch):
    """回复 = 已接手。状态还停在「待处理」会让用户以为回复是自动模板。"""
    _prep_db(monkeypatch, "promote")
    fid = _add()
    assert uf.get(fid)["status"] == "open"

    ok, _ = uf.set_reply(fid, "已定位，是复习队列的索引问题")
    assert ok
    assert uf.get(fid)["status"] == "doing"


def test_reply_does_not_downgrade_done_or_closed(monkeypatch):
    """已处理 / 不处理的反馈再补一句回复，状态不能被改写回「处理中」。

    这条是负向断言：正向路径（open → doing）走通，不代表没把别的状态带坏。
    """
    _prep_db(monkeypatch, "nodowngrade")

    fid_done = _add("u1", "这条已经解决了")
    assert uf.set_status(fid_done, "done")[0]
    assert uf.set_reply(fid_done, "补充：下个版本还会优化文案")[0]
    assert uf.get(fid_done)["status"] == "done"
    assert uf.get(fid_done)["reply"] == "补充：下个版本还会优化文案"

    fid_closed = _add("u1", "这个我们不做")
    assert uf.set_status(fid_closed, "closed")[0]
    assert uf.set_reply(fid_closed, "原因：与课表 App 定位重复")[0]
    assert uf.get(fid_closed)["status"] == "closed"


def test_reply_not_blocked_by_transition_table(monkeypatch):
    """回复不受 `_TRANSITIONS` 约束。

    曾经的写法是把回复塞进 `set_status` 的同一套校验里，结果「已处理 → 已处理」
    被判成非法流转，管理员怼着一条已处理的反馈就再也回不了话。
    """
    _prep_db(monkeypatch, "noblock")
    fid = _add()
    uf.set_status(fid, "done")  # 先走完整流程到 done（done 的下一步只有 closed）

    ok, err = uf.set_reply(fid, "顺手补一句")
    assert ok, f"回复不该被状态流转表拦住：{err}"


def test_reply_length_and_missing_id(monkeypatch):
    """超长 400 对应的服务层行为；不存在的 id 要能被识别出来（API 层转 409）。"""
    _prep_db(monkeypatch, "len")
    fid = _add()

    ok, err = uf.set_reply(fid, "长" * (uf.MAX_REPLY + 1))
    assert not ok and "最多" in err
    # 失败的写入不能留下半条数据
    assert uf.get(fid)["reply"] == ""

    # 极端：刚好等于上限要放行（边界不能写成 < 而不是 <=）
    ok, err = uf.set_reply(fid, "长" * uf.MAX_REPLY)
    assert ok, err

    ok, err = uf.set_reply(999999, "回复一条不存在的反馈")
    assert not ok and "不存在" in err


def test_empty_reply_revokes_but_keeps_status(monkeypatch):
    """回错了要能撤回：清空回复，但状态不回退（状态本身不允许回退）。"""
    _prep_db(monkeypatch, "revoke")
    fid = _add()
    uf.set_reply(fid, "回错了")
    assert uf.get(fid)["status"] == "doing"

    ok, _ = uf.set_reply(fid, "   ")
    assert ok
    row = uf.get(fid)
    assert row["reply"] == ""
    assert row["replied_at"] is None
    assert row["status"] == "doing"


def test_migration_adds_reply_columns_to_old_table(monkeypatch):
    """老库（没有 reply / replied_at 列）必须能幂等补列，且旧数据不丢。

    这是线上真实情况：`user_feedback` 表 10-27 就建好了，10-28 才加回复。
    建表语句刻意不改（只走 ALTER），所以这条断言盯的是"老库还能用"。
    """
    tmp = tempfile.mkdtemp(prefix="uf_migrate_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    uf._READY.clear()
    core.session._init_db()

    # 手工造一张「加列之前」的表，并写一行历史数据
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
                updated_at INTEGER NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO user_feedback (uid, username, category, content, status, created_at, updated_at) "
            "VALUES ('old', 'olduser', 'bug', '加列之前提的一条', 'open', 1, 1)"
        )

    uf.ensure()
    # 第二遍：幂等，不能因为列已存在而炸
    uf._READY.clear()
    uf.ensure()

    old = uf.get(1)
    assert old is not None
    assert old["content"] == "加列之前提的一条"  # 旧数据还在
    assert old["reply"] == ""  # 老行拿到合理初值
    assert old["replied_at"] is None

    # 老行也能被回复
    assert uf.set_reply(1, "历史反馈也能回")[0]
    assert uf.get(1)["reply"] == "历史反馈也能回"
