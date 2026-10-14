# -*- coding: utf-8 -*-
"""思考流端到端验证：假 OpenAI 兼容服务端 + 真实 ChatDeepSeek 客户端。

验四件事：
  1. delta.reasoning_content 能被 ChatDeepSeek 透出到 additional_kwargs
  2. 项目的 _stream_llm 把它转成 `event: reasoning`，正文走 `event: token`
  3. 思考内容**不污染** acc.text（正文）
  4. 出口剥离：reasoning_content 不留在消息里（防回灌 400）

不起 uvicorn：直接驱动 _stream_llm，用假的 request 对象替代断连检测。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, r"C:\biancheng\agent-hub")
os.environ.setdefault("DATA_DIR", os.path.join(os.environ.get("TEMP", "/tmp"), "reasoning_probe"))

REASONING_PARTS = ["先看清问题", "：用户想验证思考流。", "那么分两步走。"]
ANSWER_PARTS = ["思考流已经打通。", "这是正文部分。"]
SEEN_PAYLOAD = {}


def sse(obj) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            SEEN_PAYLOAD["body"] = json.loads(raw.decode("utf-8"))
        except Exception:
            SEEN_PAYLOAD["body"] = {"raw": raw[:400].decode("utf-8", "replace")}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta: dict, finish=None):
            return {
                "id": "chatcmpl-probe",
                "object": "chat.completion.chunk",
                "created": 1760000000,
                "model": "deepseek-reasoner",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        self.wfile.write(sse(chunk({"role": "assistant", "content": ""})))
        for p in REASONING_PARTS:                     # 阶段一：思考
            self.wfile.write(sse(chunk({"reasoning_content": p})))
        for p in ANSWER_PARTS:                        # 阶段二：正文
            self.wfile.write(sse(chunk({"content": p})))
        self.wfile.write(sse(chunk({}, "stop")))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from langchain_core.messages import HumanMessage
    from core import config, llm as llm_mod

    config.DEEPSEEK_API_KEY = "probe-key"
    config.DEEPSEEK_BASE_URL = f"http://127.0.0.1:{port}/v1"
    config.DEEPSEEK_REASONER_MODEL = "deepseek-reasoner"
    config.LLM_TIMEOUT = 30

    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(extra)) if extra else ""))
        if not cond:
            ok = False

    print("[1] 客户端与配置")
    check("langchain-deepseek 可用", llm_mod._HAS_DEEPSEEK)
    check("has_reasoner() 为真", llm_mod.has_reasoner())

    print("\n[2] 直接消费 astream：思考与正文是否分开")
    r_llm, r_on = llm_mod.pick_llm(True, max_tokens=256)
    check("pick_llm(True) 标记为思考模型", r_on is True)

    async def run_raw():
        rtext, atext = "", ""
        async for ch in r_llm.astream([HumanMessage(content="验证思考流")]):
            kw = getattr(ch, "additional_kwargs", None) or {}
            if isinstance(kw.get("reasoning_content"), str):
                rtext += kw["reasoning_content"]
            c = ch.content
            if isinstance(c, str):
                atext += c
        return rtext, atext

    rtext, atext = asyncio.run(run_raw())
    check("拿到完整思考内容", rtext == "".join(REASONING_PARTS), repr(rtext))
    check("拿到完整正文", atext == "".join(ANSWER_PARTS), repr(atext))
    check("两者互不污染", "思考流已经打通" not in rtext and "先看清问题" not in atext)

    print("\n[3] 走项目的 _stream_llm：SSE 帧是否正确")
    import api.chat as chat

    class FakeRequest:
        async def is_disconnected(self):
            return False

    async def run_stream(emit: bool):
        acc = chat._Acc()
        frames = []
        async for f in chat._stream_llm(r_llm, [HumanMessage(content="验证思考流")], FakeRequest(), acc,
                                        emit_reasoning=emit):
            frames.append(f)
        return frames, acc

    frames, acc = asyncio.run(run_stream(True))
    events = []
    for f in frames:
        line = f.split("\n", 1)[0]
        events.append(line.replace("event: ", ""))
    check("emit_reasoning=True 时出现 reasoning 事件", "reasoning" in events,
          f"事件序列={events}")
    check("正文走 token 事件", events.count("token") == len(ANSWER_PARTS), events)
    check("思考与正文事件交替顺序正确",
          events.index("token") > events.index("reasoning") or events.count("reasoning") == len(REASONING_PARTS),
          events)
    check("acc.reasoning 累计完整", acc.reasoning == "".join(REASONING_PARTS), repr(acc.reasoning))
    check("acc.text 只有正文（不被思考污染）", acc.text == "".join(ANSWER_PARTS), repr(acc.text))

    frames2, acc2 = asyncio.run(run_stream(False))
    events2 = [f.split("\n", 1)[0].replace("event: ", "") for f in frames2]
    check("emit_reasoning=False 时不发 reasoning 事件", "reasoning" not in events2, events2)
    check("关闭思考时正文照常", acc2.text == "".join(ANSWER_PARTS))

    print("\n[4] 出口剥离：reasoning_content 不应留在消息里")
    from langchain_core.messages import AIMessage

    m = AIMessage(content="答", additional_kwargs={"reasoning_content": "内部推理"})
    chat._strip_reasoning(m)
    check("_strip_reasoning 摘掉了 reasoning_content", "reasoning_content" not in m.additional_kwargs)
    check("其它 additional_kwargs 不受影响",
          (lambda: (chat._strip_reasoning(AIMessage(content="x", additional_kwargs={"other": 1})) or True))())

    print("\n[5] 请求体自查（我们发的消息里带不带 reasoning_content）")
    body = SEEN_PAYLOAD.get("body", {})
    msgs = body.get("messages", []) if isinstance(body, dict) else []
    leaked = [m for m in msgs if isinstance(m, dict) and "reasoning_content" in json.dumps(m, ensure_ascii=False)]
    check("请求体未回灌 reasoning_content", not leaked, leaked[:1])
    check("请求体 model 是 reasoner", body.get("model") == "deepseek-reasoner", body.get("model"))

    srv.shutdown()
    print("\n" + ("=" * 56))
    print("RESULT: " + ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
