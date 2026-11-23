# -*- coding: utf-8 -*-
"""用 UTF-8 解码 git 输出，避开 PowerShell 控制台代码页对中文路径的破坏。"""
import subprocess

ROOT = r"C:\biancheng\agent-hub"


def run(*args):
    return subprocess.run(
        ["git", "-C", ROOT, "-c", "core.quotepath=false", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout


tracked = [x for x in run("ls-files").splitlines() if x]
status = [x for x in run("status", "--porcelain", "-uall").splitlines() if x]

print("部署说明.md tracked ?", "部署说明.md" in tracked)
print("部署说明.md 出现在 status ?", any("部署说明" in s for s in status))
print("总跟踪文件数:", len(tracked), " status 行数:", len(status))

for s in status:
    if "部署" in s or "probe" in s:
        print("  ", s)

print("\n--- 根目录一级条目在 git 里的状态 ---")
names = sorted({s[3:].strip('"').split("/")[0] for s in status if len(s) > 3})
print("未跟踪/改动的根级条目:", names[:30])
