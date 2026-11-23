# -*- coding: utf-8 -*-
"""把 Step 1 评审所需的文件分目录复制并打包成 zip。"""
import os
import shutil
import zipfile

ROOT = r"C:\biancheng\agent-hub"
STAGE = os.path.join(ROOT, ".scratch", "step1-review")
ZIP = os.path.join(ROOT, ".scratch", "step1-review.zip")

GROUPS = {
    "01-change": [
        ("api/sse_protocol.py", "sse_protocol.py"),
        ("api/chat.py", "chat.py"),
        ("tests/test_sse_protocol.py", "test_sse_protocol.py"),
    ],
    "02-read-only": [
        ("frontend/src/lib/sse.ts", "sse.ts"),
        ("frontend/src/app/page.tsx", "page.tsx"),
        ("frontend/src/lib/types.ts", "types.ts"),
        ("tests/test_baseline_chat_stream.py", "test_baseline_chat_stream.py"),
    ],
    "03-guardrails": [
        ("tests/golden_stream.py", "golden_stream.py"),
        ("tests/test_stream_golden.py", "test_stream_golden.py"),
        ("tests/golden/rag.json", "golden-rag.json"),
        ("tests/golden/llm.json", "golden-llm.json"),
    ],
}

missing = []
for group, items in GROUPS.items():
    d = os.path.join(STAGE, group)
    os.makedirs(d, exist_ok=True)
    for src_rel, dst_name in items:
        src = os.path.join(ROOT, src_rel.replace("/", os.sep))
        if not os.path.exists(src):
            missing.append(src_rel)
            continue
        shutil.copy2(src, os.path.join(d, dst_name))

if os.path.exists(ZIP):
    os.remove(ZIP)

count = 0
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, _dirs, files in os.walk(STAGE):
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            arc = os.path.join("step1-review", os.path.relpath(full, STAGE))
            z.write(full, arc.replace(os.sep, "/"))
            count += 1

print("打包目录:", STAGE)
print("zip:", ZIP)
print("文件数:", count, "| zip 大小: %.1f KB" % (os.path.getsize(ZIP) / 1024))
print()
print("--- 包内清单 ---")
with zipfile.ZipFile(ZIP) as z:
    for info in z.infolist():
        print("  %-56s %7d 字节" % (info.filename, info.file_size))
if missing:
    print("\n缺失:", missing)
