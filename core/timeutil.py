"""时间口径的唯一入口。

为什么要有这个文件
------------------
在这之前，"今天是哪一天"有三处两种答案：

  core/scheduler.py   datetime.now(ZoneInfo(SCHEDULER_TZ))   ← 正确
  core/quota.py       time.localtime()                       ← 依赖服务器 TZ
  service/daily.py    time.localtime() + time.mktime(...)    ← 依赖服务器 TZ

服务器 TZ 恰好是 Asia/Shanghai 时三者结果一致，所以这个分裂一直没暴露。
一旦换到 TZ=UTC 的机器（容器镜像的默认值），后果不是报错，而是：

  * 每日配额在北京时间 08:00 才重置 —— 用户以为"过了零点就恢复了"，实际没有；
  * 每日总结按上海日历日取数据、却按 UTC 日算小时分布 —— 报告里的数字和
    报告里的内容不是同一天的。

两者都不报错，只是安静地错。这是最难排查的一类问题，所以把口径收成一个模块。

两条规则
--------
1. **落库一律 Unix 秒**（与 sessions / messages / usage 表一致），本模块不改变这点；
2. **解释一律走这里**。业务代码禁止直接 `time.localtime()` / 裸 `datetime.now()`。

`TZ_STRICT=0` 时 `day_str` / `day_bounds` 退回 `time.localtime()` 旧行为，
作为紧急回退开关；`now_local()` 不受影响（它本来就没用服务器 TZ）。
"""

from __future__ import annotations

import os
import time
from datetime import date as _date
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import config

TZ_NAME = getattr(config, "SCHEDULER_TZ", "Asia/Shanghai") or "Asia/Shanghai"
TZ = ZoneInfo(TZ_NAME)

# 旧行为开关：置 0 时 day_str / day_bounds 退回 time.localtime()。
# 只在"修复本身出问题"时用，正常情况下不该动。
TZ_STRICT = os.getenv("TZ_STRICT", "1") not in ("0", "false", "False")

_DAY_FMT = "%Y-%m-%d"


# ---------- 当前时刻 ----------


def now_ts() -> int:
    """当前 Unix 秒。与 time.time() 等价，存在只为统一调用点。"""
    return int(time.time())


def now_local() -> datetime:
    """带时区的当前时刻（SCHEDULER_TZ，默认 Asia/Shanghai）。

    禁止裸 `datetime.now()`——那是服务器的本地时间，不是业务时区。
    """
    return datetime.now(TZ)


# ---------- 日期解释 ----------


def day_str(ts: int | float | None = None) -> str:
    """Unix 秒 → 业务时区的日期串（YYYY-MM-DD）。

    这是 quota 的"哪一天"与 daily 的"哪一天"共用的唯一换算。
    旧实现用 time.localtime()，在 TZ=UTC 的机器上会整体偏 8 小时。
    """
    t = time.time() if ts is None else float(ts)
    if not TZ_STRICT:
        return time.strftime(_DAY_FMT, time.localtime(t))
    return datetime.fromtimestamp(t, TZ).strftime(_DAY_FMT)


# 语义更清楚的历史别名：quota.py 一直在用 day_key 这个名字
day_key = day_str


def hour_of(ts: int | float) -> int:
    """Unix 秒 → 业务时区的小时（0~23）。用于"活跃时段"这类分布统计。"""
    return datetime.fromtimestamp(float(ts), TZ).hour


def parse_day(day: str) -> _date:
    """'YYYY-MM-DD' → date。格式错直接抛，别让坏日期悄悄变成今天。"""
    return datetime.strptime(day, _DAY_FMT).date()


def day_bounds(day: str) -> tuple[int, int]:
    """把一个业务时区日历日解释成区间 [当日起点, 次日起点) 的 Unix 秒。

    用 `+ timedelta(days=1)` 而不是 `+ 86400`：亚洲无夏令时，两者当前等价，
    但 `+86400` 在有夏令时的时区会少/多一小时。这类"当前等价所以先这样写"
    正是日后换时区时最先炸的地方。

    与 core/scheduler.py 原有的 day_bounds 保持同一语义（那个版本本来就是对的，
    这里只是把实现搬过来，让 quota / daily 也走同一条路）。
    """
    d = datetime.strptime(day, _DAY_FMT).replace(tzinfo=TZ)
    return int(d.timestamp()), int((d + timedelta(days=1)).timestamp())


def day_start_ts(day: str) -> int:
    """某业务日历日的起点 Unix 秒。"""
    return day_bounds(day)[0]


def shift_day(day: str, delta_days: int) -> str:
    """日期加减。用于"近 N 天"这类回溯，避免用 `- 86400*n` 手工算。"""
    d = parse_day(day) + timedelta(days=delta_days)
    return d.strftime(_DAY_FMT)


def recent_days(n: int, end: str | None = None) -> list[str]:
    """从 end（默认今天）往回数 n 个业务日历日，按时间升序返回。"""
    end = end or day_str()
    n = max(1, int(n))
    return [shift_day(end, -(n - 1 - i)) for i in range(n)]
