"""提醒中心的调度侧：四道闸、同刻合并、流水。

这个模块的价值全在"拦下来之后你还查得到原因"。所以每个用例除了断言
「没发」之外，都断言 `reminder_fires` 里的 status/detail ——
没有流水的话，「今天为什么没响」这个问题只能靠猜。

另一条被钉住的是**推进**：无论发成功、被拦下、还是发送失败，
`next_fire_at` 都必须往前走。不推进的话这条提醒会一直躺在「已到期」里，
每次 tick 都被扫出来重试 —— 表现是用户被同一条提醒反复轰炸。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import reminder_core, session, user_prefs
from service import push as push_svc
from service import reminder_tick

TZ = ZoneInfo("Asia/Shanghai")
OWNER = "u1"


def _ts(hour: int, minute: int = 0, day: int = 9) -> int:
    return int(datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp())


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    reminder_core.reset_cache()
    session._init_db()
    yield path
    reminder_core.reset_cache()


@pytest.fixture()
def sent(monkeypatch):
    """把发送换成一个记事本。返回列表，用例自己看发了几条、内容是什么。"""
    calls: list[dict] = []
    result = {"sent": 1, "failed": 0, "gone": 0, "skipped": 0, "items": []}

    def fake(owner_id, payload):
        calls.append({"owner": owner_id, "payload": payload})
        return dict(result)

    monkeypatch.setattr(push_svc, "send_to_user", fake)
    return {"calls": calls, "result": result}


def _due_now(reminder_id: str, planned_at: int) -> None:
    """把一条提醒的 next_fire_at 拨到指定的计划时刻。"""
    with session._conn() as conn:
        conn.execute("UPDATE reminders SET next_fire_at=? WHERE id=?", (planned_at, reminder_id))


def _fires(reminder_id: str) -> list[dict]:
    return reminder_core.fires(OWNER, reminder_id)


# ==================== 正常发送 ====================


class TestSend:
    def test_sends_and_records_sent(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "复盘今天", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(item["id"], planned)

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        assert res["fired"] == 1
        assert len(sent["calls"]) == 1
        assert sent["calls"][0]["payload"]["title"] == "复盘今天"
        assert sent["calls"][0]["payload"]["url"] == "/reminders"
        assert _fires(item["id"])[0]["status"] == reminder_core.STATUS_SENT

    def test_advances_after_sending(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "复盘今天", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(item["id"], planned)
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        after = reminder_core.get(OWNER, item["id"])
        assert after["next_fire_at"] > planned
        assert datetime.fromtimestamp(after["next_fire_at"], TZ).strftime("%H:%M") == "12:00"

    def test_once_disables_itself(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "交作业", "freq": "once", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        after = reminder_core.get(OWNER, item["id"])
        assert after["enabled"] is False
        assert after["next_fire_at"] is None

    def test_note_goes_into_body(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "喝水", "note": "别又忘了", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert sent["calls"][0]["payload"]["body"] == "别又忘了"


# ==================== 四道闸 ====================


class TestGates:
    def test_quiet_hours_skips_without_sending(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "复盘", "at": "23:30"})
        planned = _ts(23, 30)
        _due_now(item["id"], planned)

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        assert res["fired"] == 0
        assert sent["calls"] == []
        log = _fires(item["id"])[0]
        assert log["status"] == reminder_core.STATUS_QUIET
        assert log["detail"] == "quiet_hours"

    def test_quiet_does_not_defer_to_morning(self, db, sent):
        """静默时段就丢掉这一次，不补发 —— 补发等于早上被昨晚的提醒砸醒。"""
        item = reminder_core.create(OWNER, {"title": "复盘", "at": "23:30"})
        planned = _ts(23, 30)
        _due_now(item["id"], planned)
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        after = reminder_core.get(OWNER, item["id"])
        assert datetime.fromtimestamp(after["next_fire_at"], TZ).strftime("%H:%M") == "23:30"
        assert after["next_fire_at"] > planned + 3600  # 顺到明天，不是十分钟后

    def test_daily_cap(self, db, sent):
        user_prefs.set_time_prefs(OWNER, {"daily_cap": 1, "quiet_start": "03:00", "quiet_end": "03:00"})
        first = reminder_core.create(OWNER, {"title": "已发过", "at": "08:00"})
        second = reminder_core.create(OWNER, {"title": "超上限", "at": "12:00"})
        reminder_core.record_fire(first["id"], _ts(8, 0), reminder_core.STATUS_SENT)
        _due_now(second["id"], _ts(12, 0))

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))

        assert res["fired"] == 0
        assert sent["calls"] == []
        assert _fires(second["id"])[0]["status"] == reminder_core.STATUS_CAP
        assert "cap=1" in _fires(second["id"])[0]["detail"]

    def test_cap_zero_blocks_everything(self, db, sent):
        user_prefs.set_time_prefs(OWNER, {"daily_cap": 0, "quiet_start": "03:00", "quiet_end": "03:00"})
        item = reminder_core.create(OWNER, {"title": "别响", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        assert reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))["fired"] == 0

    def test_cap_not_hit_below_limit(self, db, sent):
        user_prefs.set_time_prefs(OWNER, {"daily_cap": 6, "quiet_start": "03:00", "quiet_end": "03:00"})
        item = reminder_core.create(OWNER, {"title": "还能发", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        assert reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))["fired"] == 1

    def test_empty_content_is_skipped(self, db, sent, monkeypatch):
        """「没内容不发」是这个产品最不能丢的一条闸。"""
        monkeypatch.setattr(push_svc, "build_message", lambda uid, day: None)
        item = reminder_core.create(
            OWNER,
            {"title": "每日回顾", "source": "system", "at": "12:00", "action": "agent_deliver"},
        )
        _due_now(item["id"], _ts(12, 0))

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))

        assert res["fired"] == 0
        assert sent["calls"] == []
        assert _fires(item["id"])[0]["status"] == reminder_core.STATUS_EMPTY

    def test_channels_without_push_is_skipped(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "只在站内", "at": "12:00", "channels": "inapp"})
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        log = _fires(item["id"])[0]
        assert log["status"] == reminder_core.STATUS_EMPTY
        assert "channels=inapp" in log["detail"]

    def test_disabled_source_stops_existing_reminder(self, db, sent):
        """设置页承诺「关掉来源，已经建好的那几条一起停」——必须真的停。"""
        item = reminder_core.create(
            OWNER, {"title": "高数", "source": "cet", "at": "12:00"}
        )
        _due_now(item["id"], _ts(12, 0))

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))

        assert res["fired"] == 0
        assert sent["calls"] == []
        log = _fires(item["id"])[0]
        assert log["status"] == reminder_core.STATUS_EMPTY
        assert log["detail"] == "source_disabled:cet"

    def test_enabled_source_fires(self, db, sent):
        user_prefs.set_source_pref(OWNER, "cet", True)
        item = reminder_core.create(
            OWNER, {"title": "高数", "source": "cet", "at": "12:00"}
        )
        _due_now(item["id"], _ts(12, 0))

        assert reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))["fired"] == 1
        assert sent["calls"][0]["payload"]["title"] == "高数"

    def test_timetable_ignores_the_source_switch(self, db, sent):
        """课堂提醒的开关是「上课提醒」那张卡，不是来源列表。

        `reminder_source_prefs` 默认全关（没有行 = 关）。如果 timetable 也走这道闸，
        用户在卡上打开上课提醒、后端却按「来源没开」全部记 `skipped_empty` ——
        表现是「我明明开了，就是不提醒」。两处开关管一件事必然出这种结果，
        所以 timetable 被明确排除在外（`run_one` 里的 `configurable`）。
        """
        item = reminder_core.create(
            OWNER, {"title": "高数", "source": "timetable", "at": "12:00"}
        )
        _due_now(item["id"], _ts(12, 0))

        assert reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))["fired"] == 1


# ==================== 内容来源 ====================


class TestContent:
    def test_system_source_uses_legacy_builder(self, db, sent, monkeypatch):
        """system 那条必须还是改造前的文案（六级 / 打卡），不能变成一句「每日回顾」。"""
        monkeypatch.setattr(
            push_svc,
            "build_message",
            lambda uid, day: {"title": "六级今天还剩 2 个任务", "body": "背单词", "url": "/cet", "tag": "daily-cet"},
        )
        item = reminder_core.create(
            OWNER,
            {"title": "每日回顾", "source": "system", "at": "12:00", "action": "agent_deliver"},
        )
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert sent["calls"][0]["payload"]["title"] == "六级今天还剩 2 个任务"

    def test_cet_source_ignores_checkin_content(self, db, sent, monkeypatch):
        """source=cet 的提醒不能因为"今天没打卡"就响 —— 那不是它该管的事。"""
        user_prefs.set_source_pref(OWNER, "cet", True)
        monkeypatch.setattr(
            push_svc,
            "build_message",
            lambda uid, day: {
                "title": "连续 3 天，今天还没打卡",
                "body": "",
                "url": "/checkin",
                "tag": "daily-checkin",
            },
        )
        item = reminder_core.create(
            OWNER, {"title": "六级", "source": "cet", "at": "12:00", "action": "agent_deliver"}
        )
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert sent["calls"] == []
        assert _fires(item["id"])[0]["status"] == reminder_core.STATUS_EMPTY

    def test_cet_source_sends_cet_content(self, db, sent, monkeypatch):
        user_prefs.set_source_pref(OWNER, "cet", True)
        monkeypatch.setattr(
            push_svc,
            "build_message",
            lambda uid, day: {"title": "六级今天还剩 1 个任务", "body": "", "url": "/cet", "tag": "daily-cet"},
        )
        item = reminder_core.create(
            OWNER, {"title": "六级", "source": "cet", "at": "12:00", "action": "agent_deliver"}
        )
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert sent["calls"][0]["payload"]["title"] == "六级今天还剩 1 个任务"

    def test_builder_exception_is_swallowed(self, db, sent, monkeypatch):
        """单个用户的数据异常不该把整轮变成 job 失败（那会触发三次重试）。"""

        def boom(uid, day):
            raise RuntimeError("cet 库坏了")

        monkeypatch.setattr(push_svc, "build_message", boom)
        item = reminder_core.create(
            OWNER, {"title": "每日回顾", "source": "system", "at": "12:00", "action": "agent_deliver"}
        )
        _due_now(item["id"], _ts(12, 0))
        assert reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))["fired"] == 0

    def test_delegate_source_uses_reminder_text(self, db, sent):
        user_prefs.set_source_pref(OWNER, "delegate", True)
        with session._conn() as conn:
            conn.execute(
                "CREATE TABLE delegations (delegation_id TEXT PRIMARY KEY, owner_id TEXT, "
                "name TEXT, prompt TEXT, enabled INTEGER DEFAULT 1)"
            )
            conn.execute("INSERT INTO delegations VALUES ('dl_1','u1','晚间复盘','今天学得怎么样',1)")
        item = reminder_core.create(
            OWNER,
            {"title": "委托", "source": "delegate", "source_ref": "dl_1", "at": "12:00", "action": "agent_deliver"},
        )
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        payload = sent["calls"][0]["payload"]
        assert payload["title"] == "晚间复盘"
        assert payload["url"] == "/delegate"

    def test_suggest_focus_opens_the_timer(self, db, sent):
        """「开始专注」必须换个落点。

        这条之前是假的：action=suggest_focus 走的是和「只提醒」完全一样的分支，
        到点只弹一条普通通知、点开回到提醒列表 —— 用户选了它和没选一样。
        """
        item = reminder_core.create(
            OWNER, {"title": "该写作业了", "at": "12:00", "action": "suggest_focus"}
        )
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))

        payload = sent["calls"][0]["payload"]
        assert payload["url"] == "/tools?tool=pomodoro"
        assert "专注" in payload["body"]

    def test_suggest_focus_keeps_its_own_note(self, db, sent):
        """有备注就用备注，别用那句默认的「点一下开始一段专注」盖掉。"""
        item = reminder_core.create(
            OWNER,
            {"title": "写高数", "note": "第三章习题", "at": "12:00", "action": "suggest_focus"},
        )
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        payload = sent["calls"][0]["payload"]
        # 链接一起断言：只查 body 的话，专注分支被删掉时这条照样绿
        # （普通通知也带 note），等于没在测「专注」这件事。
        assert payload["url"] == "/tools?tool=pomodoro"
        assert payload["body"] == "第三章习题"


# ==================== 同刻合并 ====================


def _insert_task(
    owner: str,
    task_id: str,
    *,
    due_at: int | None,
    title: str = "交实验报告",
    note: str | None = None,
) -> str:
    """直接写一行 tasks。

    不走 `task_core.create`：它要求 `remind_at > 真实 now`，而本文件的时刻钉在
    2026-11-09。这里要测的是**排版**，不是「能不能建一条待办」。
    """
    from core import task_core

    task_core.ensure()
    with session._conn() as conn:
        conn.execute(
            """INSERT INTO tasks
                  (id, owner_id, title, note, list_key, icon, priority, status,
                   due_at, remind_at, completed_at, recurrence, parent_id, sort_order,
                   created_at, updated_at)
               VALUES (?,?,?,?,'inbox',NULL,0,'open',?,NULL,NULL,'none',NULL,0,1,1)""",
            (task_id, owner, title, note, due_at),
        )
    return task_id


class TestTaskNotification:
    """待办提醒也是「上面一行说事、下面一行说细节」。

    2026-11-13 用户看完上课提醒的新排版说「待办也要这样」。原来待办是
    title=待办名、body=备注：备注一空正文就空，而「还有多久截止」这件事
    从来没被说出来过 —— 用户得自己拿今天和 11-15 去减。
    """

    def _content(self, due_at, note=None, planned=None, task_id="t1"):
        planned = _ts(20, 0) if planned is None else planned
        _insert_task(OWNER, task_id, due_at=due_at, note=note)
        item = {
            "title": "交实验报告",
            "note": note,
            "source": "task",
            "source_ref": task_id,
            "channels": "push",
            "action": "notify",
        }
        out = reminder_tick.build_content(OWNER, item, planned)
        assert out is not None
        return out

    def test_hours_before_deadline(self, db):
        out = self._content(_ts(22, 0), note="写第三章")
        assert out["title"] == "交实验报告 · 还有 2 小时"
        assert out["body"] == "写第三章"
        assert "还有" not in out["body"], "相对时间只在标题，正文再来一次就白占一行"

    def test_minutes_before_deadline(self, db):
        out = self._content(_ts(20, 35))
        assert out["title"] == "交实验报告 · 还有 35 分钟"

    def test_days_before_deadline(self, db):
        out = self._content(_ts(20, 0, day=12))
        assert out["title"] == "交实验报告 · 还有 3 天"

    def test_overdue_is_said_out_loud(self, db):
        """发晚了（重试、静默时段顺延）不能还写「还有 -1 小时」。"""
        out = self._content(_ts(19, 0))
        assert out["title"] == "交实验报告 · 已过期"

    def test_due_right_now_says_now(self, db):
        """把提醒设成和截止同一时刻是常见做法（「到点就叫我」）。
        那一分钟告诉他「已过期」纯属添堵。"""
        out = self._content(_ts(20, 0))
        assert out["title"] == "交实验报告 · 现在截止"

    def test_empty_note_falls_back_to_deadline(self, db):
        """备注空的时候，绝对时刻就是第二行唯一的信息，不能留白。"""
        out = self._content(_ts(22, 0), note=None)
        assert out["body"] == "今天 22:00 截止"

    def test_task_without_due_keeps_plain_title(self, db):
        """只设了提醒、没设截止：没有任何时间可说，就别编一个。"""
        out = self._content(None, note="记得带伞")
        assert out["title"] == "交实验报告"
        assert out["body"] == "记得带伞"

    def test_deleted_task_does_not_break_the_push(self, db):
        """待办已删、提醒行还没清干净时，这条通知必须照发 —— 不能 KeyError 掉。
        「设了提醒不响」是最难查的一类，宁可少一句时间。"""
        item = {
            "title": "交实验报告",
            "note": "写第三章",
            "source": "task",
            "source_ref": "gone",
            "channels": "push",
            "action": "notify",
        }
        out = reminder_tick.build_content(OWNER, item, _ts(20, 0))
        assert out is not None
        assert out["title"] == "交实验报告"


class TestMerge:
    def test_two_reminders_same_minute_become_one_push(self, db, sent):
        a = reminder_core.create(OWNER, {"title": "复盘今天", "at": "12:00"})
        b = reminder_core.create(OWNER, {"title": "喝水", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(a["id"], planned)
        _due_now(b["id"], planned)

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        assert len(sent["calls"]) == 1
        assert res["fired"] == 2
        assert res["merged"] == 1
        payload = sent["calls"][0]["payload"]
        assert "2 条" in payload["title"]
        assert "复盘今天" in payload["body"] and "喝水" in payload["body"]

    def test_merged_row_records_where_it_went(self, db, sent):
        a = reminder_core.create(OWNER, {"title": "A", "at": "12:00"})
        b = reminder_core.create(OWNER, {"title": "B", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(a["id"], planned)
        _due_now(b["id"], planned)
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        logs = {log["reminder_id"]: log for log in _fires(a["id"]) + _fires(b["id"])}
        assert logs[a["id"]]["status"] == reminder_core.STATUS_SENT
        assert logs[b["id"]]["detail"].startswith("merged_into:")
        # 合并通知在每日上限里只算一条
        assert user_prefs.day_count(OWNER, "2026-11-09") == 1

    def test_merge_tag_groups_by_minute(self, db, sent):
        a = reminder_core.create(OWNER, {"title": "A", "at": "12:00"})
        b = reminder_core.create(OWNER, {"title": "B", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(a["id"], planned)
        _due_now(b["id"], planned)
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)
        assert sent["calls"][0]["payload"]["tag"] == f"rm-merge-{planned}"


# ==================== 发送失败也要推进 ====================


class TestFailure:
    def test_send_failure_still_advances(self, db, sent):
        """不推进的话这条提醒会每分钟重发一次，用户被反复轰炸或被反复计失败。"""
        sent["result"].update({"sent": 0, "failed": 1})
        item = reminder_core.create(OWNER, {"title": "发不出去", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(item["id"], planned)

        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, planned)

        assert _fires(item["id"])[0]["status"] == reminder_core.STATUS_FAILED
        assert reminder_core.get(OWNER, item["id"])["next_fire_at"] > planned

    def test_push_not_configured_detail(self, db, sent):
        sent["result"].update({"sent": 0, "failed": 0, "skipped": 1})
        item = reminder_core.create(OWNER, {"title": "没配 VAPID", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert _fires(item["id"])[0]["detail"] == "push_not_configured"

    def test_no_subscription_detail(self, db, sent):
        """四个计数全零 = 这人没有订阅设备，不是「发失败」。

        线上冒烟（2026-11-09）踩到的就是这条：假账号没有订阅，流水写成
        `send_failed:sent=0failed=0`，看日志的人会去查推送服务方而不是去开推送。
        """
        sent["result"].update({"sent": 0, "failed": 0, "gone": 0, "skipped": 0})
        item = reminder_core.create(OWNER, {"title": "没设备", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert _fires(item["id"])[0]["detail"] == "no_subscription"

    def test_real_send_failure_keeps_counts(self, db, sent):
        """真有设备但发失败时，计数还是要露出来 —— 别把这条也吞成 no_subscription。"""
        sent["result"].update({"sent": 0, "failed": 2, "gone": 0, "skipped": 0})
        item = reminder_core.create(OWNER, {"title": "发失败", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))
        assert _fires(item["id"])[0]["detail"] == "send_failed:sent=0failed=2"


# ==================== worker 接线 ====================


class TestWorkerHooks:
    def test_due_subjects_returns_owner_once(self, db, sent):
        a = reminder_core.create(OWNER, {"title": "A", "at": "12:00"})
        b = reminder_core.create(OWNER, {"title": "B", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(a["id"], planned)
        _due_now(b["id"], planned)
        assert reminder_tick.due_subjects(reminder_tick.JOB_KEY, planned) == [OWNER]

    def test_due_subjects_runs_catch_up(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "迟到", "at": "12:00"})
        planned = _ts(12, 0)
        _due_now(item["id"], planned - 3 * 3600)
        reminder_tick.due_subjects(reminder_tick.JOB_KEY, planned)
        assert reminder_core.get(OWNER, item["id"])["next_fire_at"] > planned

    def test_due_subjects_never_raises(self, db, monkeypatch):
        """due_subjects 抛异常 = 整个 job 记失败 + 三次重试，其他用户一起停。"""

        def boom(*args, **kwargs):
            raise RuntimeError("db locked")

        monkeypatch.setattr(reminder_core, "catch_up", boom)
        assert reminder_tick.due_subjects(reminder_tick.JOB_KEY, _ts(12, 0)) == []

    def test_run_job_fallback(self, db, sent):
        item = reminder_core.create(OWNER, {"title": "兜底", "at": "12:00"})
        _due_now(item["id"], _ts(12, 0))
        stats = reminder_tick.run_job(reminder_tick.JOB_KEY, _ts(12, 0))
        assert stats == {"due": 1, "fired": 1}

    def test_nothing_due_returns_skipped(self, db, sent):
        assert reminder_tick.run_one(reminder_tick.JOB_KEY, OWNER, _ts(12, 0))["skipped"] is True


# ==================== 老链路必须静默 ====================


class TestLegacyChainSilent:
    """两条链路同时活着 = 同一条提醒每天响两次，而且两边都"看着正常"。

    所以 `push.daily` 的 due 阶段必须返回空。这条断言是这次改造的**总闸**：
    它红了说明老链路又活了，用户会开始收到重复通知。
    """

    def test_push_daily_due_is_empty(self, db, monkeypatch):
        """★ 这条必须"有料"：先造出一个**本来会被选中**的用户（活订阅 + 到了自己的点 +
        有内容），再断言 due 仍然为空。否则删掉那道闸它也是绿的 —— 那就是一条摆设断言。
        """
        from core import push_store

        monkeypatch.setattr(push_svc.config, "PUSH_ENABLED", True)
        monkeypatch.setattr(push_svc.config, "VAPID_PUBLIC_KEY", "pub")
        monkeypatch.setattr(push_svc.config, "VAPID_PRIVATE_KEY", "priv")
        monkeypatch.setattr(
            push_svc,
            "build_message",
            lambda uid, day: {"title": "六级今天还剩 1 个任务", "body": "", "url": "/cet", "tag": "daily-cet"},
        )
        push_store.upsert(OWNER, "https://fcm.googleapis.com/wp/x", "B" * 87, "a" * 22)
        from core import reminder_store

        reminder_store.set_time(OWNER, 20, 0)

        assert push_svc.due_subjects("push.daily", _ts(20, 0)) == []

    def test_push_daily_job_definition_kept(self):
        """规格要求不删 job 定义：老库里的 job_runs 还要能查，回滚也要有东西可恢复。"""
        from core import scheduler as sched

        assert "push.daily" in sched.job_defs()

    def test_reminder_tick_runs_every_minute(self):
        """15 分钟的 tick 做不到「课前 10 分钟」，也做不到 22:03 这种时间。"""
        from core import scheduler as sched

        assert sched.job_defs()["reminder.tick"]["cron"] == "* * * * *"

    def test_worker_registers_the_right_module(self):
        """没挂上 handler 的 job 在 worker 里是静默空转 —— 每分钟跑一次，什么也不做。"""
        from worker import scheduler as ws

        ws._load_handlers(force=True)
        assert ws._handlers.get("reminder.tick") is reminder_tick
