"""提醒中心内核：时间推算 / CRUD / override 合并 / 老数据迁移。

为什么这些必须单独测（每条都对应一个"看起来对、用起来错"的写法）：

1. `next_fire` 的 `interval` 分支：从 `now` 起算 → 每分钟一跳把下一次一直往后推，
   提醒永远不响；只能从原来的 `next_fire_at` 递推。
2. `weekdays` 不跳周末 → 周六早上收到"课前 10 分钟"，而那天没课。
3. 改标题也重算 `next_fire_at` → 今天 22:00 的提醒被推到明天 22:00，
   用户点一下"改个名字"就丢了今天这一次。
4. 迁移跑两遍 → 同一时刻两条"每日回顾"，每天响两遍。
5. override 写回 `delegations` → 用户在提醒里改个标题，委托页的原文也变了。
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import reminder_core, session, timeutil

TZ = ZoneInfo("Asia/Shanghai")


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp())


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """每个用例一个独立库。建表缓存按路径存，所以必须显式清。"""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: db)
    reminder_core.reset_cache()
    session._init_db()
    yield db
    reminder_core.reset_cache()


def _mk(**kwargs) -> dict:
    """默认一条最简单的每日提醒，用例只覆盖自己关心的字段。"""
    data = {"title": "复盘今天", "freq": "daily", "at": "22:00", "action": "notify"}
    data.update(kwargs)
    return data


# ==================== 时间推算 ====================


class TestNextFire:
    def test_daily_later_today(self):
        now = _ts(2026, 11, 9, 21, 0)  # 周一 21:00
        assert reminder_core.next_fire({"freq": "daily", "at": "22:00"}, now) == _ts(
            2026, 11, 9, 22, 0
        )

    def test_daily_already_passed_goes_tomorrow(self):
        now = _ts(2026, 11, 9, 21, 0)
        assert reminder_core.next_fire({"freq": "daily", "at": "20:00"}, now) == _ts(
            2026, 11, 10, 20, 0
        )

    def test_exact_minute_is_not_reused(self):
        """"等于 now" 不返回：这一次已经在发了，再返回就会同一分钟无限触发。"""
        now = _ts(2026, 11, 9, 22, 0)
        assert reminder_core.next_fire({"freq": "daily", "at": "22:00"}, now) == _ts(
            2026, 11, 10, 22, 0
        )

    def test_weekdays_skips_weekend(self):
        # 2026-11-13 是周五，14/15 是周末
        now = _ts(2026, 11, 13, 23, 0)
        got = reminder_core.next_fire({"freq": "weekdays", "at": "07:00"}, now)
        assert got == _ts(2026, 11, 16, 7, 0), datetime.fromtimestamp(got, TZ)

    def test_weekly_lands_on_given_weekday(self):
        now = _ts(2026, 11, 9, 10, 0)  # 周一
        got = reminder_core.next_fire({"freq": "weekly", "weekday": 6, "at": "09:00"}, now)
        assert got == _ts(2026, 11, 15, 9, 0), datetime.fromtimestamp(got, TZ)  # 周日

    def test_interval_from_anchor_not_now(self):
        """interval 必须从 next_fire_at 递推。

        反例（从 now 起算）会让每次 tick 都把下一次推后一小时，永远不响 ——
        这是"设了但从来不响"里最难查的一种，因为字段全都看着正常。
        """
        anchor = _ts(2026, 11, 9, 10, 0)
        now = anchor + 30  # 刚触发 30 秒后
        got = reminder_core.next_fire(
            {"freq": "interval", "every_minutes": 60, "next_fire_at": anchor}, now
        )
        assert got == anchor + 3600

    def test_interval_catches_up_after_downtime(self):
        """停机 2.5 小时后恢复：跳到下一个还没过的点，而不是补发两次。"""
        anchor = _ts(2026, 11, 9, 10, 0)
        now = anchor + int(2.5 * 3600)
        got = reminder_core.next_fire(
            {"freq": "interval", "every_minutes": 60, "next_fire_at": anchor}, now
        )
        assert got == anchor + 3 * 3600

    def test_missing_at_returns_none(self):
        """算不出来就返回 None，绝不猜一个时间 —— 猜错就是"半夜乱响"。"""
        assert reminder_core.next_fire({"freq": "daily", "at": None}, _ts(2026, 11, 9, 10, 0)) is None


# ==================== 创建与校验 ====================


class TestCreate:
    def test_minimal_manual_reminder(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        assert item["source"] == "manual"
        assert item["freq"] == "daily"
        assert item["at"] == "22:00"
        assert item["enabled"] is True
        assert item["next_fire_at"] and item["next_fire_at"] > time.time() - 5

    def test_once_is_accepted(self, fresh_db):
        """规格新增的唯一一个 freq。委托侧没有它（没有"只此一次"的需求）。"""
        item = reminder_core.create("u1", _mk(freq="once", at="23:30"))
        assert item["freq"] == "once"
        assert item["next_fire_at"] is not None

    def test_limit_is_50(self, fresh_db):
        for i in range(reminder_core.MAX_PER_OWNER):
            reminder_core.create("u1", _mk(title=f"提醒{i}"))
        with pytest.raises(reminder_core.ReminderLimit):
            reminder_core.create("u1", _mk(title="第 51 条"))

    def test_limit_is_per_owner(self, fresh_db):
        for i in range(reminder_core.MAX_PER_OWNER):
            reminder_core.create("u1", _mk(title=f"提醒{i}"))
        assert reminder_core.create("u2", _mk())["owner_id"] == "u2"

    @pytest.mark.parametrize("bad", ["25:00", "7:5", "", "abc", "22:60"])
    def test_bad_time_rejected(self, fresh_db, bad):
        with pytest.raises(reminder_core.ReminderInvalid):
            reminder_core.create("u1", _mk(at=bad))

    def test_weekly_requires_weekday(self, fresh_db):
        with pytest.raises(reminder_core.ReminderInvalid):
            reminder_core.create("u1", _mk(freq="weekly", weekday=None))

    def test_interval_requires_every_minutes(self, fresh_db):
        with pytest.raises(reminder_core.ReminderInvalid):
            reminder_core.create("u1", _mk(freq="interval", at=None))

    def test_unknown_channel_rejected(self, fresh_db):
        with pytest.raises(reminder_core.ReminderInvalid):
            reminder_core.create("u1", _mk(channels="push,telegram"))

    def test_relative_trigger_rejected_for_now(self, fresh_db):
        """第一波明确不支持相对时间：锚点数据还在 localStorage，收下就等于静默不响。"""
        with pytest.raises(reminder_core.ReminderInvalid) as ei:
            reminder_core.create("u1", _mk(trigger_kind="relative"))
        assert "相对时间" in str(ei.value)

    def test_duplicate_channels_are_deduped(self, fresh_db):
        item = reminder_core.create("u1", _mk(channels="push, push,inapp"))
        assert item["channels"] == "push,inapp"


# ==================== 更新 ====================


class TestUpdate:
    def test_title_only_change_keeps_scheduled_time(self, fresh_db):
        """改标题不能动已经排好的时刻。

        用**推迟过**的提醒来测，是因为 daily 的重算在多数时刻恰好得到同一个值
        （今天是 22:00，重算还是今天 22:00）—— 拿它测等于没测。
        推迟过的时间与 `at` 不对齐，重算就会跳回 22:00，把用户刚点的「10 分钟后再提醒」
        悄悄取消掉。这是"改个名字导致提醒提前响"的真实路径。
        """
        item = reminder_core.create("u1", _mk(at="22:00"))
        snoozed = reminder_core.snooze("u1", item["id"], 10)
        target = snoozed["next_fire_at"]

        updated = reminder_core.update("u1", item["id"], {"title": "改成复盘"})
        assert updated["title"] == "改成复盘"
        assert updated["next_fire_at"] == target

    def test_time_change_recomputes(self, fresh_db):
        item = reminder_core.create("u1", _mk(at="22:00"))
        before = item["next_fire_at"]
        updated = reminder_core.update("u1", item["id"], {"at": "07:30"})
        assert updated["next_fire_at"] != before
        assert datetime.fromtimestamp(updated["next_fire_at"], TZ).strftime("%H:%M") == "07:30"

    def test_partial_update_still_validates_cross_field_rules(self, fresh_db):
        """只改 freq=weekly 而没给 weekday，必须在合并后整体校验里被挡住。

        只校验"传进来的字段"会留下一行 "weekly 但没有 weekday"，next_fire 永远 None，
        表现是这条提醒静静地再也不响。
        """
        item = reminder_core.create("u1", _mk())
        with pytest.raises(reminder_core.ReminderInvalid):
            reminder_core.update("u1", item["id"], {"freq": "weekly"})

    def test_other_owner_cannot_update(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        assert reminder_core.update("u2", item["id"], {"title": "改"}) is None
        assert reminder_core.get("u1", item["id"])["title"] == "复盘今天"

    def test_disable_then_enable_still_schedulable(self, fresh_db):
        item = reminder_core.create("u1", _mk(at="22:00"))
        paused = reminder_core.set_enabled("u1", item["id"], False)
        assert paused["enabled"] is False
        resumed = reminder_core.set_enabled("u1", item["id"], True)
        assert resumed["enabled"] is True
        assert resumed["next_fire_at"] is not None


class TestDeleteAndSnooze:
    def test_delete_scoped_to_owner(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        assert reminder_core.delete("u2", item["id"]) is False
        assert reminder_core.delete("u1", item["id"]) is True
        assert reminder_core.get("u1", item["id"]) is None

    def test_snooze_pushes_next_fire(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        snoozed = reminder_core.snooze("u1", item["id"], 10)
        delta = snoozed["next_fire_at"] - int(time.time())
        assert 590 <= delta <= 610

    def test_snooze_rejects_absurd_minutes(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        with pytest.raises(reminder_core.ReminderInvalid):
            reminder_core.snooze("u1", item["id"], 0)


# ==================== override 合并 ====================


class TestResolve:
    def test_override_title_wins(self, fresh_db):
        item = reminder_core.create(
            "u1",
            _mk(source="delegate", source_ref="dl_1", override={"title": "我自己的叫法"}),
        )
        assert reminder_core.resolve(item)["title"] == "我自己的叫法"

    def test_delegate_source_reads_name_without_touching_table(self, fresh_db):
        with session._conn() as conn:
            conn.execute(
                "CREATE TABLE delegations (delegation_id TEXT PRIMARY KEY, owner_id TEXT, "
                "name TEXT, prompt TEXT)"
            )
            conn.execute(
                "INSERT INTO delegations VALUES ('dl_1','u1','晚间复盘','今天学得怎么样')"
            )
        item = reminder_core.create("u1", _mk(source="delegate", source_ref="dl_1"))
        view = reminder_core.resolve(item)
        assert view["source_title"] == "晚间复盘"

        # 只读：delegations 一个字节都不该被改（规格禁止清单第 3 条）
        with session._conn() as conn:
            row = conn.execute("SELECT name, prompt FROM delegations WHERE delegation_id='dl_1'").fetchone()
        assert (row[0], row[1]) == ("晚间复盘", "今天学得怎么样")

    def test_missing_delegation_does_not_break_resolve(self, fresh_db):
        item = reminder_core.create("u1", _mk(source="delegate", source_ref="dl_gone"))
        assert reminder_core.resolve(item)["title"] == "复盘今天"

    def test_broken_override_json_is_tolerated(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        with session._conn() as conn:
            conn.execute("UPDATE reminders SET override_json='{not json' WHERE id=?", (item["id"],))
        assert reminder_core.resolve(reminder_core.get("u1", item["id"]))["title"] == "复盘今天"


# ==================== 到期与迟到 ====================


class TestDue:
    def _force_next(self, rid: str, ts: int) -> None:
        with session._conn() as conn:
            conn.execute("UPDATE reminders SET next_fire_at=? WHERE id=?", (ts, rid))

    def test_due_returns_id_and_planned_at(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        planned = int(time.time())
        self._force_next(item["id"], planned)
        assert reminder_core.due(planned + 30) == [(item["id"], planned)]

    def test_due_owners_dedupes(self, fresh_db):
        a = reminder_core.create("u1", _mk(title="A"))
        b = reminder_core.create("u1", _mk(title="B"))
        planned = int(time.time())
        self._force_next(a["id"], planned)
        self._force_next(b["id"], planned)
        assert reminder_core.due_owners(planned + 5) == ["u1"]

    def test_too_late_is_not_due(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        planned = int(time.time()) - 3 * 3600
        self._force_next(item["id"], planned)
        assert reminder_core.due(planned + 3 * 3600) == []

    def test_catch_up_advances_and_records_missed(self, fresh_db):
        """超窗的不补发，但必须推进 —— 否则它永远躺在「已到期」里，永远不响。"""
        item = reminder_core.create("u1", _mk(at="22:00"))
        planned = int(time.time()) - 3 * 3600
        self._force_next(item["id"], planned)

        assert reminder_core.catch_up(int(time.time())) == 1
        after = reminder_core.get("u1", item["id"])
        assert after["next_fire_at"] > int(time.time())
        logs = reminder_core.fires("u1", item["id"])
        assert logs[0]["status"] == reminder_core.STATUS_FAILED
        assert logs[0]["detail"].startswith("missed:")

    def test_paused_is_not_due(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        reminder_core.set_enabled("u1", item["id"], False)
        planned = int(time.time())
        self._force_next(item["id"], planned)
        assert reminder_core.due(planned + 5) == []


class TestAdvance:
    def test_advance_moves_forward_normally(self, fresh_db):
        item = reminder_core.create("u1", _mk(at="22:00"))
        planned = int(item["next_fire_at"])
        nxt = reminder_core.advance(item["id"], planned + 20)
        assert nxt > planned

    def test_advance_after_early_run_still_moves(self, fresh_db):
        """提前跑一次（run-now / 手动补跑）也要往前走。

        反例：基准用 now 而不是 max(now, planned_at)。planned_at 还在未来时，
        next_fire 算出来还是原来那一刻，下一跳（一分钟后就到）立刻再发一遍 ——
        表现是「点了一次立即执行，然后每分钟响一次」，而且越查越像推送服务的锅。
        """
        item = reminder_core.create("u1", _mk(at="22:00"))
        planned = int(item["next_fire_at"])
        nxt = reminder_core.advance(item["id"], planned - 120)
        assert nxt > planned, "提前执行之后 next_fire_at 必须越过原计划时刻"


# ==================== 流水幂等 ====================


class TestFires:
    def test_same_planned_at_only_one_row(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        planned = int(time.time())
        reminder_core.record_fire(item["id"], planned, reminder_core.STATUS_EMPTY, "no_content")
        reminder_core.record_fire(item["id"], planned, reminder_core.STATUS_EMPTY, "no_content")
        assert len(reminder_core.fires("u1", item["id"])) == 1

    def test_sent_row_is_not_downgraded(self, fresh_db):
        """已经 sent 的那行不能被后来的重跑改成别的 —— 否则「响过没有」失真。"""
        item = reminder_core.create("u1", _mk())
        planned = int(time.time())
        reminder_core.record_fire(item["id"], planned, reminder_core.STATUS_SENT)
        reminder_core.record_fire(item["id"], planned, reminder_core.STATUS_FAILED, "retry")
        assert reminder_core.fires("u1", item["id"])[0]["status"] == reminder_core.STATUS_SENT

    def test_failed_can_be_upgraded_to_sent(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        planned = int(time.time())
        reminder_core.record_fire(item["id"], planned, reminder_core.STATUS_FAILED, "net")
        reminder_core.record_fire(item["id"], planned, reminder_core.STATUS_SENT)
        assert reminder_core.fires("u1", item["id"])[0]["status"] == reminder_core.STATUS_SENT

    def test_owner_isolation(self, fresh_db):
        item = reminder_core.create("u1", _mk())
        reminder_core.record_fire(item["id"], int(time.time()), reminder_core.STATUS_SENT)
        assert reminder_core.fires("u2") == []


# ==================== 老数据迁移 ====================


class TestMigration:
    def _prefs(self, owner: str, hour: int, minute: int) -> None:
        with session._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reminder_prefs "
                "(owner_id TEXT PRIMARY KEY, hour INTEGER, minute INTEGER, updated_at INTEGER)"
            )
            conn.execute(
                "INSERT INTO reminder_prefs VALUES (?,?,?,?)", (owner, hour, minute, 0)
            )

    def _sub(self, owner: str) -> None:
        with session._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS push_subscriptions "
                "(owner_id TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute("INSERT INTO push_subscriptions VALUES (?,0)", (owner,))

    def _system_rows(self, owner: str) -> list[dict]:
        with session._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE owner_id=? AND source='system'", (owner,)
            ).fetchall()
        return [dict(r) for r in rows]

    def test_prefs_row_becomes_system_reminder(self, fresh_db):
        self._prefs("u1", 20, 15)
        reminder_core.ensure()
        rows = self._system_rows("u1")
        assert len(rows) == 1
        assert rows[0]["at"] == "20:15"
        assert rows[0]["action"] == "agent_deliver"

    def test_migration_is_idempotent(self, fresh_db):
        """跑两遍只能有一条 —— 两条就是每天响两遍，且用户看不出原因。"""
        self._prefs("u1", 20, 0)
        reminder_core.ensure()
        reminder_core.reset_cache()
        reminder_core.ensure()
        assert len(self._system_rows("u1")) == 1

    def test_active_subscriber_without_prefs_gets_default_row(self, fresh_db):
        """开着推送但没进过设置页的人也必须被覆盖。

        只搬 reminder_prefs 的话，这批人升级后 push.daily 静默、又没有新行，
        从此再也收不到提醒 —— 而订阅还在，服务端显示一切正常。
        """
        self._sub("u2")
        reminder_core.ensure()
        rows = self._system_rows("u2")
        assert len(rows) == 1
        hour, minute = reminder_core.default_time()
        assert rows[0]["at"] == f"{hour:02d}:{minute:02d}"

    def test_cet_and_checkin_turned_on_for_subscribers(self, fresh_db):
        """「默认全关」唯一一次破例：这两条他们本来就在收。"""
        from core import user_prefs

        self._sub("u3")
        reminder_core.ensure()
        prefs = user_prefs.source_prefs("u3")
        assert prefs.get("cet") is True
        assert prefs.get("checkin") is True
        assert prefs.get("timetable") is None  # 其余默认关（没有行）

    def test_non_subscriber_gets_nothing(self, fresh_db):
        reminder_core.ensure()
        assert self._system_rows("u4") == []


# ==================== 老接口共用同一行 ====================


class TestSystemRow:
    def test_system_time_defaults_without_writing(self, fresh_db):
        before = reminder_core.system_time("u1")
        assert before == reminder_core.default_time()
        assert reminder_core.get_system("u1") is None  # GET 不建行

    def test_set_system_time_creates_once(self, fresh_db):
        reminder_core.set_system_time("u1", 7, 30)
        reminder_core.set_system_time("u1", 21, 0)
        assert reminder_core.system_time("u1") == (21, 0)
        assert len(reminder_core.list_reminders("u1")) == 1

    def test_system_time_snaps_to_legacy_steps(self, fresh_db):
        """老接口挂了 select 下拉，只能落在 0/15/30/45；吸附规则必须还在。"""
        reminder_core.set_system_time("u1", 7, 7)
        assert reminder_core.system_time("u1") == (7, 0)


# ==================== 概览 ====================


class TestSummary:
    def test_today_counts_only_enabled_today(self, fresh_db):
        today = reminder_core.create("u1", _mk(at="23:59"))
        reminder_core.create("u1", _mk(at="00:01"))
        paused = reminder_core.create("u1", _mk(title="暂停的"))
        reminder_core.set_enabled("u1", paused["id"], False)

        got = reminder_core.summary("u1")
        ids = [item["id"] for item in got["today"]]
        assert today["id"] in ids
        assert got["paused_count"] == 1
        assert got["total"] == 3
        assert got["day"] == timeutil.day_str()
