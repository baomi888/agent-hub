"""Web Push：订阅内容生成与发送（`push.daily` 这个定时任务的业务侧）。

分三层，职责不混：

    build_message(uid, day)      决定「这个人今天该不该收、收什么」——纯取数，不发送
    send_to_user(uid, payload)   发给他名下所有还活着的订阅（一台多端会各发一次）
    due_subjects / run_one       挂到 worker 的 hook，幂等由 worker 的 job_runs 保证

**为什么内容只有两种**：推送的价值与「说的事是不是真的非现在说不可」成正比。
现在站里能支撑每日提醒的只有两件——六级今天还有任务没做完、今天还没打卡。
委托到点的提醒由 `delegate.dispatch` 站内投递，再推一遍是重复打扰；
「你有一周没来了」这种召回文案，先不说我们还没有多周留存的对象，
对一个刚注册三天的人说他一周没来会更像发错了。

**为什么不给「今天已经打开过站的人」发**：这件事放在 service worker 里判断
（`clients.matchAll()` 看有没有 visible 的窗口），比在服务端查 last_active 准。
服务端只知道「有 API 调用」，不知道用户是不是正盯着屏幕；在浏览器端判断
既准确又省一次跨库查询，见 `public/sw.js` 的 push 处理器。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from core import config, push_store, quota, reminder_store, timeutil

logger = logging.getLogger(__name__)

JOB_KEY = "push.daily"

# 单条发送的超时。推送服务方（FCM/Mozilla）正常 200–800ms 就回，
# 卡住的多半是网络问题；10 秒还不回就当失败，别让一轮里几十条串着超时。
_SEND_TIMEOUT = 10

# 到点后还能补发的宽限窗口（秒）。见 due_subjects 的说明：
# 取 2 小时是为了让「某一跳没跑成」能被后面几跳捞回来，而不是当天彻底漏掉。
_GRACE_SECONDS = 2 * 3600


# ==================== 内容 ====================


def build_message(uid: str, day: str) -> dict[str, Any] | None:
    """这个人今天该收到什么。返回 None 表示今天不打扰他。

    判断顺序就是优先级：六级（有具体的事要做）> 打卡（有连续天数可护）。
    两件都没有就什么都不发——**不为了"今天发过推送"而凑一条内容**，
    那会让通知很快变成用户第一件事就是关掉的东西。
    """
    from service import cet as cet_svc
    from service import checkin as chk

    streak = chk.compute_streak(uid)
    done_today = day in set(streak.get("dates") or ())

    cet = cet_svc.overview(uid)
    tasks = cet.get("today_tasks") or []
    pending = [t for t in tasks if not t.get("done")]
    progress = cet.get("progress") or {}
    plan = cet.get("plan") or {}

    # ① 六级：今天还有没做完的任务 —— 唯一能说出「做什么」的那条
    if pending:
        title = f"六级今天还剩 {len(pending)} 个任务"
        next_title = str(pending[0].get("title") or "").strip()
        body_bits = [next_title] if next_title else []
        days_left = plan.get("days_left")
        if isinstance(days_left, int) and days_left >= 0:
            body_bits.append(f"距考试 {days_left} 天")
        progress_txt = f"已完成 {int(progress.get('done') or 0)}/{int(progress.get('total') or 0)}"
        body_bits.append(progress_txt)
        return {
            "title": title,
            "body": " · ".join(body_bits),
            "url": "/cet",
            "tag": "daily-cet",
        }

    # ② 打卡：今天还没打，而且已经有连续天数可护 —— 才有"别断了"的意义。
    #    新用户（streak<2）今天没打卡不发，他还没形成习惯，催一次只会觉得被管。
    n = int(streak.get("streak") or 0)
    if not done_today and n >= 2:
        return {
            "title": f"连续 {n} 天，今天还没打卡",
            "body": "点一下，把今天记下来",
            "url": "/checkin",
            "tag": "daily-checkin",
        }

    return None


# ==================== 发送 ====================


def _send_one(sub: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str | None, bool]:
    """发一条。返回 (成功, 错误摘要, 订阅是否已失效)。

    410 Gone / 404 Not Found 是推送服务方在明说「这条订阅没了」（用户清了站点数据、
    卸载了 PWA、换了设备）。这种失败重试一万次也不会成功，所以 `gone=True`
    让调用方把订阅停用；其余错误（超时、5xx、限流）都只是这一次没发出去。
    """
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # 没装依赖时不该让整个 worker 起不来
        return False, "pywebpush 未安装", False

    try:
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=config.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": config.VAPID_SUBJECT},
            timeout=_SEND_TIMEOUT,
            # ★ 必须显式给一个正数：pywebpush 默认 ttl=0，而 Edge 的推送服务（WNS）
            #   会回 400「Ttl value conflicts with X-WNS-Cache-Policy」，响应体是空的
            #   （原因只写在 X-WNS-ERROR-DESCRIPTION 头里）。见 config.PUSH_TTL。
            ttl=config.PUSH_TTL,
        )
        return True, None, False
    except WebPushException as e:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        gone = status in (404, 410)
        # 推送服务方经常把真正的原因写在自定义头里、body 留空（WNS 就是这样），
        # 只记 "400 Bad Request" 会让排查从十分钟变成一天。
        reason = ""
        if resp is not None:
            try:
                h = resp.headers
                reason = h.get("X-WNS-ERROR-DESCRIPTION") or h.get("x-wns-error-description") or ""
            except Exception:  # noqa: BLE001 — 头部拿不到不影响主流程
                reason = ""
        return False, f"{status or type(e).__name__}: {reason or e}"[:300], gone
    except Exception as e:  # noqa: BLE001 — 网络层什么都可能抛
        return False, f"{type(e).__name__}: {e}"[:300], False


def send_to_user(uid: str, payload: dict[str, Any]) -> dict[str, int]:
    """发给他名下所有还活着的订阅。多端会各收一次（这是对的：人可能在任一设备上）。"""
    if not config.push_ready():
        return {"sent": 0, "failed": 0, "gone": 0, "skipped": 1}
    subs = push_store.list_active(uid)
    sent = failed = gone = 0
    for sub in subs:
        ok, err, is_gone = _send_one(sub, payload)
        if ok:
            sent += 1
            push_store.mark_ok(sub["sub_id"])
        else:
            failed += 1
            if is_gone:
                gone += 1
            push_store.mark_fail(sub["sub_id"], err or "unknown", gone=is_gone)
            logger.warning("push send failed uid=%s sub=%s gone=%s err=%s", uid, sub["sub_id"], is_gone, err)
    return {"sent": sent, "failed": failed, "gone": gone, "skipped": 0}


# ==================== worker hook ====================


def matches_time(now_local: datetime, planned_at: int, hour: int, minute: int) -> bool:
    """这一跳该不该轮到「设在 hour:minute」的人。

    窗口是 `[0, _GRACE_SECONDS]` 而不是"正好等于这一跳"：worker 重启、机器休眠
    都会让某一跳消失，窗口只开 15 分钟的话那天就彻底漏了。留 2 小时的结果是
    ——上一跳没发成的话，后面几跳会接着试（`sent_today` 是闸门，发成功就不再进
    队列），相当于自带重试。

    ★ 已知边界：设 23:45 的人，如果 23:45 那一跳没跑成，下一跳已经是次日 00:00，
      按"当天"算匹配不到，那一天就漏了。为这种极少见的组合去背跨日补发的复杂度
      （还要让 mark_sent 记到哪一天跟着改）不划算，故不处理。
    """
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    lag = planned_at - int(target.timestamp())
    # lag < 0：自己的点还没到；lag 过大：今天这个窗口已经关了，等明天
    return 0 <= lag <= _GRACE_SECONDS


def due_subjects(job_key: str, planned_at: int) -> list[str]:
    """本轮到期的对象 = 自己的提醒时刻已到、有活订阅、且今天确实有话可说的用户 id。

    这里就把 `build_message` 算完，只返回需要发的人：worker 会为每个 subject
    写一行 job_runs 占位，白占位的行会让「今天到底推给了几个人」这个数失真。
    代价是 build_message 会跑两遍（这里一次、run_one 里一次），
    但它只是几条 sqlite 读，比多记一堆空行便宜。

    ## 时刻匹配（2026-11-07 起支持每人自己设）

    `push.daily` 的 cron 是每 15 分钟一跳，而每个人的点不同，所以每一跳都要
    自己判断"该不该轮到我"。判断本身收在 `matches_time` 里（那个函数有单测，
    窗口边界这种容易写错的东西不该埋在循环里）。
    """
    if not config.push_ready():
        return []
    day = quota.day_key()
    prefs = reminder_store.all_prefs()
    now_local = datetime.fromtimestamp(planned_at, timeutil.TZ)
    out: list[str] = []
    for uid in push_store.list_owners_with_active():
        # 今天已经发过的不再进队列：worker 的幂等键按 planned_at 算，
        # 手工补跑一次就是新的 planned_at，调度层拦不住重复打扰。
        if push_store.sent_today(uid, day):
            continue
        hour, minute = prefs.get(uid) or reminder_store.default_time()
        if not matches_time(now_local, planned_at, hour, minute):
            continue
        try:
            if build_message(uid, day) is None:
                continue
        except Exception:  # noqa: BLE001 — 单个用户数据异常不该让整轮不发
            logger.exception("push build_message failed uid=%s", uid)
            continue
        out.append(uid)
    return out


def run_one(job_key: str, subject_id: str, planned_at: int) -> dict[str, Any]:
    """给一个人发今天那条。返回 tokens_out 口径的统计（这里恒为 0，不耗 token）。"""
    day = quota.day_key()
    if push_store.sent_today(subject_id, day):
        return {"skipped": True, "reason": "already_sent_today"}
    payload = build_message(subject_id, day)
    if payload is None:
        return {"skipped": True, "reason": "nothing_to_say"}
    res = send_to_user(subject_id, payload)
    # 只有真发出去至少一台才算"今天提醒过了"。全失败不记账，
    # 这样下一轮（或手工补跑）还有机会补上。
    if int(res.get("sent") or 0) > 0:
        push_store.mark_sent(subject_id, day, int(res.get("sent") or 0))
    return {"push": res, "tokens_out": 0}


def run_job(job_key: str, planned_at: int) -> dict[str, Any]:
    """兜底的整体入口（worker 优先用 due_subjects，这条只在没有它时才走）。"""
    stats = {"due": 0, "sent": 0}
    for uid in due_subjects(job_key, planned_at):
        day = quota.day_key()
        payload = build_message(uid, day)
        if payload is None:
            continue
        stats["due"] += 1
        res = send_to_user(uid, payload)
        stats["sent"] += int(res.get("sent") or 0)
    return stats
