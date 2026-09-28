# -*- coding: utf-8 -*-
"""SQLite 多会话管理。

设计：
  两张表：
    conversations  —— 会话元数据（标题 / 绑定知识库 / 创建时间）
    messages       —— 消息历史（role / content / 时间）

  所有 SQL 用参数化查询防注入；窗口截断：加载历史时只取最近
  MAX_HISTORY_TURNS 轮（默认 10 轮 = 20 条消息），避免长会话爆 token。
  后续 W3 Agent 集成时会升级到 LangGraph checkpoint，
  这里先做轻量但够用的 SQLite 实现。
"""

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Literal

from core import config

MAX_HISTORY_TURNS = 10  # 每个会话最多保留的对话轮数（1 轮 = user + assistant 两条）


# ---------- 数据库路径 ----------
def _db_path() -> str:
    """SQLite 文件路径（放在 PERSIST_DIR 同级）。"""
    db_dir = os.path.join(config.PROJECT_ROOT, "data")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "sessions.db")


# ---------- 连接管理 ----------
@contextmanager
def _get_conn():
    """获取 SQLite 连接（context manager）。"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row  # 让 cursor 返回 dict-like 对象
    conn.execute("PRAGMA journal_mode=WAL")  # 写前日志，提升并发读性能
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db() -> None:
    """首次运行时创建表（幂等）。"""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                kb_id       TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
                content         TEXT NOT NULL,
                refs            TEXT,
                created_at      INTEGER NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
        """)

        # 迁移：老库补 refs 列（已存在则忽略）
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN refs TEXT")
        except Exception:
            pass


# 模块加载时自动建表
_init_db()


# ==================== 会话 CRUD ====================

def create_conversation(title: str = "新对话", kb_id: str | None = None) -> dict:
    """新建会话，返回会话 dict。"""
    sid = uuid.uuid4().hex[:12]  # 短 ID，前端好展示
    now = int(time.time())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, kb_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (sid, title, kb_id, now, now),
        )
    return get_conversation(sid)


def get_conversation(sid: str) -> dict | None:
    """按 id 取会话，不存在返回 None。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, kb_id, created_at, updated_at FROM conversations WHERE id = ?",
            (sid,),
        ).fetchone()
        return dict(row) if row else None


def list_conversations() -> list[dict]:
    """列出所有会话（按 updated_at 倒序）。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, kb_id, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def rename_conversation(sid: str, title: str) -> bool:
    """重命名会话，返回是否成功。"""
    now = int(time.time())
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, sid),
        )
        return cur.rowcount > 0


def bind_kb(sid: str, kb_id: str | None) -> bool:
    """绑定或解绑知识库（kb_id=None 表示解绑），返回是否成功。"""
    now = int(time.time())
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE conversations SET kb_id = ?, updated_at = ? WHERE id = ?",
            (kb_id, now, sid),
        )
        return cur.rowcount > 0


def delete_conversation(sid: str) -> bool:
    """删除会话及其全部消息，返回是否成功。"""
    with _get_conn() as conn:
        # ON DELETE CASCADE 会自动删 messages，但保险起见先删消息
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (sid,))
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (sid,))
        return cur.rowcount > 0


# ==================== 消息管理 ====================

def add_message(
    sid: str,
    role: Literal["user", "assistant", "system"],
    content: str,
    refs: str = "",
) -> int:
    """追加一条消息，返回 message id。同时更新会话的 updated_at。"""
    now = int(time.time())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, refs, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, role, content, refs, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, sid),
        )
        # 取自增 id
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        return row["id"]


def get_messages(
    sid: str,
    limit_turns: int = MAX_HISTORY_TURNS,
    include_system: bool = True,
) -> list[dict]:
    """取会话消息历史（按时间正序）。

    Args:
        sid: 会话 id
        limit_turns: 最多保留的对话轮数（每轮 = user + assistant），None 表示不限
        include_system: 是否包含 system 消息

    Returns:
        [{role, content, created_at}, ...]
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, refs, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (sid,),
        ).fetchall()

    msgs = [dict(r) for r in rows]
    if not msgs:
        return []

    # 窗口截断：只保留最近 limit_turns 轮
    if limit_turns and limit_turns > 0:
        # 从后往前数，找到最后 limit_turns 个 user 消息的位置
        user_indices = [i for i, m in enumerate(msgs) if m["role"] == "user"]
        if len(user_indices) > limit_turns:
            start = user_indices[-limit_turns]
            msgs = msgs[start:]

    # system 消息保留最靠前的一条（通常是 RAG 系统提示词）
    if not include_system:
        msgs = [m for m in msgs if m["role"] != "system"]

    return msgs


def count_messages(sid: str) -> int:
    """会话消息总数。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?", (sid,)
        ).fetchone()
        return row["c"]


def get_message(sid: str, msg_id: int) -> dict | None:
    """按 id 取单条消息。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, role, content, refs, created_at FROM messages WHERE conversation_id = ? AND id = ?",
            (sid, msg_id),
        ).fetchone()
        return dict(row) if row else None


def find_turn_ids(sid: str, msg_id: int) -> list[int]:
    """找到某条消息所在的完整「一轮对话」包含的所有消息 id。

    规则：
      - 若目标是 user 消息：包含该 user 及其后连续的所有 assistant 消息，直到下一条 user 前。
      - 若目标是 assistant 消息：包含其前最近一条 user 消息，以及该 user 与目标之间的所有 assistant。
    这样前端点 user 或 assistant 的删除，都能整轮移除。
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (sid,),
        ).fetchall()

    msgs = [(r["id"], r["role"]) for r in rows]
    try:
        idx = next(i for i, (mid, _) in enumerate(msgs) if mid == msg_id)
    except StopIteration:
        return []

    role = msgs[idx][1]
    if role == "user":
        start = idx
    else:
        # assistant：向前找最近的 user
        start = next((i for i in range(idx, -1, -1) if msgs[i][1] == "user"), idx)

    ids = [msgs[start][0]]
    for i in range(start + 1, len(msgs)):
        if msgs[i][1] == "user":
            break
        ids.append(msgs[i][0])
    return ids


def delete_messages(sid: str, msg_ids: list[int]) -> bool:
    """批量删除消息，返回是否实际删到了。同时更新会话 updated_at。"""
    if not msg_ids:
        return False
    now = int(time.time())
    with _get_conn() as conn:
        placeholders = ",".join("?" * len(msg_ids))
        cur = conn.execute(
            f"DELETE FROM messages WHERE conversation_id = ? AND id IN ({placeholders})",
            (sid, *msg_ids),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, sid),
            )
        return cur.rowcount > 0
