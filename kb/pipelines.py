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

站内公开（2026-11-13）：库主可以把库标记为公开，登记在 data/kb_public.json。
公开只放开一件事：全站登录用户可以把这个库挂进对话做**只读检索**。
文件清单、上传、删除、重建等路径仍然只认 owner，与公开与否无关——
所以放行点必须显式传 allow_public=True，写路径漏传就是老行为，不会误放开。
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
# { kb_id: {"on": True, "desc": str, "at": int} } —— 公开库登记表。
# 在表里 = 全站登录用户可只读检索（广场上能被人挑走挂进对话）。
# 语义仍然是"集合"：站内公开只有开/关两态，一张扁平表让"这个库公开吗"
# 永远是一次 O(1) 查询，不用解析结构。desc / at 只是广场用的展示信息，
# 不参与任何权限判断 —— 判定只看 kb_id 在不在表里。
_KB_PUBLIC: dict[str, dict] = {}
# 上次读盘时两个注册表文件的 mtime（None = 还没读过）。
# ★ 为什么需要：板块 D 起后台 worker 也会建库（自动沉淀），它写的是同两个 json，
#   而 API 进程与 worker 是**两个服务**。原来"只在内存表为空时读一次"意味着
#   worker 建的库在 API 进程里永远不存在 —— 用户点进资料库看到的还是老列表，
#   重启后端才冒出来（11-01"存进去了但看不见"的同一个家族）。
#   用 mtime 判重：命中时只 stat 一次，不解析 json。
_KB_NAMES_MTIME: float | None = None
_KB_OWNERS_MTIME: float | None = None
_KB_PUBLIC_MTIME: float | None = None

# 保护上面五个全局容器。用 RLock：内部函数会互相调用（add_file_records → get_or_create），
# 普通 Lock 会在第二次 acquire 时把自己锁死
_REGISTRY_LOCK = threading.RLock()

# Chroma collection 合法字符：字母数字 . _ -，首尾必须字母数字，长度 3-63
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,61}[a-zA-Z0-9]$")

# 官方公开知识库的归属 id（2026-11-13）。
#
# 它**不是**一个登录账号——用户表里没有这个人，也不需要给它设密码。
# 只在 kb_owners 表里占一个 owner 值，换来三个好处：
#   1. 官方库天然不出现在任何真实用户的"我的知识库"列表里；
#   2. 归属校验、公开登记、检索挂载全部沿用既有逻辑，一行特判都不用加；
#   3. 导入脚本可以随时以这个身份重跑（幂等），不依赖谁的密码。
OFFICIAL_OWNER = "official"


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


def _public_path() -> str:
    """公开库登记表持久化路径。"""
    return os.path.join(config.PROJECT_ROOT, "data", "kb_public.json")


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
        with open(_files_path(), encoding="utf-8") as f:
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


def _mt(path: str) -> float:
    """文件 mtime；不存在返回 0。调用方需持有 _REGISTRY_LOCK。"""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _load_names() -> None:
    """从磁盘加载中文名映射。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_NAMES, _KB_NAMES_MTIME
    _KB_NAMES_MTIME = _mt(_names_path())
    try:
        with open(_names_path(), encoding="utf-8") as f:
            _KB_NAMES = json.load(f)
    except Exception:
        _KB_NAMES = {}


def _save_names() -> None:
    """持久化中文名映射（原子写）。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_NAMES_MTIME
    try:
        _atomic_write_json(_names_path(), _KB_NAMES)
        # 写完就认下这个 mtime，免得下一次读立刻又白读一遍
        _KB_NAMES_MTIME = _mt(_names_path())
    except Exception as e:
        logging.getLogger(__name__).warning("知识库名映射落盘失败：%s", e)


def _reload_names_if_changed() -> None:
    """别的进程改过注册表就重读。调用方需持有 _REGISTRY_LOCK。"""
    if _KB_NAMES_MTIME is None or _mt(_names_path()) != _KB_NAMES_MTIME:
        _load_names()


