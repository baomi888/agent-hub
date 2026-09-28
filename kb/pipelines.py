# -*- coding: utf-8 -*-
"""动态多知识库管理。

与 rag-knowledge-base-demo 固定 main/left/right 三个 collection 不同，
这里支持任意数量的知识库（每个有唯一 kb_id）。

职责：
  - 按 kb_id 创建 / 获取 / 删除 RagPipeline 实例
  - 维护每个库的文件列表状态
  - 支持遍历所有已有库（用于前端列表展示）
  - 中文名 → 安全 ASCII ID 映射（Chroma collection 名只允许 ASCII）
"""

import hashlib
import json
import os
import re

import chromadb

from core import config
from kb.rag_core import RagPipeline

# ---------- 全局注册表 ----------
# { kb_id: RagPipeline }
_PIPELINES: dict[str, RagPipeline] = {}
# { kb_id: [{"name","chunks","added_at"}, ...] }  —— 落盘到 kb_files.json，重启不丢
_KB_FILES: dict[str, list[dict]] = {}
# { safe_kb_id: 中文显示名 }
_KB_NAMES: dict[str, str] = {}

# Chroma collection 合法字符：字母数字 . _ -，首尾必须字母数字，长度 3-63
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,61}[a-zA-Z0-9]$")


def _names_path() -> str:
    """中文名映射持久化文件路径。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_names.json")


def _files_path() -> str:
    """文件清单持久化路径（存原始文件名 + 片段数，不再存临时的绝对路径）。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_files.json")


