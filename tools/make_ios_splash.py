# -*- coding: utf-8 -*-
"""生成 iOS PWA 启动屏（apple-touch-startup-image）。

为什么需要：iOS 从主屏打开 PWA 时，在页面渲染出来之前会显示自己的启动画面。
不提供 apple-touch-startup-image 的话，那一瞬间是**纯白**，和米黄纸色撞得很明显，
观感像"点开了一个网页"而不是"打开了一个 App"。

设计取最保守的一版：纸色底 + 居中图标。不放文字 —— 启动屏只出现 0.3~1 秒，
文字来不及看清，反而增加"是一张图"的感觉。

尺寸表按 iPhone 实际逻辑分辨率 × device-pixel-ratio 算，
media query 里三要素（device-width / device-height / -webkit-device-pixel-ratio）
必须同时匹配，少一个就会套到别的机型上。
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend" / "public"
OUT = PUBLIC / "splash"
ICON = PUBLIC / "icon-512.png"

# 纸色：与 tokens.css 的 --bg-canvas 浅色值一致，别用纯白 —— 纯白和页面之间
# 切换时会有一帧"闪白"，恰恰是启动屏想消除的东西。
BG = (237, 228, 200)

# (逻辑宽, 逻辑高, dpr) -> 文件名按 物理宽x物理高 命名
DEVICES = [
    (375, 667, 2),   # iPhone SE 2/3、8
    (375, 812, 3),   # iPhone X / XS / 11 Pro / 12 mini / 13 mini
    (414, 896, 2),   # iPhone XR / 11
    (414, 896, 3),   # iPhone XS Max / 11 Pro Max
    (390, 844, 3),   # iPhone 12 / 13 / 14
    (393, 852, 3),   # iPhone 14 Pro / 15 / 15 Pro / 16
    (428, 926, 3),   # iPhone 12 / 13 Pro Max / 14 Plus
    (430, 932, 3),   # iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus
    (440, 956, 3),   # iPhone 16 Pro Max
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = Image.open(ICON).convert("RGBA")

    rows = []
    for w, h, dpr in DEVICES:
        pw, ph = w * dpr, h * dpr
        canvas = Image.new("RGB", (pw, ph), BG)

        # 图标边长取短边的 32%：390 宽下约 125pt，与 iOS 原生启动屏里
        # App 图标的观感接近。26%（100pt）试过一版，在长屏上显得太空。
        size = int(min(pw, ph) * 0.32)
        logo = icon.resize((size, size), Image.LANCZOS)

        # 垂直位置：不是正中，而是略偏上（45%）。人的视觉重心比几何中心高，
        # 放正中会觉得"往下坠"。
        x = (pw - size) // 2
        y = int(ph * 0.45 - size / 2)
        canvas.paste(logo, (x, y), logo)

        name = f"{pw}x{ph}.png"
        canvas.save(OUT / name, "PNG", optimize=True)
        rows.append((w, h, dpr, name, (OUT / name).stat().st_size))

    print(f"输出目录: {OUT}")
    total = 0
    for w, h, dpr, name, sz in rows:
        total += sz
        print(f"  {w}x{h}@{dpr}x  ->  {name:<14} {sz/1024:6.1f} KB")
    print(f"共 {len(rows)} 张，合计 {total/1024:.1f} KB")


if __name__ == "__main__":
    main()
