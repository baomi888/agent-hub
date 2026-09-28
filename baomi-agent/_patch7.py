# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p,'r',encoding='utf-8') as f:
    s=f.read()

# 1) 纸张：加顶部高光 + 靠近书脊的纸页弧度 inset 阴影
old = '''.page-l{left:0;border-radius:6px 0 0 6px}
.page-r{right:0;border-radius:0 6px 6px 0}'''
new = '''.page-l{left:0;border-radius:6px 0 0 6px;box-shadow:inset -24px 0 36px -24px var(--curl)}
.page-r{right:0;border-radius:0 6px 6px 0;box-shadow:inset 24px 0 36px -24px var(--curl)}'''
assert old in s; s=s.replace(old,new,1)

# 浅色/深色补 --curl 变量
s = s.replace(
  "  --btn-import-tx:#2c2214;\n}",
  "  --btn-import-tx:#2c2214;\n  --curl:rgba(90,65,35,.14);\n}",
  1)
s = s.replace(
  "  --btn-import-tx:#ecd9b0;\n}",
  "  --btn-import-tx:#ecd9b0;\n  --curl:rgba(0,0,0,.22);\n}",
  1)

# 2) 会话项 hover：加金色左指示条
old = '''.chat-item{
  display:flex;gap:10px;align-items:flex-start;
  padding:10px 8px;border-radius:8px;cursor:pointer;
  transition:background .15s;
}
.chat-item:hover{background:rgba(255,253,246,.55)}'''
new = '''.chat-item{
  position:relative;
  display:flex;gap:10px;align-items:flex-start;
  padding:10px 8px 10px 12px;border-radius:8px;cursor:pointer;
  transition:background .18s, transform .18s;
}
.chat-item::before{
  content:"";position:absolute;left:2px;top:50%;transform:translateY(-50%) scaleY(0);
  width:3px;height:60%;border-radius:2px;background:var(--gold);
  transition:transform .18s;
}
.chat-item:hover{background:rgba(255,253,246,.55)}
.chat-item:hover::before{transform:translateY(-50%) scaleY(1)}'''
assert old in s; s=s.replace(old,new,1)

# 3) 功能卡片 hover：图标变色 + 标签微动
old = '''.feat{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:14px;
  padding:20px 10px 16px;
  text-align:center;cursor:pointer;
  box-shadow:var(--shadow);
  transition:transform .18s, box-shadow .18s;
}
.feat:hover{transform:translateY(-3px);box-shadow:var(--shadow-lg)}
.feat .fico{width:30px;height:30px;margin:0 auto 10px;color:var(--gold)}
.feat .flabel{font-size:14px;color:var(--ink);font-weight:500}'''
new = '''.feat{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:14px;
  padding:20px 10px 16px;
  text-align:center;cursor:pointer;
  box-shadow:var(--shadow);
  transition:transform .2s, box-shadow .2s, border-color .2s;
}
.feat:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg);border-color:var(--gold)}
.feat .fico{width:30px;height:30px;margin:0 auto 10px;color:var(--gold);transition:transform .2s}
.feat:hover .fico{transform:scale(1.12) rotate(-3deg)}
.feat .flabel{font-size:14px;color:var(--ink);font-weight:500;transition:color .2s}
.feat:hover .flabel{color:var(--gold)}'''
assert old in s; s=s.replace(old,new,1)

# 4) 输入框聚焦态 + 过渡
old = '''.input-box{
  max-width:880px;margin:0 auto;
  background:var(--card);
  border:1px solid var(--line);
  border-radius:16px;
  box-shadow:var(--shadow-lg);
  display:flex;align-items:center;gap:12px;
  padding:14px 16px 14px 18px;
}'''
new = '''.input-box{
  max-width:880px;margin:0 auto;
  background:var(--card);
  border:1px solid var(--line);
  border-radius:16px;
  box-shadow:var(--shadow-lg);
  display:flex;align-items:center;gap:12px;
  padding:14px 16px 14px 18px;
  transition:border-color .25s, box-shadow .25s, transform .25s;
}
.input-box:focus-within{
  border-color:var(--gold);
  box-shadow:0 10px 30px rgba(196,154,62,.18), 0 0 0 4px rgba(196,154,62,.12);
  transform:translateY(-1px);
}'''
assert old in s; s=s.replace(old,new,1)

# 5) 发送按钮 hover 动效加强
old = '.send:hover{transform:scale(1.06)}'
new = '.send:hover{transform:scale(1.1);box-shadow:0 6px 16px rgba(196,154,62,.55)}'
assert old in s; s=s.replace(old,new,1)

# 6) 搜索框 focus 态
old = '''.search input{
  width:100%;padding:10px 12px 10px 36px;
  border:1px solid rgba(122,92,54,.25);
  background:rgba(255,253,246,.6);
  border-radius:10px;font-size:13px;color:var(--ink);
  outline:none;
}'''
new = '''.search input{
  width:100%;padding:10px 12px 10px 36px;
  border:1px solid rgba(122,92,54,.25);
  background:rgba(255,253,246,.6);
  border-radius:10px;font-size:13px;color:var(--ink);
  outline:none;transition:border-color .2s, box-shadow .2s, background .2s;
}
.search input:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(196,154,62,.12);background:var(--card)}'''
assert old in s; s=s.replace(old,new,1)

with io.open(p,'w',encoding='utf-8') as f:
    f.write(s)
print('ok len', len(s))
