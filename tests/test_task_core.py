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

from core import reminder_core, session, task_core, timeutil, user_prefs
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
                "UPDATE reminders SET fire_at=?, next_fire_at=? WHERE source='task' AND source_ref=?",
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

    def test_remind_today_counts_as_today(self, db, monkeypatch):
        now = _ts(10, 12, 0)
        # create() 内部取的是真实时钟，`_check_remind` 会把"已经过去"的提醒时间挡掉。
        # 这条测试固定造 11-10 的数据，但只在 11-10 那天真实时钟才落在它前面 ——
        # 不把 now 钉住，跨过 11-10 之后它每天都红，看起来像产品坏了。
        monkeypatch.setattr(timeutil, "now_ts", lambda: now)
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


# ==================== 手动排序 ====================


class TestManualOrder:
    """`sort_order` 的三条硬约束：0 是「没排过」、只写传入的 id、跨用户写不进。"""

    def test_zero_is_not_the_first_position(self, db):
        """老数据全是 0。若把 0 当第 0 位，一拖任何一条，全部历史待办会挤到它上面。"""
        a = task_core.create(OWNER, _mk(title="没排过"))
        b = task_core.create(OWNER, _mk(title="手动排到第一"))
        task_core.reorder(OWNER, [b["id"]])
        titles = [i["title"] for i in task_core.list_tasks(OWNER, "all")]
        assert titles == ["手动排到第一", "没排过"]
        assert task_core.get(OWNER, a["id"])["sort_order"] == 0
        assert task_core.get(OWNER, b["id"])["sort_order"] == 10

    def test_manual_order_beats_priority(self, db):
        """手动排过的压过优先级：用户亲手挪的位置不能被自动规则翻回去。"""
        low = task_core.create(OWNER, _mk(title="普通", priority=0))
        task_core.create(OWNER, _mk(title="紧急", priority=2))
        assert [i["title"] for i in task_core.list_tasks(OWNER, "all")] == ["紧急", "普通"]
        task_core.reorder(OWNER, [low["id"]])
        assert [i["title"] for i in task_core.list_tasks(OWNER, "all")] == ["普通", "紧急"]

    def test_reorder_only_writes_given_ids(self, db):
        """列表可能被 scope 过滤过 —— 组外那几条在别的视图里还有自己的顺序。"""
        a = task_core.create(OWNER, _mk(title="A", due_at=_ts(17, 9, 0)))
        b = task_core.create(OWNER, _mk(title="B", due_at=_ts(18, 9, 0)))
        outside = task_core.create(OWNER, _mk(title="不在本组", due_at=_ts(19, 9, 0)))
        task_core.update(OWNER, outside["id"], {"sort_order": 99})

        assert task_core.reorder(OWNER, [b["id"], a["id"]]) == 2
        assert task_core.get(OWNER, b["id"])["sort_order"] == 10
        assert task_core.get(OWNER, a["id"])["sort_order"] == 20
        # 组外那条一个字节都没动
        assert task_core.get(OWNER, outside["id"])["sort_order"] == 99

    def test_reorder_is_owner_scoped(self, db):
        other = task_core.create("u2", _mk(title="别人的"))
        assert task_core.reorder(OWNER, [other["id"]]) == 0
        assert task_core.get("u2", other["id"])["sort_order"] == 0

    def test_reorder_empty_list_is_noop(self, db):
        task_core.create(OWNER, _mk())
        assert task_core.reorder(OWNER, []) == 0

    def test_order_holds_inside_today(self, db):
        """`today` 的 ORDER BY 也带手动序，否则挪完切个 tab 又乱回去。"""
        now = _ts(10, 12, 0)
        early = task_core.create(OWNER, _mk(title="下午一点", due_at=_ts(10, 13, 0)))
        late = task_core.create(OWNER, _mk(title="晚上十点", due_at=_ts(10, 22, 0)))
        assert [i["title"] for i in task_core.list_tasks(OWNER, "today", now=now)] == [
            "下午一点",
            "晚上十点",
        ]
        task_core.reorder(OWNER, [late["id"], early["id"]])
        assert [i["title"] for i in task_core.list_tasks(OWNER, "today", now=now)] == [
            "晚上十点",
            "下午一点",
        ]

    def test_update_accepts_zero_to_clear(self, db):
        task = task_core.create(OWNER, _mk())
        task_core.update(OWNER, task["id"], {"sort_order": 30})
        assert task_core.get(OWNER, task["id"])["sort_order"] == 30
        # 显式传 0 = 退出手动排序（落回自动规则），不是「排到最前」
        task_core.update(OWNER, task["id"], {"sort_order": 0})
        assert task_core.get(OWNER, task["id"])["sort_order"] == 0

    def test_negative_sort_order_rejected(self, db):
        task = task_core.create(OWNER, _mk())
        with pytest.raises(task_core.TaskInvalid):
            task_core.update(OWNER, task["id"], {"sort_order": -1})


