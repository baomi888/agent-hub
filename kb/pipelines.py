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
import logging
import os
import re
import threading
import time

from core import config
from kb.rag_core import RagPipeline, chroma_client

# ---------- 全局注册表 ----------
# { kb_id: RagPipeline }
_PIPELINES: dict[str, RagPipeline] = {}
# { kb_id: [{"name","chunks","added_at"}, ...] }  —— 落盘到 kb_files.json，重启不丢
_KB_FILES: dict[str, list[dict]] = {}
# { safe_kb_id: 中文显示名 }
_KB_NAMES: dict[str, str] = {}
# { kb_id: RLock }  —— 建库 / 重建按库串行，避免两个上传同时往一个 collection 里写
_KB_LOCKS: dict[str, threading.RLock] = {}

# 保护上面四个全局容器。用 RLock：内部函数会互相调用（add_file_records → get_or_create），
# 普通 Lock 会在第二次 acquire 时把自己锁死
_REGISTRY_LOCK = threading.RLock()

# Chroma collection 合法字符：字母数字 . _ -，首尾必须字母数字，长度 3-63
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,61}[a-zA-Z0-9]$")


def _names_path() -> str:
    """中文名映射持久化文件路径。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_names.json")


def _files_path() -> str:
    """文件清单持久化路径（存原始文件名 + 片段数，不再存临时的绝对路径）。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_files.json")


# os.replace 在 Windows 上撞见"有人正开着目标文件"会抛 PermissionError
# （杀毒软件扫一下、另一个进程在读都算）。这是瞬时状态，重试几次就好。
_REPLACE_RETRIES = 6
_REPLACE_BACKOFF = 0.02  # 指数退避：0.02 0.04 0.08 0.16 0.32 ≈ 0.6s 总窗口


