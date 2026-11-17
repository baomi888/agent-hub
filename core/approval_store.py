"""L4：高风险工具的真实人工确认（一次性 token）。

旧实现的问题（`core/tool_guard.py` 2026-10 版本）
----------------------------------------
`needs_approval` 工具第一次只回一个 `confirm:<hash>` 串，模型带着**同一个参数**
再调一次、串一命中就放行。等于模型自己走完「申请 → 回填」两步即可执行，
全程没有真实用户参与——这是纸糊的审批。

新语义
------
1. 工具第一次被调用：只登记一条 pending request，**不执行**；
2. 前端把这条 pending 渲染成确认卡片，用户点击后才调 `POST /api/chat/approval`；
3. 服务端在**点击这个动作发生之后**签发一次性 token，绑定
   `(user, tool_name, args_hash, turn_id)`，带过期时间；
4. 前端带着 token + 同一个 turn_id 重发本轮提问，工具执行前由守卫消费 token；
5. token 用后即焚、不能重放；参数变了（args_hash 变了）token 无效；
6. 模型拿不到 token（token 只走服务端注入的 `configurable`，不在工具 schema 里），
   所以「同参数再调一次」仍然被拒——这是 L4 的核心断言。

存储：**SQLite**（`data/sessions.db` 的 `tool_approvals` 表，复用
`core/session.py` 的线程内连接）
----------------------------------------
2026-11-17 之前这里是三个进程内 dict，文件头还写着「刻意不持久化」。那个判断是错的：
生产是 `uvicorn --workers N`，而审批链路天生要跨三个请求——

| 步骤 | 落点 | dict 版失败表现 |
|---|---|---|
| 工具触发 pending | worker A | — |
| 用户点确认 → `POST /api/chat/approval` | 任意 worker | 概率 (N−1)/N 返回 404 |
| 重发 `/api/chat/stream` 带 token | 任意 worker | `consume` 失败 → 工具再次被拒 |

N=4 时端到端成功率约 1/16，等于 L4 只有单 worker 才真能用。落库后三个请求
落在哪个 worker 都读同一张表。

**为什么落库不会变成「可重放的 token」**：能重放的前提是「用完之后还留着」。
`consume` 是一条 `DELETE ... WHERE token=? AND expires_at>?`，
`rowcount == 1` 才算放行——删除与判定是同一条语句，跨进程也只有一个赢家。
落库换掉的是「状态存在谁的堆里」，不是「token 能不能重复用」。

**仍然不做持久化承诺**：表行带 `expires_at`，进程重启后用户重新发起提问即可；
这里落库只是为了让多个 worker 看见同一份状态，不是存档。过期行在每次写操作
前顺手删（`_prune`），`pending_for_turn` 这种「每轮都会走到」的读路径只在
SQL 里过滤、不写库，避免每轮都去抢写锁。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from core import config, session as store

# 一条语句建全表 + 三个索引。放在模块里是为了让 `_db()` 只认这一份定义。
#
# 两个唯一索引各管一件事：
#   ux_appr_dedupe —— 同 (user, tool, args, turn) 只留一行。模型用相同参数
#                     重试时靠它复用同一条 pending，而不是每调一次刷一张卡片。
#                     跨 worker 也成立（进程内 dict 版靠 `_DEDUPE` 字典，只在本进程有效）。
#   ux_appr_token  —— token 全局唯一。一行只允许挂一个 token，
#                     `UPDATE ... WHERE token IS NULL` 因此天然只有一个赢家。
_DDL = """
CREATE TABLE IF NOT EXISTS tool_approvals (
    request_id   TEXT PRIMARY KEY,
    user         TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    args_hash    TEXT NOT NULL,
    args_preview TEXT NOT NULL DEFAULT '',
    turn_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    token        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_appr_dedupe
    ON tool_approvals(user, tool_name, args_hash, turn_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_appr_token
    ON tool_approvals(token) WHERE token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_appr_exp ON tool_approvals(expires_at);
CREATE INDEX IF NOT EXISTS idx_appr_turn ON tool_approvals(user, turn_id, expires_at);
"""

# 本进程已经建过表的库路径。为什么按路径缓存而不是一个布尔值：
# 测试会把 `session._db_path` 指到临时目录，布尔值会让新库里没有这张表，
# 症状是「单跑绿、连跑红」。
_READY: set[str] = set()


@contextlib.contextmanager
def _db():
    """取 SQLite 连接（复用 `core/session.py` 的线程内连接），并保证表存在。"""
    with store._get_conn() as conn:
        path = store._db_path()
        if path not in _READY:
            conn.executescript(_DDL)
            _READY.add(path)
        yield conn


def args_hash(args: dict[str, Any]) -> str:
    """工具参数的稳定指纹。参数变了 -> hash 变 -> 已签发的 token 自动失效。"""
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class PendingApproval:
    request_id: str
    user: str
    tool_name: str
    args_hash: str
    args_preview: str
    turn_id: str
    created_at: float
    expires_at: float
    # 已经签发过但还没被消费的 token（同一个 pending 只允许一个活跃 token）
    token: str | None = None

    def as_event(self) -> dict:
        return {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "input": self.args_preview,
            "turn_id": self.turn_id,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


def _ttl() -> int:
    return max(1, int(getattr(config, "TOOL_APPROVAL_TTL_SECONDS", 120)))


def _row(r) -> PendingApproval:
    return PendingApproval(
        request_id=r["request_id"],
        user=r["user"],
        tool_name=r["tool_name"],
        args_hash=r["args_hash"],
        args_preview=r["args_preview"],
        turn_id=r["turn_id"],
        created_at=float(r["created_at"]),
        expires_at=float(r["expires_at"]),
        token=r["token"],
    )


def _prune(conn, now: float | None = None) -> None:
    """删掉已过期的行。**只在写路径调用**（见模块头最后一段）。

    调用方必须已持有连接（`_db()`）。
    """
    now = time.time() if now is None else now
    conn.execute("DELETE FROM tool_approvals WHERE expires_at <= ?", (now,))


def create_pending(
    user: str, tool_name: str, args: dict[str, Any], turn_id: str
) -> PendingApproval:
    """登记一条待确认请求。同 (user, tool, args_hash, turn) 复用同一条。

    用 `INSERT OR IGNORE` + 唯一索引做幂等，而不是「先 SELECT 再 INSERT」：
    后者在两个 worker 同时被模型重试打中时会插出两行，用户界面上就是两张
    一模一样的确认卡片。
    """
    ah = args_hash(args)
    preview = json.dumps(args, ensure_ascii=False, default=str)[:400]
    now = time.time()
    with _db() as conn:
        _prune(conn, now)
        conn.execute(
            "INSERT OR IGNORE INTO tool_approvals "
            "(request_id, user, tool_name, args_hash, args_preview, turn_id, "
            " created_at, expires_at, token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (uuid.uuid4().hex, user, tool_name, ah, preview, turn_id, now, now + _ttl()),
        )
        row = conn.execute(
            "SELECT * FROM tool_approvals WHERE user = ? AND tool_name = ? "
            "AND args_hash = ? AND turn_id = ?",
            (user, tool_name, ah, turn_id),
        ).fetchone()
    return _row(row)


def get_pending(request_id: str, user: str | None = None) -> PendingApproval | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tool_approvals WHERE request_id = ? AND expires_at > ?",
            (request_id, time.time()),
        ).fetchone()
    if row is None:
        return None
    rec = _row(row)
    if user is not None and rec.user != user:
        return None
    return rec


def pending_for_turn(user: str, turn_id: str) -> list[PendingApproval]:
    """本轮产生的待确认请求（供 SSE 在流结束时推给前端）。

    每轮 Agent 结束都会调一次，所以这里**只读不写**：过期行交给
    `WHERE expires_at > ?` 过滤，不在这里 DELETE（否则每轮都要抢一次写锁）。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM tool_approvals WHERE user = ? AND turn_id = ? "
            "AND expires_at > ? ORDER BY created_at ASC",
            (user, turn_id, time.time()),
        ).fetchall()
    return [_row(r) for r in rows]


def _issued(rec: PendingApproval, token: str, now: float) -> dict:
    return {
        "request_id": rec.request_id,
        "tool_name": rec.tool_name,
        "args_hash": rec.args_hash,
        "turn_id": rec.turn_id,
        "approval_token": token,
        "expires_in": max(0, int(rec.expires_at - now)),
    }


def issue_token(request_id: str, user: str) -> dict | None:
    """**前端点击后**由服务端调用：签发一次性 token。

    同一个 pending 若已有未过期的活跃 token，直接回同一个（避免连点两下
    产生两个都能用的 token）。pending 不存在 / 不属于该用户 / 已过期 -> None。

    `UPDATE ... WHERE token IS NULL` 是并发下的仲裁点：两个 worker 同时收到
    双击时只有一个把 token 写进去，另一个读到的是前者写的那一枚。
    """
    now = time.time()
    with _db() as conn:
        _prune(conn, now)
        row = conn.execute(
            "SELECT * FROM tool_approvals WHERE request_id = ? AND user = ? "
            "AND expires_at > ?",
            (request_id, user, now),
        ).fetchone()
        if row is None:
            return None
        rec = _row(row)
        if rec.token:
            return _issued(rec, rec.token, now)
        token = "apr:" + secrets.token_urlsafe(32)
        cur = conn.execute(
            "UPDATE tool_approvals SET token = ? WHERE request_id = ? AND user = ? "
            "AND token IS NULL AND expires_at > ?",
            (token, request_id, user, now),
        )
        if cur.rowcount == 0:
            # 被另一个 worker 抢先签发：回它那一枚，别回 404（用户看到的是"点不动"）
            row2 = conn.execute(
                "SELECT * FROM tool_approvals WHERE request_id = ? AND user = ?",
                (request_id, user),
            ).fetchone()
            if row2 is None or not row2["token"]:
                return None
            rec2 = _row(row2)
            return _issued(rec2, rec2.token or "", now)
        return _issued(rec, token, now)


def consume(token: str, user: str, tool_name: str, ah: str, turn_id: str) -> bool:
    """消费一次 token。绑定全部匹配且未过期才返回 True；成功后立即删除。

    **一条 DELETE 完成「判定 + 焚毁」**：先把行删掉、再按 rowcount 判真假。
    如果写成「先 SELECT 判断、再 DELETE」，两个 worker 同时带同一 token 重发时
    都会读到这一行、都返回 True，工具就执行了两次——这正是落库要避免的事。
    """
    if not token:
        return False
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM tool_approvals WHERE token = ? AND user = ? AND tool_name = ? "
            "AND args_hash = ? AND turn_id = ? AND expires_at > ?",
            (token, user, tool_name, ah, turn_id, time.time()),
        )
        return cur.rowcount == 1


def has_active_token(request_id: str) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM tool_approvals WHERE request_id = ? "
            "AND token IS NOT NULL AND expires_at > ?",
            (request_id, time.time()),
        ).fetchone()
    return row is not None


def reset() -> None:
    """清空全部状态（仅供测试）。"""
    with _db() as conn:
        conn.execute("DELETE FROM tool_approvals")
