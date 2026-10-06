# -*- coding: utf-8 -*-
"""动态多知识库管理。

与 rag-knowledge-base-demo 固定 main/left/right 三个 collection 不同，
这里支持任意数量的知识库（每个有唯一 kb_id）。

职责：
  - 按 kb_id 创建 / 获取 / 删除 RagPipeline 实例
  - 维护每个库的文件列表状态
  - 支持遍历所有已有库（用于前端列表展示）
  - 中文名 → 安全 ASCII ID 映射（Chroma collection 名只允许 ASCII）
  - **归属隔离**：每个 kb_id 记一个 owner，任何访问都先验归属

归属隔离怎么做的（多用户改造 P2）
--------------------------------
kb_id 在**创建时**就带上 owner 的哈希：`md5(owner|名称)[:12]`。
于是两个用户各建一个「我的库」不会撞车，collection 名也天然分开。

但光靠 id 不可猜是不够的——只要接口还接受调用方传 kb_id，
"猜不到"就不是安全边界。所以另外维护一张 **kb_owners 表**（data/kb_owners.json），
每个读写函数第一件事就是 `owner_of(kb_id) == 当前用户`，否则拒绝。

改造前建的老库在这张表里没有记录 ⇒ 谁都访问不到（默认拒绝），
由 tools/migrate_owner.py 显式指派给某个账号后才恢复。
好处是完全不用动 Chroma 里的 collection（没法改名，硬改要重算），
也不会出现"先登录的人顺手认领了别人的库"。
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
# { kb_id: owner_id }  —— 归属表。没有记录 = 无主（改造前的老库），谁都访问不到
_KB_OWNERS: dict[str, str] = {}

# 保护上面五个全局容器。用 RLock：内部函数会互相调用（add_file_records → get_or_create），
# 普通 Lock 会在第二次 acquire 时把自己锁死
_REGISTRY_LOCK = threading.RLock()

# Chroma collection 合法字符：字母数字 . _ -，首尾必须字母数字，长度 3-63
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,61}[a-zA-Z0-9]$")


class NotOwned(Exception):
    """知识库不属于当前用户（或根本无主）。

    和"不存在"对外表现一致，不区分——区分就等于告诉调用方这个 id 是真的。
    """


def _names_path() -> str:
    """中文名映射持久化文件路径。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_names.json")


def _files_path() -> str:
    """文件清单持久化路径（存原始文件名 + 片段数，不再存临时的绝对路径）。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_files.json")


def _owners_path() -> str:
    """归属表持久化路径。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_owners.json")


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


