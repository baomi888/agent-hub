"""知识库文档管理的服务层（给工具 / 后台这类非 HTTP 调用方用）。

为什么不让调用方直接碰 Chroma：`api/kb.py` 里删一个文件其实要做四件事
（向量库删片段、登记表删记录、删原始文件、和重建互斥），少做一件就会留下
"界面上没了、检索还命中"或者"重建时又冒回来"的脏状态。把这四件事收在一个
函数里，工具层就不可能只做对一半。

与 HTTP 层的分工：`api/kb.py` 负责参数校验与错误码，这里只做动作、用异常
表达失败（`NotOwned` / `FileNotFoundError`），由调用方翻译成 404。
"""

from __future__ import annotations

import os

from kb import pipelines


class NotOwned(Exception):
    """不是自己的库。调用方一律翻成 404（不泄露"这个库存在"）。"""


def _must_own(owner: str, kb_id: str) -> None:
    if pipelines.owner_of(kb_id) != owner:
        raise NotOwned(kb_id)


def list_documents(owner: str, kb_id: str) -> list[dict]:
    """库里的资料清单（文件名 / 段数 / 来源）。

    与 /knowledge 页面同源（`pipelines.list_files_view`）：登记表在前，
    向量库里没登记过的 source 补在后面。用另一套口径就会重演 11-01
    "存进去了但看不见"那次事故。
    """
    _must_own(owner, kb_id)
    pipe = pipelines.get_or_create(owner, kb_id)
    return pipelines.list_files_view(owner, kb_id, pipe)


def delete_documents(owner: str, kb_id: str, names: list[str]) -> dict:
    """批量删文档。返回 `{removed, chunks, missing}`。

    `missing` 是"列了但库里没有"的名字 —— 不静默当成功，
    调用方（工具）需要知道哪几个其实没删到。
    """
    _must_own(owner, kb_id)
    want = [n.strip() for n in (names or []) if (n or "").strip()]
    if not want:
        raise ValueError("文件名不能为空")

    removed_total = 0
    missing: list[str] = []
    with pipelines.kb_lock(kb_id):
        pipe = pipelines.get_or_create(owner, kb_id)
        known = set(pipelines.get_file_names(owner, kb_id)) | {
            s.get("name", "") for s in pipe.list_sources()
        }
        for name in want:
            if name not in known:
                missing.append(name)
                continue
            removed_total += pipe.delete_by_source(name)
            pipelines.remove_file_record(owner, kb_id, name)
            try:
                p = pipelines.source_path(kb_id, name)
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
        chunks = pipe.count()
    return {"removed": removed_total, "chunks": chunks, "missing": missing}


def create_kb(owner: str, name: str) -> dict:
    """建库。名字为空直接拒绝（`create_kb` 会用空名派生出一个可用但没意义的 kb_id）。"""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("库名不能为空")
    kb_id, display = pipelines.create_kb(owner, clean)
    return {"kb_id": kb_id, "name": display}


__all__ = ["NotOwned", "create_kb", "delete_documents", "list_documents"]
