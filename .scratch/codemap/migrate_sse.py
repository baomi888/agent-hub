# -*- coding: utf-8 -*-
"""把 api/chat.py 里所有 _sse(event, payload) 调用改写成 sse.<builder>(...) 。

用 ast 定位每个 Call 的精确字节区间，避免正则把 f-string 里的花括号吃坏。
"""
import ast
import io

P = r"C:\biancheng\agent-hub\api\chat.py"
src = io.open(P, encoding="utf-8").read()

# 字节级行偏移（ast 的 col_offset 是 UTF-8 字节偏移）
data = src.encode("utf-8")
line_starts = [0]
for line in data.splitlines(keepends=True):
    line_starts.append(line_starts[-1] + len(line))


def off(lineno: int, col: int) -> int:
    return line_starts[lineno - 1] + col


# frame -> (builder, dict_key_to_unwrap or None)
SIMPLE = {
    "status": ("status", "text"),
    "token": ("token", "text"),
    "error": ("error", "detail"),
    "title": ("title", "title"),
    "sources": ("sources", "items"),
}
PASSTHROUGH = {"grounding", "tool", "approval", "workflow_node", "mode_switch", "hybrid_start", "done"}

tree = ast.parse(src)
edits = []
unknown = []

for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    if not (isinstance(node.func, ast.Name) and node.func.id == "_sse"):
        continue
    if len(node.args) != 2 or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        unknown.append(ast.dump(node)[:80])
        continue
    frame = node.args[0].value
    payload = node.args[1]
    payload_src = ast.get_source_segment(src, payload)

    if frame in SIMPLE:
        builder, key = SIMPLE[frame]
        if not (isinstance(payload, ast.Dict) and len(payload.keys) == 1):
            unknown.append(f"{frame}: 不是单键字典")
            continue
        k = payload.keys[0]
        if not (isinstance(k, ast.Constant) and k.value == key):
            unknown.append(f"{frame}: key={getattr(k, 'value', '?')} != {key}")
            continue
        value_src = ast.get_source_segment(src, payload.values[0])
        new = f"sse.{builder}({value_src})"
    elif frame == "truncation":
        if not (isinstance(payload, ast.Dict) and len(payload.keys) == 2):
            unknown.append("truncation: payload 结构不符")
            continue
        value_src = ast.get_source_segment(src, payload.values[0])  # reason
        new = f"sse.truncation({value_src})"
    elif frame in PASSTHROUGH:
        new = f"sse.{frame}({payload_src})"
    else:
        unknown.append(frame)
        continue

    edits.append((off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset), new))

if unknown:
    raise SystemExit("有无法识别的调用，先人工处理：" + repr(unknown))

print("待改写调用数:", len(edits))
for start, end, new in sorted(edits, reverse=True):
    data = data[:start] + new.encode("utf-8") + data[end:]

out = data.decode("utf-8")

# 删掉本地 _sse 定义
lines = out.splitlines(keepends=True)
start_i = next(i for i, l in enumerate(lines) if l.startswith("def _sse("))
end_i = next(
    i
    for i in range(start_i + 1, len(lines))
    if lines[i].startswith("def ") or lines[i].startswith("async def ")
)
del lines[start_i:end_i]
out = "".join(lines)

# 加 import
out = out.replace(
    "from core import (\n",
    "from api import sse_protocol as sse\nfrom core import (\n",
    1,
)

io.open(P, "w", encoding="utf-8", newline="\n").write(out)

print("残留 _sse(:", out.count("_sse("))
print("sse. 调用数:", out.count("sse."))
