"""每日总结页的标题与小节图标（2026-11-18）。

用户这一批的原话：
  · 「这个标题要改成每日总结」；
  · 「每个这个功能模块的文字前面加上对应的图标」。

所以这份测试钉三件事，都是"编译过、样式也不报错"的那类：
  1. 页头标题是**固定**的「每日总结」，不会翻到历史某天就变成日期 ——
     标题一变，底部导航进来的人会以为换了一个页面；
  2. 这一页的三层（活跃时段 / 今天的小结 / 出处折叠）各自有一枚图标，
     且三处都用同一个 `.rp-ic` 尺寸，不许一处 15px 一处 22px；
  3. 图标尺寸写在 CSS 里 —— CornIcon 自带 `width="100%"` 属性，
     className 传 Tailwind 的 `h-4 w-4` 在这个项目里不生效（没装 Tailwind 的
     preflight 那一套尺寸类不会覆盖属性值），图标会撑成父容器的 100%。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
PAGE = SRC / "app" / "(console)" / "report" / "page.tsx"
CSS = SRC / "app" / "styles" / "summary.css"


def _strip_comments(src: str) -> str:
    """注释里写着"标题以前是今天学了什么"，用原文匹配会自己红自己。"""
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def _page() -> str:
    return _strip_comments(PAGE.read_text(encoding="utf-8"))


def _css() -> str:
    return CSS.read_text(encoding="utf-8").replace("\r\n", "\n")


class TestTitle:
    def test_title_is_daily_summary_not_the_old_question(self):
        """标题是「每日总结」。

        反证：改回 `title={... : "今天学了什么"}`，本用例变红。
        """
        src = _page()
        assert 'title="每日总结"' in src, "页头标题不是「每日总结」"
        assert "今天学了什么" not in src, "旧标题还在（页头是真会被读到的第一行）"

    def test_title_does_not_turn_into_a_date(self):
        """翻到历史某天时标题也不许变成日期。

        同一个页面两种标题，是"以为自己点进了另一个页面"的典型来源。
        日期在下方的概览卡眉题和日期胶囊上都有，不缺标题这一处。

        反证：把 title 改回三元表达式，本用例变红。
        """
        src = _page()
        # title 后面必须直接是字符串字面量，不能是表达式
        assert re.search(r'title="每日总结"\s*\n', src), "title 成了表达式"


class TestSectionIcons:
    def test_three_sections_have_an_icon(self):
        """活跃时段 / 今天的小结 / 出处折叠 —— 三层各一枚图标。"""
        src = _page()
        assert src.count('className="rp-ic"') >= 3, "不是三层都挂了图标"
        # 逐处点名：只数个数的话，把三枚都塞在同一个 <p> 里也能过
        assert re.search(
            r'<span className="rp-hours-label">\s*<CornIcon[^>]*className="rp-ic"', src
        ), "「什么时候在学」前面没有图标"
        assert re.search(
            r'<p className="rp-sec">\s*<CornIcon[^>]*className="rp-ic"', src
        ), "「今天的小结」前面没有图标"
        assert re.search(
            r"<summary>\s*<CornIcon[^>]*className=\"rp-ic\"", src
        ), "「条数为什么比看起来的少？」前面没有图标"

    def test_icons_are_sized_in_css(self):
        """`.rp-ic` 必须给死宽高，否则 svg 会按父容器的 100% 撑开。"""
        css = _css()
        rule = re.search(r"\.rp-ic\s*\{[^}]*\}", css)
        assert rule, "summary.css 里没有 .rp-ic"
        body = rule.group(0)
        assert re.search(r"width:\s*15px", body), "图标没给宽"
        assert re.search(r"height:\s*15px", body), "图标没给高"
        assert "flex: none" in body, "图标会被 flex 压扁"

    def test_icon_sits_on_the_text_line(self):
        """`.rp-sec` 的 align-items 必须是 center。

        svg 的 baseline 是它的底边，父级 `align-items: baseline` 会让图标整体
        沉到文字下面半个字高 —— 这一条只在截图里看得出来，所以用规则钉住。

        反证：把 center 改回 baseline，本用例变红。
        """
        css = _css()
        rule = re.search(r"\.rp-sec\s*\{[^}]*\}", css)
        assert rule, "找不到 .rp-sec"
        assert "align-items: center" in rule.group(0), ".rp-sec 还是 baseline 对齐"

    def test_hours_label_keeps_both_ends(self):
        """「什么时候在学」带图标后，父级仍是两端对齐（右边还有一句结论）。"""
        css = _css()
        label = re.search(r"\.rp-hours-label\s*\{[^}]*\}", css)
        assert label, "缺 .rp-hours-label"
        assert "inline-flex" in label.group(0)
        head = re.search(r"\.rp-hours-head\s*\{[^}]*\}", css)
        assert "space-between" in head.group(0), "活跃时段那行丢了两端对齐"
