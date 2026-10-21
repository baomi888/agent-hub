# -*- coding: utf-8 -*-
"""四六级备考工作区的 HTTP 端点。

  GET    /api/cet/overview        首屏聚合数据（计划 / 进度 / 今日任务 / 连续天数 / 待复习词 / 分数）
  POST   /api/cet/plan            生成一份 N 天备考计划（确定性生成，不调用大模型）
  PATCH  /api/cet/task            勾选 / 取消某条任务
  GET    /api/cet/vocab           词汇本（?due=1 只看今天该复习的）
  POST   /api/cet/vocab           加入词汇本（同词去重，已存在则提前到今天复习）
  POST   /api/cet/vocab/review    复习一张词卡（remembered=true/false 驱动 SRS 间隔）
  GET    /api/cet/scores          模考 / 真考分数列表
  POST   /api/cet/scores          新增一条分数
  DELETE /api/cet/scores/{id}     删除一条分数

说明：所有端点默认登录（router 已挂 Depends(get_current_user)），归属校验失败按项目约定回 404；
端点**不带尾斜杠**，与项目其余端点统一（normalize_api_slash 中间件 + redirect_slashes=False）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.auth import get_current_user
from service import cet as svc

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/overview")
async def cet_overview(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(svc.overview, uid)


@router.post("/plan")
async def cet_create_plan(body: dict, uid: str = Depends(get_current_user)) -> dict:
    title = str(body.get("title") or "六级 54 天备考计划")[:60]
    exam_date = body.get("exam_date") or None
    days = body.get("days") or 54
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days 必须是整数")
    return await asyncio.to_thread(svc.create_plan, uid, title, exam_date, days)


@router.patch("/task")
async def cet_toggle_task(body: dict, uid: str = Depends(get_current_user)) -> dict:
    task_id = str(body.get("id") or "")
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 id")
    done = bool(body.get("done"))
    ok = await asyncio.to_thread(svc.toggle_task, uid, task_id, done)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "id": task_id, "done": done}


@router.get("/vocab")
async def cet_list_vocab(
    uid: str = Depends(get_current_user),
    due: int = Query(1, ge=0, le=1),
) -> dict:
    return {"items": await asyncio.to_thread(svc.list_vocab, uid, bool(due))}


@router.post("/vocab")
async def cet_add_vocab(body: dict, uid: str = Depends(get_current_user)) -> dict:
    word = str(body.get("word") or "").strip()
    if not word:
        raise HTTPException(status_code=400, detail="word 不能为空")
    meaning = str(body.get("meaning") or "")[:200]
    source = str(body.get("source") or "")[:80]
    try:
        return await asyncio.to_thread(svc.add_vocab, uid, word, meaning, source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/vocab/review")
async def cet_review_vocab(body: dict, uid: str = Depends(get_current_user)) -> dict:
    item_id = str(body.get("id") or "")
    if not item_id:
        raise HTTPException(status_code=400, detail="缺少 id")
    remembered = bool(body.get("remembered"))
    out = await asyncio.to_thread(svc.review_vocab, uid, item_id, remembered)
    if out is None:
        raise HTTPException(status_code=404, detail="词条不存在")
    return out


@router.get("/scores")
async def cet_list_scores(uid: str = Depends(get_current_user)) -> dict:
    return {"items": await asyncio.to_thread(svc.list_scores, uid)}


@router.post("/scores")
async def cet_add_score(body: dict, uid: str = Depends(get_current_user)) -> dict:
    exam_type = str(body.get("exam_type") or "CET6")[:20]
    try:
        score = int(body.get("score"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="score 必须是整数")
    target = body.get("target")
    target = int(target) if target not in (None, "") else None
    taken_at = body.get("taken_at") or None
    note = str(body.get("note") or "")[:100]
    try:
        return await asyncio.to_thread(svc.add_score, uid, exam_type, score, target, taken_at, note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/scores/{score_id}")
async def cet_delete_score(score_id: str, uid: str = Depends(get_current_user)) -> dict:
    ok = await asyncio.to_thread(svc.delete_score, uid, score_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True, "id": score_id}
