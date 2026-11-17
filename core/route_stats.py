"""路由统计：workflow / agent 各占多少。

为什么必须新开一张表
--------------------
现有三处都答不了"路由到 workflow 的百分比"：

* `core/metrics.py` 的 `chat_total` 只按 `path`（hybrid / agent / rag / llm）分。
  而 `path` 是**执行分支**，不是**路由判定**：`plan.route == "agent"` 的请求
  同样可能进 hybrid 分支（工作流里 execute 节点跑 agent），反过来
  `route == "workflow"` 才映射成 `mode == "hybrid"`。只看 path 无法还原判定；
* `core/router.py` 只写 `log.info("route=...")`——进程日志、多 worker 各自一份、
  会被轮转，且非结构化，事后没法按天聚合；
* `api_call_logs` 没有 route / mode 列，而且所有问答都是同一个
  `POST /api/chat/stream`，从路径上区分不出来。

另外 `metrics` 是**进程内**的：`--workers 4` 就是 4 份互不相干的计数器，
`/api/metrics` 取到哪一份取决于这次请求落到哪个 worker，拿它算比例必然抖。
所以这里落 SQLite，与 `usage` / `rate_buckets` 同一个库、同一个 WAL。

写入成本：**每轮问答一次 UPSERT**（不是每 token），与 `quota.bump_usage`
同一量级；调用方负责包 `asyncio.to_thread`（本项目对 SQLite 的一贯纪律）。

口径（这三个字段是全部歧义的来源，先定死）
------------------------------------------
``mode``    实际执行分支：hybrid / agent / rag / llm。
``route``   路由**判定**：workflow / agent。只有真的问了模型才有值。
``source``  判定来源，形如 ``fields:model`` / ``fields:fallback`` /
            ``fields:fastpath:workflow_id`` / ``mode:rag``。

⚠️ 算"workflow 占比"时分母必须是**走路由的请求**（``source`` 以 ``:model``
或 ``:fallback`` 结尾），不能拿全部问答：fastpath（用户显式指定 workflow_id、
两个能力开关都关）根本没问模型，算进去得到的是"用户点了什么"，不是
"路由器倾向什么"。两个数都在 :func:`summary` 里给出，别混用。

为什么不复用 `usage` 表
-----------------------
`usage` 的语义是"按用户计的每日额度账本"，主键是 `(user_id, day, kind)`，
`quota.history()` / `snapshot()` 都按 `_LIMITS` 里的四种 kind 取数。
往里塞 `route:workflow` 这类非配额条目，将来谁做一次
`SELECT kind, SUM(count) FROM usage GROUP BY kind` 就会看到脏数据。
一张只读不写的旁表比"复用但污染语义"便宜得多。
"""

from __future__ import annotations

from core import session as _session
from core import timeutil
from core.session import _get_conn

# 上一次建表时的库路径：判断"这个库我已经建过了"。
# 必须跟着库路径走——测试与 e2e 会在运行时换库（AGENT_HUB_DB / monkeypatch
# _db_path），只在 import 时建一次的话，后换的库是空壳，现象是
# `no such table: route_stats`，且只在组合/全量跑时出现。
# 详见 core/quota.py 的 _ensure_tables 注释（同一个坑）。
_TABLES_PATH: str | None = None

# 路由判定可能的取值。workflow_no_def 是"判了 workflow 但没有工作流定义"的
# 降级事件，单独记一行，不计入分子分母，否则会把比例算成"路由器判错了"。
ROUTE_WORKFLOW = "workflow"
ROUTE_AGENT = "agent"
ROUTE_WF_NO_DEF = "workflow_no_def"
_ROUTES = (ROUTE_WORKFLOW, ROUTE_AGENT, ROUTE_WF_NO_DEF)

# source 里表示"真的问过路由模型"的后缀。core/router.py 的 decide() 只有
# model / fallback 两种结果，fastpath 与旧 mode 直通都不会到这里。
_ROUTED_SUFFIXES = (":model", ":fallback")


