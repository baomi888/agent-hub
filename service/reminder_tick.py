"""提醒中心的调度侧：每分钟一跳，四道闸，一次只给一个人发一条。

## 闸门顺序不是随便排的

    ① 是否该发（due）→ ② 静默时段 → ③ 每日上限 → ④ 有没有内容

顺序决定「拦下来之后记什么账」，也决定成本：静默时段和上限都是纯查表，
放在「算内容」之前，就能把最贵的两步（`service.cet.overview` 会查库、
`service.checkin.compute_streak` 会扫打卡记录）省掉。

**每一道拦下都必须写 `reminder_fires`**。不写的话「今天为什么没响」永远查不出来：
用户只看到「设了 22:00，手机没动」，服务端日志里一片安静，因为你拦得很安静。

## 为什么 run_one 的 subject 是「人」而不是「提醒」

worker 的幂等键是 `(job_key, subject_id, planned_at)`。把 subject 取成 owner：

* 同一个人同一分钟里的三条提醒 = 一轮只发一条（**同刻合并**，天然实现，
  不需要在 tick 里再做一次分组）；
* 不同分钟仍然各发一次，不受影响；
* 「每人每天最多 6 条」这条产品承诺只需要在一个地方数（`user_prefs.day_count`）。

## 投递：一条推送 + 一条邮件兜底

`channels` 里只认 `push`，**邮件不是另一个 channel**，它是同一条提醒在
「推送送不出去」时的第二个投递方式。这两件事必须分开：

* `channels` 表达**用户意图**（「这条要不要提醒我」）；
* 投递方式取决于**设备能力**（这台机器收不收得到通知）。

把邮件做成 channel 会让用户在每条待办上都面对一个「用推送还是用邮件」的
选择，而他既不知道自己的手机走不走 FCM，也不该知道。所以由服务端决定：
推送零投递 → 用已验证的邮箱补一封。安卓（FCM 国内连不通）永远是零投递，
邮件就是它唯一能收到的提醒。

## 内容从哪来

`action=agent_deliver` 时按 `source` 分流，**没内容就不发**（`skipped_empty`）。
这是这个产品最不能丢的一条：一条「今天没什么要说的」的通知，价值是负的。

* `system`  —— 老「每日提醒」那条，内容仍然是 `service.push.build_message`
  （六级优先、其次打卡、都没有返回 None），**和改造前逐字一致**；
* `cet` / `checkin` —— 同一个 builder，但只认自己那一类，另一类出现就当没内容；
* `delegate` —— 读 `delegations` 的原文案（只读，绝不回写）；
* 其余来源（番茄钟 / 课表 / DDL / 单词）—— 用标题 + 备注，不做二次生成：
  这些提醒的价值是「提醒」，不是「替你写一段话」。

复用一个 builder 而不是抄一份，是为了避免「改造后四六级的文案变了」这类
看起来像回归、实则无从对照的问题。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from core import config, mail_store, mailer, reminder_core, timeutil, user_prefs
from service import course_reminder
from service import push as push_svc

logger = logging.getLogger(__name__)

JOB_KEY = "reminder.tick"

# 哪些来源用「标题 + 备注」直接发，不做二次生成
# `task` 是待办的联动提醒：内容就是待办标题 + 备注，没有别的可生成。
_PLAIN_SOURCES = ("manual", "focus", "timetable", "deadline", "words", "task")

# 来源 → 点开之后去哪。给不出更精确的落地页时，统一去校园工具集。
_TOOL_URL = "/tools"


# ---------- 内容 ----------


def _daily_brief(owner_id: str, planned_at: int) -> dict[str, Any] | None:
    """老「每日提醒」的内容（六级 > 打卡 > 无）。异常一律吞掉当无内容。

    单个用户的数据异常不该让整轮不发（`service.push.due_subjects` 里也是这个口径），
    更不能让 worker 把它记成 job 失败 —— 那会触发三次重试，重试期间这个人
    还是收不到，而日志里显示的是「调度失败」，方向全错。
    """
    try:
        return push_svc.build_message(owner_id, timeutil.day_str(planned_at))
    except Exception:  # noqa: BLE001
        logger.exception("reminder build_message failed owner=%s", owner_id)
        return None


def _split_note(note: object) -> tuple[str, str, str]:
    """`"20 分钟后上课；[官渡]学科楼 (三) 室 401；老师：张老师"` → (head, 教室, 老师)。

    `service/course_reminder.py::_note` 的逆运算，用来把一句话拆回三个字段。
    拆而不是改存储：`note` 还要给邮件兜底和 `_merge` 用，那边读的是整句。
    """
    segs = [s.strip() for s in str(note or "").split("；") if s.strip()]
    head = segs[0] if segs else ""
    room = next((s for s in segs[1:] if not s.startswith("老师")), "")
    teacher = next((s for s in segs[1:] if s.startswith("老师")), "")
    return head, room, teacher


def _class_content(reminder: dict[str, Any]) -> dict[str, Any]:
    """上课提醒的排版：上面一行「哪门课 · 还有多久」，下面一行「去哪」。

    之前是 title=`课名 · 教室`、body=`20 分钟后上课；教室；老师`，两行都从
    课名或教室起头，锁屏一行只放得下十几个字 —— 截出来是
    `计算机综合实验 · [官渡]学科楼 (三) 室 4...` / `20 分钟后上课；[官渡]学科楼...`，
    最该一眼看到的两件事（什么课、还剩几分钟）反而要拼着读。

    为什么不把「20 分钟后上课」直接写进库里的 `title`：那条 title 还给
    `course_reminder.upcoming()` 的「接下来几次课」清单用，一周后的课顶着
    「20 分钟后上课」就是假话。相对时间只在**发通知这一刻**成立，所以只在这里拼。
    """
    head, room, teacher = _split_note(reminder.get("note"))
    course = str(reminder.get("title") or "").strip() or "上课"
    # 库里的 title 是「课名 · 教室」，教室已经在下面一行了，剥掉免得重复。
    if room and course.endswith(room):
        course = course[: -len(room)].rstrip(" ·").strip()
    title = f"{course} · {head}" if head else course
    return {
        "title": title[:60],
        # 教室优先、老师其次：教室被截断就没法用了，老师截断还猜得到。
        "body": (room or teacher)[:120],
        "url": "/tools?tool=schedule",
    }


def _due_gap_label(due_at: int, now: int) -> str:
    """距截止还有多久，说人话。

    只给量级不给精度：锁屏一行，「还有 2 小时」够用了，
    「还有 1 小时 47 分钟」精确但没人拿它做决定，还把行占满。
    """
    diff = int(due_at) - int(now)
    # 正点这一分钟说「现在截止」，不是「已过期」：用户把提醒设成和截止同一
    # 时刻是常见做法（「到点就叫我」），这时告诉他「已经过期了」纯属添堵。
    if diff <= -60:
        return "已过期"
    if diff <= 0:
        return "现在截止"
    if diff < 60:
        return "马上到期"
    if diff < 3600:
        return f"还有 {diff // 60} 分钟"
    if diff < 86400:
        return f"还有 {diff // 3600} 小时"
    return f"还有 {diff // 86400} 天"


def _due_stamp_label(due_at: int, now: int) -> str:
    """绝对时刻，只在备注为空时顶到第二行 —— 那时候它是唯一的信息。"""
    due = datetime.fromtimestamp(int(due_at), timeutil.TZ)
    today = datetime.fromtimestamp(int(now), timeutil.TZ).date()
    hm = due.strftime("%H:%M")
    days = (due.date() - today).days
    if days == 0:
        return f"今天 {hm} 截止"
    if days == 1:
        return f"明天 {hm} 截止"
    if days == 2:
        return f"后天 {hm} 截止"
    return f"{due.month}月{due.day}日 {hm} 截止"


def _task_content(
    owner_id: str, reminder: dict[str, Any], planned_at: int
) -> dict[str, Any]:
    """待办提醒：上面「待办名 · 还有多久」，下面「备注」。

    和上课提醒同一条道理（见 `_class_content`）：锁屏一行只放得下十几个字，
    原来 title=`交实验报告`、body=`截止 11-15 23:59`，看着也不算挤 ——
    但「还有多久」这件事根本没被说出来，用户得自己拿今天和 11-15 相减。

    `due_at` 不在 reminders 行里：待办的提醒时刻是用户自己挑的（可以比截止
    早一天），不是固定偏移，所以得回 tasks 表拿。拿不到（任务刚被删、
    或者只设了提醒没设截止）就不拼时间 —— 宁可少一句，不编一个假时间。
    """
    title = str(reminder.get("title") or "待办")[:60]
    note = str(reminder.get("note") or "").strip()

    due_at: int | None = None
    ref = str(reminder.get("source_ref") or "")
    if ref:
        try:
            from core import task_core  # 函数内导入：task_core 依赖 reminder_core

            task = task_core.get(owner_id, ref)
            due_at = int(task["due_at"]) if task and task.get("due_at") else None
        except Exception:  # noqa: BLE001 —— 查不到截止时间不该让这条提醒发不出去
            logger.exception("task lookup failed owner=%s task=%s", owner_id, ref)

    gap = _due_gap_label(due_at, planned_at) if due_at else ""
    body = note or (_due_stamp_label(due_at, planned_at) if due_at else "")
    return {
        "title": f"{title} · {gap}" if gap else title,
        "body": body[:120],
        "url": "/reminders",
    }


def _notify_content(
    owner_id: str, reminder: dict[str, Any], planned_at: int
) -> dict[str, Any]:
    # 上课提醒点开应该落在**课程表**，不是提醒列表：用户想知道的是
    # 「下一节在哪、几点下课」，而提醒列表里没有这节课（`list_reminders`
    # 把 timetable 排除了，见 core/reminder_core.py）。
    # 落在提醒列表的话，点完看到的是一片自己的待办 —— 和那条通知毫无关系。
    source = str(reminder.get("source") or "")
    if source == "timetable":
        return _class_content(reminder)
    if source == "task":
        return _task_content(owner_id, reminder, planned_at)
    return {
        "title": str(reminder.get("title") or "提醒")[:60],
        "body": str(reminder.get("note") or "")[:120],
        "url": "/reminders",
    }


def build_content(owner_id: str, reminder: dict[str, Any], planned_at: int) -> dict[str, Any] | None:
    """这条提醒该推什么。返回 None = 没内容，不发（记 skipped_empty）。"""
    channels = str(reminder.get("channels") or "")
    if "push" not in [c.strip() for c in channels.split(",")]:
        # `push` 在这里代表「要提醒我」，不代表「必须走 Web Push」——
        # 送不出去时由邮件兜底，见模块注释「投递」一节。
        # inapp / calendar / local 要么在第二波（站内红点），
        # 要么是客户端自己的事（本地通知）。这里返回 None 而不是硬发一条，
        # 免得用户明明只勾了「站内」，手机却响了。
        return None

    action = str(reminder.get("action") or "notify")
    if action == "suggest_focus":
        # 「开始专注」必须**换个落点**，不能只换文案：点通知直接进专注计时。
        # 在这之前它走的是和「只提醒」一模一样的分支 —— 界面上多一个选项、
        # 行为上没有任何区别，用户迟早会发现自己在选一个摆设。
        return {
            "title": str(reminder.get("title") or "该专注了")[:60],
            "body": str(reminder.get("note") or "")[:120] or "点一下开始一段专注。",
            "url": "/tools?tool=pomodoro",
        }
    if action != "agent_deliver":
        return _notify_content(owner_id, reminder, planned_at)

    source = str(reminder.get("source") or "manual")
    if source == "system":
        return _daily_brief(owner_id, planned_at)
    if source in ("cet", "checkin"):
        want = "daily-cet" if source == "cet" else "daily-checkin"
        brief = _daily_brief(owner_id, planned_at)
        return brief if brief and brief.get("tag") == want else None
    if source == "delegate":
        title = str(reminder.get("source_title") or reminder.get("title") or "该做今天的委托了")[:60]
        body = str(reminder.get("source_note") or reminder.get("note") or "")[:120]
        return {"title": title, "body": body, "url": "/delegate"}
    if source in _PLAIN_SOURCES:
        return _notify_content(owner_id, reminder, planned_at)

    # 未知来源（数据里出现新值）：宁可发标题，也不要静默丢掉 ——
    # 静默丢掉的后果是「设了提醒不响」，那是最难查的一类。
    return _notify_content(owner_id, reminder, planned_at)


def _merge(batch: list[dict[str, Any]], planned_at: int) -> dict[str, Any]:
    """同刻合并成一条。一条时原样发，多条时列出前三条。

    tag 的取值刻意区分「单条」与「合并」：
    单条用 `rm-<id>`，同一条提醒连续几次不会被叠成一堆；
    合并用 `rm-merge-<planned_at>`，同一分钟的合并结果互相替换。
    """
    if len(batch) == 1:
        item = batch[0]
        payload = dict(item["content"])
        payload["tag"] = f"rm-{item['id']}"
        return payload
    first = batch[0]["content"]
    titles = [str(item["content"].get("title") or "") for item in batch]
    return {
        "title": f"{titles[0]} 等 {len(batch)} 条"[:80],
        "body": " · ".join(t for t in titles[:3] if t)[:200],
        "url": str(first.get("url") or "/reminders"),
        "tag": f"rm-merge-{int(planned_at)}",
    }


def _empty_reason(reminder: dict[str, Any]) -> str:
    channels = str(reminder.get("channels") or "")
    if "push" not in [c.strip() for c in channels.split(",")]:
        return f"channels={channels or '-'}"
    return f"no_content来源={reminder.get('source')}"


# ---------- 邮件兜底 ----------


def _mail_text(batch: list[dict[str, Any]], planned_at: int) -> tuple[str, str]:
    """邮件比通知多出的地方是**能写全**，所以正文不再砍成前三条。

    通知的 body 卡在 200 字符是为了锁屏上不滚动；邮件没有这个约束，
    把 N 条都列出来才有意义，否则用户点开邮件看到的和锁屏一模一样。
    """
    titles = [str(item["content"].get("title") or "提醒") for item in batch]
    if len(batch) == 1:
        subject = titles[0]
    else:
        subject = f"{titles[0]} 等 {len(batch)} 件"
    # 本地时刻有信息量：同一封信里可能是 15:00 的作业和 19:00 的班会
    stamp = datetime.fromtimestamp(int(planned_at), timeutil.TZ).strftime("%m月%d日 %H:%M")
    lines = [f"{stamp} 的提醒：", ""]
    for item in batch:
        title = str(item["content"].get("title") or "提醒")
        body = str(item["content"].get("body") or "").strip()
        lines.append(f"· {title}")
        if body:
            lines.append(f"  {body}")
    lines += [
        "",
        f"打开看详情：{config.PUBLIC_BASE_URL}/reminders",
        "",
        "—— ",
        "这封邮件是苞米发来的。没能给你弹出应用通知，所以改用邮件提醒。",
        f"不想收的话，在「设置 → 提醒」里关掉邮件提醒即可：{config.PUBLIC_BASE_URL}/reminders?tab=settings",
    ]
    return subject, "\n".join(lines)


def _fallback_mail(
    owner_id: str, batch: list[dict[str, Any]], planned_at: int
) -> dict[str, Any] | None:
    """推送一条都没送出去时，用邮件兜底。

    **只在 push 零投递时调用**，不是并行发两份。已经能在手机上看到通知的人，
    再收一封同样内容的邮件只是打扰；而安卓（FCM 在国内连不通）永远是零投递，
    这封邮件才是他唯一能收到的提醒。

    任何异常都不往外抛：一封邮件发不出去，不该把整轮 job 记成失败 ——
    那样 worker 会重试三次，而重试期间这个人的其他提醒照样收不到，
    日志里却写着「调度失败」，排查方向全错。
    """
    try:
        address = mail_store.verified_address(owner_id)
        if not address:
            return None
        if not config.mail_ready():
            # 服务端没配发信账号：返回一个明确的 not_configured，
            # 让 detail 里能看出「本来该发邮件，但没配」，而不是干脆没提。
            return {"ok": False, "error": "not_configured", "retryable": False, "detail": ""}
        subject, text = _mail_text(batch, planned_at)
        return mailer.send(address, subject, text)
    except Exception:  # noqa: BLE001
        logger.exception("reminder mail fallback failed owner=%s", owner_id)
        return {"ok": False, "error": "exception", "retryable": True, "detail": ""}


def _fail_detail(res: dict[str, Any], mail_res: dict[str, Any] | None = None) -> str:
    if int(res.get("skipped") or 0) > 0:
        base = "push_not_configured"
    elif int(res.get("gone") or 0) > 0:
        base = "subscription_gone"
    else:
        sent = int(res.get("sent") or 0)
        failed = int(res.get("failed") or 0)
        # 四个计数全零 = 这人名下压根没有订阅设备。写成 `send_failed:sent=0failed=0`
        # 会把它伪装成「发失败」，而排查的人会去查推送服务方——方向从一开始就错了。
        # 线上冒烟就是踩到这个：假 owner 没有订阅，流水写成 send_failed。
        if sent == 0 and failed == 0:
            base = "no_subscription"
        else:
            base = f"send_failed:sent={sent}failed={failed}"
    # 邮件也失败时把原因接在后面。不接的话「两条通道都没成」看起来和
    # 「只有推送失败」一模一样，而这两种情况的处理方式完全不同：
    # 前者要去查 SMTP 配置，后者不用管。
    if mail_res is not None and not mail_res.get("ok"):
        err = str(mail_res.get("error") or "unknown")
        if err != "not_configured":
            base = f"{base}+mail:{err}"
    return base


# ---------- worker hook ----------


def due_subjects(job_key: str, planned_at: int) -> list[str]:
    """本轮有到期提醒的人。

    **不算内容**（规格事实 #6 点名的坏味道）：`service.push.due_subjects` 在
    due 阶段就把 build_message 跑了一遍，run_one 里又跑一遍，一次推送算两次内容。
    这里 due 只出人，内容留到 run_one —— 而且 run_one 里只算一次，算完直接发。
    """
    try:
        # ★ 课表同步必须排在 due_owners 之前，而且**每一轮都跑**。
        #
        # `source='timetable'` 的行是 `freq='once'`：响过一次 `advance()` 就把它
        # `enabled=0`（见 reminder_core.advance）。不在这里重建，这门课从此再也不响，
        # 而且列表上看起来只是「没有下一次」—— 用户只会说「上课提醒时好时不好」。
        # 反过来，只在「这个人这一分钟有到期提醒」时才同步也不行：课表提醒的到期
        # 时刻本来就不密集，链条一旦断掉就永远接不上。所以按 `course_reminder.owners()`
        # 全量走一遍，靠 sync 内部的「值没变就不写」把开销压到一次读。
        course_reminder.sync_all(planned_at)
    except Exception:  # noqa: BLE001
        logger.exception("reminder.tick course sync failed")
    try:
        # 迟到的先推进（不补发），否则它会永远躺在「已到期」里被每轮扫出来
        reminder_core.catch_up(planned_at)
        return reminder_core.due_owners(planned_at)
    except Exception:  # noqa: BLE001
        logger.exception("reminder.tick due_subjects failed")
        return []


def run_one(job_key: str, subject_id: str, planned_at: int) -> dict[str, Any]:
    """给一个人发这一分钟的提醒（多条则合并成一条）。"""
    owner_id = str(subject_id)
    items = reminder_core.due_for_owner(owner_id, planned_at)
    if not items:
        return {"skipped": True, "reason": "nothing_due", "tokens_out": 0}

    prefs = user_prefs.get_time_prefs(owner_id)
    # 用 planned_at 而不是 now 判静默：约定 22:00 就不要因为 tick 晚跑 90 秒
    # 而变成「22:01 不在静默时段」—— 判断要可复现，才能写测试。
    now_local = datetime.fromtimestamp(int(planned_at), timeutil.TZ)
    quiet = user_prefs.in_quiet_hours(now_local, prefs)
    cap = int(prefs.get("daily_cap") or 0)
    used = user_prefs.day_count(owner_id, timeutil.day_str(planned_at))
    remaining = max(0, cap - used)
    # 来源开关一次取全：每个人每轮只查一次库，别在循环里按条查。
    # `manual` / `system` 不参与开关：前者是用户亲手建的，后者是老「每日回顾」，
    # 两个都没有「来源」可关（`set_source_pref` 也明确拒绝 system）。
    enabled_sources = user_prefs.source_prefs(owner_id)

    batch: list[dict[str, Any]] = []
    for item in items:
        rid = str(item["id"])
        at = int(item["next_fire_at"])
        source = str(item.get("source") or "manual")
        if quiet:
            reminder_core.record_fire(rid, at, reminder_core.STATUS_QUIET, "quiet_hours")
            reminder_core.advance(rid, planned_at)
            continue
        if remaining <= 0 and source != "timetable":
            reminder_core.record_fire(rid, at, reminder_core.STATUS_CAP, f"cap={cap}")
            reminder_core.advance(rid, planned_at)
            continue
        # `task` 同理：待办提醒的开关是「待办详情里的提醒时间」，
        # 放进来源开关里会让「关掉某个来源」把别人的待办一起弄哑。
        # `timetable` 也有自己的开关（`course_reminder_prefs.enabled`），
        # 两套开关并存的结果一定是「开了一个、另一个还关着」，用户看到的
        # 是「我明明打开了上课提醒，就是不响」。
        configurable = (
            source in reminder_core.SOURCES
            and source not in ("manual", "system", "task", "timetable")
        )
        if configurable and not enabled_sources.get(source):
            # 设置页对用户的承诺是「关掉一个来源，已经建好的那几条会一起停」。
            # 只挡新建、不挡已建，就是文案与事实不符。这里记 skipped_empty
            # 而不是新增状态值：前端按五档展示，加值会漏样式。
            reminder_core.record_fire(
                rid, at, reminder_core.STATUS_EMPTY, f"source_disabled:{source}"
            )
            reminder_core.advance(rid, planned_at)
            continue
        content = build_content(owner_id, item, planned_at)
        if not content:
            reminder_core.record_fire(rid, at, reminder_core.STATUS_EMPTY, _empty_reason(item))
            reminder_core.advance(rid, planned_at)
            continue
        batch.append({"id": rid, "at": at, "content": content})

    if not batch:
        return {"due": len(items), "fired": 0, "skipped": True, "tokens_out": 0}

    payload = _merge(batch, planned_at)
    res = push_svc.send_to_user(owner_id, payload)
    push_ok = int(res.get("sent") or 0) > 0
    # 推送零投递才发邮件。`no_subscription`（安卓）和 `send_failed`（推送服务方挂了）
    # 两种都算零投递 —— 对用户来说都是「手机上没动静」，兜底不该挑原因。
    mail_res = None if push_ok else _fallback_mail(owner_id, batch, planned_at)
    mail_ok = bool(mail_res and mail_res.get("ok"))
    ok = push_ok or mail_ok
    status = reminder_core.STATUS_SENT if ok else reminder_core.STATUS_FAILED
    base_detail = None if ok else _fail_detail(res, mail_res)
    if ok and mail_ok and not push_ok:
        # 只有邮件送达。记下来，否则「他明明收到了信，流水却写着没发」
        # 是另一个方向的查不出来。前缀 `via:` 不影响 day_count（它只排 merged_into）。
        base_detail = "via:mail"

    now = timeutil.now_ts()
    for index, item in enumerate(batch):
        detail = base_detail
        if ok and index > 0:
            # 合并进去的那几条也记 sent：它们确实送到了（在同一条通知里）。
            # 每日上限靠 detail 前缀把它们排除在计数之外，见 user_prefs.day_count。
            detail = f"merged_into:{batch[0]['id']}@{batch[0]['at']}"
        reminder_core.record_fire(item["id"], item["at"], status, detail)
        reminder_core.advance(item["id"], now)

    return {
        "due": len(items),
        "fired": len(batch),
        "merged": max(0, len(batch) - 1),
        "push": res,
        "mail": mail_res,
        "tokens_out": 0,
    }


def run_job(job_key: str, planned_at: int) -> dict[str, Any]:
    """兜底的整体入口（worker 优先用 due_subjects + run_one）。"""
    stats = {"due": 0, "fired": 0}
    for owner_id in due_subjects(job_key, planned_at):
        stats["due"] += 1
        res = run_one(job_key, owner_id, planned_at)
        stats["fired"] += int(res.get("fired") or 0)
    return stats
