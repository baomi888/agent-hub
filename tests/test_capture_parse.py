"""一句话整理（Phase B）的校验层测试。

## 为什么全部脱模型

这一层的价值不在"模型答得好不好"（那要评测集，且答案不唯一），
而在**模型答得不好时我们的行为对不对**：
JSON 带围栏、字段越界、时间换算不出来、单条坏掉、超过 12 条。

这些全都能用一段假 JSON 精确复现，不需要网络、不需要 key、不会因为模型改版就飘。
真模型的端到端由 `tools/capture_parse_live.py` 单独跑一次（人工看，不进 CI）。

## 一条纪律

`tighten()` 是纯函数（除了读 `timeutil.TZ`），所以测试直接调它，
不 mock `get_llm`。只有测 `parse()` 那条路径时才替换 `capture_parse.get_llm`
—— 因为那里唯一要验的是"上游挂了会抛什么"，不是"它解析得好不好"。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core import capture_parse, timeutil


def _now() -> int:
    """2026-11-10 22:00（周二）本地时间。所有相对时间的期望值都以它为准。"""
    return int(datetime(2026, 11, 10, 22, 0, tzinfo=timeutil.TZ).timestamp())


def _item(**over) -> dict:
    base = {
        "kind": "task",
        "title": "交高数作业",
        "source_text": "明天交高数作业",
        "due_at": "2026-11-11 23:59",
        "list_key": "homework",
    }
    base.update(over)
    return base


def _tighten(items: list[dict], text: str, unresolved: list[str] | None = None):
    data = {"items": items, "unresolved": unresolved or []}
    return capture_parse.tighten(data, text, _now())


# ---------------------------------------------------------------- 正常路径
class TestHappyPath:
    def test_basic_task(self):
        items, unresolved = _tighten([_item()], "明天交高数作业")
        assert unresolved == []
        assert len(items) == 1
        it = items[0]
        assert it["id"] == "p1"
        assert it["kind"] == "task"
        assert it["list_key"] == "homework"
        # 模型给的是本地时刻串，服务端换算成 Unix 秒（响应形状仍是秒）
        assert it["due_at"] == int(
            datetime(2026, 11, 11, 23, 59, tzinfo=timeutil.TZ).timestamp()
        )
        assert it["source_start"] == 0 and it["source_end"] == 7
        assert it["needs_confirm"] is False
        # 图标不由模型猜，一律是 null（用户在确认屏上自己选）
        assert it["icon"] is None

    def test_prompt_defaults_to_task(self):
        """类型判定的默认值必须是 task。

        上一版写的是"到点要做的事（开会、上课、生日、体检）用 reminder"，
        模型据此把「早上八点起床」「十点吃早饭」全判成提醒 —— 用户看到的
        是满屏「只提醒」，一条都打不了勾（2026-11-11 实机反馈）。

        这条测试钉的是**根因**（提示词里的默认值），不是某一条输出：
        输出由模型决定，改提示词才改得动它。断言用两个短串而不是整段比对，
        是为了让措辞可以继续打磨、但"默认 task"这个意思不许丢。
        """
        p = capture_parse.SYSTEM_PROMPT
        assert '"task" 是默认值' in p
        # 旧规则的原句必须已经不在了，否则模型会继续照着它判
        assert "到点要做的事（开会、上课、生日、体检）用" not in p
        # 周期类必须 still 指向 reminder（待办不支持重复，这一条不能一起删掉）
        assert "必须**用 \"reminder\"" in p

    def test_reminder_with_weekly(self):
        text = "每周三晚上八点背单词"
        items, _ = _tighten(
            [
                _item(
                    kind="reminder",
                    title="背单词",
                    source_text=text,
                    due_at=None,
                    list_key=None,
                    recurrence={"freq": "weekly", "at": "20:00", "weekday": 2},
                )
            ],
            text,
        )
        rec = items[0]["recurrence"]
        assert rec["freq"] == "weekly" and rec["at"] == "20:00" and rec["weekday"] == 2
        # task 才进清单；reminder 不属于任何清单
        assert items[0]["list_key"] is None

    def test_two_items_keep_original_order_offsets(self):
        text = "明天交高数作业，周五社团开会"
        items, _ = _tighten(
            [
                _item(source_text="明天交高数作业"),
                _item(
                    kind="reminder",
                    title="社团开会",
                    source_text="周五社团开会",
                    due_at=None,
                    recurrence={"freq": "once", "fire_at": "2026-11-13 19:00"},
                ),
            ],
            text,
        )
        assert [i["source_start"] for i in items] == [0, 8]
        assert text[items[1]["source_start"] : items[1]["source_end"]] == "周五社团开会"


# ---------------------------------------------------------------- 时间换算
class TestTime:
    def test_iso_and_date_only(self):
        assert capture_parse._to_ts("2026-11-11 08:30", _now()) == int(
            datetime(2026, 11, 11, 8, 30, tzinfo=timeutil.TZ).timestamp()
        )
        # 只给日期 → 当天 00:00，不是"现在"
        assert capture_parse._to_ts("2026-11-20", _now()) == int(
            datetime(2026, 11, 20, 0, 0, tzinfo=timeutil.TZ).timestamp()
        )

    def test_unix_seconds_and_millis(self):
        ts = int(datetime(2026, 11, 12, 9, 0, tzinfo=timeutil.TZ).timestamp())
        assert capture_parse._to_ts(ts, _now()) == ts
        assert capture_parse._to_ts(ts * 1000, _now()) == ts
        assert capture_parse._to_ts(str(ts), _now()) == ts

    def test_time_only_rolls_to_tomorrow(self):
        """只给「08:30」，而现在已经 22:00 → 说的是明天早上，不是今天早上。"""
        got = capture_parse._to_ts("08:30", _now())
        assert capture_parse.local_datetime(got).startswith("2026-11-11 08:30")

    def test_garbage_becomes_none_not_error(self):
        """换算不出来就写 null 让用户补，绝不瞎猜一个时间。"""
        for bad in ("后天下午", "", None, True, [], {"a": 1}):
            assert capture_parse._to_ts(bad, _now()) is None

    def test_out_of_range_year_is_dropped(self):
        """2027 写成 2017 是最常见的模型错误：宁可 null，也不要一条 2017 年的待办。"""
        assert capture_parse._to_ts("2017-01-01 09:00", _now()) is None
        assert capture_parse._to_ts(0, _now()) is None


# ---------------------------------------------------------------- 逐条校验
class TestValidation:
    def test_bad_title_does_not_kill_the_batch(self):
        text = "明天交高数作业，周五社团开会"
        items, unresolved = _tighten(
            [
                _item(source_text="明天交高数作业"),
                {"kind": "someday", "title": "社团开会", "source_text": "周五社团开会"},
            ],
            text,
        )
        assert len(items) == 1, "一条坏的不该拖垮另一条"
        assert len(unresolved) == 1
        assert "社团开会" in unresolved[0]

    def test_empty_title_rejected(self):
        _, unresolved = _tighten([_item(title="   ")], "明天交高数作业")
        assert len(unresolved) == 1

    def test_long_title_truncated_not_dropped(self):
        items, _ = _tighten([_item(title="长" * 200)], "明天交高数作业")
        assert len(items) == 1, "太长就截断：条目还在，用户下一步能改；丢掉就变成「我说了却没有」"
        assert len(items[0]["title"]) == capture_parse.TITLE_MAX

    def test_unknown_list_key_falls_back_to_inbox(self):
        items, _ = _tighten([_item(list_key="随便写的")], "明天交高数作业")
        assert items[0]["list_key"] == "inbox"

    def test_priority_clamped(self):
        items, _ = _tighten([_item(priority=9)], "明天交高数作业")
        assert items[0]["priority"] == 2
        items, _ = _tighten([_item(priority="高")], "明天交高数作业")
        assert items[0]["priority"] == 0

    def test_task_with_recurrence_becomes_reminder(self):
        """「每天背单词」被判成 task：改判 reminder，不能把"每天"丢掉。

        规格要求 task.recurrence 为 null。**丢掉**是最省事的写法，但用户原话里的
        "每天"会凭空消失，卡片还会写「缺少时间」—— 等于把模型的错记到用户头上。
        这套类型里只有 reminder 能承载"每天"，所以改判。
        """
        text = "今晚还得背单词，每天背 20 分钟"
        items, _ = _tighten(
            [_item(kind="task", title="背单词 20 分钟", source_text=text, due_at=None,
                   recurrence={"freq": "daily", "at": "20:00"})],
            text,
        )
        it = items[0]
        assert it["kind"] == "reminder", "带周期的条目只能是 reminder"
        assert it["recurrence"]["freq"] == "daily"
        assert it["recurrence"]["at"] == "20:00"
        assert it["list_key"] is None, "reminder 不属于任何清单"

    def test_fire_at_only_for_once(self):
        text = "每周三开会"
        items, _ = _tighten(
            [_item(kind="reminder", title="开会", source_text=text, due_at=None, list_key=None,
                   recurrence={"freq": "weekly", "weekday": 2, "fire_at": "2026-11-11 19:00"})],
            text,
        )
        assert items[0]["recurrence"]["fire_at"] is None

    def test_once_without_fire_at_falls_back_to_remind_at(self):
        """freq=once 却没给 fire_at：用 remind_at 兜，别让提醒变成哑弹。"""
        text = "明晚八点提醒我交表"
        items, _ = _tighten(
            [_item(kind="reminder", title="交表", source_text=text, due_at=None, list_key=None,
                   remind_at="2026-11-11 20:00", recurrence={"freq": "once"})],
            text,
        )
        assert items[0]["recurrence"]["fire_at"] == items[0]["remind_at"] is not None

    def test_bad_question_options_dropped(self):
        """只给一个选项的"问题"是死路 → 整条问题被丢掉，不再假装要确认。

        这里刻意给足了时间（remind_at 有值），好让"时间都没给"那条兜底不掺进来 ——
        本条的断言对象只有"选项不够两个"这一种坏。
        """
        text = "周五开会"
        items, _ = _tighten(
            [_item(kind="reminder", title="开会", source_text=text, due_at=None, list_key=None,
                   remind_at="2026-11-13 19:00", needs_confirm=True,
                   questions=[{"text": "是这周五吗", "options": [{"label": "是", "value": "once"}]}])],
            text,
        )
        assert items[0]["questions"] == []
        assert items[0]["needs_confirm"] is False, "问不出话就不要假装在问"

    def test_reminder_with_no_time_gets_a_question(self):
        text = "记得交表"
        items, _ = _tighten(
            [_item(kind="reminder", title="交表", source_text=text, due_at=None, list_key=None)],
            text,
        )
        assert items[0]["needs_confirm"] is True
        assert len(items[0]["questions"]) == 1

    def test_source_text_not_in_original_is_null_but_item_kept(self):
        text = "明天交高数作业"
        items, _ = _tighten([_item(source_text="模型自己编的一句话")], text)
        assert len(items) == 1, "找不到原文不丢条目：高亮不了，但事情还在"
        assert items[0]["source_start"] is None and items[0]["source_end"] is None

    def test_repeated_source_text_offsets_never_go_backwards(self):
        """同一句话出现两次时，offset 必须递增，否则前端高亮会切出错乱的区间。"""
        text = "开会，开会"
        items, _ = _tighten(
            [
                _item(kind="reminder", title="开会", source_text="开会", due_at=None, list_key=None),
                _item(kind="reminder", title="开会", source_text="开会", due_at=None, list_key=None),
            ],
            text,
        )
        starts = [i["source_start"] for i in items]
        assert starts == [0, 3], "第二个「开会」必须定位到第二处"

    def test_max_items_truncated_with_reason(self):
        text = "、" .join(f"第{i}件事" for i in range(1, 16))
        items, unresolved = _tighten(
            [_item(title=f"第{i}件事", source_text=f"第{i}件事") for i in range(1, 16)], text
        )
        assert len(items) == capture_parse.MAX_ITEMS
        assert any("12" in u for u in unresolved)

    def test_model_unresolved_passed_through(self):
        _, unresolved = _tighten([_item()], "明天交高数作业", ["「呃」这类口头语没整理"])
        assert "「呃」这类口头语没整理" in unresolved


# ---------------------------------------------------------------- JSON 抠取
class TestJsonExtraction:
    def test_fenced_json(self):
        got = capture_parse._extract_json('```json\n{"items": []}\n```')
        assert got == {"items": []}

    def test_prose_around_json(self):
        got = capture_parse._extract_json('好的，整理如下：\n{"items": []}\n希望有帮助')
        assert got == {"items": []}

    def test_broken_json_raises(self):
        for bad in ("", "   ", "抱歉我做不到", '{"items": [}'):
            with pytest.raises(capture_parse.ParseFailed):
                capture_parse._extract_json(bad)

    def test_missing_items_raises(self):
        with pytest.raises(capture_parse.ParseFailed):
            capture_parse.tighten({"foo": 1}, "明天交作业", _now())

    def test_items_not_a_list_raises(self):
        with pytest.raises(capture_parse.ParseFailed):
            capture_parse.tighten({"items": {"a": 1}}, "明天交作业", _now())


# ---------------------------------------------------------------- parse() 的失败路径
#
# 这几条要跑真协程，但仓库里没装 pytest-asyncio（装了也只是为一个文件装插件）。
# 所以统一 `asyncio.run(...)` 包一层 —— 和 tests/test_agent_checkpointer.py 同一做法。
class TestParseContract:
    """`parse()` 只验"上游出问题时抛什么"，不验它解析得好不好。"""

    def test_empty_text_raises_parse_failed(self):
        with pytest.raises(capture_parse.ParseFailed):
            asyncio.run(capture_parse.parse("   ", _now()))

    def test_model_garbage_raises_parse_failed(self, monkeypatch):
        class _Resp:
            content = "我不是 JSON"

        class _LLM:
            model_name = "fake"

            async def ainvoke(self, _messages):
                return _Resp()

        monkeypatch.setattr(capture_parse, "get_llm", lambda **_kw: _LLM())
        with pytest.raises(capture_parse.ParseFailed):
            asyncio.run(capture_parse.parse("明天交高数作业", _now()))

    def test_good_json_returns_contract_shape(self, monkeypatch):
        class _Resp:
            content = (
                '{"items": [{"kind": "task", "title": "交高数作业", '
                '"source_text": "明天交高数作业", "due_at": "2026-11-11 23:59"}], "unresolved": []}'
            )

        class _LLM:
            model_name = "fake"

            async def ainvoke(self, _messages):
                return _Resp()

        monkeypatch.setattr(capture_parse, "get_llm", lambda **_kw: _LLM())
        out = asyncio.run(capture_parse.parse("明天交高数作业", _now()))
        assert set(out) == {"items", "unresolved", "model", "latency_ms"}
        assert out["model"] == "fake"
        assert isinstance(out["latency_ms"], int)
        assert out["items"][0]["title"] == "交高数作业"

    def test_prompt_carries_now_and_text(self, monkeypatch):
        """时间必须由服务端给死：模型自己算「周五」十次里错三次。"""
        seen: list = []

        class _Resp:
            content = '{"items": []}'

        class _LLM:
            model_name = "fake"

            async def ainvoke(self, messages):
                seen.extend(messages)
                return _Resp()

        monkeypatch.setattr(capture_parse, "get_llm", lambda **_kw: _LLM())
        asyncio.run(capture_parse.parse("周五开会", _now()))
        human = [c for role, c in seen if role == "human"][0]
        assert "2026-11-10 22:00" in human
        assert "周二" in human
        assert "周五开会" in human
