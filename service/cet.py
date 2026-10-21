# -*- coding: utf-8 -*-
"""四六级备考工作区的业务逻辑。

为什么单独建一个 service：
  申报书里写「四六级备考模块已落地」，但此前实现只有「模板卡片 + 对话话题词」，
  没有可点进去的模块。这里把三件事沉淀成真实数据，而不是每次让模型重新生成：
    1. 54 天备考计划 —— 计划一旦生成即落库，任务可勾选、进度可追踪（不再是一次性文本）；
    2. 词汇本 —— 错词按 SRS（1/3/7/15/30 天）间隔复习，复习记录落库；
    3. 模考分数 —— 记录每次模考/真考分数，画出趋势与目标差距。

设计对齐 service/checkin.py：
  - 复用同一个 SQLite（sessions.db），新表懒建、幂等、**不在导入期写库**；
  - owner 强制隔离：所有查询 WHERE owner_id = uid；
  - 不引入排行榜/竞争性激励（与打卡页同一取舍）。
"""

from __future__ import annotations

import datetime
import time
import uuid
from typing import Any

from core import quota
from core import session as store

# 每日五个模块（与申报书 B3「听力、词汇、阅读、写作翻译、真题五个模块排入每日任务」一致）
_MODULES: list[tuple[str, str]] = [
    ("听力", "听力同义改写 第 {day} 组（每日 10–15 组）"),
    ("词汇", "高频词 30 个 + 昨日错词复盘"),
    ("阅读", "真题阅读 2 篇 + 长难句 3 句"),
    ("写作翻译", "作文 1 篇 或 翻译 1 段（二选一）"),
    ("真题", "真题限时 1 套（按套推进）"),
]

# SRS 间隔（天）：答对前进一档，答错回到第 1 档
_SRS_STEPS = [1, 3, 7, 15, 30]


