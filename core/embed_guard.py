"""向量空间指纹：换了 embedding 模型必须重建索引，但代码不能只靠人记得。

问题
----
`config.EMBEDDING_MODEL` 是一个环境变量。改它不需要改代码、不需要过评审、
不需要重新部署前端——但库里所有既有向量都是用旧模型算出来的。
新查询向量落在**另一个语义空间**里：维度通常相同，余弦相似度在数学上照样算得出来，
只是数值不再可比。

所以它不是报错，是**排序接近随机**。用户看到的是"它开始答非所问了"，
日志里什么都没有。这正是审计报告 A1~A4 归的那一类"静默变坏"。

为什么选择报错而不是自动重建
----------------------------
"检测到不一致就全量重建"听起来很贴心，实际上是两个坑：
  1. 触发一次**未预期**的批量 embedding 计费（一个几千片的库足够产生账单事故）；
  2. 重建过程要删旧 collection，中途失败 = 库没了，用户没有撤销手段。

按卷十二定律 1：**失败时不该产生不可恢复的后果。** 所以这里只拒绝 + 说清原因，
把"要不要花钱重建"的决定权交回给人。

老库怎么办
----------
升级前建的库没有指纹。这种情况**不能报错**——那样所有现网库会在升级瞬间全部不可用。
处理方式是"未知"：放行，并在首次检索时把当前模型补写成指纹。
这是有意的乐观：宁可放过一次已经不一致的老库，也不制造一次全站不可用。
代价写在 `check()` 的注释里。
"""

from __future__ import annotations

import json
import threading
from urllib.parse import urlparse

from core import config

# 存在 Chroma collection metadata 里的键。带项目前缀，避免和 hnsw:* 之类撞名
META_KEY = "agent_hub_embed"


class EmbeddingMismatch(RuntimeError):
    """库内向量与当前查询向量不是同一个模型算的。"""

    def __init__(self, collection: str, stored: dict, current: dict) -> None:
        self.collection = collection
        self.stored = stored
        self.current = current
        stored_model = stored.get("model") or "（未知）"
        current_model = current.get("model") or "（未知）"
        super().__init__(
            f"知识库 {collection} 的向量是用 {stored_model} 生成的，"
            f"而当前配置的向量化模型是 {current_model}。"
            "两个模型的向量不在同一语义空间，直接检索会返回近似随机的结果。"
            "请在「知识库 → 重建索引」把该库全量重建一次（会产生一次 embedding 计费），"
            "或把 EMBEDDING_MODEL 改回原值。"
        )


# 已校验过的 collection（进程内）。检索在热路径上，不能每次都去读 metadata。
_VERIFIED: set[str] = set()
_LOCK = threading.Lock()


def _host(url: str) -> str:
    """只取主机名，不带路径与查询串。

    为什么不把完整 URL 算进指纹：同一个服务换个路径前缀（/compatible-mode/v1
    vs /v1）仍然是同一批模型，把它算进去会造成"没换模型却报不一致"的假警报。
    """
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def fingerprint(dim: int | None = None) -> dict:
    """当前配置对应的向量空间指纹。"""
    fp = {
        "model": str(getattr(config, "EMBEDDING_MODEL", "") or ""),
        "base": _host(str(getattr(config, "EMBEDDING_BASE_URL", "") or "")),
    }
    if dim:
        fp["dim"] = int(dim)
    return fp


def _same_space(a: dict, b: dict) -> bool:
    """两个指纹是否指向同一个语义空间。

    只比 model（+ base）：base 不同但 model 同名，仍可能是不同供应商的同名模型，
    这种情况必须报——那是最容易被误判成"没换"的场景。
    dim 只在两边都有值时比，因为老库的指纹里可能没有 dim。
    """
    if (a.get("model") or "") != (b.get("model") or ""):
        return False
    if (a.get("base") or "") != (b.get("base") or ""):
        return False
    da, db = a.get("dim"), b.get("dim")
    if da and db and int(da) != int(db):
        return False
    return True


def _read(collection: str) -> dict | None:
    """读库上的指纹。库不存在、没指纹、读失败都返回 None。"""
    try:
        from kb.rag_core import chroma_client

        coll = chroma_client().get_collection(collection)
        meta = dict(getattr(coll, "metadata", None) or {})
        raw = meta.get(META_KEY)
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def stamp(collection: str, dim: int | None = None) -> None:
    """把当前指纹写到 collection 上（已有则保留原值，只补 dim）。

    写入路径调用。必须在 embedding **之后**调用——dim 来自真实的向量长度，
    而不是"模型文档上写的维度"。文档与实际不一致的情况真会发生（截断、适配层）。
    """
    if not _enabled():
        return
    fp = fingerprint(dim)
    existing = _read(collection)
    if existing and _same_space(existing, fp):
        if dim and not existing.get("dim"):
            existing["dim"] = int(dim)
        else:
            return  # 已经是对的，不必每次写入都 modify collection
    elif existing:
        # 同一个 collection 里混进两个模型的向量——写入路径就该拦住。
        # 这里不抛，交给 check() 在检索时报：写入失败比检索失败更难让用户理解。
        return
    try:
        from kb.rag_core import chroma_client

        coll = chroma_client().get_collection(collection)
        meta = dict(getattr(coll, "metadata", None) or {})
        # 合并而不是覆盖：hnsw:space 这类键由 Chroma 自己维护，覆盖会丢索引配置
        meta[META_KEY] = json.dumps(fp, ensure_ascii=False)
        coll.modify(metadata=meta)
        with _LOCK:
            _VERIFIED.add(collection)
    except Exception:
        # 补写指纹失败不影响检索本身，下次再补
        pass


def check(collection: str) -> None:
    """校验库内向量与当前模型是否同一空间。不一致抛 EmbeddingMismatch。

    三种情况放行：
      1. 没开开关；
      2. 本进程已校验过（热路径，不能每次读 metadata）；
      3. 库里**没有**指纹（升级前建的老库）——放行并补写当前指纹。

    第 3 条是刻意的乐观，代价必须说清：**如果那个老库恰好已经是不一致状态，
    这次补写会把它"认证"成一致，之后再也查不出来。** 唯一的补救是全量重建。
    之所以接受这个代价，是因为反过来的做法（老库一律拒绝）会让升级瞬间
    所有现网库不可用——那是更大的事故。
    """
    if not _enabled():
        return
    with _LOCK:
        if collection in _VERIFIED:
            return
    stored = _read(collection)
    current = fingerprint()
    if stored is None:
        # 老库 / 新库：记下当前指纹，放行
        stamp(collection, None)
        with _LOCK:
            _VERIFIED.add(collection)
        return
    if not _same_space(stored, current):
        from core import metrics

        metrics.inc("embed_guard_mismatch", {"kb": collection})
        raise EmbeddingMismatch(collection, stored, current)
    with _LOCK:
        _VERIFIED.add(collection)


def invalidate(collection: str) -> None:
    """清掉校验缓存（重建索引 / 换模型后调用）。"""
    with _LOCK:
        _VERIFIED.discard(collection)


def _enabled() -> bool:
    return bool(getattr(config, "EMBED_GUARD_ENABLED", True))
