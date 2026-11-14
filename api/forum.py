from __future__ import annotations

"""论坛中心路由（/api/forum）。

路由清单：
  GET    /api/forum/cats              板块清单 + 各板块话题数 + 广场统计
  GET    /api/forum/topics            话题列表（板块 / 排序 / 游标）
  POST   /api/forum/topics            发一个话题
  GET    /api/forum/topics/{id}       话题详情（含全部楼层）
  PATCH  /api/forum/topics/{id}       标「已解决」（楼主 / 管理员）
  DELETE /api/forum/topics/{id}       删话题（作者 / 管理员）
  POST   /api/forum/topics/{id}/replies  回复某个话题或某一层

它和 `/api/kb/public/{kb_id}/board` 的关系：**同一张表、两套门禁**。
  - 留言板只认"这个库公开着"，看的是 `kb_id`；
  - 论坛只认"这条有板块"，看的是 `cat != ''`，并且跨库。
一层 `_post_view` 两边共用（从 api.kb 借过来），所以楼层的形状、权限字段、
「回复 @某某」的推导规则不会漂成两份。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.kb import _board_authors, _post_view
from core import anon, forum, kb_board, quota
from core.auth import _is_secure_request, current_user_id, get_current_user
from kb import pipelines

# 论坛**不挂全局登录依赖**：它现在是免登录的（发帖只要一枚设备 cookie）。
# 管理员接口（/mod/*）单独要求真登录 + 管理员身份 —— 权限逐条声明，
# 不靠"整个 router 都要登录"这一条兜底。
#
# 这条改动是产品变更带来的：原来论坛是登录用户的讨论区，发言天然有对象；
# 现在定位成站内随手发一句的地方（占座、问一句、出个闲置），注册会把
# 发帖率打到地板。身份退化成"一枚设备 cookie"，但删除 / 举报 / 追溯
# 仍然有对象，见 core/anon.py 里写清的代价。
router = APIRouter()

# 标题长度上限，与前端输入框的 maxLength 一致。后端再截一次是因为 curl 也能发。
TITLE_MAX = kb_board.MAX_TITLE


class TopicIn(BaseModel):
    title: str = Field("", description="话题标题，最多 60 字")
    body: str = Field("", description="话题正文，最多 2000 字")
    cat: str = Field("", description="板块 key，见 GET /api/forum/cats")
    kb_id: str = Field("", description="关联的公开知识库 id，可空")


class ReplyIn(BaseModel):
    body: str = Field("", description="回复正文，最多 300 字")
    reply_to: int | None = Field(None, description="回复哪一层；不带就是回楼主")


class ModIn(BaseModel):
    action: str = Field(..., description="approve / remove / restore")
    reason: str = Field("", description="下架原因 key，见 queue 响应里的 reasons")


def anon_uid(request: Request, response: Response) -> str:
    """登录了就返回真 uid，没登录就发一枚设备 cookie 并返回匿名 uid。

    两种身份走同一个返回值，所以下游（发帖、删除、我的帖）不需要
    "如果是匿名就换一套逻辑" —— 它们只看见一个 uid。

    优先级：登录用户 > cookie。一个人登录着的时候发帖应该署真名，
    而不是被浏览器里残留的匿名 cookie 顶掉。
    """
    try:
        return current_user_id(request)
    except HTTPException:
        pass
    raw = (request.cookies.get(anon.COOKIE) or "").strip()
    if not anon.is_valid(raw):
        raw = anon.new_token()
        # httponly 必须：昵称是从这个值派生的，JS 读不到就没有
        # "偷一个 uid 来冒名发帖"的口子。
        response.set_cookie(
            anon.COOKIE,
            raw,
            max_age=anon.MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=_is_secure_request(request),  # noqa: SLF001 —— 与登录 cookie 同一判定
            path="/",
        )
    return anon.uid_of(raw)


def _norm_cat(cat: str) -> str:
    """校验板块 key。空串合法（= 没板块，但论坛发帖必须给板块，见 POST 里）。"""
    c = (cat or "").strip()
    if c and c not in forum.CAT_KEYS:
        raise HTTPException(status_code=400, detail="这个板块不存在")
    return c


def _kb_brief(kb_id: str) -> dict | None:
    """话题上的来源库 chip。库没了/名字查不到 → 不给 chip，而不是显示一串 id。"""
    if not kb_id:
        return None
    name = pipelines.name_of(kb_id)
    if not name:
        return None
    return {"id": kb_id, "name": name, "official": pipelines.is_official(kb_id)}


def _kb_briefs(rows: list[dict]) -> dict[str, dict]:
    """一页话题的来源库，按 id 去重后各查一次。

    `name_of` 是内存里的映射（带变更热加载），单次很便宜；但 20 条话题里
    常常有 8 条来自同一个库，逐条查就是白查 7 次。
    """
    out: dict[str, dict] = {}
    for kb_id in {r["kb_id"] for r in rows if r.get("kb_id")}:
        brief = _kb_brief(kb_id)
        if brief:
            out[kb_id] = brief
    return out


def _topic_view(row: dict, authors: dict[str, dict], uid: str, libs: dict[str, dict]) -> dict:
    a = authors.get(row["user_id"]) or {}
    return {
        "id": int(row["id"]),
        "title": forum.title_of(row),
        "excerpt": forum.excerpt_of(row),
        "cat": row["cat"],
        "cat_label": forum.CAT_LABEL.get(row["cat"], ""),
        "at": int(row["created_at"]),
        "last_at": int(row["last_at"]),
        "reply_n": int(row["reply_n"]),
        "mine": row["user_id"] == uid,
        # 对外只暴露 live / removed 两种。库里的正常态是空串 —— 那个值
        # 不该漏到前端，否则每处判断都要同时考虑 "" 和 "live" 是同一件事。
        "status": "removed" if (row.get("status") or "") == "removed" else "live",
        # 原因是 key 落库、label 出库：文案改一次全站生效，老数据不用动。
        "removed_reason": forum.REMOVE_LABEL.get(row.get("removed_reason") or "", ""),
        "removed_at": int(row.get("removed_at") or 0),
        "appeal_at": int(row.get("appeal_at") or 0),
        "report_n": int(row.get("report_n") or 0),
        "author": {
            "name": a.get("name") or "已注销用户",
            "avatar": a.get("avatar") or "",
            "admin": bool(a.get("admin")),
        },
        "kb": libs.get(row["kb_id"]),
    }


def _as_post(row: dict) -> dict:
    """sqlite 行 → `_post_view` 认识的形状（时间叫 `at`）。

    楼主要走和回复同一套楼层渲染，所以得先归一化：列表 SQL 里时间列名是
    `created_at`，而 `_post_view` 用的是 `at`。
    """
    return {
        "id": int(row["id"]),
        "user_id": row["user_id"],
        "body": row["body"],
        "at": int(row["created_at"]),
        "root_id": 0,
        "reply_to_id": 0,
    }


def _is_admin(uid: str) -> bool:
    return bool(quota._is_admin(uid))  # noqa: SLF001


@router.get("/cats")
async def get_cats(uid: str = Depends(anon_uid)) -> dict:
    """板块 + 计数 + 广场统计。首屏一次拿齐，省一次往返。"""
    counts, stat = await asyncio.to_thread(
        lambda: (forum.count_by_cat(), forum.stats())
    )
    admin = await asyncio.to_thread(_is_admin, uid)
    cats = [
        {
            "key": k,
            "label": label,
            "hint": hint,
            "n": int(counts.get(k, 0)),
            # 站务公告只让管理员发：这个板块在用户眼里是"官方说的话"，
            # 谁都能发就变成了第二个灌水区。
            "postable": admin or k != "meta",
        }
        for k, label, hint in forum.CATS
    ]
    return {
        "cats": cats,
        "total": int(sum(counts.values())),
        "replies": stat["replies"],
        "today": stat["today"],
    }


@router.get("/topics")
async def list_topics(
    cat: str | None = Query(None, description="板块 key，不传 = 全部"),
    sort: str = Query("new", pattern="^(new|hot)$"),
    before: int | None = Query(None, ge=1, description="上一页最后一条的 id（仅 sort=new）"),
    limit: int = Query(forum.PAGE, ge=1, le=50),
    q: str = Query("", max_length=60, description="标题或正文关键词"),
    uid: str = Depends(anon_uid),
) -> dict:
    """话题列表。

    `sort=hot` **不支持翻页**（`has_more` 恒为 false）：它按分数排序，分数一变
    游标就失效，硬做只会重复或漏条。热榜看 Top 20 够用。

    只有 `new` / `hot` 两种：这个论坛是拿来讨论和分享的，没有"提问—解决"的
    闭环，所以既没有「已解决」标记，也没有「未解决」筛选。
    
    `pinned` 只在「全部话题 + 第一页 + 没搜索」时给：公告在所有话题之上是站务
    惯例，但用户点进「求资料」或在搜关键词时，他明确要的不是公告。
    """
    clean = _norm_cat(cat or "")
    first_page = not clean and not before and not (q or "").strip() and sort == "new"

    pinned_rows = (
        await asyncio.to_thread(forum.list_pinned, 2) if first_page else []
    )
    rows, has_more = await asyncio.to_thread(
        forum.list_topics,
        clean or None,
        sort,
        before,
        limit,
        q,
        [r["id"] for r in pinned_rows],
    )
    all_rows = [*pinned_rows, *rows]
    authors = await asyncio.to_thread(_board_authors, all_rows) if all_rows else {}
    libs = await asyncio.to_thread(_kb_briefs, all_rows)
    return {
        "posts": [_topic_view(r, authors, uid, libs) for r in rows],
        "pinned": [_topic_view(r, authors, uid, libs) for r in pinned_rows],
        "has_more": bool(has_more) and sort == "new",
    }


@router.get("/topics/{post_id}")
async def get_topic(post_id: int, uid: str = Depends(anon_uid)) -> dict:
    """话题详情：标题区 + 全部楼层（楼主一层 + 回复若干）。"""
    row = await asyncio.to_thread(forum.get_topic, post_id)
    if not row or row.get("deleted_at") or not row["cat"]:
        raise HTTPException(status_code=404, detail="这个话题不存在")

    # 下架之后原链接还要能打开。直接 404 会让人以为网站坏了 —— 尤其这条
    # 链接可能已经被分享到班级群里，点进来是"页面不存在"比"已下架"伤害更大。
    if (row.get("status") or "") == "removed":
        authors = await asyncio.to_thread(_board_authors, [row])
        libs = await asyncio.to_thread(_kb_briefs, [row])
        mine = row["user_id"] == uid
        return {
            "topic": _topic_view(row, authors, uid, libs),
            "body": "",
            "posts": [],
            "removed": True,
            # 原因只给作者。别人看到"已下架"就够了 —— 公布原因等于把违规
            # 细节二次传播，也让作者在同学面前被点名。
            "reason": (
                forum.REMOVE_LABEL.get(row.get("removed_reason") or "", "")
                if mine
                else ""
            ),
            "can_appeal": mine and int(row.get("appeal_at") or 0) == 0,
            "stickers": list(kb_board.STICKERS),
            # 0 就是"不能回复"。前端据此把输入框收掉，而不是让人写完再报错。
            "max_reply": 0,
        }

    kids = await asyncio.to_thread(kb_board.replies_of, post_id)
    flat = [_as_post(row), *kids]
    authors = await asyncio.to_thread(_board_authors, flat)
    admin = await asyncio.to_thread(_is_admin, uid)
    libs = await asyncio.to_thread(_kb_briefs, [row])
    # 库主也能删自己库下面被带到论坛来的话题 —— 与留言板同一条规则，
    # 不然会出现"库主能在留言板删、在论坛删不掉"这种自相矛盾。
    owner = (libs.get(row["kb_id"]) and pipelines.owner_of(row["kb_id"])) or ""

    names = {
        p["id"]: (authors.get(p["user_id"]) or {}).get("name") or "已注销用户" for p in flat
    }
    floor = _post_view(_as_post(row), authors, names, uid, owner, admin)
    return {
        "topic": _topic_view(row, authors, uid, libs),
        "body": row["body"],
        "posts": [floor, *[_post_view(p, authors, names, uid, owner, admin) for p in kids]],
        "stickers": list(kb_board.STICKERS),
        "max_reply": kb_board.MAX_LEN,
        # 正常态也把这三个字段给上（removed=False / 空原因 / 不能申诉）。
        # 让响应形状与下架分支一致：前端不用写 `"removed" in data`，
        # 少一处"有时候有、有时候没有"的字段就少一处判断错的机会。
        "removed": False,
        "reason": "",
        "can_appeal": False,
    }


@router.post("/topics")
async def create_topic(req: TopicIn, uid: str = Depends(anon_uid)) -> dict:
    """发一个话题。板块必填、标题必填，正文可以只有一句。"""
    cat = _norm_cat(req.cat)
    if not cat:
        raise HTTPException(status_code=400, detail="先选一个板块")
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="给话题起个标题")
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX]
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="正文不能为空")
    if len(body) > kb_board.TOPIC_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"正文最多 {kb_board.TOPIC_LEN} 字，现在 {len(body)} 字",
        )
    if cat == "meta" and not await asyncio.to_thread(_is_admin, uid):
        raise HTTPException(status_code=403, detail="站务公告只有管理员能发")

    kb_id = (req.kb_id or "").strip()
    if kb_id:
        # 关联的库必须还公开着。收回公开之后不该还能往它的讨论区里发东西，
        # 也不该让"私有库 id"通过这个口子被验证存在（所以是 404 不是 403）。
        owner = await asyncio.to_thread(pipelines.owner_of, kb_id)
        if not owner or not await asyncio.to_thread(pipelines.is_public, kb_id):
            raise HTTPException(status_code=404, detail="这个知识库不存在或没有公开")

    try:
        post = await asyncio.to_thread(
            kb_board.add_post, kb_id, uid, body, None, title, cat, kb_board.TOPIC_LEN
        )
    except kb_board.TooFast as e:
        detail = f"发得太快了，{e.wait} 秒后再试" if e.wait else "这一小时发得有点多，歇一会儿再来"
        raise HTTPException(status_code=429, detail=detail) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    row = await asyncio.to_thread(forum.get_topic, post["id"])
    authors = await asyncio.to_thread(_board_authors, [post])
    libs = await asyncio.to_thread(_kb_briefs, [row]) if row else {}
    return {"topic": _topic_view(row, authors, uid, libs) if row else None}


@router.post("/topics/{post_id}/replies")
async def add_reply(
    post_id: int, req: ReplyIn, uid: str = Depends(anon_uid)
) -> dict:
    """回复话题，或者回复楼里的某一层（仍只落两层）。"""
    topic = await asyncio.to_thread(forum.get_topic, post_id)
    if not topic or topic.get("deleted_at") or not topic["cat"]:
        raise HTTPException(status_code=404, detail="这个话题不存在")
    if int(topic["root_id"] or 0):
        raise HTTPException(status_code=400, detail="只能回复话题本身")

    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="写点什么再发吧")
    if len(body) > kb_board.MAX_LEN:
        raise HTTPException(
            status_code=400, detail=f"回复最多 {kb_board.MAX_LEN} 字，现在 {len(body)} 字"
        )

    # 不传 reply_to 就是回楼主。传了的话要确认那条**就在这栋楼里** ——
    # 只靠 add_post 里"同一个 kb_id"的校验不够：同一个库下可能有十几栋楼，
    # 拿隔壁楼的回复 id 来回复，新的这条会被挂到隔壁楼下面，用户在自己的帖子里
    # 永远看不到它（而且日志不会报错）。
    target = int(req.reply_to or post_id)
    if target != post_id:
        tgt = await asyncio.to_thread(kb_board.get_post, target)
        if (
            not tgt
            or tgt.get("deleted_at")
            or int(tgt.get("root_id") or tgt["id"]) != post_id
        ):
            raise HTTPException(status_code=400, detail="要回复的那条不在这栋楼里")
    try:
        post = await asyncio.to_thread(
            kb_board.add_post, topic["kb_id"], uid, body, target
        )
    except kb_board.TooFast as e:
        detail = f"发得太快了，{e.wait} 秒后再试" if e.wait else "这一小时发得有点多，歇一会儿再来"
        raise HTTPException(status_code=429, detail=detail) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    kids = await asyncio.to_thread(kb_board.replies_of, post_id)
    authors = await asyncio.to_thread(_board_authors, [post])
    admin = await asyncio.to_thread(_is_admin, uid)
    libs = await asyncio.to_thread(_kb_briefs, [topic])
    owner = (libs.get(topic["kb_id"]) and pipelines.owner_of(topic["kb_id"])) or ""
    names = {
        p["id"]: (authors.get(p["user_id"]) or {}).get("name") or "已注销用户" for p in kids
    }
    mine = next((p for p in kids if p["id"] == post["id"]), post)
    return {
        "post": _post_view(mine, authors, names, uid, owner, admin),
        "reply_n": len(kids),
    }


@router.delete("/topics/{post_id}")
async def delete_topic(post_id: int, uid: str = Depends(anon_uid)) -> dict:
    """软删话题，或者删楼里的**单条回复**。

    同一个接口两种粒度，因为删的都是 kb_posts 里的行 —— 分成
    `/topics/{id}` 和 `/replies/{id}` 两条，前端要判断"我点的是哪一层"两次，
    而它本来就知道。返回里的 `root_id` 让前端区分：非 0 表示删的是一条回复，
    整栋楼还在，不用整页刷新。
    """
    row = await asyncio.to_thread(forum.get_topic, post_id)
    if not row or row.get("deleted_at"):
        raise HTTPException(status_code=404, detail="这条不存在")
    root_id = int(row["root_id"] or 0)
    # 删的是回复时，权限要看**所属话题**的库主，而不是这条回复自己
    parent = row if not root_id else await asyncio.to_thread(forum.get_topic, root_id)
    if not parent or parent.get("deleted_at") or not parent["cat"]:
        raise HTTPException(status_code=404, detail="这条不存在")
    admin = await asyncio.to_thread(_is_admin, uid)
    owner = (
        await asyncio.to_thread(pipelines.owner_of, parent["kb_id"])
        if parent["kb_id"]
        else ""
    )
    if not (row["user_id"] == uid or owner == uid or admin):
        raise HTTPException(status_code=403, detail="只能删除自己发的")
    removed = await asyncio.to_thread(kb_board.delete_post, post_id, uid, row["kb_id"])
    if not removed:
        raise HTTPException(status_code=404, detail="这条不存在")
    return {"ok": True, "id": post_id, "removed": removed, "root_id": root_id}


# ============================================================
# 免登录的两条：我发的帖 / 申请复核
# ============================================================


@router.get("/mine")
async def my_topics(
    limit: int = Query(50, ge=1, le=100),
    uid: str = Depends(anon_uid),
) -> dict:
    """这台设备发过的帖，**含已下架的**。

    免登录没有消息中心，这个接口就是"通知"的落点：作者回访时在
    「我发的帖」里看到哪条被判违规、原因是什么、能不能申诉。
    界面上必须写清"靠这台设备认出来的" —— 写成"我的账号"就是在撒谎。
    """
    rows = await asyncio.to_thread(forum.list_mine, uid, limit)
    authors = await asyncio.to_thread(_board_authors, rows) if rows else {}
    libs = await asyncio.to_thread(_kb_briefs, rows)
    return {
        "posts": [_topic_view(r, authors, uid, libs) for r in rows],
        "removed_n": sum(1 for r in rows if (r.get("status") or "") == "removed"),
    }


@router.post("/topics/{post_id}/appeal")
async def appeal_topic(post_id: int, uid: str = Depends(anon_uid)) -> dict:
    """作者申请复核。只能点一次 —— 反复点会把管理台刷满，复核就变成噪音。"""
    ok = await asyncio.to_thread(forum.appeal, post_id, uid)
    if not ok:
        raise HTTPException(
            status_code=400, detail="这条现在不能申请复核（可能已经申请过了）"
        )
    return {"ok": True}


# ============================================================
# 管理台（先发后审）
# ============================================================


async def _require_admin(uid: str) -> None:
    """管理员门禁。抽成函数是因为几个端点都要它 —— 逐个抄一遍，
    迟早有一个写成 `if not admin: pass`（这种错不会报错，只是不拦）。"""
    if not await asyncio.to_thread(_is_admin, uid):
        raise HTTPException(status_code=403, detail="只有管理员能进管理台")


@router.get("/mod/queue")
async def mod_queue(admin: str = Depends(get_current_user)) -> dict:
    """待审 + 被申诉 + 三个计数，一次拿齐。

    队列里没有"通过过的"（`reviewed_at > 0` 已被排除），也没有已下架的。
    管理员打开就是"现在该看的"，不用自己筛 —— 筛选条件放在服务端，
    前端多一个 tab 就多一处可能和规则不一致的实现。
    """
    await _require_admin(admin)
    pending, appeals = await asyncio.gather(
        asyncio.to_thread(forum.review_queue, 40),
        asyncio.to_thread(forum.list_appeals, 20),
    )
    stat = await asyncio.to_thread(forum.mod_stats)
    rows = [*pending, *appeals]
    authors = await asyncio.to_thread(_board_authors, rows) if rows else {}
    libs = await asyncio.to_thread(_kb_briefs, rows)
    return {
        "stats": stat,
        "pending": [_topic_view(r, authors, admin, libs) for r in pending],
        "appeals": [_topic_view(r, authors, admin, libs) for r in appeals],
        "reasons": [{"key": k, "label": v} for k, v in forum.REMOVE_REASONS],
    }


@router.post("/mod/{post_id}")
async def mod_review(
    post_id: int, req: ModIn, admin: str = Depends(get_current_user)
) -> dict:
    """通过 / 下架 / 撤销下架。三个动作一个接口 —— 它们改的是同一行、
    记的是同两个字段；拆成三个端点只会让前端的 URL 变长，规则还是同一套。"""
    await _require_admin(admin)
    try:
        row = await asyncio.to_thread(
            forum.review, post_id, req.action, admin, req.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=404, detail="这条不存在")
    authors = await asyncio.to_thread(_board_authors, [row])
    libs = await asyncio.to_thread(_kb_briefs, [row])
    return {"topic": _topic_view(row, authors, admin, libs)}
