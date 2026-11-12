"""上课提醒（`service/course_reminder.py`）+ 它在提醒中心里的接线。

为什么单独一份：这条链路的核心不是"能不能算出一个时间"，而是
**每分钟重建之后还对不对**。所以除了"算出下周一 07:40"这种正向断言，
更要钉住这几条：

1. `sync()` 幂等：同一分钟跑两遍，第二遍不能写库（否则每分钟 20 门课全量
   UPDATE，小库会被写热，而且没有任何收益）；
2. 提前量改了之后**必须**重算 —— `fire_at` 变了但 `next_fire_at` 没变的话，
   tick 会按旧时刻响，用户在界面上看不出哪里错；
3. 关掉开关要能把 `next_fire_at` 清掉，不能只是 `enabled=0` 就完事：
   留着旧值，下次打开时 `due_owners` 会捞出一条早就过期的提醒；
4. 课表里删掉的课要停用 —— 否则换学期之后，每周还在提醒一门已经不上的课。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import campus_store, reminder_core, session, timeutil  # noqa: E402
from service import course_reminder as cr  # noqa: E402

# 固定基准：2026-11-12 是周四。全部断言都相对它算，测试不会随真实日期漂。
NOW = int(datetime(2026, 11, 12, 10, 0, tzinfo=timeutil.TZ).timestamp())
TERM_START = "2026-09-07"  # 第 1 周周一

SNAP = {
    "courses": [
        {
            "id": "c1",
            "name": "高等数学",
            "day": 0,  # 周一
            "sections": [1, 2],
            "classroom": "教三201",
            "teacher": "李老师",
            "weeks": None,
        },
        {
            "id": "c2",
            "name": "单双周实验",
            "day": 3,  # 周四
            "sections": [3],
            "classroom": "实验楼",
            "weeks": [2, 4, 6],
        },
    ],
    "periods": {"starts": ["08:00", "08:55", "10:10"], "duration": 45},
    "term": {"start": TERM_START, "weeks": 18},
}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    campus_store.reset_cache()
    reminder_core.ensure()
    cr.ensure()
    # `sync` 内部读 now_ts，这里钉死，否则「下周一」会随真实时间跳动
    monkeypatch.setattr(timeutil, "now_ts", lambda: NOW)
    return path


def _put(owner: str = "u1", snap: dict | None = None) -> None:
    campus_store.put(owner, "campus.schedule.courses.v2", snap or SNAP, 1000)


def _rows(owner: str = "u1") -> list[dict]:
    with session._get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reminders WHERE owner_id=? AND source='timetable' ORDER BY next_fire_at",
                (owner,),
            ).fetchall()
        ]


class TestNextOccurrence:
    def test_default_lead_is_twenty_minutes(self, fresh_db):
        """用户 2026-11-12 明确要求默认课前 20 分钟。"""
        assert cr.DEFAULT_LEAD == 20
        assert cr.get_prefs("u1")["lead_minutes"] == 20
        assert cr.get_prefs("u1")["enabled"] is False  # 没开过就是关

    def test_fire_time_is_lead_before_class(self, fresh_db):
        _put()
        rows = cr.next_occurrences(SNAP, 20, NOW)
        math = [r for r in rows if r["title"] == "高等数学"][0]
        assert math["fire_at"] == math["start_at"] - 20 * 60
        # 2026-11-16 是下一个周一
        start = datetime.fromtimestamp(math["start_at"], timeutil.TZ)
        assert (start.month, start.day, start.hour, start.minute) == (11, 16, 8, 0)

    def test_lead_zero_is_class_start(self, fresh_db):
        rows = cr.next_occurrences(SNAP, 0, NOW)
        math = [r for r in rows if r["title"] == "高等数学"][0]
        assert math["fire_at"] == math["start_at"]

    def test_weeks_filter_really_skips(self, fresh_db):
        """`weeks=[2,4,6]` 的三周全在 9 月，11 月再算就该是空。

        这条是「别用 weekly 硬凑」的反证：weekly 会算出下周四，
        而实际那门课早就结课了。
        """
        rows = cr.next_occurrences(SNAP, 20, NOW)
        assert all(r["title"] != "单双周实验" for r in rows)

    def test_past_fire_is_not_emitted(self, fresh_db):
        """8:00 的课 7:58 打开页面，不该立刻补一条「20 分钟后上课」。"""
        just_before = int(datetime(2026, 11, 16, 7, 45, tzinfo=timeutil.TZ).timestamp())
        rows = cr.next_occurrences(SNAP, 20, just_before)
        math = [r for r in rows if r["title"] == "高等数学"][0]
        # 07:40 已过 → 跳过这周，落到 11-23
        assert datetime.fromtimestamp(math["start_at"], timeutil.TZ).day == 23

    def test_sorted_by_fire_time(self, fresh_db):
        rows = cr.next_occurrences(SNAP, 20, NOW)
        assert [r["fire_at"] for r in rows] == sorted(r["fire_at"] for r in rows)


class TestSync:
    def test_writes_once_row_per_course(self, fresh_db):
        _put()
        cr.set_prefs("u1", enabled=True)
        stats = cr.sync("u1")
        assert stats["written"] == 1  # 只有高等数学有未来的课

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "timetable"
        assert row["freq"] == "once"
        assert row["enabled"] == 1
        assert row["next_fire_at"] == row["fire_at"]
        assert row["title"] == "高等数学 · 教三201"
        assert row["at"] == "08:00"  # 展示用上课时刻，不是提醒时刻

    def test_sync_is_idempotent(self, fresh_db):
        """同一分钟跑两遍，第二遍不该写库。"""
        _put()
        cr.set_prefs("u1", enabled=True)
        assert cr.sync("u1")["written"] == 1
        assert cr.sync("u1")["written"] == 0
        assert len(_rows()) == 1

    def test_lead_change_moves_next_fire(self, fresh_db):
        """改提前量必须重算 `next_fire_at`，不能只改 `fire_at`。"""
        _put()
        cr.set_prefs("u1", enabled=True, lead=20)
        cr.sync("u1")
        before = _rows()[0]
        cr.set_prefs("u1", lead=45)
        cr.sync("u1")
        after = _rows()[0]
        assert after["fire_at"] == before["fire_at"] - 25 * 60
        assert after["next_fire_at"] == after["fire_at"]

    def test_disable_clears_next_fire(self, fresh_db):
        """关掉不能只置 `enabled=0`：留着 next_fire_at，下次打开会先捞出一条过期提醒。"""
        _put()
        cr.set_prefs("u1", enabled=True)
        cr.sync("u1")
        cr.set_prefs("u1", enabled=False)
        cr.sync("u1")
        rows = _rows()
        assert rows and all(r["enabled"] == 0 for r in rows)
        assert all(r["next_fire_at"] is None for r in rows)

    def test_removed_course_is_disabled(self, fresh_db):
        """换学期删掉一门课，它不许还在提醒。"""
        _put()
        cr.set_prefs("u1", enabled=True)
        cr.sync("u1")
        only_lab = {
            **SNAP,
            "courses": [SNAP["courses"][1]],  # 只剩那门已结课的单双周课
        }
        _put(snap=only_lab)
        cr.sync("u1")
        assert all(r["enabled"] == 0 for r in _rows())

    def test_stable_id_across_syncs(self, fresh_db):
        """id 必须稳定：随机 id 会让 `reminder_fires` 的主键挡不住重复派发。

        ★ 只断言 `_rows()[0]["id"]` 是不够的 —— 随机 id 会**多建一行**，
        两行的 `next_fire_at` 相同，排序后 `[0]` 未必是新的那条，
        于是这条测试在 id 随机化之后依然全绿（反证时抓到过）。
        所以先钉行数。
        """
        _put()
        cr.set_prefs("u1", enabled=True)
        cr.sync("u1")
        first = _rows()[0]["id"]
        cr.set_prefs("u1", lead=10)
        cr.sync("u1")
        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["id"] == first

    def test_list_excludes_timetable(self, fresh_db):
        """课表不该把提醒列表刷满十几条同样的课名。"""
        _put()
        cr.set_prefs("u1", enabled=True)
        cr.sync("u1")
        assert reminder_core.list_reminders("u1", "all") == []


class TestTickKeepsTheChain:
    """`freq='once'` 响过就 `enabled=0`，重建全靠 `due_subjects` 里那次 sync。

    这条链断掉的表现是「第一节提醒到了，后面的课再也不响」，
    而且界面上什么都看不出来 —— 所以必须有一条断言盯着它。
    """

    def test_next_class_is_rebuilt_after_fire(self, fresh_db, monkeypatch):
        from service import reminder_tick

        _put()
        cr.set_prefs("u1", enabled=True)
        cr.sync("u1")
        row = _rows()[0]
        assert row["enabled"] == 1

        # run_one 派发完就是这两步：记流水 + advance（once 会被停用）
        reminder_core.record_fire(
            row["id"], row["next_fire_at"], reminder_core.STATUS_SENT, None
        )
        reminder_core.advance(row["id"], row["next_fire_at"])
        assert _rows()[0]["enabled"] == 0, "once 触发后应当停用（前提条件）"

        later = int(row["next_fire_at"]) + 60
        monkeypatch.setattr(timeutil, "now_ts", lambda: later)
        reminder_tick.due_subjects(reminder_tick.JOB_KEY, later)

        rebuilt = _rows()[0]
        assert rebuilt["enabled"] == 1, "下一节课没有被重建，提醒链断了"
        assert int(rebuilt["next_fire_at"]) > int(row["next_fire_at"])

    def test_disabled_owner_is_not_synced_back(self, fresh_db, monkeypatch):
        """关掉之后 tick 不能又把它建回来 —— 否则「关不掉」。"""
        from service import reminder_tick

        _put()
        cr.set_prefs("u1", enabled=True)
        cr.sync("u1")
        cr.set_prefs("u1", enabled=False)

        later = NOW + 600
        monkeypatch.setattr(timeutil, "now_ts", lambda: later)
        reminder_tick.due_subjects(reminder_tick.JOB_KEY, later)
        assert all(r["enabled"] == 0 for r in _rows())


class TestLeadNormalisation:
    @pytest.mark.parametrize(
        "raw, want",
        [(0, 0), (20, 20), (22, 20), (7, 5), (-3, 0), (999, 60), ("x", 20)],
    )
    def test_unknown_lead_snaps_to_nearest(self, raw, want):
        """允许任意整数的话，日历那份 ICS 和这份提醒的提前量会对不上。"""
        assert cr._nearest_lead(raw) == want


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD = os.path.join(ROOT, "frontend", "src", "components", "reminders", "CourseCalendar.tsx")


class TestCardFollowsTheDevice:
    """一张卡片要伺候两种设备，所以「分叉」本身得被钉住。

    这几条都对应真实会翻车的地方：
    * 忘了按平台分叉 → 安卓用户点开一个「打开了却永远不来」的开关；
    * 先保存再要权限 → 后端开始发，但设备没有权限，一条都收不到；
    * 前端自己列一份提前量 → 和后端 `LEADS` 对不上，选 45 分钟实际按 30 算。
    """

    def _src(self) -> str:
        with open(CARD, encoding="utf-8") as fh:
            return fh.read()

    def test_switches_by_platform(self):
        src = self._src()
        assert "detectPlatform" in src
        assert "isAndroid" in src
        # 安卓那条路必须给日历文件，否则安卓彻底没有可用路径
        assert "下载日历文件" in src

    def test_asks_permission_before_saving_on(self):
        """顺序不能反：`enablePush()` 之前不许 `save({enabled:true})`。"""
        src = self._src()
        i_push = src.index("await enablePush()")
        i_save = src.index("save({ enabled: true })")
        assert i_push < i_save, "先保存再要通知权限，会出现「开着但一条都收不到」"

    def test_leads_are_not_hardcoded_by_the_page(self):
        src = self._src()
        assert "state?.leads" in src, "提前量档位必须来自后端，否则和后端 LEADS 漂移"
        assert "leads.map" in src

    def test_ios_install_hint_exists(self):
        """iPhone 没装到主屏时唯一的出路是「加到主屏幕」，这句话不能在代码里缺席。"""
        src = self._src()
        assert "添加到主屏幕" in src


class TestNotificationDestination:
    """点开通知落在哪一页。

    上课提醒点进 `/reminders` 是错的：`list_reminders` 明确排除了 timetable，
    用户点完看到的是一堆自己的待办，和那条「20 分钟后上课」毫无关系。
    """

    def _content(self, source: str) -> dict:
        from service import reminder_tick

        item = {
            "title": "心理学 · 二教A-302",
            "note": "20 分钟后上课；二教A-302",
            "channels": "push",
            "action": "notify",
            "source": source,
        }
        out = reminder_tick.build_content("u1", item, NOW)
        assert out is not None
        return out

    def test_timetable_points_to_schedule_tool(self, fresh_db):
        out = self._content("timetable")
        assert out["url"] == "/tools?tool=schedule"
        assert out["title"] == "心理学 · 二教A-302"

    def test_manual_still_points_to_reminders(self, fresh_db):
        """别的来源不能跟着改：待办点开回提醒列表才是对的。"""
        assert self._content("manual")["url"] == "/reminders"