def _load_owners() -> None:
    """从磁盘加载归属表。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_OWNERS
    try:
        with open(_owners_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        _KB_OWNERS = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} \
            if isinstance(data, dict) else {}
    except Exception:
        _KB_OWNERS = {}


def _save_owners() -> None:
    """持久化归属表（原子写）。调用方需持有 _REGISTRY_LOCK。"""
    try:
        _atomic_write_json(_owners_path(), _KB_OWNERS)
    except Exception as e:
        logging.getLogger(__name__).warning("知识库归属表落盘失败：%s", e)


def owner_of(kb_id: str) -> str | None:
    """该知识库属于谁；无主返回 None。"""
    with _REGISTRY_LOCK:
        if not _KB_OWNERS:
            _load_owners()
        return _KB_OWNERS.get(kb_id)


def owned_kb_ids(owner: str) -> set[str]:
    """该用户拥有的全部 kb_id。"""
    with _REGISTRY_LOCK:
        if not _KB_OWNERS:
            _load_owners()
        return {k for k, v in _KB_OWNERS.items() if v == owner}


def claim_orphan_kbs(owner: str) -> int:
    """把改造前遗留的（无主）知识库指派给某个账号。

    必须显式调用（tools/migrate_owner.py）。扫描磁盘上所有 kb_* collection，
    把归属表里还没有主人的一并认领，返回认领个数。
    """
    try:
        cols = [c.name for c in chroma_client().list_collections()]
    except Exception:
        cols = []
    n = 0
    with _REGISTRY_LOCK:
        _load_owners()
        for col in cols:
            if not col.startswith("kb_"):
                continue
            kb_id = col[3:]
            if kb_id not in _KB_OWNERS:
                _KB_OWNERS[kb_id] = owner
                n += 1
        if n:
            _save_owners()
    return n


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


def new_kb_id(owner: str, name: str) -> str:
    """为一个新库生成 kb_id：**把 owner 混进哈希**。

    不混的话，两个人各建一个「我的库」会算出同一个 id，
    后一个直接写进前一个的 collection 里。混了之后同名不同主也是两个库。
    """
    raw = "%s|%s" % (owner, (name or "").strip())
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# ==================== CRUD ====================

def create_kb(owner: str, name: str) -> tuple[str, str]:
    """登记一个知识库（中文名亦可），返回 (kb_id, display_name)。"""
    name = (name or "").strip()
    kb_id = new_kb_id(owner, name)
    with _REGISTRY_LOCK:
        # 先重读磁盘：同名库可能刚被另一个进程/线程建过，直接覆盖会把它的记录冲掉
        _load_names()
        _load_owners()
        _KB_NAMES[kb_id] = name
        _KB_OWNERS[kb_id] = owner
        _save_names()
        _save_owners()
        _get_or_create_raw(kb_id)  # 确保注册表有记录
    return kb_id, name


def _get_or_create_raw(kb_id: str) -> RagPipeline:
    """不验归属的实例工厂。只给 warmup / create_kb 这类内部路径用。"""
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            # 锁内建实例：两个并发上传同时打进来时不会各建一个，
            # 否则后建的会顶掉前一个，而前一个可能正拿着它做 embedding
            _PIPELINES[kb_id] = RagPipeline(f"kb_{kb_id}")
            _KB_FILES.setdefault(kb_id, [])  # 已有落盘记录时不能覆盖成空列表
        return _PIPELINES[kb_id]


def get_or_create(owner: str, kb_id: str) -> RagPipeline:
    """按 kb_id 获取 RagPipeline，不存在则新建。**不是自己的库直接抛 NotOwned**。

    owner 放在第一个位置、没有默认值，和 core.session 一个道理：
    漏改的调用点会直接 TypeError，而不是悄悄拿到别人的库。
    """
    if owner_of(kb_id) != owner:
        raise NotOwned(kb_id)
    return _get_or_create_raw(kb_id)


def exists(owner: str, kb_id: str) -> bool:
    """磁盘上是否存在这个知识库（且属于该用户）。"""
    if owner_of(kb_id) != owner:
        return False
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            return False
        return _PIPELINES[kb_id].peek() > 0


def delete_kb(owner: str, kb_id: str) -> bool:
    """删除知识库（磁盘 + 内存 + 原始文件）。返回是否成功。"""
    if owner_of(kb_id) != owner:
        return False
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            return False
        with kb_lock(kb_id):
            _PIPELINES[kb_id].delete()
        _PIPELINES.pop(kb_id, None)
        _KB_FILES.pop(kb_id, None)
        _KB_NAMES.pop(kb_id, None)
        _KB_OWNERS.pop(kb_id, None)
        _KB_LOCKS.pop(kb_id, None)
        _save_files()
        _save_names()
        _save_owners()
    # 原始文件一起清掉，否则删库后磁盘上还留着副本
    try:
        import shutil

        shutil.rmtree(source_dir(kb_id), ignore_errors=True)
    except Exception:
        pass
    return True


def list_all(owner: str) -> list[dict]:
    """列出**该用户**的知识库状态（归属表 + 磁盘扫描）。

    别人的库、以及改造前留下的无主库，都不出现在这里。
    """
    result = []
    # 先看磁盘上有哪些 collection
    try:
        all_collections = [c.name for c in chroma_client().list_collections()]
    except Exception:
        all_collections = []

    mine = owned_kb_ids(owner)
    seen: set[str] = set()
    seen: set[str] = set()

    # 整段加锁：循环里会 _get_or_create_raw 往注册表塞东西，
    # 不加锁的话并发遍历时 dict 被改动会直接抛 RuntimeError
    with _REGISTRY_LOCK:
        for col_name in all_collections:
            if not col_name.startswith("kb_"):
                continue
            kb_id = col_name[3:]  # 去掉 kb_ 前缀
            if kb_id not in mine:
                continue  # 不属于本次调用者，跳过
            pipe = _get_or_create_raw(kb_id)
            seen.add(kb_id)
            # 确保 Chroma 实例已打开
            if pipe._vs is None:
                pipe.get_or_open()
            # 文件清单以落盘记录为准，库里真有片段但没记录时用 Chroma 元数据兜底
            files = get_files(owner, kb_id) or pipe.list_sources()
            result.append({
                "kb_id": kb_id,
                "name": _KB_NAMES.get(kb_id, kb_id),  # 无映射时回退到 kb_id
                "chunks": pipe.count(),
                "files": files,
            })

        # 刚建好、还没传过文件的空库：Chroma 上的 collection 是第一次写入时才建的，
        # 所以扫磁盘扫不到它。但用户已经在界面上看到这个库名了，
        # 列表里凭空少一项会让人以为"建库失败了"。
        for kb_id in mine - seen:
            result.append({
                "kb_id": kb_id,
                "name": _KB_NAMES.get(kb_id, kb_id),
                "chunks": 0,
                "files": get_files(owner, kb_id),
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

def add_file_records(owner: str, kb_id: str, records: list[dict]) -> None:
    """登记文件（同名覆盖），并落盘。record: {name, chunks, added_at}"""
    if owner_of(kb_id) != owner:
        raise NotOwned(kb_id)
    with _REGISTRY_LOCK:
        # 重读磁盘再改：落盘是全量覆盖写，不重读就会把别人刚写进去的记录冲掉
        _load_files()
        _get_or_create_raw(kb_id)  # 确保注册表有记录
        cur = _KB_FILES.setdefault(kb_id, [])
        for r in records:
            cur[:] = [x for x in cur if x.get("name") != r.get("name")]
            cur.append(r)
        _save_files()


def retain_file_records(owner: str, kb_id: str, names) -> None:
    """只保留给定文件名（整库重建后清理陈旧记录）。"""
    if owner_of(kb_id) != owner:
        raise NotOwned(kb_id)
    keep = set(names)
    with _REGISTRY_LOCK:
        _load_files()
        cur = _KB_FILES.get(kb_id)
        if not cur:
            return
        cur[:] = [x for x in cur if x.get("name") in keep]
        _save_files()


def remove_file_record(owner: str, kb_id: str, name: str) -> bool:
    """移除单个文件记录，返回是否真的删掉了。"""
    if owner_of(kb_id) != owner:
        return False
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


def get_files(owner: str, kb_id: str) -> list[dict]:
    """返回该库的文件记录副本。不是自己的库返回空列表。

    origin 标记这条资料是怎么进库的：upload=用户上传，web=联网抓的。
    早期记录没有这个字段，统一按 upload 兜底，前端才能直接读。
    """
    if owner_of(kb_id) != owner:
        return []
    with _REGISTRY_LOCK:
        out = []
        for x in _KB_FILES.get(kb_id, []):
            rec = dict(x)
            rec["origin"] = rec.get("origin") or "upload"
            out.append(rec)
        return out


def get_file_names(owner: str, kb_id: str) -> list[str]:
    """该库已登记的文件名列表。不是自己的库返回空列表。"""
    if owner_of(kb_id) != owner:
        return []
    with _REGISTRY_LOCK:
        return [x.get("name", "") for x in _KB_FILES.get(kb_id, [])]


# ==================== 状态查询 ====================

def get_status(owner: str, kb_id: str) -> dict | None:
    """汇总单个库状态，不存在或不属于该用户都返回 None。"""
    if owner_of(kb_id) != owner:
        return None
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
        "files": get_files(owner, kb_id) or pipe.list_sources(),
    }


# ==================== 启动预热 ====================

def warmup() -> None:
    """启动时扫描磁盘，把已有 collection 加载进注册表。

    这里不验归属：预热只是把 Chroma 打开，不涉及任何用户数据读写，
    而且启动阶段根本没有"当前用户"这个概念。真正的门禁在每次访问时。
    """
    _load_names()
    _load_files()
    _load_owners()
    try:
        for col in chroma_client().list_collections():
            col_name = col.name
            if not col_name.startswith("kb_"):
                continue
            kb_id = col_name[3:]
            pipe = _get_or_create_raw(kb_id)
            pipe.get_or_open()
    except Exception:
        # chroma_db 目录不存在或为空时忽略
        pass
