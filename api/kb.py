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
  GET    /api/kb/                     列出所有知识库
  DELETE /api/kb/{kb_id}/             删除知识库
  POST   /api/kb/{kb_id}/query        同步问答
"""

import asyncio
import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from api.schemas import KBQueryResponse
from core import config, quota, upload
from core import session as store
from core.auth import get_current_user
from core.context_compress import mark_segments_staged
from core.netsafe import assert_safe_url
from kb import dialogue_import, pipelines
from kb.importer import import_url, import_urls_batch, search_preview
from kb.rag_core import describe_api_error, load_document

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
    """同步问答：返回带 [1][2] 引用角标的答案。"""
    _must_own(uid, kb_id)
    # 一次问答 = 一次检索 + 一次 LLM，落到每日问答额度里
    await asyncio.to_thread(quota.consume, uid, "ask")
    pipe = pipelines.get_or_create(uid, kb_id)
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
            return {"ok": True}
        raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")
    return {"ok": True}


# ==================== 文件级管理 ====================


@router.get("/{kb_id}/files")
async def list_files(kb_id: str, uid: str = Depends(get_current_user)):
    """列出库内文件（文件名 + 片段数）。记录缺失时用 Chroma 元数据兜底。"""
    _must_own(uid, kb_id)

    def _list() -> dict:
        # list_sources → get_or_open → peek，要读 sqlite，别在事件循环里做
        pipe = pipelines.get_or_create(uid, kb_id)
        records = pipelines.get_files(uid, kb_id) or pipe.list_sources()
        return {"kb_id": kb_id, "chunks": pipe.count(), "files": records}

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


def _load_dialogue(uid: str, sid: str) -> tuple[list[dict], str]:
    """读会话消息与标题；不是自己的会话统一 404（不泄露存在性）。"""
    msgs = store.get_messages(uid, sid, limit_turns=None, include_system=False)
    conv = store.get_conversation(uid, sid)
    if not conv or not msgs:
        raise HTTPException(status_code=404, detail="会话不存在")
    return msgs, conv.get("title") or "未命名会话"


@router.get("/dialogue-candidates/{sid}")
async def dialogue_candidates(sid: str, uid: str = Depends(get_current_user)) -> dict:
    """预览：这个会话里有哪些规则判定的知识候选（只列清单，不入库）。

    报告 §4.1 的设计红线：**不做自动入库**——寒暄、试错、错误中间态
    一旦自动写入就不可逆污染检索，必须人工确认 + 可预览 + 可勾选。
    """
    msgs, title = _load_dialogue(uid, sid)
    cands = await asyncio.to_thread(dialogue_import.extract_candidates, msgs)
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
    cands = await asyncio.to_thread(dialogue_import.extract_candidates, msgs)
    if not cands:
        raise HTTPException(status_code=400, detail="这个会话没有可沉淀的知识候选")
    idx = list(dict.fromkeys(req.indexes)) if req.indexes else list(range(len(cands)))
    bad = [i for i in idx if i < 0 or i >= len(cands)]
    if bad:
        raise HTTPException(status_code=400, detail=f"候选下标越界：{bad[:5]}")
    picked = [c for i in idx if (c := cands[i]).get("text", "").strip()]
    if not picked:
        raise HTTPException(status_code=400, detail="没有勾选任何有效候选")

    docs = dialogue_import.build_documents(picked, req.sid, title)
    pipe = pipelines.get_or_create(uid, kb_id)
    try:
        n = await asyncio.to_thread(pipe.add_chunks, docs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e)) from e
    if n:
        await asyncio.to_thread(quota.bump_usage, uid, "embed", n)

    ingested_refs = {r for c in picked for r in (c.get("refs") or [])}
    staged = await asyncio.to_thread(mark_segments_staged, uid, req.sid, ingested_refs)
    return {
        "imported": n,
        "staged_segments": staged,
        "kb_id": kb_id,
        "sid": req.sid,
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
