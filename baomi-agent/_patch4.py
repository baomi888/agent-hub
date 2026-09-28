# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()

old = '''[data-theme="dark"]{
  --paper:#2a211a;
  --paper-2:#241c15;
  --left:#1f1812;
  --right:#231b13;
  --cover-1:#4a3826;
  --cover-2:#2e2317;
  --cover-3:#1a130c;
  --ink:#ecd9b0;
  --ink-2:#b8a27c;
  --ink-3:#7e6e52;
  --gold:#d9b45e;
  --gold-2:#e8c878;
  --black:#12100c;
  --black-tx:#ecd9b0;
  --card:#33291d;
  --line:rgba(220,190,130,.16);
  --shadow:0 2px 10px rgba(0,0,0,.35);
  --shadow-lg:0 10px 30px rgba(0,0,0,.45);
  --spine-1:rgba(0,0,0,.18);
  --spine-2:rgba(0,0,0,.4);
  --page-noise-opacity:.3;
  --btn-import-bg:linear-gradient(180deg,#3a2e1f,#2e2418);
  --btn-import-tx:#ecd9b0;
}'''

new = '''[data-theme="dark"]{
  --paper:#3a2e21;
  --paper-2:#33291d;
  --left:#2e2418;
  --right:#32281c;
  --cover-1:#5c4830;
  --cover-2:#403222;
  --cover-3:#2a2015;
  --ink:#ecd9b0;
  --ink-2:#bfa882;
  --ink-3:#8a7a5c;
  --gold:#d9b45e;
  --gold-2:#e8c878;
  --black:#1c1712;
  --black-tx:#ecd9b0;
  --card:#443728;
  --line:rgba(220,190,130,.18);
  --shadow:0 2px 10px rgba(0,0,0,.28);
  --shadow-lg:0 10px 30px rgba(0,0,0,.38);
  --spine-1:rgba(0,0,0,.12);
  --spine-2:rgba(0,0,0,.28);
  --page-noise-opacity:.3;
  --btn-import-bg:linear-gradient(180deg,#4a3a28,#3e3020);
  --btn-import-tx:#ecd9b0;
}'''

assert old in s, 'dark block not found'
s = s.replace(old,new,1)
with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok')
