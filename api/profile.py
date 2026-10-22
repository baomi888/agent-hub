# -*- coding: utf-8 -*-
"""用户兴趣标签（画像）的 HTTP 端点。

  GET /api/profile/tags   标签体系（七大类 / 30 条 / 可选项）—— 前端不硬编码任何标签名
  GET /api/profile        当前用户的手选标签 + 权重画像
  PUT /api/profile/tags   保存手选标签（覆盖式、幂等）

为什么需要这一层：`user_tags` 表一直在被对话自动写入，但用户**从来没有机会
自己声明**。结果就是新账号进来看到的永远是空白工作台，画像要靠聊十几轮才长出
一点轮廓。这一层把"我是谁、我在干嘛"变成用户一次点击就能说清的事，
后续模板推荐 / 每日委托默认值 / 检索偏置都能直接消费。

归属隔离与项目其余端点一致：owner 一律取自登录态，绝不接受请求体里的 uid；
端点不带尾斜杠（normalize_api_slash 中间件 + redirect_slashes=False）。
失败语义：勾了词表外的标签 → 400 并告知哪些不认，"悄悄丢弃"会让前端以为存上了。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import config, session as store, tag_taxonomy
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


class TagPickIn(BaseModel):
    """手选标签请求体。前端勾什么就发什么，校验全在这边做。"""

    tags: list[str] = Field(default_factory=list)


def _enrich(rows: list[dict]) -> list[dict]:
    """给权重行补上大类/类型/说明，前端一次拿全，不用再查一遍词表。"""
    out: list[dict] = []
    for r in rows:
        meta = tag_taxonomy.TAGS_BY_NAME.get(r.get("tag", ""))
        item = {
            "tag": r.get("tag", ""),
            "weight": float(r.get("weight") or 0),
            "category": meta["category"] if meta else None,
            "type": meta["type"] if meta else None,
            "desc": meta["desc"] if meta else None,
        }
        out.append(item)
    return out


def _snapshot(uid: str) -> dict:
    """组装画像：手选 + 权重标签 + 按大类聚合的权重。"""
    picks = store.get_user_tag_picks(uid)
    # 上限读配置而不是写死 30：PROFILE_TOP_TAGS 是 C2 聚合用的同一个旋钮，
    # 两处不一致会出现"画像卡片少一条、worker 快照多一条"的诡异现象。
    limit = int(getattr(config, "PROFILE_TOP_TAGS", 30) or 30)
    rows = _enrich(store.get_user_tags(uid, limit=limit))
    # 大类聚合：用来判断"这个人的重心是考研还是求职"，模板推荐按它排优先级。
    # 只统计词表内的标签；词表外的自由主题（"数学""英语"）不进大类，
    # 否则会凭空冒出一个叫 None 的类别。
    by_cat: dict[str, float] = {}
    for r in rows:
        cat = r["category"]
        if cat:
            by_cat[cat] = round(by_cat.get(cat, 0.0) + r["weight"], 2)
    cats = [
        {"category": c, "weight": w}
        for c, w in sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "picks": picks,
        "tags": rows,
        "categories": cats,
        "has_tags": bool(picks or rows),
    }


@router.get("/tags")
async def get_tag_taxonomy() -> dict:
    """标签体系本体。纯静态数据，不需要读库，但保持一致走线程外。"""
    return await asyncio.to_thread(tag_taxonomy.taxonomy)


@router.get("")
async def get_my_profile(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(_snapshot, uid)


@router.put("/tags")
async def put_my_tags(body: TagPickIn, uid: str = Depends(get_current_user)) -> dict:
    """保存手选标签。

    校验顺序是刻意的：先归一 → 再判总量 → 最后才写库。
    先判总量再归一的话，["CET6","六级"] 会被当成两条而误报"选太多了"。
    """
    normalized = tag_taxonomy.normalize_tags(body.tags)
    known = [t for t in normalized if tag_taxonomy.is_known(t)]
    dropped = [t for t in normalized if not tag_taxonomy.is_known(t)]

    if dropped:
        raise HTTPException(
            status_code=400,
            detail=f"这些标签不在词表里，无法保存：{'、'.join(dropped)}",
        )
    if not known:
        raise HTTPException(status_code=400, detail="至少选择 1 个兴趣标签")
    if len(known) > store.MAX_PICKS:
        raise HTTPException(
            status_code=400,
            detail=f"最多选 {store.MAX_PICKS} 个，当前 {len(known)} 个",
        )

    picks = await asyncio.to_thread(store.pick_user_tags, uid, known)
    snap = await asyncio.to_thread(_snapshot, uid)
    snap["picks"] = picks or snap["picks"]
    return snap
