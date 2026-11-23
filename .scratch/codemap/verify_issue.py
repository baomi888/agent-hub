# -*- coding: utf-8 -*-
"""核对 GitHub 上 issue #18 的真实内容（不打印任何凭据）。"""
import json
import subprocess

REPO = "baomi888/agent-hub"
NUM = 18

raw = subprocess.run(
    ["curl.exe", "-sS", "-m", "30", f"https://api.github.com/repos/{REPO}/issues/{NUM}"],
    capture_output=True,
).stdout
data = json.loads(raw.decode("utf-8"))

print("title:", data.get("title"))
print("state:", data.get("state"))
print("url:", data.get("html_url"))
body = data.get("body") or ""
print("body 前 120 字:", body[:120].replace("\n", " / "))
print("body 长度:", len(body))
print("是否含替换字符 \\ufffd:", "\ufffd" in body or "\ufffd" in (data.get("title") or ""))
