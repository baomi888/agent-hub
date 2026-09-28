# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()
old = '''.doc{
  position:relative;
  background:var(--card);
  border:1px solid var(--line);
  border-radius:8px;
  padding:14px 14px 12px 40px;
  box-shadow:var(--shadow);
  transform:rotate(-.6deg);
}
.doc:nth-child(even){transform:rotate(.5deg)}'''
new = '''.doc{
  position:relative;
  background:var(--card);
  border:1px solid var(--line);
  border-radius:8px;
  padding:14px 14px 12px 40px;
  box-shadow:var(--shadow);
  transform:rotate(-.6deg);
  transition:transform .2s, box-shadow .2s, border-color .2s;
}
.doc:nth-child(even){transform:rotate(.5deg)}
.doc:hover{transform:rotate(0deg) translateY(-3px);box-shadow:var(--shadow-lg);border-color:var(--gold)}'''
assert old in s
s=s.replace(old,new,1)
with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok')
