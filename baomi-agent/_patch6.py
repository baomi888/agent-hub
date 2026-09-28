# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()
old='.welcome-logo{width:54px;height:54px;margin-bottom:18px}\n'
assert old in s
s=s.replace(old,'',1)
with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok')
