# -*- coding: utf-8 -*-
"""每日委托与每日总结的纯函数核心。

设计要点（详见 .scratch/daily-scheduler/spec.md §3.1 / issues/05-daily-summary.md）：

1. **LLM 可注入**：所有会调模型的函数都把 `llm` 作为可选参数，默认走
   `core.llm.get_llm()`（DeepSeek）。测试注入假 LLM（`.invoke()` 返回带 `.content`
   的对象即可），不依赖真实 API Key —— 满足 spec §4.8。

2. **统计确定、不走模型**：`collect_stats` 只做 SQL 聚合，可被直接 SQL 交叉验证。

3. **溯源强制**：`summarize_day` 让模型产出的每条结论都带 `source_refs`，写入前校验
   每个 ref 都能指回当天该用户真实存在的 message id；指不回去（或没有 refs）的结论
   **直接丢弃并记日志**（对应 we/先进产品思路吸收.md:40 的「不可指回原文不许入库」）。

4. **总结只读**：不写会话、不改标题、不删任何东西。

5. **复用同一 SQLite**：新表 `daily_reports` / `delegations` 走 `core.session` 的同一份
   连接（同一库），不另起连接管理。建表按需、幂等、**不在导入期写库**。

MVP 说明（与 spec §3.1 的偏差，如实标注）
----------------------------------------
spec 写的是「按会话分块摘要（map）→ 合并（reduce）」的多轮调用。MVP 这里用**单次调用**
把当天对话整体交给模型产出结论数组，再统一做溯源过滤——长上下文已在 `_gather_day_text`
里按预算截断。map-reduce 的多轮拆分是长日（数百条消息）下的伸缩优化，本次先不实装，
避免把脆弱的多段 JSON 编排提前搬进核心；接口与落库结构不变，后续可直接升级。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass

from core import config, llm as llm_mod
from core import quota
from core import session as store

logger = logging.getLogger("service.daily")

# 预算常数（spec §3.1：单结果 15K 落盘 / 聚合 20K / 保留最近 10 轮）
_MAX_CHUNK_CHARS = 15000
_MAX_REDUCE_CHARS = 20000
_RECENT_TURNS = 10

_SYS_SUMMARY = (
    "你是每日总结助手。基于用户当天对话，提炼 1~4 条**可溯源**的结论。"
    "每条必须带 source_refs：引用对话里出现的消息编号（形如 [user#12] 里的数字 12）。"
    "只提炼对话里**确实发生**的事，不要编造。"
    "严格只输出一个 JSON 数组，元素形如 "
    '{"text": "结论文字", "source_refs": [12, 15]}，不要输出任何额外文字。'
)


# ==================== DB ====================

def _report_id(uid: str, date: str) -> str:
    return hashlib.md5(f"{uid}|{date}".encode()).hexdigest()[:12]


def _delegation_id(uid: str, name: str) -> str:
    return hashlib.md5(f"{uid}|deleg|{name}".encode()).hexdigest()[:12]


_daily_tables_ready = False


def _ensure_daily_tables() -> None:
    """懒建 daily_reports / delegations 两张表（复用 sessions.db 连接，幂等）。

    刻意不在模块导入时调用：避免导入即往真实 data/ 写库。
    """
    global _daily_tables_ready
    if _daily_tables_ready:
        return
    with store._get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                report_id    TEXT PRIMARY KEY,
                owner_id     TEXT NOT NULL,
                date         TEXT NOT NULL,
                status       TEXT NOT NULL,
                stats_json   TEXT,
                content_json TEXT,
                model        TEXT,
                generated_at INTEGER,
                read_at      INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_dr_owner_date ON daily_reports(owner_id, date);

            CREATE TABLE IF NOT EXISTS delegations (
                delegation_id  TEXT PRIMARY KEY,
                owner_id       TEXT NOT NULL,
                name           TEXT NOT NULL,
                kind           TEXT NOT NULL DEFAULT 'daily',
                schedule_json  TEXT,
                prompt         TEXT,
                deliver_sid    TEXT,
                kb_id          TEXT,
                tone           TEXT,
                max_chars      INTEGER,
                enabled        INTEGER NOT NULL DEFAULT 1,
                next_run_at    INTEGER,
                created_at     INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_del_owner ON delegations(owner_id);
        """)
    _daily_tables_ready = True


# ==================== 统计 ====================

def _day_bounds(date: str) -> tuple[int, int]:
    """本地时区的当天 [start, end) unix 秒。与 core.quota.day_key 的「本地日子」口径一致。"""
    t = time.strptime(date, "%Y-%m-%d")
    start = int(time.mktime(t))
    return start, start + 86400


