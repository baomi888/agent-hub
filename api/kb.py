from __future__ import annotations

"""知识库管理路由。

路由清单：
  POST   /api/kb/{kb_id}/upload       上传文件 + 构建索引
  POST   /api/kb/import-search        联网搜索预览（返回候选列表供勾选）
  POST   /api/kb/{kb_id}/import-url   直接抓 URL 入库
  POST   /api/kb/{kb_id}/import-batch 批量抓 URL 入库（搜索勾选结果）
  GET    /api/kb/dialogue-candidates/{sid}  对话知识候选预览（报告 v2 §4.1）
  POST   /api/kb/{kb_id}/import-dialogue    勾选的对话知识句沉淀入库
  GET    /api/kb/download             代理下载远程文档到浏览器
  GET    /api/kb/{kb_id}/files         列出库内文件
  GET    /api/kb/{kb_id}/file-content  预览单个文件正文
  DELETE /api/kb/{kb_id}/files         删除单个文件
  POST   /api/kb/{kb_id}/rebuild       按切片参数重建整库
  GET    /api/kb/{kb_id}/              查看库状态
  GET    /api/kb/                      列出所有知识库
  DELETE /api/kb/{kb_id}/              删除知识库
  POST   /api/kb/{kb_id}/visibility    设置公开状态与广场简介（仅库主）
  GET    /api/kb/public                公开知识库广场列表（含简介/文件数/作者）
  GET    /api/kb/public/{kb_id}        官方课程库详情（含文件清单）
  GET    /api/kb/public/{kb_id}/file   官方课程库原始文件（下载 / 内嵌预览）
  GET    /api/kb/public/{kb_id}/text   官方课程库资料正文预览
  GET    /api/kb/public/{kb_id}/board  公开库留言（分页，游标 before）
  POST   /api/kb/public/{kb_id}/board  发一条留言（纯文字）
  DELETE /api/kb/public/{kb_id}/board/{post_id}  删留言（作者/库主/管理员）
  POST   /api/kb/{kb_id}/query         同步问答（公开库对全站登录用户开放）
"""

import asyncio
import os
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from api.schemas import KBQueryResponse
from core import account, config, kb_board, kb_favs, kb_views, quota, upload
from core import session as store
from core.auth import get_current_user
from core.netsafe import assert_safe_url
from kb import dialogue_import, pipelines
from kb.importer import import_url, import_urls_batch, search_preview
from kb.rag_core import describe_api_error, load_document
from service import sediment

# 默认要登录：知识库是用户资产，列举/下载/删除都必须先验身份
router = APIRouter(dependencies=[Depends(get_current_user)])

MAX_UPLOAD_MB = 100
# 代理下载上限：与 Agent 工具通道（kb.importer）保持一致，防大文件 OOM
_DOWNLOAD_MAX_MB = 200
_DOWNLOAD_CHUNK = 256 * 1024


def _must_own(uid: str, kb_id: str) -> None:
    """归属校验：不是自己的库统一 404。

    刻意不返回 403：403 等于告诉对方"这个 kb_id 是真的，只是你没权限"，
    那是一条可以用来枚举别人知识库的线索。不存在与无权，对外长得一样。
    """
    if pipelines.owner_of(kb_id) != uid:
        raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")


def _must_own_or_public(uid: str, kb_id: str) -> None:
    """只读检索的放行门：库主放行，全站公开库对登录用户放行。

    与 _must_own 的唯一区别就是公开库这一条例外——文件清单、上传、删除
    等其余接口一律不走这里，仍然只认库主。无权时同样 404（不泄露存在性）。
    """
    if pipelines.owner_of(kb_id) == uid or pipelines.is_public(kb_id):
        return
    raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")


# ==================== 上传 + 建库 ====================


@router.post("/{kb_id}/upload")
async def upload_and_build(
    kb_id: str,
    files: list[UploadFile],
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    mode: str = "append",
    uid: str = Depends(get_current_user),
) -> dict:
    """上传文档到指定知识库并入库。

    mode=append（默认）：只对新文件做 embedding，已有片段不动。
    mode=rebuild：清空整库后重建，仅在改了切片参数时用（会对全部文件重新计费）。

    原始文件会留档到 data/kb_sources/{kb_id}/，否则换切片参数时无法重建。
    """
    _must_own(uid, kb_id)
    # 先卡限流与当日额度，再动磁盘：超限时一个字节都不该落盘
    await asyncio.to_thread(quota.guard, uid, "embed")
    if not files:
        raise HTTPException(status_code=400, detail="未上传任何文件")
    if mode not in ("append", "rebuild"):
        raise HTTPException(status_code=400, detail="mode 只能是 append 或 rebuild")

    # 单次请求的声明总大小（S-07）：N 个文件各 100MB 时，单文件闸挡不住整体。
    declared = sum(f.size for f in files if isinstance(getattr(f, "size", None), int))
    if config.UPLOAD_TOTAL_MB and declared > config.UPLOAD_TOTAL_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"本次上传总大小超过 {config.UPLOAD_TOTAL_MB}MB，请分批上传",
        )

    now = int(time.time())
    limit = MAX_UPLOAD_MB * 1024 * 1024
    saved: list[tuple[str, str]] = []  # (落盘路径, 原始文件名)
    pipelines.ensure_source_dir(kb_id)

    # 第一趟只校验：扩展名 / 大小。
    # 以前校验和写盘混在一个循环里——第 3 个文件格式不合法时，前两个已经躺在磁盘上了，
    # 而它们既没登记进 kb_files.json 也没进 Chroma，只有删整个库才清得掉。
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".txt", ".md", ".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式：{ext}（当前支持 TXT / Markdown / PDF）",
            )
        upload.check_declared_size(f, limit)

    # 第二趟才落盘：分块读，超限立刻停手
    try:
        for f in files:
            ext = os.path.splitext(f.filename or "")[1].lower()
            # 原始文件名只用来登记和展示，落盘位置由 source_path 重算；
            # 这里先收一次，避免超长名/带路径的名把后续流程搞乱
            name = pipelines.safe_source_name(f.filename or "", fallback_ext=ext)
            dest = pipelines.source_path(kb_id, name)
            await upload.save_stream(f, dest, limit)
            saved.append((dest, name))
    except Exception:
        # 一个都还没入库，已落盘的必须清掉，否则它们成了没人认领的孤儿文件
        upload.cleanup(p for p, _ in saved)
        raise

    # 每日上传流量配额（S-07）：按实际落盘字节记账，与聊天附件同一张账。
    # 超限时先清掉刚落的文件再抛错（前面已入库的片段不受影响）。
    upload_bytes = sum(os.path.getsize(p) for p, _ in saved)
    if upload_bytes:
        upload_mb = max(1, (upload_bytes + 1024 * 1024 - 1) // (1024 * 1024))
        try:
            await asyncio.to_thread(quota.consume, uid, "upload", upload_mb)
        except quota.QuotaExceeded:
            upload.cleanup(p for p, _ in saved)
            raise

    def _build() -> tuple[int, list[str]]:
        """切片 + embedding + 写 Chroma，全程可能几十秒。

        两件事必须同时成立：
          1. 拿 kb_lock —— 同库的入库 / 重建 / 删文件串行，否则两个上传并发写
             同一个 collection，后一个的 rebuild 会清掉前一个刚写进去的片段；
          2. 跑在线程里 —— 这段是纯同步阻塞代码，留在 async def 里会把
             uvicorn 的事件循环占死，期间切会话、看列表全在排队。
        """
        with pipelines.kb_lock(kb_id):
            pipe = pipelines.get_or_create(uid, kb_id)
            total = 0
            pending: list[dict] = []
            try:
                for idx, (path, name) in enumerate(saved):
                    # 只有第一个文件在 rebuild 模式下清库，其余一律追加
                    n = pipe.build_index(
                        chunk_size,
                        chunk_overlap,
                        [path],
                        mode=(mode if idx == 0 else "append"),
                        source_names=[name],
                    )
                    total += n
                    pending.append({"name": name, "chunks": n, "added_at": now, "origin": "upload"})
            finally:
                # 一次性登记本轮已入库的文件：add_file_records 内部是"重读 kb_files.json →
                # 改 → 整份覆盖写"，逐文件调用等于 N 次读 + N 次写（每次还带一次落盘 fsync）。
                # 放在 finally 里是为了保住原来的语义：中途某个文件失败时，前面已经入库成功的
                # 文件记录照样要写进去（tests/test_baseline_kb_api.py 盯着这一点）。
                if pending:
                    pipelines.add_file_records(uid, kb_id, pending)
            if mode == "rebuild":
                # 整库重建：本次没上传的旧记录要一并清掉
                pipelines.retain_file_records(uid, kb_id, {name for _, name in saved})
            return total, pipelines.get_file_names(uid, kb_id)

    try:
        total, names = await asyncio.to_thread(_build)
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e)) from e
    # 按真实切片数记账：入口那道 ensure 只知道"还没超"，不知道这一次切了多少
    if total:
        await asyncio.to_thread(quota.bump_usage, uid, "embed", total)
    return {"kb_id": kb_id, "chunks": total, "files": names}


