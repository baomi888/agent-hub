"""待办页的新建入口（2026-11-19 提醒并入待办后改为单一入口）。

2026-11-17 的口径是「两个按钮平级」（待办 / 提醒）；2026-11-19 产品决策
「提醒也是一种待办」，提醒不再是独立对象：
  · 页头只剩一颗「新建待办」，提醒的创建走任务抽屉里的循环规则；
  · openReminderDrawer / ReminderDrawer 已整体删除，再出现就是回流；
  · 「rem-new-full / rem-new-short」长短标签机制保留（只是只剩待办一颗）。

这里钉的是"单一入口真的成立、旧双入口真的没了"：
编译、tsc、点一遍首页都看不出退化的那类。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
REM = SRC / "app" / "(console)" / "reminders" / "page.tsx"
CONSOLE_CSS = SRC / "app" / "styles" / "console.css"
TOUCH = SRC / "app" / "styles" / "touch.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    """剥注释再断言 —— 注释里写着"删掉了哪一条"，用原文匹配会自己红自己。"""
    src = re.sub(r"/\*.*?\*/", "", _read(p), flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_single_create_button():
    """页头正好一颗「新建待办」，没有第二颗、没有主次修饰。"""
    src = _code(REM)
    n_buttons = src.count('className="rem-new"')
    assert n_buttons == 1, f"页头应该正好一个 .rem-new 按钮，实际 {n_buttons} 个"
    assert "rem-new--primary" not in src, "又给按钮加了主次修饰"
    assert "openReminderDrawer" not in src, "提醒抽屉的入口回流了"
    assert "ReminderDrawer" not in src, "ReminderDrawer 组件回流了"


def test_button_opens_task_drawer():
    """唯一那颗必须开待办抽屉，且带 ＋（提醒不再有独立按钮）。"""
    src = _code(REM)
    task = re.search(r'className="rem-new"[^>]*onClick=\{\(\) => openTaskDrawer\(null\)\}', src)
    assert task, "「新建待办」按钮没开待办抽屉"
    assert 'name="plus"' in src, "待办按钮缺 ＋ 图标"


def test_old_footer_link_is_gone():
    """列表底部那行「只要到时间提醒，不用勾完成？新建提醒」连同样式一起删干净。"""
    page = _code(REM)
    assert "rem-footlink" not in page, "页脚链接的 DOM 还在"
    assert "rem-footlink" not in _code(CONSOLE_CSS), "页脚链接的样式还在（没人用的死类）"


def test_short_labels_only_on_narrow_screens():
    """只有最窄的一档用短标签，390 起给全称。"""
    page = _code(REM)
    assert src_has(page, 'className="rem-new-full"'), "缺宽屏标签节点"
    assert src_has(page, 'className="rem-new-short"'), "缺窄屏标签节点"
    css = _code(CONSOLE_CSS)
    assert re.search(r"\.rem-new-short\s*\{\s*display:\s*none", css), (
        "短标签默认必须是隐藏的（宽屏只看完整标签）"
    )
    narrow = css[css.index(".rem-new-short") :]
    assert re.search(r"\.rem-new-full\s*\{\s*display:\s*none", narrow), (
        "窄屏没把完整标签藏起来"
    )
    assert re.search(r"\.rem-new-short\s*\{\s*display:\s*inline", narrow), "窄屏没把短标签放出来"
    m = re.search(r"@media \(max-width: (\d+)px\)\s*\{(?:(?!@media)[\s\S])*?\.rem-new-full\s*\{", css)
    assert m, "找不到控制长短标签的媒体查询"
    assert m.group(1) == "375", f"切换档位是 {m.group(1)}px，390 上会被收成「待办」两个字"


def src_has(hay: str, needle: str) -> bool:
    return needle in hay


def test_touch_area_is_padded_not_inflated():
    """36 高的按钮要把热区补到 44：用伪元素扩，别把视觉高度撑大（会顶开页头）。"""
    css = _code(TOUCH)
    assert ".rem-new::before" in css, "touch.css 没给 .rem-new 补热区"
    m = re.search(r"\.rem-new::before\s*\{[^}]*inset:\s*(-?\d+)px\s+0", css)
    assert m, ".rem-new 的热区不是「只扩纵向」写法"
    assert abs(int(m.group(1))) >= 4, "36 高要补到 44，inset 至少要 4px"
