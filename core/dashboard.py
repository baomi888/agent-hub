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

from core import api_labels, apilog, timeutil, user_feedback

# ---------- 失败口径（唯一一处定义，别在 SQL 里各写一遍） ----------
#
# 内测第一天实测：183 次"失败"里 178 是 401、17 是 /api/report 的 404，
# 真正的故障（5xx）是 0。两类都不是"系统报错"：
#   · 401  = 打开页面还没登录，前端探测 /api/auth/me —— 预期行为
#   · 404 on /api/report/*  = 今天还没跑过对话，总结自然不存在 —— 业务空态
# 所以「失败」只留**需要人处理**的：401 之外的状态码 ≥400，且报告空态除外。
_FAILED_SQL = "(status >= 400 AND status != 401 AND NOT (status = 404 AND path LIKE '/api/report/%'))"
_EXPECTED_SQL = f"(status >= 400 AND NOT {_FAILED_SQL})"


def explain_status(status: int, path: str = "", method: str = "") -> tuple[str, bool]:
    """把一条失败翻成人话：返回 (原因, 是否可忽略)。

    看板要能直接回答"这条为什么失败"，否则每次都得 ssh 上去 grep 日志
    （用户 2026-10-28 的原话）。`expected=True` 表示预期行为，不用管。
    """
    p = path or ""
    if status == 401:
        return "未登录：打开页面时的身份探测，属于预期行为", True
    if status == 404 and p.startswith("/api/report/"):
        return "今天还没跑完一轮对话，总结尚未生成（空态）", True
    if status == 400 and "auth/register" in p:
        return "注册参数不合格：用户名 3~32 字符、密码 6~128 位", False
    if status == 409 and "auth/register" in p:
        return "该用户名已被占用，用户换个名字即可", False
    if status == 404:
        return "接口不存在：前端可能请求了错误的地址", False
    if status == 400:
        return "参数校验未通过（请求内容不符合接口要求）", False
    if status == 403:
        return "无权限：账号被停用或功能开关关闭", False
    if status == 422:
        return "参数格式不对（缺字段或类型不符）", False
    if status == 429:
        return "触发限流：同一账号短时间内请求过多", False
    if 500 <= status < 600:
        return "服务端异常：需要看 journalctl 定位堆栈", False
    return "其他失败，建议看日志确认", False


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
            f"       COALESCE(SUM({_EXPECTED_SQL}), 0) AS expected, "
            f"       COALESCE(SUM({_FAILED_SQL}), 0) AS failed, "
            "       COALESCE(AVG(dur_ms), 0) AS avg_ms, "
            "       COALESCE(MAX(dur_ms), 0) AS max_ms "
            "FROM api_call_logs WHERE ts >= ? AND ts < ?",
            (start, end_excl),
        ).fetchone()
    total = int(row["total"] or 0)
    ok = int(row["ok"] or 0)
    expected = int(row["expected"] or 0)
    denom = total - expected
    return {
        "total": total,
        "ok": ok,
        # expected = 401 未登录探测 + 报告空态 404。可忽略，但要说出来，
        # 否则"总数 ≠ ok + failed"会让人以为数字对不上
        "expected": expected,
        "failed": int(row["failed"] or 0),
        # 分母为 0 时返回 None（前端显示 —），不返回 0.0 假装算出个 0%
        "success_rate": round(ok / denom, 4) if denom else None,
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
                f"SELECT COUNT(*) AS n, COALESCE(SUM({_FAILED_SQL}), 0) AS bad "
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
    "fail": ("failed DESC, calls DESC, path ASC", f"HAVING SUM({_FAILED_SQL}) > 0 "),
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
            f"       COALESCE(SUM({_FAILED_SQL}), 0) AS failed, "
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


# ==================== 失败明细（带人话原因） ====================


def failures(days: int = 7, limit: int = 20) -> dict:
    """区间内最近的失败调用 + 中文原因，让看板自己回答"为什么失败"。

    分两栏给：`need_attention`（要处理的）和 `ignorable`（预期行为，如未登录
    探测、报告空态）。**不做成"全部混在一起"**：混着看，17 条预期行为会把
    1 条真故障淹掉 —— 这正是内测第一天"183 次报错"吓人的原因。

    另外按 (路径, 状态码) 聚合出 `top`，用来回答"哪类错最多、要修哪一个"。
    """
    r = range_of(days)
    limit = max(1, min(100, int(limit)))
    apilog.ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT ts, path, method, status, dur_ms FROM api_call_logs "
            "WHERE ts >= ? AND ts < ? AND status >= 400 "
            # rowid 做第二排序键：ts 是秒级，同一秒常常有上百条失败，
            # 只按 ts 排会把"刚刚那条 404"随机挤出前 20（实测踩到过）
            "ORDER BY ts DESC, rowid DESC LIMIT ?",
            (r["start"], r["end_exclusive"], limit),
        ).fetchall()
        agg = conn.execute(
            "SELECT path, method, status, COUNT(*) AS n FROM api_call_logs "
            "WHERE ts >= ? AND ts < ? AND status >= 400 "
            "GROUP BY path, method, status ORDER BY n DESC LIMIT ?",
            (r["start"], r["end_exclusive"], limit),
        ).fetchall()

    def item(row, with_ts: bool) -> dict:
        path, method, status = str(row["path"]), str(row["method"]), int(row["status"])
        hint, expected = explain_status(status, path, method)
        out = {
            "path": path,
            "method": method,
            "status": status,
            "label": api_labels.label_for(method, path),
            "hint": hint,
            "expected": expected,
        }
        if with_ts:
            ts = int(row["ts"])
            out["ts"] = ts
            out["day"] = timeutil.day_str(ts)
            out["dur_ms"] = int(row["dur_ms"] or 0)
        else:
            out["calls"] = int(row["n"] or 0)
        return out

    recent = [item(x, True) for x in rows]
    return {
        "days": r["days"],
        "recent": recent,
        "need_attention": [x for x in recent if not x["expected"]],
        "ignorable": [x for x in recent if x["expected"]],
        "top": [item(x, False) for x in agg],
        "range": {"days": r["days"], "start": r["start"], "end": r["end"]},
    }

