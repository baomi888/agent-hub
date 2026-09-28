# -*- coding: utf-8 -*-
import io
p = r'C:\Users\HUAWEI\Desktop\苞米agent\baomi-agent\苞米Agent.html'
with io.open(p, 'r', encoding='utf-8') as f:
    s = f.read()

old = '''        <div><div class="ct">帮我写个计划</div><div class="cs">知识库 · 工作笔记</div></div>
      </div>
    </div>
  </aside>'''

new = '''        <div><div class="ct">帮我写个计划</div><div class="cs">知识库 · 工作笔记</div></div>
      </div>
    </div>

    <button class="theme-toggle" onclick="toggleTheme()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
        <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>
      </svg>
      <span id="themeLabel">深色模式</span>
    </button>
  </aside>'''

assert old in s, 'old block not found'
s = s.replace(old, new, 1)

# add toggleTheme function before send()
old_js = '''function send(){'''
new_js = '''function toggleTheme(){
  const el=document.documentElement;
  const dark=el.getAttribute('data-theme')==='dark';
  if(dark){el.removeAttribute('data-theme');}
  else{el.setAttribute('data-theme','dark');}
  document.getElementById('themeLabel').textContent = dark?'深色模式':'浅色模式';
}
function send(){'''
assert old_js in s, 'js block not found'
s = s.replace(old_js, new_js, 1)

with io.open(p, 'w', encoding='utf-8') as f:
    f.write(s)
print('ok, length=', len(s))
