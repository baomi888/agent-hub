# -*- coding: utf-8 -*-
"""量化 api/chat.py 里 _chat_stream_impl 的结构：分支跨度、出口数、重复副作用。"""
import io
import re

P = r"C:\biancheng\agent-hub\api\chat.py"
lines = io.open(P, encoding="utf-8").read().splitlines()

def find_def(name):
    for i, ln in enumerate(lines):
        if re.match(rf"^(async )?def {name}\b", ln):
            return i
    return -1

start = find_def("_chat_stream_impl")
end = find_def("_references_text")
print(f"_chat_stream_impl: 第 {start+1} ~ {end} 行，共 {end-start} 行")
print(f"api/chat.py 总行数: {len(lines)}")

body = lines[start:end]

# 分支标记
marks = []
for i, ln in enumerate(body):
    if re.search(r"# ---- \d\.", ln):
        marks.append((start + i + 1, ln.strip()))
print("\n分支标记：")
for ln_no, txt in marks:
    print(f"  {ln_no:>5}  {txt}")

# 各分支跨度
bounds = [m[0] for m in marks] + [end]
print("\n分支跨度：")
for i, (ln_no, txt) in enumerate(marks):
    span = bounds[i + 1] - ln_no
    print(f"  {txt[:34]:<36} {span:>4} 行")

def count_in(lo, hi, pattern):
    seg = "\n".join(lines[lo - 1:hi - 1])
    return len(re.findall(pattern, seg))

print("\n重复的副作用调用（整段 _chat_stream_impl 内）：")
for name in ["add_message", "_schedule_compress", "_take_title", "_record_user_tags",
             "_sse(\"done\"", "_sse(\"sources\"", "_sse(\"grounding\"", "guard.feed",
             "if acc.aborted or acc.error", "return"]:
    print(f"  {name:<28} {count_in(start+1, end, re.escape(name)) if '(' not in name else len(re.findall(re.escape(name), chr(10).join(body)))} 次")

print("\nevent_generator 里 yield 的帧类型：")
frames = re.findall(r'_sse\("([a-z_]+)"', "\n".join(body))
from collections import Counter
for k, v in Counter(frames).most_common():
    print(f"  {k:<16} {v} 次")
print(f"  合计 yield 帧: {len(frames)}")
