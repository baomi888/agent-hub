"""提醒中心界面的**结构契约**（2026-11-10 重排）。

为什么用源码断言而不是截图对比：这几条要钉的不是像素，而是「界面之后会不会走回去」——
比如某个后续需求把四个动作又平铺回卡片底下、或者时间轴又退回成一行 chip。
截图能证明今天对，源码断言能证明明天还不会错。

每组断言都配过反证（把那一行改坏 → 必须变红），没做过反证的断言不要往这里加。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "frontend" / "src" / "app" / "(console)" / "reminders" / "page.tsx"
CONSOLE_CSS = ROOT / "frontend" / "src" / "app" / "styles" / "console.css"
SHELL = ROOT / "frontend" / "src" / "components" / "console" / "PageShell.tsx"

# page.tsx 用 `// ==================== 段落 ====================` 划分，按段落取源码，
# 免得断言「卡片里没有暂停按钮」时连抽屉里的字段一起算进来。
SECTIONS = {
    "axis": ("// ==================== 今天的时间轴", "// ==================== 卡片"),
    "card": ("// ==================== 卡片", "// ==================== 抽屉"),
    "drawer": ("// ==================== 抽屉", "// ==================== 页面"),
}


def _strip_comments(src: str) -> str:
    """去掉 JSX / 块 / 行注释。

    这不是洁癖：断言「卡片体上不许出现『暂停这条』」时，解释为什么的那句注释
    会先把自己判红（本轮踩了两次）。断言只该看**代码**。
    """
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


def _page() -> str:
    return _strip_comments(PAGE.read_text(encoding="utf-8"))


def _section(name: str) -> str:
    """先按原文的段落标记切，再剥注释 —— 标记本身是行注释，剥完就找不到了。"""
    start, end = SECTIONS[name]
    raw = PAGE.read_text(encoding="utf-8")
    i = raw.index(start)
    j = raw.index(end) if end else len(raw)
    assert i < j, f"{name} 段落的标记顺序不对"
    return _strip_comments(raw[i:j])


def re_compact(path: Path) -> bool:
    """页面是否给 PageShell 传了 compact（只看 JSX 属性，不看注释）。"""
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "<PageShell" not in src:
        return False
    head = src[src.index("<PageShell") : src.index("<PageShell") + 400]
    return "\n      compact\n" in head or "compact={" in head


class TestCardDensity:
    """卡片 93px → 67px 靠的是「四个动作收进一个菜单」，不是把字压小。"""

    def test_card_body_has_exactly_two_click_targets(self):
        """卡片体（菜单弹出之前的部分）只能有两个 button：标题 + ⋯。

        标题＝改这条（最高频），⋯＝其余全部。多一个就说明动作又平铺回来了。
        """
        card = _section("card")
        body = card[: card.index('className="rem-menu"')]
        assert body.count("<button") == 2, "卡片体上出现了第三个可点"
        assert 'className="rem-title"' in body
        assert 'className="rem-morebtn"' in body

    def test_actions_only_exist_inside_the_menu(self):
        """暂停 / 推迟 / 删除 都不许出现在卡片体上。"""
        card = _section("card")
        body = card[: card.index('className="rem-menu"')]
        for word in ("暂停这条", "恢复这条", "推迟 10 分钟", "删除", "改时间 / 标题"):
            assert word not in body, f"「{word}」又跑到卡片体上了，应该只在 ⋯ 菜单里"

    def test_time_column_is_first_grid_column(self):
        """时间固定在左侧第 1 列、⋯ 固定在第 4 列。

        用 grid-column 而不是靠 DOM 顺序：`.rem-rest` 在窄屏 `display:none`
        会让网格少一格，靠自动放置时 ⋯ 会滑到中间去。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "grid-template-columns: 50px minmax(0, 1fr) auto 26px;" in css
        assert ".rem-when {\n  grid-column: 1;" in css
        assert ".rem-morebtn {\n  grid-column: 4;" in css
        assert ".rem-rest {\n  display: none;\n  grid-column: 3;" in css


