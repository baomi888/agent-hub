"""把「每天 20:00 提醒一下」变成一个 .ics 日历。

## 为什么需要它

安卓的 Web Push 走 FCM（`fcm.googleapis.com`），中国大陆连不通，换浏览器也绕不开
（Edge/Chrome/三星浏览器底层都是 FCM）。而**系统日历不依赖任何推送通道**：
日历 App 用本机 AlarmManager 定时，断网也能响。所以对安卓用户，
日历订阅是目前唯一能做到「到点手机响」的路，且不需要装 APK、不需要企业资质、
不需要短信费。

## 内容为什么这么"空"

描述里刻意**不写任务标题、不写数字、不写计划名**，只有一句固定的话和一个链接。
理由是日历的刷新周期不可控（Google 日历 8–24 小时，DAVx5 可配，国产日历更随意）。
如果把「今天还剩 2 个任务」写进去，用户明天看到的可能是三天前的快照——**说了假话**。
而「点开看看今天还剩什么」在任何时刻都是真的。

信息量靠点开补齐（链接带 `?from=cal` 便于统计来源），这是唯一不会说假话的设计。
附带好处：token 泄露的后果从「泄密」降级成「别人也能加个提醒」。

## RFC 5545 的三个坑，每个都会让国产日历静默丢弃内容

1. **必须是 CRLF**。很多实现把 LF 也当换行，但 Outlook/部分安卓日历严格按 CRLF 切。
2. **单行不超过 75 个八位组（字节），超出用「单个空格 + 续行」折**。
   ★ 中文一个字 3 字节，「知穗 · 看看今天的事」这种十个字就 30 字节，
     一行描述写到二三十个字必然超。按字符数判断会觉得"才 40 个字，没超"。
   ★ 也不能按 75 字节硬切——切在多字节字符中间会产生非法 UTF-8，
     客户端会把整行丢掉，表现为「描述是空白的」。
3. **带 TZID 引用就必须有 VTIMEZONE 块**，否则 Outlook 直接拒绝整个文件。
   中国没有夏令时，所以 STANDARD 一块就够，DTSTART 固定写 19700101。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from core import config

# 事件的 UID 必须**稳定**：客户端靠它判断「这是同一个事件的新版本」还是「一个新事件」。
# 不稳定的话每天刷新都会多出一个重复提醒，用户会看到十几个"知穗 · 看看今天的事"。
# 用 token 的摘要而不是 token 本身：UID 在日历里对用户可见（有的客户端会显示），
# 直接放 token 等于把凭证贴在屏幕上。
_UID_DOMAIN = "myzhisui.cn"

_DESCRIPTION = (
    "点开看看今天还剩什么，做完就关掉，不做别的打扰。\\n\\n"
    "https://myzhisui.cn/?from=cal"
)

# 提前量：事件在 20:00 开始，提醒设在开始那一刻（PT0M）。
# 不提前是因为 20:00 本身就是"还来得及做完"的最后一段，再提前就变成催下午了。
_SUMMARY = "知穗 · 看看今天的事"

# 让客户端隔 12 小时再来拉一次。这只是"建议"，多数客户端会按自己的策略来，
# 所以前面那段"内容不能写数字"的结论才成立。
_REFRESH = "PT12H"


def _fold(line: str) -> str:
    """按 RFC 5545 §3.1 折行（75 字节上限，续行以单个空格开头）。

    逐字符累加再判断字节数，而不是按 75 切片——见模块注释第 2 条。
    """
    if len(line.encode("utf-8")) <= 75:
        return line
    out: list[str] = []
    cur = ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            # 续行的前导空格也算在 75 字节里，所以 cur 从 " " + ch 起
            cur = " " + ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return "\r\n".join(out)


def _uid(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"zhisui-daily-{digest}@{_UID_DOMAIN}"


def _dtstamp() -> str:
    """DTSTAMP 必须是 UTC 且带 Z。

    ★ 用 timezone.utc 而不是 datetime.UTC：后者是 Python 3.11 才有的，
    线上是 3.10（本地 venv 是 3.12），本地跑得好好的、一部署就
    `ImportError: cannot import name 'UTC'`，后端整个起不来。
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _first_start_utc8(hour: int, minute: int) -> str:
    """今天（北京时间）的提醒时刻，格式 `YYYYMMDDTHHMMSS`（本地时间，靠 TZID 解释）。

    今天这个点已经过了也没关系：RRULE 每天重复，客户端会从下一个未过期的实例开始提醒。
    """
    # 用 UTC+8 固定偏移而不是 Asia/Shanghai 时区库：中国 1991 年后没有夏令时，
    # 而 zoneinfo 在部分精简镜像里没有 tzdata，import 就会炸。
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    return now_cn.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime(
        "%Y%m%dT%H%M%S"
    )


