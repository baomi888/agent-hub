"""对话沉淀的服务层：抽候选 → 按主题建库/复用 → 入库 → staged 回写 → 记批次。

**这个文件是三条沉淀路径的唯一实现**（10-30 接口文档 §6 / §8.1）：

| 谁调用                     | source      | 时机                              |
|----------------------------|-------------|-----------------------------------|
| 手工（用户勾选后点存）      | `manual`    | `POST /api/kb/{kb_id}/import-dialogue` |
| 自动（压缩成功后）          | `auto`      | worker 的 `sediment.dialogue` 任务  |
| 确认建库建议（卡片点确认）  | `web_confirm` / `no_kb` | `POST /api/kb/proposals/{id}/confirm` |

三条路走同一个 `write_picked()` 的原因很实际：11-01 那次事故——
手工路径在 API 里内联写了一份"写向量库 + 登记文件 + 回写 staged"，
自动路径另写一份，结果**只有一份被改过**，另一条路上的用户就看不到自己存的东西
（`get_files(...) or list_sources()` 的短路 bug）。同一件事只能有一个实现。

## 一个容易忽略的点：这批内容"进过了"怎么判

`sediment_store.add_batch` 的唯一索引 `(owner, sid, content_hash)` 就是闸门。
它是**先占位再写库**的，所以写库失败必须 `drop_batch` 把位置让出来，
否则下次重试被自己挡住（代码里 `try/except` 那段就是这么写的）。
"""

from __future__ import annotations

import logging
import time

from core import config, quota, sediment_store
from core import session as store
from kb import dialogue_import, pipelines

_log = logging.getLogger("sediment")

JOB_KEY = "sediment.dialogue"

# 自动沉淀建出来的库统一这个名字前缀。用途有两个：
# 1) 和用户自己建的库在列表里区分开；
# 2) `reuse_or_create_topic_kb` 靠它数"我已经有多少个对话沉淀库了"。
KB_PREFIX = "对话沉淀·"


# ==================== 入库（三条路共用） ====================


def _register_file(owner: str, kb_id: str, sid: str, title: str, chunks: int) -> None:
    """把这次沉淀登记成资料库里的一条「资料」。

    ★ 不登记的话用户看不见（2026-11-01 用户实测）：/knowledge 的卡片数据源是
      `kb_files.json` 登记表，只写向量库 = 用户收到"已存入"却一条新资料都没有。
    同名会话复用同一条记录并累加段数，所以同一段对话存两次显示的是累计值。
    """
    doc_name = f"对话·{title}"
    prev = next((x for x in pipelines.get_files(owner, kb_id) if x.get("name") == doc_name), None)
    pipelines.add_file_records(
        owner,
        kb_id,
        [
            {
                "name": doc_name,
                "chunks": int((prev or {}).get("chunks") or 0) + int(chunks),
                "added_at": int(time.time()),
                "origin": "dialogue",
                "sid": sid,
            }
        ],
    )


