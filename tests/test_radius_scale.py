"""圆角只有三档：卡片 12 / 控件 10 / 胶囊 999（2026-11-16 P1 收敛）。

背景：评审实测量到首页同屏并存 8px×100、4px×68、999px×13、12px×11、16px×4、50%×4，
相邻的卡片与卡内小按钮只差 4px —— 既不像"两档"，也不像"随手写的"，
而是两者之间没有任何可读的语义差。收敛后只剩三种语义：

  · 装内容的（卡片 / 面板 / 弹层 / 输入区）= 12
  · 手能点的（按钮 / 输入 / 标签 / 图标底座）= 10
  · 成条的（胶囊 / 进度 / 开关轨道 / 微标）= 999
  · 例外只有 0（贴边）与 50%（正圆，它不是圆角档位）

为什么要有守卫：这类"值散开"的坏**不会报错**、也不影响任何接口，
上线后只是"看着有点不对"，评审下轮又会当新问题提一遍。
所以这里钉三件事：
  1. 任何 CSS 里的 border-radius 字面量只能落在三档 + 0/50%；
  2. 三档本身由 --r-card / --r-ctl / --r-pill 定义，别名 --radius-* 不许另开尺寸；
  3. 扫描量有下限（文件数与声明数），防止 glob 写错时"零命中即通过"。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
TOKENS = SRC / "app" / "styles" / "tokens.css"

# 三档的定义值。改档位只改这里和 tokens.css，两处必须同时改。
TIERS = {
    "--r-card": "12px",
    "--r-ctl": "10px",
    "--r-pill": "999px",
}

# 三档的引用写法 + 两个例外
ALLOWED_ATOMS = {
    "var(--r-card)",
    "var(--r-ctl)",
    "var(--r-pill)",
    "var(--radius-xs)",
    "var(--radius-sm)",
    "var(--radius-md)",
    "var(--radius-lg)",
    "var(--radius-xl)",
    "var(--radius-2xl)",
    "var(--radius-3xl)",
    "var(--radius-4xl)",
    # 校园模块的二级别名，定义在 campus.css 里（:root 的 --campus-radius:
    # var(--radius-lg)），最终仍落到卡片档。test_alias_points_to_tier 会钉它。
    "var(--campus-radius)",
    "0",
    "50%",
    "inherit",
    "unset",
}

# 扫描量下限：现在约 30 个样式表 / 300+ 条声明。低于这个数说明 glob 没扫到东西。
MIN_FILES = 25
MIN_DECLS = 250


def _css_files() -> list[Path]:
    out = []
    for p in SRC.rglob("*.css"):
        parts = p.parts
        if "node_modules" in parts or "_backup" in parts:
            continue
        out.append(p)
    return sorted(out)


def _strip_comments(src: str) -> str:
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def _decls() -> list[tuple[str, str]]:
    """返回 [(相对路径, border-radius 的值)]，注释已剥掉。"""
    found = []
    for path in _css_files():
        src = _strip_comments(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"border-radius\s*:\s*([^;{}]+)", src):
            found.append((str(path.relative_to(SRC)), m.group(1).strip()))
    return found


def test_scan_is_not_empty():
    """先钉扫描量：扫不到文件时下面所有断言都会空过。"""
    files = _css_files()
    assert len(files) >= MIN_FILES, "只扫到 %d 个样式表，glob 可能坏了" % len(files)
    decls = _decls()
    assert len(decls) >= MIN_DECLS, "只扫到 %d 条 border-radius，解析可能坏了" % len(decls)


def test_only_three_tiers():
    """任何圆角字面量只能落在三档（或 0 / 50%）。"""
    bad = []
    for rel, value in _decls():
        for atom in value.split():
            if atom not in ALLOWED_ATOMS:
                bad.append("%s -> border-radius: %s" % (rel, value))
                break
    assert not bad, "圆角散值回流了：\n" + "\n".join(sorted(set(bad))[:20])


def test_tiers_are_defined_once():
    """三档只在 tokens.css 定义，且别名必须指回来。"""
    src = _strip_comments(TOKENS.read_text(encoding="utf-8"))
    for name, value in TIERS.items():
        pat = re.compile(r"%s\s*:\s*([^;]+);" % re.escape(name))
        m = pat.search(src)
        assert m, "tokens.css 里找不到 %s" % name
        assert m.group(1).strip() == value, "%s 应该等于 %s，实际是 %s" % (
            name, value, m.group(1).strip()
        )

    # 别名一律指向三档，不许自己写新尺寸
    aliases = dict(re.findall(r"(--radius-[a-z0-9-]+)\s*:\s*([^;]+);", src))
    assert aliases, "tokens.css 里没有 --radius-* 别名"
    for name, value in aliases.items():
        assert value.strip() in {("var(%s)" % k) for k in TIERS}, (
            "%s 自己定义了尺寸 %s，应该指向三档之一" % (name, value.strip())
        )


def test_campus_alias_points_to_tier():
    """校园模块的 --campus-radius 必须仍然指回三档，不能自己长出一个尺寸。"""
    campus = _strip_comments(
        (SRC / "campus" / "styles" / "campus.css").read_text(encoding="utf-8")
    )
    m = re.search(r"--campus-radius\s*:\s*([^;]+);", campus)
    assert m, "campus.css 里找不到 --campus-radius"
    assert m.group(1).strip() in {("var(%s)" % k) for k in TIERS} | {
        "var(--radius-lg)"
    }, "--campus-radius 变成了 %s" % m.group(1).strip()


def test_px_literals_do_not_come_back():
    """反证用：三档定义之外，全站不该再出现 4px / 8px / 16px 这类圆角字面量。"""
    src_all = ""
    for path in _css_files():
        src_all += _strip_comments(path.read_text(encoding="utf-8"))
    for legacy in ("4px", "8px", "16px", "14px", "6px", "22px"):
        pat = re.compile(r"border-radius\s*:\s*[^;{}]*\b%s\b" % re.escape(legacy))
        assert not pat.search(src_all), "又出现了 border-radius: %s" % legacy