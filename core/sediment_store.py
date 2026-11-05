"""板块 D 的存储层：用户偏好、建库建议、沉淀批次（三张表）。

为什么单开一个模块，而不是塞进 `core/session.py`：那份文件是 A/B/C/D 四块
共同修改的热点，每加一张表都要动它，冲突成本极高（10-30 的方案 §12 明确
"D 自建 store，不改 core/session.py 的建表函数"）。这里照抄
`core/user_feedback.py` 的范式：**共用一个 sqlite 文件、各管各的表、
`ensure()` 惰性幂等**。`core/session._db_path()` 是唯一事实源，测试
monkeypatch 它之后这里自动跟着切库，不需要额外开关。

全部是同步函数（sqlite3 是同步的）。async 端点里请用
`asyncio.to_thread`，别在事件循环里读盘。

三张表的分工：
- `user_prefs`      —— 用户开关。「自动沉淀」关掉就只做正常压缩；
- `kb_proposals`    —— 建库建议。**待确认**的提案落在库里，刷新页面还能恢复，
                       而不是只活在内存/一条 SSE 帧里；
- `sediment_batches` —— 沉淀批次。回答三个问题：这批进了什么、谁触发的、
                       能不能撤销（`(owner, sid, content_hash)` 唯一的幂等键也在这）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid

from core import config

# 已确保建表的库路径集合。键必须带上路径：测试会在同一个进程里反复切库，
# 只用布尔量的话第二次起就不建表了，表现为 "no such table"（10-27 踩过）。
_READY: set[str] = set()


def _db_path() -> str:
    """当前会话库路径。函数而不是常量：测试会 monkeypatch `core.session._db_path`。"""
    from core import session as store

    return store._db_path()  # noqa: SLF001


def _conn() -> sqlite3.Connection:
    from core import session as store

    return store._get_conn()  # noqa: SLF001


def ensure() -> None:
    """建表（幂等、惰性）。模式同 `core/user_feedback.ensure`。"""
    path = _db_path()
    if path in _READY:
        return
    with _conn() as conn:
        conn.executescript(
            """
            -- 用户偏好。没有行 = 全默认（true），不预先给每个用户插一行：
            -- "默认值" 只有一处定义（_DEFAULTS），插行会让它变成两处
            CREATE TABLE IF NOT EXISTS user_prefs (
                owner_id      TEXT PRIMARY KEY,
                auto_sediment INTEGER NOT NULL DEFAULT 1,
                auto_create_kb INTEGER NOT NULL DEFAULT 1,
                updated_at    INTEGER NOT NULL
            );

            -- 建库建议。pending 落库而不是只发一条 SSE：用户可能关掉页面
            -- 明天回来再确认，卡片要能重新长出来
            CREATE TABLE IF NOT EXISTS kb_proposals (
                proposal_id  TEXT PRIMARY KEY,
                owner_id     TEXT NOT NULL,
                sid          TEXT NOT NULL,
                topic        TEXT NOT NULL,
                source       TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                kb_id        TEXT,
                created_at   INTEGER NOT NULL,
                expires_at   INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kbprop_owner ON kb_proposals(owner_id, status);
            CREATE INDEX IF NOT EXISTS idx_kbprop_sid ON kb_proposals(owner_id, sid, source, status);

            -- 沉淀批次。content_hash 唯一索引 = 幂等闸门：
            -- 同一段对话内容被自动沉淀跑了第二次（重试、双 worker）也不会进两遍库
            CREATE TABLE IF NOT EXISTS sediment_batches (
                batch_id     TEXT PRIMARY KEY,
                owner_id     TEXT NOT NULL,
                sid          TEXT NOT NULL,
                kb_id        TEXT NOT NULL,
                source       TEXT NOT NULL,
                refs_json    TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                chunk_count  INTEGER NOT NULL,
                created_at   INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'active'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_sediment_hash
                ON sediment_batches(owner_id, sid, content_hash);
            CREATE INDEX IF NOT EXISTS idx_sedbatch_owner ON sediment_batches(owner_id, created_at DESC);
            """
        )
    _READY.add(path)


# ==================== 用户偏好 ====================

_DEFAULTS = {"auto_sediment": True, "auto_create_kb": True}


def get_prefs(owner: str) -> dict:
    """读偏好。没有行时返回默认值（**不返回空 dict**：调用方不该各自兜默认）。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT auto_sediment, auto_create_kb FROM user_prefs WHERE owner_id = ?",
            (owner,),
        ).fetchone()
    if not row:
        return dict(_DEFAULTS)
    return {"auto_sediment": bool(row[0]), "auto_create_kb": bool(row[1])}


def set_prefs(owner: str, **fields) -> dict:
    """改偏好（只改传入项），返回改完的完整对象。未知字段直接抛 TypeError。"""
    unknown = [k for k in fields if k not in _DEFAULTS]
    if unknown:
        raise TypeError(f"未知偏好项：{unknown}")
    cur = get_prefs(owner)
    cur.update({k: bool(v) for k, v in fields.items()})
    ensure()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_prefs (owner_id, auto_sediment, auto_create_kb, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(owner_id) DO UPDATE SET "
            "auto_sediment = excluded.auto_sediment, "
            "auto_create_kb = excluded.auto_create_kb, "
            "updated_at = excluded.updated_at",
            (owner, int(cur["auto_sediment"]), int(cur["auto_create_kb"]), int(time.time())),
        )
    return cur


# ==================== 建库建议 ====================


def _proposal_row(row) -> dict:
    d = dict(row)
    try:
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
    except (TypeError, ValueError):
        d["payload"] = {}
    return d


def create_proposal(
    owner: str,
    sid: str,
    topic: str,
    source: str,
    payload: dict,
    ttl_hours: int | None = None,
) -> dict:
    """落一条待确认的建库建议。

    topic 取会话标题（见 `topic_of`）。payload 存"确认时要用的东西"：
    消息 id 范围 / 联网来源 / 本轮回答 id —— confirm 时不再重新推断一遍。
    """
    ensure()
    now = int(time.time())
    ttl = config.PROPOSAL_TTL_HOURS if ttl_hours is None else int(ttl_hours)
    pid = "prop_" + uuid.uuid4().hex[:16]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO kb_proposals (proposal_id, owner_id, sid, topic, source, "
            "payload_json, status, kb_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)",
            (
                pid,
                owner,
                sid,
                topic,
                source,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now + ttl * 3600,
            ),
        )
    return get_proposal(owner, pid) or {}


def get_proposal(owner: str, proposal_id: str) -> dict | None:
    """按 id 取建议。**归属并进查询**：别人的 proposal 一律当不存在（不泄露存在性）。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_proposals WHERE proposal_id = ? AND owner_id = ?",
            (proposal_id, owner),
        ).fetchone()
    return _proposal_row(row) if row else None


def update_proposal(owner: str, proposal_id: str, status: str, kb_id: str | None = None) -> bool:
    """改状态。**只有 pending 才能改**（CAS）：并发两次 confirm 只会有一次成功。

    返回是否真的改到了 —— 调用方据此回 409，而不是"看起来成功其实没改"。
    """
    ensure()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE kb_proposals SET status = ?, kb_id = COALESCE(?, kb_id) "
            "WHERE proposal_id = ? AND owner_id = ? AND status = 'pending'",
            (status, kb_id, proposal_id, owner),
        )
        return bool(cur.rowcount)


def pending_proposal(owner: str, sid: str, source: str) -> dict | None:
    """这个会话该来源最近的一条 pending 建议（用于冷却判定）。"""
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_proposals WHERE owner_id = ? AND sid = ? AND source = ? "
            "AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
            (owner, sid, source),
        ).fetchone()
    return _proposal_row(row) if row else None


def latest_pending(owner: str, sid: str) -> dict | None:
    """这个会话里**还没处理、也还没过期**的最新一条建议（刷新后恢复卡片用）。

    与 `pending_proposal` 的区别：那个按来源查（冷却判定要分来源算），
    这个不分来源 —— 用户刷新页面时只关心"这个会话还有没有等我决定的事"。
    过期的不返回：卡片上写着"可入库 N 条"，点了却回 410，比不显示更糟。
    """
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_proposals WHERE owner_id = ? AND sid = ? "
            "AND status = 'pending' AND expires_at >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (owner, sid, int(time.time())),
        ).fetchone()
    return _proposal_row(row) if row else None


def expire_stale(owner: str) -> int:
    """把过期的 pending 批量置为 expired，返回条数。

    不靠定时任务：每次读建议列表时顺手清一遍就够了，过期状态本来就不需要精确到秒。
    """
    ensure()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE kb_proposals SET status = 'expired' "
            "WHERE owner_id = ? AND status = 'pending' AND expires_at < ?",
            (owner, int(time.time())),
        )
        return int(cur.rowcount or 0)


# ==================== 沉淀批次 ====================


def content_hash(texts: list[str]) -> str:
    """一批候选的内容指纹（排序后拼接再 sha1）。

    ★ 排序是必须的：同一批候选的抽取顺序可能因为 markdown 切块而变化，
    不排序就会把同一批内容算成两个指纹，幂等失效。
    """
    joined = "\n".join(sorted(t.strip() for t in texts if (t or "").strip()))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def add_batch(
    owner: str,
    sid: str,
    kb_id: str,
    *,
    source: str,
    refs: list[int],
    chunk_count: int,
    chash: str,
) -> str | None:
    """记一批沉淀。返回 batch_id；同内容哈希已存在时返回 None（= 这批之前进过库）。

    这不是"顺手记个日志"，它就是自动沉淀的幂等闸门：worker 重试、两个 worker
    同时领到同一个 job、用户手点后又跑了一次自动 —— 三种情况都靠这一行挡住。
    """
    ensure()
    bid = "sed_" + uuid.uuid4().hex[:16]
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO sediment_batches (batch_id, owner_id, sid, kb_id, source, "
            "refs_json, content_hash, chunk_count, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active') "
            "ON CONFLICT(owner_id, sid, content_hash) DO NOTHING",
            (
                bid,
                owner,
                sid,
                kb_id,
                source,
                json.dumps([int(r) for r in refs if r], ensure_ascii=False),
                chash,
                int(chunk_count),
                int(time.time()),
            ),
        )
        return bid if cur.rowcount else None


def drop_batch(batch_id: str) -> None:
    """撤掉一条批次记录（入库失败时的回滚）。

    ★ 为什么必须有：`add_batch` 既是"记账"又是**幂等闸门**（唯一索引）。先占位
    再写向量库的话，写库失败就会留下一条"进过了"的假记录，下次重试被自己挡住，
    这批内容永远进不去。所以失败路径要把它删掉，把闸门让出来。
    """
    if not batch_id:
        return
    ensure()
    with _conn() as conn:
        conn.execute("DELETE FROM sediment_batches WHERE batch_id = ?", (batch_id,))


def list_batches(owner: str, sid: str | None = None, limit: int = 20) -> list[dict]:
    """最近的沉淀批次（审计/撤销用）。"""
    ensure()
    sql = "SELECT * FROM sediment_batches WHERE owner_id = ?"
    args: list = [owner]
    if sid:
        sql += " AND sid = ?"
        args.append(sid)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["refs"] = json.loads(d.pop("refs_json") or "[]")
        except (TypeError, ValueError):
            d["refs"] = []
        out.append(d)
    return out


# ==================== 主题名 ====================


def topic_of(owner: str, sid: str) -> str:
    """会话的主题名：优先用标题，没有标题时取首条用户消息前 24 字。

    ★ 为什么不直接用标题：新会话的标题是「新对话」，用它建库会得到
    「对话沉淀·新对话」这种没信息量的库名，第二段对话又建一个，很快满屏同名库。
    ★ 24 字的上限：库名要显示在 /knowledge 的列表里，长了会折成三行。
    """
    from core import session as store

    title = ""
    try:
        conv = store.get_conversation(owner, sid) or {}
        title = (conv.get("title") or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    if title and title not in ("新对话", "未命名会话", "untitled"):
        return title[:24]
    try:
        msgs = store.get_messages(owner, sid, limit_turns=None, include_system=False)
    except Exception:  # noqa: BLE001
        msgs = []
    for m in msgs:
        if m.get("role") != "user":
            continue
        text = " ".join((m.get("content") or "").split())
        if text:
            return text[:24]
    return "未命名对话"


__all__ = [
    "add_batch",
    "content_hash",
    "create_proposal",
    "drop_batch",
    "ensure",
    "expire_stale",
    "get_prefs",
    "get_proposal",
    "list_batches",
    "pending_proposal",
    "set_prefs",
    "topic_of",
    "update_proposal",
]
