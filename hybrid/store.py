"""混合架构持久化层：hybrid.db（与 sessions.db 同目录的独立库）。

为什么独立成库而不是往 sessions.db 加表：
  聊天会话与混合执行是两套生命周期，exec_logs 写入频率远高于消息表，
  分库后 WAL 互不争抢，备份/清理也可以分开做。

四张表：
  workflow_defs  —— 工作流定义（owner_id NULL = 系统内置，只读）
  workflow_runs  —— 每次执行的 TaskContext 整体快照（崩溃续跑的依据）
  switch_rules   —— 切换规则（owner_id NULL = 系统默认，用户同名行覆盖）
  exec_logs      —— 全链路执行事件（要求 6 的落地）

连接管理、owner 第一参数约定与 core/session.py 完全一致（理由见该文件）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from core import config
from hybrid.context import ExecEvent, TaskContext

# exec_logs.payload/decision 的落库长度上限：日志只存摘要，大文本会撑爆库
LOG_TEXT_MAX = 2000

# ---------- 内置工作流：规划 → 执行 ----------
# 执行前路由判为 workflow、又没指定 workflow_id 时的默认起跑流程：
# LLM 节点先把目标拆成步骤（非流式，产物进 shared.plan），
# Agent 节点拿规划做自主执行（流式，产物进 shared.agent_result）。
# seed 进 workflow_defs（owner_id NULL = 系统内置，前端可见、只读）。
DEFAULT_WORKFLOW_ID = "builtin_plan_execute"

DEFAULT_WORKFLOW_DEF: dict = {
    "id": DEFAULT_WORKFLOW_ID,
    "name": "规划-执行（内置）",
    "version": 1,
    "entry": "plan",
    "nodes": [
        {
            "id": "plan",
            "type": "llm",
            "name": "任务规划",
            "config": {
                "prompt": "你是任务规划器。请把下面的用户目标拆成 3~5 个可执行步骤，"
                "每步一行，只输出步骤清单，不要解释：\n{question}",
                "output_key": "plan",
                "stream": False,
            },
        },
        {
            "id": "execute",
            "type": "agent",
            "name": "自主执行",
            "config": {
                "goal": "【用户目标】\n{question}\n\n【执行规划】\n{shared.plan}\n\n"
                "请按规划完成目标，直接给出最终结果。",
                "output_key": "agent_result",
            },
        },
    ],
    "edges": [{"source": "plan", "target": "execute"}],
}


# ---------- 数据库路径 ----------
def _db_path() -> str:
    """数据库文件路径（纯计算，不做 IO）。

    目录创建挪到 `_conn()` 真正建连前做：`_conn()` 每次调用都要取一次路径，
    此前顺带一次 `os.makedirs(exist_ok=True)`，等于每条 SQL 前都白做一次系统调用。
    """
    return os.path.join(config.DATA_DIR, "hybrid.db")


# ---------- 连接管理（模式照抄 core/session.py，理由见该文件注释）----------
_LOCAL = threading.local()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "path", None) == path:
        return conn
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    os.makedirs(
        os.path.dirname(path), exist_ok=True
    )  # 建连前确保目录存在（路径可能被测试改到临时目录）
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _LOCAL.conn = conn
    _LOCAL.path = path
    return conn


@contextmanager
def _get_conn():
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------- 系统默认切换规则 ----------
# priority 数字小者先求值；signal 是编排器/引擎上报的事件名，
# condition 为空表示命中信号即触发。reason 会写进日志与前端轨迹（决策依据）。
DEFAULT_RULES: list[dict] = [
    {
        "name": "wf_requires_agent",
        "signal": "requires_agent",
        "condition": "",
        "action": "to_agent",
        "reason": "工作流节点声明需要自主执行，移交 Agent",
        "priority": 10,
        "cooldown_seconds": 0,  # 节点声明型切换是流程语义的一部分，不防抖
    },
    {
        "name": "wf_node_failure",
        "signal": "node_failed",
        "condition": "",
        "action": "to_agent",
        "reason": "工作流节点失败且无补偿分支，Agent 降级接管",
        "priority": 20,
        "cooldown_seconds": 30,
    },
    {
        "name": "agent_budget_exhausted",
        "signal": "budget_exhausted",
        "condition": "",
        "action": "to_workflow",
        "reason": "Agent 步数/时长/token 预算耗尽，收编回工作流",
        "priority": 20,
        "cooldown_seconds": 30,
    },
    {
        "name": "agent_goal_satisfied",
        "signal": "goal_satisfied",
        "condition": "",
        "action": "to_workflow",
        "reason": "Agent 子目标完成，工作流从断点继续",
        "priority": 30,
        "cooldown_seconds": 0,
    },
]


def _init_db() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_defs (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT,               -- NULL = 系统内置
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                definition  TEXT NOT NULL,      -- WorkflowDef JSON
                version     INTEGER NOT NULL DEFAULT 1,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wfdef_owner ON workflow_defs(owner_id);

            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id      TEXT PRIMARY KEY,
                task_id     TEXT NOT NULL,
                owner_id    TEXT NOT NULL,
                workflow_id TEXT,
                status      TEXT NOT NULL,
                context     TEXT NOT NULL,      -- TaskContext.snapshot() JSON
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_run_owner ON workflow_runs(owner_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS switch_rules (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id         TEXT,          -- NULL = 系统默认
                name             TEXT NOT NULL,
                signal           TEXT NOT NULL,
                condition        TEXT NOT NULL DEFAULT '',
                action           TEXT NOT NULL CHECK(action IN (
                    'to_agent','to_workflow','terminate')),
                reason           TEXT NOT NULL DEFAULT '',
                priority         INTEGER NOT NULL DEFAULT 100,
                enabled          INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds INTEGER NOT NULL DEFAULT 30,
                created_at       INTEGER NOT NULL,
                updated_at       INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rule_owner ON switch_rules(owner_id, name);

            CREATE TABLE IF NOT EXISTS exec_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     TEXT NOT NULL,
                task_id    TEXT NOT NULL,
                event_type TEXT NOT NULL,
                mode       TEXT NOT NULL,
                node_id    TEXT,
                decision   TEXT,                -- switch 事件的决策 JSON
                payload    TEXT,                -- 摘要级 JSON（截断）
                ts         INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_log_run ON exec_logs(run_id, id);
        """)

        # 种子：系统默认规则只插一次（按 name 判重，用户删不掉它们，只能覆盖）
        now = int(time.time())
        for rule in DEFAULT_RULES:
            exists = conn.execute(
                "SELECT 1 FROM switch_rules WHERE owner_id IS NULL AND name = ?",
                (rule["name"],),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO switch_rules "
                    "(owner_id, name, signal, condition, action, reason, priority, enabled, "
                    "cooldown_seconds, created_at, updated_at) "
                    "VALUES (NULL, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        rule["name"],
                        rule["signal"],
                        rule["condition"],
                        rule["action"],
                        rule["reason"],
                        rule["priority"],
                        rule["cooldown_seconds"],
                        now,
                        now,
                    ),
                )

        # 种子：内置"规划-执行"工作流（按 id 判重；owner_id NULL = 系统内置只读）
        if not conn.execute(
            "SELECT 1 FROM workflow_defs WHERE id = ?", (DEFAULT_WORKFLOW_ID,)
        ).fetchone():
            conn.execute(
                "INSERT INTO workflow_defs (id, owner_id, name, description, definition, "
                "version, created_at, updated_at) "
                "VALUES (?, NULL, ?, ?, ?, 1, ?, ?)",
                (
                    DEFAULT_WORKFLOW_ID,
                    DEFAULT_WORKFLOW_DEF["name"],
                    "内置流程：LLM 先把目标拆成步骤，Agent 按规划自主执行"
                    "（hybrid 模式默认起跑流程）",
                    json.dumps(DEFAULT_WORKFLOW_DEF, ensure_ascii=False),
                    now,
                    now,
                ),
            )


