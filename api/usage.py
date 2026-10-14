# -*- coding: utf-8 -*-
"""用量与反馈看板（P2 信任基础设施）。

  GET /api/usage/summary   当日配额快照 + 近 7 天用量 + 赞踩聚合

把「你用了多少、还剩多少、答案被怎么评价」摊开给用户看——
配额不该只在超限时跳出来吓人，反馈也不该只在点赞时被看见。
全部是只读聚合，一次请求返回，前端一个入口画完整面板。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from core.auth import get_current_user
from core import config, quota
from core import session as store

router = APIRouter()


@router.get("/summary")
async def usage_summary(uid: str = Depends(get_current_user)) -> dict:
    """当前用户的用量概览。三个来源互不依赖，并发取。"""
    snap, hist, fb = await asyncio.gather(
        asyncio.to_thread(quota.snapshot, uid),
        asyncio.to_thread(quota.history, uid, 7),
        asyncio.to_thread(store.feedback_stats, uid),
    )
    return {
        "quota": snap,
        "history": hist,
        # 0 = 后端未启用每分钟限流，前端不画这一条
        "rate_per_minute": int(getattr(config, "RATE_PER_MINUTE", 0) or 0),
        "feedback": fb,
    }
