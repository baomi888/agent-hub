# -*- coding: utf-8 -*-
"""检查最近两条提交的 message 字节是否是正确的 UTF-8（PowerShell 传参会按 GBK 编码）。"""
import subprocess

ROOT = r"C:\biancheng\agent-hub"


def run(*args: str) -> bytes:
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True).stdout


for rev in ("HEAD", "HEAD~1"):
    raw = run("log", "-1", "--format=%B", rev)
    print(f"--- {rev} raw bytes head: {raw[:24]!r}")
    try:
        print("    utf-8 解码:", raw.decode("utf-8").splitlines()[0])
    except UnicodeDecodeError as e:
        print("    ✗ 不是合法 UTF-8:", e)
        print("    gbk 解码:", raw.decode("gbk", "replace").splitlines()[0])
