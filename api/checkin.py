# -*- coding: utf-8 -*-
"""打卡的 HTTP 端点。

  GET  /api/checkin/streak   连续天数 / 最长连续 / 已打卡日期集合（来自 daily_reports）
  GET  /api/checkin/history  近 30 天打卡历史 [{date, points, shared}]
  POST /api/checkin/generate 触发今日总结（复用 daily.summarize_day，幂等）
  POST /api/checkin/share    标记某天已分享（幂等 upsert）

说明：打卡的语义就是「那天有没有生成总结」——所以后端不另造数据，直接从
service/checkin 派生。generate 复用每日总结同一份逻辑，手动与定时不产生第二条路径。
所有端点默认登录（router 已挂 Depends(get_current_user)）。端点**不带尾斜杠**，
与项目其余 ~62 个端点统一（避免 Next.js 去尾斜杠 + FastAPI 补尾斜杠的 308 黑洞）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from core import quota
from core.auth import get_current_user
from service import checkin as chk
from service import daily

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/streak")
async def checkin_streak(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(chk.compute_streak, uid)


@router.get("/history")
async def checkin_history(uid: str = Depends(get_current_user)) -> dict:
    return {"history": await asyncio.to_thread(chk.list_history, uid)}


@router.post("/generate")
async def checkin_generate(uid: str = Depends(get_current_user)) -> dict:
    date = quota.day_key()
    await asyncio.to_thread(quota.consume, uid, "auto")
    rep = await asyncio.to_thread(daily.summarize_day, uid, date)
    return {"date": date, "status": rep.status, "points": len(rep.content)}


@router.post("/share")
async def checkin_share(body: dict, uid: str = Depends(get_current_user)) -> dict:
    date = body.get("date") or quota.day_key()
    try:
        time.strptime(date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="date 格式应为 YYYY-MM-DD")
    chk.mark_shared(uid, date)
    return {"ok": True, "date": date, "shared": True}
