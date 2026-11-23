# -*- coding: utf-8 -*-
"""从交付文档里抽回 P1 / W5 两段 diff，按当前工作区实际内容重算（仅生成 diff 文本，不落盘到工作区）。"""
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(r"c:\biancheng\agent-hub")
DOC = ROOT / "docs" / "修复Diff_2026-11-10_P1标记中和与W5工作流接线.md"
OUT = ROOT / ".scratch" / "p1w5_recompute"
OUT.mkdir(parents=True, exist_ok=True)

doc = DOC.read_text(encoding="utf-8")
blocks = re.findall(r"```diff\n(.*?)```", doc, re.S)
p1 = next(b for b in blocks if "core/data_boundary.py" in b)
w5 = next(b for b in blocks if "hybrid/workflow.py" in b)


def run(*args: str) -> tuple[int, str]:
    p = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


print("=== ① 文档里的 P1 diff 直接对当前工作区做 git apply --check ===")
f_old = OUT / "p1.doc.diff"
f_old.write_text(p1, encoding="utf-8", newline="\n")
rc, out = run("git", "apply", "--check", str(f_old))
print("rc =", rc)
print(out or "(无输出)")

print()
print("=== ② 唯一失配点：_NEUTRALIZED 的取值（工作区已被改为空串）===")
print("工作区实际：", [ln for ln in (ROOT / "core" / "data_boundary.py").read_text(encoding="utf-8").splitlines()
                   if ln.startswith("_NEUTRALIZED")])
print("文档上下文：", [ln for ln in p1.splitlines() if ln.startswith(" _NEUTRALIZED")])

p1_new = p1.replace(
    ' _NEUTRALIZED = ""',
    '-_NEUTRALIZED = ""\n+_NEUTRALIZED = "［资料边界标记已移除］"',
)
f_new = OUT / "p1.recomputed.diff"
f_new.write_text(p1_new, encoding="utf-8", newline="\n")
print()
print("=== ③ 重算后的 P1 diff 再 check ===")
rc, out = run("git", "apply", "--check", str(f_new))
print("rc =", rc)
print(out or "(无输出：可应用)")

print()
print("=== ④ W5 diff 对当前工作区 check ===")
f_w5 = OUT / "w5.diff"
f_w5.write_text(w5, encoding="utf-8", newline="\n")
rc, out = run("git", "apply", "--check", str(f_w5))
print("rc =", rc)
print(out or "(无输出：可应用)")

print()
print("=== ⑤ 两段重算 diff 的 hunk 头与实际行号 ===")
for name, text in (("P1", p1_new), ("W5", w5)):
    print(f"[{name}]")
    for ln in text.splitlines():
        if ln.startswith("@@"):
            print("  ", ln)
print()
print("文件行数：data_boundary.py =", len((ROOT / "core" / "data_boundary.py").read_text(encoding="utf-8").splitlines()),
      "| workflow.py =", len((ROOT / "hybrid" / "workflow.py").read_text(encoding="utf-8").splitlines()))
print("diffs written to:", OUT)