def _replace_with_retry(tmp: str, path: str) -> None:
    """带退避的 os.replace。

    Windows 上目标文件被任何句柄开着（杀毒扫描、编辑器、备份工具）都会 WinError 5，
    而这种情况通常是瞬时的——等一下再试比直接放弃好。
    持续被占满 0.6 秒仍失败就抛出：此时旧文件原样保留，只丢这一次改动，
    总比写坏成截断 JSON（下次启动整个清单读不出来）强。
    """
    for i in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except (PermissionError, FileNotFoundError):
            if i == _REPLACE_RETRIES - 1:
                raise
            if isinstance(path, str) and os.path.dirname(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
            time.sleep(_REPLACE_BACKOFF * (2**i))


def _atomic_write_json(path: str, data) -> None:
    """原子写 JSON：先写同目录临时文件，再 os.replace 整体换上去。

    直接 open(path, "w") 写到一半被打断（进程被杀、磁盘满）会留下截断的文件，
    下次启动整个清单就读不出来了——文件清单丢了，库里的片段还在，界面就成了空壳。

    失败一律抛出、由调用方决定怎么处理：写不进去必须让人知道，
    静默 pass 的话界面显示正常、重启后记录全没了，这种错最难查。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except Exception:
        # 半途失败别把临时文件留在那儿
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_files() -> None:
    """从磁盘加载文件清单。调用方需持有 _REGISTRY_LOCK。"""
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
    """持久化文件清单（原子写）。调用方需持有 _REGISTRY_LOCK。

    写失败不能静默：界面读的是内存，看着一切正常，重启后记录全丢。
    """
    try:
        _atomic_write_json(_files_path(), _KB_FILES)
    except Exception as e:
        logging.getLogger(__name__).warning("文件清单落盘失败（重启后记录会丢）：%s", e)


def _load_names() -> None:
    """从磁盘加载中文名映射。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_NAMES
    try:
        with open(_names_path(), "r", encoding="utf-8") as f:
            _KB_NAMES = json.load(f)
    except Exception:
        _KB_NAMES = {}


def _save_names() -> None:
    """持久化中文名映射（原子写）。调用方需持有 _REGISTRY_LOCK。"""
    try:
        _atomic_write_json(_names_path(), _KB_NAMES)
    except Exception as e:
        logging.getLogger(__name__).warning("知识库名映射落盘失败：%s", e)


def kb_lock(kb_id: str) -> threading.RLock:
    """取某个库的建库锁：同一库的入库 / 重建串行，不同库之间互不影响。

    上传要先 embedding 再写 Chroma，全程可能几十秒。两个上传打到同一个库时，
    并发写同一个 collection 的收益远小于出错的风险，所以按库排队。
    不同库仍然并行——它们落的是不同 collection。
    """
    with _REGISTRY_LOCK:
        lk = _KB_LOCKS.get(kb_id)
        if lk is None:
            lk = threading.RLock()
            _KB_LOCKS[kb_id] = lk
        return lk


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
    with _REGISTRY_LOCK:
        # 先重读磁盘：同名库可能刚被另一个进程/线程建过，直接覆盖会把它的记录冲掉
        _load_names()
        _KB_NAMES[kb_id] = name
        _save_names()
        get_or_create(kb_id)  # 确保注册表有记录
    return kb_id, name


def get_or_create(kb_id: str) -> RagPipeline:
    """按 kb_id 获取 RagPipeline，不存在则新建。"""
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            # 锁内建实例：两个并发上传同时打进来时不会各建一个，
            # 否则后建的会顶掉前一个，而前一个可能正拿着它做 embedding
            _PIPELINES[kb_id] = RagPipeline(f"kb_{kb_id}")
            _KB_FILES.setdefault(kb_id, [])  # 已有落盘记录时不能覆盖成空列表
        return _PIPELINES[kb_id]


def exists(kb_id: str) -> bool:
    """磁盘上是否存在这个知识库。"""
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            return False
        return _PIPELINES[kb_id].peek() > 0


def delete_kb(kb_id: str) -> bool:
    """删除知识库（磁盘 + 内存 + 原始文件）。返回是否成功。"""
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            return False
        with kb_lock(kb_id):
            _PIPELINES[kb_id].delete()
        _PIPELINES.pop(kb_id, None)
        _KB_FILES.pop(kb_id, None)
        _KB_NAMES.pop(kb_id, None)
        _KB_LOCKS.pop(kb_id, None)
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
        all_collections = [c.name for c in chroma_client().list_collections()]
    except Exception:
        all_collections = []

    # 整段加锁：循环里会 get_or_create 往注册表塞东西，
    # 不加锁的话并发遍历时 dict 被改动会直接抛 RuntimeError
    with _REGISTRY_LOCK:
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
    # 整体再截一次：digest+safe 可能超过文件系统 255 字节上限，长了会直接 OSError
    return os.path.join(source_dir(kb_id), f"{digest}_{safe}"[:120])


# 库内文件名的主体上限（不含扩展名）：中文名按字符算，留足余量给 md5 前缀
NAME_MAX_BODY = 80


def safe_source_name(name: str, fallback_ext: str = "") -> str:
    """把上传时的原始文件名收成安全的库内文件名。

    source_path 里的 re.sub 会把 / 和 \\ 换成 _，路径穿越其实已经被挡住了，
    但"能不能落盘"不该依赖一个正则的副作用——这里显式做一次 basename，
    顺带处理空名、纯点、以及超长中文名撞上文件名长度上限的情况。

    Args:
        name: 浏览器给的原始文件名（可能带路径，可能是中文，可能极长）
        fallback_ext: 没有扩展名时补的后缀，保证 loader 还能认出类型
    """
    raw = (name or "").strip()
    # 浏览器偶尔会带上完整路径（IE 时代遗毒），也可能被人手工构造
    base = os.path.basename(raw.replace("\\", "/")).strip()
    # 去掉控制字符，避免写盘时炸在文件系统层
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    if base in ("", ".", ".."):
        base = f"unnamed{fallback_ext or '.bin'}"
    # 老库里出现过 ../ 残留：没有分隔符后这些点已经无害，但清掉更省心
    base = base.replace("..", "_")
    root, ext = os.path.splitext(base)
    if ext and not root:  # 形如 ".gitignore" 不算纯扩展名，保留
        root, ext = base, ""
    if len(root) > NAME_MAX_BODY:
        root = root[:NAME_MAX_BODY]
    out = f"{root}{ext}"
    if not os.path.splitext(out)[1] and fallback_ext:
        out += fallback_ext
    return out


# ==================== 文件管理 ====================

def add_file_records(kb_id: str, records: list[dict]) -> None:
    """登记文件（同名覆盖），并落盘。record: {name, chunks, added_at}"""
    with _REGISTRY_LOCK:
        # 重读磁盘再改：落盘是全量覆盖写，不重读就会把别人刚写进去的记录冲掉
        _load_files()
        get_or_create(kb_id)  # 确保注册表有记录
        cur = _KB_FILES.setdefault(kb_id, [])
        for r in records:
            cur[:] = [x for x in cur if x.get("name") != r.get("name")]
            cur.append(r)
        _save_files()


def retain_file_records(kb_id: str, names) -> None:
    """只保留给定文件名（整库重建后清理陈旧记录）。"""
    keep = set(names)
    with _REGISTRY_LOCK:
        _load_files()
        cur = _KB_FILES.get(kb_id)
        if not cur:
            return
        cur[:] = [x for x in cur if x.get("name") in keep]
        _save_files()


def remove_file_record(kb_id: str, name: str) -> bool:
    """移除单个文件记录，返回是否真的删掉了。"""
    with _REGISTRY_LOCK:
        _load_files()
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
    with _REGISTRY_LOCK:
        out = []
        for x in _KB_FILES.get(kb_id, []):
            rec = dict(x)
            rec["origin"] = rec.get("origin") or "upload"
            out.append(rec)
        return out


def get_file_names(kb_id: str) -> list[str]:
    """该库已登记的文件名列表。"""
    with _REGISTRY_LOCK:
        return [x.get("name", "") for x in _KB_FILES.get(kb_id, [])]


# ==================== 状态查询 ====================

def get_status(kb_id: str) -> dict | None:
    """汇总单个库状态，不存在返回 None。"""
    with _REGISTRY_LOCK:
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
        for col in chroma_client().list_collections():
            col_name = col.name
            if not col_name.startswith("kb_"):
                continue
            kb_id = col_name[3:]
            pipe = get_or_create(kb_id)
            pipe.get_or_open()
    except Exception:
        # chroma_db 目录不存在或为空时忽略
        pass
