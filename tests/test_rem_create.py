"""待办页的两个新建入口（2026-11-17）。

评审原话：「用户不知道点击这个『只要到时间提醒……』就能新建提醒，同时待办和提醒
应该是同等级别的，要有两个按钮。」

改法：
  · 页头并排放两个按钮 —— 带 ＋ 的是待办、带铃的是提醒，同尺寸同样式；
  · 列表底部那行灰字链接连同样式一起删（它既是"解释"，又是藏起来的第二个入口）；
  · 窄屏（≤900）标签收成「待办 / 提醒」，否则四个字会把页头挤成两行；
  · 触控热区用伪元素补到 44，视觉高度不动。

这里钉的是"两个按钮真的平级、真的都通到各自的抽屉、旧入口真的没了"：
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


def test_two_create_buttons_with_the_same_class():
    """两个入口必须是同一个类，谁也别想偷偷升成主按钮。"""
    src = _code(REM)
    assert src.count('className="rem-new"') == 2, (
        "页头应该正好两个 .rem-new 按钮（待办 / 提醒），实际 %d 个"
        % src.count('className="rem-new"')
    )
    # 分级修饰会让它们看起来一主一次 —— 这条就是评审要求的那句"同等级别"
    assert "rem-new--primary" not in src, "又给其中一个加了主次修饰"


def test_each_button_opens_its_own_drawer():
    """左边那颗必须开待办抽屉，右边那颗必须开提醒抽屉（不能都开同一个）。"""
    src = _code(REM)
    task = re.search(r'className="rem-new"[^>]*onClick=\{\(\) => openTaskDrawer\(null\)\}', src)
    rem = re.search(r'className="rem-new"[^>]*onClick=\{\(\) => openReminderDrawer\(null\)\}', src)
    assert task, "没有「新建待办」按钮，或它没开待办抽屉"
    assert rem, "没有「新建提醒」按钮，或它没开提醒抽屉"
    # 提醒那颗必须有铃铛图标，待办那颗必须有 ＋：两个都写「＋」会分不清哪个建什么
    block = src[src.index('className="rem-new"') :]
    assert 'name="bell"' in block, "提醒按钮缺铃铛图标"
    assert 'name="plus"' in block, "待办按钮缺 ＋ 图标"


def test_old_footer_link_is_gone():
    """列表底部那行「只要到时间提醒，不用勾完成？新建提醒」连同样式一起删干净。"""
    page = _code(REM)
    assert "rem-footlink" not in page, "页脚链接的 DOM 还在"
    assert "rem-footlink" not in _code(CONSOLE_CSS), "页脚链接的样式还在（没人用的死类）"


def test_short_labels_only_on_narrow_screens():
    """只有最窄的一档用短标签，390 起给全称。

    实测（2026-11-17，四档宽度 × 长短两版）：
      390  全称页头 104.2，短标签也 104.2 —— **不多占一行，没理由收字**
      360  全称 148.2（多一行）
      320  两版都 148.2（反正已经三行）
      430  全称 104.2（从一行变两行）
    取舍：430 上认了多一行，换 390/412 这些主流宽度把「新建」二字说出来。
    """
    page = _code(REM)
    assert src_has(page, 'className="rem-new-full"'), "缺宽屏标签节点"
    assert src_has(page, 'className="rem-new-short"'), "缺窄屏标签节点"
    css = _code(CONSOLE_CSS)
    assert re.search(r"\.rem-new-short\s*\{\s*display:\s*none", css), (
        "短标签默认必须是隐藏的（宽屏只看完整标签）"
    )
    # 切换必须发生在 ≤375 那一段里，而不是一路糊到 900
    narrow = css[css.index(".rem-new-short") :]
    assert re.search(r"\.rem-new-full\s*\{\s*display:\s*none", narrow), (
        "窄屏没把完整标签藏起来"
    )
    assert re.search(r"\.rem-new-short\s*\{\s*display:\s*inline", narrow), "窄屏没把短标签放出来"
    # 切换必须发生在 ≤375 那一段里：抓「包住 .rem-new-full{display:none} 的那条媒体查询」，
    # 而不是只 grep 一个 "@media (max-width: 375px)" —— 后者页里别处也可能有。
    m = re.search(r"@media \(max-width: (\d+)px\)\s*\{(?:(?!@media)[\s\S])*?\.rem-new-full\s*\{", css)
    assert m, "找不到控制长短标签的媒体查询"
    assert m.group(1) == "375", f"切换档位是 {m.group(1)}px，390 上会被收成「待办 / 提醒」两个字"


def src_has(hay: str, needle: str) -> bool:
    return needle in hay


def test_touch_area_is_padded_not_inflated():
    """36 高的按钮要把热区补到 44：用伪元素扩，别把视觉高度撑大（会顶开页头）。"""
    css = _code(TOUCH)
    assert ".rem-new::before" in css, "touch.css 没给 .rem-new 补热区"
    # 它和「列表 / 设置」同排，只能纵向扩（横向扩会盖住隔壁那颗）
    m = re.search(r"\.rem-new::before\s*\{[^}]*inset:\s*(-?\d+)px\s+0", css)
    assert m, ".rem-new 的热区不是「只扩纵向」写法"
    assert abs(int(m.group(1))) >= 4, "36 高要补到 44，inset 至少要 4px"