def write_picked(
    owner: str,
    sid: str,
    kb_id: str,
    picked: list[dict],
    *,
    source: str,
    title: str | None = None,
) -> dict:
    """把**已经抽好的**候选写进库。同步函数，调用方负责 to_thread 与配额检查。

    Returns:
        {imported, skipped, chunks, staged_segments, batch_id, duplicate}

    `duplicate=True` 表示这批内容之前已经沉过（幂等命中），不是错误。
    """
    conv_title = title
    if not conv_title:
        conv = store.get_conversation(owner, sid) or {}
        conv_title = conv.get("title") or "未命名会话"

    pipe = pipelines.get_or_create(owner, kb_id)
    # 去重：这个库这段对话里已经沉过的原句不再写第二遍。
    # 向量库每条都给 uuid4 当 id，同一句话存两次是两条独立片段，
    # 会重复占掉检索 top-k 的名额（kb/rag_core.py::get_dialogue_texts 有详述）。
    existing = pipe.get_dialogue_texts(sid)
    clean = dialogue_import.drop_existing(picked, existing)
    skipped = len(picked) - len(clean)
    if not clean:
        # 全存过：不是错误（用户可能手滑点了第二次），但也不该静默说成功
        return {
            "imported": 0,
            "skipped": skipped,
            "chunks": 0,
            "staged_segments": 0,
            "batch_id": "",
            "duplicate": True,
        }

    texts = [c.get("text") or "" for c in clean]
    chash = sediment_store.content_hash(texts)
    refs = sorted({int(r) for c in clean for r in (c.get("refs") or []) if r})
    batch_id = sediment_store.add_batch(
        owner,
        sid,
        kb_id,
        source=source,
        refs=refs,
        chunk_count=len(clean),
        chash=chash,
    )
    if not batch_id:
        # 同内容哈希已存在 = 这批之前进过库（重试 / 双 worker / 手点两次）
        return {
            "imported": 0,
            "skipped": skipped + len(clean),
            "chunks": 0,
            "staged_segments": 0,
            "batch_id": "",
            "duplicate": True,
        }

    try:
        docs = dialogue_import.build_documents(clean, sid, conv_title)
        n = pipe.add_chunks(docs)
        if n:
            try:
                quota.bump_usage(owner, "embed", n)
            except Exception:  # noqa: BLE001 —— 记账失败不该让已经写进去的内容算失败
                _log.warning("sediment bump_usage failed owner=%s n=%s", owner, n)
            _register_file(owner, kb_id, sid, conv_title, n)
    except Exception:
        # 闸门让出来，否则下次重试被自己的占位挡住（见模块开头的说明）
        sediment_store.drop_batch(batch_id)
        raise

    staged = 0
    if refs:
        try:
            from core import context_compress

            staged = context_compress.mark_segments_staged(owner, sid, set(refs))
        except Exception as e:  # noqa: BLE001 —— 标记失败只影响折叠时机，不影响入库
            _log.warning("sediment mark_staged failed sid=%s: %s", sid, e)

    return {
        "imported": n,
        "skipped": skipped,
        "chunks": n,
        "staged_segments": staged,
        "batch_id": batch_id,
        "duplicate": False,
    }


def ingest_candidates(
    owner: str,
    sid: str,
    kb_id: str,
    indexes: list[int] | None = None,
    *,
    source: str = "manual",
) -> dict:
    """抽候选 + 入库（`indexes` 为空 = 全部）。供 worker 与工具层调用。

    配额在这里检查（`quota.guard`）：worker 路径没有 HTTP 层替它做这件事，
    自动沉淀更不该绕开额度。
    """
    msgs = store.get_messages(owner, sid, limit_turns=None, include_system=False)
    if not msgs:
        return {"imported": 0, "skipped": 0, "chunks": 0, "staged_segments": 0, "batch_id": ""}
    cands = dialogue_import.extract_blocks(msgs)
    if not cands:
        return {"imported": 0, "skipped": 0, "chunks": 0, "staged_segments": 0, "batch_id": ""}
    if indexes:
        # 下标口径与 `GET /api/kb/dialogue-candidates` 一致：先 rank 再取。
        # 否则"预览里第 1 条"和"存进去的第 1 条"是两句话（2026-11-05 的 ★★★）。
        cands = dialogue_import.rank_for_preview(cands)
        bad = [i for i in indexes if i < 0 or i >= len(cands)]
        if bad:
            raise ValueError(f"候选下标越界：{bad[:5]}")
        cands = [cands[i] for i in indexes]
    picked = [c for c in cands if (c.get("text") or "").strip()]
    if not picked:
        return {"imported": 0, "skipped": 0, "chunks": 0, "staged_segments": 0, "batch_id": ""}
    quota.guard(owner, "embed")
    return write_picked(owner, sid, kb_id, picked, source=source)


# ==================== 按主题建库 / 复用 ====================


