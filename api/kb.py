# -*- coding: utf-8 -*-
"""知识库管理路由。

路由清单：
  POST   /api/kb/{kb_id}/upload       上传文件 + 构建索引
  POST   /api/kb/import-search        联网搜索预览（返回候选列表供勾选）
  POST   /api/kb/{kb_id}/import-url   直接抓 URL 入库
  POST   /api/kb/{kb_id}/import-batch 批量抓 URL 入库（搜索勾选结果）
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

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.schemas import KBQueryResponse
from core import quota, upload
from core.auth import get_current_user
from core.netsafe import assert_safe_url
from kb import pipelines
from kb.importer import import_url, import_urls_batch, search_preview
from kb.rag_core import describe_api_error, load_document

# 默认要登录：知识库是用户资产，列举/下载/删除都必须先验身份
router = APIRouter(dependencies=[Depends(get_current_user)])

MAX_UPLOAD_MB = 100


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
):
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
                pipelines.add_file_records(
                    uid, kb_id, [{"name": name, "chunks": n, "added_at": now, "origin": "upload"}]
                )
            if mode == "rebuild":
                # 整库重建：本次没上传的旧记录要一并清掉
                pipelines.retain_file_records(uid, kb_id, {name for _, name in saved})
            return total, pipelines.get_file_names(uid, kb_id)

    try:
        total, names = await asyncio.to_thread(_build)
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e))
    # 按真实切片数记账：入口那道 ensure 只知道"还没超"，不知道这一次切了多少
    if total:
        await asyncio.to_thread(quota.bump_usage, uid, "embed", total)
    return {"kb_id": kb_id, "chunks": total, "files": names}


# ==================== 查询 ====================

@router.post("/{kb_id}/query", response_model=KBQueryResponse)
async def query_kb(kb_id: str, question: str, top_k: int = 3, uid: str = Depends(get_current_user)):
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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=describe_api_error(e))


# ==================== 列表 / 状态 / 删除 ====================

@router.get("/")
async def list_kbs(uid: str = Depends(get_current_user)):
    """列出**当前用户**的知识库。别人的库（含改造前的无主库）不出现。"""
    # 扫磁盘 + 逐个打开 collection，全是同步 I/O，不能留在事件循环里
    return {"kbs": await asyncio.to_thread(pipelines.list_all, uid)}


class CreateKbRequest(BaseModel):
    name: str = Field(..., description="知识库名称（支持中文）")


@router.post("/create")
async def create_kb(req: CreateKbRequest, uid: str = Depends(get_current_user)):
    """登记知识库（名称支持中文），返回内部安全 kb_id 与显示名。

    kb_id 里混进了 owner，两人建同名库也各是各的。
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    kb_id, display_name = pipelines.create_kb(uid, name)
    return {"kb_id": kb_id, "name": display_name}


@router.get("/{kb_id}/")
async def get_kb_status(kb_id: str, uid: str = Depends(get_current_user)):
    """查看单个知识库状态。"""
    _must_own(uid, kb_id)
    status = await asyncio.to_thread(pipelines.get_status, uid, kb_id)
    if status is None:
        # 内存里没记录，但磁盘上可能存在
        if await asyncio.to_thread(pipelines.exists, uid, kb_id):
            return {"kb_id": kb_id, "chunks": 0, "chunk_size": None, "chunk_overlap": None, "files": []}
        raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")
    return status


@router.delete("/{kb_id}/")
async def delete_kb(kb_id: str, uid: str = Depends(get_current_user)):
    """删除知识库。"""
    _must_own(uid, kb_id)
    # 删 collection + rmtree 原始文件，都是同步 I/O
    if not await asyncio.to_thread(pipelines.delete_kb, uid, kb_id):
        # 可能磁盘上有但内存里没注册
        if await asyncio.to_thread(pipelines.exists, uid, kb_id):
            # 强制删除
            def _force_delete():
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

    def _list():
        # list_sources → get_or_open → peek，要读 sqlite，别在事件循环里做
        pipe = pipelines.get_or_create(uid, kb_id)
        records = pipelines.get_files(uid, kb_id) or pipe.list_sources()
        return {"kb_id": kb_id, "chunks": pipe.count(), "files": records}

    return await asyncio.to_thread(_list)


