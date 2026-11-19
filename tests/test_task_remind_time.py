"""待办卡片上的「明天 08:00」是怎么来的，以及老数据怎么补上。

2026-11-17 实机反馈（截图）：用户在会话里说「明早 8 点起床」，卡片上只写
「明天 / 截止」，`remind_at` 是空的，到点一声不响。三处各缺一块：

1. **解析**：说了钟点也不算「要提醒」—— 钉在 `tests/test_capture_parse.py`
   的 `TestClockMeansReminder`；
2. **展示**：时间列只写日期，把 `due_at` 里的 08:00 吃掉了 —— 本文件钉；
3. **老数据**：改代码救不了库里已经写坏的行 —— 本文件用
   `tools/backfill_task_reminders.py` 真跑一遍临时库。

这一层刻意做"源码断言 + 真跑一遍"两种：展示逻辑是 JSX（Python 跑不了），
但它错了用户一眼能看见；回填是写库（能真跑），所以必须真跑。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.session as session  # noqa: E402
from core import reminder_core, reminder_store, task_core, timeutil  # noqa: E402

PAGE = ROOT / "frontend" / "src" / "app" / "(console)" / "reminders" / "page.tsx"
CSS = ROOT / "frontend" / "src" / "app" / "styles" / "console.css"


def _strip_js_comments(src: str) -> str:
    """去掉 // 与 /* */ 注释。

    ★ 这个仓库被注释骗过三次（见 MEMORY 硬约束 53）：注释里写着"上一版是什么样"，
      直接 in 源码会把"已经删掉的东西"判成还在。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _page() -> str:
    return _strip_js_comments(PAGE.read_text(encoding="utf-8"))


def _css() -> str:
    src = CSS.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


# ---------------------------------------------------------------- 展示


class TestTaskCardShowsTheClock:
    def test_time_column_is_built_by_task_when(self):
        """时间列必须走 `taskWhen`，别再回到「remind_at ? HH:MM : 日期」的写法 ——
        那种写法让「明天」和「08:00」二选一，用户永远看不到完整的时刻。"""
        src = _page()
        assert "const when = taskWhen(task, now);" in src
        assert 'if (pivot === null) return { main: "—", small: "无日期" };' in src

    def test_small_line_carries_the_clock(self):
        """第二行是**钟点**：没有它就还是「只剩明天」。

        ★ 只写两行是排版硬限制：这一列在 390 下只有 40px，「08:00 提醒」
          放不下，硬塞会折成三行、最后一行还会落到副标题那一行上。
          所以「会不会响」交给副标题（会提醒 / 不提醒），钟点留在这一列。
        """
        src = _page()
        assert 'small: clock ?? (task.remind_at ? "提醒" : "截止")' in src

    def test_meta_says_whether_it_rings(self):
        """「会提醒 / 不提醒」必须有，而且只在还没响过时写 —— 响过之后由
        「已提醒 / 没提醒 · 免打扰」说，两个词并排会互相打架。"""
        src = _page()
        assert 'const remindText = state\n    ? ""' in src
        assert '? "会提醒"' in src and '? "不提醒"' in src
        assert '<span className="rem-state" data-tone="muted">' in src, (
            "「会提醒」不该是警示色/按钮 —— 它说的是已经设好了"
        )

    def test_sentinels_are_not_printed_as_a_time(self):
        """00:00 / 23:59 是「只说了哪天」的落库写法，不能当成用户定的钟点。

        漏了这条，每一条只写日期的待办都会多出一行「23:59 截止」，
        用户会以为那是自己设的。
        """
        src = _page()
        block = src[src.index("function clockOf(") :]
        block = block[: block.index("\n}")]
        assert "h === 0 && m === 0" in block
        assert "h === 23 && m === 59" in block

    def test_reverse_proof_sentinel_branch_is_load_bearing(self):
        """反证：把占位判断删掉，下面这段等价逻辑就漏 —— 证明它不是摆设。"""
        src = _page()
        clock_of = src[src.index("function clockOf(") :]
        clock_of = clock_of[: clock_of.index("\n}")]
        broken = clock_of.replace("if ((h === 0 && m === 0) || (h === 23 && m === 59)) return null;", "")
        assert "h === 0" not in broken, "反证没生效：占位判断根本没匹配上"

    def test_time_column_does_not_steal_width_from_the_title(self):
        """时间列宽度不许跟着内容长：390 下标题只剩 5~6 个字，
        再让 20px 就是「早上…」三个字（2026-11-17 实测截图）。"""
        css = _css()
        block = css[css.index(".rem-task {") :]
        block = block[: block.index("}")]
        assert "44px 44px minmax(0, 1fr) 26px" in block
        phone = css[css.index("  .rem-task {", css.index("@media (max-width: 900px)")) :]
        phone = phone[: phone.index("}")]
        assert "44px 40px minmax(0, 1fr) 26px" in phone


class TestReminderCardsHaveIcons:
    """提醒卡已并入待办（2026-11-19）：页面上只剩一种卡片。

    旧契约（ReminderCard / eventMark 的装饰性图标）随提醒卡整段退场 ——
    当年钉它是为了让提醒卡和任务卡看起来像同一种东西；合并之后这件事
    由结构本身保证了。这里反过来钉：半个提醒卡都不许回流。
    卡片自己的图标契约在 tests/test_reminders_ui.py 的 TestTaskMarks。
    """

    def test_merged_cards_are_gone(self):
        src = _page()
        assert "function ReminderCard" not in src, "提醒卡又回流了"
        assert "eventMark" not in src, "提醒卡的图标映射又回流了"
        # 唯一的卡片渲染函数是待办卡
        assert "function TaskCard" in src
        assert src.count("function TaskCard") == 1


# ---------------------------------------------------------------- 回填


