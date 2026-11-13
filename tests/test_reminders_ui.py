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
CORN = ROOT / "frontend" / "src" / "components" / "CornIcon.tsx"

# page.tsx 用 `// ==================== 段落 ====================` 划分，按段落取源码，
# 免得断言「卡片里没有暂停按钮」时连抽屉里的字段一起算进来。
#
# 2026-11-10 Phase 0/1 之后段落变细了：待办卡片与联动提醒卡片各占一段、
# 两个抽屉也各占一段。这里取的是**断言真正关心的那段**，不是最靠前的那段。
SECTIONS = {
    "axis": ("// ==================== 今天的时间轴", "// ==================== 推送健康条"),
    "card": ("// ==================== 联动提醒卡片", "// ==================== 送达记录"),
    "drawer": ("// ==================== 提醒抽屉（新建 / 修改）", "// ==================== 删除确认"),
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
        """带子要读成一块「挖进纸里的面」，不是一条淡色条。

        第一版写的是 step-1 → step-2（比卡片还亮），拍出来几乎看不见，只剩 24 个
        数字浮在空白上。第二版只有一道内阴影、底色仅比卡片暗 16/255，1x 下眯眼
        就散成一条淡带 —— 评审原话「简陋感剩下的根因不是厚度不够，而是带子与
        容器没有明度台阶」。

        现在靠四件事同时成立：底色在 step-0 上再压 6% 暖墨、一圈 1px 暗边给轮廓、
        上缘内阴影、下缘纸色高光当「唇」（挖出来的槽上暗下亮）。任何一条掉回
        「只有一道阴影」，这块面就又散了。
        """
        body = _css_block(CONSOLE_CSS.read_text(encoding="utf-8"), ".rem-tape-body {")
        assert "background-color: var(--step-0);" in body, "带子又和卡片同色了"
        assert "rgba(61, 47, 26, 0.06)" in body, "底色那层暖墨压深没了，明度台阶不够"
        assert body.count("inset 0") >= 3, "凹槽的三层（暗边/上缘影/下唇）缺了层"
        assert "inset 0 -1px 0" in body, "下缘那道「唇」没了，槽就只剩上暗、读成阴影"

    def test_ticks_are_a_ruler(self):
        """整点短划只贴带子下缘，6 小时那道才贯穿。

        24 道都贯穿的话，18px 高的带子会变成条形码 —— 那是把「有刻度」和「有节奏」
        搞混了。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        tick = _css_block(css, ".rem-tick {")
        assert "height: 7px;" in tick, "整点刻线又贯穿全高了"
        assert "bottom: 0;" in tick
        assert "rgba(61, 47, 26, 0.34)" in tick, "短划退回看不见的那一档"
        major = _css_block(css, '.rem-tick[data-major="1"] {')
        assert "height: 100%;" in major, "6 小时的主刻度不再是贯穿的那一道"
        # 主刻度必须比短划「更重」，不能只靠高度差：1x 下高度差读不出来，
        # 只剩带子下缘的一点灰噪（评审实测两档 1.38:1 / 1.45:1，几乎一样）
        assert "width: 1.5px;" in major, "主刻度没加重量"
        assert "rgba(61, 47, 26, 0.42)" in major

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

        2026-11-10 起合并搬到了页面的 `axisEvents` 里（TodayAxis 只负责画，
        待办与联动提醒要在同一张轴上排，就得在拿到两类数据的地方合）。
        写法也从「取 Map 槽位再原地 push」换成「先摊平排序、再和上一枚比 min」——
        原地 push 会被 react-hooks/immutability 判成改写外部值
        （`r.title` 一进闭包就报 "value cannot be modified"）。
        """
        page = _page()
        assert "seeds].sort(" in page, "待办与联动提醒没有先按时间排成一列"
        assert "last.min === min" in page, "同一分钟又被拆成两枚齿孔了"

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
        assert "inset 0 2px" in dark, "深色的凹陷感全丢了"
        # 受光方向必须和浅色一致：上暗、下亮。整圈亮边框会把顶边翻得比基底还亮，
        # 深色里那块就读成「发光的凸条」（实测顶边 57 / 基底 50，方向和浅色正相反）
        assert "inset 0 0 0 1px" not in dark, "又给深色加了一圈亮边框，顶边会反过来发亮"
        assert "inset 0 -1px 0" in dark, "深色少了底下那道「唇」"

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

    def test_axis_card_is_not_a_big_empty_box(self):
        """轴卡的高度要和它装的东西相称。

        2 倍实拍里量出来的是：卡 117px、带子 22px（19%）、带子上方 58px 空档 ——
        那 58px 只为一枚 52px 宽的胶囊让路，横贯 698px 全是白。下面是 53px 一张的
        提醒卡，轴卡是它的 2.2 倍高、信息量却是全页最少。评审原话「一个大盒子里
        一条细带」「整页最大的白装的是最细的内容」。

        现在容器收到 60px、轴卡用更紧的内边距（8/6，通用 .card-pad 是 16），
        卡片总高约 74px。这条钉的是：容器别再涨回去、内边距别再退回通用值、
        也别再有人把 card-pad 写回这张卡上。
        """
        page = _page()
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        tape = _css_block(css, ".rem-tape {")
        assert "height: 60px;" in tape, "轴的容器又涨回去了，卡里会重新出现大片空档"
        axis_card = _css_block(css, ".rem-axiscard {")
        assert "padding: 8px 18px 6px;" in axis_card, "轴卡又用回 16px 的通用内边距"
        assert 'className="card rem-axiscard"' in page, "轴卡换回 card-pad 了"

    def test_now_line_hides_behind_events(self):
        """游标要压在事件点**下面**。

        14:47 和 15:00 只差 13 分钟（桌面 8.7px），一根 1.5px 金线从 15:00 的圆点旁边
        擦过去时，两者会连成一个形 —— 评审实测净空只有 2px，读成「现在就是 15:00 那个点」。
        压到事件层之下后，线读成「从圆点背后穿过去」，两者仍是两个物件。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        # 前面还有 `.rem-tape:has(.rem-ev:hover) .rem-now {}`，不带换行会先命中它，
        # 拿到的是 `opacity: 0` 那一小段 —— 断言落在空串上比报错更难查
        now = _css_block(css, "\n.rem-now {")
        ev = _css_block(css, "\n.rem-ev {")
        assert "z-index: 1;" in now
        assert "z-index: 2;" in ev, "事件点不在游标之上"
        # 线只画在带子里：不再往上探出那截悬空的金色小棍
        assert "top: 22px;" in now and "bottom: 16px;" in now

    def test_dark_quiet_hatch_is_darker(self):
        """深色下斜纹也必须是「更暗」的那种线，和浅色同一个语义。

        暖墨 alpha 在近黑底上等于没画（0.13 叠上去只差 0.8/255），实拍里静默段
        整块消失；而如果翻成米白亮线，深浅两个主题里「斜纹」的意思就反了。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        dark = _css_block(css, ".dark .rem-tape-quiet {")
        assert "rgba(0, 0, 0, 0.28)" in dark, "深色斜纹又变成亮线了"
        assert "border-color: rgba(237, 224, 196" in dark, "区界边线在近黑底上看不见"

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
    """抽屉按用户能理解的顺序：谁的提醒 → 什么时候 → 提醒时做什么。"""

    def test_fields_follow_spec_order(self):
        drawer = _section("drawer")
        order = [
            drawer.index("提醒你什么"),
            drawer.index("由哪个功能安排"),
            drawer.index("什么时候提醒你"),
            drawer.index("提醒时做什么"),
            drawer.index("提醒方式"),
        ]
        assert order == sorted(order), "抽屉字段顺序又和规格不一致了"

    def test_delivery_block_is_not_a_control(self):
        """「提醒方式」是说明块，不是下拉框 —— 否则看着又能选又不会生效。

        断言数整个抽屉的 select：只数「提醒方式」之后的那 500 字抓不到
        「把控件插在标题前面」这种写法（反证时试出来的）。
        """
        drawer = _section("drawer")
        assert 'className="rem-deliver"' in drawer
        assert drawer.count("<select") == 3, "抽屉里多了一个 select（只允许 功能/周几/提醒动作）"
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
        """三个零不提供信息：空分组整段不渲染。

        变量名从 list 改成 children（Phase 1 的分组要同时装待办与联动提醒，
        叫 list 会和「清单 list_key」撞名），语义不变：长度 0 就整段不渲染。
        """
        page = _page()
        assert "if (children.length === 0) return null;" in page


class TestUserFacingCopy:
    """界面只说「提醒 / 通知」，不把内部术语漏给用户。

    这组断言看的是去掉注释后的源码：注释里可以写「触发窗口」，
    JSX、showToast、aria-label、placeholder 里一个都不许有。
    """

    # 「影响」里也有一个「响」，不能按裸字判；只看单独表示"响铃/发声"的用法。
    INTERNAL_PATTERNS = (
        r"(?<!影)响",
        r"推送",
        r"静默",
        r"送达",
        r"来源",
        r"日程",
        r"触发",
        r"后端",
        r"服务端",
    )

    def test_no_internal_words_in_user_facing_source(self):
        for name, src in (("页面", _page()), ("一句话添加抽屉", _capture())):
            for pattern in self.INTERNAL_PATTERNS:
                m = re.search(pattern, src)
                assert m is None, f"{name}里还有内部术语「{m.group(0) if m else pattern}」"

    def test_key_copy_stays_in_plain_chinese(self):
        page = _page()
        capture = _capture()
        assert "设了提醒时间，到时会通知你" in page
        assert "只设截止时间，不会收到提醒" in page
        assert "免打扰时间" in page
        assert "提醒记录" in page
        assert "补上时间" in capture
        assert "设好后才会提醒你" in capture


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


class TestPhase0Contracts:
    """Phase 0 的五件事，全部写成**不该出现**的断言。

    正着断言「健康条出现了」会把「任何状态都显示健康条」判成绿的，
    所以这里反过来钉：正常态必须 `return null`、失败态不许报警、
    已触发的一次性提醒不许混进「接下来」、删除取消不许改动任何东西。
    """

    def test_health_bar_hides_itself_when_push_is_healthy(self):
        """推送正常 → 整块不渲染。顶部常年挂一条「一切正常」等于没信息。"""
        page = _page()
        assert "if (status.supported && status.subscribed) return null;" in page

    def test_health_bar_says_nothing_when_the_endpoint_fails(self):
        """`/api/push/status` 挂了 → 返回 null → 整块消失。

        提醒页顶上报一个「推送状态获取失败」，用户既看不懂也修不了。
        """
        page = _page()
        assert "if (!status) return null;" in page

    def test_fire_diary_has_a_zero_state(self):
        """一条流水都没有时说「还没有提醒过」，不要留一个空白的 <ul>。"""
        page = _page()
        assert "还没有提醒过。" in page

    def test_missed_is_not_rendered_as_failed(self):
        """`failed` + `missed:` 是「错过窗口」，不是「发送失败」。

        两者的处置完全不同（前者改设备/网络，后者看服务端），
        合并成一句「发送失败」等于把排查方向指错。
        """
        page = _page()
        assert 'startsWith("missed:")' in page
        assert '"没提醒 · 当时设备不在线"' in page
        assert '"发送失败"' not in _strip_comments(page), "错过窗口不该写成发送失败"

    def test_fired_once_never_shows_up_in_upcoming(self):
        """freq=once 且 next_fire_at 为空 = 已经响过，不会再响。

        它如果进了「接下来」，那一组每一条的承诺（还会再来一次）就是假的。
        """
        page = _page()
        assert 'i.freq === "once" && !i.next_fire_at' in page

    def test_delete_cancel_changes_nothing(self):
        """取消只把 pending 置空：不发请求、不乐观删除、不留假撤销。"""
        page = _page()
        assert "onCancel={() => setPending(null)}" in page
        assert "撤销" not in page, "又做了假撤销：后端没有 restore / soft-delete"

    def test_delete_confirm_says_it_is_permanent(self):
        """确认框必须写明不可恢复，否则「二次确认」只是多一次点击。"""
        page = _page()
        assert "删除后不能恢复" in page


class TestAxisCarriesInformation:
    """时间轴要能被读出信息，而不只是"有 6 个点"（上一轮评审的三条）。"""

    def test_past_and_future_are_distinguishable(self):
        """已过 / 未到必须看得出差别。

        全都画成一样的墨点，用户分不清"9 点那条已经响过了"和"晚上 9 点还没到"。
        用同一个墨色的透明度差，不引入第二种颜色 —— 颜色在这条轴上归"紧急"。
        """
        page = _page()
        assert 'data-past={delta !== null && delta < 0 ? "1" : "0"}' in page
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        block = _css_block(css, '.rem-ev[data-past="1"] i {')
        assert "opacity: 0.4" in block, "已过去的齿孔又和未到的一样重了"

    def test_merged_events_look_heavier(self):
        """同一分钟并了多条要看得出来 —— 手机上没法悬停，`+2` 永远看不见。"""
        page = _page()
        assert 'data-many={e.count > 1 ? "1" : "0"}' in page
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert '.rem-ev[data-many="1"] i {' in css

    def test_hover_bubble_says_how_far(self):
        """齿孔 11px 放不下字，但"还有多久"是扫轴最想知道的，挂在气泡里。"""
        page = _page()
        assert "fmtDelta(delta)" in page
        assert 'className="rem-ev-delta"' in page
        # 三种说法都要有：还有 / 已经过 / 就是现在
        assert "还有 ${span}" in page
        assert "已过 ${span}" in page
        assert '"就是现在"' in page

    def test_quiet_band_has_a_legend(self):
        """斜纹没有图例就只是一块来路不明的底纹。"""
        page = _page()
        assert 'className="rem-tape-legend"' in page
        assert "免打扰 {quietStart}–{quietEnd}" in page
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert ".rem-legend-hatch {" in css
        # 深色下必须换一套斜纹，否则暖墨 alpha 在近黑上等于没画
        assert ".dark .rem-legend-hatch {" in css

    def test_ticks_inside_quiet_are_muted(self):
        """整点线穿过静默段会被读成"轴断了"，压淡它。"""
        page = _page()
        assert 'data-quiet={inQuiet(quiet, h * TICK_MIN) ? "1" : "0"}' in page
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        block = _css_block(css, '.rem-tick[data-quiet="1"] {')
        assert "rgba(61, 47, 26, 0.11)" in block

    def test_legend_sits_outside_the_tape(self):
        """图例不许撑高轴本身：`.rem-tape` 的 60px 是量过的，涨了卡里就出现空档。"""
        page = _page()
        tape = page[page.index('className="rem-tape"') : page.index('className="rem-tape-legend"')]
        # 图例出现在 .rem-tape 的闭合之后 —— 中间必然夹着 wrap 的闭合
        assert tape.count("</div>") >= 2, "图例被塞进 .rem-tape 里了"
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "height: 60px;" in _css_block(css, ".rem-tape {")


class TestSortAndBulk:
    """排序与批量（2026-11-10 第二波）的四条硬约束。

    这一组断言大部分是**负向**的：拖拽和批量最容易出的错不是"画得不好看"，
    而是「一次拖动发 N 个请求」「批量删不确认」「多选和完成混在同一格」——
    这些错误在截图里全看不见。
    """

    def test_one_mode_at_a_time(self):
        """整页只维护一个 `mode`，不是两个布尔量。

        两个开关可以同时为 true（多选 + 排序）——那时「点一下」到底算选、算拖
        还是算完成，界面自己都说不清。
        """
        page = _page()
        assert 'useState<"normal" | "select" | "sort">("normal")' in page
        assert "const [pickMode" not in page
        assert "const [sortMode" not in page
        # 退出时必须三样一起清：留着 sortIds 会让下一次进排序从旧顺序开始
        exit_block = page[page.index("const exitMode") : page.index("const beginPick")]
        for line in ('setMode("normal")', "setPicked([])", "setSortIds(null)", "setDragId(null)"):
            assert line in exit_block, f"exitMode 漏了 {line}"

    def test_changing_tab_exits_the_mode(self):
        """换 tab 必须退出：选中的 id 属于上一个列表，带着它们去新列表
        等于对看不见的行执行操作。"""
        page = _page()
        assert "setScope(o.value);" in page
        seg = page[page.index("setScope(o.value);") :][:200]
        assert "exitMode();" in seg, "换 tab 没退出多选/排序"
        # 注释被 _page() 剥掉了，理由说明要回原文里找
        assert "属于上一个列表" in PAGE.read_text(encoding="utf-8")

    def test_pick_reuses_the_completion_checkbox(self):
        """选择模式**不新增**第二个 checkbox：同一格换形状（方 = 选中，圆 = 完成）。

        一行里两个 checkbox，用户永远分不清哪个是完成、哪个是选中。
        """
        page = _page()
        assert page.count('className="rem-check"') == 1, "又多了一个 checkbox"
        assert "const checked = pick ? picked : done;" in page
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert '.rem-task[data-pick="1"] .rem-check > span {' in css

    def test_drag_is_pointer_not_html5(self):
        """用 pointer 事件而不是 `draggable`：后者在手机浏览器上不触发，
        而这个页面的主视口是 390。"""
        page = _page()
        assert "setPointerCapture" in page
        assert "document.elementFromPoint" in page
        assert "draggable" not in page, "又用回 HTML5 拖放了，手机上是死的"
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        block = _css_block(css, ".rem-grip {")
        assert "touch-action: none" in block, "少了它手指一按就滚页面，pointermove 不来"

    def test_sort_saves_in_one_request(self):
        """拖完只发一次 reorder，不逐条 PATCH。

        逐条发的话中途失败会留下半新半旧的顺序，而且 20 条就是 20 个往返。
        """
        page = _page()
        assert "api.tasks.reorder(ids)" in page
        save = page[page.index("const saveSort") : page.index("const runBulk")]
        assert "api.tasks.patch" not in save, "排序又在逐条改"

    def test_noop_save_does_not_write(self):
        """没动就不发请求：一次「什么都没改」的保存不该在服务端留 updated_at。"""
        page = _page()
        save = page[page.index("const saveSort") : page.index("const runBulk")]
        assert 'ids.join("|") === before.join("|")' in save

    def test_failed_save_keeps_the_dragged_order(self):
        """保存失败要留在排序模式。

        直接退出等于把用户拖了半天的顺序丢掉，而他看到的是一句 toast ——
        再进来时顺序已经回到原样，会以为是「保存按钮坏了」。
        """
        page = _page()
        save = page[page.index("const saveSort") : page.index("const runBulk")]
        tail = save[save.index("} catch {") :]
        assert 'setMode("normal")' not in tail, "失败后还是退出了排序模式"
        assert "再点一次" in tail

    def test_bulk_delete_still_confirms(self):
        """批量删除照样二次确认：批量更容易手滑，不因为「多选」就免掉。"""
        page = _page()
        assert 'if (action === "delete") {' in page
        assert 'setPending({ kind: "bulk", ids: picked })' in page
        assert "delete 选中的 ${pending.ids.length} 条" not in page  # 模板串写法不同
        assert "删除选中的" in page and "pending.ids.length" in page

    def test_bulk_move_always_names_the_list(self):
        """`move` 不带 list_key 时后端会 400（不静默搬进收件箱）。
        前端必须真的把选中的清单发上去。"""
        page = _page()
        assert 'action === "move" ? bulkList : undefined' in page

    def test_bulkbar_only_appears_with_a_selection(self):
        """空着也挂一条底栏会一直盖住列表最后一行，而它此刻无事可做。"""
        page = _page()
        assert 'mode === "select" && picked.length > 0' in page

    def test_sort_mode_drops_the_unmovable_rows(self):
        """排序模式只留可排序的待办：日程提醒的时刻由来源决定，拖了不会动。

        2026-11-11 改版后页面上只剩一个 `scope ===` 分支（提醒区排除「已完成」），
        四个 tab 的内容由 `taskBlocks` / `remBlocks` 两张表决定。所以这条断言
        改成钉「三块东西都受 mode 管」—— 轴、待办区、提醒区，漏掉任何一块，
        进排序模式都会残留一堆拖不动的行。
        """
        page = _page()
        assert page.count('mode !== "sort"') >= 3, "轴/待办区/提醒区有一个没被挡住"
        # 排序模式下不显示「下一件」和快速添加：正在挑行时多一个输入框会抢焦点
        assert 'scope !== "completed" && mode === "normal"' in page


class TestTaskMarks:
    """待办左端的类型图标（2026-11-10）。

    这一组只钉两类错误，因为它们是"画面上看不出来"的：
      1) 两个清单共用一个图标 —— 每个图标单看都对，并排看也"挺整齐"，
         只有把六条排在一起才发现作业和考试长得一样；
      2) 图标名写错 —— `CornIcon` 对未知名字**静默回退**成玉米，
         于是页面照样有图、截图照样好看，只是全部变成了同一个。
    """

    def _list_mark(self) -> str:
        page = _page()
        i = page.index("const LIST_MARK")
        return page[i : page.index("};", i)]

    def test_every_list_has_its_own_mark(self):
        """六个清单六个不同图标。撞了就有一类永远认不出来。"""
        block = self._list_mark()
        pairs = re.findall(r"(\w+):\s*\"([a-z]+)\"", block)
        assert [k for k, _ in pairs] == ["inbox", "course", "homework", "club", "life", "exam"]
        marks = [m for _, m in pairs]
        assert len(set(marks)) == 6, f"有清单共用图标：{marks}"

    def test_marks_exist_in_the_icon_set(self):
        """用到的名字必须在 `ICONS` 里，否则会静默变成玉米（页面不报错）。"""
        src = CORN.read_text(encoding="utf-8")
        i = src.index("const ICONS")
        known = set(re.findall(r"^\s*(\w+):", src[i : src.index("};", i)], re.M))
        assert "corn" in known, "兜底图标本身不在集合里，测试的前提就不成立"

        used = set(re.findall(r":\s*\"([a-z]+)\"", self._list_mark()))
        page = _page()
        hints = page[page.index("const MARK_HINTS") : page.index("function taskMark")]
        used |= set(re.findall(r",\s*\"([a-z]+)\"\]", hints))
        assert used <= known, f"这些名字不在 ICONS 里，会静默退回玉米：{sorted(used - known)}"

    def test_inbox_falls_back_to_the_title(self):
        """清单是权威，只有收件箱才按标题猜。反了会跟 meta 里的清单名打架。"""
        page = _page()
        i = page.index("function taskMark")
        seg = page[i : i + 260]
        assert 'if (task.list_key !== "inbox") return LIST_MARK[task.list_key]' in seg

    def test_mark_is_a_button_but_list_name_stays(self):
        """图标从装饰升级成按钮（2026-11-11）：读屏要念得出「改图标和等级」，
        同时清单名仍留在 meta 里 —— 图标变得可点，不代表可以拿掉那句文字。"""
        page = _page()
        i = page.index('className="rem-head"')
        head = page[i : i + 900]
        assert 'className="rem-markbtn"' in head
        assert "aria-label={`改「${task.title}」的图标和等级`}" in head
        assert 'aria-hidden="true"' not in head, "可点的按钮不能再对读屏隐身"
        assert "<b>{LIST_LABEL[task.list_key] ?? task.list_key}</b>" in page

    def test_mark_sits_between_checkbox_and_title(self):
        """位置固定在 checkbox 之后、标题之前：不占那格 44px 的完成热区。"""
        page = _page()
        check = page.index('className="rem-check"')
        mark = page.index('className="rem-markbtn"', check)
        title = page.index('className="rem-title"', mark)
        assert check < mark < title

    def test_mark_never_squeezes_the_title(self):
        """图标占位必须从标题里"借"，但不能把标题压到没有省略号。"""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "width: 20px" in _css_block(css, ".rem-mark {")
        assert "min-width: 0" in _css_block(css, ".rem-head {")
        title = _css_block(css, ".rem-head .rem-title {")
        assert "flex: 1" in title and "min-width: 0" in title
        # 标题自己仍然要能省略，否则窄屏会顶出卡片
        # （锚行首：`.rem-head .rem-title` 里也含 `.rem-title {`，不锚会取错块）
        assert "text-overflow: ellipsis" in _css_block(css, "\n.rem-title {")

    def test_linked_reminder_gets_no_list_mark(self):
        """联动提醒的时刻由来源决定，它不属于任何清单 —— 硬套图标就是编造。"""
        page = _page()
        i = page.index('className="rem-next-mark"')
        assert "isTask ? (" in page[max(0, i - 260) : i]
        # 三个挂载点：待办卡、排序行、下一件（且下一件是条件渲染）
        assert page.count("<CornIcon name={taskMark(") == 3

    def test_drawer_picker_follows_the_selection(self):
        """抽屉里的图标跟着下拉框走：写死一个名字等于选什么都显示同一类。"""
        page = _page()
        i = page.index('className="rem-listpick"')
        seg = page[i : i + 700]
        assert "data-mark={LIST_MARK[listKey]}" in seg
        assert "<CornIcon name={LIST_MARK[listKey]}" in seg

    def test_home_icon_is_registered(self):
        """生活清单用的新图标：类型、函数、注册表三处都要有。"""
        src = CORN.read_text(encoding="utf-8")
        assert '| "home"' in src
        assert "function Home()" in src
        assert re.search(r"^\s*home: Home,", src, re.M)
        # 描边必须跟主题走，否则深色下图标会变成一块黑色轮廓
        assert 'stroke="currentColor"' in src


CAPTURE = ROOT / "frontend" / "src" / "components" / "reminders" / "AiCaptureDrawer.tsx"
FIXTURE = ROOT / "frontend" / "src" / "components" / "reminders" / "captureFixture.ts"
A11Y_CSS = ROOT / "frontend" / "src" / "app" / "styles" / "a11y.css"


def _capture() -> str:
    return _strip_comments(CAPTURE.read_text(encoding="utf-8"))


def _jsx_element(src: str, opening: str) -> str:
    """截出以 opening 开头那个 JSX 元素（从开标签到配平的闭标签）。

    为什么不能只比 index 先后：v13 的第一版断言写的是
    `body_i < dock_i < comp_i`，反证时在 dock 前面插一个空
    `<div className="rem-cap-dock" />`，`src.index` 命中的是那个空 div，
    三个位置关系照样成立 —— 断言静默变绿，等于没写。
    要比的是**包含关系**：composer 是不是真的在这个 footer 的子树里。

    只配平同名标签，不解析属性（属性里的 `=>` 会骗过朴素的 `<[^>]*>`）。
    """
    tag = re.match(r"<([A-Za-z][\w.-]*)", opening).group(1)
    i = src.index(opening)
    depth = 0
    for m in re.finditer(r"<(/?)" + re.escape(tag) + r"\b", src[i:]):
        if m.group(1):
            depth -= 1
            if depth == 0:
                return src[i : i + m.start()]
        else:
            depth += 1
    raise AssertionError(f"{opening} 找不到配平的闭标签，层级无从判断")


def _dock_condition(src: str) -> str:
    """取 ③ 底栏那个条件表达式（`{status === "idle" || … ? (`）。

    为什么要单独切一段：`status === "parsing" ||` 在发送按钮的 disabled 里也出现一次，
    整文件搜字串的话，把整理中从底栏条件里删掉照样能搜到 → 断言静默变绿（踩过）。
    """
    i = src.index('{status === "idle" ||')
    return src[i : src.index("? (", i)]


class TestAiCaptureShell:
    """AI 整理抽屉（v12 · Phase A）的壳层契约。

    Phase A 还没有后端，能出错的地方全在「顺序」和「默认值」上：
    结果态是不是一打开就挂在 DOM 里、示例句会不会偷偷提交、
    勾选全取消之后主按钮还能不能点、语音没接 STT 会不会假装在听。
    这些都不是像素问题，端到端脚本能验一次，源码断言能保证明天还不会错。
    """

    def test_drawer_is_a_real_dialog(self):
        """抽屉要能被读屏当成模态：role=dialog + aria-modal + 标题挂 aria-labelledby。"""
        src = _capture()
        assert 'role="dialog"' in src
        assert 'aria-modal="true"' in src
        assert "aria-labelledby" in src

    def test_now_is_a_required_prop(self):
        """渲染期不许 Date.now()：否则纯函数组件每次渲染结果都可能不同。

        `now` 是必填 prop（页面已有每分钟走一次的 useNowMinute），
        一旦有人把它写成 `now?: number` 再 `?? Date.now()` 兜底，这条要红。
        """
        src = _capture()
        assert "now: number;" in src
        assert "Date.now()" not in src

    def test_result_state_is_mounted_conditionally(self):
        """输入态和结果态是二选一渲染，不是把结果态藏起来。

        藏起来的话：一次都没整理过的用户，Tab 键能摸到「加入 0 条」。
        """
        src = _capture()
        assert 'status === "ready"' in src
        assert '{status === "ready" ? (' in src

    def test_example_sentence_only_fills_the_box(self):
        """示例只填入、只聚焦 —— 不许顺手提交。

        这是设计稿最容易被实现错的一条：点了示例句立刻出结果，
        用户会以为自己刚才说了什么。
        """
        src = _capture()
        # 锚在示例卡上：追问选项用的是同一个 chip class，从 class 找会抓错块
        i = src.index('className="rem-cap-demo"')
        seg = src[i : src.index("rem-cap-demo-foot")]
        assert "onClick={fillDemo}" in seg
        assert "void runParse" not in seg, "示例卡不许直接触发整理"

    def test_join_button_follows_the_selection(self):
        """取消勾选要能一路取消到 0，且 0 条时既 disabled、commit 也直接返回。"""
        src = _capture()
        assert "disabled={pickedItems.length === 0 || committing}" in src
        assert "if (committingRef.current || pickedItems.length === 0) return;" in src

    def test_voice_entry_only_exists_when_asr_is_wired(self):
        """没接转写就不渲染麦克风。

        上一版是"按钮一直在，点了弹『语音输入还没接』"—— 用户看到的不是
        "这里有个功能"，而是"这个产品没做完"。现在入口由后端 capabilities 决定：
        没配密钥就根本不出现，配了才出现，出现就真能录。

        同时钉住"不假装在听"：源码里不许有那种点了没反应却写「正在听…」的假状态。
        """
        src = _capture()
        assert "正在听…" not in src, "不许出现伪造的『正在听』"
        assert "{voiceOn ? (" in src, "麦克风必须挂在 voiceOn 后面，不能无条件渲染"
        assert ".capabilities()" in src, "有没有接语音要问后端，不能前端写死"
        assert "{status === \"parsing\" || voice.phase !== \"idle\"}" in src, (
            "录音中必须锁住输入框：转写结果是追加进去的，边打边说会对不上"
        )

    def test_fixture_never_invents_a_reminder(self):
        """只有原话里说了「提醒我」才给 remind_at：只设截止的待办不会响。

        光断言「有这个变量名」是摆设（改名成 wantsRemindX 照样是子串）——
        要钉的是**门**：remind_at 必须挂在 ASK_WORDS 这个条件后面。
        """
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        assert re.search(
            r'const wantsRemind = kind === "task" && ASK_WORDS\.test\(clause\);', fx
        ), "remind_at 的开关不见了：又会在没让它提醒的时候自己响"
        assert "remind_at: wantsRemind ? when.remindAt : null," in fx

    def test_input_box_has_no_second_outline(self):
        """输入框的聚焦描边由外层那张纸承担，a11y.css 里必须留了例外。

        漏了这条就会重演「金边框 + 2px 描边 = 套两个框」（auth 输入框踩过同款）。
        注：外层从 `.rem-cap-composer`（贴底胶囊）改名为 `.rem-cap-pad`（纸）后，
        这里跟着改；旧名字不许再出现，否则等于有两条规则并存。
        """
        a11y = A11Y_CSS.read_text(encoding="utf-8")
        assert ".rem-cap-pad textarea:focus-visible" in a11y
        # 旧选择器不许再留一份（注释里提到类名不算，这里钉的是选择器本身）
        assert ".rem-cap-composer input:focus-visible" not in a11y
        sub = a11y[a11y.index(".rem-cap-pad textarea:focus-visible") :]
        assert "outline: none" in sub[: sub.index("}")]

    def test_small_print_is_not_typeset_at_badge_size(self):
        """底栏汇总句是给人读的：不许借 --fs-badge（10px）。"""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "font-size: var(--fs-xs)" in _css_block(css, ".rem-cap-summary {")

    def test_checked_diamond_stays_visible_in_dark(self):
        """深色里 --accent-ink 本身就是金：金圆上的菱形必须换深墨。"""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "--cap-on-accent: #22190a;" in _css_block(css, ".rem-cap-drawer {")
        # 菱形不再靠一个额外的 span，而是把方块转 45°；选中后必须还是实心菱形
        assert "transform: rotate(45deg)" in _css_block(
            css, '.rem-cap-node[data-kind="reminder"] {'
        )
        assert "background: var(--accent)" in _css_block(
            css, '.rem-cap-node[data-kind="reminder"][data-on="1"] {'
        )


class TestCaptureIsAToolNotAChat:
    """v13：抽屉是工具，不是聊天（2026-11-10 第三次改版，用户打回"中间太空"之后）。

    第二版把输入做成"占满中部的纸"：纸上下各空 200px，示例句和信任说明被挤进纸里，
    底部没有任何东西承重视线，整屏读起来像一张空表格。v13 把承重的换成**贴底输入栏**：

        ① .rem-cap-top   固定不滚：步骤条 / 标题 / 一句说明 / 关闭
        ② .rem-cap-body  flex:1 + overflow-y:auto：结果再多也只滚这一块
        ③ .rem-cap-dock  贴底：输入栏永远在同一位置

    这几条钉的是"三段关系"，不是像素 —— 像素会改，关系改了就退回一张纸。
    """

    def test_three_segments_are_the_skeleton(self):
        src = _capture()
        assert 'className="rem-cap-top"' in src
        assert 'className="rem-cap-body"' in src
        assert 'className="rem-cap-dock"' in src

    def test_composer_lives_in_the_dock_not_in_the_scrolling_area(self):
        """输入框必须在 dock 这个 footer 的子树里，不能落进滚动区。

        放进滚动区的话，用户滚到结果一半就找不到输入框了 —— 而且"贴底"这件事
        是靠 flex 撑出来的，不是靠 position。

        判据是**包含关系**（composer 落在 dock 子树内），不是三段 index 的先后：
        前后关系挡不住"多插一个同名空节点"这种改法（上一版就是这么漏的）。
        """
        src = _capture()
        dock_open = src.index('<footer className="rem-cap-dock">')
        dock = _jsx_element(src, '<footer className="rem-cap-dock">')
        assert 'className="rem-cap-composer"' in dock, (
            "composer 必须在 dock 子树里；挪到滚动区用户就找不到输入框了"
        )
        # 反过来钉住：dock 是 body 之后的那一段，不能整个被搬进滚动区
        assert src.index('<div className="rem-cap-body"') < dock_open

    def test_css_pins_the_dock_and_lets_only_the_body_scroll(self):
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        body = _css_block(css, ".rem-cap-body {")
        assert "flex: 1 1 auto" in body
        assert "overflow-y: auto" in body
        dock = _css_block(css, ".rem-cap-dock {")
        assert "flex: 0 0 auto" in dock
        drawer = _css_block(css, ".rem-cap-drawer {")
        assert "flex-direction: column" in drawer

    def test_no_step_wizard_and_no_tutorial_block(self):
        """11 日撤掉顶部步骤条和输入态的教程块。

        第一版按验收文档挂了 `1 说一句 → 2 确认 → 3 加入`，输入态还堆了
        「说清什么时候、要做什么」+ 例句 + 等待区虚线 + 「说错也没关系」。
        每一条单看都有道理，摞起来像没做完的产品在教用户用。

        反证：把任一段 DOM 粘回 `_capture()`，这条必须红。
        """
        src = _capture()
        for dead in [
            "rem-cap-steps",
            "rem-cap-steparrow",
            "rem-cap-guide",
            "试试这样说",
            "说错也没关系",
        ]:
            assert dead not in src, f"引导性元素又回来了：{dead}"

    def test_no_first_person_narration(self):
        """去 AI 味 P0：抽屉里不许出现"我听到 / 我先把 / 我在整理"。"""
        src = _capture()
        for bad in ["我听到", "我先把", "我在整理", "我拿不准"]:
            assert bad not in src, f"抽屉里还有第一人称旁白：{bad}"

    def test_no_avatar_or_greeting_bubble(self):
        src = _capture()
        assert "chat-avatar" not in src
        assert "rem-cap-bubble" not in src

    def test_dock_no_longer_carries_a_second_row_of_examples(self):
        """示例句从底栏搬进了空态卡片，底栏不再挂第二排例子。

        原来屏幕上有三处在举例：演示卡、底栏 chips、输入框 placeholder。
        chips 是最吃亏的那个 —— 四个短语挤一行，每个被压到八来个字，
        既读不全，又把输入栏顶高了一截。示例卡本身现在可点，
        「一键试一句」的能力没有丢，丢的只是重复。
        """
        src = _capture()
        assert "rem-cap-chips--dock" not in src, "底栏的第二排例子又回来了"
        assert "CAPTURE_EXAMPLES" not in src, "这份数据已经没人用了，别留在这儿当诱饵"

    def test_input_is_reachable_from_failure_and_zero(self):
        """失败态和 0 条都要给同一个输入框。

        不给的话，用户就被卡在一个"没有出口"的页面里 —— 而这两种状态的下一步
        本来就是"再写一句"，回退到输入态反而多一次点击。
        """
        src = _capture()
        dock = _dock_condition(src)
        assert 'status === "failed"' in dock
        assert 'items.length === 0' in dock

    def test_composer_stays_put_while_parsing(self):
        """整理中也要留着输入栏，只是锁住。

        撤掉的话下半屏会空出一大块、输入框还跳了位置 —— 这正是"上下两边太空"的来源。
        v13 的第一版就漏了这一段：整理中整条 dock 不渲染，截图里 60% 是空的。
        """
        src = _capture()
        assert 'status === "parsing"' in _dock_condition(src), (
            "整理中必须和 idle / 失败 / 0 条共用同一个 composer"
        )
        assert "voice.phase !== \"idle\"" in src, "录音/听写中光标还能动会骗人"

    def test_reminderless_task_explains_itself_in_place(self):
        """「不提醒」必须点得开，而且只解释自己那一行。

        上一版是整屏一行黄字（`.rem-cap-warnline`）：用户读完还是不知道
        「不提醒」指什么 —— 连写这行提示的人都把它记成了「只退出不完成」。
        疑问在哪产生，答案就在哪展开。
        """
        src = _capture()
        assert "rem-cap-warnline" not in src, "旧的整屏黄字提示该撤掉了"
        assert "aria-expanded={openNrmd === item.id}" in src
        assert "aria-controls={`rem-cap-nrmd-${item.id}`}" in src
        assert 'className="rem-cap-nrmd-panel"' in src
        # 说明句用大白话：直接说加入后不会提醒、去哪能补
        assert "加入后不会提醒你" in src
        assert "在列表里打开它" in src

    def test_noremind_hit_area_reaches_44(self):
        """视觉 20px 的文字按钮，热区必须补到 44（手机）。"""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        hit = _css_block(css, ".rem-cap-nrmd-btn::before")
        assert "inset: -12px -8px" in hit, "20 + 12×2 = 44，改数字就得重新算"


class TestCaptureTalksToTheBackend:
    """Phase B：解析走真实接口，fixture 只在**离线验收**时活着。

    这一组守的不是"接没接上"，是"假结果会不会跑到真实用户面前" ——
    一个能骗过用户的结果，比一次 502 严重得多。
    """

    def test_parse_goes_through_the_api(self):
        src = _capture()
        assert "api.capture.parse(" in src, "解析必须走 POST /api/capture/parse"
        assert "if (OFFLINE_PARSE) {" in src, "离线分支要单独成块，别和真实分支搅在一起"

    def test_offline_fixture_cannot_reach_production(self):
        """假结果的开关必须被 NODE_ENV 兜死。

        `next build` 里 `process.env.NODE_ENV !== "production"` 恒为 false，
        整支 fixture 会被当成死代码消掉。把这一句删了，环境变量一旦漏配，
        用户看到的就是一份"整理得很好、但其实没调用模型"的假结果。
        """
        src = _capture()
        i = src.index("const OFFLINE_PARSE")
        gate = src[i : i + 240]
        assert 'process.env.NODE_ENV !== "production"' in gate, "缺 NODE_ENV 兜底"
        assert 'process.env.NEXT_PUBLIC_CAPTURE_OFFLINE === "1"' in gate, "缺显式开关"

    def test_no_internal_schedule_words_on_screen(self):
        """界面上不许出现「Phase A / 写库在 C 阶段」这类词。

        这是本轮最贵的一条教训：用户原话是"连我自己都不懂"。
        排期是内部的事，用户只关心"我点了会发生什么"。
        """
        for name, src in (("抽屉", _capture()), ("列表页", _page())):
            for bad in ("Phase A", "Phase B", "Phase C", "C 阶段"):
                assert bad not in src, f"{name}上还写着内部排期词：{bad}"

    def test_three_failures_have_three_next_steps(self):
        """429 要等、502 要换说法、断网要重试 —— 三种失败不能共用一句话。

        只断言 "err?.status === 502" 出现过是不够的：把两种失败改成同一句话
        照样能过（反证抓到的假绿）。所以取到那一行，再看它给的下一步是什么。
        """
        src = _capture()
        i429 = src.index("err?.status === 429")
        i502 = src.index("err?.status === 502")
        line429 = src[i429 : src.index("\n", i429)]
        line502 = src[i502 : src.index("\n", i502)]
        assert "有点多" in line429, "429 是「现在人多」，要让用户等一下"
        assert "换个说法" in line502, "502 是模型没读懂，要让用户换说法"
        assert "换个说法" not in line429, "两种失败别共用一句话"
        assert "rem-cap-fail-why" in src, "失败原因要显示出来，不能只说「没整理出来」"

    def test_offline_demo_fail_goes_through_the_same_translation(self):
        """离线演示的失败态必须过真机那条翻译 —— 否则截图会替自己背书。

        上一版 demo 分支直接 `setStatus("failed")`、没设原因：截图里的失败态只有
        「这次没整理出来」，而真机上新加了「换个说法」。截图全绿，真机才看得见差别。
        现在让假异常接着走 `parseFailMessage`，两边的字就不可能分叉。
        """
        src = _capture()
        i = src.index("if (DEMO_FAIL_CMDS.includes(demoCmd))")
        branch = src[i : i + 400]
        assert "setFailMsg(parseFailMessage(demoError(demoCmd)))" in branch, (
            "演示失败也要给原因，而且必须走真机同一个翻译函数"
        )
        assert "这次没读懂" not in branch, "原因文案只有一处来源，不许在演示分支里另写一句"

    def test_demo_can_reproduce_all_three_failures(self):
        """三种失败都要能在离线复现，不然只有 502 有截图、429 只能靠真机。

        这里必须查 DEMO_FAIL_CMDS 这一行，不能只查「fail429 出现过」：
        `demoError()` 里也有 `cmd === "fail429"`，从清单里删掉照样能过（反证抓到的假绿）。
        """
        src = _capture()
        i = src.index("const DEMO_FAIL_CMDS = ")
        line = src[i : src.index(";", i)]
        for cmd in ('"fail"', '"fail429"', '"failnet"'):
            assert cmd in line, f"离线演示复现不了 {cmd}"
        assert '{ status: 429' in src, "429 要带 status，翻译函数按 status 分岔"


class TestCaptureAxis:
    """结果态是**一条按时间排好的轴**（2026-11-10 晚，按 TimeMind 的形态重做）。

    用户的原话是"说话颠三倒四，它按时间给我排好了"。这句话里能验的只有一个字：
    **序**。第一版按天分组的卡片墙做不到 —— 重复项被踢进"每天/每周X"分组，
    等于把最该按时间插进去的东西挪出了时间线。
    """

    def test_result_is_one_axis_not_day_groups(self):
        """一张轴，不是按天分组的卡片墙。分组标题一回来，序就散了。"""
        src = _capture()
        assert 'className="rem-cap-axis"' in src
        assert "rem-cap-day" not in src
        assert "rem-cap-item" not in src

    def test_rows_carry_their_sort_key(self):
        """排序键要落在 DOM 上（data-at），否则"有没有排"只能靠肉眼。"""
        src = _capture()
        assert "data-at={slot.at}" in src
        assert ".sort((a, b) => a.slot.at - b.slot.at)" in src

    def test_repeats_are_sorted_by_their_next_occurrence(self):
        """每周三的瑜伽按"下一个周三"插进轴里，不是排到最后。

        这条是整个重做的命门：只看 `due_at` 的话，重复项没有 due_at，
        会被统一丢到最后，用户看到的就还是一堆分组。
        """
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        assert "export function nextOccurrenceAt(" in fx
        assert 'if (rec.freq === "weekly" && w === (rec.weekday ?? 1)) return d.getTime();' in fx

    def test_reminder_time_also_comes_from_due_at(self):
        """一次性日程的时间在 `due_at` 里，不只在 `recurrence.fire_at`。

        2026-11-11 真实对话抓到的：后端对「下周二中午跟导师吃饭」返回的是
        `recurrence: null` + `due_at: 1794888000`。而这里当时只读 `rec?.fire_at`，
        fire 恒为 null → 那一行显示「待定」；可同一行右侧又挂着「可直接加入」，
        因为 isWritable 走的是 reminderDraftOf，那边有 due_at 兜底。
        同一个界面里两套判据打架，用户看到的是"时间待定但可以直接加"。

        离线 fixture 造不出这个形状（它给 reminder 的 due_at 恒为 null，
        时间挂在 recurrence.fire_at 上），所以 188 条离线断言**永远测不到**。
        这条静态断言补的就是那个洞。
        """
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        i = fx.index("export function nextOccurrenceAt(")
        seg = fx[i : i + 3000]
        m = re.search(r"const fire = (.+?);", seg)
        assert m, "找不到 fire 的取值那行"
        expr = m.group(1)
        assert "item.due_at" in expr, "reminder 分支漏了 due_at —— 会显示成「待定」"
        assert "item.remind_at" in expr, "单独设的提醒时间也要算"

    def test_will_ring_distinguishes_deadline_from_reminder(self):
        """「会提醒」不能只看有没有字段，要区分截止时间和提醒时刻。

        待办的 `due_at` 是截止时间，到点不响；日程能写出 fire_at 就会响。
        之前底栏手写的是 `remind_at !== null || recurrence !== null`，
        3 条里 2 条日程只报「1 条会提醒」—— 少算的那条用户看不出来，
        但他会因此以为「不用再设提醒了」。
        """
        src = _capture()
        i = src.index("export function willRing")
        seg = src[i : i + 400]
        assert 'item.kind === "reminder"' in seg
        assert "reminderDraftOf(item) !== null" in seg, "日程按能否写出 fire_at 判"
        assert "item.remind_at !== null" in seg, "待办只有设了提醒才会响"
        # 计数必须走这个函数，不许在底栏再手写一版
        assert "isWritable(i) && willRing(i)" in src

    def test_day_anchor_written_only_when_the_day_changes(self):
        """同一天只在第一行写日期，否则每行都重复"明天"，又退化成分组标题。"""
        src = _capture()
        assert "showDay: i === 0 || rows[i - 1].slot.day !== row.slot.day" in src

    def test_filler_clauses_never_become_items(self):
        """整句都是"呃 / 然后那个 / 哦对了"的，丢掉且**不计入 unresolved**。

        弹一条"没看懂「呃」"等于告诉用户他说了句废话。
        """
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        assert "if (FILLER_ONLY.test(trimmed)) continue;" in fx
        assert "function isTimeOnly(" in fx

    def test_correction_patches_the_previous_item_only(self):
        """"三点，不对，四点"：修正只改上一条的时间，**不许新建条目**。

        钉的是 patchTime 的边界：它的函数体里一旦出现 push，一句话就会裂成两条。
        """
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        assert "if (CORRECTION_START.test(trimmed)) {" in fx
        body = fx[fx.index("function patchTime(") :]
        body = body[: body.index("\n}\n")]
        assert "push" not in body
        assert "fire_at: dayStart + (hour ?? 18) * 3600 + minute * 60" in body

    def test_pickup_code_attaches_instead_of_becoming_a_todo(self):
        """取件码 / 单号挂在上一件事上，不单独成一条待办。"""
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        assert "if (ATTACH_TO_PREV.test(trimmed) && items.length > 0) {" in fx
        assert "last.note =" in fx

    def test_original_words_are_open_by_default(self):
        """v13 把「你说的」放在结果区顶部，默认展开；点标题可收起。

        上一版是默认折叠（第一屏直接给轴），但 v13 要求"原话可回看"必须在
        第一屏看得到——否则用户不知道整理结果是从哪句话来的。
        """
        src = _capture()
        assert "const [showSource, setShowSource] = useState(true);" in src
        assert "{showSource ? (" in src

    def test_weekday_question_says_zhou(self):
        """"是这六，还是以后每六？"是错的 —— weekdayZh 只返回「六」，要自己补「周」。"""
        fx = _strip_comments(FIXTURE.read_text(encoding="utf-8"))
        assert "const zh = `周${weekdayZh(when.weekday)}`;" in fx


class TestCopySpeaksUserLanguage:
    """界面上不许端出内部术语（2026-11-10 晚）。

    起因：作者本人把「联动提醒只推迟不完成」记成了「只退出不完成」。
    一句话连写它的人都记不准，就是没进脑子 —— 用户更不可能懂「联动」：
    那是代码里的 `source != 'manual'`，界面上找不到任何叫「联动」的东西。

    钉的是用户真正看得见的三层，不是措辞品味：
      1. 导航与页标题只说「待办」，提醒退回成它的一个属性；
      2. 副标题里不出现「联动」「完成语义」这些分类词；
      3. 抽屉问候句论「条」不论「件」，且不再承诺「下一件 N 点会响」。
    """

    def test_nav_and_title_both_say_todo(self):
        """一个词不能在侧边栏、页标题、tab 三层各指一件事。"""
        nav = (ROOT / "frontend" / "src" / "components" / "console" / "ConsoleLayout.tsx").read_text(
            encoding="utf-8"
        )
        assert '{ href: "/reminders", icon: "bell", label: "待办" }' in nav
        assert 'title="待办"' in _page()
        assert 'title="提醒 / 待办"' not in _page()

    def test_subtitle_has_no_internal_category_words(self):
        """「联动」是代码词，「完成语义」是产品经理词，两样都不是用户的话。"""
        page = _page()
        assert "联动" not in page
        assert "完成语义" not in page

    def test_subtitle_answers_which_items_will_notify(self):
        """副标题必须回答「哪些会通知」—— 这正是用户分不清的那件事。"""
        assert "设了提醒时间，到时会通知你" in _page()

    def test_context_line_counts_todos_not_pieces(self):
        """待办论「条」，不论「件」。"""
        cap = _capture()
        assert "条待办" in cap
        assert "件${" not in cap

    def test_greeting_does_not_promise_it_will_ring(self):
        """不许再写「下一件 23:59 交高数作业」：那是 due_at，卡片上写着「不提醒」。

        正文里专门防了「只设截止不会响」，副标题却暗示会响 —— 等于自己推翻自己。
        """
        cap = _capture()
        assert "下一件" not in cap
        assert "nextLabel" not in cap


class TestCaptureWritesForReal:
    """Phase C：点「加入」真的写库。

    这一组守的不是"能写进去"，而是**写错的那几种**（规格 §8.2）：
    双击写两遍、把 `remind_at: null` 也发出去（等于替用户造一条联动提醒）、
    把没定时间的提醒写成哑弹、部分失败却声称全成功、失败原因不落款。
    """

    def test_commit_calls_the_two_existing_apis(self):
        """复用现有端点，不自建批量接口 —— 批量接口会把"部分失败"吞成整批回滚。"""
        page = _page()
        assert "api.tasks.create(taskDraftOf(item))" in page
        assert "api.reminders.create(draft)" in page
        fn = page[page.index("const commitCapture = useCallback(") :]
        fn = fn[: fn.index("\n  );")]
        assert "Promise.allSettled(" in fn, "一条失败不能拖累另外两条"
        assert "Promise.all(" not in fn

    def test_task_draft_omits_remind_at_instead_of_sending_null(self):
        """只说「明天交作业」的任务，请求里不许出现 `remind_at`。

        传 `remind_at: null` 与不传在后端是两件事：前者走"设置提醒时间"分支，
        会顺手造一条联动提醒 —— 用户没要求，列表里却多出一个会响的东西。
        """
        src = _capture()
        assert "if (item.remind_at !== null) draft.remind_at = item.remind_at;" in src
        assert "remind_at: null" not in src, "不许把 null 当默认值发出去"
        assert "if (item.due_at !== null) draft.due_at = item.due_at;" in src

    def test_reminder_draft_never_claims_task_source(self):
        """`source:"task"` 是联动提醒的专属标记，从这里写进去用户改不动它（会 400）。"""
        src = _capture()
        i = src.index("export function reminderDraftOf")
        body = src[i : src.index("\n}", i)]
        assert 'source: "manual"' in body
        assert '"task"' not in body

    def test_reminder_without_a_time_is_not_written(self):
        """算不出「什么时候」的提醒一个请求都不发：提醒的全部价值就是那个时刻。"""
        src = _capture()
        assert "if (at === null || at === undefined) return null;" in src
        assert "if (!rec?.at) return null;" in src
        # 拦住之后必须给出路：光说"没写几点"，用户唯一能做的就是取消勾选
        assert "点下方「补上时间」，设好后才会提醒你" in src

    def test_double_click_guard_is_a_ref_not_state(self):
        """防双击必须用 ref —— 这条是**真机 e2e 抓出来的**。

        上一版写的是 `if (committing) return; setCommitting(true)`：连续两次点击
        发生在同一个渲染里，两次读到的 `committing` 都还是 false，于是后端多出两条
        一模一样的待办（e2e B1 实测 got=2 / want=1）。源码断言的顺序当时是"对"的，
        这就是只查源码会给自己作的保。

        现在断言的是：闸门读的是 `committingRef.current`，而且它在 await 之前置上。
        """
        src = _capture()
        i = src.index("const commit = async () => {")
        body = src[i : src.index("\n  };", i)]
        guard = body.index("if (committingRef.current || pickedItems.length === 0) return;")
        arm = body.index("committingRef.current = true;")
        firstAwait = body.index("await onCommit(")
        assert guard < arm < firstAwait, "防双击那三行的顺序错了"
        assert "const committingRef = useRef(false);" in src

    def test_drawer_closes_only_when_nothing_failed(self):
        """全进去了才关抽屉；有失败的留在原地（关掉就等于把失败项一起丢了）。"""
        src = _capture()
        close = src.index("onClose();\n      return;")
        cond = src.rindex("if (Object.keys(failed).length === 0)", 0, close)
        assert cond < close

    def test_successful_items_are_removed_from_the_axis(self):
        """写成功的那几条要从轴上摘掉：它们已经在真实列表里，留着像写了两遍。"""
        src = _capture()
        assert "setItems((prev) => prev.filter((i) => !okIds.includes(i.id)));" in src

    def test_failure_reason_is_shown_under_that_row(self):
        """只标一个红点「没加上」，用户不知道该重试还是该改 —— 两件事差很远。"""
        src = _capture()
        assert 'className="rem-cap-issue"' in src
        assert 'role="alert"' in src
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert ".rem-cap-issue {" in css
        assert "var(--red-ink)" in _css_block(css, ".rem-cap-issue {")

    def test_pages_that_write_also_reload(self):
        """写完不刷新，用户去列表里看不到刚加的那条，会以为又失败了。"""
        page = _page()
        i = page.index("const commitCapture = useCallback(")
        fn = page[i : page.index("\n  );", i)]
        # 只报「没加上」却不刷新，用户去列表里看不到刚加的那条，会以为又失败了
        assert "if (okIds.length > 0) await reload();" in fn

    def test_toast_says_how_many_will_remind(self):
        """规格 §8.1：toast 要说清几条会提醒，不能只说"已加入"。"""
        page = _page()
        assert "其中 ${remind} 条会提醒你" in page
        assert "已加入 ${okIds.length} 条" in page

    def test_partial_failure_does_not_toast(self):
        """有失败时不要弹 toast：它在手机上是弹在主轴按钮上的（真机截图抓到过）。

        而且它会和抽屉底栏说同一件事、说的还不一样（"已加入 1 条" vs
        "已加入 1 条，1 条没加上"），用户会以为是两个结果。
        """
        page = _page()
        i = page.index("const commitCapture = useCallback(")
        fn = page[i : page.index("\n  );", i)]
        assert "if (bad === 0 && okIds.length > 0) {" in fn
        assert 'showToast("error", "这次一条都没加上' not in fn

    def test_preview_wording_is_gone(self):
        """Phase B 的「预览：都还没写进去」现在会变成反话 —— 它已经真的写进去了。"""
        for name, src in (("抽屉", _capture()), ("列表页", _page())):
            assert "预览" not in src, f"{name}上还写着预览"
            assert "onCommitPreview" not in src

    def test_demo_partial_cannot_be_triggered_in_production(self):
        """演示缝（造一次部分失败）必须和离线开关绑在一起，生产构建里摇掉。"""
        src = _capture()
        i = src.index("const forced =")
        gate = src[i : i + 200]
        assert "OFFLINE_PARSE && demoMode" in gate


class TestCaptureFillsMissingTime:
    """「缺少时间」得有个就地补的入口。

    上一版只是个灰色标签：标了却不给出口，用户唯一的动作是取消勾选 ——
    等于告诉他「你说错了」，还不给改的机会。这一组守的是补时间这条路的
    **判据、时区、拦下、回写**，任何一条错了用户都会得到一条错时刻的提醒。

    顺带修掉一个既有 bug：标签原来按「有没有时间字段」判，于是
    「每天…但没钟点」的提醒会显示「可直接加入」，点下去却发不出去 ——
    字段齐不等于写得进去。
    """

    def test_flag_uses_writability_not_field_presence(self):
        """标签必须按 `isWritable` 判，不能按"有没有时间字段"。

        `recurrence` 非 null 但 `at` 缺失的提醒（"每天提醒我喝水"没说几点），
        字段是全的，`reminderDraftOf` 却返回 null —— 用户看到「可直接加入」
        却一条都写不进去，只能怪自己。
        """
        src = _capture()
        assert ") : !isWritable(item) ? (" in src, "缺少时间这一档要按能不能写来判"
        assert "hasAnyTime" not in src, "旧的 hasAnyTime 判据会放过「有周期但没钟点」的提醒"

    def test_remind_count_only_counts_writable_rows(self):
        """底栏的「N 条会提醒」只算**真能写进去的**。

        截图抓到的原状：一条"有 recurrence 但没钟点"的提醒，字段齐、却发不出请求，
        底栏一边写「1 条会提醒」，一边写「1 条都没加上」—— 两句话当着用户的面互相打脸。
        """
        src = _capture()
        i = src.index("const remindCount = pickedItems.filter(")
        body = src[i : src.index(").length", i)]
        assert "isWritable(i) &&" in body

    def test_primary_button_counts_only_writable(self):
        """主按钮上的条数只能是**真能写进去的**。

        说「加入 1 条」就得真加 1 条。一条都写不进去时改成「1 条还不能加」——
        仍然可点（点一下给统一原因），但不能许诺一个不会发生的结果。
        """
        src = _capture()
        assert "const writableCount = pickedItems.filter(isWritable).length;" in src
        assert "`${pickedItems.length} 条还不能加`" in src
        assert "`加入 ${writableCount} 条，并设 ${remindCount} 条提醒`" in src

    def test_entry_only_on_unwritable_reminders(self):
        """待办不做这个入口：待办没有"几点响"这个概念，给它补时间是另一个功能。"""
        src = _capture()
        i = src.index('className="rem-cap-fix"')
        head = src[src.rindex("{", 0, i - 200) : i]
        assert 'item.kind === "reminder" && !isWritable(item)' in head

    def test_clock_only_for_repeating_rules(self):
        """重复规则只补钟点。

        给「每天 22:00」再问一个日期，用户填完发现日期没生效 —— 他只会觉得坏了。
        """
        src = _capture()
        assert "export function needsClockOnly" in src
        assert "!rec.at" in src or "freq !== \"once\"" in src
        assert "{needsClockOnly(item) ? null : (" in src, "重复项不该出现日期输入框"
        assert "这条会重复提醒，还差一个时间。" in src

    def test_local_time_is_interpreted_as_local(self):
        """拼 `YYYY-MM-DDTHH:MM` 交给 Date，**绝不用 Date.UTC**。

        用 UTC 拼的话，成都用户填「早八点」会得到下午四点响 —— 而且是在他睡着的时候。
        不带时区的 ISO 串在 JS 里按本地时区解释，正好是用户填的那一刻。
        """
        src = _capture()
        assert "new Date(`${date}T${time}:00`)" in src
        assert "Date.UTC" not in src

    def test_past_time_is_blocked_before_the_request(self):
        """过去的时间在前端就拦：用户改的是"什么时候响"，让他看着输入框就知道错。"""
        src = _capture()
        i = src.index("const applyFix = () => {")
        body = src[i : src.index("\n  };", i)]
        assert "if (ts <= Math.floor(now / 1000))" in body
        assert "这个时间已经过去了" in body
        # 拦下之后不能写：比的是**同一分支内**的先后（clockOnly 分支前面也有 setItems）
        past = body.index("这个时间已经过去了")
        assert past < body.index("setItems((prev) =>", past)

    def test_filled_time_lands_on_the_item(self):
        """补完要真的写回这一条：一次性写 `remind_at`，重复项写 `recurrence.at`。"""
        src = _capture()
        i = src.index("const applyFix = () => {")
        body = src[i : src.index("\n  };", i)]
        assert "recurrence: { ...i.recurrence, at: fixing.time }" in body
        assert "remind_at: ts," in body

    def test_confirm_flag_is_released_only_when_nothing_was_asked(self):
        """补时间要能解掉「需要确认」的死结，但**只在没有具体问句时**。

        `needsConfirm()` 的判据是「有没有答过问句」。有些条目 `needs_confirm`
        为 true 却 `questions` 为空（"没说哪天"），`[].some(...)` 恒假 →
        永远显示「需要确认」，界面上一个可点的地方都没有。
        而"这周六还是每周六"这种问句，补时间并不能替用户回答，硬清掉就是替他做主。
        """
        src = _capture()
        assert "export" not in src[src.index("function settledByTime") - 20 : src.index("function settledByTime")]
        i = src.index("function settledByTime(item: CaptureProposal, next: CaptureProposal)")
        body = src[i : src.index("\n}", i)]
        assert "item.questions.length === 0 ? { ...next, needs_confirm: false } : next" in body

    def test_filling_time_also_ticks_the_row(self):
        """补了时间就把这条勾上 —— 他特意补，说明要加进去。"""
        src = _capture()
        i = src.index("const applyFix = () => {")
        body = src[i : src.index("\n  };", i)]
        assert "setPicked((prev) => ({ ...prev, [item.id]: true }))" in body

    def test_filling_time_clears_the_previous_failure(self):
        """补完时间要撤掉上一次「没加上」的红字。

        不撤的话，用户改好了还看着「没加上」，会以为自己白改了一遍 ——
        他甚至可能再改一次。
        """
        src = _capture()
        i = src.index("const applyFix = () => {")
        body = src[i : src.index("\n  };", i)]
        # 钉住那个条件，不只钉"有没有这两行"：写成 `if (true) return prev;`
        # 两行都还在，但什么都没撤 —— 上一版就是这么假绿的
        assert "if (!(item.id in prev)) return prev;" in body
        assert body.index("if (!(item.id in prev)) return prev;") < body.index(
            "delete next[item.id];"
        )

    def test_fix_error_stays_in_the_row(self):
        """拦下理由写在输入框下面，不弹 toast —— 用户的眼睛正看着输入框。"""
        src = _capture()
        assert 'className="rem-cap-fix-err" role="alert"' in src
        assert "setFixErr(\"先选一个时间。\")" in src
        assert "setFixErr(\"日期和时刻都要选。\")" in src
        # `.rem-cap-fix-err` 先出现在 `-hint, -err` 的合并选择器里，
        # `_css_block` 会抓到那个共享块 —— 所以这里直接钉它自己那段声明
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert ".rem-cap-fix-err {\n  font-weight: 600;\n  color: var(--red-ink);" in css

    def test_fix_controls_are_touchable_and_do_not_zoom(self):
        """触控 ≥44、手机输入框 16px —— 两条都是硬约束，不是审美。"""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "min-height: 44px" in _css_block(css, ".rem-cap-fix-input {")
        assert "min-height: 44px" in _css_block(css, ".rem-cap-fix-ok {")
        # iOS 聚焦 <16px 的输入框会把整页放大且不还原（a11y.css 里有全局兜底，
        # 抽屉自己也要显式一份，不能指望别处的通配规则）。
        # 用正则定位到「那条 media 里的那个选择器」，而不是在文件里搜 16px ——
        # 别处写一个 16px 就能让这条静默变绿。
        assert re.search(
            r"@media \(max-width: 900px\) \{[^}]*\.rem-cap-fix-input \{\s*font-size: 16px;",
            css,
            re.S,
        ), "移动端要给 .rem-cap-fix-input 显式 16px"

    def test_no_wall_clock_read_during_render(self):
        """渲染期不许读 Date.now()：`now` 是页面每分钟推下来的，自己读会和服务端差一截。"""
        src = _capture()
        assert "Date.now()" not in src


class TestCaptureEmptyStateDemo:
    """空态里那块示范：不写教程，直接演一遍。

    把引导删干净之后抽屉中间是纯白一片（用户原话「太空了」）。但补回去的不能是
    说明文字 —— 堆步骤就变回「被教着用」。取的是 TimeMind 那种做法：
    给一轮真实的输入和它的结果，静态、不发请求、不写库。

    这组守四件事：**它在、它只在没人说话时在、它演的是产品真做得到的事、
    并且它自己说了这是示例**。最后一条最要紧 —— 不说的话，用户会以为
    那两条已经加进列表了。
    """

    def test_demo_only_when_idle_and_silent(self):
        """一提笔或一开口就收起：那时用户已经在行动，示例只会挡住结果。"""
        src = _capture()
        assert 'className="rem-cap-empty"' in src
        i = src.index('className="rem-cap-empty"')
        # 条件挂在整块空态（品牌行 + 演示卡）上，不是挂在卡片上：
        # 只收起卡片的话，品牌行会孤零零留在上面，比空着更怪
        head = src[max(0, i - 400) : i]
        assert 'status === "idle"' in head, "只在输入态出现"
        assert 'text.trim() === ""' in head, "一打字就该让位"
        assert 'voice.phase === "idle"' in head, "录音时不能还摆着示例"

    def test_demo_fills_the_input_without_sending(self):
        """卡可以点，但点了只填字：不发请求、不写库、不触发整理。

        这条是防退化的 —— 以后有人想「让示例也能点一下真的跑一遍」，
        会先在这里撞上，然后被迫想清楚要不要给示例加网络请求。
        和旧的示例 chips 是同一条规则（视觉基线 §7）：填充是帮忙，提交是替用户做主。
        """
        src = _capture()
        start = src.index('className="rem-cap-demo"')
        seg = src[start : src.index("rem-cap-demo-foot")]
        assert "onClick={fillDemo}" in seg, "卡要能点，否则看不出它是快捷输入"
        for forbidden in ("api.", "fetch(", "onCommit", "runParse", "useEffect"):
            assert forbidden not in seg, f"示例块里不该出现 {forbidden}"

        i = src.index("const fillDemo = ()")
        body = src[i : src.index("};", i)]
        assert "setText(DEMO_SAID)" in body
        assert "inputRef.current?.focus()" in body
        for forbidden in ("runParse", "api.", "onCommit", "fetch("):
            assert forbidden not in body, f"fillDemo 只许填字，不许 {forbidden}"

    def test_empty_state_keeps_the_brand_mark(self):
        """空态要保留举着待办清单的知穗。

        2026-11-11 线上核对时发现它被删过一轮，当时的理由是「这一屏要回答
        『我能做什么』，不是『这是谁做的』」。但它在这里不是署名：手里那张
        清单正是下面演示卡在做的事，属于演示的一部分；而且这是用户明确要过的
        图标（原话「加上我们的品牌图标，结合我们这个现在的功能特别点设计」）。
        删掉它属于替用户改需求，不是优化。
        """
        src = _capture()
        assert '<MascotLogo holding="list" />' in src, "品牌图标又丢了"
        assert 'className="rem-cap-brand-mark"' in src
        # 仍然不给可访问名：旁边就有主张句，读屏读两遍是噪音
        i = src.index('className="rem-cap-brand-mark"')
        seg = src[i : i + 160]
        assert "aria-hidden" in seg, "图标不该有自己的可访问名"

    def test_brand_mark_holds_a_todo_list(self):
        """不是把首页那个 logo 搬过来：它手里举着一张待办清单。

        空态下面演的就是「一句话被拆成几条」，站在旁边的那位得是这件事的
        执行者，否则只是贴了一个装饰。这条钉住三件事：道具只在被要求时出现、
        道具里有一条已完成的勾（待办能勾完是我们和纯日程产品的区别）、
        以及它挂在 mascot-body 里（跟着身体摇，才像"举着"）。
        """
        mascot = (ROOT / "frontend" / "src" / "components" / "chat" / "MascotLogo.tsx").read_text(
            encoding="utf-8"
        )
        assert 'holding === "list" ? <HoldingList /> : null' in mascot, "不能默认出现在全站"
        assert "mascot-hold" in mascot
        # 金色对勾：清单上那一条是「已经做完的」
        hold = mascot[mascot.index("function HoldingList()") : mascot.index("export default function")]
        assert 'stroke="#F5B840"' in hold, "完成态用品牌金标出来"
        # 道具必须在 mascot-body 里面，才会跟着摇摆
        body_start = mascot.index('className="mascot-body"')
        assert mascot.index("<HoldingList />", body_start) > body_start

    def test_empty_state_leads_with_the_task_not_the_brand(self):
        """空态第一句必须是任务本身：说清什么时候、要做什么。"""
        src = _capture()
        assert "说清「什么时候、要做什么」" in src

    def test_task_lead_sits_above_the_demo(self):
        """先说这一屏要做什么，再演一遍结果。"""
        src = _capture()
        assert src.index('className="rem-cap-lead"') < src.index('className="rem-cap-demo"')

    def test_task_lead_is_a_paragraph(self):
        """这句是纯文字，用 p；不要再往里塞图片或组件。"""
        src = _capture()
        assert '<p className="rem-cap-lead">说清「什么时候、要做什么」</p>' in src

    def test_brand_mark_uses_the_same_type_as_the_landing(self):
        """衬线 + 700 + --ls-tight，与 landing.css 的 .lp-brand 逐项对齐。

        不手动写死 15px/16px：字号用 1.0625rem，和首页保持同一个数，
        改一处两边一起动。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        block = _css_block(css, ".rem-cap-brand {")
        assert "font-family: var(--font-serif)" in block
        assert "font-weight: 700" in block
        assert "letter-spacing: var(--ls-tight)" in block
        assert "font-size: 1.0625rem" in block

    def test_demo_shows_only_what_the_product_can_do(self):
        """原话写「下午三点」「晚上七点」，结果就必须是 15:00 / 19:00。

        示例最怕演一个产品做不到的效果：用户照着说完发现对不上，
        比什么都不演更伤人。这里钉住原话与结果的一致，
        并且**待办那条只写日期**：待办在真实结果里只占日期锚点、不显示钟点，
        演示里给它编一个 20:00 就是演一件产品不会做的事。
        """
        src = _capture()
        assert "明天下午三点交高数作业，周五晚上七点社团开会，周日交实验报告" in src
        assert "明天 15:00" in src
        assert "周五 19:00" in src
        assert ">周日<" in src, "待办只写日期锚点"

    def test_demo_admits_it_is_an_example(self):
        """必须自报「这是示例」—— 否则用户以为那几条已经进了列表。

        同时要说明卡能点（这是它从静态图变成快捷输入后的唯一 affordance）。
        两句合成一行：一句说怎么用，一句说用了不会留下东西。

        不用“点一下可以试试 · 不会存进你的列表”那种写法 —— 间隔号 + “可以试试”
        是产品文档的语气。人说话会直接讲**会发生什么**：填进输入框、不会保存。
        """
        src = _capture()
        assert "点一下填入输入框" in src
        assert "不会保存" in src
        # 上一版的两个说法不许回来：它们是「AI 味」的具体形态，不是抽象感觉
        assert "可以试试 ·" not in src
        assert "不会存进你的列表" not in src

    def test_demo_rows_use_the_same_node_shapes_as_the_real_axis(self):
        """圈 = 待办，菱形 = 日程。

        演示里出现第三种形状，用户对照真实结果时就得多想一次"这俩是一回事吗"。
        形状在这里不仅好看，它是这个产品区分两类对象的唯一视觉手段。
        """
        src = _capture()
        assert 'className="rem-cap-demo-node" data-kind="task"' in src
        assert 'data-kind="reminder"' in src
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "border-radius: 50%" in _css_block(css, '.rem-cap-demo-node[data-kind="task"] {')
        assert "rotate(45deg)" in _css_block(css, '.rem-cap-demo-node[data-kind="reminder"] {')

    def test_empty_state_has_a_second_type_scale(self):
        """空态要有一句 22px 的主张，不能只有 20px 标题和 12px 正文两档。

        上一版整屏最大 20px、其余全 12px，中间是断的 —— 所以无论往中间塞多少内容，
        读起来都是"没有层次"，那才是"空"的真正来源，不是像素面积不够。
        """
        src = _capture()
        assert 'className="rem-cap-lead"' in src
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        block = _css_block(css, ".rem-cap-lead {")
        assert "font-family: var(--font-serif)" in block
        assert "font-size: 1.375rem" in block, "22px：标题 20 之下、正文 12 之上的那一档"

    def test_demo_card_does_not_share_the_drawer_background(self):
        """卡底不能和抽屉底同一个令牌 —— 那正是「糊在一起」的字面原因。

        抽屉底是 --card-bg（浮层最亮的一档）。上一版卡底也是它，
        两个同色矩形之间只剩一条 10% 的暖墨线，用户看图的原话是
        「画框和背景色一模一样」。这条断言盯的是**同值**这个事实，
        不是"看起来像不像"：以后谁把卡底改回 --card-bg，这里立刻红。

        只换明度不够：同一个色相里 ΔL* 三四个点在浅底上分不出，
        所以卡用的是另一套色相令牌（--memo-*），这一层也一起钉住。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        drawer_bg = re.search(r"background:\s*([^;]+);", _css_block(css, ".rem-cap-drawer {"))
        card_bg = re.search(r"background:\s*([^;]+);", _css_block(css, ".rem-cap-demo {"))
        assert drawer_bg and card_bg
        assert card_bg.group(1).strip() != drawer_bg.group(1).strip(), (
            f"卡底和抽屉底同色：{card_bg.group(1).strip()}"
        )
        assert card_bg.group(1).strip() == "var(--memo-bg)", "卡底要用便签那套冷色"

    def test_demo_card_is_a_light_note_on_a_warm_sheet(self):
        """卡内还要有一次冷暖分层：便签（冷）上贴三条暖白小纸条。

        上一版是卡亮行暗、同一个暖色里差 3 个点，凑近了才看得出。
        这一版靠色相直接分开，扫一眼就有里外 —— 行卡用回 --card-bg 的暖白，
        正好和冷底对立。这也是为什么行卡不能跟着改成 --memo-* 系：
        两层都冷，卡就又糊成一整块了。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        row = _css_block(css, ".rem-cap-demo-row {")
        assert "background: var(--card-bg)" in row

    def test_memo_tokens_exist_in_both_themes(self):
        """便签色必须在浅色和深色里各写一遍。

        CSS 变量不会因为切到 .dark 自动换值：只在 :root 写一次，
        深色主题下会拿到浅色的 #E8EEF7，在 #292217 的抽屉上变成一块发光的蓝斑。
        这类 bug 在浅色主题下永远看不见，只能靠断言拦。
        """
        tokens = (ROOT / "frontend" / "src" / "app" / "styles" / "tokens.css").read_text(
            encoding="utf-8"
        )
        i = tokens.index(".dark {")
        for name in ("--memo-bg:", "--memo-line:", "--memo-ink:"):
            assert name in tokens[:i], f"浅色主题缺 {name}"
            assert name in tokens[i:], f"深色主题缺 {name}"

    def test_demo_is_vertically_balanced(self):
        """内容不足一屏时上下 auto。

        贴顶会让下面空出一大片，那就是用户说的「太空」—— 加了内容却还是空的，
        等于白加。溢出时 auto 自动退化成 0，照常滚动。

        auto 挂在整块空态上、不挂在卡片上：两处都写的话，卡片还会在
        品牌行和底栏之间再挤一次，把两者顶开。
        """
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert "margin: auto 0" in _css_block(css, ".rem-cap-empty {")
        assert "margin: auto 0" not in _css_block(css, ".rem-cap-demo {")


# ==================== 用户文案规范（2026-11-11）====================
#
# docs/design/提醒中心_文案规范_2026-11-11.md §5.1 的九个别用词。
# 这份清单只对**用户看得见的地方**生效，所以要先真的把注释剔掉。
BANNED_COPY_WORDS = ("响", "推送", "送达", "静默", "来源", "日程", "触发", "后端", "服务端")

# 「影响」「响应」「音响」里都含「响」，但都不是闹钟那个动作。
# 不做这层剔除，规范页里「不影响你建多少提醒」会被自己的检查判红。
SAFE_COMPOUNDS = ("影响", "响应", "音响")


def _strip_js_comments(src: str) -> str:
    """按字符状态剥掉 JS/TS 注释，字符串里的 `//` 不算注释。

    为什么不复用上面的 `_strip_comments`：那个是正则，遇到
    `const u = "https://myzhisui.cn/x"` 会把 `https:` 后面的整行当行注释吃掉。
    用来断言「卡片里没有某个词」时这没问题（那行本来也没有），
    但用来找禁用词就是**漏检** —— 被吃掉的半行里有什么都查不到。

    注释里的换行保留，报错时行号才和源文件对得上。
    已知局限：模板字符串 `${...}` 内部的注释不会被识别（极少见，且只会误报不会漏检）。
    """
    out: list[str] = []
    i, n = 0, len(src)
    state = "code"  # code | dq | sq | tpl
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                j = src.find("\n", i)
                if j == -1:
                    break
                out.append("\n")
                i = j + 1
                continue
            if ch == "/" and nxt == "*":
                j = src.find("*/", i + 2)
                end = n if j == -1 else j
                out.append("\n" * src.count("\n", i, end))
                i = n if j == -1 else j + 2
                continue
            if ch == '"':
                state = "dq"
            elif ch == "'":
                state = "sq"
            elif ch == "`":
                state = "tpl"
            out.append(ch)
            i += 1
            continue
        # 字符串态：转义要整体跳过，否则 `\"` 会被当成字符串结束
        if ch == "\\":
            out.append(src[i : i + 2])
            i += 2
            continue
        if (state == "dq" and ch == '"') or (state == "sq" and ch == "'") or (state == "tpl" and ch == "`"):
            state = "code"
        out.append(ch)
        i += 1
    return "".join(out)


def _banned_hits(path: Path) -> list[str]:
    """返回 `行号 词 :: 上下文`；空列表表示这一份合规。"""
    code = _strip_js_comments(path.read_text(encoding="utf-8"))
    hits = []
    for no, line in enumerate(code.splitlines(), 1):
        probe = line
        for safe in SAFE_COMPOUNDS:
            probe = probe.replace(safe, "")
        found = [w for w in BANNED_COPY_WORDS if w in probe]
        if found:
            hits.append(f"{path.name}:{no} {'/'.join(found)} :: {line.strip()[:80]}")
    return hits


class TestUserCopyWords:
    """§5.1 静态检查：用户可见文案里不许出现九个别用词。

    规范页把这张表写成人工检查清单，但人工清单只能保证「今天查过」。
    写成断言才能保证「明天改回来会红」—— 这正是同一份文档 §6
    要求「不做假语音、假进度、假撤销」的同一个思路：不靠自觉，靠会红。
    """

    def test_no_banned_words_in_visible_copy(self):
        for path in (PAGE, CAPTURE):
            hits = _banned_hits(path)
            assert not hits, "用户可见文案里出现了规范禁止的词：\n" + "\n".join(hits)

    def test_checker_ignores_comments(self):
        """注释里的词不算 —— 这条是防止别人嫌红就把检查删掉。

        源码里大量注释在解释「为什么只设截止不会响」，那些必须被放过，
        否则测试会逼着人把注释改歪，那就变成了规范在篡改理由。
        """
        src = '// 到点不会响\nconst a = 1; /* 最近送达 */\nconst b = 2; // 推送'
        cleaned = _strip_js_comments(src)
        for word in ("响", "送达", "推送"):
            assert word not in cleaned

    def test_checker_keeps_urls_inside_strings(self):
        """字符串里的 `//` 不是注释 —— 这是正则版会踩的坑，必须钉住。"""
        src = 'const u = "https://myzhisui.cn/x"; const t = "推送";'
        assert "推送" in _strip_js_comments(src)

class TestCaptureEntryVisibility:
    """入口条必须与"列表是否为空"解耦。

    2026-11-11 线上抓到的真 bug：captureStrip 原先写在 listView 内部，
    而空列表时外层走的是 Callout 分支、listView 整个不渲染 ——
    **新用户第一次进待办页，恰恰看不到「一句话添加」**，而那正是最需要它的时刻。
    离线 188 条 e2e 全绿也没抓到，因为测试数据都 seed 了任务（isEmpty=false）。
    """

    def test_strip_is_defined_outside_list_view(self):
        """它得是个独立变量，不能长在 listView 里面。"""
        src = PAGE.read_text(encoding="utf-8")
        assert "const captureStrip" in src, "入口条不见了"
        assert src.index("const captureStrip") < src.index("const listView = ("), (
            "captureStrip 又回到 listView 内部了 —— 空列表时它会跟着一起消失"
        )

    def test_strip_renders_in_empty_branch_too(self):
        """空态和列表是二选一的两条分支，入口条必须在分支之外。"""
        src = PAGE.read_text(encoding="utf-8")
        k = src.index("{captureStrip}")
        seg = src[k : k + 700]
        assert "isEmpty" in seg, "captureStrip 没被放进 isEmpty 的判断里"
        assert "listView" in seg, "captureStrip 后面应该紧接着 isEmpty ? 空态 : listView"

    def test_strip_hidden_in_select_and_sort(self):
        """多选/排序时仍然不挂 —— 这是原设计，提出来之后不能顺手丢掉。"""
        src = PAGE.read_text(encoding="utf-8")
        i = src.index("const captureStrip")
        seg = src[i : i + 300]
        assert 'mode === "normal"' in seg, "多选/排序时入口条应该收起"


# ==================== 语音输入：全屏态与采集约束（2026-11-11）====================

VOICE = ROOT / "frontend" / "src" / "components" / "reminders" / "useVoiceInput.ts"


def _css_rule(selector: str, path: Path = CONSOLE_CSS) -> str:
    """取某个选择器的第一段 `{...}`。

    必须用 `selector + " {"` 精确匹配：`.rem-cap-live` 不能匹配到
    `.rem-cap-live-head`，否则断言读到的是另一条规则，永远绿。
    """
    raw = path.read_text(encoding="utf-8")
    i = raw.find(selector + " {")
    assert i >= 0, f"CSS 里找不到 {selector}"
    j = raw.index("}", i)
    return raw[i:j]


class TestVoiceCaptureSurface:
    """语音输入从"底栏一行提示"改成"整屏只做一件事"。

    参考的形态（TimeMind 的语音页）：按下麦克风后整屏只放这一件事，
    字大到能自己看出有没有说错，底部一条 `× | 波形 | 计时 | ✓` 的胶囊。

    为什么这些要用源码断言钉住：
      · 「字要大」最容易被下一个人为了"一屏多显示几条"改回 15px，而
        那恰恰是用户这次提的问题；
      · 采集约束（关降噪、开 AGC）看不出效果，删掉了界面完全正常，
        只有识别率会悄悄掉下去 —— 这是最典型的"改坏了没人发现"。
    """

    def test_live_layer_covers_the_drawer(self):
        """全屏层必须盖住抽屉本身，而不是塞在 body 里跟着一起滚。

        塞进 body 的话，字一多就把输入框顶走，而这一层的存在意义就是
        "说话时没有别的控件在动"。

        反证：把 `position: absolute` 删掉，本用例变红。
        """
        rule = _css_rule(".rem-cap-live")
        assert "position: absolute" in rule
        assert "inset: 0" in rule

    def test_live_text_is_far_bigger_than_the_input(self):
        """至少 24px。用户的原话是"文字大一点，现在好小"。

        反证：把 clamp 改成 `clamp(14px, 3vw, 16px)`，本用例变红。
        """
        rule = _css_rule(".rem-cap-live-text")
        assert "clamp(24px" in rule, f"字号又变回去了：{rule}"

    def test_dock_has_cancel_wave_clock_and_done(self):
        """底栏四个件缺一不可：取消、波形、计时、完成。

        少计时 → 用户不知道说了多久（60 秒上限时会突然被截断）；
        少波形 → 看不出麦克风有没有收到声音，只能靠念完再看结果。
        """
        src = CAPTURE.read_text(encoding="utf-8")
        for cls in (
            "rem-cap-live-x",
            "rem-cap-live-wave",
            "rem-cap-live-clock",
            "rem-cap-live-ok",
        ):
            assert cls in src, f"语音底栏少了 {cls}"

    def test_mic_blurs_the_input_before_starting(self):
        """点麦克风必须先收起键盘。

        抽屉打开时会自动聚焦输入框，手机上键盘已经弹起来；不 blur 的话
        全屏层下半屏被键盘盖住，刚上屏的字一个字都看不见。

        反证：删掉 `inputRef.current?.blur()`，本用例变红。
        """
        src = _strip_js_comments(CAPTURE.read_text(encoding="utf-8"))
        blur = src.find("inputRef.current?.blur()")
        start = src.find("void voice.start()")
        assert blur > 0 and start > 0
        assert blur < start, "blur 必须在 start 之前，否则键盘已经顶起来了"

    def test_capture_turns_off_noise_suppression(self):
        """降噪必须关、AGC 必须开。

        `noiseSuppression` 是为通话设计的，手机端会削 2-4kHz 的弱能量段，
        而中文声母 s/sh/x/j/q/zh/ch 正好在那里 —— 录出来人耳听着干净，
        识别引擎却少了区分字头的信息，表现为成片同音错字。

        反证：把 `noiseSuppression: false` 改回 true，本用例变红。
        """
        src = VOICE.read_text(encoding="utf-8")
        assert "noiseSuppression: false" in src
        assert "autoGainControl: true" in src
        assert "noiseSuppression: true" not in src

    def test_audio_is_normalized_before_resampling(self):
        """峰值归一化必须在编码前跑，且先于重采样。

        手机麦克风峰值常常只有 0.05-0.15，16bit 里有效位数只剩 12 位左右，
        声母先被量化掉一截。把峰值顶到 0.85 等于免费多 3-4 bit。

        反证：把调用改回 `resample(pcm, cur.rate)`，本用例变红。
        """
        src = VOICE.read_text(encoding="utf-8")
        assert "normalizePeak(pcm)" in src
        assert "normalizePeak(await" not in src

    def test_wave_stops_when_recording_stops(self):
        """波形只在录音时跑。不停的话 interval 会一直 setState，
        抽屉关掉之后还在跑（内存泄漏 + 电）。"""
        src = VOICE.read_text(encoding="utf-8")
        assert "setLevel(0)" in src, "停止时没把电平清零，波形会停在最后一帧"

    def test_result_titles_are_at_least_17px(self):
        """整理结果的条目标题不许退回 13px。

        这一屏的全部内容就是几条待办，13px 在手机上要凑近看。

        反证：把 17px 改回 `var(--fs-md)`（13px），本用例变红。
        """
        rule = _css_rule(".rem-cap-axisttl")
        assert "font-size: 17px" in rule, f"条目标题又变小了：{rule}"

    def test_live_animation_respects_reduced_motion(self):
        """关掉动效的人不该看到闪烁光标和呼吸点。"""
        raw = CONSOLE_CSS.read_text(encoding="utf-8")
        i = raw.rindex("@media (prefers-reduced-motion: reduce)")
        tail = raw[i:]
        assert ".rem-cap-live-caret" in tail
        assert "animation: none" in tail


# ==================== 流式语音：真实时链路（2026-11-11）====================


class TestStreamingVoice:
    """边说边出字。

    这一层最容易出的不是"字错了"，而是**字没到**：`onopen` 就发包、结束前
    不 flush 剩余半包、`partial` 当成追加拼接。三种都只在真机上表现为
    "我明明说了但没记上"，在代码里看着完全正常。
    """

    def test_audio_waits_for_ready_not_onopen(self):
        """必须等上游的 `ready` 帧才能发音频。

        WebSocket 的 `onopen` 只表示"这条连接建立了"，**不表示握手成功**：
        签名不对、APPID 不匹配时连接照样会 open，然后立刻被上游关掉。
        在 onopen 里置 `wsOpen = true` 的话，那几百毫秒的音频会发进一个
        即将关闭的连接，用户看到的是"说了开头，后面全没了"。

        反证：把 `cur.wsOpen = true` 挪到 `ws.onopen` 里，本用例变红。
        """
        src = _strip_js_comments(VOICE.read_text(encoding="utf-8"))
        # 锚点必须带赋值形态：文件前面的 teardown 里有 `cur.ws.onmessage = null`，
        # 光搜 "ws.onmessage" 会先命中那一行，断言的区间就成了倒的。
        onopen = src.find("ws.onopen = ()")
        onmessage = src.find("ws.onmessage = (event)")
        onclose = src.find("ws.onclose = ()")
        assert 0 < onopen < onmessage < onclose
        # onopen 里不许出现开闸
        assert "wsOpen = true" not in src[onopen:onmessage]
        # 开闸必须发生在 ready 帧的处理里
        assert "cur.wsOpen = true" in src[onmessage:onclose]

    def test_partial_replaces_instead_of_appending(self):
        """`partial` 是"这句到目前为止的全文"，必须整句替换。

        追加的话会得到"我我我要要去去图书馆"，而且它只在连续两次 partial
        之间才看得出来 —— 单测里不看这一条就永远发现不了。

        反证：改成 `setStreamLive((prev) => prev + frame.text)`，本用例变红。
        """
        src = _strip_js_comments(CAPTURE.read_text(encoding="utf-8"))
        assert "setStreamLive(frame.text)" in src
        assert "setStreamLive((prev)" not in src

    def test_remaining_bytes_are_flushed_before_end(self):
        """发 end 之前必须把没凑满的那半包发出去。

        每包 200ms，用户点完成时手上往往还压着几十毫秒 —— 而最后那个字
        常常正好在这几十毫秒里。不发的话它不会出现在任何结果里，
        但界面上刚才明明显示过。

        反证：删掉 `flush(cur, true)` 那一行，本用例变红。
        """
        src = _strip_js_comments(VOICE.read_text(encoding="utf-8"))
        flush_at = src.find("flush(cur, true)")
        end_at = src.find('JSON.stringify({ type: "end" })')
        assert flush_at > 0 and end_at > 0
        assert flush_at < end_at, "end 必须先 flush 再发，否则最后一个字会丢"

    def test_stream_url_is_same_origin_in_production(self):
        """生产环境必须连**同域**的 `/api/capture/ws`。

        这是整条链路能不能通的前提：生产 `/api/*` 走 Next.js 的 rewrites，
        而 rewrites **不支持 WebSocket 升级**，所以 nginx 必须把这条路径
        单独 proxy_pass 到后端 8000。如果前端写成绝对地址（比如把服务器 IP
        写死），换域名、上 HTTPS 都会静默失效。

        反证：把返回值改成写死的 `ws://8.163.62.25:8000/...`，本用例变红。
        """
        src = _strip_js_comments(VOICE.read_text(encoding="utf-8"))
        assert "window.location.host}/api/capture/ws" in src
        # 本地开发要能自己找到后端，否则每个新拉代码的人都要先配环境变量
        assert 'process.env.NODE_ENV === "development"' in src
        assert ":8000/api/capture/ws" in src

    def test_drawer_passes_streaming_flag(self):
        """抽屉要把 `/capabilities` 的 `stream` 传给录音钩子。

        不传的话钩子默认 `streaming=false`，界面上一切正常 —— 只是永远
        走一句话识别，"边说边出字"这个功能等于没做。
        """
        src = _strip_js_comments(CAPTURE.read_text(encoding="utf-8"))
        assert "streaming: streamOn" in src
        assert "setStreamOn(Boolean(c.stream))" in src

    def test_live_text_shows_what_is_still_changing(self):
        """正在说的那句要用次级色，和已经定型的区分开。

        这一屏唯一能告诉用户"这半句还会变"的方式就是颜色。分不出来的话，
        用户会在字还没稳的时候点完成，然后发现结果少了一截。

        反证：把 `.rem-cap-live-heard` 的颜色改成 `var(--text-1)`，本用例变红。
        """
        rule = _css_rule(".rem-cap-live-heard")
        assert "color: var(--text-2)" in rule

    def test_typing_animation_is_skipped_when_streamed(self):
        """流式已经逐条显示过了，收尾时不能再播一遍打字机。

        再播一遍的代价不只是多等 620ms：用户会看到同一段话在屏幕上
        "重新打一遍"，像是又识别了一次。
        """
        src = _strip_js_comments(CAPTURE.read_text(encoding="utf-8"))
        assert "streamedRef.current" in src
        # 流式分支里要直接给全长
        seg = src[src.find("if (streamedRef.current)") :][:200]
        assert "setLiveShown(said.length)" in seg


# ==================== 提醒权限搬进待办（2026-11-11）====================

ACCESS = ROOT / "frontend" / "src" / "components" / "reminders" / "ReminderAccess.tsx"
SETTINGS_PAGE = ROOT / "frontend" / "src" / "app" / "(console)" / "settings" / "page.tsx"


class TestReminderAccessInTodo:
    """提醒权限从「设置」搬进「待办 → 设置」，日历订阅整条删除。

    为什么这些值得钉住：
      · 「设置里还能开关提醒」= 两个地方管同一件事，关了一个、另一个还显示开着，
        是这一轮最想消掉的重复；
      · 「设备清单里能不能关掉别的设备」只在多设备时才需要，最容易在重构里悄悄
        退化成「只能看不能改」；
      · 日历一删，所有指着它的文案都变成了假出路，而假出路比没有出路更费时间。
    """

    def test_card_has_exactly_one_switch(self):
        """整张卡只有一个开关 —— 本机的提醒权限。

        用户的原话是「只把权限开关做一个按钮就行」。多一个「每天几点」的开关，
        就会和每条待办自己的提醒时间打架。

        反证：再加一个 `<Switch`，本用例变红。
        """
        src = _strip_js_comments(ACCESS.read_text(encoding="utf-8"))
        assert src.count("<Switch") == 1, "提醒权限卡里出现了不止一个开关"

    def test_other_devices_can_be_turned_off(self):
        """设备清单里，别的设备必须能单独关掉。

        「手机开了、电脑忘了关」是这张卡最真实的使用场景。只能看不能关，
        等于把用户逼回设置页。

        反证：把 `api.push.remove(d.id)` 删掉，本用例变红。
        """
        src = _strip_js_comments(ACCESS.read_text(encoding="utf-8"))
        assert "api.push.remove(d.id)" in src
        assert "关掉" in src

    def test_this_device_toggles_through_browser(self):
        """本机必须走浏览器的订阅/退订，而不是删服务端那行。

        只删服务端行的话，浏览器那边还留着一条悬空订阅，下次打开页面
        会「自己又开了」——用户会认为这个开关坏了。

        反证：把 `await disablePush();` 删掉，本用例变红。
        """
        src = _strip_js_comments(ACCESS.read_text(encoding="utf-8"))
        assert "disablePush()" in src
        assert "enablePush()" in src

    def test_mounted_in_todo_settings_tab(self):
        """卡必须挂在待办页的「设置」标签里，且排在免打扰之前。

        排在后面的话，用户打开设置先看到「免打扰」「每天最多提醒几次」，
        而这三项在权限没开时调了也不响。

        反证：把 `<ReminderAccess />` 挪到 `免打扰时间` 那段之后，本用例变红。
        """
        src = PAGE.read_text(encoding="utf-8")
        i = src.find("<ReminderAccess />")
        # 认标题本身而不是「免打扰时间」这四个字：设置标签底部那行汇总
        # （「免打扰 23:00–07:00 · 每天最多提醒 6 次」）里也有它，先匹配到就永远绿。
        j = src.find('<h3 className="card-title">免打扰时间</h3>')
        assert i > 0 and j > 0
        assert i < j, "提醒权限卡被排到免打扰后面了"

    def test_settings_page_only_links_out(self):
        """设置页不再放第二份控件，只留一条指路。

        反证：把 `<PushRow />` 加回去，本用例变红。
        """
        src = _strip_js_comments(SETTINGS_PAGE.read_text(encoding="utf-8"))
        for gone in ("<PushRow", "<CalendarRow", "<ReminderTimeCard"):
            assert gone not in src, f"设置页还在渲染 {gone}，两处都能开关同一件事"
        assert "/reminders?tab=settings" in src

    def test_removed_components_are_really_gone(self):
        """三个组件文件删掉、摘要日历没有偷偷接回来。

        留着文件不引用，下一个人会把它当成还能用的东西接回去。

        反证：随便新建一个 CalendarRow.tsx，本用例变红。

        ★ 2026-11-12 改过这里的接口断言：课表订阅加回来了，而它当初被
          一起删掉只是"前端入口全删"的连带结果 —— 删的理由（摘要日历固定
          20:00，和每条待办各自的时间对不上）跟课表无关。所以现在放行
          `/api/calendar/link`（只用来取地址），但摘要那份（`.ics` 主份、
          `link/reset`）仍然不许出现在前端：接回来就等于把那个"对不上"的问题接回来。
        """
        base = ROOT / "frontend" / "src" / "components" / "settings"
        for name in ("CalendarRow.tsx", "PushRow.tsx", "ReminderTimeCard.tsx"):
            assert not (base / name).exists(), f"{name} 还在"
        api_src = (ROOT / "frontend" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
        # 课表订阅只借这一条端点取地址（api.ts 里是对象属性，字面不会出现 api.calendar）
        assert "/api/calendar/link" in api_src
        # 摘要日历的痕迹：端点路径以 .ics 结尾，以及换链接的那个动作
        assert ".ics" not in api_src, "每日摘要日历被接回前端了"
        assert "link/reset" not in api_src

    def test_tab_can_be_opened_from_a_link(self):
        """`?tab=settings` 要能直接落到设置标签。

        首页那条提醒提示、教程页的按钮都靠它一步到位；不支持的话点过去
        停在列表，用户还得自己再点一次「设置」。

        反证：删掉读 `search` 的那段 effect，本用例变红。
        """
        src = _strip_js_comments(PAGE.read_text(encoding="utf-8"))
        assert 'get("tab")' in src
        assert 'setTab("settings")' in src

    def test_failed_status_is_not_reported_as_unsupported(self):
        """接口失败不能显示成「浏览器不支持通知」。

        `/api/push/status` 拿不到时返回 null。早先把它和「服务端没开推送」
        混成同一个分支，界面就对一个其实能收通知的浏览器说「你收不到」——
        用户照着这句话去换设备，方向从一开始就错了。

        反证：并回 `if (!s || !s.supported)`，本用例变红。
        """
        src = _strip_js_comments(ACCESS.read_text(encoding="utf-8"))
        assert 'setView("error")' in src
        assert "if (!s || !s.supported)" not in src

    def test_no_copy_promises_the_calendar_anymore(self):
        """界面上不能再出现「去日历里订阅」这种已经做不到的指引。

        这一条是给「文案与事实对齐」兜底的：删功能时最容易漏的就是别处的
        一句话，而那句话会把用户送去一个不存在的入口。

        反证：把「也可以在系统日历里订阅」加回待办页，本用例变红。
        """
        nudge = _strip_js_comments(
            (ROOT / "frontend" / "src" / "components" / "chat" / "PushNudge.tsx").read_text(
                encoding="utf-8"
            )
        )
        todo = _strip_js_comments(PAGE.read_text(encoding="utf-8"))
        for src in (nudge, todo):
            for bad in ("用日历", "系统日历里订阅", "日历也能提醒"):
                assert bad not in src, f"还有指向已删功能的文案：{bad}"


class TestTwoAreasAndTimeline:
    """首页的「时间轴 + 待办区 + 提醒区」（2026-11-11）。

    用户原话：在首页（有今天时间轴那一屏）看不到自己的待办和提醒，
    必须切到「全部」才有；而且时间轴只在其中一个 tab 出现。
    根因不是数据，是渲染：四个 scope 各写一套 JSX，空分组整段 return null，
    轴只在 `scope === "today"` 里渲染。

    这一组钉三件事，每一条都配过反证：
      1) 轴不再挂任何一个 tab；
      2) 两个区永远都在（0 条也留一行说明）；
      3) 勾完的卡片不回列表里消失，而是划线留在原地。
    """

    def _seg(self, start: str, end: str, back: int = 420) -> str:
        """从 `start` **往前多取一段**再截到 `end`。

        轴那段的条件写在卡片前面（`{mode !== "sort" ? (`），只从
        `card rem-axiscard` 开始截，正好把要断言的条件切在外面 —— 那样
        「轴绑回某个 tab」也照样通过，等于没断言（本轮踩过）。
        """
        page = _page()
        i = page.index(start)
        j = page.index(end, i)
        return page[max(0, i - back) : j]

    def test_axis_is_not_tied_to_a_tab(self):
        """轴表示的永远是「今天」，跟当前 tab 筛哪一批无关。

        反证：把轴那段的条件改回 `mode !== "sort" && scope === "today"` → 变红。
        """
        seg = self._seg("card rem-axiscard", 'data-kind="task"')
        assert 'mode !== "sort" ?' in seg
        assert "scope ===" not in seg, "轴又绑回某个 tab 了，切 tab 就会整块消失"

    def test_both_areas_always_render(self):
        """空的时候给一行字，而不是整段不渲染。

        反证：把 `remTotal === 0` 那个分支删掉（让空数组直接 map）→ 变红。
        """
        page = _page()
        assert 'className="rem-sect" data-kind="task"' in page
        assert 'className="rem-sect" data-kind="rem"' in page
        # 空的分支要真的挂在「0 条」上，不能只是字符串还在文件里
        assert "{remTotal === 0 ? (" in page
        # 0 条时的那句话必须存在，否则空区就是一整片什么都没
        assert "{taskEmpty}" in page and "{remEmpty}" in page
        # 只有一段时走的是「不画分段标题」的分支 —— 那个分支**必须也渲染条目**，
        # 否则「今天有 2 条、没有刚完成的」这种最常见的状态会整块空白。
        # （本轮就踩了：只写了 showTaskSub 的三元，else 写了 null。）
        assert "{taskBlocks[0]?.items.map(taskCard)}" in page
        assert "{remBlocks[0]?.items.map(card)}" in page

    def test_checked_task_is_not_pulled_out_of_the_list(self):
        """勾选不能把卡片从列表里删掉 —— 这是本次改动最核心的一条。

        旧写法 `[scope]: prev.tasks[scope].filter(...)` 会让卡片当场消失。
        所以既断言「那句删卡代码不在 toggleTask 里」，也断言「它在原地翻转状态」。
        反证：把 `.filter((t) => t.id !== task.id)` 塞回 toggleTask → 变红。
        """
        page = _page()
        i = page.index("const toggleTask = useCallback")
        block = page[i : page.index("const patchStyle", i)]
        assert "prev.tasks[scope]" not in block, "勾选又把卡片从当前 tab 里删掉了"
        assert "status: willDone ? \"done\" : \"open\"" in block
        # 四个 scope 都要翻，漏一个就会出现「今天划掉了、全部里还在」
        for scope in ("today", "upcoming", "all"):
            assert f"{scope}: prev.tasks.{scope}.map(flip)" in block
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        done = _css_block(css, '.rem-task[data-done="1"] .rem-title {')
        assert "line-through" in done

    def test_done_tail_comes_from_completed_at_not_memory(self):
        """「今天完成的」按 completed_at 捞，不靠内存里的「刚点过」。

        内存集合一刷新就没了，而用户刷一下页面那条划线卡就消失，等于没改。
        """
        page = _page()
        i = page.index("const doneToday")
        seg = page[i : i + 700]
        assert "tasks.completed.filter" in seg
        assert "t.completed_at >= start" in seg

    def test_done_card_has_an_explicit_delete_button(self):
        """已完成的卡片上直接给「删除」，不再收进 ⋯ 菜单。

        反证：把 done 分支删掉（让所有卡片都走 ⋯）→ 变红。
        """
        page = _page()
        assert 'className="rem-donebtn"' in page
        assert 'aria-label={`删除「${task.title}」`}' in page


class TestPriorityLevels:
    """四档优先级（2026-11-11）：普通 / 日常 / 重要 / 紧急。

    苹果提醒事项的做法：等级靠颜色表达，名字只出现在编辑面板里。
    这一组钉「档数」和「颜色只给等级，不给图标」两件事。
    """

    def test_four_levels_everywhere(self):
        page = _page()
        opts = page[page.index("const PRIORITY_OPTIONS") : page.index("const PRIORITY_OPTIONS") + 400]
        for label in ("普通", "日常", "重要", "紧急"):
            assert label in opts, f"少了一档：{label}"

    def test_level_colors_are_on_the_card_edge(self):
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        for level, token in (("1", "--info"), ("2", "--accent"), ("3", "--red")):
            block = _css_block(css, f'.rem-task[data-priority="{level}"] {{')
            assert token in block, f"等级 {level} 的颜色没挂上 {token}"
        # 0 普通不着色：四档都上色就变成四种标签，看不出哪条真急
        assert '.rem-task[data-priority="0"]' not in css

    def test_backend_and_frontend_agree_on_the_range(self):
        """后端放宽到 3、前端类型是 0|1|2|3，两头必须一起改。"""
        core = (ROOT / "core" / "task_core.py").read_text(encoding="utf-8")
        api = (ROOT / "api" / "tasks.py").read_text(encoding="utf-8")
        types = (ROOT / "frontend" / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
        assert "CHECK (priority IN (0, 1, 2, 3))" in core
        assert "PRIORITY_LABELS = {0: \"普通\", 1: \"日常\", 2: \"重要\", 3: \"紧急\"}" in core
        assert "le=task_core.PRIORITY_MAX" in api
        assert "export type TaskPriority = 0 | 1 | 2 | 3;" in types


class TestActionCopyIsHonest:
    """「提醒时做什么」这三档全是行话，用户猜不出来。

    2026-11-12 用户原话：「用户可能不知道『让助手帮我写』『开始专注』是什么意思」。
    查下来不只是看不懂，其中一档还是**假的** —— `suggest_focus` 在后端走的
    和「只提醒」完全同一个分支，选了等于没选。所以这里锁两件事：
    每档都要有一句人话解释；「让助手帮我写」不许在没内容可写的来源上出现。
    """

    def test_every_action_has_a_plain_hint(self):
        page = _page()
        block = page[page.index("const ACTION_HINT") : page.index("const AGENT_SOURCES")]
        for key, word in (
            ("notify", "通知"),
            ("agent_deliver", "简报"),
            ("suggest_focus", "专注"),
        ):
            assert key in block, f"{key} 没有解释文案"
            assert word in block, f"{key} 的解释没说到点会发生什么（缺「{word}」）"

    def test_agent_deliver_only_where_there_is_content(self):
        """自己建的提醒上没有东西可写，列出来只会让人以为助手会替他写作业。"""
        page = _page()
        assert "AGENT_SOURCES" in page
        assert "actionOptionsFor" in page
        # 抽屉必须按来源取选项；直接渲染全量 ACTION_OPTIONS 等于过滤被绕过
        assert "ACTION_OPTIONS.map" not in page, "抽屉又直接渲染全量选项了"
        assert "actionList.map" in page

    def test_focus_action_really_changes_destination(self):
        """「开始专注」必须真的换落点，不然它和「只提醒」没有区别。"""
        tick = (ROOT / "service" / "reminder_tick.py").read_text(encoding="utf-8")
        assert 'if action == "suggest_focus":' in tick
        assert '"/tools?tool=pomodoro"' in tick


class TestDailyTimeEntry:
    """「每天几点做回顾」必须在设置页有入口（2026-11-13 补上）。

    在这之前这个值只活在库里和调度器里：用户能在列表里看到「每日回顾」那条，
    想改几点却只能删掉重建 —— 而「删了重建」会丢掉送达流水，
    也解释不了「为什么不能直接改」。

    2026-11-13 第二批把控件从「两个 select」换成 `type=time`（与免打扰同款）。
    下面几条钉的是**换完之后仍然成立**的约束，不是当时那一版的实现细节：
    分钟仍受服务端档位约束、卡里仍不许长开关、关闭态仍要说清会被重新开启。
    """

    def test_settings_has_the_control(self):
        page = _page()
        assert "每天几点给你做回顾" in page
        # 读要能读到当前值，写要真的写回去 —— 只有半边的入口等于摆设
        assert "api.remind.get()" in page
        assert "api.remind.set(" in page

    def test_uses_the_same_control_as_quiet_hours(self):
        """同一屏里四种「选时间」是上一轮的毛病：两个 select + 两个 type=time。

        控件是一套语法，同一屏里出现两种选时间的方式，比配色不一致更容易被看见。
        现在统一成 type=time —— 这张卡里不许再出现 select。
        """
        page = _page()
        i = page.index("每天几点给你做回顾")
        block = page[i : i + 2000]
        assert 'type="time"' in block, "每天几点必须用 type=time（与免打扰同款）"
        assert "<select" not in block, "这张卡里不许再有 select"

    def test_minutes_come_from_the_server(self):
        """分钟仍然只允许 0/15/30/45，且档位来自服务端。

        后端 `_snap_time` 只接受这四档。换成 type=time 之后，
        「可选范围」由两件事一起保证：`step={900}` 让原生选择器只给这四档，
        `snapMinute` 把手动键入的分钟在提交前吸附掉。
        少任何一个，用户都会遇到「选了 20 分、存成 15 分」却说不出原因。
        """
        page = _page()
        assert "daily.steps" in page, "分钟档位必须取 /api/remind 的 steps"
        assert "snapMinute" in page, "手动键入的分钟必须在提交前吸附"
        assert "step={900}" in page, "原生时间选择器必须按 15 分钟步进"

    def test_closed_state_says_editing_reopens_it(self):
        """关掉之后改时间会重新启用（`set_system_time` 的语义就是「改到这个点并启用」）。

        不写这句，用户关掉后碰了一下时间、第二天又收到通知，会以为关不掉。
        """
        assert "改时间会重新开启" in _page()

    def test_switches_stay_in_one_place(self):
        """这张卡不另开一个开关：列表里那条「每日回顾」已经是开关。

        同一件事两个开关必然打架 —— 用户在卡上关了，列表里还显示开着，
        或者反过来，而两边都读同一个 `enabled`。
        """
        page = _page()
        i = page.index("每天几点给你做回顾")
        block = page[i : i + 2000]
        assert "<Switch" not in block, "设置卡里又长出一个开关，会和列表里那条打架"