def collect_stats(uid: str, date: str) -> dict:
    """统计某用户某天的对话情况，确定性、不走模型。

    返回 {date, conversation_count, message_count, user_msg_count,
          assistant_msg_count, active_hours, tool_call_count}。
    tool_call_count 以 assistant 消息数作代理（messages 表未单独存工具调用，
    见 issues/05-daily-summary.md 验收 #2 的口径约定）。
    """
    start, end = _day_bounds(date)
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT m.conversation_id, m.role, m.created_at FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            "WHERE c.owner_id = ? AND m.created_at >= ? AND m.created_at < ? "
            "ORDER BY m.id ASC",
            (uid, start, end),
        ).fetchall()

    conv_ids: set[str] = set()
    user_n = assistant_n = 0
    active_hours: dict[int, int] = {}
    for r in rows:
        conv_ids.add(r["conversation_id"])
        if r["role"] == "user":
            user_n += 1
        elif r["role"] == "assistant":
            assistant_n += 1
        h = time.localtime(r["created_at"]).tm_hour
        active_hours[h] = active_hours.get(h, 0) + 1

    return {
        "date": date,
        "conversation_count": len(conv_ids),
        "message_count": len(rows),
        "user_msg_count": user_n,
        "assistant_msg_count": assistant_n,
        "active_hours": active_hours,
        "tool_call_count": assistant_n,
    }


def _day_message_ids(uid: str, date: str) -> set[int]:
    """当天该用户真实存在的消息 id 白名单（溯源校验用）。"""
    start, end = _day_bounds(date)
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT m.id FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            "WHERE c.owner_id = ? AND m.created_at >= ? AND m.created_at < ?",
            (uid, start, end),
        ).fetchall()
    return {int(r["id"]) for r in rows}


# ==================== 每日总结 ====================

@dataclass
class Report:
    """一条每日总结。status: pending | generating | ready | empty。"""
    report_id: str
    owner_id: str
    date: str
    status: str
    stats: dict
    content: list
    model: str
    generated_at: int | None = None
    read_at: int | None = None


def _gather_day_text(uid: str, date: str) -> str:
    """把当天对话拼成带消息编号的文本（按预算截断）。"""
    start, end = _day_bounds(date)
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT m.id, m.role, m.content FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            "WHERE c.owner_id = ? AND m.created_at >= ? AND m.created_at < ? "
            "ORDER BY m.id ASC",
            (uid, start, end),
        ).fetchall()
    lines = []
    total = 0
    for r in rows:
        piece = f"[{r['role']}#{int(r['id'])}] {r['content']}"
        if total + len(piece) > _MAX_REDUCE_CHARS:
            lines.append("…（后续对话已截断）")
            break
        lines.append(piece)
        total += len(piece)
    return "\n".join(lines)


def _call_llm(llm, prompt: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = llm.invoke([SystemMessage(content=_SYS_SUMMARY), HumanMessage(content=prompt)])
    content = resp.content
    return content if isinstance(content, str) else str(content)


def _parse_conclusions(raw: str) -> list[dict]:
    """把模型输出解析成结论列表，容错（先整段 json，再退而求其次抓数组）。"""
    text = (raw or "").strip()
    data = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if isinstance(data, dict):
        data = data.get("conclusions") or data.get("items") or []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text_c = item.get("text") or item.get("summary") or ""
        refs = item.get("source_refs") or item.get("refs") or []
        if isinstance(refs, int):
            refs = [refs]
        if not isinstance(refs, list):
            refs = []
        refs = [int(x) for x in refs if str(x).isdigit()]
        out.append({"text": text_c, "source_refs": refs})
    return out


def _filter_by_traceability(conclusions: list[dict], whitelist: set[int]) -> tuple[list[dict], list[dict]]:
    """溯源强制：保留每条 refs 都指回真实消息的结论；其余丢弃。"""
    kept: list[dict] = []
    dropped: list[dict] = []
    for c in conclusions:
        refs = c.get("source_refs") or []
        if not refs:
            dropped.append(c)
            continue
        if all(r in whitelist for r in refs):
            kept.append(c)
        else:
            dropped.append(c)
    return kept, dropped


def _save_report(rep: Report) -> None:
    _ensure_daily_tables()
    with store._get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports "
            "(report_id, owner_id, date, status, stats_json, content_json, model, generated_at, read_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rep.report_id, rep.owner_id, rep.date, rep.status,
                json.dumps(rep.stats, ensure_ascii=False),
                json.dumps(rep.content, ensure_ascii=False),
                rep.model, rep.generated_at, rep.read_at,
            ),
        )


def _row_to_report(row) -> Report:
    return Report(
        report_id=row["report_id"],
        owner_id=row["owner_id"],
        date=row["date"],
        status=row["status"],
        stats=json.loads(row["stats_json"]) if row["stats_json"] else {},
        content=json.loads(row["content_json"]) if row["content_json"] else [],
        model=row["model"],
        generated_at=row["generated_at"],
        read_at=row["read_at"],
    )