def _load_backfill():
    path = ROOT / "tools" / "backfill_task_reminders.py"
    spec = importlib.util.spec_from_file_location("backfill_task_reminders", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    reminder_core.reset_cache()
    task_core.reset_cache()
    reminder_store._READY.clear()
    session._init_db()
    yield path
    reminder_core.reset_cache()
    task_core.reset_cache()
    reminder_store._READY.clear()


def _mk_task(title: str, due_at: int | None, *, status: str = "open") -> dict:
    item = task_core.create(
        "u1", {"title": title, "list_key": "life", "due_at": due_at, "remind_at": None}
    )
    if status == "done":
        task_core.complete("u1", item["id"])
        item = task_core.get("u1", item["id"])
    return item


class TestBackfill:
    def test_clock_time_tasks_get_a_linked_reminder(self, db):
        now = int(datetime(2026, 11, 17, 21, 0, tzinfo=timeutil.TZ).timestamp())
        due = int(datetime(2026, 11, 18, 8, 0, tzinfo=timeutil.TZ).timestamp())
        task = _mk_task("起床", due)

        mod = _load_backfill()
        rows = mod.candidates(now)
        assert [r["id"] for r in rows] == [task["id"]]

        assert mod.apply(rows, now) == 1
        fresh = task_core.get("u1", task["id"])
        assert fresh["remind_at"] == due
        linked = reminder_core.get_task_reminder("u1", task["id"])
        assert linked is not None and linked["enabled"] == 1
        assert linked["next_fire_at"] == due

    def test_sentinel_times_are_left_alone(self, db):
        """00:00 / 23:59 是「只说了哪天」，补上就是让每条待办半夜响一声。"""
        now = int(datetime(2026, 11, 17, 21, 0, tzinfo=timeutil.TZ).timestamp())
        midnight = int(datetime(2026, 11, 18, 0, 0, tzinfo=timeutil.TZ).timestamp())
        end_of_day = int(datetime(2026, 11, 18, 23, 59, tzinfo=timeutil.TZ).timestamp())
        _mk_task("交作业", midnight)
        _mk_task("交表", end_of_day)

        mod = _load_backfill()
        assert mod.candidates(now) == []

    def test_past_and_done_and_already_set_rows_are_skipped(self, db, monkeypatch):
        now = int(datetime(2026, 11, 17, 21, 0, tzinfo=timeutil.TZ).timestamp())
        # task_core.create 内部读真实钟：本用例的「未来」写的是 11-19，
        # 真实时钟走过它之后 create 就会 400（曾因此假红）。
        # 把钟冻结到测试自己的 now，用例不再随日期过期。
        monkeypatch.setattr(timeutil, "now_ts", lambda: now)
        past = int(datetime(2026, 11, 17, 8, 0, tzinfo=timeutil.TZ).timestamp())
        future = int(datetime(2026, 11, 19, 8, 0, tzinfo=timeutil.TZ).timestamp())

        _mk_task("过去的事", past)
        _mk_task("做完的事", future, status="done")
        done = task_core.create(
            "u1", {"title": "已经设过提醒", "due_at": future, "remind_at": future}
        )
        assert done["remind_at"] == future

        mod = _load_backfill()
        assert mod.candidates(now) == [], "过去 / 已完成 / 已设过的一律不许动"

    def test_second_run_is_a_no_op(self, db):
        """回填要能重复跑：线上经常是"跑一次、看一眼、再跑一次"。"""
        now = int(datetime(2026, 11, 17, 21, 0, tzinfo=timeutil.TZ).timestamp())
        due = int(datetime(2026, 11, 18, 8, 0, tzinfo=timeutil.TZ).timestamp())
        task = _mk_task("起床", due)

        mod = _load_backfill()
        assert mod.apply(mod.candidates(now), now) == 1
        assert mod.candidates(now) == []
        # 联动提醒只有一条，不会因为多跑一次变成两条
        rows = mod.candidates(now, "u1")
        assert rows == []
        linked = reminder_core.get_task_reminder("u1", task["id"])
        assert linked is not None
        assert task_core.get("u1", task["id"])["remind_at"] == due

    def test_backfilled_reminder_is_due_at_that_moment(self, db):
        """补完不算完：到那个时刻，worker 的到期查询必须**扫得到它**。

        用户的原话是「也没按设定时间触发提醒」—— 所以这里不只看"建了一行"，
        而是把时钟拨到 08:00 问一次「现在该响谁」。
        """
        now = int(datetime(2026, 11, 17, 21, 0, tzinfo=timeutil.TZ).timestamp())
        due = int(datetime(2026, 11, 18, 8, 0, tzinfo=timeutil.TZ).timestamp())
        task = _mk_task("起床", due)

        mod = _load_backfill()
        mod.apply(mod.candidates(now), now)

        assert reminder_core.due_for_owner("u1", now=now - 60) == [], "还没到点就不该响"
        hits = reminder_core.due_for_owner("u1", now=due + 5)
        assert len(hits) == 1
        assert hits[0]["source"] == "task"
        assert hits[0]["source_ref"] == task["id"]
        assert hits[0]["next_fire_at"] == due
        # 内容也要对得上：标题是待办名，点开落在待办页
        assert hits[0]["title"] == "起床"

    def test_owner_filter(self, db):
        now = int(datetime(2026, 11, 17, 21, 0, tzinfo=timeutil.TZ).timestamp())
        due = int(datetime(2026, 11, 18, 8, 0, tzinfo=timeutil.TZ).timestamp())
        task_core.create("u1", {"title": "甲", "due_at": due})
        task_core.create("u2", {"title": "乙", "due_at": due})

        mod = _load_backfill()
        assert [r["owner_id"] for r in mod.candidates(now, "u2")] == ["u2"]
