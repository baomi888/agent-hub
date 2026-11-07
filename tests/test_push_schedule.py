"""提醒时刻：窗口判断 + 偏好落库 + ICS 起点。

这三件事各自都有"看起来对、用起来错"的写法，所以分开测：

1. `matches_time` 的窗口边界（提前一跳不能发、晚一跳要补发、超窗要放弃）；
2. 偏好落库（默认值不写库、分钟吸附、一人一行覆盖）；
3. ICS 的 DTSTART 必须等于用户设的时刻（设置页显示 07:30 而日历落在 20:00
   是最容易被当成"改了没生效"的那类 bug）。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import reminder_store, session
from service import calendar as cal
from service import push

TZ = ZoneInfo("Asia/Shanghai")


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


# ==================== 窗口判断 ====================


class TestMatchesTime:
    def test_exact_hit(self):
        now = datetime(2026, 11, 7, 7, 30, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 7, 30) is True

    def test_previous_tick_is_not_due(self):
        """7:15 那一跳不该发给设在 7:30 的人 —— 早发等于催。"""
        now = datetime(2026, 11, 7, 7, 15, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 7, 30) is False

    def test_next_tick_catches_up(self):
        """7:45 那一跳要补上 7:30 —— 上一跳 worker 没跑成时靠这个兜。"""
        now = datetime(2026, 11, 7, 7, 45, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 7, 30) is True

    def test_grace_window_inclusive_at_2h(self):
        now = datetime(2026, 11, 7, 9, 30, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 7, 30) is True

    def test_beyond_grace_gives_up(self):
        """超过 2 小时就不补了：晚上 9 点收到"早上 7 点该做的事"是骚扰。"""
        now = datetime(2026, 11, 7, 9, 45, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 7, 30) is False

    def test_different_time_same_tick(self):
        """同一跳里，只有设了这个点的人才命中，别人不能被顺带喊醒。"""
        now = datetime(2026, 11, 7, 20, 0, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 20, 0) is True
        assert push.matches_time(now, _ts(now), 7, 30) is False
        assert push.matches_time(now, _ts(now), 21, 0) is False

    def test_midnight_boundary_known_limitation(self):
        """已知边界，写成断言是为了让它是"决定"而不是"哪天发现的 bug"：
        设 23:45 的人，23:45 那跳没跑成，次日 00:00 不会补发。"""
        now = datetime(2026, 11, 8, 0, 0, tzinfo=TZ)
        assert push.matches_time(now, _ts(now), 23, 45) is False


# ==================== 偏好落库 ====================


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """每个用例一个独立库。键按路径缓存，所以新路径自然会重新建表。"""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: db)
    reminder_store._READY.clear()
    yield db
    reminder_store._READY.clear()


class TestReminderStore:
    def test_default_does_not_write_row(self, fresh_db):
        """没设置过的人不该因为"打开设置页"就在库里多一行。"""
        assert reminder_store.get("u1") == reminder_store.default_time()
        with session._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM reminder_prefs").fetchone()[0]
        assert n == 0

    def test_set_and_get_roundtrip(self, fresh_db):
        assert reminder_store.set_time("u1", 7, 30) == (7, 30)
        assert reminder_store.get("u1") == (7, 30)

    def test_minute_snaps_to_steps(self, fresh_db):
        """7 分吸附到 0 分（比 15 更近）。不吸附的话这个人永远不会响。"""
        hour, minute = reminder_store.set_time("u1", 7, 7)
        assert (hour, minute) == (7, 0)
        assert minute in reminder_store.MINUTE_STEPS

    def test_hour_clamped(self, fresh_db):
        assert reminder_store.set_time("u1", 99, 0) == (23, 0)
        assert reminder_store.set_time("u1", -5, 0) == (0, 0)

    def test_one_row_per_owner(self, fresh_db):
        reminder_store.set_time("u1", 7, 0)
        reminder_store.set_time("u1", 21, 45)
        assert reminder_store.get("u1") == (21, 45)
        with session._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM reminder_prefs").fetchone()[0]
        assert n == 1

    def test_all_prefs_bulk(self, fresh_db):
        reminder_store.set_time("u1", 7, 0)
        reminder_store.set_time("u2", 21, 30)
        got = reminder_store.all_prefs()
        assert got == {"u1": (7, 0), "u2": (21, 30)}


# ==================== ICS 起点 ====================


class TestCalendarUsesChosenTime:
    def test_dtstart_follows_argument(self):
        ics = cal.build_ics("a" * 32, 7, 30)
        assert "DTSTART;TZID=Asia/Shanghai:" in ics
        assert "T073000" in ics

    def test_default_argument_still_works(self):
        """不传参数的老调用方（测试、样例生成）仍要能跑。"""
        ics = cal.build_ics("a" * 32)
        assert "DTSTART;TZID=Asia/Shanghai:" in ics

    def test_describe_matches_ics(self):
        """设置页显示的时刻必须和 ICS 里的同一份 —— 两处不一致就是"改了没生效"。"""
        info = cal.describe("a" * 32, 7, 30)
        assert info["time"] == "07:30"
        assert "T073000" in cal.build_ics("a" * 32, 7, 30)

    def test_rrule_and_alarm_unchanged(self):
        """改时间不该动重复规则和提醒触发点。"""
        ics = cal.build_ics("a" * 32, 7, 30)
        assert "RRULE:FREQ=DAILY" in ics
        assert "TRIGGER:PT0M" in ics
        assert "DURATION:PT10M" in ics

    def test_line_folding_still_safe(self):
        """换了时刻以后仍要满足 75 字节折行（中文描述最容易踩）。"""
        ics = cal.build_ics("a" * 32, 7, 30)
        assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n"))