def reuse_or_create_topic_kb(owner: str, topic: str) -> tuple[str, str]:
    """同主题复用库，没有才建。返回 (kb_id, 库名)。

    「同主题」的判定只按**库名完全相同**，不做语义相似：
    自动推断主题相似度一定会错，而错了就是把两段不相干的对话混进一个库，
    检索时互相污染 —— 宁可多建几个库，也不要混。
    """
    name = f"{KB_PREFIX}{topic}"
    try:
        mine = [k for k in pipelines.list_all(owner) if (k.get("name") or "") == name]
    except Exception:  # noqa: BLE001 —— 列库失败不该让沉淀整个失败
        mine = []
    if mine:
        # 同名只可能有一条（create_kb 用名字派生 kb_id），但真出现多条时取段数最多的
        best = max(mine, key=lambda k: int(k.get("chunks") or 0))
        return str(best["kb_id"]), name

    # 上限保护：长期用户会攒出几百个库，把 /knowledge 撑成一屏滚动条
    try:
        topic_kbs = [k for k in pipelines.list_all(owner) if (k.get("name") or "").startswith(KB_PREFIX)]
    except Exception:  # noqa: BLE001
        topic_kbs = []
    cap = int(getattr(config, "SEDIMENT_MAX_TOPIC_KBS", 50) or 0)
    if cap and len(topic_kbs) >= cap:
        # 复用最近写入最多的那个（近似"最常用的主题"）：不再新建
        best = max(topic_kbs, key=lambda k: int(k.get("chunks") or 0))
        return str(best["kb_id"]), str(best.get("name") or name)

    kb_id, display = pipelines.create_kb(owner, name)
    return kb_id, display


# ==================== 自动沉淀：入队与执行 ====================


def enqueue(owner: str, sid: str, boundary_id: int) -> bool:
    """压缩成功后登记一次沉淀任务。**任何异常都不外抛**。

    ★ 这条规矩比看起来重要：它挂在 `maybe_compress` 的返回路径上，
      压缩本身是"让上下文不炸"的关键路径，沉淀失败绝不能把它拖下水。
      所以这里连 import 错误都吞掉。
    """
    if not getattr(config, "SEDIMENT_ENABLED", True):
        return False
    try:
        from core import scheduler as sched

        rid, _key = sched.enqueue_job(
            JOB_KEY, f"{owner}:{sid}", max(1, int(boundary_id or 0)), trigger="compress"
        )
        # rid 为 None 表示同一 boundary 已经排过（幂等命中），不算失败
        return rid is not None
    except Exception as e:  # noqa: BLE001
        _log.warning("sediment enqueue failed sid=%s: %s", sid, e)
        return False


def run_one(job_key: str, subject_id: str, planned_at: int) -> dict:
    """worker 执行入口。`subject_id` 形如 `{owner}:{sid}`。

    返回 `{"tokens_out": n}`；偏好关闭 / 无候选时返回 `{}`（都算成功：
    "没什么可沉"不是错误，不该在 job_runs 里留一条 failed）。
    """
    if not getattr(config, "SEDIMENT_ENABLED", True):
        return {}
    owner, _, sid = str(subject_id).partition(":")
    if not owner or not sid:
        _log.warning("sediment bad subject_id=%s", subject_id)
        return {}

    if not sediment_store.get_prefs(owner).get("auto_sediment", True):
        return {}

    try:
        msgs = store.get_messages(owner, sid, limit_turns=None, include_system=False)
    except Exception as e:  # noqa: BLE001
        _log.warning("sediment read messages failed sid=%s: %s", sid, e)
        return {}
    if not msgs:
        return {}

    picked = dialogue_import.extract_blocks(msgs)
    floor = float(getattr(config, "SEDIMENT_MIN_SALIENCE", 0) or 0)
    if floor > 0:
        picked = [c for c in picked if float(c.get("salience") or 0) >= floor]
    picked = [c for c in picked if (c.get("text") or "").strip()]
    if not picked:
        return {}

    topic = sediment_store.topic_of(owner, sid)
    kb_id, _name = reuse_or_create_topic_kb(owner, topic)
    quota.guard(owner, "embed")
    res = write_picked(owner, sid, kb_id, picked, source="auto")
    n = int(res.get("imported") or 0)
    _log.info(
        "sediment done owner=%s sid=%s kb=%s imported=%s staged=%s duplicate=%s",
        owner,
        sid,
        kb_id,
        n,
        res.get("staged_segments"),
        res.get("duplicate"),
    )
    return {"tokens_out": n}


__all__ = [
    "JOB_KEY",
    "KB_PREFIX",
    "enqueue",
    "ingest_candidates",
    "reuse_or_create_topic_kb",
    "run_one",
    "write_picked",
]