# ==================== 批量 ====================


class TestBulk:
    def test_complete_marks_all_and_stops_reminders(self, db):
        """批量完成必须连带停用联动提醒 —— 单条 complete 是这么做的，批量很容易漏。"""
        remind = _ts(20, 9, 0)
        a = task_core.create(OWNER, _mk(title="A", remind_at=remind))
        b = task_core.create(OWNER, _mk(title="B", remind_at=remind))
        c = task_core.create(OWNER, _mk(title="C", remind_at=remind))
        assert len(reminder_core.due_for_owner(OWNER, now=remind)) == 3

        assert task_core.bulk(OWNER, [a["id"], b["id"]], "complete") == 2
        assert task_core.get(OWNER, a["id"])["status"] == "done"
        assert task_core.get(OWNER, a["id"])["completed_at"] is not None
        # C 没被选中，照旧响
        assert [i["title"] for i in reminder_core.due_for_owner(OWNER, now=remind)] == ["C"]
        assert _linked(a["id"])["enabled"] is False
        assert _linked(c["id"])["enabled"] is True

    def test_complete_skips_already_done(self, db):
        a = task_core.create(OWNER, _mk(title="A"))
        b = task_core.create(OWNER, _mk(title="B"))
        task_core.complete(OWNER, a["id"])
        assert task_core.bulk(OWNER, [a["id"], b["id"]], "complete") == 1

    def test_reopen_does_not_resurrect_past_reminder(self, db):
        """提醒时间早就过了：重开只回到列表，不许补发一条。"""
        past = _ts(10, 9, 0)
        a = task_core.create(OWNER, _mk(title="A", remind_at=_ts(20, 9, 0)))
        task_core.complete(OWNER, a["id"])
        task_core.bulk(OWNER, [a["id"]], "reopen")
        assert task_core.get(OWNER, a["id"])["status"] == "open"
        assert task_core.get(OWNER, a["id"])["completed_at"] is None
        assert _linked(a["id"])["enabled"] is True

        # 把提醒挪到过去（直接改库，模拟时间流逝），再走一轮重开
        with session._conn() as conn:
            conn.execute("UPDATE tasks SET remind_at=? WHERE id=?", (past, a["id"]))
        task_core.complete(OWNER, a["id"])
        task_core.bulk(OWNER, [a["id"]], "reopen")
        assert _linked(a["id"])["enabled"] is False

    def test_move_needs_explicit_list_key(self, db):
        a = task_core.create(OWNER, _mk(title="A"))
        with pytest.raises(task_core.TaskInvalid):
            task_core.bulk(OWNER, [a["id"]], "move")
        assert task_core.get(OWNER, a["id"])["list_key"] == "homework"

    def test_move_changes_list(self, db):
        a = task_core.create(OWNER, _mk(title="A"))
        b = task_core.create(OWNER, _mk(title="B"))
        assert task_core.bulk(OWNER, [a["id"], b["id"]], "move", "inbox") == 2
        assert task_core.get(OWNER, a["id"])["list_key"] == "inbox"
        by_list = task_core.summary(OWNER)["by_list"]
        assert by_list["inbox"] == 2
        assert by_list["homework"] == 0

    def test_delete_removes_linked_reminder(self, db):
        a = task_core.create(OWNER, _mk(title="A", remind_at=_ts(20, 9, 0)))
        b = task_core.create(OWNER, _mk(title="B", remind_at=_ts(20, 9, 0)))
        assert task_core.bulk(OWNER, [a["id"]], "delete") == 1
        assert task_core.get(OWNER, a["id"]) is None
        assert _linked(a["id"]) is None
        assert _linked(b["id"]) is not None

    def test_cross_user_ids_are_ignored(self, db):
        mine = task_core.create(OWNER, _mk(title="我的"))
        theirs = task_core.create("u2", _mk(title="别人的"))
        assert task_core.bulk(OWNER, [mine["id"], theirs["id"]], "complete") == 1
        assert task_core.get("u2", theirs["id"])["status"] == "open"
        assert task_core.get(OWNER, mine["id"])["status"] == "done"

    def test_all_ids_foreign_returns_zero(self, db):
        theirs = task_core.create("u2", _mk(title="别人的"))
        assert task_core.bulk(OWNER, [theirs["id"]], "delete") == 0
        assert task_core.get("u2", theirs["id"]) is not None

    def test_duplicate_ids_counted_once(self, db):
        a = task_core.create(OWNER, _mk(title="A"))
        assert task_core.bulk(OWNER, [a["id"], a["id"], a["id"]], "complete") == 1

    def test_unknown_action_rejected(self, db):
        a = task_core.create(OWNER, _mk(title="A"))
        with pytest.raises(task_core.TaskInvalid):
            task_core.bulk(OWNER, [a["id"]], "archive")

    def test_empty_ids_is_noop(self, db):
        assert task_core.bulk(OWNER, [], "complete") == 0
        assert task_core.bulk(OWNER, [], "move", "inbox") == 0


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
            lambda owner_id, payload: (
                calls.append({"owner": owner_id, "payload": payload})
                or {"sent": 1, "failed": 0, "gone": 0, "skipped": 0, "items": []}
            ),
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
        monkeypatch.setattr(push_svc, "send_to_user", lambda *a, **k: {"sent": 0, "failed": 0, "gone": 0})
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


