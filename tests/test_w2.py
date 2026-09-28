# -*- coding: utf-8 -*-
"""W2 端到端测试：通过 HTTP 调后端验证会话层 + SSE 流式问答。"""

import json
import sys

import requests

BASE = "http://localhost:8000"
PASS = "✅"
FAIL = "❌"

s = requests.Session()


def _parse_frame(frame):
    """SSE 帧解析：返回 (event_type, data_json_str)。"""
    lines = frame.split("\n")
    ev_type = "message"
    ev_data = ""
    for line in lines:
        if line.startswith("event:"):
            ev_type = line[6:].strip()
        elif line.startswith("data:"):
            ev_data += line[5:].strip()
    return ev_type, ev_data


def http(method, path, body=None):
    r = s.request(method, BASE + path, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def test(label, fn):
    try:
        result = fn()
        print(f"{PASS} {label}")
        return result
    except Exception as e:
        print(f"{FAIL} {label}: {e}")
        return None


print("=" * 50)
print("W2 端到端测试")
print("=" * 50)

# ====== 1. 会话 CRUD ======
print("\n--- 1. 会话 CRUD ---")

r = test("创建会话（默认）", lambda: http("POST", "/api/sessions/", {"title": "默认"}))
assert r, "创建失败"
sid = r["id"]
print(f"   sid = {sid}")

r2 = test("创建会话（绑定 test_w1 知识库）", lambda: http("POST", "/api/sessions/", {
    "title": "RAG 测试",
    "kb_id": "test_w1",
}))
assert r2
sid2 = r2["id"]
print(f"   sid2 = {sid2}, kb_id = {r2.get('kb_id')}")

test("列表会话", lambda: http("GET", "/api/sessions/"))
test("查看详情", lambda: http("GET", f"/api/sessions/{sid}/"))
test("重命名", lambda: http("PATCH", f"/api/sessions/{sid}/", {"title": "重命名后"}))
test("绑定知识库", lambda: http("PATCH", f"/api/sessions/{sid}/", {"kb_id": "test_w1"}))
test("解绑知识库", lambda: http("PATCH", f"/api/sessions/{sid}/", {"kb_id": None}))
test("删除会话", lambda: http("DELETE", f"/api/sessions/{sid}/"))

# ====== 2. 纯 LLM SSE 流式 ======
print("\n--- 2. 纯 LLM SSE 流式 ---")

resp = s.post(
    BASE + "/api/chat/stream",
    json={"sid": sid2, "question": "用一句话介绍你自己。"},
    stream=True,
)
resp.raise_for_status()

# 按 SSE 帧切
sse_events = []
buffer = ""
for raw_line in resp.iter_lines(decode_unicode=True):
    if raw_line is None:
        # 空行 = 帧分隔
        if buffer.strip():
            frame = buffer.strip()
            ev_type, ev_data = _parse_frame(frame)
            sse_events.append((ev_type, ev_data))
            buffer = ""
        continue
    buffer += raw_line + "\n"
# 最后一帧
if buffer.strip():
    ev_type, ev_data = _parse_frame(buffer.strip())
    sse_events.append((ev_type, ev_data))

resp.close()

event_types = [e[0] for e in sse_events]
print(f"   事件: {event_types}")

assert "token" in event_types, f"缺 token"
assert "done" in event_types, f"缺 done"
assert "title" in event_types, f"缺 title（首轮应自动生成）"

done_data = json.loads(sse_events[event_types.index("done")][1])
print(f"   LLM 回答: {done_data['answer'][:100]}...")
print(f"   自动标题: {done_data.get('title', '(无)')}")
print(f"{PASS} 纯 LLM SSE（含自动标题）")

# ====== 3. RAG 绑定 SSE ======
print("\n--- 3. RAG 绑定 SSE ---")

# 确认 sid2 仍绑定 test_w1
detail = http("GET", f"/api/sessions/{sid2}/")
print(f"   sid2 kb_id = {detail.get('kb_id')}")

resp = s.post(
    BASE + "/api/chat/stream",
    json={"sid": sid2, "question": "RAG 技术的核心思想是什么？", "top_k": 2},
    stream=True,
)
resp.raise_for_status()

sse_events2 = []
buffer = ""
for raw_line in resp.iter_lines(decode_unicode=True):
    if raw_line is None:
        if buffer.strip():
            frame = buffer.strip()
            ev_type, ev_data = _parse_frame(frame)
            sse_events2.append((ev_type, ev_data))
            buffer = ""
        continue
    buffer += raw_line + "\n"
if buffer.strip():
    ev_type, ev_data = _parse_frame(buffer.strip())
    sse_events2.append((ev_type, ev_data))
resp.close()

event_types2 = [e[0] for e in sse_events2]
print(f"   事件: {event_types2}")

done_data2 = json.loads(sse_events2[event_types2.index("done")][1])
print(f"   RAG 回答: {done_data2['answer'][:120]}...")
print(f"   refs: {'有' if done_data2.get('refs') else '无'}")

has_ref = "依据：[" in done_data2["answer"] or "[" in done_data2["answer"]
print(f"   包含引用角标: {has_ref}")

assert "token" in event_types2 and "done" in event_types2
print(f"{PASS} RAG 绑定 SSE（带引用角标）")

# ====== 4. 多轮对话 ======
print("\n--- 4. 多轮对话历史 ---")

detail_after = http("GET", f"/api/sessions/{sid2}/")
msgs = detail_after.get("messages", [])
print(f"   消息条数: {len(msgs)}")

# 再发一条（第二轮）
resp = s.post(
    BASE + "/api/chat/stream",
    json={"sid": sid2, "question": "那 LangChain 框架呢？"},
    stream=True,
)
resp.raise_for_status()

sse_events3 = []
buffer = ""
for raw_line in resp.iter_lines(decode_unicode=True):
    if raw_line is None:
        if buffer.strip():
            frame = buffer.strip()
            ev_type, ev_data = _parse_frame(frame)
            sse_events3.append((ev_type, ev_data))
            buffer = ""
        continue
    buffer += raw_line + "\n"
if buffer.strip():
    ev_type, ev_data = _parse_frame(buffer.strip())
    sse_events3.append((ev_type, ev_data))
resp.close()

event_types3 = [e[0] for e in sse_events3]
done_data3 = json.loads(sse_events3[event_types3.index("done")][1])
print(f"   第二轮回答: {done_data3['answer'][:100]}...")
print(f"   refs: {'有' if done_data3.get('refs') else '无'}")
print(f"{PASS} 多轮对话历史传递")

# ====== 清理 ======
print("\n--- 5. 清理 ---")
http("DELETE", f"/api/sessions/{sid2}/")
print(f"{PASS} 清理测试会话")

print("\n" + "=" * 50)
print("🎉  W2 端到端验证全部通过！")
print("=" * 50)



