"""把 loadtest_concurrency.py 产出的 JSON 转成 Markdown 表格，并按门槛判定是否通过。

用法：
    python tools/loadtest_report.py <result.json> [--max-p95 3000] [--max-err 0.01] \
        [--out summary.md]

退出码：0 = 全部达标；1 = 有场景超门槛（CI 用它做回归拦截面）。

为什么单独一个脚本而不是在 workflow 里写内联 python：
    本地跑完压测也想用同一套口径看结果，内联一次就得复制一份，口径分叉只是时间问题。
"""

from __future__ import annotations

import argparse
import json
import sys


def fmt_row(r: dict) -> str:
    codes = r.get("codes", {})
    codes_s = "、".join(f"{k}×{v}" for k, v in sorted(codes.items())) or "-"
    return (
        f"| {r.get('scenario', '')} | {r.get('requests', 0)} | "
        f"**{r.get('rps', 0)}** | {r.get('p50_ms', 0)} | {r.get('p95_ms', 0)} | "
        f"{r.get('p99_ms', 0)} | {r.get('err_rate', 0)}% | {codes_s} |"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--max-p95", type=float, default=3000.0,
                    help="p95 上限（毫秒），超过即判失败")
    ap.add_argument("--max-err", type=float, default=1.0,
                    help="错误率上限（百分比）")
    ap.add_argument("--out", default="", help="Markdown 落盘路径（不填则只打屏）")
    args = ap.parse_args()

    with open(args.json_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = data.get("rows", [])
    lines = [
        f"- 并发：**{data.get('concurrency', '?')}**（{data.get('processes', '?')} 个生成器进程）　"
        f"时长：{data.get('duration', '?')}s　模式：`{data.get('mode', '?')}`",
        "",
        "| 场景 | 请求数 | RPS | p50 | p95 | p99 | 错误率 | 状态码 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    failed: list[str] = []
    for r in rows:
        lines.append(fmt_row(r))
        name = r.get("scenario", "?")
        if r.get("p95_ms", 0) > args.max_p95:
            failed.append(f"{name}：p95 {r['p95_ms']}ms > {args.max_p95}ms")
        if r.get("err_rate", 0) > args.max_err:
            failed.append(f"{name}：错误率 {r['err_rate']}% > {args.max_err}%")

    if failed:
        lines += ["", "**门槛未通过：**", ""] + [f"- {x}" for x in failed]
        verdict = "❌ 未通过门槛"
    else:
        lines += ["", f"**门槛：** p95 ≤ {args.max_p95}ms、错误率 ≤ {args.max_err}%　✅ 通过"]
        verdict = "✅ 通过门槛"

    md = "\n".join(lines)
    print(verdict)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        print(f"已写入 {args.out}")
    else:
        print(md)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