class TestAxis:
    """时间轴要真的是 24 小时刻度，不是把几条横排一行。"""

    def test_scale_is_24h(self):
        axis = _section("axis")
        assert "(n.min / 1440) * 100" in axis, "节点没有按 24 小时定位"
        assert "(nowMin / 1440) * 100" in axis, "当前时刻没有按 24 小时定位"

    def test_now_marker_exists(self):
        axis = _section("axis")
        assert 'className="rem-axis-now"' in axis
        assert "useNowMinute" in _page()

    def test_nodes_jump_to_card(self):
        """点轴上的点必须能跳到对应卡片 —— 否则那个点只是个装饰。"""
        page = _page()
        assert "scrollIntoView" in page
        assert "rem-card-${item.id}" in page
        assert "onJump" in _section("axis")

    def test_no_date_call_during_render(self):
        """渲染期不能调 Date.now()（react-hooks/purity，也是 hydration 不一致的根源）。

        只允许出现在 `useNowMinute` 的定时器回调里 —— 也就是 `setNow(Date.now())`。
        直接断言整个函数段里的出现次数会被注释里的那句解释误伤，所以钉具体的调用形式。
        """
        page = _page()
        assert "useState(Date.now())" not in page
        assert page.count("setNow(Date.now())") == 1
        assert "Date.now()" in page

    def test_phone_hides_axis_labels(self):
        """390 宽里 1 小时只值 14px，六个标签必然连成 `10:2712:3015:00`。"""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        phone = css[css.index("@media (max-width: 900px)") :]
        assert ".rem-axis-node em {\n    display: none;" in phone


class TestDrawerOrder:
    """抽屉按规格「来源 → 触发 → 动作 → 送达」重排。"""

    def test_fields_follow_spec_order(self):
        drawer = _section("drawer")
        order = [
            drawer.index("提醒什么"),
            drawer.index("这是关于什么的"),
            drawer.index("什么时候提醒你"),
            drawer.index("到点之后"),
            drawer.index("怎么送达"),
        ]
        assert order == sorted(order), "抽屉字段顺序又和规格不一致了"

    def test_delivery_block_is_not_a_control(self):
        """「怎么送达」是说明块，不是下拉框 —— 否则看着又能选又不会生效。"""
        drawer = _section("drawer")
        i = drawer.index("怎么送达")
        block = drawer[i : i + 500]
        assert "<select" not in block
        assert 'className="rem-deliver"' in drawer

    def test_bottom_preview_reflects_the_form(self):
        drawer = _section("drawer")
        assert 'className="rem-preview"' in drawer
        assert "freqLine" in drawer and "ACTION_LABEL[action]" in drawer


class TestWeeklyEditRegression:
    """2026-11-10 修掉的 bug：weekly 提醒连改标题都存不了。

    前端对 weekly 传 `at: null`，后端 `_validate` 直接 400「时间要写成 HH:MM」。
    这条断言钉住「提交的 at 一定是字符串」，别再让它回来。
    """

    def test_no_null_at_on_weekly(self):
        page = _page()
        assert 'freq === "weekly" ? null : at' not in page
        assert "at: null" not in page

    def test_override_at_is_a_string(self):
        page = _page()
        assert "override: { title: draft.title, at }" in page


class TestGroupTitles:
    """分组标题只说「哪一组 · 几条」，不再自说自话。"""

    def test_no_self_explaining_hints(self):
        page = _page()
        for hint in ("今天会响的", "之后要响的", "不会响，点恢复就继续"):
            assert hint not in page, f"分组标题又挂回解释句「{hint}」"

    def test_empty_group_is_not_rendered(self):
        """三个零不提供信息：空分组整段不渲染。"""
        page = _page()
        assert "if (list.length === 0) return null;" in page


class TestCompactHead:
    """页头瘦身只给量过的页面用。"""

    def test_shell_supports_compact(self):
        shell = SHELL.read_text(encoding="utf-8")
        assert "compact?: boolean" in shell
        assert '"pg-head pg-head--compact"' in shell

    def test_only_reminders_asks_for_it(self):
        """别的页面没量过「省下来能不能换来一条内容」，不许跟着开。"""
        app = ROOT / "frontend" / "src" / "app"
        users = [p for p in app.rglob("*.tsx") if re_compact(p)]
        names = {p.parent.name for p in users}
        assert names <= {"reminders"}, f"compact 被这些页面用上了：{names}"

