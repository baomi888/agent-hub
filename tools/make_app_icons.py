# -*- coding: utf-8 -*-
"""生成 Expo 的 App 图标与启动图（mobile/assets/）。

三个坑，都踩过：

1. **不能拿 `icon-512.png` 做 App 图标**：它四角是透明的（圆角已经烘进图里），
   而 iOS 会再套一层自己的圆角遮罩 —— 结果是「圆角里再套一圈圆角」，
   四角还会露出黑色（App Store 图标不允许 alpha 通道）。
   用 `icon-maskable-512.png`：四角本来就是纸色 (239,230,200) 且全不透明。

2. **启动图不用抠图**：把纸色底留着就行。expo-splash-screen 的背景色配成
   同一个 #EDE4C8，图片边缘和背景天然无缝，抠透明反而会在边缘留一圈灰边。

3. **Android 自适应图标不能直接缩 maskable 图**（2026-11-18 修）：
   maskable 的安全区是 PWA 的（图形占 85% 高），而 Android 自适应图标是
   108dp 画布、真正可见的只有中间 72dp，且大多数启动器套的是圆形/超椭圆遮罩。
   直接缩到 432 会让玉米的叶尖和头顶须子被裁掉 —— 真机上就是「图标里只剩
   一张大脸」。这里按**图形到画布中心的最大半径**反算缩放，保证整个图案都落在
   直径 66dp 的安全圆里，四周留纸色，任何遮罩都不会切到。
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "public" / "icon-maskable-512.png"
OUT = ROOT / "mobile" / "assets"

# 设计 token 里的纸色（frontend/src/app/styles/tokens.css --step-0）。
# 导出的 icon-maskable 图底是 (239,230,200)，差 2/255；这里统一到 #EDE4C8，
# 启动图/自适应图标的底色才能和 app.json 的背景色严丝合缝。
PAPER = (237, 228, 200)

# iOS App Store 要求 1024x1024、无 alpha 通道。
ICON_SIZE = 1024

# 自适应图标画布。Expo 会把它按 108dp 处理，中间 72dp 才是可见区。
ADAPTIVE_SIZE = 432
# 安全圆半径（像素）：66dp / 108dp * 432 / 2 ≈ 132。留 2px 余量给遮罩抗锯齿。
ADAPTIVE_SAFE_RADIUS = 130
# 图形与背景的判定阈值。纸色底是纯色，12 足够区分抗锯齿边缘。
BG_TOLERANCE = 12


def art_bbox(im: Image.Image, bg: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """返回非背景像素的外接框（左、上、右、下，含端点）。"""
    px = im.load()
    w, h = im.size
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if max(abs(p[i] - bg[i]) for i in range(3)) > BG_TOLERANCE:
                minx = min(minx, x)
                miny = min(miny, y)
                maxx = max(maxx, x)
                maxy = max(maxy, y)
    if maxx < 0:
        raise SystemExit("源图里找不到图形：检查 icon-maskable-512.png 的背景色")
    return minx, miny, maxx, maxy


def make_adaptive(src: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    """把图形整体缩进自适应图标的安全圆，四周补纸色。

    注意是**按最大半径**而不是按高度算缩放：玉米的两片叶子是斜着伸出去的，
    只看高度会以为放得下，实际叶尖刚好落在圆形遮罩之外。
    """
    minx, miny, maxx, maxy = art_bbox(src, bg)
    # 外扩 4px，避免把图形边缘的抗锯齿像素切掉
    pad = 4
    box = (
        max(0, minx - pad),
        max(0, miny - pad),
        min(src.width, maxx + 1 + pad),
        min(src.height, maxy + 1 + pad),
    )
    art = src.crop(box)

    # 外接框中心到图形最远像素的距离 —— 以它为准算缩放
    px = art.load()
    w, h = art.size
    cx, cy = (w - 1) / 2, (h - 1) / 2
    max_r = 0.0
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if max(abs(p[i] - bg[i]) for i in range(3)) > BG_TOLERANCE:
                max_r = max(max_r, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5)
    if max_r <= 0:
        raise SystemExit("图形为空，无法计算自适应图标的安全区")

    scale = ADAPTIVE_SAFE_RADIUS / max_r
    art = art.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
    )

    canvas = Image.new("RGB", (ADAPTIVE_SIZE, ADAPTIVE_SIZE), bg)
    canvas.paste(
        art,
        (
            (ADAPTIVE_SIZE - art.width) // 2,
            (ADAPTIVE_SIZE - art.height) // 2,
        ),
    )
    return canvas


def to_paper(src: Image.Image) -> Image.Image:
    """把整张图的颜色平移一点点，让纸色底正好等于 PAPER。

    只替换"等于背景色"的像素会在图形边缘留下 1px 的旧纸色描边（抗锯齿像素
    是旧纸色和图形色的混合）；整体平移同一个差值，边缘才不会露出来。
    """
    old = src.getpixel((0, 0))
    delta = [PAPER[i] - old[i] for i in range(3)]
    if not any(delta):
        return src
    chans = [
        ch.point(lambda v, d=d: max(0, min(255, v + d)))
        for ch, d in zip(src.split(), delta)
    ]
    return Image.merge("RGB", chans)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = to_paper(Image.open(SRC).convert("RGB"))
    bg = src.getpixel((0, 0))

    icon = src.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    icon.save(OUT / "icon.png", "PNG", optimize=True)

    # 启动图原样复制（纸色底 + 居中图形）。expo-splash-screen 会按 contain 摆放，
    # 背景配成同一个纸色就不会有接缝。
    src.resize((512, 512), Image.LANCZOS).save(
        OUT / "splash-icon.png", "PNG", optimize=True
    )

    make_adaptive(src, bg).save(OUT / "adaptive-icon.png", "PNG", optimize=True)

    for name in ("icon.png", "splash-icon.png", "adaptive-icon.png"):
        p = OUT / name
        im = Image.open(p)
        print(f"  {name:<18} {im.size[0]}x{im.size[1]}  {im.mode}  {p.stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()