def _load_files() -> None:
    """从磁盘加载文件清单。"""
    global _KB_FILES
    try:
        with open(_files_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _KB_FILES = {
                k: [r for r in v if isinstance(r, dict) and r.get("name")]
                for k, v in data.items()
                if isinstance(v, list)
            }
        else:
            _KB_FILES = {}
    except Exception:
        _KB_FILES = {}


def _save_files() -> None:
    """持久化文件清单。"""
    try:
        os.makedirs(os.path.dirname(_files_path()), exist_ok=True)
        with open(_files_path(), "w", encoding="utf-8") as f:
            json.dump(_KB_FILES, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_names() -> None:
    """从磁盘加载中文名映射。"""
    global _KB_NAMES
    try:
        with open(_names_path(), "r", encoding="utf-8") as f:
            _KB_NAMES = json.load(f)
    except Exception:
        _KB_NAMES = {}


def _save_names() -> None:
    """持久化中文名映射。"""
    try:
        os.makedirs(os.path.dirname(_names_path()), exist_ok=True)
        with open(_names_path(), "w", encoding="utf-8") as f:
            json.dump(_KB_NAMES, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def safe_kb_id(name: str) -> str:
    """把任意名称（含中文）转为 Chroma 合法的 kb_id。

    纯 ASCII 且合法的名称直接复用；否则取名称 md5 前 12 位作为 ID。
    """
    name = (name or "").strip()
    if _SAFE_ID_RE.match(name) and ".." not in name:
        return name
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
    return h


# ==================== CRUD ====================

def create_kb(name: str) -> tuple[str, str]:
    """登记一个知识库（中文名亦可），返回 (safe_kb_id, display_name)。"""
    name = (name or "").strip()
    kb_id = safe_kb_id(name)
    _KB_NAMES[kb_id] = name
    _save_names()
    get_or_create(kb_id)  # 确保注册表有记录
    return kb_id, name


def get_or_create(kb_id: str) -> RagPipeline:
    """按 kb_id 获取 RagPipeline，不存在则新建。"""
    if kb_id not in _PIPELINES:
        _PIPELINES[kb_id] = RagPipeline(f"kb_{kb_id}")
        _KB_FILES.setdefault(kb_id, [])  # 已有落盘记录时不能覆盖成空列表
    return _PIPELINES[kb_id]


def exists(kb_id: str) -> bool:
    """磁盘上是否存在这个知识库。"""
    if kb_id not in _PIPELINES:
        return False
    return _PIPELINES[kb_id].peek() > 0


def delete_kb(kb_id: str) -> bool:
    """删除知识库（磁盘 + 内存 + 原始文件）。返回是否成功。"""
    if kb_id not in _PIPELINES:
        return False
    _PIPELINES[kb_id].delete()
    _PIPELINES.pop(kb_id, None)
    _KB_FILES.pop(kb_id, None)
    _KB_NAMES.pop(kb_id, None)
    _save_files()
    _save_names()
    # 原始文件一起清掉，否则删库后磁盘上还留着副本
    try:
        import shutil

        shutil.rmtree(source_dir(kb_id), ignore_errors=True)
    except Exception:
        pass
    return True


def list_all() -> list[dict]:
    """列出所有已知知识库的状态（内存注册表 + 磁盘扫描）。"""
    result = []
    # 先看磁盘上有哪些 collection
    try:
        client = chromadb.PersistentClient(path=config.PERSIST_DIR)
        all_collections = [c.name for c in client.list_collections()]
    except Exception:
        all_collections = []

    for col_name in all_collections:
        if not col_name.startswith("kb_"):
            continue
        kb_id = col_name[3:]  # 去掉 kb_ 前缀
        pipe = get_or_create(kb_id)
        # 确保 Chroma 实例已打开
        if pipe._vs is None:
            pipe.get_or_open()
        # 文件清单以落盘记录为准，库里真有片段但没记录时用 Chroma 元数据兜底
        files = get_files(kb_id) or pipe.list_sources()
        result.append({
            "kb_id": kb_id,
            "name": _KB_NAMES.get(kb_id, kb_id),  # 无映射时回退到 kb_id
            "chunks": pipe.count(),
            "files": files,
        })
    return result


# ==================== 原始文件存储 ====================
# 只留向量不够：换切片参数要重建索引，必须有原始文件；删单个文件也要能定位它

def source_dir(kb_id: str) -> str:
    """该知识库原始文件的存放目录。"""
    return os.path.join(config.DATA_DIR, "kb_sources", kb_id)


def ensure_source_dir(kb_id: str) -> str:
    d = source_dir(kb_id)
    os.makedirs(d, exist_ok=True)
    return d


def source_path(kb_id: str, name: str) -> str:
    """原始文件的落盘位置：md5 前缀避免中文/重名冲突，保留扩展名给 loader 用。"""
    safe = re.sub(r"[^\w.\-]", "_", name)
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return os.path.join(source_dir(kb_id), f"{digest}_{safe}")


# ==================== 文件管理 ====================

def add_file_records(kb_id: str, records: list[dict]) -> None:
    """登记文件（同名覆盖），并落盘。record: {name, chunks, added_at}"""
    get_or_create(kb_id)  # 确保注册表有记录
    cur = _KB_FILES.setdefault(kb_id, [])
    for r in records:
        cur[:] = [x for x in cur if x.get("name") != r.get("name")]
        cur.append(r)
    _save_files()


def retain_file_records(kb_id: str, names) -> None:
    """只保留给定文件名（整库重建后清理陈旧记录）。"""
    keep = set(names)
    cur = _KB_FILES.get(kb_id)
    if not cur:
        return
    cur[:] = [x for x in cur if x.get("name") in keep]
    _save_files()


def remove_file_record(kb_id: str, name: str) -> bool:
    """移除单个文件记录，返回是否真的删掉了。"""
    cur = _KB_FILES.get(kb_id)
    if not cur:
        return False
    before = len(cur)
    cur[:] = [x for x in cur if x.get("name") != name]
    if len(cur) != before:
        _save_files()
        return True
    return False


def get_files(kb_id: str) -> list[dict]:
    """返回该库的文件记录副本。

    origin 标记这条资料是怎么进库的：upload=用户上传，web=联网抓的。
    早期记录没有这个字段，统一按 upload 兜底，前端才能直接读。
    """
    out = []
    for x in _KB_FILES.get(kb_id, []):
        rec = dict(x)
        rec["origin"] = rec.get("origin") or "upload"
        out.append(rec)
    return out


def get_file_names(kb_id: str) -> list[str]:
    """该库已登记的文件名列表。"""
    return [x.get("name", "") for x in _KB_FILES.get(kb_id, [])]


# ==================== 状态查询 ====================

def get_status(kb_id: str) -> dict | None:
    """汇总单个库状态，不存在返回 None。"""
    if kb_id not in _PIPELINES:
        return None
    pipe = _PIPELINES[kb_id]
    chunks = pipe.peek()
    params = pipe._params
    return {
        "kb_id": kb_id,
        "chunks": chunks,
        "chunk_size": params[0] if params else None,
        "chunk_overlap": params[1] if params else None,
        "files": get_files(kb_id) or pipe.list_sources(),
    }


# ==================== 启动预热 ====================

def warmup() -> None:
    """启动时扫描磁盘，把已有 collection 加载进注册表。"""
    _load_names()
    _load_files()
    try:
        client = chromadb.PersistentClient(path=config.PERSIST_DIR)
        for col in client.list_collections():
            col_name = col.name
            if not col_name.startswith("kb_"):
                continue
            kb_id = col_name[3:]
            pipe = get_or_create(kb_id)
            pipe.get_or_open()
    except Exception:
        # chroma_db 目录不存在或为空时忽略
        pass
