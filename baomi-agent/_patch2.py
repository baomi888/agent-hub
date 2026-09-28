# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()

# 1) 玉米图换成透明版
s = s.replace('src="assets/corn.png"','src="assets/corn_t.png"')

# 2) 去掉 blend 相关样式，换成普通 object-fit
old_css = '''.logo-img{object-fit:contain;mix-blend-mode:multiply}
.welcome-logo{width:64px;height:64px;margin-bottom:18px;object-fit:contain;mix-blend-mode:multiply}
[data-theme="dark"] .logo-img,
[data-theme="dark"] .welcome-logo{
  mix-blend-mode:normal;
  filter:drop-shadow(0 3px 8px rgba(217,180,94,.35));
}'''
new_css = '''.logo-img{object-fit:contain}
.welcome-logo{width:72px;height:72px;margin-bottom:18px;object-fit:contain}
[data-theme="dark"] .welcome-logo{filter:drop-shadow(0 3px 10px rgba(217,180,94,.4))}'''
assert old_css in s, 'css block not found'
s = s.replace(old_css,new_css,1)

# 3) 左栏背景用变量
old_left = '''.left{
  width:264px;flex:0 0 264px;
  background:linear-gradient(180deg,#f4e7c6 0%,#eedfb6 100%);'''
new_left = '''.left{
  width:264px;flex:0 0 264px;
  background:var(--left);'''
assert old_left in s,'left not found'
s = s.replace(old_left,new_left,1)

# 4) 右栏背景用变量
old_right = '''.right{
  width:280px;flex:0 0 280px;
  background:linear-gradient(180deg,#eee1c2 0%,#e6d6b0 100%);'''
new_right = '''.right{
  width:280px;flex:0 0 280px;
  background:var(--right);'''
assert old_right in s,'right not found'
s = s.replace(old_right,new_right,1)

with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok len',len(s))
