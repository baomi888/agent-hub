import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["AUTH_SECRET"] = "gate-test"

import httpx  # noqa: E402

from core.auth import create_token  # noqa: E402


async def _stub():
    async def _h(reader, writer):
        try:
            await asyncio.sleep(120)
        finally:
            writer.close()

    return await asyncio.start_server(_h, "127.0.0.1", 8899)


async def main() -> None:
    stub = await _stub()
    print("stub listening:", [s.getsockname() for s in stub.sockets])
    base = "http://127.0.0.1:8124"
    h = {"X-Auth-Token": create_token("u-gate-accept", username="gate_accept")}
    async with httpx.AsyncClient(base_url=base, headers=h, timeout=20) as c:
        r = await c.post("/api/sessions/", json={"title": "probe"})
        print("create session:", r.status_code, r.text[:200])
        sid = (r.json() or {}).get("id")
        if not sid:
            rows = (await c.get("/api/sessions/")).json()
            items = rows.get("items") if isinstance(rows, dict) else rows
            sid = items[0]["id"]
        print("sid:", sid)
        async with c.stream(
            "POST", "/api/chat/stream",
            json={"sid": sid, "question": "探针", "mode": "llm"},
        ) as resp:
            print("status:", resp.status_code, "headers:", dict(resp.headers))
            async for line in resp.aiter_lines():
                if line.strip():
                    print("FRAME:", line[:300])
                if len(line) > 0 and "done" in line:
                    break


asyncio.run(main())
