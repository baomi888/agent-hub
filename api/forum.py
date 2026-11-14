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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.kb import _board_authors, _post_view
from core import forum, kb_board, quota
from core.auth import get_current_user
from kb import pipelines

# 论坛和留言板一样要登录：发言是要署名的，匿名会让"举报/删除"没有对象
router = APIRouter(dependencies=[Depends(get_current_user)])

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


class TopicPatch(BaseModel):
    solved: bool | None = Field(None, description="标记 / 取消「已解决」")


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
        "solved": bool(row["solved"]),
        "at": int(row["created_at"]),
        "last_at": int(row["last_at"]),
        "reply_n": int(row["reply_n"]),
        "mine": row["user_id"] == uid,
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
async def get_cats(uid: str = Depends(get_current_user)) -> dict:
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
    sort: str = Query("new", pattern="^(new|hot|unsolved)$"),
    before: int | None = Query(None, ge=1, description="上一页最后一条的 id（仅 sort=new）"),
    limit: int = Query(forum.PAGE, ge=1, le=50),
    q: str = Query("", max_length=60, description="标题或正文关键词"),
    uid: str = Depends(get_current_user),
) -> dict:
    """话题列表。

    `sort=hot` / `sort=unsolved` **不支持翻页**（`has_more` 恒为 false）：
    它们按分数排序，分数一变游标就失效，硬做只会重复或漏条。热榜看 Top 20 够用。
    """
    clean = _norm_cat(cat or "")
    rows, has_more = await asyncio.to_thread(
        forum.list_topics, clean or None, sort, before, limit, q
    )
    authors = await asyncio.to_thread(_board_authors, rows) if rows else {}
    libs = await asyncio.to_thread(_kb_briefs, rows)
    return {
        "posts": [_topic_view(r, authors, uid, libs) for r in rows],
        "has_more": bool(has_more) and sort == "new",
    }


@router.get("/topics/{post_id}")
async def get_topic(post_id: int, uid: str = Depends(get_current_user)) -> dict:
    """话题详情：标题区 + 全部楼层（楼主一层 + 回复若干）。"""
    row = await asyncio.to_thread(forum.get_topic, post_id)
    if not row or row.get("deleted_at") or not row["cat"]:
        raise HTTPException(status_code=404, detail="这个话题不存在")

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
        # 标「已解决」只给楼主和管理员：路人觉得"这题答上了"不算数，
        # 提问的人自己说解决了才算。
        "can_solve": row["user_id"] == uid or admin,
        "stickers": list(kb_board.STICKERS),
        "max_reply": kb_board.MAX_LEN,
    }


@router.post("/topics")
async def create_topic(req: TopicIn, uid: str = Depends(get_current_user)) -> dict:
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
    post_id: int, req: ReplyIn, uid: str = Depends(get_current_user)
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


@router.patch("/topics/{post_id}")
async def patch_topic(
    post_id: int, req: TopicPatch, uid: str = Depends(get_current_user)
) -> dict:
    """目前只开放「已解决」。改标题/板块会破坏别人引用过的那句话，不做。"""
    topic = await asyncio.to_thread(forum.get_topic, post_id)
    if not topic or topic.get("deleted_at") or not topic["cat"]:
        raise HTTPException(status_code=404, detail="这个话题不存在")
    admin = await asyncio.to_thread(_is_admin, uid)
    if not (topic["user_id"] == uid or admin):
        raise HTTPException(status_code=403, detail="只有楼主能标记解决状态")
    if req.solved is None:
        raise HTTPException(status_code=400, detail="没有要改的字段")
    await asyncio.to_thread(forum.set_solved, post_id, req.solved)
    return {"ok": True, "id": post_id, "solved": bool(req.solved)}


@router.delete("/topics/{post_id}")
async def delete_topic(post_id: int, uid: str = Depends(get_current_user)) -> dict:
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
