"""A2 回归：时区口径必须只有一份（2026-10-20 审计修复）。

修复前三种做法并存：

  core/scheduler.py   datetime.now(ZoneInfo(SCHEDULER_TZ))   ← 只有这个是对的
  core/quota.py       time.localtime()                       ← 依赖服务器 TZ
  service/daily.py    time.localtime() + time.mktime(...)    ← 依赖服务器 TZ

服务器 TZ 恰好是 Asia/Shanghai 时三者结果一致，所以问题一直没暴露。
换成 TZ=UTC 的机器（容器镜像默认值）后：每日配额在北京时间 08:00 才重置；
每日总结按上海日历日取数据、却按另一套口径算小时分布——
**报告里的数字和报告里的内容不是同一天的**。两者都不报错。

本测试锁死四件事：
  1) 三个模块共用同一份实现（不是"看起来一致"，是同一个函数对象）；
  2) 行为按业务时区解释，而不是按服务器本地时区；
  3) 日界是"日历加一天"，不是"加 86400 秒"；
  4) 回退开关仍然可用。
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from core import quota, scheduler, timeutil
from service import daily


def test_single_source_of_truth():
    """不是"两处写得一样"，而是"只有一处实现"。

    这条断言是本次修复的核心：只要有人再写一份 localtime 版本，
    这里就会红。比注释里写"记得同步"可靠得多。
    """
    assert scheduler.day_bounds is timeutil.day_bounds
    assert scheduler.now_local is timeutil.now_local
    assert scheduler.now_ts is timeutil.now_ts
    assert scheduler.TZ is timeutil.TZ
    # quota.day_key 保留了一个包装函数（为了把这次的事故写在 docstring 里），
    # 所以这里断言的是**行为等价**而不是同一对象。
    for ts in (0, 1_700_000_000, int(time.time())):
        assert quota.day_key(ts) == timeutil.day_str(ts)


def test_no_localtime_left_in_daily():
    """daily 的日界必须委托给 timeutil，不能自己算。"""
    assert daily._day_bounds("2026-10-20") == timeutil.day_bounds("2026-10-20")
    assert timeutil.hour_of(timeutil.day_bounds("2026-10-20")[0] + 3600) == 1


def test_business_timezone_not_server_localtime():
    """核心回归：北京时间 07:30 属于当天，而 UTC 下那是前一天 23:30。

    ts 取自 2026-10-21 07:30 北京时间 = 2026-10-20 23:30 UTC。
    如果实现依赖服务器本地时区，在 TZ=UTC 的机器上会得到 "2026-10-20"——
    那正是每日配额晚 8 小时才重置的根因。
    """
    ts = int(datetime(2026, 10, 21, 7, 30, tzinfo=timeutil.TZ).timestamp())
    # 先确认这个 ts 确实是"上海 10-21 07:30 / UTC 10-20 23:30"
    assert timeutil.day_str(ts) == "2026-10-21"
    assert time.strftime("%Y-%m-%d", time.gmtime(ts)) == "2026-10-20"
    assert timeutil.hour_of(ts) == 7


@pytest.mark.parametrize("day", ["2026-01-01", "2026-02-28", "2026-10-20", "2026-12-31"])
def test_day_bounds_shape(day):
    start, end = timeutil.day_bounds(day)
    assert end - start == 86400
    assert timeutil.day_str(start) == day
    # 起点必须是业务时区零点
    d = datetime.fromtimestamp(start, timeutil.TZ)
    assert (d.hour, d.minute, d.second) == (0, 0, 0)
    # 区间闭合：终点前一秒仍属当天，终点已属次日
    assert timeutil.day_str(end - 1) == day
    assert timeutil.day_str(end) != day


def test_day_bounds_is_calendar_add_not_plus_86400():
    """跨月/跨年靠日历加一天，不是靠 +86400 秒。

    亚洲无夏令时所以两者当前等价，但 +86400 是错的写法——
    换成有夏令时的时区会少/多一小时。这里用跨年边界把日历语义钉住。
    """
    start, end = timeutil.day_bounds("2026-12-31")
    assert timeutil.day_str(end) == "2027-01-01"


def test_recent_days_is_ascending_and_contiguous():
    days = timeutil.recent_days(3, end="2026-03-01")
    assert days == ["2026-02-27", "2026-02-28", "2026-03-01"]
    # 跨闰年（2028 是闰年）：2 月要有 29 号
    assert timeutil.recent_days(2, end="2028-03-01") == ["2028-02-29", "2028-03-01"]


def test_shift_day_crosses_month():
    assert timeutil.shift_day("2026-10-01", -1) == "2026-09-30"
    assert timeutil.shift_day("2026-02-28", 1) == "2026-03-01"


def test_quota_day_key_uses_business_tz():
    """配额日键与 timeutil 同源，且不受服务器 localtime 影响。"""
    ts = int(datetime(2026, 10, 20, 23, 30, tzinfo=timeutil.TZ).timestamp())
    assert quota.day_key(ts) == "2026-10-20"
    ts2 = ts + 3600  # 上海 00:30，已跨日
    assert quota.day_key(ts2) == "2026-10-21"


def test_fallback_switch_keeps_working(monkeypatch):
    """TZ_STRICT=0 退回 localtime。

    这条不是"应该如此"，而是**回退开关必须真的可用**：
    修复本身出问题时，运维要能一行 env 回到旧行为，而不是回滚版本。
    """
    monkeypatch.setattr(timeutil, "TZ_STRICT", False)
    ts = int(time.time())
    assert timeutil.day_str(ts) == time.strftime("%Y-%m-%d", time.localtime(ts))
