"""校园工具集 · 专注记录的云端存储（B6）。

两张表，语义完全不同，因此同步策略也完全不同：

  focus_sessions —— **追加型**。一条记录生成后就不再修改，所以服务端用
                   `INSERT OR IGNORE` 按主键 id 幂等写入即可，天然无冲突。
                   重复上报（断网重试、两台设备各推一次）不会重复计数，
                   这正是「网络不可靠」场景下最需要的性质。

  focus_tasks    —— **会被修改**（改名、归档、改大类）。每行带 `updated_at`，
                   写入时「后写胜」：只有当新数据的 updated_at 更大时才覆盖。
                   没有这个条件，一台设备上的旧数据会把另一台上的新名字盖回去。

profile 与 checkin 的做法一致：复用同一个 SQLite（sessions.db），新表**懒建、幂等、
不在导入期写库**；所有查询强制 `WHERE owner_id = ?`，漏传 uid 直接 TypeError。

隐私边界：这里只存「学什么、多久」，不存任何对话内容。
`api/admin.py` 的统计口径需要单独确认（见审核报告的风险项）。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from core import session as store

# 单次上报上限：前端按 200 条一批分片，这里留 5 倍余量防手抖，
# 但绝不能无限——一条 10 万行的请求会把 SQLite 写锁占满整个 busy_timeout。
MAX_BATCH = 1000

# 允许从外部写入的字段白名单。**不直接用请求体拼 SQL**：
# 前端可以送任何 key 过来，白名单是唯一能保证「不会有人写 owner_id」的办法。
_TASK_PATCH_FIELDS = ("name", "category", "color", "archived", "last_used_at")


def _ensure_focus_tables() -> None:
    """懒建两张表（复用 sessions.db 连接，幂等）。"""
    with store._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS focus_sessions (
                id               TEXT PRIMARY KEY,
                owner_id         TEXT NOT NULL,
                task_id          TEXT NOT NULL DEFAULT '',
                task_name        TEXT NOT NULL DEFAULT '',
                category         TEXT NOT NULL DEFAULT '未分类',
                started_at       INTEGER NOT NULL,
                ended_at         INTEGER NOT NULL,
                seconds          INTEGER NOT NULL DEFAULT 0,
                planned_seconds  INTEGER NOT NULL DEFAULT 0,
                direction        TEXT NOT NULL DEFAULT 'countdown',
                mode             TEXT NOT NULL DEFAULT 'normal',
                paused_count     INTEGER NOT NULL DEFAULT 0,
                away_count       INTEGER NOT NULL DEFAULT 0,
                away_seconds     INTEGER NOT NULL DEFAULT 0,
                end_reason       TEXT NOT NULL DEFAULT 'finished',
                created_at       INTEGER NOT NULL,
                synced_at        INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_focus_owner_time
                ON focus_sessions(owner_id, started_at DESC);

            CREATE TABLE IF NOT EXISTS focus_tasks (
                id            TEXT PRIMARY KEY,
                owner_id      TEXT NOT NULL,
                name          TEXT NOT NULL,
                category      TEXT NOT NULL DEFAULT '未分类',
                color         TEXT,
                archived      INTEGER NOT NULL DEFAULT 0,
                created_at    INTEGER NOT NULL,
                last_used_at  INTEGER NOT NULL,
                updated_at    INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_focus_task_owner
                ON focus_tasks(owner_id, archived);
            """
        )


# ---------------- 归一化 ----------------
#
# 服务端也要做一遍清洗：客户端的校验可以被绕过（改前端、直接打接口），
# 而脏数据一旦进库，前端每次读取都得带着容错逻辑兜它一辈子。


def _int(value: Any, default: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n


def _text(value: Any, default: str = "", limit: int = 200) -> str:
    if not isinstance(value, str):
        return default
    return value.strip()[:limit]


def _norm_session(uid: str, row: Any, now: int) -> tuple | None:
    """把一条上报的会话洗成可直接入库的元组；不合格返回 None（整条丢弃）。"""
    if not isinstance(row, dict):
        return None

    sid = _text(row.get("id"), "", 80)
    started = _int(row.get("startedAt"), -1)
    ended = _int(row.get("endedAt"), -1)
    if sid == "" or started < 0 or ended < 0:
        return None

    direction = "countup" if row.get("direction") == "countup" else "countdown"
    mode = row.get("mode") if row.get("mode") in ("immersive", "strict") else "normal"
    end_reason = (
        row.get("endReason")
        if row.get("endReason") in ("early", "abandoned", "stopped")
        else "finished"
    )

    return (
        sid,
        uid,
        _text(row.get("taskId"), "", 80),
        _text(row.get("taskName"), "", 120),
        _text(row.get("category"), "未分类", 40) or "未分类",
        started,
        ended,
        max(0, _int(row.get("seconds"), 0)),
        max(0, _int(row.get("plannedSeconds"), 0)),
        direction,
        mode,
        max(0, _int(row.get("pausedCount"), 0)),
        max(0, _int(row.get("awayCount"), 0)),
        max(0, _int(row.get("awaySeconds"), 0)),
        end_reason,
        now,
        _int(row.get("syncedAt"), now),
    )


def _norm_task(uid: str, row: Any, now: int) -> tuple | None:
    if not isinstance(row, dict):
        return None

    tid = _text(row.get("id"), "", 80)
    name = _text(row.get("name"), "", 120)
    if tid == "" or name == "":
        return None

    created = _int(row.get("createdAt"), now)
    last_used = _int(row.get("lastUsedAt"), created)
    # 前端没送 updatedAt（旧版本）时退回 lastUsedAt：否则老任务会被当成
    # 「1970 年更新的」，永远输给任何一条远端数据。
    updated = _int(row.get("updatedAt"), last_used or created)

    return (
        tid,
        uid,
        name,
        _text(row.get("category"), "未分类", 40) or "未分类",
        _text(row.get("color"), "", 20) or None,
        1 if row.get("archived") is True else 0,
        created,
        last_used,
        updated,
    )


def _session_dict(row: Any) -> dict:
    """库里的行 → 前端用的驼峰结构（与 types.ts 的 FocusSession 严格对应）。"""
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "taskName": row["task_name"],
        "category": row["category"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "seconds": row["seconds"],
        "plannedSeconds": row["planned_seconds"],
        "direction": row["direction"],
        "mode": row["mode"],
        "pausedCount": row["paused_count"],
        "awayCount": row["away_count"],
        "awaySeconds": row["away_seconds"],
        "endReason": row["end_reason"],
        "syncedAt": row["synced_at"],
    }


def _task_dict(row: Any) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "color": row["color"] or "",
        "archived": bool(row["archived"]),
        "createdAt": row["created_at"],
        "lastUsedAt": row["last_used_at"],
        "updatedAt": row["updated_at"],
    }


