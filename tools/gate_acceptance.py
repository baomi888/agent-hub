"""P0-2 验收：并发闸在**真实服务**上会不会真的把超出的人挡在门外（429 + Retry-After）。

为什么单独写一个脚本而不是单测
------------------------------
单测能证明信号量语义对，但证明不了"路由层真的在 200 响应头之前把闸抢了"。
这个脚本用真 uvicorn + 真 HTTP，验证完整的因果链：
    并发 > LLM_MAX_CONCURRENCY  →  多出来的人排队  →  排队超时  →  429 + Retry-After

怎么让"占着名额"这件事稳定发生
------------------------------
脚本自己起一个**只接受连接、永不回包**的 TCP 桩，服务端的 DEEPSEEK_BASE_URL 指向它，
于是抢到名额的那一轮会卡在上游读超时上（LLM_TIMEOUT 秒），名额被真实占住，
其余请求必然排队超时。这样不烧一分钱 token，也不依赖外网。

用法（服务端这样起）：
    AUTH_SECRET=gate-test  LLM_MAX_CONCURRENCY=1  LLM_QUEUE_TIMEOUT=0.3 \\
    DEEPSEEK_BASE_URL=http://127.0.0.1:8899/v1 \\
    python -m uvicorn main:app --host 127.0.0.1 --port 8124 --workers 1

    python tools/gate_acceptance.py --base-url http://127.0.0.1:8124 \\
        --secret gate-test --clients 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# 允许 `python tools/gate_acceptance.py` 直接跑：脚本目录不是仓库根，把根塞进 path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

STUB_PORT = 8899


async def _start_blackhole(port: int) -> asyncio.Server:
    """只接受连接、永不回包的上游桩：把名额稳定占住。"""

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.sleep(300)  # 一直挂着，直到被取消
        finally:
            writer.close()

    return await asyncio.start_server(_handler, "127.0.0.1", port)


async def _run(args) -> dict:
    import httpx

    from core.auth import create_token

    stub = await _start_blackhole(STUB_PORT)
    base = args.base_url.rstrip("/")
    headers = {"X-Auth-Token": create_token("u-gate-accept", username="gate_accept")}

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as c:
        before = (await c.get("/health")).json().get("llm_gate", {})

        # 建一个会话（闸在 404 之前抢，但要让"抢到的人"真的卡住，必须有真会话）
        r = await c.post("/api/sessions/", json={"title": "gate-acceptance"})
        sid = (r.json() or {}).get("id") if r.status_code < 300 else None
        if not sid:
            # 接口形状可能变：退一步直接问现有会话
            rows = (await c.get("/api/sessions/")).json()
            items = rows.get("items") if isinstance(rows, dict) else rows
            sid = items[0]["id"] if items else None
        if not sid:
            stub.close()
            raise SystemExit(f"建会话失败：HTTP {r.status_code} {r.text[:200]}")

        payload = {"sid": sid, "question": "验收：并发闸排队", "mode": "llm"}

        async def one(i: int) -> dict:
            import time as _t

            t0 = _t.monotonic()
            req = c.build_request("POST", "/api/chat/stream", json=payload)
            r = await c.send(req, stream=True)
            code = r.status_code
            retry = r.headers.get("Retry-After")
            held = 0.0
            first = ""
            second = ""
            if code == 200:
                # 关键：**不能**拿到响应头就断开。SSE 的 200 在开始吐流时就发出去了，
                # 立刻 aclose 会让服务端马上检测到断连并释放名额，那样永远测不出排队。
                # 读一帧后把连接保持 hold 秒，模拟"用户正盯着屏幕等答案"。
                # 注意：aiter_bytes() 不能重复调用（第二次会 StreamConsumed），必须先拿迭代器。
                it = r.aiter_bytes()
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=5)
                    first = chunk.decode("utf-8", "replace")[:80]
                    held = _t.monotonic()
                    await asyncio.sleep(args.hold)
                except (TimeoutError, StopAsyncIteration, httpx.HTTPError, RuntimeError):
                    pass
                # 抢到名额的那条：如果上游桩真生效，这里应该**读不到下一帧**（一直挂着）。
                # 读到 EOF 或 error 帧，说明上游立刻报错、名额早就还回去了 —— 桩没生效的信号。
                try:
                    nxt = await asyncio.wait_for(it.__anext__(), timeout=0.5)
                    second = nxt.decode("utf-8", "replace")[:160]
                except TimeoutError:
                    second = "HANGING(桩生效)"
                except StopAsyncIteration:
                    second = "EOF(上游已结束)"
                except (httpx.HTTPError, RuntimeError) as e:
                    second = f"ERR:{type(e).__name__}"
            await r.aclose()
            return {
                "i": i,
                "status": code,
                "retry_after": retry,
                "elapsed_ms": int((_t.monotonic() - t0) * 1000),
                "first_chunk": first,
                "second": second,
                "held": bool(held),
            }

        # 同时发：1 个抢到并卡住，其余排队超时
        results = await asyncio.gather(*[one(i) for i in range(args.clients)])

        after = (await c.get("/health")).json().get("llm_gate", {})

    # 只 close 不 wait_closed：桩的 handler 故意睡 300 秒，
    # wait_closed() 会一直等这些连接结束，把脚本挂死（踩过一次）。
    stub.close()

    codes = [r["status"] for r in results]
    n429 = sum(1 for s in codes if s == 429)
    retry_after = next((r["retry_after"] for r in results if r["status"] == 429), None)
    return {
        "clients": args.clients,
        "status_codes": codes,
        "accepted": sum(1 for s in codes if s == 200),
        "rejected_429": n429,
        "retry_after": retry_after,
        "detail": results,
        "gate_before": before,
        "gate_after": after,
        "pass": n429 >= args.clients - 1 and bool(retry_after),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 并发闸端到端验收")
    ap.add_argument("--base-url", default="http://127.0.0.1:8124")
    ap.add_argument("--secret", default="", help="与被测服务相同的 AUTH_SECRET")
    ap.add_argument("--clients", type=int, default=6, help="同时发起的问答数")
    ap.add_argument("--hold", type=float, default=2.0, help="抢到名额的客户端保持连接的秒数")
    ap.add_argument("--json-out", default="", help="把结果写成 JSON")
    args = ap.parse_args()

    if args.secret:
        os.environ["AUTH_SECRET"] = args.secret  # 必须在 import core.config 之前

    result = asyncio.run(_run(args))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
