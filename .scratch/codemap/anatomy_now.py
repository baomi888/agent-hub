# -*- coding: utf-8 -*-
"""用 ast 精确测量 api/chat.py 的结构（行号、各分支跨度、局部变量池）。"""
import ast
import io
import re

P = r"C:\biancheng\agent-hub\api\chat.py"
src = io.open(P, encoding="utf-8").read()
lines = src.splitlines()
tree = ast.parse(src)


def span(node):
    return node.lineno, node.end_lineno, node.end_lineno - node.lineno + 1


print("总行数:", len(lines))
print()

# 顶层结构
top_funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
print("顶层函数 %d 个，总行 %d" % (len(top_funcs), sum(span(f)[2] for f in top_funcs)))
print()

impl = next(f for f in top_funcs if f.name == "_chat_stream_impl")
a, b, n = span(impl)
print("_chat_stream_impl: %d-%d = %d 行" % (a, b, n))

gen = None


def walk(node):
    global gen
    for c in ast.iter_child_nodes(node):
        if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c.name == "event_generator":
            gen = c
        walk(c)


walk(impl)
if gen is not None:
    ga, gb, gn = span(gen)
    print("  event_generator (嵌套): %d-%d = %d 行" % (ga, gb, gn))
    print("  外层壳（签名 + 前置校验 + 建流）: %d 行" % (gn and (n - gn)))

print()
# event_generator 内的 if/elif 链
target = gen or impl
chain = []
for node in ast.walk(target):
    if isinstance(node, ast.If):
        seg = ast.get_source_segment(src, node.test) or ""
        if re.search(r"mode ==|_has_image", seg):
            st, en, ln = span(node)
            chain.append((st, en, ln, " ".join(seg.split())[:48]))
seen = set()
print("--- 分支判定（行号 / 跨度 / 条件）---")
for st, en, ln, seg in sorted(chain):
    if st in seen:
        continue
    seen.add(st)
    print("  %5d  %4d 行  %s" % (st, ln, seg))

print()
# 事件生成器里的局部变量：只看最外层赋值
assigned = []
for node in ast.walk(target):
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in tgts:
            if isinstance(t, ast.Name):
                assigned.append((node.lineno, t.id))
uniq = {}
for ln, name in assigned:
    uniq.setdefault(name, ln)
print("--- event_generator 内被赋值的局部名（去重 %d 个）---" % len(uniq))
print("  " + ", ".join(sorted(uniq)))

print()
print("yield 数量:", sum(1 for node in ast.walk(target) if isinstance(node, ast.Yield)))
print("return 数量:", sum(1 for node in ast.walk(target) if isinstance(node, ast.Return)))
