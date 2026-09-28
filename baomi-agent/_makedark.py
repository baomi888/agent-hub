# -*- coding: utf-8 -*-
import io, shutil
src = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
dst = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\_dark_preview.html'
with io.open(src, 'r', encoding='utf-8') as f:
    s = f.read()
s = s.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-theme="dark">', 1)
with io.open(dst, 'w', encoding='utf-8') as f:
    f.write(s)
print('ok')
