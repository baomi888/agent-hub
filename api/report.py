"""每日总结的 HTTP 端点。

  GET  /api/report/today        当前用户今日总结（无则 404 + detail）
  GET  /api/report/{date}       指定日期总结（date=YYYY-MM-DD）
  POST /api/report/generate     触发今日总结（同步生成后返回报告）

说明：spec 原本设想「生成返 202 + generating，详情轮询」；MVP 先做成同步返回，
降低联调成本。轮询能力在 worker 侧已就绪（run-now + runs），后续可上浮到此处。
所有端点默认登录（router 已挂 Depends(get_current_user)）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from core import quota
from core.auth import get_current_user
from service import daily

router = APIRouter(dependencies=[Depends(get_current_user)])


def _report_to_dict(rep: daily.Report) -> dict[str, Any]:
    return {
        "report_id": rep.report_id,
        "date": rep.date,
        "status": rep.status,
        "stats": rep.stats,
        "content": rep.content,
        "model": rep.model,
        "generated_at": rep.generated_at,
        "read_at": rep.read_at,
    }


@router.get("/today")
async def report_today(uid: str = Depends(get_current_user)) -> dict:
    date = quota.day_key()
    rep = await asyncio.to_thread(daily.get_report, uid, date)
    if rep is None:
        raise HTTPException(status_code=404, detail="今日总结尚未生成")
    return _report_to_dict(rep)


@router.get("/{date}")
async def report_by_date(date: str, uid: str = Depends(get_current_user)) -> dict:
    try:
        time.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date 格式应为 YYYY-MM-DD") from None
    rep = await asyncio.to_thread(daily.get_report, uid, date)
    if rep is None:
        raise HTTPException(status_code=404, detail="该日总结不存在")
    return _report_to_dict(rep)


@router.post("/generate")
async def report_generate(uid: str = Depends(get_current_user)) -> dict:
    date = quota.day_key()
    await asyncio.to_thread(quota.consume, uid, "auto")
    rep = await asyncio.to_thread(daily.summarize_day, uid, date)
    return _report_to_dict(rep)
