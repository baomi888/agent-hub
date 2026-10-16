# -*- coding: utf-8 -*-
"""打卡业务逻辑：从 daily_reports 派生连续天数 / 历史，并维护分享标记。

设计要点（对齐 service/daily.py）：
  1. LLM 可注入：generate 直接复用 daily.summarize_day，llm 可选注入，测试不依赖真实 Key。
  2. 复用同一 SQLite（sessions.db）：新表 checkin_shares 懒建、幂等、**不在导入期写库**。
  3. owner 强制隔离：所有查询 WHERE owner_id = uid，与现有多用户隔离一致。
  4. 不引入竞争性激励（排行榜 / 连击奖励），与前端打卡页定位一致
     （前端注释明确：参考作业帮教训，竞争性激励是负体验）。

「打卡日」的判定口径（单一事实来源，前后端共用）：daily_reports.status = 'ready' 的那天。
这样「连续天数 / 圆点 / 历史」都从同一份服务端数据派生，不会与本地记录打架。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from core import quota
from core import session as store

# 一天秒数（本地日步进用；中国无夏令时，足够准确）
_DAY = 86400


def _ensure_checkin_tables() -> None:
    """懒建 checkin_shares 表（复用 sessions.db 连接，幂等）。"""
    with store._get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_shares (
                owner_id   TEXT NOT NULL,
                date       TEXT NOT NULL,
                shared_at  INTEGER,
                PRIMARY KEY (owner_id, date)
            )
            """
        )


def _ready_dates(uid: str) -> list[str]:
    """该用户所有「已生成总结」的日期（去重、升序）。这决定了哪些天算打卡。"""
    _ensure_checkin_tables()
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT date FROM daily_reports WHERE owner_id = ? AND status = 'ready'",
            (uid,),
        ).fetchall()
    return sorted({r["date"] for r in rows})


def _walk_backward(dates: set[str], start: str, max_days: int = 400) -> int:
    """从 start 往回数，连续几天在 dates 里。"""
    n = 0
    cur = datetime.date.fromisoformat(start)
    for _ in range(max_days):
        if cur.isoformat() not in dates:
            break
        n += 1
        cur -= datetime.timedelta(days=1)
    return n


def _longest_run(dates: set[str]) -> int:
    """最长连续天数：只在「区间起点」（前一天不在 dates）起数，避免重复计算。"""
    best = 0
    for d in sorted(dates):
        prev = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
        if prev in dates:
            continue
        n = 0
        cur = datetime.date.fromisoformat(d)
        for _ in range(400):
            if cur.isoformat() not in dates:
                break
            n += 1
            cur += datetime.timedelta(days=1)
        best = max(best, n)
    return best


def compute_streak(uid: str) -> dict[str, Any]:
    """连续天数 / 最长连续 / 已打卡日期集合。

    口径：今天还没打卡，则从昨天起算 —— 否则每天清晨打开都是 0 天，像在惩罚用户
    （与前端原 computeStreak 的「今天没记就从昨天起算」一致）。
    """
    dates = _ready_dates(uid)
    s = set(dates)
    today = quota.day_key()
    cursor = today if today in s else quota.day_key(int(__import__("time").time()) - _DAY)
    return {
        "dates": dates,
        "streak": _walk_backward(s, cursor),
        "longest": _longest_run(s),
    }


def list_history(uid: str, limit: int = 30) -> list[dict[str, Any]]:
    """近 limit 天打卡历史：[{date, points, shared}]。points = 当天结论条数。"""
    _ensure_checkin_tables()
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT date, content_json FROM daily_reports "
            "WHERE owner_id = ? AND status = 'ready' ORDER BY date DESC LIMIT ?",
            (uid, limit),
        ).fetchall()
        shares = {
            r["date"]
            for r in conn.execute(
                "SELECT date FROM checkin_shares WHERE owner_id = ?", (uid,)
            ).fetchall()
        }
    out: list[dict[str, Any]] = []
    for r in rows:
        content = json.loads(r["content_json"]) if r["content_json"] else []
        out.append({"date": r["date"], "points": len(content), "shared": r["date"] in shares})
    return out


def mark_shared(uid: str, date: str) -> bool:
    """标记某天已分享（幂等 upsert）。返回是否为首次分享。"""
    _ensure_checkin_tables()
    now = int(__import__("time").time())
    with store._get_conn() as conn:
        cur = conn.execute(
            "INSERT OR REPLACE INTO checkin_shares (owner_id, date, shared_at) "
            "VALUES (?, ?, ?)",
            (uid, date, now),
        )
    return cur.rowcount > 0