_init_db()


def _truncate(text: str) -> str:
    return text if len(text) <= LOG_TEXT_MAX else text[:LOG_TEXT_MAX] + "…[截断]"


# ==================== 工作流定义 CRUD ====================


def create_workflow(owner: str, name: str, description: str, definition: dict) -> dict:
    """新建用户工作流，返回完整行。definition 的合法性由调用方（API 层）先校验。"""
    wf_id = uuid.uuid4().hex[:12]
    now = int(time.time())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO workflow_defs (id, owner_id, name, description, definition, "
            "version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (wf_id, owner, name, description, json.dumps(definition, ensure_ascii=False), now, now),
        )
    return get_workflow(owner, wf_id)


def get_workflow(owner: str, wf_id: str) -> dict | None:
    """按 id 取工作流。系统内置（owner_id NULL）对所有人可见；用户的只对自己可见。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, owner_id, name, description, definition, version, created_at, updated_at "
            "FROM workflow_defs WHERE id = ? AND (owner_id IS NULL OR owner_id = ?)",
            (wf_id, owner),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["definition"] = json.loads(d["definition"])
    d["builtin"] = d["owner_id"] is None
    return d


def list_workflows(owner: str) -> list[dict]:
    """列出系统内置 + 该用户自己的全部工作流。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, owner_id, name, description, definition, version, created_at, updated_at "
            "FROM workflow_defs WHERE owner_id IS NULL OR owner_id = ? ORDER BY updated_at DESC",
            (owner,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["definition"] = json.loads(d["definition"])
        d["builtin"] = d["owner_id"] is None
        out.append(d)
    return out


def update_workflow(owner: str, wf_id: str, name: str, description: str, definition: dict) -> bool:
    """更新用户工作流（系统内置的改不动：owner_id IS NULL 不匹配 WHERE）。"""
    now = int(time.time())
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT version FROM workflow_defs WHERE id = ? AND owner_id = ?", (wf_id, owner)
        ).fetchone()
        if not row:
            return False
        cur = conn.execute(
            "UPDATE workflow_defs SET name = ?, description = ?, definition = ?, "
            "version = ?, updated_at = ? "
            "WHERE id = ? AND owner_id = ?",
            (
                name,
                description,
                json.dumps(definition, ensure_ascii=False),
                row["version"] + 1,
                now,
                wf_id,
                owner,
            ),
        )
        return cur.rowcount > 0


