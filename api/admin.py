"""开发者数据看板接口（仅管理员）。

路由清单：
  GET   /api/admin/overview              总览（用户数 / 调用统计 / 趋势 / 反馈计数）
  GET   /api/admin/apis                  接口排行（按路由模板聚合）
  GET   /api/admin/failures              失败明细（带人话原因）
  GET   /api/admin/real-users            真实用户使用数据（排除内部/测试账号）
  POST  /api/admin/users/{target}/internal  标记/取消内部账号
  GET   /api/admin/feedback              反馈列表（可选按状态筛）
  PATCH /api/admin/feedback/{fid}        改**这一条**的状态（任意互转，含回退）
  POST  /api/admin/feedback/{fid}/reply  给**这一条**追加回复（带作者，发出即定稿）
  GET   /api/admin/feedback/export       一键导出 Word（日期范围 + 脱敏 + 留审计）
  GET   /api/admin/dict                  分类/状态字典（前端不得硬编码）

**导出接口（2026-11-18 建，2026-11-19 改 Word + 日期范围）**：
  · 脱敏：不再"打码后带出去"，而是 uid / 姓名 / 联系方式 / 回复 / 对话
    **根本不进导出件**；只有反馈问题、提交日期、分类、解决状态。
  · 审计：每次导出写一行 `event=feedback_export` 到 `${DATA_DIR}/audit.log`，
    带条数和日期范围（复用 `account.audit`，与账号安全事件同一个文件）。
  · 日期：`start_date` / `end_date` 都是 YYYY-MM-DD、含当天；都不传 = 全部。
    用业务时区（core.timeutil.day_bounds），不拿字符串比大小。
格式是 Word（.docx）：用户明确"想导出的是 word"。生成器在
`core/feedback_docx.py`，纯标准库 zipfile + OOXML，线上不需要装 python-docx。

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
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from core import account, dashboard, quota, user_feedback, verify_tag
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


@router.get("/failures")
async def failures(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100),
    uid: str = Depends(get_current_user),
) -> dict:
    """最近的失败调用 + 中文原因（看板自己回答"为什么失败"，不用去 grep 日志）。

    分 `need_attention` / `ignorable` 两栏：401 未登录、报告空态 404 属于预期行为，
    和真故障混在一起看会把真问题淹掉。
    """
    _require_admin(uid)
    return await asyncio.to_thread(dashboard.failures, days, limit)


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


@router.get("/feedback/export")
async def feedback_export(
    status: str = Query("", description="按状态筛；空表示全部"),
    start_date: str = Query("", description="起始日期 YYYY-MM-DD（含当天）；空表示不限"),
    end_date: str = Query("", description="结束日期 YYYY-MM-DD（含当天）；空表示不限"),
    uid: str = Depends(get_current_user),
) -> Response:
    """一键导出 Word（附件）。**这个路由必须写在不带变量的 `/feedback` 之后、
    带变量的 `/feedback/{fid}` 之前** —— 否则 `export` 会被当成 `{fid}` 吞掉
    （FastAPI 按声明顺序匹配，`{fid}` 声明在前时 `int` 转换失败就是 422）。

    导出是**只读但留痕**的动作：先把审计写掉、再返回正文。反过来（先返回再写）
    一旦写审计抛异常，前端已经拿到文件，这条记录就永远丢了。
    """
    _require_admin(uid)
    # 日期先在这里校验成 400（请求写错），不要让坏日期钻到 core 里抛
    # ValueError 变成 500 —— 那是"服务器坏了"，与用户填错无关。
    from core import timeutil

    for label, day in (("start_date", start_date), ("end_date", end_date)):
        if day:
            try:
                timeutil.parse_day(day)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"{label} 必须是 YYYY-MM-DD"
                ) from None
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="起始日期不能晚于结束日期")

    blob, meta = await asyncio.to_thread(
        user_feedback.export_docx, status, start_date, end_date
    )
    # 审计失败不阻断导出（account.audit 内部已经吞异常），但审计里的条数
    # 必须是这次真正导出的条数 —— 用返回的 meta，不要在这里重新查一遍。
    await asyncio.to_thread(
        account.audit,
        "feedback_export",
        uid,
        {
            "items": meta["items"],
            "status": meta["status"] or "all",
            "start": start_date or "all",
            "end": end_date or "all",
            "bytes": len(blob),
        },
    )
    # 文件名用 ASCII + 日期：中文文件名要 RFC 5987 的 filename*，各浏览器实现
    # 不一致，反而是"下载下来叫 .bin 或者乱码"。文档里的标题才是中文。
    span = ""
    if start_date or end_date:
        span = "-" + (start_date or "all").replace("-", "")
        if end_date:
            span += "-" + end_date.replace("-", "")
    stamp = time.strftime("%Y%m%d")
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="feedback-{stamp}{span}.docx"',
            # 导出件会被反代缓存就麻烦了，明确禁掉
            "Cache-Control": "no-store",
        },
    )


@router.get("/feedback/threads")
async def feedback_threads(
    status: str = Query("", description="只列出名下含该状态反馈的用户；空表示全部"),
    limit: int = Query(60, ge=1, le=200),
    uid: str = Depends(get_current_user),
) -> dict:
    """会话列表（每个用户一条）。

    与 `/feedback` 的关系：那个是**按条**的工单视图（导出用），这个是**按人**
    的对话视图（回复用）。两个都留着，因为它们是两种真实的工作流：
    月底要把所有条整理成一份文件，平时是要跟具体的人把话说完。
    """
    _require_admin(uid)
    data = await asyncio.to_thread(user_feedback.threads, limit, status)
    data["counts"] = await asyncio.to_thread(user_feedback.counts)
    return data


@router.get("/feedback/thread/{target}")
async def feedback_thread(
    target: str, uid: str = Depends(get_current_user)
) -> dict:
    """某个用户的整段对话（正文 + 回复，按时间正序）。

    参数名用 `target` 而不是 `{uid}`：`uid` 已经是"当前管理员"的依赖名，
    同名会让 FastAPI 把它绑成同一个值（那种错误的表现是"永远只能查到
    自己的反馈"，看起来像没数据）。
    """
    _require_admin(uid)
    data = await asyncio.to_thread(user_feedback.thread, target)
    return data


class ThreadReply(BaseModel):
    text: str = Field(..., description="回复正文。发出即定稿，不可修改")
    fid: int | None = Field(
        default=None,
        description="挂到哪条反馈下。不传则挂最新一条（老前端的行为，保留兼容）",
    )


@router.post("/feedback/thread/{target}/reply")
async def feedback_thread_reply(
    target: str,
    req: ThreadReply,
    request: Request,
    uid: str = Depends(get_current_user),
) -> dict:
    """在会话里回一句：默认挂该用户最新一条反馈，传 fid 则精确挂到那条。

    返回整个 thread（分组 + 平铺两套），让前端一次拿到最新状态：
    回复可能顺手把 open 推进到 doing，前端自己猜会显示旧状态。
    """
    _require_admin(uid)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="回复不能为空")
    if len(text) > user_feedback.MAX_REPLY:
        raise HTTPException(
            status_code=400, detail=f"回复最多 {user_feedback.MAX_REPLY} 个字"
        )
    author = _reply_author(uid, request)
    ok, err = await asyncio.to_thread(
        user_feedback.reply_thread, target, uid, author, text, req.fid
    )
    if not ok:
        # fid 不属于这个用户时是"越权写别人那条反馈"，用 400 而不是 404：
        # 反馈本身存在，错的是这组参数。
        code = 404 if "没有反馈" in err else 400
        raise HTTPException(status_code=code, detail=err)
    return await asyncio.to_thread(user_feedback.thread, target)


@router.get("/feedback/stats")
async def feedback_stats(
    days: int = Query(30, ge=1, le=365), uid: str = Depends(get_current_user)
) -> dict:
    """图表数据：分类分布 / 状态积压 / 逐日新增。口径见 `user_feedback.stats`。"""
    _require_admin(uid)
    return await asyncio.to_thread(user_feedback.stats, days)


class StatusPatch(BaseModel):
    status: str = Field(..., description="目标状态：open / doing / done / closed")


@router.patch("/feedback/{fid}")
async def feedback_patch(
    fid: int, req: StatusPatch, uid: str = Depends(get_current_user)
) -> dict:
    """改这一条反馈的状态。

    2026-11-19 起**允许任意互转（含回退）**：原来点错「已处理」就再也改不回来，
    而状态本来就是为了帮人记事，不是审计凭证。

    错误码：状态值不合法 → 400；反馈不存在 → 404。（旧的 409 已不再产生，
    因为不再有"流转冲突"这回事。）
    """
    _require_admin(uid)
    ok, err = await asyncio.to_thread(user_feedback.set_status, fid, req.status)
    if not ok:
        raise HTTPException(status_code=404 if "不存在" in err else 400, detail=err)
    return {"ok": True, "status": req.status}


class ReplyRequest(BaseModel):
    reply: str = Field(
        ...,
        description=(
            f"追加给提意见的人的回复，{user_feedback.MIN_REPLY}~{user_feedback.MAX_REPLY} 字。"
            "**只能追加，不能改写或撤回**"
        ),
    )


def _reply_author(uid: str, request: Request) -> str:
    """回复下方署的名：昵称 → 用户名 → 「管理员」。

    为什么查一次库而不直接用 token 里的 username：昵称是用户自己设的、
    也是界面上到处显示的那个名字，两个管理员同名同姓时用户只能靠它分辨。
    token 里的 username 是登录名，偶尔和昵称不是一回事。
    """
    try:
        user = account.get_user(uid) or {}
    except Exception:  # noqa: BLE001 - 取不到名字不该挡住回复
        user = {}
    name = str(user.get("nickname") or "").strip()
    if not name:
        name = str(getattr(request.state, "username", "") or "").strip()
    return name[:64]


@router.post("/feedback/{fid}/reply")
async def feedback_reply(
    fid: int, req: ReplyRequest, request: Request, uid: str = Depends(get_current_user)
) -> dict:
    """给一条反馈**追加**一条回复（管理员 → 用户）。

    为什么是 `POST .../reply` 而不是在 PATCH 里塞一个 reply 字段：
      PATCH `/feedback/{fid}` 的语义是"改这条反馈的属性"，它的错误契约是
      409 + 状态流转；回复是另一件事（还带着"顺手把 open 推到 doing"的副作用），
      两件事共用一个端点，将来想给回复加审计（谁回的、什么时候改过）就无处落脚。

    ★ 2026-11-17 起语义变了：这是**追加**，不是改写。
      加进第二个管理员之后，"一格回复人人可改"会让人静默覆盖别人的话。
      用户的要求是「自己发的回复不能被修改，别人只能在我的回复下面增加」，
      所以每条回复都带作者、发出去就定稿。说错了只能再补一条更正。

    400 / 409 分工与 PATCH 一致：长度超限、内容为空是"请求写错了"（400），
    反馈不存在是"和当前数据冲突"（409）—— 前端拿到 409 会去刷新列表。
    """
    _require_admin(uid)
    text = (req.reply or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="回复不能为空")
    if len(text) > user_feedback.MAX_REPLY:
        raise HTTPException(
            status_code=400, detail=f"回复最多 {user_feedback.MAX_REPLY} 个字"
        )
    author = await asyncio.to_thread(_reply_author, uid, request)
    ok, err = await asyncio.to_thread(user_feedback.add_reply, fid, uid, author, text)
    if not ok:
        raise HTTPException(status_code=409, detail=err)
    # 回读一次最新状态：add_reply 可能在 open 时把它推到 doing，
    # 前端要按新状态重画按钮，不能让前端自己猜。
    row = await asyncio.to_thread(user_feedback.get, fid) or {}
    return {
        "ok": True,
        "reply": text,
        "replies": row.get("replies", []),
        "status": row.get("status", ""),
        "status_label": row.get("status_label", ""),
        "replied_at": row.get("replied_at"),
    }


@router.get("/dict")
async def dict_api(uid: str = Depends(get_current_user)) -> dict:
    """分类 / 状态字典 + 流转表 + 长度限制。前端按钮与校验全部从这里取。"""
    _require_admin(uid)
    return user_feedback.dict_payload()


# ==================== 真实用户使用数据（2026-11-15）====================


@router.get("/real-users")
async def real_users(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=50),
    uid: str = Depends(get_current_user),
) -> dict:
    """真实用户使用数据（口径见字典 §2.8 / 契约见 §7.8）。

    与 `/overview` **并列而不是加个开关**：两者的分母不同，混在一个筛选里
    会让人以为"切了筛选总数会变"，实际是换了统计对象。所以各自独立，
    由响应里的 `calls` 四桶（真实 / 内部 / 已注销 / 未登录）保证两块能对上账。
    """
    _require_admin(uid)
    return await asyncio.to_thread(dashboard.real_user_view, days, limit)


class InternalPatch(BaseModel):
    internal: bool = Field(..., description="true = 标为内部/测试账号；false = 取消")


@router.post("/users/{target}/internal")
async def set_user_internal(
    target: str, req: InternalPatch, uid: str = Depends(get_current_user)
) -> dict:
    """标记 / 取消标记某个账号为内部账号。

    路径参数用 `{target}` 而不是 `{uid}`：函数参数里已经有一个 `uid`
    （当前管理员自己），同名会让 FastAPI 把两者绑到同一个值上 ——
    结果是"管理员永远只能标记自己"。这类错误不会报错，只会静默改错行。

    uid 不存在返回 **404**（与 `_require_admin` 的伪装一致：不确认这个 uid 是否存在）。
    """
    _require_admin(uid)
    if not target:
        raise HTTPException(status_code=404, detail="Not Found")
    ok = await asyncio.to_thread(account.set_internal, target, req.internal)
    if not ok:
        raise HTTPException(status_code=404, detail="Not Found")
    # 内部账号集合有 60s 缓存（采集层用它判定 X-Verify 是否可信）。
    # 标记完立刻失效，否则"刚标为内部"的账号这一分钟内的验证流量仍会被算成真实用户。
    verify_tag.invalidate()
    return {"ok": True, "uid": target, "internal": req.internal}

