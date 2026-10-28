"""开发者数据看板接口（仅管理员）。

路由清单：
  GET   /api/admin/overview              总览（用户数 / 调用统计 / 趋势 / 反馈计数）
  GET   /api/admin/apis                  接口排行（按路由模板聚合）
  GET   /api/admin/feedback              反馈列表（可选按状态筛）
  PATCH /api/admin/feedback/{fid}        改反馈状态（严格按流转表，不允许回退）
  GET   /api/admin/dict                  分类/状态字典（前端不得硬编码）

**没有导出接口**：2026-10-27 明确不做 CSV/Excel 导出。这个看板是给开发者
在浏览器里看数的，把数据搬进 Excel 是另一种工作流（要权限、要脱敏、要留痕），
不属于本期范围。若将来要恢复，须先补"导出审计 + 字段脱敏"两件事。

鉴权两层（文档 §6）：
  1. `router` 挂了 `Depends(get_current_user)` → 未登录一律 **401**
  2. 函数内 `_require_admin` → 登录但不是管理员一律 **404**（伪装成接口不存在，见该函数）
两层顺序不能反：先判身份再判权限。若顺序反了，未登录用户会先撞上"无权"
那条分支，拿到的是一个伪造的 404 —— 明明只是没登录，却表现得像路径错了，
用户永远不知道该去登录，排查时也会误判成"路由被删了"。

判据用 `quota._is_admin`（按 `ADMIN_USERNAMES` 用户名比对），不新增角色表：
现在是单机自用部署，多一张角色表就多一套同步问题。将来要多角色改这一个函数即可。

**为什么不用"回环地址"判定**：Next.js 的 /api/* rewrite 让所有请求的
`request.client.host` 都是 127.0.0.1，用它判定等于对公网全开（`api/metrics.py` 头部记录过这个坑）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core import dashboard, quota, user_feedback
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


def _require_admin(uid: str) -> None:
    """非管理员一律按"**这个接口不存在**"处理 —— 返回 404，不是 403。

    403 等于向对方确认两件事：这个路径存在、你的身份不够。对一个只有开发者
    自己用的看板来说，这本身就是泄露（扫到 403 就知道这里有后台）。
    404 不给任何确认：分不清是"没有这个接口"还是"你无权访问"。

    **未登录仍然是 401**（由 router 上的 `Depends(get_current_user)` 拦下，
    走不到这里）：那是 HTTP 语义正确的"需要先认证"，而且前端要靠它区分
    "该去登录"和"这条路不存在"。把未登录也改成 404，会让登录态过期时的
    表现和"页面被删了"混在一起，没法排查。

    detail 固定写英文 `Not Found`：与 FastAPI 自己的 404 文案一致，
    不留"中文错误信息"这种能被用来区分真假的特征。
    """
    if not quota._is_admin(uid):
        raise HTTPException(status_code=404, detail="Not Found")


# ==================== 看板 ====================


@router.get("/overview")
async def overview(days: int = Query(7, ge=1, le=90), uid: str = Depends(get_current_user)) -> dict:
    """总览（口径见文档 §2 / §7.3）。

    `async def` + `to_thread`：SQLite 是同步驱动，直接在事件循环里跑会阻塞
    其他请求（哪怕只有几毫秒，也是全进程串行）。这是本项目一贯的纪律。
    """
    _require_admin(uid)
    return await asyncio.to_thread(dashboard.overview, days)


@router.get("/apis")
async def apis(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("calls", description="calls / slow / fail"),
    uid: str = Depends(get_current_user),
) -> dict:
    """接口排行（口径见文档 §2.6）。`sort=slow` 只统计调用 ≥ 3 次的接口。"""
    _require_admin(uid)
    return await asyncio.to_thread(dashboard.api_ranking, days, limit, sort)


@router.get("/feedback")
async def feedback_list(
    status: str = Query("", description="按状态筛；空表示全部"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    uid: str = Depends(get_current_user),
) -> dict:
    """反馈列表。顺带返回各状态计数，省前端一次请求。"""
    _require_admin(uid)
    items, total = await asyncio.to_thread(user_feedback.list_all, status, limit, offset)
    counts = await asyncio.to_thread(user_feedback.counts)
    return {"items": items, "total": total, "counts": counts}


class StatusPatch(BaseModel):
    status: str = Field(..., description="目标状态：doing / done / closed")


@router.patch("/feedback/{fid}")
async def feedback_patch(
    fid: int, req: StatusPatch, uid: str = Depends(get_current_user)
) -> dict:
    """改状态。非法流转返回 **409**（不是 400）：

    400 是"你请求本身写得不对"，409 是"请求没写错，但和当前状态冲突"——
    前端对这两者的处理不同（400 该改参数，409 该刷新列表看最新状态）。
    """
    _require_admin(uid)
    ok, err = await asyncio.to_thread(user_feedback.set_status, fid, req.status)
    if not ok:
        raise HTTPException(status_code=409, detail=err)
    return {"ok": True, "status": req.status}


class ReplyRequest(BaseModel):
    reply: str = Field(
        default="", description=f"给提意见的人的回复，{user_feedback.MIN_REPLY}~{user_feedback.MAX_REPLY} 字；空串表示撤回"
    )


@router.post("/feedback/{fid}/reply")
async def feedback_reply(
    fid: int, req: ReplyRequest, uid: str = Depends(get_current_user)
) -> dict:
    """给一条反馈回复（管理员 → 用户）。

    为什么是 `POST .../reply` 而不是在 PATCH 里塞一个 reply 字段：
      PATCH `/feedback/{fid}` 的语义是"改这条反馈的属性"，它的错误契约是
      409 + 状态流转；回复是另一件事（还带着"顺手把 open 推到 doing"的副作用），
      两件事共用一个端点，将来想给回复加审计（谁回的、什么时候改过）就无处落脚。

    400 / 409 分工与 PATCH 一致：长度超限是"请求写错了"（400），
    反馈不存在是"和当前数据冲突"（409）—— 前端拿到 409 会去刷新列表。
    """
    _require_admin(uid)
    text = (req.reply or "").strip()
    if len(text) > user_feedback.MAX_REPLY:
        raise HTTPException(
            status_code=400, detail=f"回复最多 {user_feedback.MAX_REPLY} 个字"
        )
    ok, err = await asyncio.to_thread(user_feedback.set_reply, fid, text)
    if not ok:
        raise HTTPException(status_code=409, detail=err)
    # 回读一次最新状态：set_reply 可能在 open 时把它推到 doing，
    # 前端要按新状态重画按钮，不能让前端自己猜。
    row = await asyncio.to_thread(user_feedback.get, fid)
    return {
        "ok": True,
        "reply": text,
        "status": (row or {}).get("status", ""),
        "status_label": (row or {}).get("status_label", ""),
        "replied_at": (row or {}).get("replied_at"),
    }


@router.get("/dict")
async def dict_api(uid: str = Depends(get_current_user)) -> dict:
    """分类 / 状态字典 + 流转表 + 长度限制。前端按钮与校验全部从这里取。"""
    _require_admin(uid)
    return user_feedback.dict_payload()