def summarize_day(uid: str, date: str, stats: dict | None = None, llm=None) -> Report:
    """生成某用户某天的每日总结。

    流程：collect_stats →（无对话则落 empty 直接返回）放置 generating →
    调模型产结论 → 溯源过滤 → 落 ready。返回 Report。
    """
    _ensure_daily_tables()
    stats = stats or collect_stats(uid, date)
    generated_at = int(time.time())
    model = getattr(config, "DEEPSEEK_MODEL", "deepseek-chat")
    rid = _report_id(uid, date)

    if stats.get("message_count", 0) == 0:
        # 当天没对话：不调模型，避免空烧 token
        rep = Report(rid, uid, date, "empty", stats, [], model, generated_at)
        _save_report(rep)
        return rep

    rep = Report(rid, uid, date, "generating", stats, [], model, generated_at)
    _save_report(rep)  # pending -> generating 落盘，便于轮询

    if llm is None:
        llm = llm_mod.get_llm(temperature=0.2, max_tokens=2000)

    whitelist = _day_message_ids(uid, date)
    day_text = _gather_day_text(uid, date)
    raw = _call_llm(llm, day_text)
    conclusions = _parse_conclusions(raw)
    kept, dropped = _filter_by_traceability(conclusions, whitelist)
    for d in dropped:
        logger.warning(
            "丢弃无法溯源的结论(uid=%s date=%s): %s", uid, date, d.get("text")
        )

    rep.content = kept
    rep.status = "ready" if kept else "empty"
    _save_report(rep)
    return rep


def get_report(uid: str, date: str) -> Report | None:
    """取某用户某天的总结（不存在返回 None）。"""
    _ensure_daily_tables()
    rid = _report_id(uid, date)
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM daily_reports WHERE report_id = ? AND owner_id = ?",
            (rid, uid),
        ).fetchone()
    return _row_to_report(row) if row else None


def mark_read(uid: str, date: str) -> bool:
    """标记总结已读（更新 read_at）。"""
    _ensure_daily_tables()
    rid = _report_id(uid, date)
    now = int(time.time())
    with store._get_conn() as conn:
        cur = conn.execute(
            "UPDATE daily_reports SET read_at = ? WHERE report_id = ? AND owner_id = ?",
            (now, rid, uid),
        )
        return cur.rowcount > 0


# ==================== 每日委托 ====================

def save_delegation(
    uid: str,
    name: str,
    kind: str = "daily",
    schedule_json: dict | None = None,
    prompt: str = "",
    deliver_sid: str | None = None,
    kb_id: str | None = None,
    tone: str = "neutral",
    max_chars: int = 500,
    enabled: bool = True,
) -> dict:
    """新建或覆盖一条委托。返回存好的 dict。"""
    _ensure_daily_tables()
    now = int(time.time())
    did = _delegation_id(uid, name)
    with store._get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO delegations "
            "(delegation_id, owner_id, name, kind, schedule_json, prompt, deliver_sid, "
            " kb_id, tone, max_chars, enabled, next_run_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did, uid, name, kind,
                json.dumps(schedule_json, ensure_ascii=False) if schedule_json is not None else None,
                prompt, deliver_sid, kb_id, tone, max_chars,
                1 if enabled else 0, None, now, now,
            ),
        )
    return get_delegation(uid, did)  # type: ignore[return-value]


def get_delegation(uid: str, did: str) -> dict | None:
    _ensure_daily_tables()
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM delegations WHERE delegation_id = ? AND owner_id = ?",
            (did, uid),
        ).fetchone()
    return dict(row) if row else None


