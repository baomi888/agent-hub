"""原型页自审：emoji / 第三方外链 / CDN 字体 / 旧项目名 / 图标占位。

用法：
    python tools/audit_prototype.py                    # 扫描 prototypes/*.html
    python tools/audit_prototype.py <文件路径> [...]      # 指定文件

规范依据：docs/知穗_UI设计规范_v0.1.md
  · 界面与交付物不使用 emoji，图标一律走 CornIcon 手绘语言
  · 不出现 ← → 这类字符箭头（用 CornIcon 的 back / arrow）
  · 单文件原型必须离线可用：无第三方 img / font / CDN 外链
只读检查，不改文件。退出码非 0 表示有命中。
"""
import glob
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GLOB = os.path.join(ROOT, "prototypes", "*.html")

EMOJI_RANGES = [
    (0x1F300, 0x1FAFF),  # 表情、符号、补充符号
    (0x1F000, 0x1F2FF),  # 麻将、扑克、区域指示
    (0x2600, 0x27BF),    # 杂项符号、装饰符号（✎ ✕ ★ 等）
    (0x2B00, 0x2BFF),    # 杂项符号与箭头
    (0xFE0F, 0xFE0F),    # 变体选择符
    (0x2190, 0x21FF),    # 箭头字符（← → 等）
]

# 注意：「苞米地笔记本」是**刻意保留**的设计语言名（与「苞米」吉祥物、账号「苞米呀」同源），
# 不属于旧名残留，不要加进来。要查的是改名前后的系统名。
OLD_NAMES = ["寻臻", "苞米Agent", "知慧Agent"]


def is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return any(a <= cp <= b for a, b in EMOJI_RANGES)


def audit(path: str) -> int:
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    name = os.path.basename(path)
    problems = 0

    print("=" * 66)
    print(f"文件：{name}  （{len(lines)} 行 / {len(src.encode('utf-8')) / 1024:.1f} KB）")
    print("=" * 66)

    # 1) emoji / 字符箭头
    hits = []
    for i, line in enumerate(lines, 1):
        for ch in line:
            if is_emoji(ch):
                hits.append((i, ch, unicodedata.name(ch, "?"), line.strip()[:64]))
    print(f"[1] emoji / 字符箭头命中：{len(hits)}")
    for i, ch, nm, ctx in hits[:12]:
        print(f"      L{i}: {ch!r} {nm}")
        print(f"           {ctx}")
    if len(hits) > 12:
        print(f"      …另有 {len(hits) - 12} 处")
    problems += bool(hits)

    # 2) 第三方外链
    ext = re.findall(r'(?:src|href)\s*=\s*"(https?://[^"]+)"', src)
    ext += re.findall(r'url\(\s*["\']?(https?://[^)"\']+)', src)
    print(f"[2] 第三方外链：{len(ext)}")
    for u in sorted(set(ext)):
        print("      ", u)
    problems += bool(ext)

    # 3) CDN / 在线字体
    cdn = re.findall(r"fonts\.googleapis|fonts\.gstatic|cdn\.jsdelivr|unpkg\.com|cdnjs", src)
    print(f"[3] CDN / 在线字体：{len(cdn)}")
    for c in sorted(set(cdn)):
        print("      ", c)
    problems += bool(cdn)

    # 4) 旧项目名
    old = [w for w in OLD_NAMES if w in src]
    print(f"[4] 旧项目名残留：{old or '无'}")
    problems += bool(old)

    # 5) 图标完整性：页面用到的每个 data-i 都必须在 ICON 表里有定义
    ph = src.count('data-i="')
    phones = src.count('class="phone"')
    specs = src.count('class="spec"')
    if ph:
        used = set(re.findall(r'data-i="([a-zA-Z]+)"', src))
        defined = set(re.findall(r"\n\s{2}([a-zA-Z]+):\s*'", src))
        extra = f"｜手机框 {phones}" if phones else ""
        print(f"[5] 结构：图标占位 {ph}（{len(used)} 种）｜ICON 已定义 {len(defined)} 种｜设计说明表 {specs}{extra}")
        missing = sorted(used - defined)
        if missing:
            print(f"      ✗ 用到了但 ICON 表没定义：{missing}")
            problems += 1
        else:
            print("      ✓ 用到的图标全部已定义")

    print("结论：", "通过" if problems == 0 else f"有 {problems} 类命中，见上")
    print()
    return problems


def main() -> int:
    args = sys.argv[1:]
    files = args if args else sorted(glob.glob(DEFAULT_GLOB))
    if not files:
        print("没有找到待审文件")
        return 1
    total = sum(audit(f) for f in files)
    print("=" * 66)
    print(f"共 {len(files)} 个文件，{'全部通过' if total == 0 else f'合计 {total} 类命中'}")
    print("=" * 66)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
