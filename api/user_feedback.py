"""用户反馈接口（DeepSeek 式：几个分类按钮 + 一段话）。

路由清单：
  POST /api/user-feedback/        提交一条反馈
  GET  /api/user-feedback/mine    我提交过的（含处理状态）
  GET  /api/user-feedback/dict    分类/状态字典（前端按钮从这里取，不硬编码）

**为什么和 `/api/feedback` 分开**：那个是"某条回答的赞/踩"，是提示词自进化
（`service/prompt_evo.py`）的差样本来源。把产品建议写进同一张表，会被当成
低分回答样本去优化提示词 —— 直接污染训练集。两张表、两个路由，语义各自清楚。

限流刻意比全局严（每分钟 5 条）：正常用户提一条意见就完事，脚本才会一分钟打几十次，
而每次都是一条写库。超限由 main.py 的全局处理器转成 429 并带 `retry_after`。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import quota, user_feedback
from core.auth import get_current_user

# 默认要登录：反馈要记"谁提的"，匿名反馈无法回访、也无法防刷
router = APIRouter(dependencies=[Depends(get_current_user)])

_RATE_PER_MIN = 5


class SubmitRequest(BaseModel):
    category: str = Field(..., description="分类：bug / answer / feature / ui / other")
    content: str = Field(..., description=f"{user_feedback.MIN_CONTENT}~{user_feedback.MAX_CONTENT} 字")
    contact: str = Field(default="", description="可选联系方式")
    page: str = Field(default="", description="从哪个页面提交的")


@router.post("/")
async def submit(
    req: SubmitRequest, request: Request, uid: str = Depends(get_current_user)
) -> dict:
    """提交反馈。返回新 id，前端据此提示"已收到"。"""
    # 限流放最前面：否则脚本可以靠刷校验错误（400）来无限试探
    await asyncio.to_thread(quota.hit, uid, _RATE_PER_MIN)

    cat = (req.category or "").strip()
    if cat not in dict(user_feedback.CATEGORIES):
        raise HTTPException(status_code=400, detail=f"分类不合法：{cat}")
    content = (req.content or "").strip()
    if len(content) < user_feedback.MIN_CONTENT:
        raise HTTPException(
            status_code=400, detail=f"内容太短，至少 {user_feedback.MIN_CONTENT} 个字"
        )
    if len(content) > user_feedback.MAX_CONTENT:
        raise HTTPException(
            status_code=400, detail=f"内容太长，最多 {user_feedback.MAX_CONTENT} 个字"
        )
    if len(req.contact or "") > user_feedback.MAX_CONTACT:
        raise HTTPException(status_code=400, detail="联系方式太长")
    if len(req.page or "") > user_feedback.MAX_PAGE:
        raise HTTPException(status_code=400, detail="来源页面字段太长")

    # username 从 token 的 claim 取（auth 依赖已写进 request.state），不额外查库：
    # 本系统没有改名功能，token 里的名字与库里的名字不会分叉
    username = str(getattr(request.state, "username", "") or "")
    fid = await asyncio.to_thread(
        user_feedback.add, uid, username, cat, content, req.contact or "", req.page or ""
    )
    return {"ok": True, "id": fid}


@router.get("/mine")
async def mine(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    uid: str = Depends(get_current_user),
) -> dict:
    """我提交过的反馈。用户需要看到"我提的有没有人处理"，否则他不会提第二次。"""
    items, total = await asyncio.to_thread(user_feedback.list_mine, uid, limit, offset)
    return {"items": items, "total": total}


@router.get("/dict")
async def dict_api() -> dict:
    """分类/状态字典。挂在用户侧（登录即可），因为提交表单要用它渲染按钮。"""
    return user_feedback.dict_payload()