def list_delegations(uid: str) -> list[dict]:
    _ensure_daily_tables()
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM delegations WHERE owner_id = ? ORDER BY updated_at DESC",
            (uid,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_enabled(uid: str, did: str, enabled: bool) -> bool:
    _ensure_daily_tables()
    now = int(time.time())
    with store._get_conn() as conn:
        cur = conn.execute(
            "UPDATE delegations SET enabled = ?, updated_at = ? "
            "WHERE delegation_id = ? AND owner_id = ?",
            (1 if enabled else 0, now, did, uid),
        )
        return cur.rowcount > 0


def plan_today(delegation: dict, context: str = "", llm=None) -> str:
    """生成一条委托投递内容（纯文本）。delegation 来自 get_delegation 的 dict。

    只读：不写会话、不改 next_run_at（dry_run 语义由调用方决定）。
    """
    if llm is None:
        llm = llm_mod.get_llm(temperature=0.4, max_tokens=800)
    max_chars = int(delegation.get("max_chars") or 500)
    tone = delegation.get("tone") or "neutral"
    prompt = delegation.get("prompt") or ""
    sys_msg = (
        f"你是一条每日委托提醒，语气{tone}，严格控制在{max_chars}字以内。"
        f"基于上下文生成今日待办/提醒，不要编造上下文里没有的事实。"
    )
    user_msg = f"委托意图：{prompt}\n\n今日上下文：\n{context[:_MAX_REDUCE_CHARS]}"
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = llm.invoke([SystemMessage(content=sys_msg), HumanMessage(content=user_msg)])
    content = resp.content
    text = content if isinstance(content, str) else str(content)
    return text[:max_chars]


# ==================== Worker 钩子（被 worker/scheduler.py 按 job_key 调用）====================
# worker 的 _load_handlers 把 JOB_DEFS 里所有 key（含 report.daily / delegate.dispatch /
# report.backfill）都挂到本模块。下面三个是 worker 约定要的契约：
#   due_subjects(job_key, planned_at) -> list[str]   本轮到期的对象 id
#   run_one(job_key, subject_id, planned_at) -> dict 干一次活（可返回 {"tokens_out": int}）
#   on_failure(job_key, subject_id, fatal)           可选：不可重试失败时收尾
# 这套钩子本身**不调模型**（模型调用在 summarize_day / plan_today 里，已可注入）；
# 这里只负责「枚举对象 + 幂等键 + 配额闸门 + 投递落库」。

def _load_delegation(did: str) -> dict | None:
    """按 delegation_id 取委托（不过滤 owner——worker 只拿到 id）。"""
    _ensure_daily_tables()
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM delegations WHERE delegation_id = ?", (did,)
        ).fetchone()
    return dict(row) if row else None


def today_context(uid: str, date: str) -> str:
    """给委托投递/预览用的今日上下文：优先用当日总结的结论，否则空。"""
    rep = get_report(uid, date)
    if rep and rep.content:
        return "\n".join(f"- {c['text']}" for c in rep.content)
    return "（今日暂无总结）"


def due_subjects(job_key: str, planned_at: int) -> list[str]:
    """本轮到期的对象 id。

    - report.daily：当天有消息的真实用户（owner 非空）
    - delegate.dispatch：已启用且到点（next_run_at 为空或 ≤ now）的委托
    - 其余（report.backfill / housekeeping 等）：MVP 暂不实装，返回空
    """
    date = quota.day_key(planned_at)
    if job_key == "report.daily":
        start, end = _day_bounds(date)
        with store._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT c.owner_id FROM messages m "
                "JOIN conversations c ON m.conversation_id = c.id "
                "WHERE c.owner_id IS NOT NULL "
                "AND m.created_at >= ? AND m.created_at < ?",
                (start, end),
            ).fetchall()
        return [r["owner_id"] for r in rows]
    if job_key == "delegate.dispatch":
        with store._get_conn() as conn:
            rows = conn.execute(
                "SELECT delegation_id FROM delegations "
                "WHERE enabled = 1 AND (next_run_at IS NULL OR next_run_at <= ?)",
                (planned_at,),
            ).fetchall()
        return [r["delegation_id"] for r in rows]
    return []


def run_one(job_key: str, subject_id: str, planned_at: int) -> dict:
    """执行单个对象。配额闸门（auto）在这里卡：超限交给 worker 分类成 CLASS_QUOTA 延后。"""
    date = quota.day_key(planned_at)
    if job_key == "report.daily":
        quota.consume(subject_id, "auto")  # 每日自动任务烧的 token 走 auto 配额
        rep = summarize_day(subject_id, date)
        return {"tokens_out": 0, "status": rep.status}
    if job_key == "delegate.dispatch":
        return _dispatch_one(subject_id, planned_at)
    return {}


def _dispatch_one(did: str, planned_at: int) -> dict:
    d = _load_delegation(did)
    if not d or not d["enabled"]:
        return {}
    uid = d["owner_id"]
    quota.consume(uid, "auto")
    context = today_context(uid, quota.day_key(planned_at))
    text = plan_today(d, context)
    if d.get("deliver_sid"):
        # 投递目标会话若已不属于该用户，add_message 抛 NotOwned → 转 fatal → on_failure 停用
        store.add_message(uid, d["deliver_sid"], "assistant", text)
    _bump_next_run(did, planned_at + 86400)  # 默认次日同刻
    return {"tokens_out": 0}


def _bump_next_run(did: str, next_at: int) -> None:
    _ensure_daily_tables()
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE delegations SET next_run_at = ?, updated_at = ? WHERE delegation_id = ?",
            (next_at, int(time.time()), did),
        )


def on_failure(job_key: str, subject_id: str, fatal: bool) -> None:
    """不可重试失败时收尾：委托投递目标会话失效 → 停用该委托，避免每天重试同一错误。"""
    if job_key == "delegate.dispatch" and fatal:
        d = _load_delegation(subject_id)
        if d:
            set_enabled(d["owner_id"], subject_id, False)
