# -*- coding: utf-8 -*-
"""把 data_block.txt 拼进 code-map-template.html，只替换 MAP-DATA 区间与 MAP-META 元信息。"""
import os
import re
import shutil
import sys

ROOT = r"C:\biancheng\agent-hub"
SKILL = os.path.join(ROOT, "docs", "答辩可视化", "skill", "xfg-coding-map-skills-main")
TPL = os.path.join(SKILL, "assets", "code-map-template.html")
BLOCK = os.path.join(ROOT, ".scratch", "codemap", "data_block.txt")
OUT_DIR = os.path.join(ROOT, "docs", "code-map", "agent-hub-overview")
OUT = os.path.join(OUT_DIR, "agent-hub-overview.html")

html = open(TPL, encoding="utf-8").read()
block = open(BLOCK, encoding="utf-8-sig").read().lstrip("\ufeff").rstrip() + "\n"

# ---- 1. 整体替换 MAP-DATA 区间 ----
pat = re.compile(r"/\*\s*═+\s*MAP-DATA-START\s*═+.*?/\*\s*═+\s*MAP-DATA-END\s*═+\s*\*/", re.S)
found = pat.findall(html)
if len(found) != 1:
    sys.exit(f"MAP-DATA 区间匹配数异常：{len(found)}")
html = pat.sub(lambda m: block.rstrip(), html, count=1)

# ---- 2. 元信息 ----
REPL = [
    ("<title>xfg-2-1 开发串联全景图 · 对比基线 xfg-1-3</title>",
     "<title>知穗 Agent · 全项目代码地图</title>"),
    ("<h1>xfg-2-1 开发串联全景图</h1>",
     "<h1>知穗 Agent · 全项目代码地图</h1>"),
    ("<p>基线 <b>xfg-1-3</b><i>→</i>本节 <b>xfg-2-1</b> · LLM 端口-适配器链路</p>",
     "<p>单进程 FastAPI 内核 <i>·</i> 五条执行分支 <i>·</i> 双引擎与混合检索</p>"),
    ("""    <div class="stat"><span class="num">20</span><span class="lbl">新增文件</span></div>
    <div class="stat"><span class="num amber">4</span><span class="lbl">调整文件</span></div>
    <div class="stat"><span class="num">+3278</span><span class="lbl">/ -1 行</span></div>""",
     """    <div class="stat"><span class="num">107</span><span class="lbl">API 端点</span></div>
    <div class="stat"><span class="num amber">22</span><span class="lbl">router</span></div>
    <div class="stat"><span class="num">86</span><span class="lbl">测试文件</span></div>"""),
    ('<span class="proj">cn.xiaofuge.deepseek.harness</span>',
     '<span class="proj">agent-hub · FastAPI + Next.js</span>'),
    ('<div class="cap">变更说明 · vs xfg-1-3</div>',
     '<div class="cap">节点说明 · 全项目架构</div>'),
    ("<h2>xfg-2-1 开发串联全景图</h2>",
     "<h2>知穗 Agent · 全项目代码地图</h2>"),
    ('<div class="w-sub">基线 <b>xfg-1-3</b> → 本节 <b>xfg-2-1</b> · LLM 端口-适配器链路 · 新增 20 文件 / +3278 行</div>',
     '<div class="w-sub"><b>107</b> 个 API 端点 · <b>22</b> 个 router · <b>99</b> 个后端模块 · 一次提问穿过 6 层</div>'),
    ("<li><span class=\"wi\">🧭</span>点上方<b>「跟着流程走」</b>导航条，8 站从装配走到组装，每站自动定位联动</li>",
     "<li><span class=\"wi\">🧭</span>点上方<b>「跟着流程走」</b>导航条，10 站从入口走到护栏，每站自动定位联动</li>"),
    # 图例语义（本图是 Onboarding，不再是"新增/复用"的迭代对比口径）
    ('<i><span class="dot" style="background:#e6f6ec;border:1.5px solid #17a45f"></span>🆕 新增</i>',
     '<i><span class="dot" style="background:#e6f6ec;border:1.5px solid #17a45f"></span>核心链路</i>'),
    ('<i><span class="dot" style="background:#f1f3f6;border:1.5px dashed #9aa3af"></span>1-3 复用</i>',
     '<i><span class="dot" style="background:#f1f3f6;border:1.5px dashed #9aa3af"></span>周边模块</i>'),
    ('<i><span class="dot" style="background:#eaf1fe;border:1.5px solid #3574f0"></span>外部网关</i>',
     '<i><span class="dot" style="background:#eaf1fe;border:1.5px solid #3574f0"></span>存储 / 外部依赖</i>'),
    ('<i><span class="dot" style="background:#fff4e0;border:1.5px solid #e6a23c"></span>异常 / 兜底</i>',
     '<i><span class="dot" style="background:#fff4e0;border:1.5px solid #e6a23c"></span>护栏 / 兜底</i>'),
    ("'xfg21-welcomed'", "'zhisui-codemap-welcomed'"),
]

for old, new in REPL:
    n = html.count(old)
    if n == 0:
        sys.exit("未找到待替换片段：" + old[:60])
    if n > 1 and "xfg21-welcomed" not in old:
        sys.exit(f"片段出现 {n} 次，无法唯一替换：" + old[:60])
    html = html.replace(old, new)

os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(html)

# ---- 3. vendor 离线渲染件 ----
dst_vendor = os.path.join(OUT_DIR, "vendor")
if os.path.isdir(dst_vendor):
    shutil.rmtree(dst_vendor)
shutil.copytree(os.path.join(SKILL, "assets", "vendor"), dst_vendor)

print("生成：", OUT, len(html), "chars")
print("vendor：", os.listdir(dst_vendor))
