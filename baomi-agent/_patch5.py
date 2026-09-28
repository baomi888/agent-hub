# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()

old = '''.logo-img{object-fit:contain}
.welcome-logo{width:72px;height:72px;margin-bottom:18px;object-fit:contain}
[data-theme="dark"] .welcome-logo{filter:drop-shadow(0 3px 10px rgba(217,180,94,.4))}'''

new = '''.logo-img{object-fit:contain;width:46px;height:46px;filter:contrast(1.25) saturate(1.6) brightness(.85)}
.welcome-logo{width:92px;height:92px;margin-bottom:18px;object-fit:contain;filter:contrast(1.25) saturate(1.6) brightness(.85)}
[data-theme="dark"] .logo-img{filter:contrast(1.15) saturate(1.4)}
[data-theme="dark"] .welcome-logo{filter:contrast(1.15) saturate(1.4) drop-shadow(0 3px 12px rgba(217,180,94,.5))}'''

assert old in s,'logo css not found'
s = s.replace(old,new,1)
with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok')