# ==================== 查询 ====================


@router.post("/{kb_id}/query", response_model=KBQueryResponse)
async def query_kb(
    kb_id: str,
    question: str,
    top_k: int = Query(default=3, gt=0, le=config.TOP_K_MAX),
    uid: str = Depends(get_current_user),
) -> dict:
    """同步问答：返回带 [1][2] 引用角标的答案。

    公开库例外：别的登录用户也可以问（站内公开只放开检索这一件事），
    额度记在**提问者**头上——消耗的是调用方自己的 ask 额度。
    """
    _must_own_or_public(uid, kb_id)
    # 一次问答 = 一次检索 + 一次 LLM，落到每日问答额度里
    await asyncio.to_thread(quota.consume, uid, "ask")
    pipe = pipelines.get_or_create(uid, kb_id, allow_public=True)
    try:
        # 检索 + LLM 都是同步阻塞调用，丢到线程里跑，别占着事件循环
        answer, refs = await asyncio.to_thread(pipe.answer, question, top_k)
        return {"answer": answer, "refs": refs}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e)) from e


# ==================== 列表 / 状态 / 删除 ====================


@router.get("/")
async def list_kbs(uid: str = Depends(get_current_user)) -> dict:
    """列出**当前用户**的知识库。别人的库（含改造前的无主库）不出现。"""
    # 扫磁盘 + 逐个打开 collection，全是同步 I/O，不能留在事件循环里
    return {"kbs": await asyncio.to_thread(pipelines.list_all, uid)}


class CreateKbRequest(BaseModel):
    name: str = Field(..., description="知识库名称（支持中文）")


