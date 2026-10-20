"""本地并发压测：量一量这台机器能扛多少"同时在线"。

为什么不用 ab / wrk
-------------------
被测的不是静态页，是带 Cookie / JWT 鉴权、且每条请求都会摸一次 SQLite 的
接口。ab 不会自己注册登录，也不区分 429/401/500 的语义，测出来的 RPS 好看
但没用。这里用 httpx 异步并发，把状态码分布和分位数一起打出来。

三种口径（对应三种"在线"的含意）
--------------------------------
  health  纯框架开销：不鉴权、不摸库。这是这台机器的**天花板**。
  me      鉴权开销：JWT 验签 + jti 黑名单查询 + users 表点查。
          "200 人同时在线"真正要比的是这个。
  login   口令成本：PBKDF2-SHA256(200000 轮) 是 CPU 密集，用来判断
           "200 人同时登录"会不会把机器打满。

用法
----
  python tools/loadtest_concurrency.py --base-url http://127.0.0.1:8123 \
      --mode health --concurrency 200 --duration 20

  # 三种口径一起跑
  python tools/loadtest_concurrency.py --base-url http://127.0.0.1:8123 --mode all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

import httpx

DEFAULT_BASE = "http://127.0.0.1:8123"


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * len(sorted_vals) - 0.5))))
    return sorted_vals[k]


def summarize(name: str, lat: list[float], codes: dict[int, int], wall: float) -> dict:
    lat.sort()
    total = len(lat)
    ok = sum(c for code, c in codes.items() if 200 <= code < 300)
    row = {
        "scenario": name,
        "requests": total,
        "rps": round(total / wall, 1) if wall > 0 else 0,
        "ok": ok,
        "err_rate": round(100.0 * (total - ok) / total, 2) if total else 0.0,
        "p50_ms": round(pct(lat, 50), 1),
        "p95_ms": round(pct(lat, 95), 1),
        "p99_ms": round(pct(lat, 99), 1),
        "max_ms": round(lat[-1], 1) if lat else 0.0,
        "codes": {str(k): v for k, v in sorted(codes.items())},
    }
    return row


async def hit(client: httpx.AsyncClient, method: str, url: str, headers: dict, body: dict | None):
    t0 = time.perf_counter()
    try:
        if method == "GET":
            r = await client.get(url, headers=headers)
        else:
            r = await client.post(url, headers=headers, json=body)
        return r.status_code, (time.perf_counter() - t0) * 1000.0
    except Exception:
        return -1, (time.perf_counter() - t0) * 1000.0


async def run_scenario(
    name: str,
    base: str,
    path: str,
    method: str,
    concurrency: int,
    duration: float,
    cookie: str = "",
    body: dict | None = None,
    header_token: str = "",
) -> dict:
    headers: dict[str, str] = {}
    if cookie:
        headers["Cookie"] = cookie
    if header_token:
        headers["X-Auth-Token"] = header_token

    url = base.rstrip("/") + path
    lat: list[float] = []
    codes: dict[int, int] = {}
    stop_at = time.perf_counter() + duration

    limits = httpx.Limits(
        max_connections=concurrency + 50, max_keepalive_connections=concurrency + 50
    )
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:

        async def worker() -> None:
            while time.perf_counter() < stop_at:
                code, ms = await hit(client, method, url, headers, body)
                lat.append(ms)
                codes[code] = codes.get(code, 0) + 1

        t0 = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(concurrency)])
        wall = time.perf_counter() - t0

    return summarize(name, lat, codes, wall)


def _child_run(spec: tuple) -> dict:
    name, base, path, method, conc, dur, cookie, body, header_token = spec
    return asyncio.run(
        run_scenario(name, base, path, method, conc, dur, cookie=cookie, body=body, header_token=header_token)
    )


def run_multi(
    name: str,
    base: str,
    path: str,
    method: str,
    concurrency: int,
    duration: float,
    processes: int,
    cookie: str = "",
    body: dict | None = None,
) -> dict:
    """把总并发拆到 N 个**独立进程**里跑。

    为什么必须多进程：单个 Python 进程的 asyncio 事件循环受 GIL 限制，把
    并发从 20 拉到 200 时，生成器自己先饱和——吞吐不升反降（实测 684 RPS
    → 138 RPS），此时测到的是压测脚本的上限，不是服务器的上限。
    拆进程后每个进程有自己的 GIL 和事件循环，才是真的 200 并发压力。

    合并口径：请求数 / RPS / 错误数直接相加；p50 按请求数加权，
    p95、p99 取各进程最大值（偏保守，说"最差那一路的尾巴"）。
    """
    if processes <= 1:
        return asyncio.run(
            run_scenario(name, base, path, method, concurrency, duration, cookie=cookie, body=body)
        )

    from concurrent.futures import ProcessPoolExecutor

    per = max(1, concurrency // processes)
    specs = [
        (name, base, path, method, per, duration, cookie, body, "") for _ in range(processes)
    ]
    with ProcessPoolExecutor(max_workers=processes) as ex:
        parts = list(ex.map(_child_run, specs))

    total = sum(p["requests"] for p in parts) or 1
    codes: dict[str, int] = {}
    for p in parts:
        for k, v in p["codes"].items():
            codes[k] = codes.get(k, 0) + v
    ok = sum(p["ok"] for p in parts)
    return {
        "scenario": f"{name}  [进程x{processes}, 每进程{per}]",
        "requests": total,
        "rps": round(sum(p["rps"] for p in parts), 1),
        "ok": ok,
        "err_rate": round(100.0 * (total - ok) / total, 2),
        "p50_ms": round(sum(p["p50_ms"] * p["requests"] for p in parts) / total, 1),
        "p95_ms": max(p["p95_ms"] for p in parts),
        "p99_ms": max(p["p99_ms"] for p in parts),
        "max_ms": max(p["max_ms"] for p in parts),
        "codes": codes,
    }


async def ensure_user(base: str, username: str, password: str) -> tuple[str, str]:
    """注册（已存在则登录），返回 (cookie 串, token)。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        for path in ("/api/auth/register/", "/api/auth/login/"):
            r = await client.post(
                base.rstrip("/") + path, json={"username": username, "password": password}
            )
            if r.status_code == 200:
                sc = r.headers.get("set-cookie", "")
                cookie = sc.split(";")[0] if sc else ""
                token = (r.json() or {}).get("token") or ""
                return cookie, token
    raise SystemExit("压测账号准备失败（注册与登录都没成功）")


