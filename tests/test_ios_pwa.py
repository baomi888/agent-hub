# -*- coding: utf-8 -*-
"""iOS 内测（PWA 装到主屏）的结构性约束。

这组断言守的是三件**改坏了不会报错、只会在真机上难看**的事：

1. `viewportFit: "cover"` —— 缺了它，全站 30 多处 `env(safe-area-inset-*)` 恒为 0。
   在桌面浏览器里完全看不出来（桌面本来就没有安全区），只有 iPhone 上才会
   表现为「内容压在 Home 指示条底下」。

2. `statusBarStyle: "black-translucent"` 与「顶部安全区」的**配套关系**。
   这是这组断言里最要紧的一条：黑透模式下内容会铺到状态栏底下，
   每一个占满视口的容器都必须自己让出 `env(safe-area-inset-top)`。
   单看任何一处 CSS 都是对的，漏一处也只有漏的那一页会坏 ——
   典型的「测试全绿、真机翻车」。所以这里做成联动断言：
   只有当状态栏真的是黑透时，才要求那些容器全都有；改回 default 就不要求。

3. 启动屏的 9 张图必须真的存在，且 link 数量与尺寸表一致。
   少一张不会报错，只会有一款 iPhone 在启动时闪白 —— 而那台恰好可能是你自己的。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend"
LAYOUT = FE / "src" / "app" / "layout.tsx"
SPLASH_DIR = FE / "public" / "splash"


def _read(p: Path) -> str:
    assert p.exists(), f"文件不存在: {p}"
    return p.read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """取出一条规则块（含嵌套的媒体查询里那一份也算）。

    用「从选择器开始到第一个右花括号」而不是全文 `in`：
    后者会让 `.console-page` 命中 `.console-scroll` 的 padding，
    断言就变成永真式了（本项目踩过这个坑）。
    """
    # 允许选择器前面有组合符（例如 media query 里的 "  main.workbench-main {"）
    pat = re.compile(re.escape(selector) + r"\s*\{([^{}]*)\}", re.S)
    hits = pat.findall(css)
    assert hits, f"{selector} 在 CSS 里找不到"
    return "\n".join(hits)


# ==================== ① viewport-fit 与状态栏 ====================


def test_viewport_fit_is_cover():
    src = _read(LAYOUT)
    assert 'viewportFit: "cover"' in src, (
        "layout.tsx 的 viewport 里没有 viewportFit: cover —— "
        "iOS 上所有 env(safe-area-inset-*) 会全部退化成 0"
    )


def test_status_bar_style_is_black_translucent():
    src = _read(LAYOUT)
    assert 'statusBarStyle: "black-translucent"' in src, (
        "状态栏不是 black-translucent。default 模式下深色背景配黑字，"
        "深色模式里状态栏会什么都看不见"
    )
    assert 'statusBarStyle: "default"' not in src, "两处 statusBarStyle 互相矛盾"


# ==================== ② 黑透模式下的顶部安全区（联动） ====================

# (CSS 文件, 选择器, 说明) —— 每一个都是「占满视口的滚动容器或整屏外壳」
TOP_SAFE_CONTAINERS = [
    ("src/app/styles/console.css", ".console-scroll", "控制台 14 个页面共用外壳"),
    ("src/app/styles/forum-tb.css", ".tb-top", "论坛顶栏（sticky top:0）"),
    ("src/app/styles/base.css", "main.workbench-main", "首页工作台卡片（桌面）"),
    ("src/app/styles/fx.css", "main.workbench-main", "首页工作台卡片（≤899 覆盖）"),
    ("src/app/styles/install-tour.css", ".tour", "/install 与 /reminder 共用外壳"),
    ("src/app/styles/auth.css", ".auth-stage", "登录门"),
    ("src/app/styles/library.css", ".lib-top", "资料库顶栏"),
]


def test_every_fullscreen_shell_reserves_top_safe_area():
    """黑透状态栏 → 每个整屏容器都必须让出刘海高度。

    状态栏是 default 时跳过：那时 safe-area-inset-top 恒为 0，不需要。
    """
    if 'statusBarStyle: "black-translucent"' not in _read(LAYOUT):
        pytest.skip("状态栏不是黑透模式，容器无需顶部安全区")

    missing = []
    for rel, selector, why in TOP_SAFE_CONTAINERS:
        css = _read(FE / rel)
        block = _block(css, selector)
        if "safe-area-inset-top" not in block:
            missing.append(f"{rel} 的 {selector}（{why}）")

    assert not missing, (
        "黑透状态栏下这些容器没有让出 env(safe-area-inset-top)，"
        "iPhone 上会被刘海/状态栏压住：\n  " + "\n  ".join(missing)
    )


def test_console_scroll_keeps_bottom_safe_area():
    """顶部安全区不能把底部的挤掉 —— 改 padding 简写最容易犯这个错。"""
    block = _block(_read(FE / "src" / "app" / "styles" / "console.css"), ".console-scroll")
    assert "safe-area-inset-bottom" in block, "控制台的底部安全区没了"


def test_workbench_margin_and_height_move_together():
    """首页卡片：上边距加多少，高度就得减多少。

    只加上边距不减高度的话，卡片会高出视口一截，被 body 的 overflow:hidden
    裁掉底边（当初 h-screen + margin 那个 bug 就是这么来的）。
    """
    for rel in ("src/app/styles/base.css", "src/app/styles/fx.css"):
        block = _block(_read(FE / rel), "main.workbench-main")
        assert "safe-area-inset-top" in block, f"{rel} 的首页容器没有顶部安全区"
        # margin 与 height 里都要出现，缺一边就会错位
        margin_line = next((ln for ln in block.splitlines() if "margin:" in ln), "")
        height_lines = [ln for ln in block.splitlines() if "height: calc(" in ln]
        assert "safe-area-inset-top" in margin_line, f"{rel}: margin 没算安全区"
        assert height_lines, f"{rel}: 找不到 height: calc(...)"
        for ln in height_lines:
            assert "safe-area-inset-top" in ln, (
                f"{rel}: height 没减安全区 —— {ln.strip()}"
            )


# ==================== ③ 启动屏 ====================


def test_splash_links_cover_all_devices():
    src = _read(LAYOUT)
    assert "apple-touch-startup-image" in src, "head 里没有启动屏 link"
    # 尺寸表里的每一条都要有对应的 media + href
    media = re.findall(r"device-width: \$\{s\.w\}px", src)
    assert media, "SPLASH 表不在了，或者 map 渲染被删了"
    n = len(re.findall(r'\{ w: \d+, h: \d+, dpr: \d+, file: "', src))
    assert n >= 9, f"启动屏尺寸只有 {n} 个，iPhone 主力机型至少要 9 个"


def test_splash_files_exist():
    src = _read(LAYOUT)
    files = re.findall(r'file: "([^"]+\.png)"', src)
    assert files, "尺寸表里没有文件名"
    missing = [f for f in files if not (SPLASH_DIR / f).exists()]
    assert not missing, f"启动屏图片缺失: {missing}（跑 tools/make_ios_splash.py 生成）"


def test_splash_media_has_all_three_parts():
    """media 必须同时有宽/高/像素比。

    只写宽度的话，同宽不同代（SE 的 375x667 与 12 mini 的 375x812）会互相抢，
    被命中的那张会被拉伸成另一个比例。
    """
    src = _read(LAYOUT)
    # 必须按 -webkit-device-pixel-ratio 定位那一行：
    # 只找 device-width 会命中 viewport 配置里的 width: "device-width"（那行没有 height，
    # 断言就假红了 —— 这条自己踩过一次）
    lines = [ln for ln in src.splitlines() if "-webkit-device-pixel-ratio" in ln]
    assert lines, "layout.tsx 里找不到带 -webkit-device-pixel-ratio 的 media"
    line = lines[0]
    assert "device-width" in line and "device-height" in line, line
    assert "-webkit-device-pixel-ratio" in line, line


# ==================== ④ 图片尺寸与文件名自洽 ====================


def test_splash_pixel_size_matches_filename():
    """文件名写的物理像素，必须等于 逻辑尺寸 × 像素比。

    这条防的是「改了尺寸表忘了重新生成图片」—— 那时 link 指向旧图，
    系统按 media 命中了但图片比例不对，启动屏会被裁掉一块。
    """
    from PIL import Image

    src = _read(LAYOUT)
    rows = re.findall(r"\{ w: (\d+), h: (\d+), dpr: (\d+), file: \"(\d+x\d+\.png)\"", src)
    assert rows, "尺寸表解析失败"
    for w, h, dpr, file in rows:
        pw, ph = int(w) * int(dpr), int(h) * int(dpr)
        assert file == f"{pw}x{ph}.png", f"文件名 {file} 与 {w}x{h}@{dpr}x 不符"
        im = Image.open(SPLASH_DIR / file)
        assert im.size == (pw, ph), f"{file} 实际尺寸 {im.size}，应为 {(pw, ph)}"