class TestPriorityWidening:
    """三档优先级的库升到四档（2026-11-11）。

    SQLite 不能 ALTER 一条 CHECK 约束，只能整表重建。两件事都是**静默**失败：
      - 不重建：写 3 撞 CHECK，用户选了「紧急」保存不上；
      - 重建但原样搬：老的 1=重要 悄悄降成「日常」、2=紧急 降成「重要」。
    还有一件更隐蔽的：先改名再建表时，旧索引的名字还占着，
    `CREATE INDEX IF NOT EXISTS` 会静默跳过，最后 DROP 旧表把索引一起带走。
    """

    _LEGACY = """
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, note TEXT,
        list_key TEXT NOT NULL DEFAULT 'inbox', icon TEXT,
        priority INTEGER NOT NULL DEFAULT 0 CHECK (priority IN (0, 1, 2)),
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
        due_at INTEGER, remind_at INTEGER, completed_at INTEGER,
        recurrence TEXT NOT NULL DEFAULT 'none', parent_id TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    );
    CREATE INDEX idx_tasks_owner_status_due ON tasks(owner_id, status, due_at);
    INSERT INTO tasks VALUES ('a', 'u1', '普通', NULL, 'inbox', NULL, 0, 'open',
                              NULL, NULL, NULL, 'none', NULL, 0, 1, 1);
    INSERT INTO tasks VALUES ('b', 'u1', '重要', NULL, 'inbox', NULL, 1, 'open',
                              NULL, NULL, NULL, 'none', NULL, 0, 1, 1);
    INSERT INTO tasks VALUES ('c', 'u1', '紧急', NULL, 'inbox', NULL, 2, 'done',
                              NULL, NULL, 1, 'none', NULL, 0, 1, 1);
    """

    def test_levels_are_shifted_not_moved_as_is(self, tmp_path, monkeypatch):
        import sqlite3

        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(self._LEGACY)
        conn.commit()
        conn.close()

        monkeypatch.setattr(session, "_db_path", lambda: path)
        task_core.reset_cache()

        got = {t["title"]: t["priority"] for t in task_core.list_tasks(OWNER, "all")}
        assert got == {"普通": 0, "重要": 2}, "1 档没平移到 2（重要）"
        assert task_core.get(OWNER, "c")["priority"] == 3, "2 档没平移到 3（紧急）"
        # 4 档之后 3 必须写得进去
        assert task_core.create(OWNER, _mk(priority=3))["priority"] == 3

    def test_indexes_survive_the_rebuild(self, tmp_path, monkeypatch):
        import sqlite3

        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(self._LEGACY)
        conn.commit()
        conn.close()

        monkeypatch.setattr(session, "_db_path", lambda: path)
        task_core.reset_cache()
        task_core.ensure()

        conn = sqlite3.connect(path)
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tasks'"
            )
        }
        conn.close()
        assert {
            "idx_tasks_owner_status_due",
            "idx_tasks_owner_remind",
            "idx_tasks_owner_created",
        } <= names, "重建表把索引弄丢了，查询会退化成全表扫"

    def test_migration_is_idempotent(self, tmp_path, monkeypatch):
        """重启两次不能再搬一遍（那会把 2 一直往上推）。"""
        import sqlite3

        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(self._LEGACY)
        conn.commit()
        conn.close()

        monkeypatch.setattr(session, "_db_path", lambda: path)
        for _ in range(3):
            task_core.reset_cache()
            task_core.ensure()
        assert task_core.get(OWNER, "b")["priority"] == 2