def _ensure_cet_tables() -> None:
    """懒建四张表（复用 sessions.db 连接，幂等）。"""
    with store._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cet_plans (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL,
                start_date  TEXT NOT NULL,
                exam_date   TEXT,
                total_days  INTEGER NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cet_plans_owner ON cet_plans(owner_id, created_at);

            CREATE TABLE IF NOT EXISTS cet_tasks (
                id          TEXT PRIMARY KEY,
                plan_id     TEXT NOT NULL,
                owner_id    TEXT NOT NULL,
                day_index   INTEGER NOT NULL,
                task_date   TEXT NOT NULL,
                module      TEXT NOT NULL,
                title       TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                done_at     INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_cet_tasks_owner_date ON cet_tasks(owner_id, task_date);

            CREATE TABLE IF NOT EXISTS vocab_items (
                id           TEXT PRIMARY KEY,
                owner_id     TEXT NOT NULL,
                word         TEXT NOT NULL,
                meaning      TEXT,
                source       TEXT,
                step         INTEGER NOT NULL DEFAULT 0,
                interval_days INTEGER NOT NULL DEFAULT 1,
                next_review  TEXT NOT NULL,
                wrong_count  INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL,
                last_review  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_vocab_owner_due ON vocab_items(owner_id, next_review);

            CREATE TABLE IF NOT EXISTS cet_scores (
                id         TEXT PRIMARY KEY,
                owner_id   TEXT NOT NULL,
                exam_type  TEXT NOT NULL,
                score      INTEGER NOT NULL,
                target     INTEGER,
                taken_at   TEXT NOT NULL,
                note       TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cet_scores_owner ON cet_scores(owner_id, taken_at);
            """
        )


def _add_days(date_str: str, n: int) -> str:
    return (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- 计划


def create_plan(
    uid: str,
    title: str = "六级 54 天备考计划",
    exam_date: str | None = None,
    days: int = 54,
    start_date: str | None = None,
) -> dict[str, Any]:
    """生成一份备考计划并落库（每天 5 个模块任务）。

    计划是**确定性生成**的：不调用大模型，因此同一输入结果稳定、可测试，
    也不消耗 token。模型的角色是「解释与调整」，不是「每次重新造计划」。
    """
    _ensure_cet_tables()
    days = max(7, min(int(days), 180))  # 夹逼，避免误传 10000 天把库写爆
    start = start_date or quota.day_key()
    if exam_date is None:
        exam_date = _add_days(start, days - 1)
    plan_id = uuid.uuid4().hex
    now = int(time.time())
    rows: list[tuple] = []
    for i in range(days):
        d = _add_days(start, i)
        for module, tpl in _MODULES:
            rows.append(
                (uuid.uuid4().hex, plan_id, uid, i + 1, d, module, tpl.format(day=i + 1), 0, None)
            )
    with store._get_conn() as conn:
        conn.execute(
            "INSERT INTO cet_plans (id, owner_id, title, start_date, exam_date, total_days, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan_id, uid, title, start, exam_date, days, now),
        )
        conn.executemany(
            "INSERT INTO cet_tasks (id, plan_id, owner_id, day_index, task_date, module, title, done, done_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return {"id": plan_id, "title": title, "start_date": start, "exam_date": exam_date, "total_days": days}


def _latest_plan(conn, uid: str) -> Any:
    return conn.execute(
        "SELECT * FROM cet_plans WHERE owner_id = ? ORDER BY created_at DESC LIMIT 1", (uid,)
    ).fetchone()


def _streak_days(conn, uid: str) -> int:
    """连续学习天数：某天只要有任意任务被勾选，就算学了一天。"""
    rows = conn.execute(
        "SELECT DISTINCT task_date FROM cet_tasks WHERE owner_id = ? AND done = 1", (uid,)
    ).fetchall()
    dates = {r["task_date"] for r in rows}
    if not dates:
        return 0
    today = quota.day_key()
    cursor = today if today in dates else _add_days(today, -1)
    n = 0
    for _ in range(400):
        if cursor not in dates:
            break
        n += 1
        cursor = _add_days(cursor, -1)
    return n


def overview(uid: str) -> dict[str, Any]:
    """工作区首屏所需的全部数据（一次请求返回，减少移动端往返）。"""
    _ensure_cet_tables()
    today = quota.day_key()
    with store._get_conn() as conn:
        plan = _latest_plan(conn, uid)
        if plan is None:
            return {
                "plan": None,
                "progress": {"done": 0, "total": 0, "percent": 0},
                "today_tasks": [],
                "streak": 0,
                "vocab_due": 0,
                "scores": [],
                "today": today,
            }
        agg = conn.execute(
            "SELECT COUNT(*) AS total, SUM(done) AS done FROM cet_tasks WHERE plan_id = ?",
            (plan["id"],),
        ).fetchone()
        total = agg["total"] or 0
        done = agg["done"] or 0
        today_rows = conn.execute(
            "SELECT id, module, title, done FROM cet_tasks "
            "WHERE owner_id = ? AND task_date = ? ORDER BY rowid",
            (uid, today),
        ).fetchall()
        vocab_due = conn.execute(
            "SELECT COUNT(*) AS n FROM vocab_items WHERE owner_id = ? AND next_review <= ?",
            (uid, today),
        ).fetchone()["n"]
        scores = [
            dict(r)
            for r in conn.execute(
                "SELECT id, exam_type, score, target, taken_at, note FROM cet_scores "
                "WHERE owner_id = ? ORDER BY taken_at ASC LIMIT 30",
                (uid,),
            ).fetchall()
        ]
        return {
            "plan": {
                "id": plan["id"],
                "title": plan["title"],
                "start_date": plan["start_date"],
                "exam_date": plan["exam_date"],
                "total_days": plan["total_days"],
            },
            "progress": {
                "done": done,
                "total": total,
                "percent": round(done * 100 / total) if total else 0,
            },
            "today_tasks": [dict(r) for r in today_rows],
            "streak": _streak_days(conn, uid),
            "vocab_due": vocab_due,
            "scores": scores,
            "today": today,
        }


def toggle_task(uid: str, task_id: str, done: bool) -> bool:
    """勾选/取消某条任务。归属不符返回 False（上层转 404）。"""
    _ensure_cet_tables()
    with store._get_conn() as conn:
        cur = conn.execute(
            "UPDATE cet_tasks SET done = ?, done_at = ? WHERE id = ? AND owner_id = ?",
            (1 if done else 0, int(time.time()) if done else None, task_id, uid),
        )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- 词汇本


def list_vocab(uid: str, due_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    _ensure_cet_tables()
    today = quota.day_key()
    sql = "SELECT * FROM vocab_items WHERE owner_id = ?"
    args: list[Any] = [uid]
    if due_only:
        sql += " AND next_review <= ?"
        args.append(today)
    sql += " ORDER BY next_review ASC, created_at DESC LIMIT ?"
    args.append(limit)
    with store._get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def add_vocab(uid: str, word: str, meaning: str = "", source: str = "") -> dict[str, Any]:
    """加入词汇本。同一 owner 下重复单词不新建，只把下次复习提前到今天。"""
    _ensure_cet_tables()
    word = word.strip()
    if not word:
        raise ValueError("word 不能为空")
    today = quota.day_key()
    now = int(time.time())
    with store._get_conn() as conn:
        old = conn.execute(
            "SELECT id FROM vocab_items WHERE owner_id = ? AND word = ?", (uid, word)
        ).fetchone()
        if old:
            conn.execute(
                "UPDATE vocab_items SET next_review = ?, meaning = COALESCE(NULLIF(?, ''), meaning) "
                "WHERE id = ? AND owner_id = ?",
                (today, meaning, old["id"], uid),
            )
            return {"id": old["id"], "word": word, "existed": True}
        vid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO vocab_items (id, owner_id, word, meaning, source, step, interval_days, "
            "next_review, wrong_count, created_at, last_review) VALUES (?, ?, ?, ?, ?, 0, 1, ?, 0, ?, NULL)",
            (vid, uid, word, meaning, source, today, now),
        )
    return {"id": vid, "word": word, "existed": False}


def review_vocab(uid: str, item_id: str, remembered: bool) -> dict[str, Any] | None:
    """复习一张词卡：记得则进入下一档间隔，忘记则回到第 1 档并计一次错。"""
    _ensure_cet_tables()
    today = quota.day_key()
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT step, wrong_count FROM vocab_items WHERE id = ? AND owner_id = ?",
            (item_id, uid),
        ).fetchone()
        if row is None:
            return None
        step = int(row["step"])
        if remembered:
            step = min(step + 1, len(_SRS_STEPS) - 1)
            wrong = int(row["wrong_count"])
        else:
            step = 0
            wrong = int(row["wrong_count"]) + 1
        interval = _SRS_STEPS[step]
        conn.execute(
            "UPDATE vocab_items SET step = ?, interval_days = ?, next_review = ?, "
            "wrong_count = ?, last_review = ? WHERE id = ? AND owner_id = ?",
            (step, interval, _add_days(today, interval), wrong, today, item_id, uid),
        )
    return {"id": item_id, "step": step, "interval_days": interval, "next_review": _add_days(today, interval)}


# --------------------------------------------------------------------------- 分数


def list_scores(uid: str) -> list[dict[str, Any]]:
    _ensure_cet_tables()
    with store._get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, exam_type, score, target, taken_at, note FROM cet_scores "
                "WHERE owner_id = ? ORDER BY taken_at ASC",
                (uid,),
            ).fetchall()
        ]


def add_score(
    uid: str,
    exam_type: str,
    score: int,
    target: int | None = None,
    taken_at: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    _ensure_cet_tables()
    if score < 0 or score > 750:
        raise ValueError("score 应在 0–750 之间")
    sid = uuid.uuid4().hex
    now = int(time.time())
    taken = taken_at or quota.day_key()
    with store._get_conn() as conn:
        conn.execute(
            "INSERT INTO cet_scores (id, owner_id, exam_type, score, target, taken_at, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, uid, exam_type, score, target, taken, note, now),
        )
    return {"id": sid, "exam_type": exam_type, "score": score, "target": target, "taken_at": taken}


def delete_score(uid: str, score_id: str) -> bool:
    _ensure_cet_tables()
    with store._get_conn() as conn:
        cur = conn.execute("DELETE FROM cet_scores WHERE id = ? AND owner_id = ?", (score_id, uid))
    return cur.rowcount > 0
