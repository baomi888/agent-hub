# -*- coding: utf-8 -*-
"""关卡式学习路径（/api/path）服务层测试。

不联网、不依赖真实 API Key、不打开 Chroma：
  · 每个测试 monkeypatch core.session._db_path 到独立临时库；
  · daily 有模块级「已建表」标志，切库必须复位，否则新库里没有 daily_reports（no such table）；
  · 知识库计数（service.path._kb_counts）整体替换掉，测试不去读真实 data/kb_*.json。

覆盖：
  1. 空用户：12 关全未过，第一关是 current，其余 locked
  2. 真实数据驱动解锁：知识库 / 提问 / 计划 各自点亮对应关卡
  3. 全通关：词汇、真题、分数、总结、委托都到位后 12/12
  4. 首次达成时间：INSERT OR IGNORE 不覆盖已记录的时间戳
  5. owner 强制隔离
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session  # noqa: E402

from core import quota  # noqa: E402
from service import cet  # noqa: E402
from service import checkin  # noqa: E402
from service import daily  # noqa: E402
from service import path as path_svc  # noqa: E402


def _exec(sql: str, params: tuple = ()) -> None:
    with core.session._get_conn() as conn:
        conn.execute(sql, params)


def _prep_db(monkeypatch, tag: str) -> None:
    tmp = tempfile.mkdtemp(prefix=f"path_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    # daily 的建表是「只做一次」的：换库必须复位，否则新库里查不到 daily_reports
    daily._daily_tables_ready = False
    daily._ensure_daily_tables()
    cet._ensure_cet_tables()
    checkin._ensure_checkin_tables()
    path_svc._ensure_path_tables()
    # 知识库计数默认 0（不读真实 data 目录），需要的用例自己覆盖
    monkeypatch.setattr(path_svc, "_kb_counts", lambda uid: (0, 0))


def _add_vocab(uid: str, n: int, step: int) -> None:
    for i in range(n):
        _exec(
            "INSERT INTO vocab_items(id, owner_id, word, meaning, source, step, "
            "interval_days, next_review, wrong_count, created_at, last_review) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{uid}-w{i}", uid, f"word{i}", "", "test", step, 3, "2099-01-01", 0, 0, None),
        )


def _add_real_tasks(uid: str, plan_id: str, n: int, today: str) -> None:
    for i in range(n):
        _exec(
            "INSERT INTO cet_tasks(id, plan_id, owner_id, day_index, task_date, module, title, done, done_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{uid}-rt{i}", plan_id, uid, 1, today, "真题", "真题限时 1 套", 1, 0),
        )


def _add_reports(uid: str, n: int) -> None:
    base = datetime.date.today()
    for i in range(n):
        d = (base - datetime.timedelta(days=i)).isoformat()
        _exec(
            "INSERT INTO daily_reports(report_id, owner_id, date, status, stats_json, content_json, "
            "model, generated_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{uid}-r{i}", uid, d, "ready", None, None, None, 0, None),
        )


def _add_delegation(uid: str) -> None:
    _exec(
        "INSERT INTO delegations(delegation_id, owner_id, name, kind, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"{uid}-d1", uid, "每日复盘", "daily", 1, 0, 0),
    )


def _levels(ov: dict) -> dict[str, dict]:
    return {lv["id"]: lv for ch in ov["chapters"] for lv in ch["levels"]}


# ==================== 用例 ====================

def test_empty_user(monkeypatch):
    _prep_db(monkeypatch, "empty")
    ov = path_svc.overview("u1")

    assert ov["progress"] == {"done": 0, "total": 12, "percent": 0}
    assert len(ov["chapters"]) == 4
    assert sum(len(ch["levels"]) for ch in ov["chapters"]) == 12

    lv = _levels(ov)
    assert lv["L01"]["status"] == "current"
    assert lv["L02"]["status"] == "locked"
    assert lv["L12"]["status"] == "locked"
    assert ov["current"]["id"] == "L01"


def test_unlock_by_real_data(monkeypatch):
    _prep_db(monkeypatch, "unlock")
    # 知识库 1 个 + 3 份资料 → L01、L02
    monkeypatch.setattr(path_svc, "_kb_counts", lambda uid: (1, 3))
    # 一次用户提问 → L03
    _exec(
        "INSERT INTO conversations(id, owner_id, title, kb_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "u1", "六级怎么复习", "kb1", 0, 0),
    )
    _exec(
        "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("c1", "user", "帮我拆一下 54 天计划", 0),
    )
    # 计划 → L04
    cet.create_plan("u1", days=7)

    ov = path_svc.overview("u1")
    lv = _levels(ov)

    assert [lv[f"L0{i}"]["status"] for i in range(1, 5)] == ["done"] * 4
    assert ov["progress"]["done"] == 4
    assert ov["chapters"][0]["done"] == 3
    # 第 5 关（连续打卡）还没达成 → 成为当前关，后面锁住
    assert lv["L05"]["status"] == "current"
    assert lv["L06"]["status"] == "locked"
    assert ov["current"]["id"] == "L05"
    # 详情文案要带上真实数值，而不是「0/1」这种占位
    assert lv["L02"]["detail"] == "已入库资料 3 / 3 份"


def test_all_levels_cleared(monkeypatch):
    _prep_db(monkeypatch, "all")
    monkeypatch.setattr(path_svc, "_kb_counts", lambda uid: (1, 5))
    _exec(
        "INSERT INTO conversations(id, owner_id, title, kb_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "u1", "t", "kb1", 0, 0),
    )
    _exec(
        "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("c1", "user", "hi", 0),
    )
    plan = cet.create_plan("u1", days=7)
    _add_reports("u1", 7)  # 顺带把连续打卡做到 7 天
    _add_vocab("u1", 300, step=2)  # 同时满足 100（step>=1）与 300（step>=2）
    _add_real_tasks("u1", plan["id"], 10, quota.day_key())
    cet.add_score("u1", "CET6", 500, 500)
    _add_delegation("u1")

    ov = path_svc.overview("u1")
    assert ov["progress"] == {"done": 12, "total": 12, "percent": 100}
    assert ov["current"] is None
    assert all(ch["done"] == ch["total"] for ch in ov["chapters"])

    lv = _levels(ov)
    assert lv["L09"]["checks"][0]["value"] == 300
    assert lv["L10"]["checks"][0]["target"] == 500  # 目标分取用户自己填的
    assert lv["L10"]["checks"][0]["passed"] is True


def test_done_at_is_first_time(monkeypatch):
    _prep_db(monkeypatch, "ts")
    monkeypatch.setattr(path_svc, "_kb_counts", lambda uid: (1, 3))

    first = path_svc.overview("u1")
    assert _levels(first)["L01"]["done_at"] is not None

    # 手工把首次达成时间改成一个旧值，再跑一次 overview：不能被现在的时钟覆盖
    _exec("UPDATE path_progress SET done_at = 12345 WHERE owner_id = 'u1' AND node_id = 'L01'")
    again = path_svc.overview("u1")
    assert _levels(again)["L01"]["done_at"] == 12345


def test_owner_isolation(monkeypatch):
    _prep_db(monkeypatch, "owner")
    monkeypatch.setattr(path_svc, "_kb_counts", lambda uid: (1, 5) if uid == "u1" else (0, 0))
    cet.create_plan("u1", days=7)
    _add_vocab("u1", 300, step=2)

    ov_a = path_svc.overview("u1")
    ov_b = path_svc.overview("u2")
    assert ov_a["progress"]["done"] > ov_b["progress"]["done"]
    assert ov_b["progress"]["done"] == 0
    assert ov_b["current"]["id"] == "L01"
    # u2 不能因为 u1 的数据而点亮任何一关
    assert all(x["status"] != "done" for x in _levels(ov_b).values())