async def amain(args) -> int:
    base = args.base_url
    pwd = "loadtest-pass-123"
    user = args.user or f"loadtest_{int(time.time())}"

    print(f"[准备] 压测账号 {user} @ {base}")
    cookie, token = await ensure_user(base, user, pwd)
    print(f"[准备] Cookie 已拿到（{len(cookie)} 字节），header token={'有' if token else '无(默认不返回)'}")
    print(f"[准备] 并发 {args.concurrency}，每个场景持续 {args.duration}s\n")

    rows = []
    want = ["health", "me", "login"] if args.mode == "all" else [args.mode]

    if "health" in want:
        rows.append(
            run_multi(
                "GET /health（无鉴权，纯框架开销）",
                base, "/health", "GET", args.concurrency, args.duration, args.processes,
            )
        )
    if "me" in want:
        rows.append(
            run_multi(
                "GET /api/auth/me/（Cookie 鉴权 + 查库）",
                base, "/api/auth/me/", "GET", args.concurrency, args.duration, args.processes,
                cookie=cookie,
            )
        )
    if "login" in want:
        rows.append(
            run_multi(
                "POST /api/auth/login/（PBKDF2 校验）",
                base, "/api/auth/login/", "POST", min(args.concurrency, 50), args.duration, 1,
                body={"username": user, "password": pwd},
            )
        )

    print(f"{'场景':<44}{'请求':>7}{'RPS':>8}{'错误%':>8}{'p50':>8}{'p95':>8}{'p99':>9}{'max':>9}")
    print("-" * 101)
    for r in rows:
        print(
            f"{r['scenario']:<44}{r['requests']:>7}{r['rps']:>8}{r['err_rate']:>8}"
            f"{r['p50_ms']:>8}{r['p95_ms']:>8}{r['p99_ms']:>9}{r['max_ms']:>9}"
        )
    print("\n状态码分布：")
    for r in rows:
        print(f"  {r['scenario']}: {r['codes']}")

    if args.json_out:
        # duration / processes / mode 一并落盘：CI 的 job summary 与门槛判定
        # 全靠这几个字段复述口径，缺了就只能靠人记当时是怎么跑的。
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "base": base,
                    "concurrency": args.concurrency,
                    "processes": args.processes,
                    "duration": args.duration,
                    "mode": args.mode,
                    "rows": rows,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n[输出] {os.path.abspath(args.json_out)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="agent-hub 本地并发压测")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--mode", default="all", choices=["health", "me", "login", "all"])
    ap.add_argument("--concurrency", type=int, default=200)
    ap.add_argument(
        "--processes",
        type=int,
        default=1,
        help="生成器进程数。测 200 并发时应设 4~8，否则单进程会先被 GIL 卡住",
    )
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--user", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