def delete_workflow(owner: str, wf_id: str) -> bool:
    """删除用户工作流（系统内置删不掉）。"""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM workflow_defs WHERE id = ? AND owner_id = ?", (wf_id, owner)
        )
        return cur.rowcount > 0


# ==================== 执行快照（workflow_runs）====================


def save_run(ctx: TaskContext) -> None:
    """把 TaskContext 整体快照落库（upsert）。每次切换前后由编排器调用。

    崩溃恢复时从最近快照 restore 续跑，所以这里存全量而不是增量。
    """
    now = int(time.time())
    data = json.dumps(ctx.snapshot(), ensure_ascii=False)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO workflow_runs (run_id, task_id, owner_id, workflow_id, "
            "status, context, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, "
            "context = excluded.context, workflow_id = excluded.workflow_id, "
            "updated_at = excluded.updated_at",
            (ctx.run_id, ctx.task_id, ctx.owner_id, ctx.workflow_id, ctx.status, data, now, now),
        )


def get_run(owner: str, run_id: str) -> dict | None:
    """按 run_id 取执行记录（含上下文快照）。不是自己的返回 None。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT run_id, task_id, owner_id, workflow_id, status, context, "
            "created_at, updated_at "
            "FROM workflow_runs WHERE run_id = ? AND owner_id = ?",
            (run_id, owner),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["context"] = json.loads(d["context"])
    return d


def list_runs(owner: str, limit: int = 20) -> list[dict]:
    """列出该用户最近的执行记录（不含 context 大字段，列表页够用）。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT run_id, task_id, workflow_id, status, created_at, updated_at "
            "FROM workflow_runs WHERE owner_id = ? ORDER BY updated_at DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== 切换规则 ====================


