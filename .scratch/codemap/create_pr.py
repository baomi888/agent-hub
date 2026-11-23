# -*- coding: utf-8 -*-
"""创建 PR（base=dev）。凭据只在内存里，不落盘、不打印。"""
import json
import subprocess
import urllib.error
import urllib.request

REPO = "baomi888/agent-hub"
HEAD = "refactor/chat-decouple"
BASE = "dev"
ISSUE = 18

body = f"""摘要与风险见 #{ISSUE}。

## 提交
1. `chore(gitignore)`：忽略 pytest 缓存与临时输出
2. `feat(stats)`：route_stats 路由判定分布（先前 WIP，一并提交，测试已覆盖）
3. `refactor(chat)`：检索编排抽 `kb/retrieval.py`、SSE 编码抽 `api/sse_protocol.py`、
   `StreamOutcome` 取代 `_Acc`

## 验证
- 全量 pytest **923 passed / 1 skipped**
- RAG 帧序列基线测试不变；迁移前后逐帧计数完全一致
- `api/chat.py` 2290 → 2207 行，`_sse(` 归零

## 建议 review 顺序
1. `kb/retrieval.py`（新增，130 行）——看异常策略与"只返回数据不发帧"
2. `api/sse_protocol.py`（新增，111 行）——看帧格式是否与前端 `lib/sse.ts` 对齐
3. `api/chat.py` 的三个调用点（图片 / 智能体 / RAG）与收尾

Closes #{ISSUE}
"""

payload = json.dumps(
    {"title": "refactor(chat): 检索编排与 SSE 编码解耦", "head": HEAD, "base": BASE, "body": body},
    ensure_ascii=False,
).encode("utf-8")

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
    f"https://api.github.com/repos/{REPO}/pulls",
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
    print("PR #%s -> %s" % (data["number"], data["html_url"]))
    print("head:", data["head"]["ref"], "base:", data["base"]["ref"])
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read().decode("utf-8", "replace")[:800])
