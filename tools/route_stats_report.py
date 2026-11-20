#!/usr/bin/env python
"""路由分布报告：workflow / agent 各占多少。

回答的是一个具体问题：**这一轮请求，路由器判 workflow 的比例是多少**。
数据来自 `route_stats` 表（每轮问答一次 UPSERT，多 worker 汇总到同一个
SQLite），不是 `/api/metrics` 的进程内计数器。

用法
----
    # 本机（读本地库）
    python tools/route_stats_report.py

    # 线上：先把库拉下来，再离线看（脚本**只读**，不会改生产库）
    scp root@host:/srv/agent-hub/data/sessions.db ./sessions.db
    python tools/route_stats_report.py --db ./sessions.db --days 7

    # 机器可读
    python tools/route_stats_report.py --json

    # 只看逐日
    python tools/route_stats_report.py --by-day

三个百分比别混用（报告头部会把它们分开写）
------------------------------------------
  workflow_pct      判 workflow / **走了路由的**   ← "路由器倾向"，通常看这个
  workflow_pct_all  判 workflow / 全部问答         ← 会被 fastpath 稀释
  fallback_pct      路由兜底 / 走了路由的          ← 越高说明路由模型越不稳

分母不含 fastpath：用户显式指定 workflow_id、或两个能力开关都关的时候，
压根没问模型，算进去得到的是"用户点了什么"，不是"路由器倾向什么"。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _default_db() -> str:
    return os.path.join(_REPO, "data", "sessions.db")


def _connect_ro(path: str) -> sqlite3.Connection:
    """**只读**打开。报告工具不该有任何写生产库的可能。

    用 URI 的 mode=ro：即使库文件权限允许写，SQLite 也会拒绝一切写操作。
    文件不存在时报一条人能看懂的话，而不是抛 sqlite3 的裸异常。
    """
    if not os.path.exists(path):
        raise SystemExit(f"数据库不存在：{path}\n先确认路径，或用 --db 指定")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='route_stats'"
    ).fetchone()
    return row is not None


def _collect(conn: sqlite3.Connection, days: int) -> dict:
    """与 core.route_stats.summary 同一套口径，但独立实现。

    为什么不在工具里 import 项目模块：这个脚本要能在**只有 DB 副本**的机器上跑
    （没装依赖、没 .env）。所以口径在这里重写一遍——代价是两处要同步，
    因此下面的百分比字段名与 summary() 完全一致，改口径时两处一起改。
    """
    from datetime import date, timedelta

    today = date.today()
    keys = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    since = keys[0]

    rows = conn.execute(
        "SELECT day, mode, route, source, n FROM route_stats WHERE day >= ?", (since,)
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

        slot = per_day.get(r["day"])
        # 逐日 total 必须在分支之前加：下面有 continue，放分支里会漏掉
        # workflow_no_def，出现"逐日合计 ≠ 全局合计"。
        if slot is not None:
            slot["total"] += n

        if route == "workflow_no_def":
            no_def += n
            continue
        routed_here = route in ("workflow", "agent") and (
            source.endswith(":model") or source.endswith(":fallback")
        )
        if not routed_here:
            continue

        routed += n
        if route == "workflow":
            wf += n
        else:
            ag += n
        if source.endswith(":fallback"):
            fb += n
        if slot is not None:
            slot["routed"] += n
            if route == "workflow":
                slot["workflow"] += n

    def pct(part: int, whole: int) -> float:
        return round(part / whole * 100, 1) if whole else 0.0

    for s in per_day.values():
        s["workflow_pct"] = pct(s["workflow"], s["routed"])

    return {
        "since": keys[0],
        "until": keys[-1],
        "days": days,
        "total": total,
        "routed": routed,
        "workflow": wf,
        "agent": ag,
        "workflow_pct": pct(wf, routed),
        "workflow_pct_all": pct(wf, total),
        "fallback": fb,
        "fallback_pct": pct(fb, routed),
        "no_def": no_def,
        "by_day": [per_day[d] for d in keys],
        "by_mode": dict(sorted(by_mode.items(), key=lambda kv: -kv[1])),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
    }


def _bar(pct: float, width: int = 24) -> str:
    """百分比 → 一根等宽条。终端里比数字更快看出量级差。"""
    filled = int(round(pct / 100 * width))
    return "█" * filled + "·" * (width - filled)


def _render(data: dict) -> str:
    out: list[str] = []
    w = out.append
    w("")
    w(f"路由分布  {data['since']} ~ {data['until']}（{data['days']} 天）")
    w("=" * 62)

    if data["total"] == 0:
        w("")
        w("这个库里没有 route_stats 数据。可能原因：")
        w("  1) 还没升级到带 core/route_stats.py 的版本（表是空的）；")
        w("  2) 统计写入一直被静默吞掉（看后端日志里有没有 route_stats 相关报错）；")
        w("  3) 这个库不是线上那个（--db 指错了）。")
        w("")
        return "\n".join(out)

    w("")
    w(f"  问了路由模型：{data['routed']:>6} 次（分母）")
    w(f"    判 workflow：{data['workflow']:>6} 次   {data['workflow_pct']:>5.1f}%  {_bar(data['workflow_pct'])}")
    w(f"    判 agent   ：{data['agent']:>6} 次   {100 - data['workflow_pct'] if data['routed'] else 0:>5.1f}%  {_bar(100 - data['workflow_pct'] if data['routed'] else 0)}")
    w("")
    w(f"  全部问答    ：{data['total']:>6} 次   workflow 占 {data['workflow_pct_all']:.1f}%（含没走路由的，会被稀释）")
    w(f"  路由兜底    ：{data['fallback']:>6} 次   {data['fallback_pct']:.1f}% of routed（越高说明路由模型越不稳）")
    if data["no_def"]:
        w(f"  ⚠ 判了 workflow 但没工作流定义：{data['no_def']} 次（部署缺数据，不是路由偏好）")

    if data["by_mode"]:
        w("")
        w("  按执行分支（这些是 mode，不是路由判定）")
        for k, v in data["by_mode"].items():
            w(f"    {k or '(空)':<12} {v:>6}")

    if data["by_source"]:
        w("")
        w("  按判定来源")
        for k, v in data["by_source"].items():
            w(f"    {k or '(空)':<34} {v:>6}")

    if data["days"] > 1:
        w("")
        w("  逐日")
        w(f"    {'日期':<12}{'问了路由':>9}{'workflow':>10}{'占比':>8}")
        for d in data["by_day"]:
            w(f"    {d['day']:<12}{d['routed']:>9}{d['workflow']:>10}{d['workflow_pct']:>7.1f}%")

    w("")
    w("  口径：workflow_pct = 判 workflow / 走了路由的请求（不含 fastpath 与旧 mode 直通）。")
    w("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="查看路由到 workflow 的百分比")
    ap.add_argument("--db", default=_default_db(), help="sessions.db 路径（默认项目 data/sessions.db）")
    ap.add_argument("--days", type=int, default=7, help="看最近多少天（默认 7）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给脚本/看板用）")
    ap.add_argument("--by-day", action="store_true", help="只输出逐日明细")
    args = ap.parse_args(argv)

    days = max(1, min(int(args.days), 365))
    conn = _connect_ro(args.db)
    try:
        if not _has_table(conn):
            print(f"{args.db}\n这个库里没有 route_stats 表：还没跑过带路由统计的版本。", file=sys.stderr)
            return 2
        data = _collect(conn, days)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.by_day:
        for d in data["by_day"]:
            print(f"{d['day']}  routed={d['routed']:>5}  workflow={d['workflow']:>5}  "
                  f"pct={d['workflow_pct']:.1f}%")
        return 0

    print(_render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
