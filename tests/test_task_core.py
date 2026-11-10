"""待办内核：`tasks` 表 + linked reminder 的十条不变量。

每一条都对应一种「看起来对、用起来错」的写法：

1. 只设 `due_at` 却建了联动提醒 → 用户设了「周五交」,周四晚就被叫起来，
   而他从来没开过提醒；
2. 改 `due_at` 顺手重算了 `fire_at` → 用户只是想改截止日期，提醒时间被悄悄挪走；
3. 完成待办不停提醒 → 勾掉了还会响，从此不信这个勾；
4. `reopen` 无条件重启用 → 提醒时间早过了,一取消完成立刻补发一条；
5. 清空 `remind_at` 直接删行 → 送达记录跟着没了,「上次到底响没响」查不到；
6. 标题改了不同步 → 提醒弹出来还是旧名字；
7. `list_reminders` 不排除 task → 提醒页两条一模一样的「交高数作业」；
8. linked reminder 占 50 条额度 → 有 50 个带提醒的待办后建不了普通提醒；
9. 跨用户没 404 → 猜中 id 就能读别人的待办；
10. `today` 不含逾期 → 昨天该交的作业从列表里消失。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import reminder_core, session, task_core, user_prefs
from service import push as push_svc
from service import reminder_tick

TZ = ZoneInfo("Asia/Shanghai")
OWNER = "u1"


def _ts(day: int, hour: int = 0, minute: int = 0, month: int = 11) -> int:
    return int(datetime(2026, month, day, hour, minute, tzinfo=TZ).timestamp())


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    reminder_core.reset_cache()
    task_core.reset_cache()
    session._init_db()
    yield path
    reminder_core.reset_cache()
    task_core.reset_cache()


def _mk(**kwargs) -> dict:
    data = {"title": "交高数作业", "list_key": "homework"}
    data.update(kwargs)
    return data


def _linked(task_id: str) -> dict | None:
    return reminder_core.get_task_reminder(OWNER, task_id)


# ==================== 建表 ====================


class TestSchema:
    def test_fire_at_column_added(self, db):
        """老库补列：`CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列。"""
        reminder_core.ensure()
        with session._conn() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)").fetchall()}
        assert "fire_at" in cols

    def test_fire_at_backfill_on_old_db(self, db, monkeypatch):
        """模拟一个没有 fire_at 的老库：ensure() 之后必须补上，且一行不丢。"""
        reminder_core.reset_cache()
        task_core.reset_cache()
        with session._conn() as conn:
            conn.execute("DROP TABLE IF EXISTS reminders")
            conn.execute(
                "CREATE TABLE reminders (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,"
                " title TEXT NOT NULL, note TEXT, source TEXT NOT NULL DEFAULT 'manual',"
                " source_ref TEXT, override_json TEXT, freq TEXT NOT NULL DEFAULT 'daily',"
                " at TEXT, weekday INTEGER, every_minutes INTEGER,"
                " trigger_kind TEXT NOT NULL DEFAULT 'at', anchor TEXT, offset_minutes INTEGER,"
                " action TEXT NOT NULL DEFAULT 'notify', action_json TEXT,"
                " channels TEXT NOT NULL DEFAULT 'push', enabled INTEGER NOT NULL DEFAULT 1,"
                " next_fire_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
            conn.execute(
                "INSERT INTO reminders (id, owner_id, title, freq, at, created_at, updated_at)"
                " VALUES ('rm_old','u1','老提醒','daily','22:00',1,1)"
            )
        reminder_core.ensure()
        with session._conn() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(reminders)").fetchall()}
            kept = conn.execute("SELECT title, fire_at FROM reminders WHERE id='rm_old'").fetchone()
        assert "fire_at" in cols
        assert kept[0] == "老提醒"
        assert kept[1] is None

    def test_unique_index_one_task_reminder(self, db):
        """一个待办最多一条 linked reminder —— 靠唯一索引兜底，不靠调用方自觉。"""
        import sqlite3

        reminder_core.ensure()
        now = 1_800_000_000
        insert = (
            "INSERT INTO reminders (id, owner_id, title, source, source_ref, freq,"
            " channels, enabled, created_at, updated_at)"
            " VALUES (?,?,'同一条','task','tk_x','once','push',1,?,?)"
        )
        with session._conn() as conn:
            conn.execute(insert, ("rm_a", OWNER, now, now))
        with pytest.raises(sqlite3.IntegrityError):
            with session._conn() as conn:
                conn.execute(insert, ("rm_b", OWNER, now, now))


# ==================== 建 / 读 ====================


class TestCreate:
    def test_due_only_creates_no_reminder(self, db):
        task = task_core.create(OWNER, _mk(due_at=_ts(20, 23, 59)))
        assert task["due_at"] == _ts(20, 23, 59)
        assert task["remind_at"] is None
        assert _linked(task["id"]) is None

    def test_remind_creates_linked_reminder(self, db):
        remind = _ts(20, 9, 0)
        task = task_core.create(OWNER, _mk(remind_at=remind))
        linked = _linked(task["id"])
        assert linked is not None
        assert linked["source"] == "task"
        assert linked["freq"] == "once"
        assert linked["fire_at"] == remind
        assert linked["next_fire_at"] == remind
        assert linked["enabled"] is True

    def test_past_remind_rejected_and_nothing_written(self, db):
        with pytest.raises(task_core.TaskInvalid):
            task_core.create(OWNER, _mk(remind_at=_ts(1, 9, 0)))
        assert task_core.list_tasks(OWNER, "all") == []

    def test_title_trimmed_and_empty_rejected(self, db):
        task = task_core.create(OWNER, _mk(title="  写作业  "))
        assert task["title"] == "写作业"
        with pytest.raises(task_core.TaskInvalid):
            task_core.create(OWNER, _mk(title="   "))

    def test_bad_list_key_rejected(self, db):
        with pytest.raises(task_core.TaskInvalid):
            task_core.create(OWNER, _mk(list_key="作业"))

    def test_recurrence_closed_in_phase1(self, db):
        with pytest.raises(task_core.TaskInvalid):
            task_core.create(OWNER, _mk(recurrence="daily"))

    def test_parent_closed_in_phase1(self, db):
        with pytest.raises(task_core.TaskInvalid):
            task_core.create(OWNER, _mk(parent_id="tk_parent"))

    def test_limit(self, db, monkeypatch):
        monkeypatch.setattr(task_core, "MAX_TASKS_PER_OWNER", 2)
        task_core.create(OWNER, _mk(title="a"))
        task_core.create(OWNER, _mk(title="b"))
        with pytest.raises(task_core.TaskLimit):
            task_core.create(OWNER, _mk(title="c"))
        # 完成一条之后又能建：上限数的是 open，不是历史总量
        done = task_core.list_tasks(OWNER, "all")[0]
        task_core.complete(OWNER, done["id"])
        task_core.create(OWNER, _mk(title="d"))


# ==================== 改：due / remind 分开 ====================


class TestUpdate:
    def test_change_due_does_not_touch_fire_at(self, db):
        remind = _ts(20, 9, 0)
        task = task_core.create(OWNER, _mk(due_at=_ts(20, 23, 0), remind_at=remind))
        before = _linked(task["id"])

        updated = task_core.update(OWNER, task["id"], {"due_at": _ts(22, 18, 0)})

        after = _linked(task["id"])
        assert updated["due_at"] == _ts(22, 18, 0)
        assert updated["remind_at"] == remind
        assert after["fire_at"] == before["fire_at"] == remind
        assert after["next_fire_at"] == remind
        assert after["enabled"] is True

    def test_change_remind_moves_fire_at_only(self, db):
        task = task_core.create(OWNER, _mk(due_at=_ts(20, 23, 0), remind_at=_ts(20, 9, 0)))
        later = _ts(20, 10, 30)
        updated = task_core.update(OWNER, task["id"], {"remind_at": later})
        assert updated["due_at"] == _ts(20, 23, 0)
        linked = _linked(task["id"])
        assert linked["fire_at"] == later
        assert linked["next_fire_at"] == later

    def test_clear_remind_disables_but_keeps_row(self, db):
        task = task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        rid = _linked(task["id"])["id"]
        task_core.update(OWNER, task["id"], {"remind_at": None})
        linked = _linked(task["id"])
        assert linked is not None, "清空提醒时间不该删行（送达历史还要能查）"
        assert linked["enabled"] is False
        assert linked["next_fire_at"] is None
        assert linked["id"] == rid

    def test_title_syncs_to_reminder(self, db):
        task = task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        task_core.update(OWNER, task["id"], {"title": "交英语作业"})
        assert _linked(task["id"])["title"] == "交英语作业"

    def test_past_remind_on_patch_rejected(self, db):
        task = task_core.create(OWNER, _mk())
        with pytest.raises(task_core.TaskInvalid):
            task_core.update(OWNER, task["id"], {"remind_at": _ts(1, 9, 0)})

    def test_setting_remind_later_creates_it(self, db):
        task = task_core.create(OWNER, _mk())
        assert _linked(task["id"]) is None
        task_core.update(OWNER, task["id"], {"remind_at": _ts(20, 9, 0)})
        assert _linked(task["id"])["enabled"] is True


# ==================== 完成 / 取消完成 / 删除 ====================


class TestLifecycle:
    def test_complete_stops_the_reminder(self, db):
        remind = _ts(20, 9, 0)
        task = task_core.create(OWNER, _mk(due_at=remind, remind_at=remind))
        task_core.complete(OWNER, task["id"])
        assert _linked(task["id"])["enabled"] is False
        # 负向：worker 那一侧真的看不到它了
        assert reminder_core.due_for_owner(OWNER, now=remind, max_lag=60) == []

    def test_complete_is_idempotent(self, db):
        task = task_core.create(OWNER, _mk())
        first = task_core.complete(OWNER, task["id"])
        second = task_core.complete(OWNER, task["id"])
        assert first["completed_at"] == second["completed_at"]

    def test_reopen_future_remind_re_enables(self, db):
        task = task_core.create(OWNER, _mk(remind_at=_ts(25, 9, 0)))
        task_core.complete(OWNER, task["id"])
        assert _linked(task["id"])["enabled"] is False
        reopened = task_core.reopen(OWNER, task["id"])
        assert reopened["status"] == "open"
        assert reopened["completed_at"] is None
        assert _linked(task["id"])["enabled"] is True

    def test_reopen_past_remind_stays_disabled(self, db):
        remind = _ts(2, 9, 0)  # 已过去
        task = task_core.create(OWNER, _mk(remind_at=_ts(25, 9, 0)))
        # 直接把两边的提醒时间都拨到过去（创建时会被拦，模拟「放了两周没管」）
        with session._conn() as conn:
            conn.execute("UPDATE tasks SET remind_at=? WHERE id=?", (remind, task["id"]))
            conn.execute(
                "UPDATE reminders SET fire_at=?, next_fire_at=? "
                "WHERE source='task' AND source_ref=?",
                (remind, remind, task["id"]),
            )
        task_core.complete(OWNER, task["id"])
        reopened = task_core.reopen(OWNER, task["id"])
        assert reopened["status"] == "open"
        assert _linked(task["id"])["enabled"] is False
        assert reminder_core.due_for_owner(OWNER, now=remind + 60, max_lag=3600) == []

    def test_delete_removes_linked_reminder(self, db):
        task = task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        rid = _linked(task["id"])["id"]
        assert task_core.delete(OWNER, task["id"]) is True
        assert task_core.get(OWNER, task["id"]) is None
        assert reminder_core.get(OWNER, rid) is None

    def test_delete_missing_returns_false(self, db):
        assert task_core.delete(OWNER, "tk_nope") is False


# ==================== 归属隔离 ====================


class TestIsolation:
    def test_cross_user_is_none_everywhere(self, db):
        task = task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        assert task_core.get("u2", task["id"]) is None
        assert task_core.update("u2", task["id"], {"title": "改掉"}) is None
        assert task_core.complete("u2", task["id"]) is None
        assert task_core.reopen("u2", task["id"]) is None
        assert task_core.delete("u2", task["id"]) is False
        # 原主人的数据一个字节都没动
        assert task_core.get(OWNER, task["id"])["title"] == "交高数作业"


# ==================== 四个 scope ====================


class TestScopes:
    def test_today_includes_overdue(self, db):
        now = _ts(10, 12, 0)
        overdue = task_core.create(OWNER, _mk(title="昨天该交", due_at=_ts(9, 23, 0)))
        future = task_core.create(OWNER, _mk(title="下个月交", due_at=_ts(10, 10, 0, month=12)))
        titles = [i["title"] for i in task_core.list_tasks(OWNER, "today", now=now)]
        assert "昨天该交" in titles
        assert "下个月交" not in titles
        assert overdue["id"] != future["id"]

    def test_today_puts_overdue_first(self, db):
        now = _ts(10, 12, 0)
        task_core.create(OWNER, _mk(title="今天下午", due_at=_ts(10, 18, 0)))
        task_core.create(OWNER, _mk(title="前天", due_at=_ts(8, 18, 0)))
        titles = [i["title"] for i in task_core.list_tasks(OWNER, "today", now=now)]
        assert titles[0] == "前天"

    def test_remind_today_counts_as_today(self, db):
        now = _ts(10, 12, 0)
        task_core.create(OWNER, _mk(title="今晚提醒", remind_at=_ts(10, 21, 0)))
        assert [i["title"] for i in task_core.list_tasks(OWNER, "today", now=now)] == ["今晚提醒"]

    def test_upcoming_excludes_today_and_overdue(self, db):
        now = _ts(10, 12, 0)
        task_core.create(OWNER, _mk(title="今天", due_at=_ts(10, 18, 0)))
        task_core.create(OWNER, _mk(title="逾期", due_at=_ts(1, 18, 0)))
        task_core.create(OWNER, _mk(title="下周", due_at=_ts(17, 18, 0)))
        titles = [i["title"] for i in task_core.list_tasks(OWNER, "upcoming", now=now)]
        assert titles == ["下周"]

    def test_undated_only_in_all(self, db):
        now = _ts(10, 12, 0)
        task_core.create(OWNER, _mk(title="买教材"))
        assert task_core.list_tasks(OWNER, "today", now=now) == []
        assert task_core.list_tasks(OWNER, "upcoming", now=now) == []
        assert [i["title"] for i in task_core.list_tasks(OWNER, "all", now=now)] == ["买教材"]

    def test_completed_scope(self, db):
        task = task_core.create(OWNER, _mk())
        task_core.complete(OWNER, task["id"])
        assert task_core.list_tasks(OWNER, "all") == []
        assert [i["id"] for i in task_core.list_tasks(OWNER, "completed")] == [task["id"]]

    def test_all_scope_sorted_by_priority(self, db):
        task_core.create(OWNER, _mk(title="普通", priority=0))
        task_core.create(OWNER, _mk(title="紧急", priority=2))
        task_core.create(OWNER, _mk(title="重要", priority=1))
        assert [i["title"] for i in task_core.list_tasks(OWNER, "all")] == ["紧急", "重要", "普通"]


# ==================== 概览 ====================


class TestSummary:
    def test_counts_and_next(self, db):
        now = _ts(10, 12, 0)
        task_core.create(OWNER, _mk(title="今天晚点", due_at=_ts(10, 20, 0)))
        task_core.create(OWNER, _mk(title="今天早点", due_at=_ts(10, 13, 0)))
        task_core.create(OWNER, _mk(title="逾期", due_at=_ts(2, 9, 0)))
        task_core.create(OWNER, _mk(title="下周", due_at=_ts(17, 9, 0)))
        task_core.create(OWNER, _mk(title="没日期"))
        done = task_core.create(OWNER, _mk(title="做完了"))
        task_core.complete(OWNER, done["id"])

        s = task_core.summary(OWNER, now=now)
        assert s["today"] == 2
        assert s["overdue"] == 1
        assert s["upcoming"] == 1
        assert s["all_open"] == 5
        assert s["completed"] == 1
        assert s["by_list"]["homework"] == 5
        # 逾期优先：哪怕「今天 13:00」更早，也先看昨天该交的
        assert s["next"]["title"] == "逾期"

    def test_empty_summary(self, db):
        s = task_core.summary(OWNER)
        assert s["all_open"] == 0
        assert s["next"] is None


# ==================== 与提醒中心的边界 ====================


class TestReminderBoundary:
    def test_reminder_list_excludes_task(self, db):
        task_core.create(OWNER, _mk(title="带提醒的待办", remind_at=_ts(20, 9, 0)))
        reminder_core.create(OWNER, {"title": "手工提醒", "freq": "daily", "at": "22:00"})
        titles = [i["title"] for i in reminder_core.list_reminders(OWNER)]
        assert titles == ["手工提醒"]

    def test_reminder_summary_excludes_task(self, db):
        task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        s = reminder_core.summary(OWNER, now=_ts(20, 8, 0))
        assert s["today_count"] == 0
        assert s["total"] == 0

    def test_source_pref_rejects_task(self, db):
        with pytest.raises(reminder_core.ReminderInvalid):
            user_prefs.set_source_pref(OWNER, "task", False)

    def test_closing_a_source_does_not_touch_task_reminders(self, db):
        task = task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        user_prefs.set_source_pref(OWNER, "cet", False)
        user_prefs.set_source_pref(OWNER, "timetable", False)
        assert _linked(task["id"])["enabled"] is True

    def test_linked_reminder_does_not_eat_quota(self, db, monkeypatch):
        monkeypatch.setattr(reminder_core, "MAX_PER_OWNER", 1)
        reminder_core.create(OWNER, {"title": "手工提醒", "freq": "daily", "at": "22:00"})
        # 普通提醒已满，但待办的联动提醒不占额度
        task = task_core.create(OWNER, _mk(remind_at=_ts(20, 9, 0)))
        assert _linked(task["id"])["enabled"] is True
        # 但确实建不了第二条普通提醒了
        with pytest.raises(reminder_core.ReminderLimit):
            reminder_core.create(OWNER, {"title": "再来一条", "freq": "daily", "at": "21:00"})

    def test_worker_sends_and_records_sent(self, db, monkeypatch):
        remind = _ts(20, 9, 0)
        task = task_core.create(OWNER, _mk(remind_at=remind))
        calls: list[dict] = []
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda owner_id, payload: calls.append({"owner": owner_id, "payload": payload})
            or {"sent": 1, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        stats = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, remind)
        assert stats["fired"] == 1
        assert calls[0]["payload"]["title"] == "交高数作业"
        fires = reminder_core.fires(OWNER, _linked(task["id"])["id"])
        assert fires and fires[0]["status"] == "sent"

    def test_worker_stays_silent_for_due_only_task(self, db, monkeypatch):
        """只设截止日期：到点前 worker 一条都看不到（不响是**对的**）。"""
        remind = _ts(20, 9, 0)
        task = task_core.create(OWNER, _mk(due_at=remind))
        monkeypatch.setattr(
            push_svc, "send_to_user", lambda *a, **k: {"sent": 0, "failed": 0, "gone": 0}
        )
        assert reminder_core.due_for_owner(OWNER, now=remind, max_lag=60) == []
        assert _linked(task["id"]) is None

    def test_once_advance_disables(self, db):
        remind = _ts(20, 9, 0)
        task = task_core.create(OWNER, _mk(remind_at=remind))
        rid = _linked(task["id"])["id"]
        assert reminder_core.advance(rid, remind) is None
        linked = _linked(task["id"])
        assert linked["enabled"] is False
        assert linked["next_fire_at"] is None
