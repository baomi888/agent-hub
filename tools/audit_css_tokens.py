#!/usr/bin/env python3
"""样式令牌自审：找出 var(--x) 引用了「谁都没定义」的令牌。

为什么需要这个脚本：
    CSS 自定义属性不存在时**不报错、不警告** —— `background: var(--gold)` 里
    只要 `--gold` 没定义，整条声明就按 invalid-at-computed-value-time 作废，
    元素直接没有背景色。开发时不看渲染结果根本发现不了。

    本轮就踩到了：`prototypes/知穗_新页面设计稿.html` 在它自己的 :root 里定义了
    `--gold: #F5B840`（品牌玉米金），样式移植进 frontend 后 tokens.css 并没有这个
    令牌 —— 总结页的活跃时段柱子、设置页的用量条、打卡页的圆点全部静默消失。

用法：
    python tools/audit_css_tokens.py                 # 审 frontend/src/app/styles
    python tools/audit_css_tokens.py <目录> [<目录>]  # 审指定目录下的 *.css
"""

import glob
import os
import re
import sys

# var(--x) / var(--x, fallback) 都要抓；只记录变量名
USE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
# 定义： --x: value    （含 @theme / @media 块里的）
DEF_RE = re.compile(r"^\s*(--[A-Za-z0-9_-]+)\s*:", re.M)
# /* ... */ 注释要先剥掉：注释里提到某个坏令牌名（例如「原来是 var(--muted)」）
# 会造成误报 —— 这个脚本自己就踩过一次
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)

# Tailwind v4 自己注入的命名空间，文件里看不到定义属正常
IGNORE_PREFIX = ("--tw-",)


def collect(paths):
    """返回 (uses, defined)：uses 是 {文件: {令牌: 次数}}"""
    uses = {}
    defined = set()
    files = []
    for p in paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "*.css"))))
        else:
            files.append(p)
    for f in files:
        raw = open(f, encoding="utf-8").read()
        src = COMMENT_RE.sub("", raw)
        hits = [t for t in USE_RE.findall(src) if not t.startswith(IGNORE_PREFIX)]
        if hits:
            uses[os.path.basename(f)] = {t: hits.count(t) for t in set(hits)}
        defined.update(DEF_RE.findall(src))
    return uses, defined, files


def main() -> int:
    args = sys.argv[1:] or ["frontend/src/app/styles"]
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    paths = [a if os.path.isabs(a) else os.path.join(root, a) for a in args]

    uses, defined, files = collect(paths)
    if not files:
        print("没有找到任何 .css 文件：", paths)
        return 1

    print(f"扫描 {len(files)} 个样式文件｜已定义令牌 {len(defined)} 个")
    total_bad = 0
    for fname, table in uses.items():
        bad = {t: n for t, n in table.items() if t not in defined}
        if not bad:
            continue
        total_bad += len(bad)
        print(f"\n[{fname}] 未定义令牌 {len(bad)} 个：")
        for t, n in sorted(bad.items()):
            print(f"    {t}  （引用 {n} 处）")

    if total_bad:
        print(f"\n结论：发现 {total_bad} 个未定义令牌 —— 相关声明会被浏览器整条丢弃，不报错。")
        return 1
    print("\n结论：全部通过（没有 var() 引用到不存在的令牌）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
