# -*- coding: utf-8 -*-
"""生成 Expo 的 App 图标与启动图（mobile/assets/）。

两个坑，都踩过：

1. **不能拿 `icon-512.png` 做 App 图标**：它四角是透明的（圆角已经烘进图里），
   而 iOS 会再套一层自己的圆角遮罩 —— 结果是「圆角里再套一圈圆角」，
   四角还会露出黑色（App Store 图标不允许 alpha 通道）。
   用 `icon-maskable-512.png`：四角本来就是纸色 (239,230,200) 且全不透明。

2. **启动图不用抠图**：把纸色底留着就行。expo-splash-screen 的背景色配成
   同一个 #EDE4C8，图片边缘和背景天然无缝，抠透明反而会在边缘留一圈灰边。
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "public" / "icon-maskable-512.png"
OUT = ROOT / "mobile" / "assets"

# iOS App Store 要求 1024x1024、无 alpha 通道。
ICON_SIZE = 1024


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = Image.open(SRC).convert("RGB")

    icon = src.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    icon.save(OUT / "icon.png", "PNG", optimize=True)

    # 启动图原样复制（纸色底 + 居中图形）。expo-splash-screen 会按 contain 摆放，
    # 背景配成同一个纸色就不会有接缝。
    src.resize((512, 512), Image.LANCZOS).save(OUT / "splash-icon.png", "PNG", optimize=True)

    # Android 自适应图标：前景需要留安全区，maskable 版正好就是这个比例。
    src.resize((432, 432), Image.LANCZOS).save(OUT / "adaptive-icon.png", "PNG", optimize=True)

    for name in ("icon.png", "splash-icon.png", "adaptive-icon.png"):
        p = OUT / name
        im = Image.open(p)
        print(f"  {name:<18} {im.size[0]}x{im.size[1]}  {im.mode}  {p.stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()
