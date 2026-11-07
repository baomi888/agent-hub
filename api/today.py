"""今日一屏：一次请求拿到「今天该做什么」的几个数字。

首屏（欢迎页）要决定「显不显示今天这条」以及条上写什么。本来得并发打
`/api/cet/overview` + `/api/checkin/streak` + `/api/delegate` 三个端点：
每个 100–300ms，手机弱网下多一次往返就多一次可见的空白；更要紧的是——
任何一个失败，前端都分不清「这人确实没有数据」和「请求挂了」，
结果就是同一条「今天」在弱网里时有时无。

所以这里只做「取数 + 裁剪」，**不新增第二套算法**：
    - 打卡天数 / 已打日期  → service.checkin.compute_streak（原口径：今天没打就从昨天起算）
    - 四六级计划 / 今日任务 → service.cet.overview（含 days_left 与 plan_id 隔离）
    - 委托启用数 / 下次执行 → service.daily.list_delegations
同一个数字在首页和功能页长得不一样，比多一个端点糟得多。

`has_any` 是给前端的显隐开关：四件事一件都没建的人，首页不该多出一条空卡
（他会以为这是又一个要填的表单）。为 false 时前端整条不渲染。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends

from core import quota
from core.auth import get_current_user
from service import cet as cet_svc
from service import checkin as chk
from service import daily

router = APIRouter(dependencies=[Depends(get_current_user)])


def _next_pending(tasks: list[dict[str, Any]]) -> str | None:
    """今天第一个还没做完的任务标题。

    全都做完了返回 None —— 那时条上该说「今天的都做完了」，
    而不是把第一条已完成的任务再念一遍。
    """
    for t in tasks:
        if not t.get("done"):
            title = str(t.get("title") or "").strip()
            if title:
                return title
    return None


def _soonest(delegations: list[dict[str, Any]], now: int) -> dict[str, Any] | None:
    """启用中的委托里，下一次执行最早的那个 —— 只认**未来**的时间点。

    两个过滤都不能省：
      · 只取 enabled：停用的委托 next_run_at 可能还停在过去某一刻；
      · 只取 > now：worker 停过一阵之后 next_run_at 会停在过期值上（线上实测
        有一条停在 8 天前），照原样返回的话，首页会写「21:00 每日学习复盘」——
        用户会读成"今晚 21:00"，是假信息。过期就当作没有下次时间，
        由前端降级成「N 个委托已开启」。
    """
    best: dict[str, Any] | None = None
    for d in delegations:
        if not d.get("enabled"):
            continue
        at = d.get("next_run_at")
        if not at:
            continue
        at = int(at)
        if at <= now:
            continue
        if best is None or at < best["at"]:
            best = {"at": at, "name": str(d.get("name") or "")}
    return best


@router.get("")
async def today_overview(uid: str = Depends(get_current_user)) -> dict[str, Any]:
    day = quota.day_key()

    # 三份数据互不依赖，串行执行等于把三次 sqlite 往返叠起来算
    streak, cet, delegations = await asyncio.gather(
        asyncio.to_thread(chk.compute_streak, uid),
        asyncio.to_thread(cet_svc.overview, uid),
        asyncio.to_thread(daily.list_delegations, uid),
    )

    dates = set(streak.get("dates") or ())
    done_today = day in dates
    checkin = {
        "streak": int(streak.get("streak") or 0),
        "longest": int(streak.get("longest") or 0),
        "done_today": done_today,
    }

    plan = cet.get("plan") or None
    tasks = cet.get("today_tasks") or []
    progress = cet.get("progress") or {}
    cet_out = {
        "has_plan": bool(plan),
        "days_left": (plan or {}).get("days_left"),
        "total": int(progress.get("total") or 0),
        "done": int(progress.get("done") or 0),
        "today_total": len(tasks),
        "today_done": sum(1 for t in tasks if t.get("done")),
        "next_task": _next_pending(tasks),
    }

    soon = _soonest(delegations, int(time.time()))
    delegate = {
        "enabled": sum(1 for d in delegations if d.get("enabled")),
        "next_at": soon["at"] if soon else None,
        "next_name": soon["name"] if soon else None,
    }

    return {
        "date": day,
        "has_any": bool(
            done_today
            or cet_out["has_plan"]
            or delegate["enabled"]
            or checkin["streak"]
        ),
        "checkin": checkin,
        "cet": cet_out,
        "delegate": delegate,
    }
