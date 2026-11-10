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
        """三个零不提供信息：空分组整段不渲染。

        变量名从 list 改成 children（Phase 1 的分组要同时装待办与联动提醒，
        叫 list 会和「清单 list_key」撞名），语义不变：长度 0 就整段不渲染。
        """
        page = _page()
        assert "if (children.length === 0) return null;" in page


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
        """一条流水都没有时说「还没响过」，不要留一个空白的 <ul>。"""
        page = _page()
        assert "还没响过。" in page

    def test_missed_is_not_rendered_as_failed(self):
        """`failed` + `missed:` 是「错过窗口」，不是「发送失败」。

        两者的处置完全不同（前者改设备/网络，后者看服务端），
        合并成一句「发送失败」等于把排查方向指错。
        """
        page = _page()
        assert 'startsWith("missed:")' in page
        assert '"错过"' in page

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
        assert "静默 {quietStart}–{quietEnd}" in page
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        assert ".rem-legend-hatch {" in css
        # 深色下必须换一套斜纹，否则暖墨 alpha 在近黑上等于没画
        assert ".dark .rem-legend-hatch {" in css

    def test_ticks_inside_quiet_are_muted(self):
        """整点线穿过静默段会被读成"轴断了"，压淡它。"""
        page = _page()
        assert "data-quiet={inQuiet(quiet, h * TICK_MIN) ? \"1\" : \"0\"}" in page
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
        exit_block = page[page.index("const exitMode"): page.index("const beginPick")]
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
        save = page[page.index("const saveSort"): page.index("const runBulk")]
        assert "api.tasks.patch" not in save, "排序又在逐条改"

    def test_noop_save_does_not_write(self):
        """没动就不发请求：一次「什么都没改」的保存不该在服务端留 updated_at。"""
        page = _page()
        save = page[page.index("const saveSort"): page.index("const runBulk")]
        assert 'ids.join("|") === before.join("|")' in save

    def test_failed_save_keeps_the_dragged_order(self):
        """保存失败要留在排序模式。

        直接退出等于把用户拖了半天的顺序丢掉，而他看到的是一句 toast ——
        再进来时顺序已经回到原样，会以为是「保存按钮坏了」。
        """
        page = _page()
        save = page[page.index("const saveSort"): page.index("const runBulk")]
        tail = save[save.index("} catch {"):]
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
        """排序模式只留可排序的待办：日程提醒的时刻由来源决定，拖了不会动。"""
        page = _page()
        assert page.count('mode !== "sort" && scope ===') == 4, "四个 scope 有一个没被挡住"
        # 排序模式下不显示「下一件」和快速添加：正在挑行时多一个输入框会抢焦点
        assert 'scope !== "completed" && mode === "normal"' in page
