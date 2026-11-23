# -*- coding: utf-8 -*-
"""对比 HEAD~1 与 HEAD 的 api/chat.py：最大函数长度、分支块尺寸、局部名字数。"""
import ast
import subprocess

ROOT = r"C:\biancheng\agent-hub"


def load(ref):
    args = ["git", "-C", ROOT, "show", f"{ref}:api/chat.py"] if ref else None
    if args:
        return subprocess.run(args, capture_output=True).stdout.decode("utf-8")
    import io

    return io.open(ROOT + r"\api\chat.py", encoding="utf-8").read()


def find_fn(node, name):
    for c in ast.iter_child_nodes(node):
        if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c.name == name:
            return c
        r = find_fn(c, name)
        if r:
            return r
    return None


def report(src, label):
    tree = ast.parse(src)
    lines = src.split("\n")
    funcs = [
        (n.end_lineno - n.lineno + 1, n.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    funcs.sort(reverse=True)
    impl = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_chat_stream_impl"
    )
    gen = find_fn(impl, "event_generator")

    # 分支块（只算 if 的 body）
    branches = []
    if gen:
        for node in ast.walk(gen):
            if isinstance(node, ast.If):
                seg = " ".join((ast.get_source_segment(src, node.test) or "").split())
                if "mode ==" in seg or "_has_image" in seg:
                    branches.append((node.body[-1].end_lineno - node.body[0].lineno + 1, seg[:40]))

    # 局部名字
    names = set()
    if gen:
        for node in ast.walk(gen):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in tgts:
                    if isinstance(t, ast.Name):
                        names.add(t.id)

    yields = sum(1 for n in ast.walk(gen) if isinstance(n, ast.Yield)) if gen else 0
    returns = sum(1 for n in ast.walk(gen) if isinstance(n, ast.Return)) if gen else 0

    print("=" * 58)
    print(label, "| 总行", len(lines) - 1)
    print("  最大的 3 个函数：", ", ".join(f"{n}行/{name}" for n, name in funcs[:3]))
    print("  _chat_stream_impl:", impl.end_lineno - impl.lineno + 1, "行")
    if gen:
        print("  event_generator  :", gen.end_lineno - gen.lineno + 1, "行")
        print("  分支块尺寸       :", sorted(branches, reverse=True))
        print("  局部名字         :", len(names))
        print("  yield / return   :", yields, "/", returns)


report(load("HEAD~1"), "重构前 (HEAD~1)")
report(load("HEAD"), "重构后 (HEAD)")
