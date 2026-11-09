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

from core import reminder_core, timeutil, user_prefs
from service import push as push_svc

logger = logging.getLogger(__name__)

JOB_KEY = "reminder.tick"

# 哪些来源用「标题 + 备注」直接发，不做二次生成
_PLAIN_SOURCES = ("manual", "focus", "timetable", "deadline", "words")

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


def _notify_content(reminder: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(reminder.get("title") or "提醒")[:60],
        "body": str(reminder.get("note") or "")[:120],
        "url": "/reminders",
    }


def build_content(owner_id: str, reminder: dict[str, Any], planned_at: int) -> dict[str, Any] | None:
    """这条提醒该推什么。返回 None = 没内容，不发（记 skipped_empty）。"""
    channels = str(reminder.get("channels") or "")
    if "push" not in [c.strip() for c in channels.split(",")]:
        # 第一波只实现了 push。inapp / calendar / local 要么在第二波（站内红点），
        # 要么是客户端自己的事（本地通知）。这里返回 None 而不是硬发一条，
        # 免得用户明明只勾了「站内」，手机却响了。
        return None

    action = str(reminder.get("action") or "notify")
    if action != "agent_deliver":
        return _notify_content(reminder)

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
        return _notify_content(reminder)

    # 未知来源（数据里出现新值）：宁可发标题，也不要静默丢掉 ——
    # 静默丢掉的后果是「设了提醒不响」，那是最难查的一类。
    return _notify_content(reminder)


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


def _fail_detail(res: dict[str, Any]) -> str:
    if int(res.get("skipped") or 0) > 0:
        return "push_not_configured"
    if int(res.get("gone") or 0) > 0:
        return "subscription_gone"
    return f"send_failed:sent={res.get('sent')}failed={res.get('failed')}"


# ---------- worker hook ----------


def due_subjects(job_key: str, planned_at: int) -> list[str]:
    """本轮有到期提醒的人。

    **不算内容**（规格事实 #6 点名的坏味道）：`service.push.due_subjects` 在
    due 阶段就把 build_message 跑了一遍，run_one 里又跑一遍，一次推送算两次内容。
    这里 due 只出人，内容留到 run_one —— 而且 run_one 里只算一次，算完直接发。
    """
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

    batch: list[dict[str, Any]] = []
    for item in items:
        rid = str(item["id"])
        at = int(item["next_fire_at"])
        if quiet:
            reminder_core.record_fire(rid, at, reminder_core.STATUS_QUIET, "quiet_hours")
            reminder_core.advance(rid, planned_at)
            continue
        if remaining <= 0:
            reminder_core.record_fire(rid, at, reminder_core.STATUS_CAP, f"cap={cap}")
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
    ok = int(res.get("sent") or 0) > 0
    status = reminder_core.STATUS_SENT if ok else reminder_core.STATUS_FAILED
    base_detail = None if ok else _fail_detail(res)

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
