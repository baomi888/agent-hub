# -*- coding: utf-8 -*-
"""迁移前后逐帧计数比对：任何一类帧数量变化都说明有调用点被吃掉了。"""
import io
import re
import subprocess

ROOT = r"C:\biancheng\agent-hub"
old = subprocess.run(
    ["git", "-C", ROOT, "show", "HEAD:api/chat.py"],
    capture_output=True, text=True, encoding="utf-8",
).stdout
new = io.open(ROOT + r"\api\chat.py", encoding="utf-8").read()

frames = ["status", "token", "error", "sources", "grounding", "title",
          "truncation", "done", "tool", "workflow_node", "mode_switch",
          "hybrid_start", "approval"]

print(f"{'帧':<16}{'HEAD':>6}{'现在':>6}")
bad = []
for f in frames:
    o = len(re.findall(rf'_sse\(\s*"{f}"', old))
    n = len(re.findall(rf"sse\.{f}\(", new))
    flag = "" if o == n else "  ← 不一致"
    if o != n:
        bad.append((f, o, n))
    print(f"{f:<16}{o:>6}{n:>6}{flag}")

print("\nHEAD 里 _sse 总数:", len(re.findall(r"_sse\(", old)))
print("现在 _sse 总数:", len(re.findall(r"_sse\(", new)))
print("不一致项:", bad or "无")
