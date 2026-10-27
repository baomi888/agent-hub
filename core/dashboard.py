"""开发者数据看板的聚合查询（只读，纯 SQL）。

口径的唯一依据是 `docs/数据看板_2026-10-27/口径与数据字典.md`，
本模块每个函数的 docstring 都标了对应章节号 —— 改口径要两边一起改。

三个刻意的实现选择
------------------
1. **不加缓存**。文档承诺"准实时"，而缓存会引入"刷新了但数字没变"的困惑。
   600k 行 + 索引下 GROUP BY 是毫秒级，没必要为省这点 CPU 牺牲口径清晰。
2. **趋势图逐天查，不用 `(ts - start)/86400` 分组**。后者在无夏令时的
   Asia/Shanghai 上恰好正确，在有夏令时的时区会错一小时——
   正是 `core/timeutil.py` 开头批评的那种"当前等价所以先这样写"。
   30 次走索引的小查询（每次毫秒级）换口径绝对正确，这个交易划算。
3. **所有时间边界都走 `timeutil.day_bounds`**，禁止裸 `datetime` / `localdate`。

本模块不处理权限：谁能看由 `api/admin.py` 判定（文档 §6）。
"""

from __future__ import annotations

from core import apilog, api_labels, timeutil, user_feedback


def range_of(days: int = 7) -> dict:
    """把 `days` 解释成一个左闭右开的 Unix 秒区间（文档 §1）。

    `days=1` 是**今日自然日**（00:00 到次日 00:00），不是滚动 24 小时。
    `days=7` 是含今日的 7 个自然日。
    """
    days = max(1, min(90, int(days)))
    day_list = timeutil.recent_days(days)
    start = timeutil.day_bounds(day_list[0])[0]
    end_excl = timeutil.day_bounds(day_list[-1])[1]
    return {
        "days": days,
        "days_list": day_list,
        "start": start,
        "end_exclusive": end_excl,
        # 对外展示用的"区间终点"：最后一次有效秒
        "end": end_excl - 1,
        "tz": timeutil.TZ_NAME,
    }


# ==================== 总览 ====================


def _call_stats(start: int, end_excl: int) -> dict:
    """区间内的调用统计（文档 §2.3~2.5）。"""
    apilog.ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "       COALESCE(SUM(status < 400), 0) AS ok, "
            "       COALESCE(SUM(status >= 400), 0) AS failed, "
            "       COALESCE(AVG(dur_ms), 0) AS avg_ms, "
            "       COALESCE(MAX(dur_ms), 0) AS max_ms "
            "FROM api_call_logs WHERE ts >= ? AND ts < ?",
            (start, end_excl),
        ).fetchone()
    total = int(row["total"] or 0)
    ok = int(row["ok"] or 0)
    return {
        "total": total,
        "ok": ok,
        "failed": int(row["failed"] or 0),
        # 分母为 0 时返回 None（前端显示 —），不返回 0.0 假装算出个 0%
        "success_rate": round(ok / total, 4) if total else None,
        "avg_ms": int(row["avg_ms"] or 0),
        "max_ms": int(row["max_ms"] or 0),
    }


def _user_stats(start: int, end_excl: int) -> dict:
    """用户口径（文档 §2.1/2.2）。

    `total` 是**当前快照**，不随 days 变化；`new` 才是区间内的。
    两者必须在 UI 上区分标注，否则用户切到"今日"看总数没变会以为筛选坏了。
    """
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at < ?",
            (start, end_excl),
        ).fetchone()[0]
    return {"total": int(total), "new": int(new)}


def _series(day_list: list[str]) -> list[dict]:
    """按自然日的调用趋势（文档 §2.3）。

    逐天查而不是一次 GROUP BY：理由见模块 docstring 第 2 条。
    """
    apilog.ensure()
    from core import session as store

    out: list[dict] = []
    with store._get_conn() as conn:  # noqa: SLF001
        for day in day_list:
            s, e = timeutil.day_bounds(day)
            row = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(status >= 400), 0) AS bad "
                "FROM api_call_logs WHERE ts >= ? AND ts < ?",
                (s, e),
            ).fetchone()
            out.append({"day": day, "calls": int(row["n"] or 0), "failed": int(row["bad"] or 0)})
    return out


