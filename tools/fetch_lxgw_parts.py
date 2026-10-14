#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载 LXGW WenKai Screen 的全部 unicode-range 分片（jsdelivr 上的 webfont 包），
用 fontTools 合并成完整 TTF，供 subset_fonts_project.py 子集化。

之所以不直接下完整 TTF：GitHub Release 直连在本机返回 502，npm 无完整版包。
分片是静态 woff2，合并比可变字体稳（不会踩 VarStore 合并坑）。
"""
import os
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

from fontTools.merge import Merger
from fontTools.ttLib import TTFont

PKG = "lxgw-wenkai-screen-webfont@1.7.0"
BASE = f"https://cdn.jsdelivr.net/npm/{PKG}/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PART_DIR = os.path.join(ROOT, "tools", "_fontsrc", "lxgw_parts")
OUT = os.path.join(ROOT, "tools", "_fontsrc", "LXGWWenKaiScreen.ttf")


def fetch_list():
    url = f"https://data.jsdelivr.com/v1/packages/npm/{PKG}?structure=flat"
    raw = subprocess.run(
        ["curl", "-sL", "-m", "30", url], capture_output=True, text=True
    ).stdout
    data = json.loads(raw)
    return [
        f["name"]
        for f in data.get("files", [])
        if f["name"].endswith(".woff2")
        and f["name"].startswith("/files/lxgwwenkaiscreen-subset-")
    ]


def dl(name):
    url = BASE + name.lstrip("/")
    dst = os.path.join(PART_DIR, os.path.basename(name))
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    for _ in range(3):
        r = subprocess.run(
            ["curl", "-sL", "-m", "60", "-o", dst, url],
            capture_output=True,
        )
        if r.returncode == 0 and os.path.getsize(dst) > 0:
            return dst
    print("FAIL", name)
    return None


def main():
    os.makedirs(PART_DIR, exist_ok=True)
    woff2 = fetch_list()
    print("screen 分片数:", len(woff2))
    with ThreadPoolExecutor(max_workers=16) as ex:
        parts = [p for p in ex.map(dl, woff2) if p]
    print("下载成功:", len(parts))
    if not parts:
        raise SystemExit("没有下载到任何分片")
    print("合并中（388 个静态 woff2，可能要 1~2 分钟）...")
    merger = Merger()
    merged = merger.merge(parts)  # Merger 接收「路径列表」，不要预先 TTFont()
    merged.save(OUT)
    print("合并完成:", OUT, f"{os.path.getsize(OUT)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
