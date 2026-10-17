#!/usr/bin/env python3
"""
把知穗前端依赖的两个外部中文字体离线化，彻底去掉 layout.tsx 里的 jsdelivr / 飞书外链。

源字体：
  - LXGW WenKai Screen（设计灵魂手写体）：完整 TTF，weight 400 静态，直接子集化
  - Noto Sans SC / Noto Serif SC：Windows 自带可变字体（NotoSansSC-VF / NotoSerifSC-VF），
    实例化到所需字重后子集化

字符集 = 源码出现的所有字符 ∪ GB2312 全量 6763 汉字兜底 ∪ ASCII
  → 标题区（开发者文案）精确覆盖；聊天区（用户输入）也兜底，绝不 fallback 成系统体。

关键坑（见 offline-cjk-font-subset 技能）：
  - Windows 自带 Noto VF 的 unitsPerEm = 1000，实例化后交给 Chromium 光栅化，
    大字横笔画会出现贯穿细线 → 必须等比放大到 2048（subset 之后再 scale）。
  - 实例化后必须把 OS/2.usWeightClass 改回真实字重（否则浏览器按 ExtraLight 选替代字体）。

产出：frontend/public/fonts/*.woff2 + frontend/public/fonts/fonts.css
用法：python tools/subset_fonts_project.py
"""

import glob
import os

from fontTools import ttLib
from fontTools.subset import Options, Subsetter
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates
from fontTools.varLib import instancer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_FONTS = os.path.join(ROOT, "frontend", "public", "fonts")

# ---- 源字体路径 ----
LXGW_TTF = os.path.join(ROOT, "tools", "_fontsrc", "LXGWWenKaiScreen.ttf")
NOTO_SANS_VF = r"C:/Windows/Fonts/NotoSansSC-VF.ttf"
NOTO_SERIF_VF = r"C:/Windows/Fonts/NotoSerifSC-VF.ttf"

# ---- 要生成的字重 ----
# (family, 源路径, 目标字重, 输出 slug, 是否需要 GB2312 全量兜底)
#   LXGW / Noto Serif 只用于开发者写的标题/眉题文案（封闭集合），无需 GB2312 兜底；
#   Noto Sans 用于正文/聊天区（含用户输入），必须 GB2312 兜底，避免用户输入 fallback 成系统体。
FACES = [
    ("LXGW WenKai Screen", LXGW_TTF, 400, "LXGWWenKaiScreen", False),
    ("Noto Sans SC", NOTO_SANS_VF, 300, "NotoSansSC", True),
    ("Noto Sans SC", NOTO_SANS_VF, 400, "NotoSansSC", True),
    ("Noto Sans SC", NOTO_SANS_VF, 500, "NotoSansSC", True),
    ("Noto Sans SC", NOTO_SANS_VF, 700, "NotoSansSC", True),
    ("Noto Serif SC", NOTO_SERIF_VF, 400, "NotoSerifSC", False),
    ("Noto Serif SC", NOTO_SERIF_VF, 600, "NotoSerifSC", False),
    ("Noto Serif SC", NOTO_SERIF_VF, 700, "NotoSerifSC", False),
    ("Noto Serif SC", NOTO_SERIF_VF, 900, "NotoSerifSC", False),
]

TARGET_UPEM = 2048


def gb2312_chars():
    out = set()
    for hi in range(0xA1, 0xFE):
        for lo in range(0xA1, 0xFE):
            try:
                out.add(bytes([hi, lo]).decode("gb2312"))
            except Exception:
                pass
    return out


def collect_source_chars():
    """扫描前端源码 + 后端 Python，收集出现过的所有字符（含 ASCII / 标点 / CJK）。"""
    charset = set()
    patterns = [
        os.path.join(ROOT, "frontend", "src", "**", "*.ts"),
        os.path.join(ROOT, "frontend", "src", "**", "*.tsx"),
        os.path.join(ROOT, "core", "**", "*.py"),
        os.path.join(ROOT, "agent", "**", "*.py"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p, recursive=True))
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        charset.update(text)
    return charset


def build_charsets():
    """返回 (strict, full) 两个字符集：
    - strict：源码出现字符 ∪ ASCII ∪ 标点（不含 GB2312），给封闭的标题/眉题字族用；
    - full  ：strict ∪ GB2312 全量 6763 汉字，给正文/聊天字族兜底用户输入。"""
    strict = collect_source_chars()
    # ASCII 全量（数字/英文/符号），保证任何拉丁字符都有字形
    strict |= set(chr(c) for c in range(0x20, 0x7F))
    # 一些常用全角标点/符号兜底
    strict |= set(
        "　，。、；：？！“”‘’（）《》【】〔〕—…·～「」『』〈〉「」『』"
        "｛｝［］％＆＊＋＝＜＞／＼｜＠＃"
    )
    full = strict | gb2312_chars()
    return "".join(sorted(strict)), "".join(sorted(full))


