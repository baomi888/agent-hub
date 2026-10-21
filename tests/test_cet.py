# -*- coding: utf-8 -*-
"""P7 四六级备考工作区（/api/cet）服务层测试。

不联网、不依赖真实 API Key：
  · 每个测试 monkeypatch core.session._db_path 到独立临时库，测试结束自动还原；
  · 只测服务层（SQL + 业务规则），HTTP 层由 api/cet.py 的薄封装承担，风险低；
  · cet 没有模块级「已建表」标志，_ensure_cet_tables 每次 CREATE IF NOT EXISTS，切库安全。

覆盖：
  1. 计划：生成 N 天 × 5 模块、进度、勾选后连续天数
  2. 词汇本：SRS 记得前进一档 / 忘记回到第 1 档、到期过滤
  3. 分数：新增 / 列表 / 非法分值被拒
  4. owner 强制隔离：B 看不到 A 的计划与词卡
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session  # noqa: E402

from service import cet  # noqa: E402


def _prep_db(monkeypatch, tag: str) -> None:
    tmp = tempfile.mkdtemp(prefix=f"cet_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    cet._ensure_cet_tables()


def test_create_plan_and_toggle(monkeypatch):
    _prep_db(monkeypatch, "plan")
    p = cet.create_plan("u1", days=10)
    assert p["total_days"] == 10

    ov = cet.overview("u1")
    assert ov["plan"]["id"] == p["id"]
    assert ov["progress"]["total"] == 50  # 10 天 × 5 模块
    assert ov["progress"]["done"] == 0
    assert len(ov["today_tasks"]) == 5
    assert ov["streak"] == 0

    t = ov["today_tasks"][0]
    assert cet.toggle_task("u1", t["id"], True) is True
    ov2 = cet.overview("u1")
    assert ov2["progress"]["done"] == 1
    assert ov2["streak"] == 1

    # 幂等：再次勾同一任务仍成功，done 不重复累加
    assert cet.toggle_task("u1", t["id"], True) is True
    assert cet.overview("u1")["progress"]["done"] == 1


def test_vocab_srs(monkeypatch):
    _prep_db(monkeypatch, "vocab")
    v1 = cet.add_vocab("u1", "abandon", "放弃")
    assert v1["existed"] is False
    # 同词去重：不新建，只把下次复习提前
    v2 = cet.add_vocab("u1", "abandon", "")
    assert v2["existed"] is True and v2["id"] == v1["id"]

    assert len(cet.list_vocab("u1", due_only=True)) == 1
    # 记得 → 间隔 3 天，当天不再到期
    r = cet.review_vocab("u1", v1["id"], True)
    assert r["interval_days"] == 3
    assert len(cet.list_vocab("u1", due_only=True)) == 0
    # 忘记 → 回到 1 天，并计一次错
    r2 = cet.review_vocab("u1", v1["id"], False)
    assert r2["interval_days"] == 1
    items = cet.list_vocab("u1", due_only=False)
    assert items[0]["wrong_count"] == 1


def test_scores(monkeypatch):
    _prep_db(monkeypatch, "score")
    cet.add_score("u1", "CET6", 473, 500)
    cet.add_score("u1", "MOCK", 490, 500)
    rows = cet.list_scores("u1")
    assert len(rows) == 2
    assert rows[0]["score"] == 473

    # 非法分值直接拒（避免手误把 9999 写进曲线）
    try:
        cet.add_score("u1", "CET6", 9999)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("超范围分数未被拒绝")


def test_owner_isolation(monkeypatch):
    _prep_db(monkeypatch, "owner")
    cet.create_plan("u1", days=7)
    cet.add_vocab("u1", "fragile", "脆弱的")

    ov_b = cet.overview("u2")
    assert ov_b["plan"] is None
    assert ov_b["progress"]["total"] == 0
    assert cet.list_vocab("u2", due_only=False) == []

    # 越权勾选他人任务：rowcount=0 → False（上层转 404）
    task = cet.overview("u1")["today_tasks"][0]
    assert cet.toggle_task("u2", task["id"], True) is False
