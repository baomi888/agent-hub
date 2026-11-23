# -*- coding: utf-8 -*-
"""生成自查预览页：隐藏欢迎层 + 底部打印 SVG 自然尺寸/缩放比（仅自查，不交付）。"""
import os

D = r"C:\biancheng\agent-hub\docs\code-map\agent-hub-overview"
src = open(os.path.join(D, "agent-hub-overview.html"), encoding="utf-8").read()

base = src.replace('<div class="welcome-mask" id="welcomeMask">',
                   '<div class="welcome-mask hide" id="welcomeMask">')

DBG = """
/* ---- preview debug ---- */
const dbg = document.createElement('div');
dbg.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:9999;background:#000;color:#0f0;font:13px monospace;padding:6px 10px;border-radius:4px';
const _n = naturalSize('over');
const _s = naturalSize('seq');
const _f = naturalSize('flow');
dbg.textContent = 'over ' + Math.round(_n.w) + 'x' + Math.round(_n.h) + ' @' + currentScale('over').toFixed(3)
  + ' | seq ' + Math.round(_s.w) + 'x' + Math.round(_s.h) + ' @' + currentScale('seq').toFixed(3)
  + ' | flow ' + Math.round(_f.w) + 'x' + Math.round(_f.h) + ' @' + currentScale('flow').toFixed(3);
document.body.appendChild(dbg);
"""

for name, swap in (
    ('over', None),
    ('seq', ('seq',)),
    ('flow', ('flow',)),
):
    html = base
    if swap:
        html = (html
                .replace('<div class="tab on" data-tab="over">', '<div class="tab" data-tab="over">')
                .replace(f'<div class="tab" data-tab="{name}">', f'<div class="tab on" data-tab="{name}">', 1)
                .replace('<div class="diagram-wrap on" id="wrap-over">', '<div class="diagram-wrap" id="wrap-over">')
                .replace(f'<div class="diagram-wrap" id="wrap-{name}">', f'<div class="diagram-wrap on" id="wrap-{name}">'))
    html = html.replace("</script>\n</body>", DBG + "</script>\n</body>")
    open(os.path.join(D, f"_preview-{name}.html"), "w", encoding="utf-8", newline="\n").write(html)

print("previews ready")
