# -*- coding: utf-8 -*-
from PIL import Image
import os
src = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\assets\corn.png'
dst = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\assets\corn_t.png'
im = Image.open(src).convert('RGBA')
px = im.load()
w,h = im.size
# 背景色接近 #fbf6ea (251,246,234)，按距离阈值抠透明
bg = (251,246,234)
thr = 28
for y in range(h):
    for x in range(w):
        r,g,b,a = px[x,y]
        d = abs(r-bg[0])+abs(g-bg[1])+abs(b-bg[2])
        if d < thr:
            px[x,y] = (r,g,b,0)
        elif d < thr+25:
            # 边缘半透明过渡
            alpha = int(255*(d-thr)/25)
            px[x,y] = (r,g,b,alpha)
im.save(dst)
print('saved', dst, im.size)
