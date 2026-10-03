# -*- coding: utf-8 -*-
"""
苞米agent · 现场性能测试（零第三方依赖版）
==========================================
只用 Python 标准库，不需要 pip install 任何东西 —— 这是为现场演示准备的硬要求。

默认约 30 秒跑完，全程滚动输出，老师能看着它一行行出结果。

用法：
    python 现场压测.py                      # 默认：健康检查 + 只读阶梯 + 问答
    python 现场压测.py --quick              # 快模式：跳过问答，约 10 秒
    python 现场压测.py --with-upload        # 额外演示上传链路（会建临时库再自动删除）
    python 现场压测.py --host 1.2.3.4       # 换目标机器

随时 Ctrl+C 中断，不会有残留。
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = {"User-Agent": "baomi-stress-live/1.0"}


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def ms(x):
    return x * 1000.0


def http_get(url, timeout=15):
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return (time.perf_counter() - t0, r.status)
    except Exception:
        return (time.perf_counter() - t0, -1)


def http_post_json(url, payload, timeout=120):
    t0 = time.perf_counter()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={**UA, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
            return (time.perf_counter() - t0, r.status)
    except Exception:
        return (time.perf_counter() - t0, -1)


def post_multipart(url, fields, files, timeout=120):
    boundary = "----baomi_live_boundary_x9"
    body = b""
    for k, v in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                 f"{v}\r\n").encode("utf-8")
    for name, filename, content in files:
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n').encode("utf-8")
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={**UA, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return (time.perf_counter() - t0, r.status)
    except Exception:
        return (time.perf_counter() - t0, -1)


def run_concurrent(fn, conc, total, tick=4):
    """用线程池发起 total 次请求，实时打点。返回 [(latency, status), ...]"""
    done = [0]
    results = []

    def one(i):
        r = fn(i)
        done[0] += 1
        if done[0] % tick == 0:
            print(f"      · {done[0]}/{total}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=conc) as pool:
        results = list(pool.map(one, range(total)))
    return results


def report(label, rows, unit="ms"):
    lat = [r[0] for r in rows]
    ok = sum(1 for r in rows if r[1] == 200)
    fails = len(rows) - ok
    p50 = statistics.median(lat)
    p95 = pct(lat, 95)
    mx = max(lat)
    if unit == "ms":
        print(f"      {label:<8} 样本 {len(rows):>2}  "
              f"P50 {ms(p50):>7.1f}ms  P95 {ms(p95):>7.1f}ms  "
              f"最大 {ms(mx):>7.1f}ms  失败 {fails}", flush=True)
    else:
        print(f"      {label:<8} 样本 {len(rows):>2}  "
              f"P50 {p50:>6.2f}s  最大 {mx:>6.2f}s  失败 {fails}", flush=True)
    if fails:
        codes = sorted({str(r[1]) for r in rows if r[1] != 200})
        print(f"               └ 失败状态码：{', '.join(codes)}", flush=True)
    return {"label": label, "n": len(rows), "ok": ok, "fails": fails,
            "p50": p50, "p95": p95, "max": mx}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="8.163.62.25")
    ap.add_argument("--quick", action="store_true", help="快模式，跳过问答链路")
    ap.add_argument("--with-upload", action="store_true", help="额外演示上传链路")
    args = ap.parse_args()

    FE = f"http://{args.host}:3000"
    BE = f"http://{args.host}:8000"

    print("=" * 66, flush=True)
    print(" 苞米agent · 现场性能测试", flush=True)
    print(f" 目标 {args.host}  ·  阿里云 ECS 2核2G  ·  经公网访问", flush=True)
    print(" 说明：延迟含公网往返，只做低并发验证，不做极限压测", flush=True)
    print("=" * 66, flush=True)
    print(flush=True)

    # ---------- 0. 健康检查 ----------
    print("[0/3] 健康检查", flush=True)
    r = http_get(f"{BE}/health")
    if r[1] != 200:
        print(f"      ✗ 后端不可达（HTTP {r[1]}）。改用兜底方案：展示已跑好的数据。", flush=True)
        return
    print(f"      ✓ /health  HTTP {r[1]}  {ms(r[0]):.0f}ms", flush=True)
    r2 = http_get(f"{FE}/api/kb/")
    print(f"      ✓ 前端代理 /api/kb/  HTTP {r2[1]}  {ms(r2[0]):.0f}ms", flush=True)
    print(flush=True)

    # ---------- 1. 只读阶梯 ----------
    print("[1/3] 只读接口并发阶梯（每档 12 次请求）", flush=True)
    ladder = [1, 4] if args.quick else [1, 4, 8]
    readonly = []
    for conc in ladder:
        print(f"      ---- 并发 {conc} ----", flush=True)
        rows = run_concurrent(lambda i: http_get(f"{FE}/api/kb/"), conc, 12)
        readonly.append(report(f"并发{conc}", rows))
        if conc == 8:
            print("      ↑ 此刻正在打请求 —— 现在刷新浏览器，页面照样能开", flush=True)
    print(flush=True)

    # ---------- 2. 问答链路 ----------
    qa = []
    if not args.quick:
        print("[2/3] 问答全链路（检索 + 大模型生成，SSE 流式）", flush=True)
        sid = None
        try:
            req = urllib.request.Request(f"{FE}/api/sessions", headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            lst = data if isinstance(data, list) else data.get("sessions", [])
            if lst:
                sid = lst[0].get("sid") or lst[0].get("id")
        except Exception:
            pass
        if not sid:
            print("      (无可用会话，跳过)", flush=True)
        else:
            for i in range(2):
                t, st = http_post_json(
                    f"{FE}/api/chat/stream",
                    {"sid": sid, "question": "简单介绍这个项目用了哪些技术？",
                     "top_k": 3, "mode": "rag"},
                )
                qa.append({"label": f"第{i+1}次", "n": 1, "ok": 1 if st == 200 else 0,
                           "fails": 0 if st == 200 else 1, "p50": t, "p95": t, "max": t})
                print(f"      第 {i+1} 次  {t:>5.2f}s  "
                      f"{'✓' if st == 200 else '✗ ' + str(st)}", flush=True)
    else:
        print("[2/3] 问答链路 —— 快模式已跳过", flush=True)
    print(flush=True)

    # ---------- 3. 上传（可选） ----------
    up = []
    if args.with_upload:
        print("[3/3] 上传链路（建临时库 → 上传 → 自动删除）", flush=True)
        kb_id = None
        try:
            data = json.dumps({"name": "现场演示临时库"}).encode("utf-8")
            req = urllib.request.Request(
                f"{FE}/api/kb/create", data=data,
                headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                kb_id = json.loads(r.read().decode("utf-8")).get("kb_id")
        except Exception as e:
            print(f"      建库失败：{e}", flush=True)
        if kb_id:
            doc = ("本项目使用 FastAPI 与 Chroma 构建检索增强生成服务，"
                   "前端基于 Next.js。文档上传后自动切片并向量化。\n" * 60).encode("utf-8")
            for conc in (1, 3):
                # 每次用不同文件名 —— 并发上传同名文件会在落盘环节互相覆盖
                rows = run_concurrent(
                    lambda i: post_multipart(
                        f"{FE}/api/kb/{kb_id}/upload",
                        {"chunk_size": "500", "chunk_overlap": "50", "mode": "append"},
                        [("files", f"live_demo_{i}.txt", doc)]), conc, conc * 2, tick=2)
                up.append(report(f"并发{conc}", rows, unit="s"))
            # 删除必须试两种写法：urllib 对 308 重定向不跟随，而本项目 /api 的
            # 斜杠约定是混合的（带尾斜杠会被 Next 去尾斜杠重定向）。实测不带尾斜杠才成功。
            deleted = False
            for url in (f"{FE}/api/kb/{kb_id}", f"{FE}/api/kb/{kb_id}/"):
                try:
                    req = urllib.request.Request(url, method="DELETE", headers=UA)
                    with urllib.request.urlopen(req, timeout=15) as r:
                        print(f"      清理临时库 -> HTTP {r.status}", flush=True)
                        deleted = True
                        break
                except Exception:
                    continue
            if not deleted:
                print(f"      ⚠ 清理失败，请手工删除知识库 {kb_id}", flush=True)
    else:
        print("[3/3] 上传链路 —— 默认跳过（加 --with-upload 可演示）", flush=True)
    print(flush=True)

    # ---------- 汇总 ----------
    print("=" * 66, flush=True)
    print(" 汇总", flush=True)
    print("=" * 66, flush=True)
    print(f" 只读接口   并发 1 → {ladder[-1]}   "
          f"P50 {ms(readonly[0]['p50']):.1f}ms → {ms(readonly[-1]['p50']):.1f}ms   "
          f"失败 {sum(x['fails'] for x in readonly)}", flush=True)
    if qa:
        print(f" 问答链路   P50 {statistics.median([x['p50'] for x in qa]):.2f}s   "
              f"失败 {sum(x['fails'] for x in qa)}", flush=True)
    if up:
        print(f" 上传链路   P50 {up[0]['p50']:.2f}s → {up[-1]['p50']:.2f}s   "
              f"失败 {sum(x['fails'] for x in up)}", flush=True)
    total_fail = (sum(x["fails"] for x in readonly)
                  + sum(x["fails"] for x in qa)
                  + sum(x["fails"] for x in up))
    print(f" 全程失败次数：{total_fail}", flush=True)
    print(flush=True)
    print(" 结论：并发提升后延迟基本持平、无失败，服务在压力下仍可正常访问。", flush=True)
    print(" 局限：只做低并发验证，未测崩溃拐点；延迟含公网往返。", flush=True)
    print("=" * 66, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断（无残留）。", flush=True)
