# -*- coding: utf-8 -*-
"""创建「三条路径缺 HTTP 级帧序列测试」的 issue。凭据只在内存里，不落盘、不打印。"""
import json
import subprocess
import urllib.error
import urllib.request

REPO = "baomi888/agent-hub"

TITLE = "test(chat): 补 vision / agent / hybrid 三条路径的 HTTP 级帧序列基线"

BODY = """## 背景

#18 / #19 把 SSE 编码从 `api/chat.py` 的 58 处调用收进了 `api/sse_protocol.py`。协议层的**单帧字节**已被 `tests/test_sse_protocol.py`（11 条）锁死，但「某条业务路径会不会少发 / 多发 / 乱序一帧」仍无自动化保护。

当时的验证方式是临时脚本逐帧计数比对（`status 17/17`、`token 10/10`、`tool 3/3` …），**该脚本没有入库**，因此不构成任何回归防护。

## 现状

| 路径 | HTTP 级帧序列测试 | 说明 |
|---|---|---|
| `llm` | 有 | `test_llm_stream_event_sequence`，6 帧全断言 |
| `rag` | 有 | `test_rag_stream_event_sequence`，10 帧 + `done` 字段集合 |
| `hybrid` | 部分 | `test_hybrid_answer_pick.py` 直接调 `_chat_stream_impl`，只断言「兜底答案有没有落库」，不看帧 |
| `agent` | 无 | — |
| `vision` | 无 | — |

注：`test_truncation_detect.py` 覆盖的是 `_stream_llm` / `_is_truncation` 的**函数级**行为，不是 HTTP 链路。

## 从来没有任何断言覆盖过的帧

| 帧 | 唯一产出点 | 前端消费 |
|---|---|---|
| `tool` | `api/chat.py:1732` / `1738`（agent）、`1466`（hybrid） | `frontend/src/app/page.tsx:446` |
| `approval` | `api/chat.py:1756`（agent） | `frontend/src/app/page.tsx` |
| `workflow_node` | `api/chat.py:1475`（hybrid） | `page.tsx:378`、`components/HybridTrace.tsx` |
| `mode_switch` | `api/chat.py:1481`（hybrid） | `page.tsx:398`、`lib/useHybridTrace.ts:117` |
| `hybrid_start` | `api/chat.py:1430`（hybrid） | `page.tsx:374`、`components/HybridTrace.tsx:107` |

这五类帧的共同特征：**前端有专门的渲染分支，后端一旦不发，界面静默少一块，不报错**。工具卡片、审批卡片、执行轨迹都属于这一类。

`vision` 路径的 `sources` / `grounding`（`1266` / `1268`）同样没有任何序列断言。

## 为什么这三条难写

`test_baseline_chat_stream.py` 现有的两个假实现只覆盖了 `LLM.astream` 和 `RagPipeline`。这三条路要伪造的东西更深：

1. **agent** —— 要按 LangGraph `astream(stream_mode=["updates","messages"])` 的事件形状产 `(mode_str, payload)` 二元组，payload 里还得塞带 `tool_calls` 的 `AIMessage` 才能触发 `tool` 帧。
2. **hybrid** —— 要伪造 `HybridOrchestrator` 产 `ExecEvent`（`token` / `error` / `switched` / `node_enter` / `node_exit`）。`test_hybrid_answer_pick.py` 里的 `_PlanOnlyOrch` 已是雏形，可提取复用。
3. **vision** —— 要构造 `req.attachments` 并把 `get_vision_llm` 换成假实现。

共同风险：**假实现本身会漂移**。上游事件形状一变、假实现不跟着变，测试就变成「假绿」（断言通过但真实链路已坏）。缓解办法是在假实现里加形状断言（如 `assert len(event) == 2`、`assert isinstance(payload, tuple)`），让漂移变成红灯而不是静默。

## 建议顺序（本文只记录，不排期）

1. **agent** —— 优先级最高。`tool` 帧完全无断言，实现也最直接（假 `astream` + 一个带 `tool_calls` 的 chunk）。
2. **hybrid** —— `_PlanOnlyOrch` 已有雏形，主要工作是接进 HTTP 链路，并断言 `hybrid_start` / `workflow_node` / `mode_switch` 的相对位置。
3. **vision** —— 最麻烦（附件构造），风险最低（只有 `sources` / `grounding` 两帧与其它路径重叠）。

## 验收标准

- 三条各有一个 `test_<path>_stream_event_sequence`，断言完整帧名列表
- 至少各断言一次 `tool` / `hybrid_start` / `workflow_node` / `mode_switch` 的实际出现
- 假实现内带形状断言，防止上游格式变化时静默通过

## 声明

记这条是为了把「这三条没保护」从口头约定变成可检索的事实，**不代表当前有 bug**：三条路径现在都正常，PR #19 已用一次性脚本验证过。新增测试属于回归防护，不改任何现有行为。
"""

payload = json.dumps({"title": TITLE, "body": BODY}, ensure_ascii=False).encode("utf-8")

cred = subprocess.run(
    ["git", "credential", "fill"],
    input=b"protocol=https\nhost=github.com\n\n",
    capture_output=True,
).stdout.decode("utf-8", "replace")
token = ""
for line in cred.splitlines():
    if line.startswith("password="):
        token = line.split("=", 1)[1]
if not token:
    raise SystemExit("没有取到凭据")

req = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/issues",
    data=payload,
    method="POST",
    headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "agent-hub-refactor",
    },
)

try:
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print("ISSUE #%s -> %s" % (data["number"], data["html_url"]))
    print("state:", data["state"], "| labels:", [x["name"] for x in data.get("labels", [])])
    print("title:", data["title"])
    print("body len:", len(data.get("body") or ""), "| 含替换字符:", "\ufffd" in (data.get("body") or ""))
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read().decode("utf-8", "replace")[:800])
