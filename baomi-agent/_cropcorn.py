# -*- coding: utf-8 -*-
from PIL import Image
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\assets\corn_t.png'
im = Image.open(p).convert('RGBA')
bbox = im.getbbox()
print('before bbox', bbox, im.size)
# 裁到内容边界，再留 5% 边距
if bbox:
    l,t,r,b = bbox
    pad = int((r-l)*0.04)
    l=max(0,l-pad); t=max(0,t-pad)
    r=min(im.width,r+pad); b=min(im.height,b+pad)
    im2 = im.crop((l,t,r,b))
    # 补成正方形
    s = max(im2.width, im2.height)
    canvas = Image.new('RGBA',(s,s),(0,0,0,0))
    canvas.paste(im2,((s-im2.width)//2,(s-im2.height)//2),im2)
    canvas.save(p)
    print('after', canvas.size)
