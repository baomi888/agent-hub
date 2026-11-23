# -*- coding: utf-8 -*-
"""只统计代码行（剔除注释/文档串），避免把解释性文字算进重复度。"""
import io
import re

P = r"C:\biancheng\agent-hub\api\chat.py"
raw = io.open(P, encoding="utf-8").read().splitlines()
lines = []
for ln in raw:
    code = ln.split("#", 1)[0]
    code = re.sub(r'""".*?"""', "", code)
    lines.append(code)

START, END = 1140, 1982
seg = lines[START - 1:END - 1]

def n(pat):
    return len(re.findall(pat, "\n".join(seg)))

print("=== 代码行（已剔除注释）里的实际重复 ===")
for label, pat in [
    ("store.add_message", r"store\.add_message"),
    ("_sse(\"sources\"", r'_sse\("sources"'),
    ("_sse(\"grounding\"", r'_sse\("grounding"'),
    ("_sse(\"error\"", r'_sse\("error"'),
    ("_sse(\"status\"", r'_sse\("status"'),
    ("_sse(\"token\"", r'_sse\("token"'),
    ("_sse(\"done\"", r'_sse\("done"'),
    ("guard.feed", r"guard\.feed"),
    ("guard.flush", r"guard\.flush"),
    ("guard.blocked", r"guard\.blocked"),
    ("acc.aborted or acc.error", r"acc\.aborted or acc\.error"),
    ("_schedule_compress(", r"_schedule_compress\("),
    ("_record_user_tags(", r"_record_user_tags\("),
    ("_take_title(", r"_take_title\("),
    ("rename_conversation", r"rename_conversation"),
    ("return（提前退出）", r"^\s+return\s*$"),
    ("asyncio.to_thread", r"asyncio\.to_thread"),
]:
    print(f"  {label:<26} {n(pat)}")

print("\n=== 帧类型分布 ===")
from collections import Counter
print(" ", Counter(re.findall(r'_sse\("([a-z_]+)"', "\n".join(seg))).most_common())

print("\n=== 各分支的长度与 yield 数 ===")
bounds = [1262, 1270, 1370, 1589, 1822, 1894, 1919]
names = ["入站护栏", "vision", "hybrid", "agent", "rag", "llm/else", "共享收尾"]
for i, nm in enumerate(names):
    a, b = bounds[i], bounds[i + 1]
    chunk = "\n".join(lines[a - 1:b - 1])
    print(f"  {nm:<10} 行 {b-a:>4}   yield {len(re.findall(r'yield ', chunk)):>3}   return {len(re.findall(r'^\s+return\s*$', chunk, re.M)):>2}")
