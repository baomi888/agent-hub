# -*- coding: utf-8 -*-
"""关卡式学习路径的 HTTP 端点。

  GET /api/path/overview   章节 / 12 关三态 / 总进度 / 当前该做的一关

只有一条端点，是刻意的：路径的全部数值都是**读时现算**（见 service/path.py
顶部说明），所以不存在「刷新一下让人重算」的需求 —— 每读一次就是最新事实，
再加一条 POST /refresh 只会变成一个没人调用、却要一直维护的死接口。

归属隔离与项目其余端点一致：所有查询按 owner_id 过滤，看不到别人的进度；
端点不带尾斜杠（normalize_api_slash 中间件 + redirect_slashes=False）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from core.auth import get_current_user
from service import path as svc

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/overview")
async def path_overview(uid: str = Depends(get_current_user)) -> dict:
    # 内部要读 SQLite + 知识库注册表，都是同步 I/O，必须挪出事件循环
    return await asyncio.to_thread(svc.overview, uid)