def _ensure_tables(force: bool = False) -> None:
    """建 route_stats（幂等；换了库路径会重跑）。"""
    global _TABLES_PATH
    path = _session._db_path()
    if not force and path == _TABLES_PATH:
        return
    with _get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS route_stats (
                day    TEXT NOT NULL,
                mode   TEXT NOT NULL,
                route  TEXT NOT NULL,
                source TEXT NOT NULL,
                n      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, mode, route, source)
            );
            -- 报告只按天取，一个范围扫描就够；索引是给"按 day 范围 + 排序"用的
            CREATE INDEX IF NOT EXISTS idx_route_stats_day ON route_stats(day);
            """
        )
    _TABLES_PATH = path


_ensure_tables()


def record(mode: str, route: str = "", source: str = "", day: str | None = None) -> None:
    """记一轮问答的去向（幂等累加，一天一行）。

    **任何异常都吞掉**：统计不该让业务请求失败——与 `apilog.flush`、
    `quota._write_bucket` 同一个原则。

    ``mode`` 空串也照记（正常不会出现），这样"总数"不会因为某个分支忘了
    传参而悄悄少一块；报告里它会显示成 ``(空)``，反而能暴露接线漏了。

    ``day`` 只给**补数与测试**用（形如 ``2026-11-17``）。生产调用方一律不传，
    走业务时区的"今天"——传参就有"写错一天"的可能，默认路径不该留这个口子。
    """
    try:
        _ensure_tables()
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO route_stats (day, mode, route, source, n) VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT(day, mode, route, source) DO UPDATE SET n = n + 1",
                (
                    str(day or timeutil.day_str()),
                    str(mode or ""),
                    str(route or ""),
                    str(source or ""),
                ),
            )
    except Exception:  # noqa: BLE001 —— 统计失败静默
        pass


def is_routed(source: str) -> bool:
    """这个 source 是否表示"真的问了路由模型"。

    只有 ``...:model`` / ``...:fallback`` 算。fastpath（显式 workflow_id、
    两个开关都关）与旧 mode 直通都不算——它们没问过模型。
    """
    s = str(source or "")
    return any(s.endswith(x) for x in _ROUTED_SUFFIXES)


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def summary(days: int = 7, end: str | None = None) -> dict:
    """近 N 天的路由分布。

    返回结构（字段名即口径，避免调用方自己再算一遍算错）：

    ``total``          全部问答轮次（含没走路由的）
    ``routed``         真的问了路由模型的轮次（分母）
    ``workflow``       判 workflow 的轮次（分子）
    ``agent``          判 agent 的轮次
    ``workflow_pct``   workflow / routed          ← 路由器倾向
    ``workflow_pct_all`` workflow / total         ← 占全站问答
    ``fallback``       source 为 :fallback 的轮次
    ``fallback_pct``   fallback / routed
    ``no_def``         判了 workflow 但缺工作流定义而降级的轮次
    ``by_day``         逐日明细（升序，缺失日补零）
    ``by_mode`` / ``by_source``  交叉分布，排障用
    """
    keys = timeutil.recent_days(days, end=end)
    since = keys[0]
    # 查询前也要确保表存在：只写不读的时序下（全新库第一次就查）record 还没跑过，
    # 直接 SELECT 会抛 `no such table: route_stats`。
    _ensure_tables()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT day, mode, route, source, n FROM route_stats WHERE day >= ?",
            (since,),
        ).fetchall()

    total = routed = wf = ag = fb = no_def = 0
    by_mode: dict[str, int] = {}
    by_source: dict[str, int] = {}
    per_day = {d: {"day": d, "total": 0, "routed": 0, "workflow": 0} for d in keys}

    for r in rows:
        n = int(r["n"] or 0)
        mode = str(r["mode"] or "")
        route = str(r["route"] or "")
        source = str(r["source"] or "")
        total += n
        by_mode[mode] = by_mode.get(mode, 0) + n
        by_source[source] = by_source.get(source, 0) + n
        # 逐日 total 必须在这里先加：下面几个分支都会 continue，
        # 放到分支里会漏掉 workflow_no_def，导致"逐日合计 ≠ 全局合计"。
        slot = per_day.get(r["day"])
        if slot is not None:
            slot["total"] += n

        if route == ROUTE_WF_NO_DEF:
            # 判了 workflow 但没有工作流定义：部署缺数据，不是路由偏好。
            # 单记一档，既不当分子也不当分母。
            no_def += n
            continue
        if route not in (ROUTE_WORKFLOW, ROUTE_AGENT) or not is_routed(source):
            # 没走路由（旧 mode 直通 / fastpath）：只进 total，不进 routed
            continue

        routed += n
        if route == ROUTE_WORKFLOW:
            wf += n
        else:
            ag += n
        if source.endswith(":fallback"):
            fb += n
        if slot is not None:
            slot["routed"] += n
            if route == ROUTE_WORKFLOW:
                slot["workflow"] += n

    by_day = []
    for d in keys:
        s = per_day[d]
        s["workflow_pct"] = _pct(s["workflow"], s["routed"])
        by_day.append(s)

    return {
        "days": len(keys),
        "since": keys[0] if keys else "",
        "until": keys[-1] if keys else "",
        "total": total,
        "routed": routed,
        "workflow": wf,
        "agent": ag,
        "workflow_pct": _pct(wf, routed),
        "workflow_pct_all": _pct(wf, total),
        "fallback": fb,
        "fallback_pct": _pct(fb, routed),
        "no_def": no_def,
        "by_day": by_day,
        "by_mode": dict(sorted(by_mode.items(), key=lambda kv: -kv[1])),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
    }


def reset() -> None:
    """清空统计（仅测试 / 明确要重算时用）。"""
    _ensure_tables()
    with _get_conn() as conn:
        conn.execute("DELETE FROM route_stats")