def build_ics(
    token: str, hour: int | None = None, minute: int | None = None
) -> str:
    """生成整份日历。token 只影响 UID（稳定 + 不暴露凭证）。

    `hour` / `minute` 是**这个用户**自己设的提醒时刻（见 core/reminder_store.py）。
    传 None 时回落到 `config.PUSH_HOUR/PUSH_MINUTE` 这个全站默认值——
    保留默认是为了让测试和"只想生成一份样例"的调用方不必自己算。
    """
    if hour is None or minute is None:
        hour = max(0, min(23, int(config.PUSH_HOUR)))
        minute = max(0, min(59, int(config.PUSH_MINUTE)))
    else:
        hour = max(0, min(23, int(hour)))
        minute = max(0, min(59, int(minute)))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ZhiSui//Daily Reminder//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        # 下面三行是给客户端的"这个日历叫什么"，各客户端认的不一样，都给上
        "X-WR-CALNAME:知穗 · 每日提醒",
        "X-WR-CALDESC:每天提醒你看看今天还剩什么。不要了就在日历里删掉这个日历。",
        "X-WR-TIMEZONE:Asia/Shanghai",
        f"REFRESH-INTERVAL;VALUE=DURATION:{_REFRESH}",
        f"X-PUBLISHED-TTL:{_REFRESH}",
        # VTIMEZONE：带 TZID 引用就必须定义，见模块注释第 3 条
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        f"UID:{_uid(token)}",
        f"DTSTAMP:{_dtstamp()}",
        f"DTSTART;TZID=Asia/Shanghai:{_first_start_utc8(hour, minute)}",
        # 从 20:00 起画 10 分钟。
        #
        # 这 10 分钟**不是"提醒持续 10 分钟"**，只是让这条事件在日历里有个可见的长度：
        # 提醒本身是 VALARM 在起点响一次就结束。之所以不写成全天事件
        # （DTSTART;VALUE=DATE），是因为全天事件会被手机折进日历顶部的横条，
        # 很容易被当成"又是哪个日程"划掉；带具体时刻的 10 分钟块才会走到提醒通道。
        "DURATION:PT10M",
        "RRULE:FREQ=DAILY",
        f"SUMMARY:{_SUMMARY}",
        f"DESCRIPTION:{_DESCRIPTION}",
        "URL:https://myzhisui.cn/?from=cal",
        # 提醒是"顺手看一眼"，不该在日历上占成"忙碌"
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER:PT0M",
        f"DESCRIPTION:{_SUMMARY}",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    # 每一行（包括较短的行）都在这里统一折行，避免调用方漏处理某一行。
    # 末尾 CRLF 是 RFC 要求的「最后一行也要有换行」。
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def describe(
    token: str, hour: int | None = None, minute: int | None = None
) -> dict[str, str]:
    """给前端用的两个链接（订阅 / 下载）与人类可读的时刻。

    时刻要和 `build_ics` 用同一份值，否则设置页会显示「每天 07:30」
    而日历里那条事件还落在 20:00 —— 用户会认为改了没生效。
    """
    if hour is None or minute is None:
        hour = max(0, min(23, int(config.PUSH_HOUR)))
        minute = max(0, min(59, int(config.PUSH_MINUTE)))
    return {
        "subscribe": f"/api/calendar/{token}.ics",
        "download": f"/api/calendar/{token}.ics?dl=1",
        "time": f"{hour:02d}:{minute:02d}",
        "uid": _uid(token),
    }
