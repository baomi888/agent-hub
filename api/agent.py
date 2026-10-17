# -*- coding: utf-8 -*-
from __future__ import annotations
"""Agent 编排的元数据路由。

路由清单：
  GET  /api/agent/tools           列出可用工具及其执行元数据
  POST /api/agent/tools/discover  激活被延迟的工具（返回其 schema）

这两个端点都是**只读语义的元数据接口**，不执行任何工具、不改用户数据：
工具是代码注册的，不接受运行时增删。

为什么要单独开这两个端点
------------------------
前端编排面板需要"有哪些工具、能不能并发、会不会要用户确认"才能画节点；
编排引擎需要在执行前知道同一个道理。**同一份元数据，两个消费者**——
所以它必须是从代码里读出来的，而不是前端自己写死一份。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import tool_registry
from core.auth import get_current_user

# 默认要登录：与 api/chat.py:46 同款写法，新增端点忘了写鉴权时默认也落在安全一侧
router = APIRouter(dependencies=[Depends(get_current_user)])

# 说明：这两个端点**刻意不挂配额与限流**。
# 它们只读代码里的元数据表，不调模型、不落盘、不碰用户数据，成本为零。
# 挂限流反而会让编排面板加载时被自己限住。真正的成本闸在"执行"那一侧
# （quota.consume(uid, "auto")），不在"看有什么工具"这一侧。


class DiscoverRequest(BaseModel):
    query: str = Field(
        ...,
        description='关键词搜索；或以 select: 前缀按名精确激活，如 "select:get_weather"。',
    )


@router.get("/tools")
async def list_tools(
    deferred: bool | None = None,
    q: str = "",
    uid: str = Depends(get_current_user),
) -> dict:
    """列出可用工具及其执行元数据。

    deferred=true 只看**尚未激活**的延迟工具（前端"可发现的工具"列表）；
    q 按名称与描述做子串匹配。
    """
    items = await asyncio.to_thread(tool_registry.list_meta, deferred, q, uid)
    return {"items": items, "total": len(items)}


@router.post("/tools/discover")
async def discover_tools(req: DiscoverRequest, uid: str = Depends(get_current_user)) -> dict:
    """激活延迟工具并返回它们的 schema。

    未命中时返回 404，**但响应体里带上全部可激活的工具名**——
    让调用方（人或模型）下一步能猜对，而不是只被告知"失败了"。
    """
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    try:
        activated, schemas = await asyncio.to_thread(tool_registry.discover, query, uid)
    except tool_registry.NoMatchError as e:
        # 404 而不是 200+空列表：这是"你要的东西不存在"，不是"查到了零条"。
        # 同时把候选名单一并给出，下一步才有得选。
        raise HTTPException(
            status_code=404,
            detail=f"没有匹配的工具：{query}。可激活的工具：{('、'.join(e.candidates)) or '（无）'}",
        )
    return {
        "activated": activated,
        "schemas": schemas,
        "available": tool_registry.deferred_names(),
    }