def _load_owners() -> None:
    """从磁盘加载归属表。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_OWNERS, _KB_OWNERS_MTIME
    _KB_OWNERS_MTIME = _mt(_owners_path())
    try:
        with open(_owners_path(), encoding="utf-8") as f:
            data = json.load(f)
        _KB_OWNERS = (
            {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
            if isinstance(data, dict)
            else {}
        )
    except Exception:
        _KB_OWNERS = {}


def _save_owners() -> None:
    """持久化归属表（原子写）。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_OWNERS_MTIME
    try:
        _atomic_write_json(_owners_path(), _KB_OWNERS)
        _KB_OWNERS_MTIME = _mt(_owners_path())
    except Exception as e:
        logging.getLogger(__name__).warning("知识库归属表落盘失败：%s", e)


def _reload_owners_if_changed() -> None:
    """别的进程改过归属表就重读。调用方需持有 _REGISTRY_LOCK。"""
    if _KB_OWNERS_MTIME is None or _mt(_owners_path()) != _KB_OWNERS_MTIME:
        _load_owners()


def _load_public() -> None:
    """从磁盘加载公开库登记表。调用方需持有 _REGISTRY_LOCK。

    两种格式都认（2026-11-13 广场化时扩的元信息）：
      - 旧：{"kb_x": true}                                     ← 只有开关
      - 新：{"kb_x": {"on": true, "desc": "…", "at": 1763…}}    ← 带简介与分享时间
    读进内存一律是后者，写出去也只会是后者 —— 所以升级不需要迁移脚本，
    第一次有人碰这张表时旧条目自然被补成新结构。
    旧条目没有 at，广场上按"很久以前"处理（前端不显示"刚分享"这种假信息）。
    """
    global _KB_PUBLIC, _KB_PUBLIC_MTIME
    _KB_PUBLIC_MTIME = _mt(_public_path())
    try:
        with open(_public_path(), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        _KB_PUBLIC = {}
        return
    if not isinstance(data, dict):
        _KB_PUBLIC = {}
        return
    loaded: dict[str, dict] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, dict):
            if not v.get("on"):
                continue
            loaded[k] = {
                "on": True,
                "desc": str(v.get("desc") or ""),
                "at": int(v.get("at") or 0),
                # 官方库（2026-11-13）：站点自建的课程资料库。**只有它**允许
                # 非库主看到文件清单并下载原文——那些资料本来就是公开课程材料；
                # 用户自己分享的库永远只给数量，不给人翻文件。
                "official": bool(v.get("official")),
            }
        elif v:
            loaded[k] = {"on": True, "desc": "", "at": 0, "official": False}
    _KB_PUBLIC = loaded


def _save_public() -> None:
    """持久化公开库登记表（原子写）。调用方需持有 _REGISTRY_LOCK。"""
    global _KB_PUBLIC_MTIME
    try:
        _atomic_write_json(_public_path(), _KB_PUBLIC)
        _KB_PUBLIC_MTIME = _mt(_public_path())
    except Exception as e:
        logging.getLogger(__name__).warning("公开库登记表落盘失败：%s", e)


def _reload_public_if_changed() -> None:
    """别的进程改过公开登记表就重读。调用方需持有 _REGISTRY_LOCK。"""
    if _KB_PUBLIC_MTIME is None or _mt(_public_path()) != _KB_PUBLIC_MTIME:
        _load_public()


def owner_of(kb_id: str) -> str | None:
    """该知识库属于谁；无主返回 None。"""
    with _REGISTRY_LOCK:
        _reload_owners_if_changed()
        return _KB_OWNERS.get(kb_id)


def owned_kb_ids(owner: str) -> set[str]:
    """该用户拥有的全部 kb_id。"""
    with _REGISTRY_LOCK:
        _reload_owners_if_changed()
        return {k for k, v in _KB_OWNERS.items() if v == owner}


def is_public(kb_id: str) -> bool:
    """该库是否已对全站登录用户公开（只读检索）。"""
    with _REGISTRY_LOCK:
        _reload_public_if_changed()
        return kb_id in _KB_PUBLIC


def is_official(kb_id: str) -> bool:
    """该库是否是站点自建的官方课程库（公开且带 official 标记）。

    两个条件都要：官方标记只在公开登记表里，收回公开就整条删掉，
    所以"标记还在"本身就蕴含"还公开着"。这里仍然把两件事分开读，
    是为了让调用方一眼看出门禁依赖的是哪两个前提。
    """
    with _REGISTRY_LOCK:
        _reload_public_if_changed()
        meta = _KB_PUBLIC.get(kb_id)
        return bool(meta and meta.get("official") and meta.get("on"))


