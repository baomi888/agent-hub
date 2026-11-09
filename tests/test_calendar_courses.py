"""课表 ICS（规格 2.4）。

为什么单独一份而不是塞进「每日提醒」那份：订阅是用户自己在日历 App 里加的，
合成一份意味着「我只想要课表」的人被迫接收每日提醒，反之亦然。

这里钉住四件事（都踩过或极易踩）：
1. **RDATE 与 RRULE 的分工**：`weeks=null` 用 RRULE；给了具体周次就逐周 RDATE
   —— 「第 2、4、6 周上」不是任何 RRULE 能表达的（隔周还行，跳着不行）。
2. **课程名里的逗号必须转义**：不转义会被客户端截断在逗号处，看起来像"课程名少了一半"。
3. **CRLF**：整份必须是 \r\n，否则 Outlook/部分安卓日历直接丢内容。
4. **没设学期时给空日历、不是 404**：手机日历里显示"还没课"，
   而不是让用户以为订阅链接坏了。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import calendar as cal  # noqa: E402

SNAP = {
    "courses": [
        {
            "id": 1,
            "name": "高等数学",
            "day": 0,
            "sections": [1, 2],
            "classroom": "教三-201",
            "weeks": None,
            "teacher": "王老师",
            "notes": "",
            "color": "#c00",
        },
        {
            "id": 2,
            "name": "大学物理, 实验",
            "day": 2,
            "sections": [3],
            "classroom": "",
            "weeks": [1, 3, 5],
            "teacher": "",
            "notes": "",
            "color": "#0c0",
        },
    ],
    "term": {"start": "2026-09-07", "weeks": 18},
    "periods": {"count": 12, "starts": ["08:00", "08:55", "10:00"], "duration": 45},
}


def _lines(ics: str) -> list[str]:
    return ics.split("\r\n")


class TestShape:
    def test_crlf_only(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert ics.endswith("\r\n")
        assert "\n" not in ics.replace("\r\n", ""), "有裸 LF"

    def test_two_events(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert ics.count("BEGIN:VEVENT") == 2
        assert ics.count("END:VEVENT") == 2

    def test_calendar_name_distinguishes_from_daily(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert "X-WR-CALNAME:知穗 · 课表" in ics
        # 两份 ICS 的 PRODID 必须不同，否则某些客户端会把它们当成同一个日历
        assert "PRODID:-//ZhiSui//Course Timetable//CN" in ics

    def test_uid_stable_across_calls(self):
        a = cal.build_courses_ics("tok", SNAP)
        b = cal.build_courses_ics("tok", SNAP)
        uid_a = [row for row in a.split("\r\n") if row.startswith("UID:")]
        uid_b = [row for row in b.split("\r\n") if row.startswith("UID:")]
        assert uid_a == uid_b and len(uid_a) == 2

    def test_uid_differs_per_token(self):
        a = cal.build_courses_ics("tok-a", SNAP)
        b = cal.build_courses_ics("tok-b", SNAP)
        assert a.split("UID:")[1].split("\r\n")[0] != b.split("UID:")[1].split("\r\n")[0]


class TestSchedule:
    def test_weekly_uses_rrule_with_count(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=18" in ics
        assert "DTSTART;TZID=Asia/Shanghai:20260907T080000" in ics  # 第 1 周周一 08:00

    def test_specific_weeks_use_rdate(self):
        ics = cal.build_courses_ics("tok", SNAP)
        rdates = [row for row in _lines(ics) if row.startswith("RDATE")]
        assert len(rdates) == 1
        # day=2 是周三：第 1/3/5 周 → 09-09 / 09-23 / 10-07
        assert "20260909T100000" in rdates[0]
        assert "20260923T100000" in rdates[0]
        assert "20261007T100000" in rdates[0]
        assert "RRULE" not in rdates[0]

    def test_section_time_comes_from_periods(self):
        """第 3 节 = periods.starts[2] = 10:00。服务端只取下标，不重算作息。"""
        ics = cal.build_courses_ics("tok", SNAP)
        assert "20260909T100000" in ics

    def test_duration_is_sections_times_lesson(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert "DURATION:PT90M" in ics  # 2 节 × 45
        assert "DURATION:PT45M" in ics  # 1 节 × 45

    def test_summary_has_room(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert "SUMMARY:高等数学 · 教三-201" in ics

    def test_transp_opaque(self):
        """上课占时间，和「每日提醒」那条的 TRANSPARENT 相反。"""
        ics = cal.build_courses_ics("tok", SNAP)
        assert ics.count("TRANSP:OPAQUE") == 2
        assert "TRANSP:TRANSPARENT" not in ics


class TestEscaping:
    def test_comma_in_name_escaped(self):
        ics = cal.build_courses_ics("tok", SNAP)
        assert "SUMMARY:大学物理\\, 实验" in ics

    def test_semicolon_and_backslash_escaped(self):
        snap = {
            "courses": [{"id": 9, "name": "实验;报告\\A", "day": 1, "sections": [1], "weeks": None}],
            "term": {"start": "2026-09-07", "weeks": 2},
            "periods": {"starts": ["08:00"], "duration": 45},
        }
        ics = cal.build_courses_ics("tok", snap)
        assert "实验\\;报告\\\\A" in ics

    def test_long_summary_folded_under_75_bytes(self):
        long_name = "超长课程名" * 8
        snap = {
            "courses": [{"id": 1, "name": long_name, "day": 0, "sections": [1], "weeks": None}],
            "term": {"start": "2026-09-07", "weeks": 2},
            "periods": {"starts": ["08:00"], "duration": 45},
        }
        for line in cal.build_courses_ics("tok", snap).split("\r\n"):
            assert len(line.encode("utf-8")) <= 75, line


class TestDegenerate:
    def test_no_term_returns_empty_calendar(self):
        """没设学期 → 空日历（不是 404）。日历里显示"还没课"比"订阅失败"诚实。"""
        ics = cal.build_courses_ics("tok", {"courses": SNAP["courses"], "periods": SNAP["periods"]})
        assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
        assert "BEGIN:VEVENT" not in ics

    def test_empty_courses(self):
        ics = cal.build_courses_ics("tok", {"courses": [], "term": SNAP["term"]})
        assert "BEGIN:VEVENT" not in ics

    def test_bad_courses_do_not_kill_others(self):
        snap = {
            "courses": [
                {"id": 1, "name": "坏课", "day": 99, "sections": [1], "weeks": None},
                {"id": 2, "name": "好课", "day": 1, "sections": [1], "weeks": None},
            ],
            "term": {"start": "2026-09-07", "weeks": 4},
            "periods": {"starts": ["08:00"], "duration": 45},
        }
        ics = cal.build_courses_ics("tok", snap)
        assert ics.count("BEGIN:VEVENT") == 1
        assert "SUMMARY:好课" in ics

    def test_bad_term_start_returns_empty(self):
        ics = cal.build_courses_ics("tok", {**SNAP, "term": {"start": "不是日期", "weeks": 4}})
        assert "BEGIN:VEVENT" not in ics

    def test_accepts_plain_course_list(self):
        """前端两个键各自上报，可能只给了课程数组、没带 term —— 这时给空日历，不报错。"""
        ics = cal.build_courses_ics("tok", SNAP["courses"])
        assert "BEGIN:VCALENDAR" in ics
        assert "BEGIN:VEVENT" not in ics

    def test_term_passed_separately_wins(self):
        """term.v1 是独立上报的键，单独给也要能生成（schedule 快照里没有 term）。"""
        ics = cal.build_courses_ics(
            "tok",
            {"courses": SNAP["courses"], "periods": SNAP["periods"]},
            {"start": "2026-09-07", "weeks": 18},
        )
        assert ics.count("BEGIN:VEVENT") == 2
        assert "COUNT=18" in ics


# ==================== HTTP 层 ====================

import tempfile  # noqa: E402

import core.session as session  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="baomi_cal_courses_")
_ORIG_DB_PATH = session._db_path
session._db_path = lambda: os.path.join(_TMP, "boot.db")
session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import calendar_store, campus_store  # noqa: E402

session._db_path = _ORIG_DB_PATH


class TestEndpoint:
    def _setup(self, tmp_path, monkeypatch):
        path = tmp_path / "sessions.db"
        monkeypatch.setattr(session, "_db_path", lambda: path)
        session._init_db()
        calendar_store._READY.clear()
        campus_store.reset_cache()
        return TestClient(main.app)

    def test_public_token_serves_courses(self, tmp_path, monkeypatch):
        client = self._setup(tmp_path, monkeypatch)
        campus_store.put("u1", "campus.schedule.courses.v2", SNAP, 1000)
        token = calendar_store.get_or_create("u1")

        res = client.get(f"/api/calendar/{token}/courses.ics")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/calendar")
        body = res.text
        assert "BEGIN:VEVENT" in body
        assert "SUMMARY:高等数学 · 教三-201" in body
        # 这份不该混进「每日提醒」那条
        assert "知穗 · 看看今天的事" not in body

    def test_daily_ics_still_works(self, tmp_path, monkeypatch):
        """两条端点并列：加课表不能把老的每天提醒搞坏。"""
        client = self._setup(tmp_path, monkeypatch)
        token = calendar_store.get_or_create("u2")
        res = client.get(f"/api/calendar/{token}.ics")
        assert res.status_code == 200
        assert "知穗 · 看看今天的事" in res.text
        assert "BEGIN:VEVENT" in res.text

    def test_unknown_token_404(self, tmp_path, monkeypatch):
        client = self._setup(tmp_path, monkeypatch)
        assert client.get("/api/calendar/nonexistent-token/courses.ics").status_code == 404

    def test_no_cloud_state_gives_empty_calendar(self, tmp_path, monkeypatch):
        client = self._setup(tmp_path, monkeypatch)
        token = calendar_store.get_or_create("u3")
        res = client.get(f"/api/calendar/{token}/courses.ics")
        assert res.status_code == 200
        assert "BEGIN:VCALENDAR" in res.text
        assert "BEGIN:VEVENT" not in res.text

    def test_two_endpoints_are_independent(self, tmp_path, monkeypatch):
        """日历里可能出现两个独立日历；UID 前缀必须不同，否则会互相覆盖。"""
        client = self._setup(tmp_path, monkeypatch)
        campus_store.put("u4", "campus.schedule.courses.v2", SNAP, 1000)
        token = calendar_store.get_or_create("u4")
        daily = client.get(f"/api/calendar/{token}.ics").text
        courses = client.get(f"/api/calendar/{token}/courses.ics").text
        assert "zhisui-daily-" in daily
        assert "zhisui-course-" in courses
