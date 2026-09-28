# -*- coding: utf-8 -*-
from PIL import Image
src = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\assets\corn_line.png'
dst = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\assets\corn_t.png'
im = Image.open(src).convert('RGBA')
px = im.load()
w,h = im.size
bg = (255,255,255)
thr = 30
for y in range(h):
    for x in range(w):
        r,g,b,a = px[x,y]
        d = abs(r-bg[0])+abs(g-bg[1])+abs(b-bg[2])
        if d < thr:
            px[x,y] = (r,g,b,0)
        elif d < thr+20:
            alpha = int(255*(d-thr)/20)
            px[x,y] = (r,g,b,alpha)
im.save(dst)
print('saved', im.size)