def set_public(
    owner: str,
    kb_id: str,
    on: bool,
    desc: str | None = None,
    official: bool | None = None,
    access: str | None = None,
) -> dict:
    """把库标记为公开 / 收回公开，可顺带写一句简介与访问方式。只有库主有权切换。

    `access` 是公开后**别人能拿它做什么**（2026-11-13 用户要求，两种）：
      - `"preview"`（默认）→ 别人可以看文件清单、在线预览，但不能下载原件；
      - `"download"`      → 再加一项"下载原件"。
    两者都能检索、都能看清单 —— 区别只在最后那一下"存到本地"。
    没传（None）就不动，老库读出空值时调用方按 `"preview"` 处理：
    新增能力（能看清单）是用户明确要的，但"能下载"必须由库主自己点开。

    `desc` 三态，别搞混：
      - None（默认）→ 不动已有简介。收回公开时前端一般不传，别把用户写的话抹了。
      - ""          → 清空简介（广场上回落成"这个库还没写简介"）。
      - 非空串       → 覆盖。

    `official` 三态（只有导入脚本会传）：
      - None → 保持原样（普通用户分享走这条，永远拿不到官方标记）。
      - True → 标为官方库，之后非库主也能列文件清单、下载原文。
      - False → 取消官方标记。
    ★ 收敛点就在这里：官方身份**只能**由服务端脚本写入，接口层（
      `/visibility`）从不透传这个字段，所以用户没办法把自己的库伪装成官方。
    返回写进去的那条元信息，省得调用方再读一次盘。
    """
    if owner_of(kb_id) != owner:
        raise NotOwned(kb_id)
    with _REGISTRY_LOCK:
        _reload_public_if_changed()
        if on:
            meta = dict(_KB_PUBLIC.get(kb_id) or {})
            if desc is not None:
                meta["desc"] = desc
            if official is not None:
                meta["official"] = bool(official)
            if access is not None:
                # 白名单收口：调用方可能来自请求体，任何非法值都当"仅观看"，
                # 绝不能让一个手滑的字符串变成"可下载"
                meta["access"] = "download" if access == "download" else "preview"
            meta["on"] = True
            meta.setdefault("desc", "")
            meta.setdefault("official", False)
            # 老库没有这个字段：默认仅观看（可预览、不可下载），
            # 要开下载得库主自己点。
            meta.setdefault("access", "preview")
            # 只在第一次公开时记时间；改简介不该让"分享于"变成今天
            if not meta.get("at"):
                meta["at"] = int(time.time())
            _KB_PUBLIC[kb_id] = meta
        else:
            # 整条删掉，不留 on=False 的壳。代价是简介一起没了，再分享要重写；
            # 但留着壳意味着「在表里 ≠ 公开」，is_public / list_public 每个读点
            # 都要再判一次 on —— 漏一处就是把私有库当公开，这条不能拿便利换。
            _KB_PUBLIC.pop(kb_id, None)
            meta = {"on": False, "desc": "", "at": 0, "official": False, "access": "preview"}
        _save_public()
        return meta


def _access_of(meta: dict) -> str:
    """登记表里的访问方式；老数据（没这个字段）按最保守的"仅观看"。"""
    return "download" if meta.get("access") == "download" else "preview"


def public_meta(kb_id: str) -> dict:
    """公开登记表里该库的那条元信息；没公开（或已收回）返回空字典。

    给 API 层读简介/官方标记用。之前这类读取直接摸 `_KB_PUBLIC` + 私有锁，
    等于把"登记表的内部结构"泄漏给了调用方——换个存储就得改两处。
    """
    with _REGISTRY_LOCK:
        _reload_public_if_changed()
        return dict(_KB_PUBLIC.get(kb_id) or {})


def name_of(kb_id: str | None) -> str | None:
    """kb_id → 显示名。

    只读名字映射，不扫磁盘、不开 collection —— 与 list_all 的区别就在这里：
    调用方只想在响应里带上"投递到了哪个库"的库名时，不该为一个字段去遍历全部库。
    映射里没有就返回 None，不拿 kb_id 冒充库名（那是 list_all 展示兜底的做法）。
    """
    if not kb_id:
        return None
    with _REGISTRY_LOCK:
        _reload_names_if_changed()
        return _KB_NAMES.get(kb_id)


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
    raw = "{}|{}".format(owner, (name or "").strip())
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


