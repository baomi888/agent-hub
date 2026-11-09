"""用户时间偏好：静默时段（跨零点是主路径）、每日上限计数、来源开关。

静默时段这块最典型的错误写法是 `start <= now < end`。它在 09:00–18:00 这类
配置下完全正确，在 23:00–07:00（最常见的配置）下永远为假 ——
表现是「设了静默，夜里照样被吵醒」。所以跨零点必须在**主路径**上被测到。

每日上限的计数口径也容易错：同刻合并时被并入的那几条也写 `sent`，
如果直接 COUNT，一个合并通知会被算成三条，上限提前用光。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import reminder_core, session, user_prefs

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: db)
    reminder_core.reset_cache()
    session._init_db()
    yield db
    reminder_core.reset_cache()


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 9, hour, minute, tzinfo=TZ)


# ==================== 静默时段 ====================


class TestQuietHours:
    def test_crossing_midnight_inside_late(self):
        prefs = {"quiet_start": "23:00", "quiet_end": "07:00"}
        assert user_prefs.in_quiet_hours(_at(23, 30), prefs) is True

    def test_crossing_midnight_inside_early(self):
        prefs = {"quiet_start": "23:00", "quiet_end": "07:00"}
        assert user_prefs.in_quiet_hours(_at(2, 0), prefs) is True

    def test_crossing_midnight_start_is_inclusive(self):
        prefs = {"quiet_start": "23:00", "quiet_end": "07:00"}
        assert user_prefs.in_quiet_hours(_at(23, 0), prefs) is True

    def test_crossing_midnight_end_is_exclusive(self):
        """设 07:00 就是想起床：07:00 整不该还被判成静默，否则那条永远不响。"""
        prefs = {"quiet_start": "23:00", "quiet_end": "07:00"}
        assert user_prefs.in_quiet_hours(_at(7, 0), prefs) is False

    def test_crossing_midnight_outside(self):
        prefs = {"quiet_start": "23:00", "quiet_end": "07:00"}
        assert user_prefs.in_quiet_hours(_at(12, 0), prefs) is False

    def test_non_crossing(self):
        prefs = {"quiet_start": "12:00", "quiet_end": "14:00"}
        assert user_prefs.in_quiet_hours(_at(13, 0), prefs) is True
        assert user_prefs.in_quiet_hours(_at(11, 59), prefs) is False
        assert user_prefs.in_quiet_hours(_at(14, 0), prefs) is False

    def test_same_start_end_means_off(self):
        prefs = {"quiet_start": "00:00", "quiet_end": "00:00"}
        assert user_prefs.in_quiet_hours(_at(3, 0), prefs) is False

    def test_empty_means_off(self):
        assert user_prefs.in_quiet_hours(_at(3, 0), {"quiet_start": "", "quiet_end": ""}) is False


# ==================== 偏好读写 ====================


class TestPrefsStore:
    def test_defaults_without_row(self, fresh_db):
        prefs = user_prefs.get_time_prefs("u1")
        assert prefs["daily_cap"] == 6
        assert prefs["quiet_start"] == "23:00"
        with session._conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM user_time_prefs").fetchone()[0]
        assert n == 0  # 读不写库

    def test_roundtrip(self, fresh_db):
        user_prefs.set_time_prefs("u1", {"quiet_start": "22:30", "quiet_end": "06:30", "daily_cap": 3})
        prefs = user_prefs.get_time_prefs("u1")
        assert (prefs["quiet_start"], prefs["quiet_end"], prefs["daily_cap"]) == ("22:30", "06:30", 3)

    def test_partial_update_keeps_other_fields(self, fresh_db):
        user_prefs.set_time_prefs("u1", {"daily_cap": 2})
        user_prefs.set_time_prefs("u1", {"quiet_start": "01:00"})
        prefs = user_prefs.get_time_prefs("u1")
        assert prefs["daily_cap"] == 2
        assert prefs["quiet_start"] == "01:00"

    @pytest.mark.parametrize("cap", [-1, 31])
    def test_cap_out_of_range(self, fresh_db, cap):
        with pytest.raises(reminder_core.ReminderInvalid):
            user_prefs.set_time_prefs("u1", {"daily_cap": cap})

    def test_bad_clock_rejected(self, fresh_db):
        with pytest.raises(reminder_core.ReminderInvalid):
            user_prefs.set_time_prefs("u1", {"quiet_start": "24:00"})

    def test_active_windows_roundtrip(self, fresh_db):
        windows = [{"weekday": [1, 2, 3], "start": "19:00", "end": "22:00"}]
        user_prefs.set_time_prefs("u1", {"active_windows": windows})
        assert user_prefs.get_time_prefs("u1")["active_windows"] == windows


# ==================== 来源开关 ====================


class TestSources:
    def test_default_all_off(self, fresh_db):
        assert user_prefs.source_prefs("u1") == {}
        assert user_prefs.source_enabled("u1", "cet") is False

    def test_toggle_persists(self, fresh_db):
        user_prefs.set_source_pref("u1", "timetable", True)
        assert user_prefs.source_enabled("u1", "timetable") is True
        user_prefs.set_source_pref("u1", "timetable", False)
        assert user_prefs.source_enabled("u1", "timetable") is False

    def test_off_keeps_row(self, fresh_db):
        """关掉写 enabled=0 而不是删行：不然「他什么时候关的」查不出来。"""
        user_prefs.set_source_pref("u1", "focus", True)
        user_prefs.set_source_pref("u1", "focus", False)
        with session._conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM reminder_source_prefs").fetchone()[0]
        assert n == 1

    def test_unknown_source_rejected(self, fresh_db):
        with pytest.raises(reminder_core.ReminderInvalid):
            user_prefs.set_source_pref("u1", "wechat", True)

    def test_system_source_not_configurable(self, fresh_db):
        """system 是老「每日回顾」的内部来源，不该出现在七个开关里。"""
        with pytest.raises(reminder_core.ReminderInvalid):
            user_prefs.set_source_pref("u1", "system", True)


# ==================== 每日上限计数 ====================


class TestDayCount:
    def _fire(self, rid: str, planned: int, status: str, detail: str | None = None) -> None:
        reminder_core.record_fire(rid, planned, status, detail)

    def test_counts_only_sent(self, fresh_db):
        a = reminder_core.create("u1", {"title": "A", "at": "08:00"})
        b = reminder_core.create("u1", {"title": "B", "at": "09:00"})
        now = int(datetime(2026, 11, 9, 12, 0, tzinfo=TZ).timestamp())
        self._fire(a["id"], now, reminder_core.STATUS_SENT)
        self._fire(b["id"], now - 60, reminder_core.STATUS_QUIET, "quiet_hours")
        assert user_prefs.day_count("u1", "2026-11-09") == 1

    def test_merged_rows_are_not_counted_again(self, fresh_db):
        """合并通知算一条：漏了这条，上限会提前用光，用户觉得「没收到几条就不响了」。"""
        a = reminder_core.create("u1", {"title": "A", "at": "08:00"})
        b = reminder_core.create("u1", {"title": "B", "at": "08:00"})
        now = int(datetime(2026, 11, 9, 8, 0, tzinfo=TZ).timestamp())
        self._fire(a["id"], now, reminder_core.STATUS_SENT)
        self._fire(b["id"], now, reminder_core.STATUS_SENT, f"merged_into:{a['id']}@{now}")
        assert user_prefs.day_count("u1", "2026-11-09") == 1

    def test_other_day_and_other_owner_excluded(self, fresh_db):
        a = reminder_core.create("u1", {"title": "A", "at": "08:00"})
        b = reminder_core.create("u2", {"title": "B", "at": "08:00"})
        today = int(datetime(2026, 11, 9, 8, 0, tzinfo=TZ).timestamp())
        self._fire(a["id"], today, reminder_core.STATUS_SENT)
        self._fire(b["id"], today, reminder_core.STATUS_SENT)
        self._fire(a["id"], today - 86400, reminder_core.STATUS_SENT)
        assert user_prefs.day_count("u1", "2026-11-09") == 1
