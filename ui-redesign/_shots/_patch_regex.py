# -*- coding: utf-8 -*-
"""修复 ChatArea.tsx：时间轴正则去掉裸"阶段"误判"""
p = r"C:\Users\HUAWEI\Desktop\苞米agent\frontend\src\components\ChatArea.tsx"
with open(p, encoding="utf-8") as f:
    src = f.read()

old = "|筹备期|预热期|执行期|收尾|阶段)/i.test(line)"
new = "|筹备期|预热期|执行期|收尾|第[一二三四五六七八九十0-9]{1,3}阶段)/i.test(line)"
assert old in src, "pattern not found"
src = src.replace(old, new, 1)
with open(p, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("patched")
# 确认
import re
m = re.search(r"/\(.*?\)/i\.test\(line\)", src)
print(m.group(0)[:160])
