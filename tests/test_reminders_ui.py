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


def _css_block(css: str, selector: str) -> str:
    """取某个选择器后面那对花括号里的声明。

    找不到就抛（ValueError），不返回空串 —— 断言落在空串上会「静默变绿」，
    比直接报错难查得多。
    """
    i = css.index(selector)
    j = css.index("{", i) + 1
    return css[j : css.index("}", j)]


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
    """时间轴要真的是 24 小时刻度，不是把几条横排一行。

    2026-11-10 第二版把轴从「一条 1px 细线 + 几个空心圆」换成「有厚度的带子」，
    这里钉的是新结构里**每一条都会退化**的地方：带子被改回和卡片同色、刻度被统一成
    贯穿全高的条形码、静默时段被删掉、同一分钟的齿孔被拆回两枚……
    每一条都做过反证（改坏对应的那一行 → 必须红）。
    """

    def test_scale_is_24h(self):
        axis = _section("axis")
        assert "const DAY_MIN = 1440;" in axis, "一天又不在轴上了"
        assert "(e.min / DAY_MIN) * 100" in axis, "齿孔没有按 24 小时定位"
        assert "(nowMin / DAY_MIN) * 100" in axis, "当前时刻没有按 24 小时定位"

    def test_now_marker_exists(self):
        axis = _section("axis")
        assert 'className="rem-now"' in axis
        assert "useNowMinute" in _page()

    def test_nodes_jump_to_card(self):
        """点齿孔必须能跳到对应卡片 —— 否则那枚齿孔只是个装饰。"""
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

    def test_tape_is_a_groove_not_a_line(self):
        """带子要比卡片暗一档 —— 它是挖进纸里的槽。

        第一版写的是 step-1 → step-2（比卡片还亮），970px 宽的卡里那条轴拍出来
        几乎看不见，只剩 24 个数字浮在空白上。这条防的就是改回去。
        """
        body = _css_block(CONSOLE_CSS.read_text(encoding="utf-8"), ".rem-tape-body {")
        assert "background: var(--step-0);" in body, "带子又和卡片同色了，整条轴会消失"
        assert "inset 0 1px 3px" in body, "上缘内阴影没了，凹槽感就没了"

    def test_ticks_are_a_ruler(self):
        """整点短划只贴带子下缘，6 小时那道才贯穿。

        24 道都贯穿的话，18px 高的带子会变成条形码 —— 那是把「有刻度」和「有节奏」
        搞混了。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        tick = _css_block(css, ".rem-tick {")
        assert "height: 5px;" in tick, "整点刻线又贯穿全高了"
        assert "bottom: 0;" in tick
        major = _css_block(css, '.rem-tick[data-major="1"] {')
        assert "height: 100%;" in major, "6 小时的主刻度不再是贯穿的那一道"

    def test_quiet_hours_are_drawn(self):
        """静默时段要画成斜纹 —— 「这一段到点也不响」是轴上第二条真信息。

        它比再加一个圆点有用：用户看到 23:30 的提醒时，得能明白它为什么不会响。
        """
        axis = _section("axis")
        assert "quietBands(" in axis
        assert 'className="rem-tape-quiet"' in axis
        assert "quietStart" in axis and "quietEnd" in axis
        # 值必须从 prefs 来，不能是组件里另写一个 23:00
        assert "quietStart={prefs?.quiet_start}" in _page()

    def test_quiet_band_splits_at_midnight(self):
        """23:00–07:00 跨零点，要拆成「0 点到结束」和「开始到 24 点」两截。

        不拆的话那一段宽 100%（负宽），整条带子都会变成斜纹。
        """
        axis = _section("axis")
        assert "if (a > b) {" in axis, "跨零点的静默段没有拆开"
        assert "{ from: 0, to: b }" in axis
        assert "{ from: a, to: DAY_MIN }" in axis

    def test_same_minute_merges_into_one_hole(self):
        """同一分钟的多条合成一枚齿孔。

        服务端本来就把同一分钟的提醒并成一条通知；轴上画两枚只会互相压住，
        而且叠在上面的那枚永远点不到。
        """
        axis = _section("axis")
        assert "new Map<number," in axis
        assert "buckets.get(min)" in axis, "同一分钟又被拆成两枚齿孔了"

    def test_dark_groove_goes_lighter(self):
        """深色下带子要比卡片**亮**一档，不能沿用浅色那套「比卡片暗」。

        深色卡片是 L*13.7，再暗到 step-0 的 L*7.5 只差 6 个明度，在近黑底上
        实拍等于没画（右半截整条融进卡片，只剩金色已过段浮着）。深色只有往上
        才有空间 —— 靠顶部那道内阴影维持「挖进去」，边界才立得住。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        dark = _css_block(css, ".dark .rem-tape-body {")
        assert "background: rgba(237, 224, 196" in dark, "深色带子又回到「比卡片更暗」了"
        assert "var(--step-0)" not in dark
        assert "inset 0 1px" in dark, "深色的凹陷感全丢了"

    def test_no_left_anchored_fill(self):
        """轴上不许再有「从左端起染的已过填充」。

        这条是**反向**断言，代价最大的一次决定：淡金渐染 + 贴底水位线做了两版，
        两版都被读成媒体播放器的已播放进度条 —— 因为形状就是「从最左端开始填、
        推进端正好是游标」。这条轴要回答的是「今天几点有事」，不是「过去了多少」；
        后者已经由游标上的时间胶囊回答了，再画一遍就是重复，还抢走了事件点的第一眼。
        """
        page = _page()
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "rem-tape-past" not in page, "已过填充又回来了，整条轴会被读成进度条"
        assert ".rem-tape-past" not in css

    def test_quiet_paints_under_ticks(self):
        """斜纹要垫在刻度下面。

        静默段 23:00–07:00 正好盖住 6 点那道贯穿刻度；斜纹画在刻度之上时，
        那道线在深色里和斜纹同色，整条消失 —— 静默段越清楚，时间骨架反而越糊。
        """
        axis = _section("axis")
        assert axis.index("rem-tape-quiet") < axis.index("rem-tick"), "斜纹又盖到刻度上面了"

    def test_hover_yields_the_now_pill(self):
        """悬停某一条时，「现在」的胶囊要让位。

        两者都在带子上方、同一高度：12:51 的胶囊和 15:00 的气泡并排时，
        读起来是一句「12:51 15:00 专注 25 分钟」。让步只在悬停期间发生。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert ".rem-tape:has(.rem-ev:hover) .rem-now" in css
        assert "opacity: 0;" in _css_block(css, ".rem-tape:has(.rem-ev:hover) .rem-now")

    def test_tooltip_arrow_is_outlined(self):
        """气泡的指向三角必须是描边的，不能是纸色实心。

        第一版用 border-top 画实心三角、颜色取卡片纸色 —— 纸色三角压在同一张纸上，
        截图里完全不存在。改成 45° 方块、只描朝外的两条边。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        arrow = _css_block(css, ".rem-ev b::after {")
        assert "rotate(45deg)" in arrow
        assert "border-right: 1px solid" in arrow and "border-bottom: 1px solid" in arrow
        assert "border-top:" not in arrow, "又退回实心三角了"

    def test_events_are_solid_dots(self):
        """事件是实心墨点，不是一圈空心环。

        这条轴要回答的第一个问题是「今天几点有事」，「已经过去多少」排在它后面。
        空心圈只有 1px 的环，视觉重量低于它身后的金染 —— 整条轴会被读成进度条。
        评审原话：「把重做的力气全给了已过/未过，事件层原封不动留在旧版上」。
        """
        dot = _css_block(CONSOLE_CSS.read_text(encoding="utf-8"), ".rem-ev i {")
        assert "background: var(--ink);" in dot, "事件点又变回空心圈了"
        assert "border:" not in dot, "实心点不该再有描边环"
        assert "width: 11px;" in dot, "点的直径缩回去了"

    def test_dark_keeps_the_skeleton(self):
        """深色下刻度线必须翻成米白。

        刻度是暖墨 alpha，在 #1A1610 的带上等于没画 —— 深色截图里 6 小时那四道
        分隔线整条消失，带子只剩一片黑，24 小时的结构就没了。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert ".dark .rem-tick {" in css, "深色没有覆盖刻度线"
        assert "rgba(237, 224, 196" in _css_block(css, ".dark .rem-tick {")
        assert ".dark .rem-tape-hours {" in css, "深色刻度数字还是最暗那一档"

    def test_now_pill_reads_in_both_themes(self):
        """时间胶囊用墨底 + 纸色字 —— 明暗两套都成立。

        `--accent-ink` 在深色主题下是亮金 #E0A03A，压白字只有 2:1；
        这一枚胶囊是全轴唯一实心的东西，读不清就白搭。
        """
        pill = _css_block(CONSOLE_CSS.read_text(encoding="utf-8"), ".rem-now b {")
        assert "background: var(--ink);" in pill
        assert "color: var(--card-bg);" in pill

    def test_phone_thins_the_tape(self):
        """390 宽里 1 小时只值 16px：整点刻线全关、数字只留 0/12/24。

        第一版的做法是「手机上把时间标签藏掉」；新版没有常驻标签了，
        要收的是刻度密度和数字个数。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        phone = css[css.index("@media (max-width: 900px)") :]
        assert '.rem-tick[data-major="0"] {\n    display: none;' in phone, "手机上 24 道刻线还在"
        assert ".rem-tape-hours span:nth-child(2)," in phone, "手机上还标着 6 和 18"
        assert ".rem-tape-hours span:nth-child(4)" in phone


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
        """「怎么送达」是说明块，不是下拉框 —— 否则看着又能选又不会生效。

        断言数整个抽屉的 select：只数「怎么送达」之后的那 500 字抓不到
        「把控件插在标题前面」这种写法（反证时试出来的）。
        """
        drawer = _section("drawer")
        assert 'className="rem-deliver"' in drawer
        assert drawer.count("<select") == 3, "抽屉里多了一个 select（只允许 来源/周几/到点之后）"
        block = drawer[drawer.index('className="rem-deliver"') :]
        assert "<select" not in block[:400]

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

