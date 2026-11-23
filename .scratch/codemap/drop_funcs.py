# -*- coding: utf-8 -*-
"""删掉两个已搬走的函数定义（_retrieval_query / _references_text），保持文件可编译。"""
import io
import re

P = r"C:\biancheng\agent-hub\api\chat.py"
lines = io.open(P, encoding="utf-8").read().splitlines(keepends=True)


def drop(name: str) -> str:
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        return f"{name}: 未找到（可能已删）"
    end = None
    for i in range(start + 1, len(lines)):
        if re.match(r"^(async )?def ", lines[i]):
            end = i
            break
    if end is None:
        raise SystemExit(f"{name}: 找不到下一个函数边界")
    removed = end - start
    del lines[start:end]
    return f"{name}: 删除 {removed} 行（{start + 1} 起）"


print(drop("_retrieval_query"))
print(drop("_references_text"))

io.open(P, "w", encoding="utf-8", newline="\n").writelines(lines)

src = "".join(lines)
for token in ("_retrieval_query", "def _references_text", "assess_grounding", "enhance_svc", "rewrite_query"):
    print(f"残留 {token}: {src.count(token)}")
