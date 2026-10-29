"""四六级备考工作区的 HTTP 端点。

  GET    /api/cet/overview        首屏聚合数据（档案 / 进度 / 今日任务 / 连续天数 / 待复习词 / 分数）
  POST   /api/cet/plan            建立备考档案（确定性生成任务，不调用大模型）
  PATCH  /api/cet/plan            修改档案设置（考期 / 模块 / 强度 / 目标分）
  DELETE /api/cet/plan            结束当前计划（归档，不物理删除）
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

from fastapi import APIRouter, Depends, HTTPException, Query

from core.auth import get_current_user
from service import cet as svc

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/overview")
async def cet_overview(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(svc.overview, uid)


@router.post("/plan")
async def cet_create_plan(body: dict, uid: str = Depends(get_current_user)) -> dict:
    """建立备考档案。参数全部可选（不传就取默认值），因此旧的 `{days: 54}` 调用仍然可用。"""
    try:
        return await asyncio.to_thread(
            svc.create_plan,
            uid,
            title=body.get("title"),
            exam_date=body.get("exam_date"),
            days=body.get("days") or 54,
            exam_type=body.get("exam_type") or "CET6",
            modules=body.get("modules"),
            intensity=body.get("intensity") or "standard",
            target_score=body.get("target_score"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# 只允许 PATCH 改这几个字段：白名单而不是黑名单，
# 防止把 `id` / `owner_id` / `archived_at` 这类不该由客户端控制的列带进来。
_PLAN_PATCHABLE = {"exam_type", "exam_date", "modules", "intensity", "target_score"}


@router.patch("/plan")
async def cet_update_plan(body: dict, uid: str = Depends(get_current_user)) -> dict:
    changes = {k: v for k, v in (body or {}).items() if k in _PLAN_PATCHABLE}
    if not changes:
        raise HTTPException(status_code=400, detail="没有可修改的字段")
    try:
        return await asyncio.to_thread(svc.update_plan, uid, changes)
    except LookupError as e:
        # 没有生效中的档案 → 404（与项目"归属/不存在一律 404"的约定一致）
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/plan")
async def cet_archive_plan(uid: str = Depends(get_current_user)) -> dict:
    """结束当前计划。归档而非删除：连续天数与学习路径关卡都不受影响。"""
    ok = await asyncio.to_thread(svc.archive_plan, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="没有进行中的备考计划")
    return {"ok": True}


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail="score 必须是整数") from None
    target = body.get("target")
    target = int(target) if target not in (None, "") else None
    taken_at = body.get("taken_at") or None
    note = str(body.get("note") or "")[:100]
    try:
        return await asyncio.to_thread(svc.add_score, uid, exam_type, score, target, taken_at, note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/scores/{score_id}")
async def cet_delete_score(score_id: str, uid: str = Depends(get_current_user)) -> dict:
    ok = await asyncio.to_thread(svc.delete_score, uid, score_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True, "id": score_id}
