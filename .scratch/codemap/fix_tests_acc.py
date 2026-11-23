# -*- coding: utf-8 -*-
"""同步两个测试文件里的类名。"""
import io

FILES = {
    r"C:\biancheng\agent-hub\tests\test_sse_disconnect.py": [
        ("chat_mod._Acc()", "chat_mod.StreamOutcome()"),
        ("`_Acc.aborted=True`", "`StreamOutcome.aborted=True`"),
    ],
    r"C:\biancheng\agent-hub\tests\test_truncation_detect.py": [
        ("_Acc, _is_truncation", "StreamOutcome, _is_truncation"),
        ("acc = _Acc()", "acc = StreamOutcome()"),
    ],
}

for path, pairs in FILES.items():
    src = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        n = src.count(old)
        if n == 0:
            print(f"  [跳过] {path.split(chr(92))[-1]}: 未找到 {old!r}")
            continue
        src = src.replace(old, new)
        print(f"  [替换x{n}] {path.split(chr(92))[-1]}: {old!r} -> {new!r}")
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)

for path in FILES:
    left = io.open(path, encoding="utf-8").read().count("_Acc")
    print(f"残留 _Acc in {path.split(chr(92))[-1]}: {left}")
