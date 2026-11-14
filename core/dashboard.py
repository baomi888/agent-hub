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

from core import api_labels, apilog, config, timeutil, user_feedback

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

    `purged` 是 2026-11-13 加的：区间内被删掉的账号数。那天用户看到
    「注册用户一小时前还是 177，现在 169」，以为真实用户流失 —— 实际是清理了
    8 个验证账号。只给 total 的话，这种解释需求每次都得翻库才能回答，
    所以在同一个响应里把「为什么掉」也带上。
    """
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at < ?",
            (start, end_excl),
        ).fetchone()[0]
        # 老库可能还没有 deleted_users（没跑过新版本启动），缺表按 0 处理，
        # 不能让一张统计用的辅助表把整个看板打成 500。
        try:
            purged = conn.execute(
                "SELECT COUNT(*) FROM deleted_users WHERE deleted_at >= ? AND deleted_at < ?",
                (start, end_excl),
            ).fetchone()[0]
        except Exception:  # noqa: BLE001 — 表不存在/结构不符都按"没有清理记录"
            purged = 0
    return {"total": int(total), "new": int(new), "purged": int(purged)}


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


# ==================== 真实用户使用数据（2026-11-15）====================
#
# 为什么单开一个视图，而不是给总览加个筛选开关
# ------------------------------------------------
# 总览回答的是「这台机器被调了多少次」，真实用户视图回答的是「有多少真人、
# 用得多深」。两者分母不同，混在一个开关里会出现「切了筛选，成功率也跟着变」——
# 那会让人以为在筛选数据，实际是在换统计对象。所以做成并列的两块，
# 各自带自己的口径标签，并由「口径对照」把两块**能对上账**：
#
#     全量调用 = 真实用户 + 内部账号 + 已注销/未知账号 + 未登录请求
#
# 四个桶必须互斥且穷尽，否则用户拿计算器一减就发现对不上，然后再也不信这个页面。
#
# 桶的判定（顺序即优先级，与 core/account.py 的注释一致）
# ------------------------------------------------------
#   anonymous —— uid 为空：未登录请求（401 探测、注册/登录本身、公开页）
#   orphan    —— uid 有值但 users 里查不到：已注销账号的历史调用
#   internal  —— is_internal = 1，或 uid 在 ADMIN_UIDS/ADMIN_USERNAMES 里
#   real      —— 其余
#
# 为什么管理员要单独算：管理员的调用里有看板刷新、审核、批量运维，
# 那不是"用户在用产品"。而且 `api/admin/*` 虽已在采集层被排除（见 apilog.EXCLUDE），
# 管理员打开首页、翻资料库同样会产生普通接口调用，不排掉就永远有一个假活跃用户。
#
# 为什么不按用户名猜测试账号：`gd67254984`、`ct67620262` 这类脚本生成的号长得像
# 机器，而 `12345`、`111`、`456` 是真人随手起的名字 —— 规则一写就会误伤，
# 而且误伤了没人发现。所以判定只认**显式标记**，猜测那一层放在
# `tools/mark_internal_users.py` 里，且默认 dry-run 要人确认。


def _admin_uids() -> list[str]:
    """管理员 uid 列表（ADMIN_UIDS 直取 + ADMIN_USERNAMES 反查）。

    每个请求查一次（一条按用户名索引的 SELECT，毫秒级）。不做进程内缓存：
    `.env` 改完重启才生效这件事已经够容易忘了，再叠一层缓存会让"我明明改了
    管理员名单"变成新的排查项。
    """
    ids = [x for x in (getattr(config, "ADMIN_UIDS", None) or ()) if x]
    names = [x for x in (getattr(config, "ADMIN_USERNAMES", None) or ()) if x]
    if names:
        from core import session as store

        with store._get_conn() as conn:  # noqa: SLF001
            marks = ",".join("?" * len(names))
            rows = conn.execute(
                "SELECT id FROM users WHERE username IN (" + marks + ")", names
            ).fetchall()
            ids.extend(str(r[0]) for r in rows)
    return list(dict.fromkeys(ids))


# 真实用户视角的 FROM/WHERE 前半段。四个聚合查询共用，避免各写一遍走样。
_REAL_FROM = "FROM api_call_logs al LEFT JOIN users u ON u.id = al.uid WHERE al.ts >= ? AND al.ts < ?"


def _real_where(admin_ids: list[str]) -> tuple[str, list]:
    """真实用户视角的额外条件（以 AND 开头，供拼在 `_REAL_FROM` 之后）。

    参数顺序固定为 (start, end, *admin_ids) —— 调用方按这个顺序给值。
    """
    frag = [
        "al.uid IS NOT NULL",
        "al.uid <> ''",
        # LEFT JOIN 没命中 = 账号已注销：不算"现在的用户"，
        # 但它的调用量会进 orphan 桶，所以账还是平的
        "u.id IS NOT NULL",
        "u.is_internal = 0",
    ]
    if admin_ids:
        frag.append("al.uid NOT IN (" + ",".join("?" * len(admin_ids)) + ")")
    return " AND " + " AND ".join(frag), list(admin_ids)


def _bucket_counts(conn, start: int, end_excl: int, admin_ids: list[str]) -> dict:
    """把区间内所有调用切成四个互斥的桶（见本节顶部注释）。"""
    admin_case = ""
    params: list = []
    if admin_ids:
        admin_case = " OR al.uid IN (" + ",".join("?" * len(admin_ids)) + ")"
        params = list(admin_ids)
    sql = (
        "SELECT CASE"
        "    WHEN al.uid IS NULL OR al.uid = '' THEN 'anonymous'"
        "    WHEN u.id IS NULL THEN 'orphan'"
        "    WHEN u.is_internal = 1" + admin_case + " THEN 'internal'"
        "    ELSE 'real' END AS bucket,"
        "  COUNT(*) AS calls"
        "  FROM api_call_logs al LEFT JOIN users u ON u.id = al.uid"
        "  WHERE al.ts >= ? AND al.ts < ?"
        "  GROUP BY bucket"
    )
    # 参数顺序必须跟 SQL 文本里 ? 出现的顺序一致：CASE 在前，WHERE 在后
    rows = conn.execute(sql, params + [start, end_excl]).fetchall()
    out = {"real": 0, "internal": 0, "orphan": 0, "anonymous": 0}
    for r in rows:
        out[str(r["bucket"])] = int(r["calls"] or 0)
    return out


def _real_series(conn, day_list: list[str], admin_ids: list[str]) -> tuple[list[dict], dict]:
    """逐日的「真实调用量 + 活跃真实用户数」，顺带算每个人活跃了几天。

    逐天查而不是一次 GROUP BY：与 `_series` 同一理由（见模块 docstring 第 2 条），
    而且这里还要顺带收集 `uid -> 活跃日集合`，用 Python 归并比写窗口函数清楚。
    30 天 × 几十个活跃用户，成本可以忽略。
    """
    frag, ap = _real_where(admin_ids)
    out: list[dict] = []
    days_by_uid: dict[str, set[str]] = {}
    for day in day_list:
        s, e = timeutil.day_bounds(day)
        rows = conn.execute(
            "SELECT al.uid AS uid, COUNT(*) AS n " + _REAL_FROM + frag + " GROUP BY al.uid",
            (s, e, *ap),
        ).fetchall()
        calls = 0
        for r in rows:
            n = int(r["n"] or 0)
            calls += n
            days_by_uid.setdefault(str(r["uid"]), set()).add(day)
        out.append({"day": day, "calls": calls, "users": len(rows)})
    return out, days_by_uid


def _real_by_feature(conn, start: int, end_excl: int, admin_ids: list[str], limit: int) -> list[dict]:
    """按功能（路由模板）聚合，但只算真实用户的调用。

    `users` 是这个功能被多少个**真实用户**用过 —— 比调用次数更能说明"是不是
    只有一个人在刷"。全量排行里没有这个数，因为那里 uid 可能为空。
    """
    frag, ap = _real_where(admin_ids)
    rows = conn.execute(
        "SELECT al.path AS path, al.method AS method, COUNT(*) AS calls,"
        "       COUNT(DISTINCT al.uid) AS users "
        + _REAL_FROM
        + frag
        + " GROUP BY al.path, al.method ORDER BY calls DESC, al.path ASC, al.method ASC LIMIT ?",
        (start, end_excl, *ap, limit),
    ).fetchall()
    return [
        {
            "path": str(x["path"]),
            "method": str(x["method"]),
            "label": api_labels.label_for(str(x["method"]), str(x["path"])),
            "calls": int(x["calls"] or 0),
            "users": int(x["users"] or 0),
        }
        for x in rows
    ]


def _real_by_user(
    conn, start: int, end_excl: int, admin_ids: list[str], days_by_uid: dict, limit: int
) -> list[dict]:
    """按账号聚合（真实用户榜）。

    显示 username 而不是 uid：看板是管理员看的，"谁在用"必须能对上人。
    昵称优先（用户自己设的显示名），没设过昵称就用登录名 —— 与全站一致。
    """
    frag, ap = _real_where(admin_ids)
    rows = conn.execute(
        "SELECT al.uid AS uid, COUNT(*) AS calls, COALESCE(MAX(al.ts), 0) AS last_ts,"
        "       u.username AS username, u.nickname AS nickname "
        + _REAL_FROM
        + frag
        + " GROUP BY al.uid ORDER BY calls DESC, last_ts DESC LIMIT ?",
        (start, end_excl, *ap, limit),
    ).fetchall()
    return [
        {
            "uid": str(x["uid"]),
            "username": str(x["nickname"] or x["username"] or ""),
            "calls": int(x["calls"] or 0),
            # 活跃天数来自逐日那趟的归并结果，不重复查一次
            "days": len(days_by_uid.get(str(x["uid"]), ())),
            "last_ts": int(x["last_ts"] or 0),
        }
        for x in rows
    ]


def _internal_accounts(conn, start: int, end_excl: int, limit: int = 30) -> tuple[list[dict], int]:
    """被标记的内部账号 + 它们在本区间的调用量（含 0 次）。

    为什么要列出 0 次的：标错的账号如果这区间没调用，就永远不出现在任何榜上，
    也就永远没办法在看板上取消标记 —— 必须给一个"全部已标记"的出口。
    """
    rows = conn.execute(
        "SELECT u.id AS uid, u.username AS username, u.nickname AS nickname,"
        "       COUNT(al.id) AS calls, COALESCE(MAX(al.ts), 0) AS last_ts"
        "  FROM users u"
        "  LEFT JOIN api_call_logs al ON al.uid = u.id AND al.ts >= ? AND al.ts < ?"
        " WHERE u.is_internal = 1"
        " GROUP BY u.id ORDER BY calls DESC, u.created_at DESC LIMIT ?",
        (start, end_excl, max(1, min(200, int(limit)))),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM users WHERE is_internal = 1").fetchone()[0]
    return (
        [
            {
                "uid": str(x["uid"]),
                "username": str(x["nickname"] or x["username"] or ""),
                "calls": int(x["calls"] or 0),
                "last_ts": int(x["last_ts"] or 0),
            }
            for x in rows
        ],
        int(total or 0),
    )


def _real_user_snapshot(conn, start: int, end_excl: int, admin_ids: list[str]) -> dict:
    """真实用户账号的快照口径（不随区间变化）。"""
    args: list = []
    clause = "WHERE is_internal = 0"
    if admin_ids:
        clause += " AND id NOT IN (" + ",".join("?" * len(admin_ids)) + ")"
        args = list(admin_ids)
    total = conn.execute("SELECT COUNT(*) FROM users " + clause, args).fetchone()[0]
    new = conn.execute(
        "SELECT COUNT(*) FROM users " + clause + " AND created_at >= ? AND created_at < ?",
        (*args, start, end_excl),
    ).fetchone()[0]
    return {"total": int(total or 0), "new": int(new or 0)}


def real_user_view(days: int = 7, limit: int = 20) -> dict:
    """真实用户使用数据（口径见字典 §2.8，接口见 §7.8）。

    一次返回四块：卡片指标 / 口径对照 / 逐日趋势 / 两个维度的明细
    （按功能、按账号）。前端用一次请求画整块，切维度不再请求 ——
    两个维度各 20 行，合起来也远小于一次往返的开销。
    """
    r = range_of(days)
    limit = max(1, min(50, int(limit)))
    apilog.ensure()
    admin_ids = _admin_uids()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        buckets = _bucket_counts(conn, r["start"], r["end_exclusive"], admin_ids)
        series, days_by_uid = _real_series(conn, r["days_list"], admin_ids)
        by_feature = _real_by_feature(conn, r["start"], r["end_exclusive"], admin_ids, limit)
        by_user = _real_by_user(
            conn, r["start"], r["end_exclusive"], admin_ids, days_by_uid, limit
        )
        internal_list, internal_total = _internal_accounts(
            conn, r["start"], r["end_exclusive"]
        )
        snap = _real_user_snapshot(conn, r["start"], r["end_exclusive"], admin_ids)

    real_calls = buckets["real"]
    total_calls = sum(buckets.values())
    active = len(days_by_uid)
    # 回访 = 在区间内至少两个自然日有调用。days=1 时恒为 0，
    # 前端要显示「今日算不出回访」而不是显示 0（0 会被读成"全是新来的一次性用户"）
    returning = sum(1 for d in days_by_uid.values() if len(d) >= 2)
    total_days = sum(len(d) for d in days_by_uid.values())
    return {
        "range": {
            "days": r["days"],
            "start": r["start"],
            "end": r["end"],
            "tz": r["tz"],
        },
        "calls": {
            "real": real_calls,
            # 占比的分子分母都摆出来：只给一个 3.2% 没人能核对，
            # 而"真实 1180 / 全量 36659"这种写法一眼就能验算
            "total": total_calls,
            "share": round(real_calls / total_calls, 4) if total_calls else None,
            "internal": buckets["internal"],
            "orphan": buckets["orphan"],
            "anonymous": buckets["anonymous"],
        },
        "users": {
            "active": active,
            "returning": returning,
            "single_day": active - returning,
            "per_user": round(real_calls / active, 1) if active else 0.0,
            "avg_days": round(total_days / active, 1) if active else 0.0,
            # 快照口径（不随区间变），与 `users.active` 不是一回事，UI 上要分开标
            "total": snap["total"],
            "new": snap["new"],
        },
        "internal": {
            # 标记总数 = 人工标记 + 管理员（管理员不需要标记，按配置即时排除）
            "accounts": internal_total + len(admin_ids),
            "marked": internal_total,
            "admins": len(admin_ids),
            "calls": buckets["internal"],
            "list": internal_list,
        },
        "series": series,
        "by_feature": by_feature,
        "by_user": by_user,
    }