class DeleteFileRequest(BaseModel):
    name: str = Field(..., description="要删除的文件名（与文件列表一致）")


@router.delete("/{kb_id}/files")
async def delete_file(kb_id: str, req: DeleteFileRequest, uid: str = Depends(get_current_user)):
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
    for src in pipe.list_sources():
        if src.get("name") == name:
            return src["name"]
    base = os.path.basename(name.replace("\\", "/"))
    if not base:
        return name
    for cand in known | {s.get("name", "") for s in pipe.list_sources()}:
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
async def rebuild_kb(kb_id: str, req: RebuildRequest, uid: str = Depends(get_current_user)):
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
        raise HTTPException(status_code=500, detail=describe_api_error(e))

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
async def preview_search(req: ImportSearchRequest, uid: str = Depends(get_current_user)):
    """Serper 联网搜索预览，返回候选列表供前端勾选后批量导入。"""
    # 每次预览都要花钱调搜索 API，限流是必要的；这里不占 embed 额度（还没入库）
    await asyncio.to_thread(quota.hit, uid)
    try:
        results = await asyncio.to_thread(search_preview, req.keyword, req.max_results)
        return {"keyword": req.keyword, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{kb_id}/import-url")
async def import_single_url(kb_id: str, req: ImportURLRequest, uid: str = Depends(get_current_user)):
    """直接抓一个 URL 正文追加到指定知识库。"""
    _must_own(uid, kb_id)
    await asyncio.to_thread(quota.guard, uid, "embed")
    try:
        r = await asyncio.to_thread(import_url, uid, kb_id, req.url, req.chunk_size, req.chunk_overlap)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if r.get("chunks"):
        await asyncio.to_thread(quota.bump_usage, uid, "embed", r["chunks"])
    return r


@router.post("/{kb_id}/import-batch")
async def import_batch_urls(kb_id: str, req: ImportBatchRequest, uid: str = Depends(get_current_user)):
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
        raise HTTPException(status_code=500, detail=str(e))
    got = sum(int(r.get("chunks") or 0) for r in results)
    if got:
        await asyncio.to_thread(quota.bump_usage, uid, "embed", got)
    ok_count = sum(1 for r in results if r["ok"])
    return {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count, "details": results}


@router.get("/download")
async def download_remote(url: str, uid: str = Depends(get_current_user)):
    """把远程文档文件代理下载到浏览器（作为附件保存到本机）。"""
    import httpx

    await asyncio.to_thread(quota.hit, uid)
    from urllib.parse import quote, unquote

    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="仅支持 http/https 链接")
    try:
        # 服务端代理下载 = 让浏览器借服务器的网络位置取东西，必须挡 SSRF
        assert_safe_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Agent/1.0",
    }
    try:
        # follow_redirects=False：跳一次就要重新校验一次，否则公网链接
        # 302 到 169.254.169.254 就能绕开上面那道检查
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if loc:
                    from urllib.parse import urljoin

                    nxt = urljoin(url, loc)
                    try:
                        assert_safe_url(nxt)
                    except ValueError as e:
                        raise HTTPException(status_code=400, detail=str(e))
                    resp = await client.get(nxt, headers=headers)
            resp.raise_for_status()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="下载失败：无法访问该链接或网络异常。")

    # 文件名：优先 Content-Disposition，其次 URL 路径
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    filename = unquote(m.group(1)) if m else ""
    if not filename:
        filename = os.path.basename(url.split("?")[0].split("#")[0]) or "download"
    content_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()

    return Response(
        content=resp.content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