def get_or_create(owner: str, kb_id: str, *, allow_public: bool = False) -> RagPipeline:
    """按 kb_id 获取 RagPipeline，不存在则新建。**无权访问直接抛 NotOwned**。

    owner 放在第一个位置、没有默认值，和 core.session 一个道理：
    漏改的调用点会直接 TypeError，而不是悄悄拿到别人的库。

    allow_public 只给「跨用户只读检索」的调用点用（公开库挂进对话）。
    写路径不传它就是默认 False——漏改不会误放开，只会维持原拒绝行为。
    """
    if owner_of(kb_id) != owner and not (allow_public and is_public(kb_id)):
        raise NotOwned(kb_id)
    return _get_or_create_raw(kb_id)


def exists(owner: str, kb_id: str, *, allow_public: bool = False) -> bool:
    """磁盘上是否存在这个知识库（且属于该用户，或已公开且 allow_public）。"""
    if owner_of(kb_id) != owner and not (allow_public and is_public(kb_id)):
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
        # 库都没了，公开标记不能残留：否则 list_public 会一直跳过它（有 owner 才显示），
        # 但同名新库（kb_id 不同）不受影响——这里清的是 id 级的登记
        _KB_PUBLIC.pop(kb_id, None)
        _KB_LOCKS.pop(kb_id, None)
        _save_files()
        _save_names()
        _save_owners()
        _save_public()
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

    # 整段加锁：循环里会 _get_or_create_raw 往注册表塞东西，
    # 不加锁的话并发遍历时 dict 被改动会直接抛 RuntimeError
    with _REGISTRY_LOCK:
        # 名字表也要先对齐磁盘：worker 建的库名字存在同一个 json 里，
        # 不刷新的话列表里会出现"名字 = kb_id"这种半生不熟的一行
        _reload_names_if_changed()
        # 公开标记同样可能被别的进程（worker/另一个 API 实例）改过
        _reload_public_if_changed()
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
            # 文件清单：登记表 + 向量库里没登记过的 source（见 list_files_view 的说明）
            files = list_files_view(owner, kb_id, pipe)
            result.append(
                {
                    "kb_id": kb_id,
                    "name": _KB_NAMES.get(kb_id, kb_id),  # 无映射时回退到 kb_id
                    "chunks": pipe.count(),
                    "files": files,
                    # 前端渲染「公开」标识与开关初态用；公开与否不影响这里能看到什么
                    "public": kb_id in _KB_PUBLIC,
                    # 广场上那句简介。只在公开期间有 —— 收回时整条登记会被删掉
                    # （见 set_public：不留 on=False 的壳，避免读点漏判）
                    "public_desc": (_KB_PUBLIC.get(kb_id) or {}).get("desc", ""),
                    # 仅观看还是可下载。只在公开期间有意义；没设置的按"仅观看"给，
                    # 前端据此把开关摆在正确的那一档
                    "public_access": _access_of(_KB_PUBLIC.get(kb_id) or {}),
                }
            )

        # 刚建好、还没传过文件的空库：Chroma 上的 collection 是第一次写入时才建的，
        # 所以扫磁盘扫不到它。但用户已经在界面上看到这个库名了，
        # 列表里凭空少一项会让人以为"建库失败了"。
        for kb_id in mine - seen:
            result.append(
                {
                    "kb_id": kb_id,
                    "name": _KB_NAMES.get(kb_id, kb_id),
                    "chunks": 0,
                    "files": get_files(owner, kb_id),
                    "public": kb_id in _KB_PUBLIC,
                    "public_desc": (_KB_PUBLIC.get(kb_id) or {}).get("desc", ""),
                    "public_access": _access_of(_KB_PUBLIC.get(kb_id) or {}),
                }
            )
    return result


