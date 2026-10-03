# -*- coding: utf-8 -*-
"""联网资料导入：URL 抓取 + Serper 搜索预览 + 文件下载 + 批量入库。"""

import os
import re
import time
from pathlib import Path

import httpx

from kb import pipelines
from kb.rag_core import load_document, split_documents

_HTTP_TIMEOUT = 15
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Agent/1.0",
}

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_HTML_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _strip_html(html: str) -> str:
    html = _HTML_SCRIPT_RE.sub("", html)
    text = _HTML_TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url_text(url: str) -> tuple[str, str]:
    """抓 URL 正文，返回 (纯文本, 标题)。"""
    resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=_HTTP_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].split(";")[0].strip()
        try:
            html = resp.content.decode(charset)
        except (UnicodeDecodeError, LookupError):
            html = resp.text
    else:
        html = resp.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else url
    return _strip_html(html), title


def import_url(kb_id: str, url: str, chunk_size: int = 500, chunk_overlap: int = 50) -> dict:
    """抓 URL 正文追加到指定知识库（正文留档 + 登记文件，便于后续单条删除/重建）。"""
    text, title = fetch_url_text(url)
    name = _safe_name(title or url, ".txt")
    dest = pipelines.source_path(kb_id, name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(f"来源URL: {url}\n标题: {title}\n\n{text}")

    # 只锁「写库」这一段：抓网页那几秒不该占着锁，
    # 但 embedding + add_documents + 改文件清单必须和同库的入库 / 重建 / 删除互斥
    with pipelines.kb_lock(kb_id):
        pipe = pipelines.get_or_create(kb_id)
        docs = load_document(dest, source_name=name)
        chunks = split_documents(docs, chunk_size, chunk_overlap)
        if not chunks:
            raise ValueError("URL 内容切片为空")
        pipe.get_or_open()
        pipe._vs.add_documents(chunks)
        pipelines.add_file_records(kb_id, [{
            "name": name,
            "chunks": len(chunks),
            "added_at": int(time.time()),
            "origin": "web",
        }])
    return {"url": url, "title": title, "chunks": len(chunks), "file": name}


def _safe_name(title: str, ext: str) -> str:
    """标题 → 可作为文件名的安全字符串（保留中文与扩展名）。"""
    stem = re.sub(r"[^\w一-龥.-]", "_", title)[:60].strip("_") or "web"
    if not stem.endswith(ext):
        stem += ext
    return stem


# ==================== App 搜索优化 ====================

_APP_SIGNALS = ("apk", "app", "android", "安卓", "ios", "软件", "应用", "客户端",
                "安装包", "官网", "官方", "下载", "安装")

_APP_STOP = ("什么", "如何", "怎么", "为什么", "教程", "论文", "文档", "文章", "新闻",
             "天气", "介绍", "意思", "区别", "对比", "评价", "推荐", "好处", "原理")

_DOWNLOAD_INTENT = ("下载", "安装", "apk", "安装包")

# 裸词里会被误判为 App 的非 App 短词（精确匹配，避免把「学习强国」这类真 App 拦掉）
_APP_NAME_BLOCK = {
    "深度学习", "机器学习", "人工智能", "神经网络", "数据科学", "大数据",
    "计算机", "编程", "前端", "后端", "算法", "数据库",
    "数学", "物理", "化学", "历史", "地理", "生物", "语文", "英语", "政治",
    "区块链", "元宇宙", "云计算", "微服务", "容器",
    "python", "java", "linux", "mysql",
}


def boost_app_query(keyword: str) -> str:
    """把 App / 软件类搜索补上「官网」，让官方下载页排到前面。

    触发条件：关键词含 App 相关信号，或是一个很短的裸词（大概率是 App 名）。
    含「什么 / 如何 / 教程…」等泛问词时不做改写，避免影响普通搜索。
    """
    kw = (keyword or "").strip()
    if not kw:
        return kw
    low = kw.lower()
    if any(s in low for s in _APP_STOP):
        return kw
    has_signal = any(s in low for s in _APP_SIGNALS)
    is_bare = len(kw) <= 6 and not any(ch.isspace() for ch in kw) \
        and not any(ch in kw for ch in "？?！!，,。.：:") \
        and low not in _APP_NAME_BLOCK
    if not (has_signal or is_bare):
        return kw
    if "官网" in kw or "官方" in kw:
        return kw
    if "下载" in kw:
        return f"{kw} 官网"
    if any(s in low for s in _DOWNLOAD_INTENT):
        return f"{kw} 官网下载"
    return f"{kw} 官网"


def search_preview(keyword: str, max_results: int = 10) -> list[dict]:
    """Serper 联网搜索预览（App 类搜索自动补「官网」直达下载页）。"""
    from core import config

    if not config.SERPER_API_KEY:
        raise RuntimeError("联网搜索需要 SERPER_API_KEY，请在 .env 中配置后重启服务。")

    try:
        q = boost_app_query(keyword)
        resp = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": q, "num": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        items = (resp.json() or {}).get("organic") or []
    except Exception:
        raise RuntimeError("联网搜索暂时不可用，请稍后再试。")

    return [{"title": r.get("title", "?"), "body": (r.get("snippet") or "")[:300], "href": r.get("link", "")}
            for r in items]


# ==================== 文件下载 ====================

_DOWNLOAD_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".pptx", ".xlsx",
                  ".apk", ".zip", ".exe", ".dmg", ".msi", ".iso"}

_CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/vnd.android.package-archive": ".apk",
}


def download_file(url: str, save_dir: str | None = None) -> dict:
    """下载文档类文件到本地，返回 {url, filename, path, ext, size}。"""
    from core import config

    save_dir = save_dir or config.DOWNLOAD_DIR
    os.makedirs(save_dir, exist_ok=True)

    resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=30, follow_redirects=True)
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

    with open(path, "wb") as f:
        f.write(resp.content)
    return {"url": url, "filename": os.path.basename(path), "path": path,
            "ext": ext, "size": len(resp.content)}


def download_and_index(kb_id: str, url: str, chunk_size: int = 500, chunk_overlap: int = 50) -> dict:
    """下载文档类文件，若为 pdf/txt/md 则一并索引入库。"""
    info = download_file(url)
    if info["ext"] not in (".txt", ".md", ".pdf"):
        info["chunks"] = 0
        return info

    # 下载件留档到知识库原始目录，之后才能单独删除或跟着重建
    dest = pipelines.source_path(kb_id, info["filename"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as out:
        out.write(Path(info["path"]).read_bytes())

    with pipelines.kb_lock(kb_id):
        docs = load_document(dest, source_name=info["filename"])
        chunks = split_documents(docs, chunk_size, chunk_overlap)
        if not chunks:
            raise ValueError("文档内容切片为空，无法入库")
        pipe = pipelines.get_or_create(kb_id)
        pipe.get_or_open()
        pipe._vs.add_documents(chunks)
        info["chunks"] = len(chunks)
        pipelines.add_file_records(kb_id, [{
            "name": info["filename"],
            "chunks": len(chunks),
            "added_at": int(time.time()),
            "origin": "web",
        }])
    return info


def import_urls_batch(kb_id: str, urls: list[str], chunk_size: int = 500, chunk_overlap: int = 50) -> list[dict]:
    """批量抓 URL 追加入库。"""
    results = []
    for url in urls:
        try:
            r = import_url(kb_id, url, chunk_size, chunk_overlap)
            results.append({"url": url, "ok": True, **r})
        except Exception as e:
            results.append({"url": url, "ok": False, "error": str(e)})
    return results