def list_rules(owner: str) -> list[dict]:
    """取该用户的**生效规则集**：系统默认 + 用户同名覆盖，按 priority 升序。

    用户行与系统行同名时用户行胜出（覆盖语义），不同名则并存。
    停用的规则（enabled=0）直接不返回，引擎不用关心。
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, owner_id, name, signal, condition, action, reason, "
            "priority, enabled, cooldown_seconds "
            "FROM switch_rules WHERE (owner_id IS NULL OR owner_id = ?) AND enabled = 1",
            (owner,),
        ).fetchall()
    merged: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["builtin"] = d["owner_id"] is None
        # 系统行先合并，用户行（builtin=False）同名时整体覆盖
        if d["name"] not in merged or not d["builtin"]:
            merged[d["name"]] = d
    return sorted(merged.values(), key=lambda r: r["priority"])


def upsert_rule(
    owner: str,
    name: str,
    signal: str,
    condition: str,
    action: str,
    reason: str,
    priority: int,
    enabled: bool,
    cooldown_seconds: int,
) -> None:
    """写入用户级规则（同名覆盖系统默认，也覆盖自己旧的）。系统行本身不动。"""
    now = int(time.time())
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM switch_rules WHERE owner_id = ? AND name = ?", (owner, name)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE switch_rules SET signal = ?, condition = ?, action = ?, reason = ?, "
                "priority = ?, enabled = ?, cooldown_seconds = ?, updated_at = ? WHERE id = ?",
                (
                    signal,
                    condition,
                    action,
                    reason,
                    priority,
                    int(enabled),
                    cooldown_seconds,
                    now,
                    row["id"],
                ),
            )
        else:
            conn.execute(
                "INSERT INTO switch_rules (owner_id, name, signal, condition, action, "
                "reason, priority, enabled, cooldown_seconds, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    owner,
                    name,
                    signal,
                    condition,
                    action,
                    reason,
                    priority,
                    int(enabled),
                    cooldown_seconds,
                    now,
                    now,
                ),
            )


def delete_rule(owner: str, name: str) -> bool:
    """删除用户的覆盖规则（删除后系统默认规则重新生效）。系统行删不掉。"""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM switch_rules WHERE owner_id = ? AND name = ?", (owner, name)
        )
        return cur.rowcount > 0


# ==================== 执行日志 ====================


def add_log(ctx: TaskContext, event: ExecEvent) -> None:
    """落一条执行事件。payload/decision 序列化后截断，摘要级数据不进大文本。"""
    decision = json.dumps(event.decision, ensure_ascii=False) if event.decision else None
    payload = json.dumps(event.payload, ensure_ascii=False) if event.payload else None
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO exec_logs (run_id, task_id, event_type, mode, node_id, "
            "decision, payload, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.run_id,
                ctx.task_id,
                event.event_type,
                event.mode,
                event.node_id,
                _truncate(decision) if decision else None,
                _truncate(payload) if payload else None,
                event.ts,
            ),
        )


def list_logs(owner: str, run_id: str, limit: int = 500) -> list[dict]:
    """取某次执行的全量事件（按时间正序）。归属校验并进 JOIN，不是自己的返回空。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT l.id, l.event_type, l.mode, l.node_id, l.decision, l.payload, l.ts "
            "FROM exec_logs l JOIN workflow_runs r ON l.run_id = r.run_id "
            "WHERE l.run_id = ? AND r.owner_id = ? ORDER BY l.id ASC LIMIT ?",
            (run_id, owner, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["decision"] = json.loads(d["decision"]) if d["decision"] else None
        d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        out.append(d)
    return out
