# -*- coding: utf-8 -*-
"""把 _Acc / acc 统一改名为 StreamOutcome / outcome，并把 4 处中断判断收敛到 .interrupted。"""
import io
import re

P = r"C:\biancheng\agent-hub\api\chat.py"
src = io.open(P, encoding="utf-8").read()

before = {
    "_Acc": len(re.findall(r"\b_Acc\b", src)),
    "acc": len(re.findall(r"\bacc\b", src)),
}

src = re.sub(r"\b_Acc\b", "StreamOutcome", src)
src = re.sub(r"\bacc\b", "outcome", src)

# 4 处「断连或报错」判定 → 一个谓词
n_interrupted = src.count("outcome.aborted or outcome.error")
src = src.replace("outcome.aborted or outcome.error", "outcome.interrupted")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)

print("改名前:", before)
print("改为 outcome.interrupted 的处数:", n_interrupted)
print("残留 _Acc:", len(re.findall(r"\b_Acc\b", src)))
print("残留 acc(独立词):", len(re.findall(r"\bacc\b", src)))