@router.post("/create")
async def create_kb(req: CreateKbRequest, uid: str = Depends(get_current_user)) -> dict:
    """登记知识库（名称支持中文），返回内部安全 kb_id 与显示名。

    kb_id 里混进了 owner，两人建同名库也各是各的。
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    kb_id, display_name = pipelines.create_kb(uid, name)
    return {"kb_id": kb_id, "name": display_name}


@router.get("/{kb_id}/")
async def get_kb_status(kb_id: str, uid: str = Depends(get_current_user)) -> dict:
    """查看单个知识库状态。"""
    _must_own(uid, kb_id)
    status = await asyncio.to_thread(pipelines.get_status, uid, kb_id)
    if status is None:
        # 内存里没记录，但磁盘上可能存在
        if await asyncio.to_thread(pipelines.exists, uid, kb_id):
            return {
                "kb_id": kb_id,
                "chunks": 0,
                "chunk_size": None,
                "chunk_overlap": None,
                "files": [],
            }
        raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")
    return status


@router.delete("/{kb_id}/")
async def delete_kb(kb_id: str, uid: str = Depends(get_current_user)) -> dict:
    """删除知识库。"""
    _must_own(uid, kb_id)
    # 删 collection + rmtree 原始文件，都是同步 I/O
    if not await asyncio.to_thread(pipelines.delete_kb, uid, kb_id):
        # 可能磁盘上有但内存里没注册
        if await asyncio.to_thread(pipelines.exists, uid, kb_id):
            # 强制删除
            def _force_delete() -> None:
                pipelines.get_or_create(uid, kb_id).delete()

            await asyncio.to_thread(_force_delete)
            # 库都没了，留言/浏览/收藏也就没有了展示对象：这里物理删，
            # 不留软删的行（收藏不清的话，「我的收藏」里会留一张点开就 404 的卡片）
            await asyncio.to_thread(kb_board.purge_kb, kb_id)
            await asyncio.to_thread(kb_views.purge_kb, kb_id)
            await asyncio.to_thread(kb_favs.purge_kb, kb_id)
            return {"ok": True}
        raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")
    await asyncio.to_thread(kb_board.purge_kb, kb_id)
    await asyncio.to_thread(kb_views.purge_kb, kb_id)
    await asyncio.to_thread(kb_favs.purge_kb, kb_id)
    return {"ok": True}


# ==================== 公开共享（站内只读） ====================


class VisibilityRequest(BaseModel):
    public: bool = Field(..., description="true=公开，false=收回公开")
    desc: str | None = Field(
        None,
        max_length=200,
        description="广场上那句简介。不传 = 不动已有简介；传空串 = 清空",
    )
    access: str | None = Field(
        None,
        pattern="^(preview|download)$",
        description="公开后别人能做什么：preview=仅观看（可预览不可下载），download=可下载。不传=不动",
    )


class ForkRequest(BaseModel):
    name: str | None = Field(
        None,
        max_length=60,
        description="副本的名字。不传 = 沿用源库名（同名会被复用/更新而不是新建）",
    )


def _owner_display_name(uid: str) -> str:
    """公开库卡片上的作者名。

    优先昵称，没设过退回用户名 —— 与 `core/account._public` 的回落规则一致
    （那里空串表示"没设过"）。查不到人（注销、脏归属）返回空串，
    前端会显示成「某位同学」，不至于让整张卡片因为一个名字加载失败。
    """
    try:
        u = account.get_user(uid)
    except Exception:
        return ""
    if not u:
        return ""
    return (u.get("nickname") or "").strip() or (u.get("username") or "").strip()


@router.post("/{kb_id}/visibility")
async def set_kb_visibility(
    kb_id: str, req: VisibilityRequest, uid: str = Depends(get_current_user)
) -> dict:
    """设置知识库公开状态、广场简介与访问方式（仅库主）。

    公开后别人能做什么由 `access` 决定（2026-11-13 起）：
      - `preview`（默认）→ 能看文件清单、在线预览，**不能下载原件**；
      - `download`      → 再加一项"下载原件"。
    文件清单从此也对同学分享的库开放了 —— 这是用户明确要的（"同学可以设置
    仅观看还是可以下载"）。上传、删除、改归属仍然只有库主能动。
    收回公开立即生效——别人的对话里还挂着这个库的话，下一轮提问的认主
    逻辑会把它按"没绑"处理，不需要对方做什么。
    """
    _must_own(uid, kb_id)
    # 简介归一化：纯空白等于没写。None 表示"这次别动简介"（收回时不传）
    desc = req.desc.strip() if req.desc is not None else None
    try:
        meta = pipelines.set_public(uid, kb_id, req.public, desc, access=req.access)
    except pipelines.NotOwned:  # _must_own 之后库被并发删掉的窄窗口
        raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}") from None
    return {
        "kb_id": kb_id,
        "public": req.public,
        "desc": meta.get("desc") or "",
        "access": "download" if meta.get("access") == "download" else "preview",
    }


@router.get("/public")
async def list_public_kbs(uid: str = Depends(get_current_user)) -> dict:
    """公开知识库广场的列表（全站登录用户可见）。

    比"能检索"多回展示用元信息：简介、文件数、作者显示名、分享时间、留言数、
    浏览数。两条边界仍然守着：
      - **列表里**不含文件清单，只给数量（清单要点进详情才给，见 `GET /public/{kb_id}`）；
      - 不回库主的 uid，只回显示名（uid 是内部标识，没必要抛给前端）。
    调用者自己的库不重复出现 —— 它们在 GET /api/kb/ 里，广场另有「我的」分组。
    """
    kbs = await asyncio.to_thread(pipelines.list_public)
    mine = await asyncio.to_thread(pipelines.owned_kb_ids, uid)
    # 留言数一次查全：广场上十几个库，逐个 count 就是十几次查询，而卡片上
    # 「N 条留言」本来就是让人判断"这个库有没有人在讨论"的信号，不能没有。
    post_counts = await asyncio.to_thread(kb_board.counts_by_kb)
    view_counts = await asyncio.to_thread(kb_views.counts)
    fav_counts = await asyncio.to_thread(kb_favs.counts)
    my_favs = await asyncio.to_thread(kb_favs.mine_ids, uid)
    # 同名库：不同用户可能传来同名的库（"考研数学"会有好几份）。
    # 广场不合并它们（内容不同，合并等于替用户做判断），但要告诉前端
    # "这个名字下还有几个"，前端据此做同名筛选、并按热度排。
    name_counts: dict[str, int] = {}
    for k in kbs:
        nm = (k.get("name") or "").strip()
        name_counts[nm] = name_counts.get(nm, 0) + 1
    out = []
    for k in kbs:
        if k["kb_id"] in mine:
            continue
        k = dict(k)
        k["posts"] = post_counts.get(k["kb_id"], 0)
        # 浏览数（独立访客）：卡片上「N 人看过」，也是"最多人用的库"这个排序的依据
        k["views"] = view_counts.get(k["kb_id"], 0)
        # 收藏数：比浏览更"重"的信号（有意识收藏 vs 点进去看一眼）
        k["favs"] = fav_counts.get(k["kb_id"], 0)
        k["favorited"] = k["kb_id"] in my_favs
        k["same_name"] = name_counts.get((k.get("name") or "").strip(), 1)
        # 官方库没有真实账号，`_owner_display_name` 查不到人只会回空串；
        # 这里直接署名"官方"，避免卡片上出现"某位同学"这种明显不对的说法
        k["owner_name"] = "官方" if k.get("official") else await asyncio.to_thread(
            _owner_display_name, k["owner"]
        )
        k.pop("owner", None)
        out.append(k)
    return {"kbs": out}


# ==================== 收藏（公开库）====================
#
# 收藏是"我留着它的清单"，和浏览（点进去就算）不是一回事，所以单独一张表、
# 单独的读法。广场卡片上的星标、详情页的收藏按钮、以及"我的收藏"页共用这三条接口。
# 用 PUT/DELETE 而不是一个 toggle 接口：toggle 在网络重试时会翻来覆去，
# 用户看到的状态和他以为的会不一样。


@router.put("/public/{kb_id}/favorite")
async def add_favorite(kb_id: str, uid: str = Depends(get_current_user)) -> dict:
    """收藏一个公开库。重复收藏是幂等的（返回 added=false）。"""
    _public_kb_or_404(kb_id)
    added = await asyncio.to_thread(kb_favs.add, kb_id, uid)
    count = await asyncio.to_thread(kb_favs.count, kb_id)
    return {"kb_id": kb_id, "favorited": True, "added": added, "favs": count}


@router.delete("/public/{kb_id}/favorite")
async def remove_favorite(kb_id: str, uid: str = Depends(get_current_user)) -> dict:
    """取消收藏。本来没收藏也不报错（幂等）。"""
    _public_kb_or_404(kb_id)
    removed = await asyncio.to_thread(kb_favs.remove, kb_id, uid)
    count = await asyncio.to_thread(kb_favs.count, kb_id)
    return {"kb_id": kb_id, "favorited": False, "removed": removed, "favs": count}


@router.get("/favorites")
async def list_favorites(uid: str = Depends(get_current_user)) -> dict:
    """我收藏的公开库，最近收藏的在前。

    每条带**来源与作者**（用户要求）：
      - 官方库 → `source = "官方知识库"`；
      - 同学分享 → `source = "某某 的库"`。
    库被收回公开 / 删掉之后就从这里消失 —— 收藏不是"永久快照"，
    它只是指向那个库的一个标记；库没了，收藏一条点开 404 的卡片没有意义。
    """
    favs = await asyncio.to_thread(kb_favs.mine, uid)
    if not favs:
        return {"kbs": []}
    public = await asyncio.to_thread(pipelines.list_public)
    by_id = {k["kb_id"]: k for k in public}
    post_counts = await asyncio.to_thread(kb_board.counts_by_kb)
    view_counts = await asyncio.to_thread(kb_views.counts)
    fav_counts = await asyncio.to_thread(kb_favs.counts)

    out = []
    for f in favs:
        k = by_id.get(f["kb_id"])
        if not k:
            continue
        official = bool(k.get("official"))
        owner_name = "官方" if official else await asyncio.to_thread(
            _owner_display_name, k["owner"]
        )
        out.append(
            {
                "kb_id": k["kb_id"],
                "name": k["name"],
                "desc": k.get("desc") or "",
                "official": official,
                "official_owner": official,
                # 来源与作者是两件事：来源说明它从哪来，作者说明谁做的。
                # 官方库没有"某位同学"这种作者，所以两者都写"官方知识库"。
                "source": "官方知识库" if official else f"{owner_name} 的库",
                "owner_name": owner_name,
                "access": "download" if official else _access_of_from(k),
                "files": k.get("files") or 0,
                "chunks": k.get("chunks") or 0,
                "at": int(k.get("at") or 0),
                "views": view_counts.get(k["kb_id"], 0),
                "favs": fav_counts.get(k["kb_id"], 0),
                "posts": post_counts.get(k["kb_id"], 0),
                "favorited": True,
                "favorited_at": f["at"],
            }
        )
    return {"kbs": out}


def _access_of_from(k: dict) -> str:
    """广场条目里的 access 已经是归一化过的（pipelines.list_public 里做的）。"""
    return "download" if k.get("access") == "download" else "preview"


@router.post("/public/{kb_id}/fork")
async def fork_public_kb(
    kb_id: str, req: ForkRequest, uid: str = Depends(get_current_user)
) -> dict:
    """把这个公开库复制成**我的**知识库，之后可以往里加自己的资料。

    用户原话是"除了用官方知识库，还可以往官方知识库加自己的资料，形成自己的库"。
    实现上不往官方库里写（那会改到所有人看到的东西），而是复制一份到自己名下：
    原件 + 预览件 + 向量都复制，之后这个副本完全属于调用者。

    同名复用：已经复制过同一个名字 → 更新那个副本，不新建第二个
    （`pipelines.create_kb` 的语义），别人收藏的卡片也还指向同一个 kb_id。
    """
    owner, _meta = await asyncio.to_thread(_public_detail_or_404, kb_id)
    # 复制要重新算一遍向量，按"嵌入"计费，防止有人一键把几十个库复制成自己的
    await asyncio.to_thread(quota.guard, uid, "embed")
    if owner == uid:
        # 自己的库不用复制，直接说清楚（否则用户会看到"复制成功"却多了一个自己）
        raise HTTPException(status_code=400, detail="这是你自己的库，不需要复制")
    try:
        out = await asyncio.to_thread(pipelines.fork_public, uid, kb_id, req.name)
    except pipelines.NotOwned:
        raise HTTPException(status_code=404, detail="知识库不存在或没有公开") from None
    await asyncio.to_thread(quota.consume, uid, "embed")
    return out


# ============ 公开库详情与原文（官方库 + 用户主动公开的库）============
#
# 2026-11-13 之前只有官方库能列清单：同学分享的库"只能检索，看不到文件"。
# 用户后来要求"同学分享的库也能预览、能下载，由库主自己定仅观看还是可下载"，
# 于是这三条路（详情 / 原文 / 正文）统一改走 `_public_detail_or_404`：
#   - **能看清单**：所有已公开的库（官方 + 同学公开）；
#   - **能不能下载**：官方库始终可以；用户库看 `access`（preview/download）；
#   - **上传/删除/改归属**：仍然只认库主（`_must_own`），公开与此无关。
# 官方标记本身仍然只有导入脚本能写；用户库即使公开也拿不到"官方"。
# 所以 `_official_kb_or_404` 保留给"必须确认是官方"的场合（如统计官方资料用量），
# 上面三条路不再用它。

# 浏览器能直接内嵌显示的：PDF 用内置阅读器，图片直接 <img>
_INLINE_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif")
# 需要后端先抽出文字再给前端渲染的（与 load_document 支持的文本类格式一致）
_TEXT_EXT = (".txt", ".md", ".docx", ".csv", ".jsonl", ".json")

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jsonl": "text/plain; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _official_kb_or_404(kb_id: str) -> str:
    """确认这是官方公开库，返回它真正的 owner；否则 404。

    ★ 404 而不是 403：和 `_must_own` 一个道理，403 等于承认"这个 kb_id 是真的"。
    详情/原文/正文三条路已经改走 `_public_detail_or_404`（同学公开的库也能看），
    这个函数留给"确实要区分官方"的调用方。
    """
    owner = pipelines.owner_of(kb_id)
    if not owner or not pipelines.is_official(kb_id):
        raise HTTPException(status_code=404, detail="官方资料库不存在")
    return owner


def _official_file_record(owner: str, kb_id: str, name: str) -> dict:
    """在公开库的文件清单里按展示名找一条记录；找不到 404。

    这张清单同时是**白名单**：只有登记过的名字才允许读盘。
    虽然 source_path 是哈希拼出来的、本身就穿越不出去，但"能读的文件
    = 库里登记的文件"这条边界应该由清单来定，而不是依赖路径计算的副作用。

    除了资料原名，也认记录里的 `preview`（Word 转出来的原版 PDF）——
    它是给人看的预览载体，不在清单里单独列一行，但必须能读。
    返回的记录带 `_matched`：真正要读盘的那个名字。
    """
    for rec in pipelines.list_files_view(
        owner, kb_id, pipelines.get_or_create(owner, kb_id, allow_public=True)
    ):
        for candidate in (rec.get("name"), rec.get("preview")):
            if candidate and candidate == name:
                out = dict(rec)
                out["_matched"] = candidate
                return out
    raise HTTPException(status_code=404, detail=f"资料不存在：{name}")


def _public_detail_or_404(kb_id: str) -> tuple[str, dict]:
    """这个库能不能被别人打开看资料：官方库，或用户主动公开的库。

    返回 `(owner, meta)`。私有库一律 404 —— 与 `_must_own` 同一个道理：
    403 等于告诉对方"这个 kb_id 是真的，只是你看不到"。

    ★ 2026-11-13 起用户分享的库也走这条路（用户要求"同学也可以自定义
      仅观看还是可以下载"）。能不能看清单不再由 official 独占；
      "能不能下载"由库主的 `access` 决定，官方库始终可下载。
    """
    owner = pipelines.owner_of(kb_id)
    if not owner or not pipelines.is_public(kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在或没有公开")
    return owner, pipelines.public_meta(kb_id)


def _access_of(meta: dict) -> str:
    """老数据没有 access 字段 → 按最保守的"仅观看"处理。"""
    return "download" if meta.get("access") == "download" else "preview"


@router.get("/public/{kb_id}")
async def get_official_kb(kb_id: str, uid: str = Depends(get_current_user)) -> dict:
    """公开库详情：简介 + 文件清单（名称、大小、能否预览、能否下载）。

    文件大小在这里现算（os.path.getsize）：清单本身不存大小也能算，
    但存了会因为"文件被替换/迁移"而对不上，不如以磁盘为准。

    每次打开都记一次浏览（同一人只记一次，见 core/kb_views.py）：
    "哪个库最多人用"是用户明确要的指标，放在详情页而不是广场列表，
    是因为广场只是扫一眼卡片，点进去才算真的用了。
    """
    owner, meta = await asyncio.to_thread(_public_detail_or_404, kb_id)
    official = bool(meta.get("official"))
    access = "download" if official else _access_of(meta)

    def _read() -> dict:
        pipe = pipelines.get_or_create(owner, kb_id, allow_public=True)
        files = []
        for rec in pipelines.list_files_view(owner, kb_id, pipe):
            name = rec.get("name") or ""
            ext = os.path.splitext(name)[1].lower()
            try:
                size = os.path.getsize(pipelines.source_path(kb_id, name))
            except OSError:
                size = 0
            files.append(
                {
                    "name": name,
                    "chunks": rec.get("chunks") or 0,
                    "added_at": rec.get("added_at"),
                    "size": size,
                    "ext": ext.lstrip("."),
                    # Word 的原版 PDF：有它就用浏览器阅读器看，排版与公式一字不差；
                    # 没有才退回抽正文（正文里公式是〔公式〕占位）
                    "preview_file": rec.get("preview") or "",
                    # 能不能在线看：PDF/图片内嵌，其余抽文字
                    "preview": bool(rec.get("preview")) or ext in _INLINE_EXT or ext in _TEXT_EXT,
                    # 有原件**且**库主允许下载，才给下载按钮。
                    # 仅观看的库，这里直接 false —— 前端不用自己再判 access
                    "download": size > 0 and access == "download",
                }
            )
        return {
            "kb_id": kb_id,
            "name": pipelines.name_of(kb_id) or kb_id,
            "chunks": pipe.count(),
            "files": files,
        }

    data = await asyncio.to_thread(_read)
    data["desc"] = meta.get("desc") or ""
    data["official"] = official
    data["access"] = access
    data["owner_name"] = (
        "官方" if official else await asyncio.to_thread(_owner_display_name, owner)
    )
    # 上传/分享日期：官方库是导入上线那天，用户库是第一次公开那天。
    # 用户要求"每个库都写上上传日期"，卡片和详情都用这一个字段。
    data["uploads_at"] = int(meta.get("at") or 0)
    # 记浏览放在最后：前面任何一步抛错（库被并发删掉）都不该算一次"看过"
    await asyncio.to_thread(kb_views.touch, kb_id, uid)
    data["views"] = await asyncio.to_thread(kb_views.count, kb_id)
    data["favs"] = await asyncio.to_thread(kb_favs.count, kb_id)
    data["favorited"] = await asyncio.to_thread(kb_favs.has, kb_id, uid)
    data["mine"] = owner == uid
    return data


@router.get("/public/{kb_id}/file")
async def get_official_file(
    kb_id: str,
    name: str,
    inline: int = 0,
    uid: str = Depends(get_current_user),
) -> Response:
    """取公开库里的原始文件。

    inline=1 → `Content-Disposition: inline`，给 PDF/图片在页面里内嵌用；
    默认 → `attachment`，浏览器直接下载。两条路返回的是**同一份字节**，
    区别只在浏览器怎么处理，所以下载到的文件和预览看到的一定一致。

    ★ 库主设为"仅观看"时，只有 inline 这一条路可用（默认 403）：
      预览必须能读，下载不该给。注意这是**接口层**的限制 ——
      浏览器终究能把内嵌的字节存下来，它挡住的是"我们主动提供下载"，
      不是"技术上绝对取不到"。UI 上别把它说成加密。
    """
    owner, meta = await asyncio.to_thread(_public_detail_or_404, kb_id)
    official = bool(meta.get("official"))
    if not inline and not official and _access_of(meta) != "download":
        raise HTTPException(status_code=403, detail="库主设置为仅观看，这份资料不能下载")
    # 清单校验 + 取扩展名（顺带把 path 定死，后面不再接受外部输入拼路径）
    rec = await asyncio.to_thread(_official_file_record, owner, kb_id, name)
    serve = rec.get("_matched") or rec.get("name") or ""
    ext = os.path.splitext(serve)[1].lower()
    path = pipelines.source_path(kb_id, serve)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="这份资料的原始文件没有留存，只能预览正文")

    # 下载时仍用**资料原名**（学生拿到的是 .docx，不是预览用的 .pdf）
    base = os.path.basename((rec.get("name") or name).replace("\\", "/"))
    disp = "inline" if inline else "attachment"
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"),
        headers={
            # RFC 5987：中文文件名必须走 filename*，filename= 只留 ASCII 兜底
            "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(base)}",
            # 内容不会变（同名文件重导会换 kb_id 或覆盖），但仍别让中间层缓存旧稿
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/public/{kb_id}/text")
async def get_official_file_text(
    kb_id: str, name: str, uid: str = Depends(get_current_user)
) -> dict:
    """公开库资料的正文预览（Word/文本/表格 → 纯文字）。

    PDF 不走这里：浏览器自带阅读器比抽出来的文字好得多，前端直接内嵌
    `/file?inline=1`。所以本接口对 PDF 也允许调用，只是没必要。
    "仅观看"的库同样能用：这就是它的"看"。
    """
    owner, _meta = await asyncio.to_thread(_public_detail_or_404, kb_id)
    rec = await asyncio.to_thread(_official_file_record, owner, kb_id, name)

    def _read() -> dict:
        path = pipelines.source_path(kb_id, rec.get("_matched") or rec.get("name") or "")
        docs = load_document(path, source_name=rec.get("name"))
        text = "\n\n".join((d.page_content or "") for d in docs)
        return {
            "name": rec.get("name"),
            "truncated": len(text) > PREVIEW_MAX_CHARS,
            "content": text[:PREVIEW_MAX_CHARS],
        }

    try:
        return await asyncio.to_thread(_read)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="这份资料的原始文件没有留存") from None
    except ValueError as e:
        # 扫描件 / 解析失败：说清原因，别让前端只看到 500
        raise HTTPException(status_code=400, detail=str(e)) from e



# ==================== 文件级管理 ====================


@router.get("/{kb_id}/files")
async def list_files(kb_id: str, uid: str = Depends(get_current_user)):
    """列出库内资料（名称 + 片段数）。

    ★ 与 `GET /api/kb/` 共用 `pipelines.list_files_view`：这两条路以前各写一份
      兜底逻辑，都写成 `get_files(...) or pipe.list_sources()` —— 登记表非空时
      Chroma 兜底永远不执行，对话沉淀进来的内容就永远看不见。抽成一处实现，
      以后只改一个地方，不会再一条路修好、另一条路还漏。
    """
    _must_own(uid, kb_id)

    def _list() -> dict:
        # list_sources → get_or_open → peek，要读 sqlite，别在事件循环里做
        pipe = pipelines.get_or_create(uid, kb_id)
        files = pipelines.list_files_view(uid, kb_id, pipe)
        return {"kb_id": kb_id, "chunks": pipe.count(), "files": files}

    return await asyncio.to_thread(_list)


class DeleteFileRequest(BaseModel):
    name: str = Field(..., description="要删除的文件名（与文件列表一致）")


@router.delete("/{kb_id}/files")
async def delete_file(
    kb_id: str, req: DeleteFileRequest, uid: str = Depends(get_current_user)
) -> dict:
    """删除单个文件：从向量库移除它的片段 + 删掉原始文件 + 更新清单。"""
    _must_own(uid, kb_id)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    def _remove() -> tuple[int, int]:
        # 与入库 / 重建互斥：删的那一刻若有人正在重建，会把删掉的片段又写回来
        with pipelines.kb_lock(kb_id):
            pipe = pipelines.get_or_create(uid, kb_id)
            removed = pipe.delete_by_source(name)
            pipelines.remove_file_record(uid, kb_id, name)
            try:
                p = pipelines.source_path(kb_id, name)
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
            return removed, pipe.count()

    removed, chunks = await asyncio.to_thread(_remove)
    return {"ok": True, "removed": removed, "chunks": chunks}


class RebuildRequest(BaseModel):
    chunk_size: int = 500
    chunk_overlap: int = 50


# 预览正文上限：预览不是全文阅读器，30K 字符足够判断内容，也避免一次拉爆内存
PREVIEW_MAX_CHARS = 30_000


def _resolve_source(pipe, owner: str, kb_id: str, name: str) -> str:
    """把调用方给的文件名对上库里真实记录的 source。

    老库入库时用的是临时绝对路径（形如 C:\\Users\\...\\kb_up_xxxx.pdf），
    界面上展示的只有文件名，直接拿去查会 404，所以做一次末段比对。
    """
    known = set(pipelines.get_file_names(owner, kb_id))
    if name in known:
        return name
    # 落盘记录里对不上时才需要看 Chroma 元数据；取一次复用，别为同一件事扫两遍
    sources = pipe.list_sources()
    for src in sources:
        if src.get("name") == name:
            return src["name"]
    base = os.path.basename(name.replace("\\", "/"))
    if not base:
        return name
    for cand in known | {s.get("name", "") for s in sources}:
        if cand and os.path.basename(cand.replace("\\", "/")) == base:
            return cand
    return name


@router.get("/{kb_id}/file-content")
async def get_file_content(kb_id: str, name: str, uid: str = Depends(get_current_user)):
    """预览库内文件正文。

    优先读留档的原始文件（txt/md 直读，pdf 抽文字层）；
    老库没有留档时，退回从向量库把该文件的片段按顺序拼回来。
    """
    _must_own(uid, kb_id)
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    def _read() -> dict:
        # 读原始文件 / 从向量库拼片段都是同步 I/O，PDF 抽文字层尤其慢
        pipe = pipelines.get_or_create(uid, kb_id)
        # 名字可能对不上：老库把临时绝对路径写进了 source，调用方手里往往只有文件名。
        # 先按原名找，找不到再拿末段去 Chroma 里比对。
        resolved = _resolve_source(pipe, uid, kb_id, name)

        # 1) 留档的原始文件
        path = pipelines.source_path(kb_id, resolved)
        if os.path.exists(path):
            try:
                docs = load_document(path, source_name=resolved)
                text = "\n\n".join((d.page_content or "") for d in docs)
                return {
                    "name": resolved,
                    "origin": "archive",
                    "truncated": len(text) > PREVIEW_MAX_CHARS,
                    "content": text[:PREVIEW_MAX_CHARS],
                }
            except Exception:
                pass  # 留档读不出来就走向量库兜底

        # 2) 向量库兜底（老库 / 留档丢失）
        chunks = pipe.get_source_chunks(resolved)
        if not chunks:
            raise HTTPException(status_code=404, detail=f"找不到文件内容：{resolved}")
        text = "\n\n".join(c["text"] for c in chunks)
        return {
            "name": resolved,
            "origin": "chunks",
            "truncated": len(text) > PREVIEW_MAX_CHARS,
            "content": text[:PREVIEW_MAX_CHARS],
        }

    return await asyncio.to_thread(_read)


@router.post("/{kb_id}/rebuild")
async def rebuild_kb(kb_id: str, req: RebuildRequest, uid: str = Depends(get_current_user)) -> dict:
    """按新切片参数重建整库（重读留档的原始文件，会对全部片段重新 embedding）。"""
    _must_own(uid, kb_id)
    await asyncio.to_thread(quota.guard, uid, "embed")
    names = pipelines.get_file_names(uid, kb_id)
    if not names:
        raise HTTPException(status_code=400, detail="该知识库没有文件记录，无法重建")

    now = int(time.time())

    def _rebuild() -> tuple[int, list[dict]]:
        with pipelines.kb_lock(kb_id):
            pipe = pipelines.get_or_create(uid, kb_id)
            total = 0
            done: list[dict] = []
            first = True
            for name in names:
                path = pipelines.source_path(kb_id, name)
                if not os.path.exists(path):
                    continue  # 原始文件已丢失的跳过，不中断整个重建
                n = pipe.build_index(
                    req.chunk_size,
                    req.chunk_overlap,
                    [path],
                    mode=("rebuild" if first else "append"),
                    source_names=[name],
                )
                first = False
                total += n
                done.append({"name": name, "chunks": n, "added_at": now})
            return total, done

    try:
        total, done = await asyncio.to_thread(_rebuild)
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e)) from e

    if not done:
        raise HTTPException(status_code=400, detail="原始文件已全部丢失，请重新上传文档")
    pipelines.retain_file_records(uid, kb_id, {d["name"] for d in done})
    pipelines.add_file_records(uid, kb_id, done)
    if total:
        await asyncio.to_thread(quota.bump_usage, uid, "embed", total)
    return {"kb_id": kb_id, "chunks": total, "files": done}


# ==================== 联网导入 ====================


class ImportSearchRequest(BaseModel):
    keyword: str = Field(..., description="联网搜索关键词")
    max_results: int = Field(default=10, ge=1, le=20)


class ImportURLRequest(BaseModel):
    url: str
    chunk_size: int = 500
    chunk_overlap: int = 50


class ImportBatchRequest(BaseModel):
    urls: list[str]
    chunk_size: int = 500
    chunk_overlap: int = 50


@router.post("/import-search")
async def preview_search(req: ImportSearchRequest, uid: str = Depends(get_current_user)) -> dict:
    """Serper 联网搜索预览，返回候选列表供前端勾选后批量导入。"""
    # 每次预览都要花钱调搜索 API，限流是必要的；这里不占 embed 额度（还没入库）
    await asyncio.to_thread(quota.hit, uid)
    try:
        results = await asyncio.to_thread(search_preview, req.keyword, req.max_results)
        return {"keyword": req.keyword, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{kb_id}/import-url")
async def import_single_url(
    kb_id: str, req: ImportURLRequest, uid: str = Depends(get_current_user)
):
    """直接抓一个 URL 正文追加到指定知识库。"""
    _must_own(uid, kb_id)
    await asyncio.to_thread(quota.guard, uid, "embed")
    try:
        r = await asyncio.to_thread(
            import_url, uid, kb_id, req.url, req.chunk_size, req.chunk_overlap
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if r.get("chunks"):
        await asyncio.to_thread(quota.bump_usage, uid, "embed", r["chunks"])
    return r


@router.post("/{kb_id}/import-batch")
async def import_batch_urls(
    kb_id: str, req: ImportBatchRequest, uid: str = Depends(get_current_user)
) -> dict:
    """批量抓多个 URL 追加入库（搜索勾选结果的落地接口）。"""
    _must_own(uid, kb_id)
    if not req.urls:
        raise HTTPException(status_code=400, detail="URL 列表为空")
    await asyncio.to_thread(quota.guard, uid, "embed")
    try:
        results = await asyncio.to_thread(
            import_urls_batch, uid, kb_id, req.urls, req.chunk_size, req.chunk_overlap
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    got = sum(int(r.get("chunks") or 0) for r in results)
    if got:
        await asyncio.to_thread(quota.bump_usage, uid, "embed", got)
    ok_count = sum(1 for r in results if r["ok"])
    return {
        "total": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "details": results,
    }


# ==================== 对话沉淀入库（报告 v2 §4.1 P0「开通路」）====================


class ImportDialogueRequest(BaseModel):
    sid: str
    # 勾选的候选下标（按 candidates 接口返回顺序）；空 = 全部
    indexes: list[int] = Field(default_factory=list)
    # 「就存这几条消息」——用户在界面上指着一句话按下保存时用。
    #
    # ★ 与 indexes 互斥，且**不走候选判据**：判据（`extract_blocks`）只是给批量
    #   预览排个序。用户明确点了某一句，这份"我确定要存"本身就是确认，比正则
    #   更可信 —— 再拿正则去否掉它，就会出现「我指着这句点保存，你说没挑出来」
    #   这种当面打脸。
    #   user 消息原样一条入库；assistant 消息按结构切块（长回答整条进库会占满
    #   检索 top-k），所以一条回答可能产出多段。
    message_ids: list[int] = Field(default_factory=list)


def _messages_as_candidates(msgs: list[dict], ids: list[int]) -> list[dict]:
    """把「指定的消息」直接当候选。

    user 消息原样一条 = 一条知识，不切句：用户在界面上指的是这一整句消息，
    把它拆成几段存进去，溯源时反而对不上他当时指的东西。

    ★ assistant 消息走结构分块（2026-11-01）：一段 700 字的回答整条塞进向量库，
      召回一条就占满检索 top-k，精度反而下降。切成小节后每块自带标题，
      检索命中时知道它属于哪个主题。所以「指一条 AI 回答」可能产出多条候选，
      这是刻意的，界面上会告诉用户拆成了几段。
    """
    by_id = {int(m.get("id") or 0): m for m in msgs}
    out: list[dict] = []
    for mid in dict.fromkeys(int(i) for i in ids):
        m = by_id.get(mid)
        if not m:
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        if m.get("role") == "assistant":
            out.extend(dialogue_import.extract_blocks([m], roles=("assistant",)))
            continue
        out.append(
            {
                "text": text,
                # 走这条路的都是"用户自己讲的事实/规定"，没有分类器参与，统一按事实标
                "kind": "事实",
                "refs": [mid],
                "role": "user",
                "ts": int(m.get("created_at") or 0),
            }
        )
    return out


def _load_dialogue(uid: str, sid: str) -> tuple[list[dict], str]:
    """读会话消息与标题；不是自己的会话统一 404（不泄露存在性）。"""
    msgs = store.get_messages(uid, sid, limit_turns=None, include_system=False)
    conv = store.get_conversation(uid, sid)
    if not conv or not msgs:
        raise HTTPException(status_code=404, detail="会话不存在")
    return msgs, conv.get("title") or "未命名会话"


@router.get("/dialogue-candidates/{sid}")
async def dialogue_candidates(sid: str, uid: str = Depends(get_current_user)) -> dict:
    """预览：这个会话里有哪些知识候选（只列清单，不入库）。

    两种说话人两套判据（详见 `kb/dialogue_import.py::extract_blocks`）：
    用户的话按句 + 七类信号；助手的回答按 markdown 结构切块，只问"是不是整理过的内容"。
    前端按候选自带的 `role` 分红绿两组 —— 用户说的是一手事实，助手说的是模型产出，
    以后检索命中时这个区别要能看见。

    报告 §4.1 的设计红线：**不做自动入库**——寒暄、试错、错误中间态
    一旦自动写入就不可逆污染检索，必须人工确认 + 可预览 + 可勾选。
    """
    msgs, title = _load_dialogue(uid, sid)
    cands = await asyncio.to_thread(dialogue_import.extract_blocks, msgs)
    return {
        "sid": sid,
        "conv_title": title,
        "total": len(cands),
        "candidates": dialogue_import.rank_for_preview(cands),
    }


@router.post("/{kb_id}/import-dialogue")
async def import_dialogue(
    kb_id: str, req: ImportDialogueRequest, uid: str = Depends(get_current_user)
) -> dict:
    """把勾选的对话知识句沉淀进知识库（人工确认制）。

    防伪造：候选一律由后端从会话消息**重新提取**，前端只传下标——
    想入库的内容必须真的在这个会话里，不能凭空捏造一条塞进来。
    入库成功后回写压缩段的 staged 标记：已备份的段才允许被折叠释放
    （报告 §4.4"折叠 = 已备份后的释放"）。
    """
    _must_own(uid, kb_id)
    await asyncio.to_thread(quota.guard, uid, "embed")
    msgs, title = _load_dialogue(uid, req.sid)

    if req.message_ids:
        # 指着某句话/某条回答存：不经过准入判据，但要真的属于这段会话。
        # 助手的长回答在这里会切成多个小节，所以 picked 的条数可能多于 message_ids。
        picked = _messages_as_candidates(msgs, req.message_ids)
        if not picked:
            raise HTTPException(
                status_code=400,
                detail="这条消息没有可入库的内容 —— 短句、寒暄和追问不算资料",
            )
        # 计数口径跟着切块走：一条回答切 4 段，就是 4 个单元
        requested = len(picked)
    else:
        raw = await asyncio.to_thread(dialogue_import.extract_blocks, msgs)
        if not raw:
            raise HTTPException(status_code=400, detail="这个会话没有可沉淀的知识候选")
        # ★★ 下标基准必须和预览**同一个序**。`dialogue-candidates` 回的是
        #   `rank_for_preview(raw)`，用户在界面上勾的是"第几条"；这里若按 raw 取，
        #   勾「一、基础」会存成「三、题型技巧」，而且两端各自都"没错" ——
        #   只有把预览顺序和入库顺序对着看才发现（2026-11-05 审出的 ★★★）。
        #   助手块在 rank_for_preview 里保持原序且整体后置，所以对齐后依然稳定。
        cands = dialogue_import.rank_for_preview(raw)
        idx = list(dict.fromkeys(req.indexes)) if req.indexes else list(range(len(cands)))
        bad = [i for i in idx if i < 0 or i >= len(cands)]
        if bad:
            raise HTTPException(status_code=400, detail=f"候选下标越界：{bad[:5]}")
        picked = [c for i in idx if (c := cands[i]).get("text", "").strip()]
        if not picked:
            raise HTTPException(status_code=400, detail="没有勾选任何有效候选")
        requested = len(idx)

    # 入库统一走服务层（板块 D 的 §8.1）：手工 / 自动 / 确认建议三条路
    # 共用一份实现。11-01 那次"存进去了但看不见"就是因为这段逻辑在 API 里
    # 内联了一份、worker 里另写了一份，只改了一处。
    try:
        res = await asyncio.to_thread(
            sediment.write_picked, uid, req.sid, kb_id, picked, source="manual", title=title
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e)) from e

    # skipped 的口径要含"勾了但没内容"的那些（例如勾中的是空行/纯符号），
    # 所以是"选中数 - 真正写进去的"（向量库去重的那部分在 write_picked 里另算）
    return {
        "imported": int(res.get("imported") or 0),
        "skipped": (requested - len(picked)) + int(res.get("skipped") or 0),
        "staged_segments": int(res.get("staged_segments") or 0),
        "kb_id": kb_id,
        "sid": req.sid,
        # 批次 id：撤销与审计的抓手，同时让前端能判断"这批是不是之前存过"
        "batch_id": res.get("batch_id") or "",
        "duplicate": bool(res.get("duplicate")),
    }


async def _close_download(client, resp) -> None:
    """关掉下载用的流式响应与客户端；任一为 None 都安全，异常也不外抛。"""
    if resp is not None:
        try:
            await resp.aclose()
        except Exception:
            pass
    try:
        await client.aclose()
    except Exception:
        pass


@router.get("/download")
async def download_remote(url: str, uid: str = Depends(get_current_user)) -> Response:
    """把远程文档文件代理下载到浏览器（作为附件保存到本机）。

    流式转发：旧实现 `Response(content=resp.content)` 会把整个文件先读进内存再吐给
    浏览器，一个 ISO 级文件就能把进程打爆。这里改成边收边发（256KB 一块），
    并加 200MB 上限（优先看 Content-Length，缺失时在流式过程中兜底拦截）。
    """
    from urllib.parse import quote, unquote, urljoin

    import httpx

    await asyncio.to_thread(quota.hit, uid)

    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="仅支持 http/https 链接")
    try:
        # 服务端代理下载 = 让浏览器借服务器的网络位置取东西，必须挡 SSRF
        assert_safe_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Agent/1.0",
    }
    # 不用 async with：流式响应要在函数返回后才消费，客户端必须活到生成器结束
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=False)
    resp = None
    try:
        # follow_redirects=False：跳一次就要重新校验一次，否则公网链接
        # 302 到 169.254.169.254 就能绕开上面那道检查
        req = client.build_request("GET", url, headers=headers)
        resp = await client.send(req, stream=True)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            await resp.aclose()
            if loc:
                nxt = urljoin(url, loc)
                try:
                    assert_safe_url(nxt)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
                req = client.build_request("GET", nxt, headers=headers)
                resp = await client.send(req, stream=True)
        resp.raise_for_status()
    except HTTPException:
        await _close_download(client, resp)
        raise
    except Exception:
        await _close_download(client, resp)
        raise HTTPException(
            status_code=500, detail="下载失败：无法访问该链接或网络异常。"
        ) from None

    # 先看 Content-Length，超限直接拒（能拿到时是最干净的一刀）
    cl = resp.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _DOWNLOAD_MAX_MB * 1024 * 1024:
        await _close_download(client, resp)
        raise HTTPException(
            status_code=413, detail=f"文件超过 {_DOWNLOAD_MAX_MB}MB 上限，已拒绝下载"
        )

    # 文件名：优先 Content-Disposition，其次 URL 路径
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    filename = unquote(m.group(1)) if m else ""
    if not filename:
        filename = os.path.basename(url.split("?")[0].split("#")[0]) or "download"
    content_type = (
        resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    )

    async def _body():
        total = 0
        try:
            async for chunk in resp.aiter_bytes(chunk_size=_DOWNLOAD_CHUNK):
                total += len(chunk)
                # 兜底：Content-Length 缺失或说谎时，在流式中途拦截（响应头已发，
                # 只能中断连接，浏览器会看到下载失败，好过悄悄给一个截断的文件）
                if total > _DOWNLOAD_MAX_MB * 1024 * 1024:
                    raise ValueError(f"文件超过 {_DOWNLOAD_MAX_MB}MB 上限，下载已中止")
                yield chunk
        finally:
            await _close_download(client, resp)

    return StreamingResponse(
        _body(),
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# ==================== 公开库留言板（论坛式留言）====================
#
# 覆盖两类库：官方课程库 + 用户自己公开的库。两者在"能不能留言"上完全一样，
# 区别只在"能不能看文件清单"（那是另一件事，见上面的 official 段）。
#
# 只收文字，没有图片、没有附件。表情就是普通字符（前端插入到正文里），
# 所以这里不需要单独的表情字段 —— 多一个字段就多一处校验、多一种"这条
# 到底算不算空"的边界，而它带来的能力（表情和文字分开存）根本用不到。


class BoardPostIn(BaseModel):
    body: str = Field("", description="留言正文，纯文字，最多 300 字")
    reply_to: int | None = Field(
        None, description="要回复的留言 id；不带就是新开一楼（顶层）"
    )


def _public_kb_or_404(kb_id: str) -> str:
    """确认这是**已公开**的库，返回 owner；否则 404。

    用户的私有库打到这里也是 404，与 `_must_own` 同一个理由：
    403 等于告诉对方"这个 kb_id 是真的，只是你看不到"。
    库主把库收回公开后，留言接口立刻跟着 404 —— 留言不会跟着库泄露出去。
    """
    owner = pipelines.owner_of(kb_id)
    if not owner or not pipelines.is_public(kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在或没有公开")
    return owner


def _admin_sets() -> tuple[set[str], set[str]]:
    """管理员的 (uid 集合, 小写用户名集合)。

    为什么不逐条调 `quota._is_admin`：它对不在 ADMIN_UIDS 里的人要**反查用户名**
    （一次查询）。一页 30 条留言就是 30 次查询，而 `_board_authors` 手里
    本来就有 username —— 拿现成的比一对集合就行，零查询。
    判定顺序与 `_is_admin` 保持一致：uid 优先，用户名兜底。
    """
    uids = {u for u in (getattr(config, "ADMIN_UIDS", None) or ()) if u}
    names = {
        (n or "").strip().lower()
        for n in (getattr(config, "ADMIN_USERNAMES", None) or ())
        if (n or "").strip()
    }
    return uids, names


def _board_authors(posts: list[dict]) -> dict[str, dict]:
    """一次查出这页留言的作者名、头像、是否管理员。

    删号的人查不到 → 显示「已注销用户」，而不是空白：一条没有署名的留言
    看起来像系统故障，写清楚"这个人注销了"反而是有用的信息。
    """
    users = account.get_users([p["user_id"] for p in posts])
    admin_uids, admin_names = _admin_sets()
    out: dict[str, dict] = {}
    for p in posts:
        u = users.get(p["user_id"]) or {}
        name = (u.get("nickname") or "").strip() or (u.get("username") or "").strip()
        uname = (u.get("username") or "").strip().lower()
        admin = p["user_id"] in admin_uids or (bool(uname) and uname in admin_names)
        out[p["user_id"]] = {
            "name": name or "已注销用户",
            "avatar": u.get("avatar") or "",
            # 查不到用户时一律 False：一个已经注销的号不该还顶着金标说话
            "admin": bool(admin and u),
        }
    return out


def _post_view(
    post: dict,
    authors: dict[str, dict],
    names: dict[int, str],
    uid: str,
    kb_owner: str,
    admin: bool,
) -> dict:
    a = authors.get(post["user_id"]) or {}
    rt = int(post.get("reply_to_id") or 0)
    root = int(post.get("root_id") or 0)
    return {
        "id": post["id"],
        "body": post["body"],
        "at": post["at"],
        "author": {
            "name": a.get("name") or "已注销用户",
            "avatar": a.get("avatar") or "",
            "admin": bool(a.get("admin")),
        },
        "mine": post["user_id"] == uid,
        # 作者能删自己的，库主能删自己库里的，管理员都能删。
        # 前端据此决定要不要显示那个「删除」，但真正的判定在 DELETE 里再走一遍。
        "can_delete": post["user_id"] == uid or kb_owner == uid or admin,
        # 只在"回复的是楼里某条回复"时点名：直接回复楼主时，位置本身已经
        # 说明对象了，再加一行「回复 @楼主」只是噪音。
        "reply_to": (
            {"id": rt, "name": names.get(rt) or "某条留言"} if rt and rt != root else None
        ),
    }


def _thread_view(
    root: dict,
    kids: list[dict],
    authors: dict[str, dict],
    uid: str,
    kb_owner: str,
    admin: bool,
) -> dict:
    """一栋楼的完整视图：顶层帖 + 它的回复。

    `names` 用来把 reply_to_id 翻成名字。回复只可能指向同一栋楼里的帖，
    所以用这一批就够了，不用再查库。
    """
    everyone = [root, *kids]
    names = {p["id"]: (authors.get(p["user_id"]) or {}).get("name") or "已注销用户" for p in everyone}
    view = _post_view(root, authors, names, uid, kb_owner, admin)
    view["replies"] = [
        _post_view(p, authors, names, uid, kb_owner, admin) for p in kids
    ]
    return view


@router.get("/public/{kb_id}/board")
async def get_board(
    kb_id: str,
    limit: int = Query(kb_board.PAGE, ge=1, le=100),
    before: int | None = Query(None, ge=1),
    uid: str = Depends(get_current_user),
) -> dict:
    """一页顶层留言（各自带回复），最新的楼在前。`before` 传上一页最后一栋楼的 id。"""
    owner = _public_kb_or_404(kb_id)

    def _read() -> tuple[list[dict], tuple[int, int]]:
        return (
            kb_board.list_threads(kb_id, limit=limit, before=before),
            kb_board.stats(kb_id),
        )

    threads, (total, tops) = await asyncio.to_thread(_read)
    # 作者信息按"这页所有帖（含回复）"一次查齐，不受回复层数影响
    flat = [p for t in threads for p in [t, *t["replies"]]]
    authors = await asyncio.to_thread(_board_authors, flat) if flat else {}
    admin = await asyncio.to_thread(quota._is_admin, uid)  # noqa: SLF001
    meta = await asyncio.to_thread(pipelines.public_meta, kb_id)
    return {
        "kb_id": kb_id,
        "name": await asyncio.to_thread(pipelines.name_of, kb_id) or kb_id,
        "desc": meta.get("desc") or "",
        "official": await asyncio.to_thread(pipelines.is_official, kb_id),
        "owner_name": "官方" if meta.get("official") else "",
        "total": total,
        "stickers": list(kb_board.STICKERS),
        "posts": [
            _thread_view(t, t["replies"], authors, uid, owner, admin) for t in threads
        ],
        # 用**顶层帖数**判断，不是总条数：一栋楼里聊了 20 句时总数是 21，
        # 但这一页只带回 1 栋楼，用总数会让「加载更早」永远亮着。
        "has_more": len(threads) >= limit and tops > len(threads),
    }


@router.post("/public/{kb_id}/board")
async def add_board_post(
    kb_id: str, req: BoardPostIn, uid: str = Depends(get_current_user)
) -> dict:
    """发一条留言，或者回复某一条（`reply_to`）。纯文字，最多 300 字。

    返回里带整栋楼的最新样子：前端拿它直接替换那一楼，不用重拉整页
    —— 重拉会把用户正在看的位置顶走，而且多一次往返。
    """
    owner = _public_kb_or_404(kb_id)
    text = (req.body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="写点什么再发吧")
    if len(text) > kb_board.MAX_LEN:
        raise HTTPException(
            status_code=400, detail=f"留言最多 {kb_board.MAX_LEN} 字，现在 {len(text)} 字"
        )
    try:
        post = await asyncio.to_thread(kb_board.add_post, kb_id, uid, text, req.reply_to)
    except kb_board.TooFast as e:
        detail = f"发得太快了，{e.wait} 秒后再试" if e.wait else "这个库一小时内的留言有点多，歇一会儿再来"
        raise HTTPException(status_code=429, detail=detail) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    root_id = int(post["root_id"] or post["id"])

    def _build() -> tuple[dict, list[dict]]:
        root = kb_board.get_post(root_id) or post
        return root, kb_board.replies_of(root_id)

    root, kids = await asyncio.to_thread(_build)
    authors = await asyncio.to_thread(_board_authors, [root, *kids])
    admin = await asyncio.to_thread(quota._is_admin, uid)  # noqa: SLF001
    thread = _thread_view(root, kids, authors, uid, owner, admin)
    new_id = post["id"]
    mine = next((p for p in [thread, *thread.get("replies", [])] if p["id"] == new_id), thread)
    return {"post": mine, "thread": thread}


@router.delete("/public/{kb_id}/board/{post_id}")
async def delete_board_post(
    kb_id: str, post_id: int, uid: str = Depends(get_current_user)
) -> dict:
    """删一条留言（软删）。作者本人 / 库主 / 管理员。删顶层帖会连带它的回复。"""
    owner = _public_kb_or_404(kb_id)
    post = await asyncio.to_thread(kb_board.get_post, post_id)
    # 路径上的库必须和帖子的库是同一个：否则拿 A 库的路径去删 B 库的帖，
    # 权限判断会按 A 库的库主身份放行。
    if not post or post["kb_id"] != kb_id or post.get("deleted_at"):
        raise HTTPException(status_code=404, detail="这条留言不存在")
    admin = await asyncio.to_thread(quota._is_admin, uid)  # noqa: SLF001
    if not (post["user_id"] == uid or owner == uid or admin):
        raise HTTPException(status_code=403, detail="只能删除自己的留言")
    # 删楼主会连回复一起删，所以这里返回条数而不是布尔：前端要按实际
    # 消失的条数减计数，否则「N 条」会越用越偏。
    removed = await asyncio.to_thread(kb_board.delete_post, post_id, uid, kb_id)
    if not removed:
        raise HTTPException(status_code=404, detail="这条留言不存在")
    return {"ok": True, "id": post_id, "removed": removed, "root_id": post.get("root_id") or 0}
