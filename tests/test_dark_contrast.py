"""深色主题的可读性下限。

起因：2026-11-14 晚第一次做深色实拍（8 页 × 2 视口，`output/_dark_audit.mjs`），
16 张里结构全过（无横向溢出、无边框消失、三层纸面色可分），唯一的硬问题是
`--text-3` 这一档：11–13px 的小字在「比 step-2 更亮的层」上只有 3.83–4.19。

`tokens.css` 原来的注释写着「#A08B66 在最亮的卡片底上有 4.78:1」——
那个 4.78 只对 `--step-2`(#292217) 成立。深色实际不止三层：选中态、
公告条、帖子头、code 片段各自把底抬到 #332C20 / #382B16 / #3F311B，
注释里的前提本身就不成立，所以值也就不够。

这份断言做两件事：
  1. 把线上实测到的每一层底色钉在这里，逐个算对比度，低于 4.5 就红；
  2. 防止「为了让对比度好看」把 --text-3 一路提亮到和 --text-2 分不开。

⚠️ 底色全部来自 `output/log-dark-audit.txt` 的实测读数，不是估的。
   新增浅色底（比现有最亮那层还亮）时，这里要跟着加一条 ——
   加的时候顺手把「一共几层」的断言也改大，否则扫不到也没人知道。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLES = ROOT / "frontend" / "src" / "app" / "styles"
TOKENS = STYLES / "tokens.css"
LIBRARY = STYLES / "library.css"
CONSOLE = STYLES / "console.css"

# 深色下真实出现过的「弱文字身后的底色」，取自 output/log-dark-audit.txt。
# 名字对应实测时打出来的元素，方便回头对图上位置。
DARK_BASES = {
    "step-2 卡片": (41, 34, 23),  # #292217
    "kbp-read 底": (46, 39, 27),  # #2E271B
    "settings code 底": (51, 44, 32),  # #332C20
    "tb-notice 底": (51, 45, 33),  # #332D21
    "帖子头 底": (56, 43, 22),  # #382B16
    "kbp-item 选中": (63, 49, 27),  # #3F311B
}

MIN_RATIO = 4.5  # WCAG AA，正文与小字的门槛


def _lum(rgb: tuple[int, int, int]) -> float:
    def f(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])


def _ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hex(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _dark_block() -> str:
    """取 tokens.css 里那个顶层 `.dark { ... }` 块。

    只认行首的 `.dark {`：`base.css` 里有 `.dark body{}`、`library.css` 里有
    `.dark .kb-post-me{}`，它们都不该被当成令牌块。
    """
    css = TOKENS.read_text(encoding="utf-8")
    hits = list(re.finditer(r"^\.dark\s*\{", css, re.M))
    assert len(hits) == 1, f"tokens.css 里 .dark 令牌块应恰好 1 个，实际 {len(hits)}"
    start = hits[0].end()
    end = css.index("\n}", start)
    return css[start:end]


def _token(block: str, name: str) -> str:
    m = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{3,8}})", block)
    assert m, f"深色令牌块里没有 --{name}"
    return m.group(1)


def test_dark_text3_clears_aa_on_every_measured_layer():
    """弱文字在深色下的每一层底色上都要过 AA。

    这条是这轮实拍的核心产出。反证：把 --text-3 改回 #A08B66，
    在「kbp-item 选中」那层会掉到 3.83，这里必红。
    """
    assert len(DARK_BASES) >= 6, f"底色清单只扫到 {len(DARK_BASES)} 层，少于实测的 6 层"

    dark = _dark_block()
    text3 = _hex(_token(dark, "text-3"))

    bad = []
    for name, base in DARK_BASES.items():
        r = _ratio(text3, base)
        if r < MIN_RATIO:
            bad.append(f"{name} {r:.2f}:1")
    assert not bad, "--text-3 在这些深色底上低于 4.5:1 → " + "；".join(bad)


def test_dark_text3_stays_below_text2():
    """--text-3 不能提亮到和 --text-2 分不开。

    提亮对比度最省事的做法就是一路加白，加到和次级文字同色，层级就塌了。
    实测 1.29 的可辨差是这一档的下限，低于 1.15 说明三级灰已经名存实亡。
    """
    dark = _dark_block()
    t2 = _hex(_token(dark, "text-2"))
    t3 = _hex(_token(dark, "text-3"))
    gap = _ratio(t2, t3)
    assert gap >= 1.15, f"--text-2 与 --text-3 只差 {gap:.2f}:1，三级文字已经没有层级"


def test_dark_post_me_drops_the_gold_on_gold():
    """深色下「楼主」标不许再用「亮金字 + 淡金底」这种同色系组合。

    浅色是淡金底 + 深棕字（5.5:1），换到深色后 --accent-ink 变成亮金，
    底还是金 alpha，11px 只有 4.15:1 —— 实测里最难读的一处。
    """
    css = LIBRARY.read_text(encoding="utf-8")
    m = re.search(r"\.dark\s+\.kb-post-me\s*\{([^}]*)\}", css)
    assert m, "library.css 里没有 .dark .kb-post-me 的深色覆盖"
    body = m.group(1)
    assert "--accent-tint" not in body, "深色「楼主」标仍在用 --accent-tint 铺底"
    assert "--accent-ink" not in body, "深色「楼主」标仍在用 --accent-ink 上色"
    assert "var(--text-2)" in body, "深色「楼主」标应改用次级文字色"
    # 用 inset 阴影而不是 border：border 会把小药丸撑高 2px，与「管理员」标错基线
    assert "inset" in body, "深色「楼主」标应用 inset 阴影描边，避免改变盒高"


def test_tabbar_caps_its_column_width():
    """底部 tab 的每一列都要有宽度上限。

    纯 `repeat(5, minmax(0, 1fr))` 在 640–900 这段（大手机横屏、小平板竖屏）
    会把五个 tab 摊成各 180px 的巨块 —— 一条横跨 899px 的底栏。
    390 下仍然是各 75px，所以手机形态不受影响。
    """
    css = CONSOLE.read_text(encoding="utf-8")
    blocks = [m for m in re.finditer(r"\.tabbar\s*\{([^}]*)\}", css)]
    assert len(blocks) >= 2, f".tabbar 应有默认隐藏 + 手机档两条规则，只找到 {len(blocks)}"

    # 手机档那条（带 grid-template-columns 的）才是要检查的对象
    cols = [b.group(1) for b in blocks if "grid-template-columns" in b.group(1)]
    assert len(cols) == 1, f"带列定义的 .tabbar 规则应恰好 1 条，实际 {len(cols)}"
    m = re.search(r"repeat\(\s*5\s*,\s*minmax\(\s*0\s*,\s*(\d+)px\s*\)\s*\)", cols[0])
    assert m, "tabbar 列定义没有像素上限，640–900 会被拉成巨型按钮"
    cap = int(m.group(1))
    assert cap <= 160, f"tabbar 单列上限 {cap}px 太宽，五个 tab 合起来仍会像横幅"
