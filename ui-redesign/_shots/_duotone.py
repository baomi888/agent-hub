# -*- coding: utf-8 -*-
"""把封面黑白工作台照处理成「深棕 + 米白」双色调（编辑式 duotone）"""
from PIL import Image, ImageOps

SRC = r"C:\Users\HUAWEI\Desktop\苞米agent\frontend\public\photo-desk.jpg"
DST = r"C:\Users\HUAWEI\Desktop\苞米agent\frontend\public\photo-desk-duotone.jpg"

# 双色调锚点：深棕墨 #1C1A16 ↔ 米白纸 #F5F0E6
INK = (28, 26, 22)
PAPER = (245, 240, 230)


def main():
    img = Image.open(SRC).convert("L")
    # 1) 自动对比（印刷感）
    img = ImageOps.autocontrast(img, cutoff=1)
    # 2) 轻微 S 曲线（smoothstep）增强中间调层次
    def curve(v):
        t = v / 255.0
        t = t * t * (3 - 2 * t)
        return int(255 * t)

    lut_s = [curve(i) for i in range(256)]
    img = img.point(lut_s)
    # 3) 线性映射到 深棕↔米白
    r_lut = [int(INK[0] + (PAPER[0] - INK[0]) * (i / 255.0)) for i in range(256)]
    g_lut = [int(INK[1] + (PAPER[1] - INK[1]) * (i / 255.0)) for i in range(256)]
    b_lut = [int(INK[2] + (PAPER[2] - INK[2]) * (i / 255.0)) for i in range(256)]
    duo = Image.merge(
        "RGB",
        (img.point(r_lut), img.point(g_lut), img.point(b_lut)),
    )
    duo.save(DST, quality=90)
    print("saved:", DST, duo.size)


if __name__ == "__main__":
    main()
