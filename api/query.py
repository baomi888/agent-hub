"""检索词增强的 HTTP 端点（roadmap B3）。

  POST /api/query/enhance   预览一句话的增强结果（原文 / 检索词 / 命中位置 / 追问建议）
  GET  /api/query/vocab     当前生效的词表与裁决表规模（"用的是哪版词表"）

这一层只做三件事：校验、限流、把 service 的结果原样吐出。业务都在 service/enhance.py。

字段契约（前端按这个接，别再自己算偏移）
----------------------------------------
    original   原话，**前端展示与聊天记录只能用这个**
    retrieval  增强后的检索用文本，只喂检索
    changed    有没有发生替换
    hits[]     {raw, canonical, start, end} —— start/end 是在 original 上的下标，
               前端拿它直接切字符串做高亮，不要用 raw 去 indexOf(会错位，同一个词可能出现多次)
    clarify[]  需要澄清时的追问句（歧义词），可以直接渲染成选项
    skipped[]  命中但没换的原因（给排障看，不展示）

为什么不做成 GET
----------------
查询文本可能很长且含换行，塞 query string 会被各种代理截断。POST 更稳。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import config, quota
from core.auth import get_current_user
from service import enhance

router = APIRouter(dependencies=[Depends(get_current_user)])


class EnhanceIn(BaseModel):
    """预览请求体。"""

    text: str = Field(..., min_length=1, max_length=20000)
    # 关掉追问有时有用：比如工作流里只想静默换词，不想弹问题。
    with_clarify: bool = True


@router.post("/enhance")
async def post_enhance(body: EnhanceIn, uid: str = Depends(get_current_user)) -> dict:
    """预览增强结果。

    限流走 `quota.hit`（每用户每分钟），和 KB 检索预览同一档——
    这个接口虽然不花钱，但它是"每敲一句都可能调一次"的高频端点。
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容为空，没什么可校准的")
    if len(text) > config.ENHANCE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"文本太长（{len(text)} 字），预览接口上限 {config.ENHANCE_MAX_CHARS} 字",
        )

    await asyncio.to_thread(quota.hit, uid)
    return await asyncio.to_thread(enhance.preview, text, body.with_clarify)


@router.get("/vocab")
async def get_vocab_stats() -> dict:
    """当前生效的词表版本与规模。纯本地读文件，不涉及用户数据。"""
    return await asyncio.to_thread(enhance.stats)
