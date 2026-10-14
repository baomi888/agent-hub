# -*- coding: utf-8 -*-
"""P2 信任基础设施：用量历史与赞踩聚合的离线单测（不联网）。

验证点：
- quota.history 返回固定长度、缺失日补零、按天升序
- feedback_stats 只统计自己的会话（他人会话的赞踩不可见）
- feedback_stats 空数据返回全 0 而非报错

用临时库：monkeypatch session._db_path，避免污染真实 data/sessions.db。
"""
import os
import sys
import time
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import session, quota  # noqa: E402


def _use_temp_db(case: unittest.TestCase) -> None:
    d = tempfile.mkdtemp(prefix="baomi_p2_")
    path = os.path.join(d, "t.db")
    case._orig = session._db_path
    session._db_path = lambda: path
    # 重建表（_init_db 幂等）
    session._init_db()
    quota._ensure_tables()


def _restore_db(case: unittest.TestCase) -> None:
    if getattr(case, "_orig", None) is not None:
        session._db_path = case._orig


class TestUsageHistory(unittest.TestCase):
    def setUp(self):
        _use_temp_db(self)

    def tearDown(self):
        _restore_db(self)

    def test_history_shape_and_zero_fill(self):
        h = quota.history("u_test", 7)
        self.assertEqual(len(h), 7)
        self.assertEqual([d["day"] for d in h], sorted(d["day"] for d in h))
        self.assertTrue(all(d["ask"] == 0 and d["embed"] == 0 for d in h))

    def test_history_records_today(self):
        quota.bump_usage("u_test", "ask", 4)
        quota.bump_usage("u_test", "embed", 12)
        h = quota.history("u_test", 7)
        self.assertEqual(h[-1]["ask"], 4)
        self.assertEqual(h[-1]["embed"], 12)

    def test_history_days_clamped(self):
        self.assertEqual(len(quota.history("u_test", 0)), 1)
        self.assertEqual(len(quota.history("u_test", 999)), 31)


class TestFeedbackStats(unittest.TestCase):
    def setUp(self):
        _use_temp_db(self)
        now = int(time.time())
        with session._get_conn() as conn:
            for sid, owner in (("s_a", "A"), ("s_b", "B")):
                conn.execute(
                    "INSERT INTO conversations(id,owner_id,title,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (sid, owner, f"会话{sid}", now, now),
                )
        session.add_feedback("A", "s_a", None, "up", "好")
        session.add_feedback("A", "s_a", None, "down", "有误")
        session.add_feedback("B", "s_b", None, "up", "")

    def tearDown(self):
        _restore_db(self)

    def test_only_own_conversations(self):
        st = session.feedback_stats("A")
        self.assertEqual(st["up"], 1)
        self.assertEqual(st["down"], 1)
        self.assertEqual(st["total"], 2)
        self.assertTrue(all(r["sid"] == "s_a" for r in st["recent"]))

    def test_empty_user(self):
        st = session.feedback_stats("NOBODY")
        self.assertEqual(st["total"], 0)
        self.assertEqual(st["recent"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