def list_public() -> list[dict]:
    """全站公开库概要，供其他用户在广场里挑一个挂进对话检索。

    除检索必需的 kb_id / 名称 / 片段数，再给一点**展示用**元信息
    （简介、文件数、作者、分享时间）—— 只有库名和片段数的话，用户没法判断
    这个库值不值得挂，广场就退化成一张列表。

    但**文件清单仍然不给**：那是库主的东西，这里只回数量。
    归属表里已经没有记录的（库删了、公开标记残留）直接跳过 ——
    没有 owner 的公开库谁也检索不了，列出来只会让人点了挂载却一直空转。
    """
    with _REGISTRY_LOCK:
        _reload_public_if_changed()
        _reload_owners_if_changed()
        _reload_names_if_changed()
        # 文件清单是懒加载的（只有上传/删除/预览时才读盘），广场要文件数就得先补上。
        # 代价是每次列广场读一次 kb_files.json：几十 KB 的小文件，比显示 0 份资料诚实。
        _load_files()
        result = []
        for kb_id, meta in _KB_PUBLIC.items():
            owner = _KB_OWNERS.get(kb_id)
            if not owner:
                continue
            pipe = _get_or_create_raw(kb_id)
            if pipe._vs is None:
                pipe.get_or_open()
            result.append(
                {
                    "kb_id": kb_id,
                    "name": _KB_NAMES.get(kb_id, kb_id),
                    "chunks": pipe.count(),
                    "files": len(_KB_FILES.get(kb_id) or []),
                    # owner 只给 id，显示名由 API 层补 —— 这一层不认识账户体系
                    "owner": owner,
                    "desc": meta.get("desc") or "",
                    "at": int(meta.get("at") or 0),
                    # 官方课程库：前端据此分两个分区，并允许点进去看文件
                    "official": bool(meta.get("official")),
                    # 别人能拿它做什么：preview=只能在线看，download=还能存原件。
                    # 老数据没有这个字段，按最保守的"仅观看"给。
                    "access": "download" if meta.get("access") == "download" else "preview",
                }
            )
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


def list_files_view(owner: str, kb_id: str, pipe: "RagPipeline") -> list[dict]:
    """资料列表的完整视图 = 登记表在前 + 向量库里没登记过的 source 在后。

    ★ 为什么不能再用 `get_files(...) or pipe.list_sources()`（2026-11-01）：
      那个 `or` 是个短路陷阱 —— 只要 kb_files.json 里有记录（哪怕只有一条），
      Chroma 兜底就永远不会执行。而「对话沉淀」只往向量库写、不登记文件，
      于是用户存完 AI 的分析、收到"已存入"，回到资料库却一条新资料都没有，
      顶端的"共 N 段"还涨了 —— 看着像自己没存对。
      这里改成合并而不是二选一，历史数据因此自动显形，不用重存一遍。

    origin 对没有登记记录的 source 只能按名字猜（`对话·` 前缀来自
    dialogue_import.build_documents 的 source 约定）；猜错只影响标签，
    不影响查看和删除 —— 两步都按 name 直接查向量库。
    """
    records = get_files(owner, kb_id)
    known = {r.get("name") for r in records}
    extra = []
    for src in pipe.list_sources():
        nm = src.get("name") or ""
        if not nm or nm in known:
            continue
        extra.append(
            {
                "name": nm,
                "chunks": src.get("chunks") or 0,
                "added_at": src.get("added_at"),
                "origin": "dialogue" if nm.startswith("对话·") else "upload",
            }
        )
    return records + extra


# ==================== 状态查询 ====================


def get_status(owner: str, kb_id: str) -> dict | None:
    """汇总单个库状态，不存在或不属于该用户都返回 None。"""
    if owner_of(kb_id) != owner:
        return None
    with _REGISTRY_LOCK:
        if kb_id not in _PIPELINES:
            return None
        pipe = _PIPELINES[kb_id]
        _reload_public_if_changed()
        is_pub = kb_id in _KB_PUBLIC
    chunks = pipe.peek()
    params = pipe._params
    return {
        "kb_id": kb_id,
        "chunks": chunks,
        "chunk_size": params[0] if params else None,
        "chunk_overlap": params[1] if params else None,
        # 同样走合并视图：单库状态页也要能看见对话沉淀进来的资料
        "files": list_files_view(owner, kb_id, pipe),
        # 与 list_all 的 public 字段同口径：状态页也要渲染公开标识
        "public": is_pub,
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
    _load_public()
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
