# -*- coding: utf-8 -*-
"""验证纸纹噪点 + 双色调：采样浅/深截图的关键像素"""
from PIL import Image

LIGHT = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\质感提升-纸纹双色调-浅色.png"
DARK = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\质感提升-纸纹双色调-深色.png"


def show(path, pts):
    img = Image.open(path).convert("RGB")
    print("==", path.split("\\")[-1], img.size)
    for name, (x, y) in pts.items():
        print(f"  {name:<14} ({x:4},{y:4}) -> RGB{img.getpixel((x, y))}")


def grain_check(path, pts):
    """同一色块邻域采样，看是否有颗粒抖动"""
    img = Image.open(path).convert("RGB")
    print("-- 噪点检查", path.split("\\")[-1])
    for name, (x, y) in pts.items():
        vals = [img.getpixel((x + dx, y + dy)) for dx, dy in
                [(-2, -2), (0, -2), (2, -2), (-2, 0), (0, 0), (2, 0), (-2, 2), (0, 2), (2, 2)]]
        spread = max(max(v) - min(v) for v in zip(*vals))
        print(f"  {name:<14} 邻域9点 max通道差={spread}  样本={vals[4]}")


if __name__ == "__main__":
    light_pts = {
        "照片-亮部A": (620, 300),
        "照片-中部B": (520, 330),
        "照片-暗部C": (420, 360),
        "背景-侧栏": (100, 500),
        "背景-右栏": (880, 500),
    }
    dark_pts = {
        "照片-亮部A": (620, 300),
        "照片-中部B": (520, 330),
        "照片-暗部C": (420, 360),
        "背景-侧栏": (100, 500),
        "背景-右栏": (880, 500),
    }
    show(LIGHT, light_pts)
    grain_check(LIGHT, {"背景-侧栏": (100, 500), "背景-右栏": (880, 500), "中间-标题区": (700, 460)})
    show(DARK, dark_pts)
    grain_check(DARK, {"背景-侧栏": (100, 500), "背景-右栏": (880, 500), "中间-标题区": (700, 460)})