def overview(days: int = 7) -> dict:
    """看板首屏一次取齐（文档 §7.3）。"""
    r = range_of(days)
    return {
        "range": {
            "days": r["days"],
            "start": r["start"],
            "end": r["end"],
            "tz": r["tz"],
        },
        "users": _user_stats(r["start"], r["end_exclusive"]),
        "calls": _call_stats(r["start"], r["end_exclusive"]),
        # 反馈计数用**全量**（不受 days 影响）：它是当前待办队列，
        # "有多少条还没处理"跟时间筛选无关，筛了反而会漏掉积压的老反馈
        "feedback": user_feedback.counts(),
        "series": _series(r["days_list"]),
    }


# ==================== 接口排行 ====================

# 排序档位 → (ORDER BY 子句, HAVING 子句)
# 参数是白名单拼进去的，不是用户输入直拼 —— `sort` 先被 `_SORTS` 过滤过一道。
_SORTS = {
    "calls": ("calls DESC, path ASC, method ASC", ""),
    "slow": ("avg_ms DESC, calls DESC, path ASC", "HAVING COUNT(*) >= 3 "),
    "fail": ("failed DESC, calls DESC, path ASC", "HAVING SUM(status >= 400) > 0 "),
}


def api_ranking(days: int = 7, limit: int = 20, sort: str = "calls") -> dict:
    """按接口模板聚合（文档 §2.6）。

    `sort` 三档（口径都写进文档 §2.6）：
      - `calls`：调用次数降序 —— "在调什么"
      - `slow` ：平均耗时降序 —— "哪里慢"
      - `fail` ：失败次数降序 —— "哪里在报错"

    三档都带**第二个排序键**（path/method 字典序）。不加的话，并列项的
    顺序由 SQLite 决定，两次刷新会跳，看起来像数据在乱动。
    另外 `slow` 档带 `calls >= 3` 下限：只调用过 1 次且恰好很慢的接口
    （首次冷启动、建表）会把整个"最慢"榜占满，那不是性能问题。
    """
    r = range_of(days)
    limit = max(1, min(100, int(limit)))
    sort = sort if sort in _SORTS else "calls"
    order, having = _SORTS[sort]
    apilog.ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT path, method, COUNT(*) AS calls, "
            "       COALESCE(SUM(status >= 400), 0) AS failed, "
            "       CAST(COALESCE(AVG(dur_ms), 0) AS INTEGER) AS avg_ms, "
            "       COALESCE(MAX(dur_ms), 0) AS max_ms "
            "FROM api_call_logs WHERE ts >= ? AND ts < ? "
            "GROUP BY path, method "
            f"{having}"
            f"ORDER BY {order} LIMIT ?",
            (r["start"], r["end_exclusive"], limit),
        ).fetchall()
        total_paths = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM api_call_logs "
            "WHERE ts >= ? AND ts < ? GROUP BY path, method)",
            (r["start"], r["end_exclusive"]),
        ).fetchone()[0]
    return {
        "items": [
            {
                "path": str(x["path"]),
                "method": str(x["method"]),
                # 中文业务名：给非技术读者看的那一层。原始 path 仍然返回，
                # 前端以小字附在下面 —— 中文名回答"是什么功能"，路径回答"哪个接口"，
                # 两个都要，不能只留一个。
                "label": api_labels.label_for(str(x["method"]), str(x["path"])),
                "calls": int(x["calls"]),
                "failed": int(x["failed"] or 0),
                "avg_ms": int(x["avg_ms"] or 0),
                "max_ms": int(x["max_ms"] or 0),
            }
            for x in rows
        ],
        "total_paths": int(total_paths or 0),
        "sort": sort,
        "range": {"days": r["days"], "start": r["start"], "end": r["end"]},
    }

