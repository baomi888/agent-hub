"""数据看板对账脚本（口径文档 §9）。

用途：**上线前 / 改完查询逻辑后**手跑一次，确认看板上的数字与库里能对得上。
与 `tests/test_dashboard.py` 的分工：
  - 单测验的是"代码分支"，用临时库、造数据；
  - 本脚本验的是"线上这份数据"，直接读生产库，不需要造数据。

四项检查（任一不过 → 退出码 1）：
  ① 今日调用数：`dashboard.overview(1)` 与手写 SQL 相等
  ② 路径归一化：最近 N 条里不应出现 uuid / 纯数字 / 32 位 hex 段
  ③ 采集范围：不应出现 `/health`、`/api/metrics`、`/api/admin/*`
  ④ 不重复计数：排行各接口 calls 之和 ≤ 区间总调用数

用法：
    python tools/check_dashboard.py                # 默认查近 7 天 + 最近 500 条路径
    python tools/check_dashboard.py --days 30 --sample 2000
    python tools/check_dashboard.py --json         # 机器可读，给 CI 用
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import account, apilog, dashboard, timeutil, user_feedback  # noqa: E402
from core import session as store  # noqa: E402

# 与 core/apilog.py 的 _RE_* 保持一致：这里**故意不复用**那些私有正则，
# 因为对账脚本要用"另一套写法"验同一件事，复用同一个实现等于自己验自己。
_HEXISH = set("0123456789abcdefABCDEF")


def _looks_like_id(seg: str) -> bool:
    if not seg:
        return False
    if seg.isdigit():
        return True
    if len(seg) == 36 and seg.count("-") == 4:
        return all(c in _HEXISH or c == "-" for c in seg)
    return len(seg) >= 8 and all(c in _HEXISH for c in seg)


def _fail(problems: list[str], msg: str) -> None:
    problems.append(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description="数据看板对账")
    ap.add_argument("--days", type=int, default=7, help="统计区间（自然日），默认 7")
    ap.add_argument("--sample", type=int, default=500, help="抽查多少条日志路径，默认 500")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（CI 用）")
    args = ap.parse_args()

    # 建表是幂等的，且只建"缺的表"：真实部署上本来就有，等于空操作；
    # 在全新 checkout（CI）上则让脚本能跑完而不是报 "no such table: users"。
    apilog.ensure()
    user_feedback.ensure()
    account._ensure_tables()  # noqa: SLF001 —— 看板的"注册用户数"要读 users 表
    problems: list[str] = []
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            problems.append(f"{name}：{detail}")

    # ---------- ① 今日调用数：看板 vs 裸 SQL ----------
    today_start = timeutil.day_bounds(timeutil.day_str())[0]
    board = dashboard.overview(1)["calls"]["total"]
    with store._get_conn() as conn:  # noqa: SLF001
        raw = conn.execute(
            "SELECT COUNT(*) FROM api_call_logs WHERE ts >= ?", (today_start,)
        ).fetchone()[0]
    add("今日调用数一致", board == raw, f"看板={board} SQL={raw}")

    # ---------- ② 路径归一化 ----------
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT DISTINCT path FROM api_call_logs ORDER BY ts DESC LIMIT ?", (args.sample,)
        ).fetchall()
    raw_paths = [str(r["path"]) for r in rows]
    bad = [p for p in raw_paths if any(_looks_like_id(seg) for seg in p.split("/"))]
    add("路径已归一化", not bad, f"抽查 {len(raw_paths)} 条，未归一化 {len(bad)} 条：{bad[:3]}")

    # ---------- ③ 采集范围 ----------
    forbidden = [p for p in raw_paths if not apilog.should_record(p)]
    add("无排除路径", not forbidden, f"出现不该记录的前缀：{forbidden[:3]}")

    # ---------- ④ 排行不重复计数 ----------
    r = dashboard.range_of(args.days)
    with store._get_conn() as conn:  # noqa: SLF001
        total = conn.execute(
            "SELECT COUNT(*) FROM api_call_logs WHERE ts >= ? AND ts < ?",
            (r["start"], r["end_exclusive"]),
        ).fetchone()[0]
    rank = dashboard.api_ranking(args.days, limit=100, sort="calls")
    ranked_sum = sum(x["calls"] for x in rank["items"])
    # limit=100 时若接口种类 ≤ 100，两者必须严格相等；否则只能要求前者不超过总数
    if rank["total_paths"] <= 100:
        add("排行合计=区间总数", ranked_sum == total, f"排行={ranked_sum} 总数={total}")
    else:
        add("排行合计≤区间总数", ranked_sum <= total, f"排行={ranked_sum} 总数={total}")

    ok = not problems
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks, "problems": problems}, ensure_ascii=False))
    else:
        print(f"数据看板对账 · 近 {args.days} 天 · 库 {store._db_path()}")  # noqa: SLF001
        print("=" * 72)
        for c in checks:
            print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']:<16} {c['detail']}")
        print("=" * 72)
        print("结论：" + ("全部通过" if ok else f"{len(problems)} 项未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
