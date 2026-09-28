# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()

# 浅色变量加 btn-import
s = s.replace(
  "  --page-noise-opacity:.6;\n}",
  "  --page-noise-opacity:.6;\n  --btn-import-bg:linear-gradient(180deg,#f7eccb,#efdfb4);\n  --btn-import-tx:#2c2214;\n}",
  1)
# 深色变量加 btn-import
s = s.replace(
  "  --page-noise-opacity:.3;\n}",
  "  --page-noise-opacity:.3;\n  --btn-import-bg:linear-gradient(180deg,#3a2e1f,#2e2418);\n  --btn-import-tx:#ecd9b0;\n}",
  1)

# btn-import 用变量
old = '''.btn-import{
  width:100%;
  display:flex;align-items:center;justify-content:center;gap:8px;
  background:linear-gradient(180deg,#f7eccb,#efdfb4);
  border:1px solid rgba(122,92,54,.3);
  border-radius:10px;padding:12px;font-size:14px;font-weight:500;
  color:var(--ink);cursor:pointer;'''
new = '''.btn-import{
  width:100%;
  display:flex;align-items:center;justify-content:center;gap:8px;
  background:var(--btn-import-bg);
  border:1px solid var(--line);
  border-radius:10px;padding:12px;font-size:14px;font-weight:500;
  color:var(--btn-import-tx);cursor:pointer;'''
assert old in s,'btn-import not found'
s = s.replace(old,new,1)

with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok')