def scale_upem(font, target=TARGET_UPEM):
    """把 unitsPerEm 等比放大到 target，轮廓与全部度量同步缩放。必须在 subset 之后调用。"""
    cur = font["head"].unitsPerEm
    if cur == target:
        return
    k = target / cur
    glyf = font.get("glyf")
    if glyf is not None:
        for name in font.getGlyphOrder():
            g = glyf[name]
            if g.isComposite():
                for comp in g.components:
                    if hasattr(comp, "x"):
                        comp.x = int(round(comp.x * k))
                        comp.y = int(round(comp.y * k))
                g.recalcBounds(glyf)
            elif g.numberOfContours > 0:
                coords, endPts, flags = g.getCoordinates(glyf)
                coords = GlyphCoordinates(
                    [(int(round(x * k)), int(round(y * k))) for x, y in coords]
                )
                g.coordinates = coords
                g.endPtsOfContours = endPts
                g.flags = flags
                g.recalcBounds(glyf)
    for tag in ("hmtx", "vmtx"):
        if tag in font:
            mt = font[tag].metrics
            for name in list(mt.keys()):
                adv, lsb = mt[name]
                mt[name] = (int(round(adv * k)), int(round(lsb * k)))
    head = font["head"]
    head.unitsPerEm = target
    head.xMin = int(round(head.xMin * k))
    head.xMax = int(round(head.xMax * k))
    head.yMin = int(round(head.yMin * k))
    head.yMax = int(round(head.yMax * k))
    hhea = font["hhea"]
    for attr in ("ascent", "descent", "lineGap"):
        setattr(hhea, attr, int(round(getattr(hhea, attr) * k)))
    if "OS/2" in font:
        os2 = font["OS/2"]
        for attr in (
            "sTypoAscender",
            "sTypoDescender",
            "sTypoLineGap",
            "usWinAscent",
            "usWinDescent",
            "yStrikeoutSize",
            "yStrikeoutPosition",
            "sxHeight",
            "sCapHeight",
        ):
            if hasattr(os2, attr):
                setattr(os2, attr, int(round(getattr(os2, attr) * k)))
    if "post" in font:
        post = font["post"]
        post.underlinePosition = int(round(post.underlinePosition * k))
        post.underlineThickness = int(round(post.underlineThickness * k))


def subset_one(family, src_path, weight, slug, charset, need_gb2312):
    print(f"  → {family} {weight}  (源 {os.path.basename(src_path)})")
    if not os.path.exists(src_path):
        print(f"    [跳过] 源字体不存在: {src_path}")
        return None
    font = ttLib.TTFont(src_path)
    # Noto 可变字体需要实例化到目标字重；LXGW 是静态 TTF，跳过
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": weight}, inplace=False)
    opts = Options()
    opts.flavor = "woff2"
    opts.desubroutinize = True
    opts.drop_tables = list(opts.drop_tables) + ["DSIG"]
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    s = Subsetter(options=opts)
    s.populate(text=charset)
    s.subset(font)
    # 子集化之后：归一化 upem（规避大字横线 bug）+ 修正字重
    scale_upem(font, TARGET_UPEM)
    if "OS/2" in font:
        font["OS/2"].usWeightClass = weight
    out_name = f"{slug}-{weight}.woff2"
    out_path = os.path.join(PUBLIC_FONTS, out_name)
    font.flavor = "woff2"
    font.save(out_path)
    size = os.path.getsize(out_path)
    tag = "full(含GB2312)" if need_gb2312 else "strict(仅源码)"
    print(f"    [OK] {out_name}  ({size / 1024:.1f} KB)  [{tag}]")
    return out_name


def main():
    os.makedirs(PUBLIC_FONTS, exist_ok=True)
    strict, full = build_charsets()
    print(f"字符集 strict={len(strict)}  full={len(full)}")
    css_blocks = []
    for family, src, weight, slug, need_gb2312 in FACES:
        charset = full if need_gb2312 else strict
        name = subset_one(family, src, weight, slug, charset, need_gb2312)
        if name:
            css_blocks.append(
                f"/* {family} */\n"
                "@font-face {\n"
                f"  font-family: '{family}';\n"
                "  font-style: normal;\n"
                f"  font-weight: {weight};\n"
                "  font-display: swap;\n"
                f"  src: url('/fonts/{name}') format('woff2');\n"
                "}\n"
            )
    css_path = os.path.join(PUBLIC_FONTS, "fonts.css")
    with open(css_path, "w", encoding="utf-8") as fh:
        fh.write("/* 自动生成：离线中文字体子集，不要用外链 */\n")
        fh.write("".join(css_blocks))
    print(f"\n[OK] 生成 {css_path}")
    print(f"[OK] 共 {len(css_blocks)} 个字重")


if __name__ == "__main__":
    main()