# ---------------- 会话 ----------------


def upsert_sessions(uid: str, rows: Iterable[Any]) -> dict:
    """批量上报会话。按 id 幂等：已存在的一律忽略，绝不覆盖。

    返回 `accepted`（真正写进去的条数）与 `received`（收到的条数）。
    两者不等不一定是错误 —— 断网重试时大量重复是正常的。
    """
    _ensure_focus_tables()
    now = int(time.time() * 1000)

    prepared: list[tuple] = []
    for row in rows:
        item = _norm_session(uid, row, now)
        if item is not None:
            prepared.append(item)

    if not prepared:
        return {"accepted": 0, "received": 0}

    with store._get_conn() as conn:
        cur = conn.executemany(
            """
            INSERT OR IGNORE INTO focus_sessions (
                id, owner_id, task_id, task_name, category,
                started_at, ended_at, seconds, planned_seconds,
                direction, mode, paused_count, away_count, away_seconds,
                end_reason, created_at, synced_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            prepared,
        )
        accepted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    return {"accepted": accepted, "received": len(prepared)}


def list_sessions(uid: str, from_ts: int = 0, to_ts: int = 0, limit: int = 5000) -> list[dict]:
    """拉取区间内的会话（左闭右开，与前端统计口径一致）。`to_ts<=0` 表示不限上界。"""
    _ensure_focus_tables()
    limit = max(1, min(int(limit or 5000), 5000))

    sql = "SELECT * FROM focus_sessions WHERE owner_id = ? AND started_at >= ?"
    params: list[Any] = [uid, int(from_ts or 0)]
    if to_ts and to_ts > 0:
        sql += " AND started_at < ?"
        params.append(int(to_ts))
    sql += " ORDER BY started_at ASC LIMIT ?"
    params.append(limit)

    with store._get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()

    return [_session_dict(r) for r in rows]


# ---------------- 任务 ----------------


def upsert_tasks(uid: str, rows: Iterable[Any]) -> dict:
    """任务上报：同 id 且「更新的时间更大」才覆盖，否则保留服务端版本。

    `WHERE` 里带上 owner 判断：万一两个账号生成了相同 id（同一毫秒 + 同名，
    概率极低但不是零），别人的行不能被这条 UPDATE 改掉。
    """
    _ensure_focus_tables()
    now = int(time.time() * 1000)

    prepared: list[tuple] = []
    for row in rows:
        item = _norm_task(uid, row, now)
        if item is not None:
            prepared.append(item)

    if not prepared:
        return {"accepted": 0, "received": 0}

    with store._get_conn() as conn:
        cur = conn.executemany(
            """
            INSERT INTO focus_tasks (
                id, owner_id, name, category, color, archived,
                created_at, last_used_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                color = excluded.color,
                archived = excluded.archived,
                last_used_at = excluded.last_used_at,
                updated_at = excluded.updated_at
            WHERE focus_tasks.owner_id = excluded.owner_id
              AND excluded.updated_at > focus_tasks.updated_at
            """,
            prepared,
        )
        accepted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    return {"accepted": accepted, "received": len(prepared)}


def list_tasks(uid: str) -> list[dict]:
    """该账号的全部任务（含归档）。数量级很小，不分页。"""
    _ensure_focus_tables()
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM focus_tasks WHERE owner_id = ? ORDER BY last_used_at DESC",
            (uid,),
        ).fetchall()
    return [_task_dict(r) for r in rows]


def patch_task(uid: str, task_id: str, fields: dict) -> dict | None:
    """改单个任务（改名 / 归档 / 改大类）。字段走白名单，找不到返回 None。"""
    _ensure_focus_tables()

    sets: list[str] = []
    params: list[Any] = []
    for key in _TASK_PATCH_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "archived":
            sets.append("archived = ?")
            params.append(1 if value is True else 0)
        elif key == "last_used_at":
            sets.append("last_used_at = ?")
            params.append(_int(value, int(time.time() * 1000)))
        else:
            sets.append(f"{key} = ?")
            params.append(_text(value, "", 120))

    if not sets:
        return None

    # 改了任何字段，updated_at 都要跟着动，否则下次同步会被远端旧值覆盖回来
    sets.append("updated_at = ?")
    params.append(int(time.time() * 1000))
    params.extend([task_id, uid])

    with store._get_conn() as conn:
        conn.execute(
            f"UPDATE focus_tasks SET {', '.join(sets)} WHERE id = ? AND owner_id = ?",
            tuple(params),
        )
        row = conn.execute(
            "SELECT * FROM focus_tasks WHERE id = ? AND owner_id = ?",
            (task_id, uid),
        ).fetchone()

    return _task_dict(row) if row else None
