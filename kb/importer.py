# -*- coding: utf-8 -*-
"""联网资料导入：URL 抓取 + Serper 搜索预览 + 文件下载 + 批量入库。"""

import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from html import unescape as _unescape

import httpx
from urllib.parse import urljoin

from core.netsafe import assert_safe_url
from core.serper import search_serper
from kb import pipelines
from kb.rag_core import load_document, split_documents

_HTTP_TIMEOUT = 15
_MAX_REDIRECTS = 5
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Agent/1.0",
}

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
# 整块剔除的噪声元素：脚本样式 + 页面骨架（导航/页脚/侧栏/表单等）。
# 只收录有闭合标签的元素——void 标签（embed/input/img）进这里会把整篇文档吞掉。
_HTML_NOISE_RE = re.compile(
    r"<(script|style|noscript|template|svg|iframe|object|head|nav|header|footer"
    r"|aside|form|select|button)[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# 主内容容器：有 article/main 就只取容器内正文，否则退回去噪后的 body
_MAIN_CONTENT_RE = re.compile(r"<(article|main)\b[^>]*>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body\s*>", re.DOTALL | re.IGNORECASE)
# 块级标签：作为文本块边界，一块出一行，短块（面包屑/按钮字/残留链接）整块丢弃
_BLOCK_SPLIT_RE = re.compile(
    r"</?(?:p|h[1-6]|li|blockquote|pre|tr|table|ul|ol|dl|dt|dd|div|section"
    r"|article|main|figure|figcaption|br)\b[^>]*>",
    re.IGNORECASE,
)
_MIN_BLOCK_CHARS = 12  # 短于此的文本块视为页面装饰，不入库


def _strip_html(html: str) -> str:
    """网页正文提取：去脚本/导航/页脚等噪声，只留主内容文本块（零依赖折中）。

    旧实现只删 script/style 就把全部标签换空格——导航链接、页脚版权、cookie
    横幅全部进库，稀释 embedding 语料和 BM25 词表，检索质量被噪声拉低。
    现在做三步：
      1. 整块剔除噪声元素与 HTML 注释；
      2. 有 <article>/<main> 只取容器内正文，没有则退回 <body>（此时
         页面骨架已在上一步剔掉，剩余噪声主要靠第 3 步的长度闸兜底）；
      3. 按块级标签切文本块，丢弃不足 _MIN_BLOCK_CHARS 的短块。
    返回按块换行拼接的纯文本（切片用，换行对 RecursiveCharacterTextSplitter 无害）。
    """
    html = _HTML_COMMENT_RE.sub(" ", html)
    html = _HTML_NOISE_RE.sub(" ", html)
    m = _MAIN_CONTENT_RE.search(html)
    if m:
        html = m.group(2)
    else:
        b = _BODY_RE.search(html)
        if b:
            html = b.group(1)
    blocks = []
    for raw in _BLOCK_SPLIT_RE.split(html):
        text = _unescape(_HTML_TAG_RE.sub(" ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= _MIN_BLOCK_CHARS:
            blocks.append(text)
    return "\n".join(blocks)


# 网页正文上限：伪装成 HTML 的大文件（或异常巨页）会把整个响应体吃进内存
_MAX_HTML_BYTES = 5 * 1024 * 1024


@contextmanager
def _safe_stream(url: str, timeout: int):
    """带 SSRF 校验的流式 GET，**每一跳重定向都要重新校验**。

    为什么从 httpx.get 换成 httpx.stream：httpx.get 返回时响应体已经**全部读进
    内存**，之后 iter_bytes 只是在现成的内存块上切片——下载的 200MB 闸、网页的
    5MB 闸都成了马后炮（闸触发前内存已经吃完了）。stream 模式按需拉取，
    配合 _read_capped / 分块写盘才是真正的"超限即中止"。

    follow_redirects=False 是刻意的：交给 httpx 自动跟随时，
    一个公网 URL 只要 302 到 http://169.254.169.254/latest/meta-data/，
    入口那次校验就成了摆设。多用户之后这是别人能打进内网的口子。
    """
    target = assert_safe_url(url)
    for _ in range(_MAX_REDIRECTS + 1):
        with httpx.stream(
            "GET", target, headers=_HTTP_HEADERS, timeout=timeout, follow_redirects=False
        ) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if loc:
                    target = assert_safe_url(urljoin(target, loc))
                    continue
            yield resp
            return
    raise ValueError(f"重定向次数超过 {_MAX_REDIRECTS} 次，已放弃：{url}")


def _read_capped(resp: httpx.Response, limit: int) -> bytes:
    """流式读取响应体，超限立即中止（内存里最多只有 limit 大小的数据）。"""
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
        size += len(chunk)
        if size > limit:
            raise ValueError(f"响应体超过 {limit // 1024 // 1024}MB 上限，已中止抓取")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_html(resp: httpx.Response, raw: bytes) -> str:
    """按响应头 charset 解码；缺失或失败时用 charset_normalizer 探测（httpx 自带依赖）。"""
    content_type = resp.headers.get("content-type", "")
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].split(";")[0].strip()
        try:
            return raw.decode(charset)
        except (UnicodeDecodeError, LookupError):
            pass
    try:
        from charset_normalizer import detect
        enc = (detect(raw) or {}).get("encoding") or "utf-8"
    except ImportError:
        enc = "utf-8"
    return raw.decode(enc, errors="replace")


def fetch_url_text(url: str) -> tuple[str, str]:
    """抓 URL 正文，返回 (纯文本, 标题)。流式读取，超 5MB 直接 ValueError。"""
    with _safe_stream(url, _HTTP_TIMEOUT) as resp:
        resp.raise_for_status()
        raw = _read_capped(resp, _MAX_HTML_BYTES)
        html = _decode_html(resp, raw)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else url
    return _strip_html(html), title


def _index_into_kb(owner: str, kb_id: str, dest: str, name: str, chunk_size: int,
                   chunk_overlap: int, empty_msg: str) -> int:
    """把已落档的来源切片入库；同名来源已在库内则跳过。返回本次写入的切片数（跳过 0）。

    抓网页正文与下载文档这两条入口，后段流程完全一致（加锁 → 去重 → 加载 → 切片 →
    写向量 → 登记文件记录），只有"切片为空"时的报错文案不同，所以文案作为参数传入。
    那段文案会直接显示给用户，两条必须各自保留，不要合并成一句。

    只锁「写库」这一段：抓网页/下载那几秒不该占着锁，但 embedding + 写向量 + 改文件
    清单必须和同库的入库 / 重建 / 删除互斥。
    """
    with pipelines.kb_lock(kb_id):
        pipe = pipelines.get_or_create(owner, kb_id)
        # 去重：同名来源已入库时跳过，避免重复切片 + 重复付 embedding 费
        if pipe.has_source(name):
            return 0
        docs = load_document(dest, source_name=name)
        chunks = split_documents(docs, chunk_size, chunk_overlap)
        if not chunks:
            raise ValueError(empty_msg)
        # 空库也要能写进去：add_chunks 内部走 _ensure_vs()（没有 collection 就先建），
        # 换成 get_or_open() 会在 _vs=None 上 add_documents ⇒ AttributeError，
        # 而且失败在建 collection 之前，重试永远不可能成功。
        pipe.add_chunks(chunks)
        pipelines.add_file_records(owner, kb_id, [{
            "name": name,
            "chunks": len(chunks),
            "added_at": int(time.time()),
            "origin": "web",
        }])
        return len(chunks)


def import_url(owner: str, kb_id: str, url: str, chunk_size: int = 500, chunk_overlap: int = 50) -> dict:
    """抓 URL 正文追加到指定知识库（正文留档 + 登记文件，便于后续单条删除/重建）。

    归属在 pipelines 层校验，这里只是把 owner 透传下去。
    """
    text, title = fetch_url_text(url)
    name = _safe_name(title or url, ".txt")
    dest = pipelines.source_path(kb_id, name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(f"来源URL: {url}\n标题: {title}\n\n{text}")

    n = _index_into_kb(owner, kb_id, dest, name, chunk_size, chunk_overlap, "URL 内容切片为空")
    if n == 0:
        return {"url": url, "title": title, "chunks": 0, "file": name, "skipped": True}
    return {"url": url, "title": title, "chunks": n, "file": name}


def _safe_name(title: str, ext: str) -> str:
    """标题 → 可作为文件名的安全字符串（保留中文与扩展名）。"""
    stem = re.sub(r"[^\w一-龥.-]", "_", title)[:60].strip("_") or "web"
    if not stem.endswith(ext):
        stem += ext
    return stem


# ==================== App 搜索优化 ====================

def search_preview(keyword: str, max_results: int = 10) -> list[dict]:
    """Serper 联网搜索预览（走 core.serper 统一入口：连接复用 + 缓存 + App 查询补官网）。"""
    from core import config

    if not config.SERPER_API_KEY:
        raise RuntimeError("联网搜索需要 SERPER_API_KEY，请在 .env 中配置后重启服务。")

    data = search_serper(keyword, max_results)
    if data is None:
        raise RuntimeError("联网搜索暂时不可用，请稍后再试。")
    items = data.get("organic") or []

    return [{"title": r.get("title", "?"), "body": (r.get("snippet") or "")[:300], "href": r.get("link", "")}
            for r in items]


# ==================== 文件下载 ====================

_DOWNLOAD_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".pptx", ".xlsx",
                  ".apk", ".zip", ".exe", ".dmg", ".msi", ".iso"}

_MAX_DOWNLOAD_MB = 200  # 下载大小上限：防 OOM，超限删半成品

_CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/vnd.android.package-archive": ".apk",
}


def download_file(url: str, save_dir: str | None = None) -> dict:
    """下载文档类文件到本地，返回 {url, filename, path, ext, size}。

    save_dir 由调用方给：多用户后下载件必须落到各自目录，
    否则 A 下载的东西可能被 B 的 /download 顺手取走。
    """
    from core import config

    save_dir = save_dir or config.DOWNLOAD_DIR
    os.makedirs(save_dir, exist_ok=True)

    with _safe_stream(url, 30) as resp:
        resp.raise_for_status()

        content_type = (resp.headers.get("content-type") or "").lower().split(";")[0].strip()
        ext = os.path.splitext(url.split("?")[0].split("#")[0])[1].lower()
        if ext not in _DOWNLOAD_EXTS:
            ext = _CONTENT_TYPE_EXT.get(content_type, "")
        if not ext:
            raise ValueError("无法识别该链接的文件类型（仅支持文档 / 应用安装包等可下载文件）")

        base = os.path.basename(url.split("?")[0].split("#")[0]) or "download"
        stem = re.sub(r"[^\w一-龥.-]", "_", base)
        if os.path.splitext(stem)[1].lower() != ext:
            stem = os.path.splitext(stem)[0] + ext
        path = os.path.join(save_dir, stem)
        n = 1
        while os.path.exists(path):
            path = os.path.join(save_dir, f"{os.path.splitext(stem)[0]}_{n}{ext}")
            n += 1

        # 流式写盘：resp.content 会把整个文件读进内存（ISO 级文件直接 OOM），
        # 分块写 + 累计大小上限，超限删掉半成品再报错。
        # 注意必须走 _safe_stream 才是真流式：httpx.get 返回时 body 已全部进内存。
        size = 0
        try:
            with open(path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    size += len(chunk)
                    if size > _MAX_DOWNLOAD_MB * 1024 * 1024:
                        raise ValueError(f"文件超过 {_MAX_DOWNLOAD_MB}MB 上限，下载已中止")
                    f.write(chunk)
        except ValueError:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
    return {"url": url, "filename": os.path.basename(path), "path": path,
            "ext": ext, "size": size}


def download_and_index(owner: str, kb_id: str, url: str, chunk_size: int = 500, chunk_overlap: int = 50) -> dict:
    """下载文档类文件，若为 pdf/txt/md 则一并索引入库。"""
    from core import config

    # 下载件按用户分目录：共用目录的话，别人猜到文件名就能把你下载的东西取走
    info = download_file(url, save_dir=os.path.join(config.DOWNLOAD_DIR, owner))
    if info["ext"] not in (".txt", ".md", ".pdf"):
        info["chunks"] = 0
        return info

    # 下载件留档到知识库原始目录，之后才能单独删除或跟着重建
    dest = pipelines.source_path(kb_id, info["filename"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as out:
        out.write(Path(info["path"]).read_bytes())

    n = _index_into_kb(
        owner, kb_id, dest, info["filename"], chunk_size, chunk_overlap,
        "文档内容切片为空，无法入库",
    )
    info["chunks"] = n
    if n == 0:
        info["skipped"] = True
    return info


def import_urls_batch(owner: str, kb_id: str, urls: list[str], chunk_size: int = 500, chunk_overlap: int = 50) -> list[dict]:
    """批量抓 URL 追加入库。"""
    results = []
    for url in urls:
        try:
            r = import_url(owner, kb_id, url, chunk_size, chunk_overlap)
            results.append({"url": url, "ok": True, **r})
        except Exception as e:
            results.append({"url": url, "ok": False, "error": str(e)})
    return results
