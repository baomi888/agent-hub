"""手机底部 tab 的三条不变量（2026-11-13 第三批）。

背景：手机上原来是一套 3 列 × 3 行的导航网格，9 个入口铺在内容上面，
光导航就吃掉首屏 216px —— 每次打开待办都要先滑过一遍导航。
这一批换成底部 tab，代价是「导航和内容抢高度」这件事没有了，但换来三处新耦合：

  1. **tab 的高度必须让出去**。内容区不让位 → 最后一条待办永远压在 tab 底下
     点不到；让成写死的数字 → 哪天把 tab 做高一档就有一处忘了跟。
  2. **tab 有几格必须和清单切片数对得上**。清单里前 4 项进 tab，CSS 却写死 5 列；
     有人把切片改成 5 项，第 6 个 tab 会被挤进第 5 格里，或者干脆换行。
  3. **桌面不能再出现第二条导航**。tabbar 默认是 display:none，
     只有 ≤900 才开 —— 反过来写（默认显示、宽屏再隐藏）会在平板上同时出现两条。

这三条都是"页面看起来正常、接口全 200、没有任何报错"的那种坏，只能靠断言钉。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
CONSOLE_CSS = SRC / "app" / "styles" / "console.css"
FX_CSS = SRC / "app" / "styles" / "fx.css"
NAV_TS = SRC / "lib" / "nav.ts"
TABBAR_TSX = SRC / "components" / "console" / "ConsoleTabBar.tsx"
LAYOUT_TSX = SRC / "components" / "console" / "ConsoleLayout.tsx"
HOME_TSX = SRC / "app" / "page.tsx"
# 工具集页和其它页一样住在 (console) 里，由 layout 统一挂外壳（2026-11-13 第三轮）
TOOLS_PAGE = SRC / "app" / "(console)" / "tools" / "page.tsx"
# 旧路由组。它必须一直是"不存在"的状态：两个组各有一份 tools/page.tsx 会撞路由，
# 而 2026-10-30 那次双路由事故就是"删了文件、服务器上还留着"造成的。
LEGACY_CAMPUS_GROUP = SRC / "app" / "(campus)"
CAMPUS_CSS = SRC / "campus" / "styles" / "campus.css"
TOOLS_HUB = SRC / "campus" / "components" / "CampusToolsHub.tsx"


def _read(p: Path) -> str:
    # 路径写错时不能静默通过
    assert p.exists(), f"文件不存在：{p}"
    return p.read_text(encoding="utf-8")


def _css_rules(css: str, selector: str) -> list[str]:
    """取选择器后面**所有** `{...}` 的内容。

    同一选择器在一个媒体块里可以出现多次（`.console-shell` 就同时定了列宽和
    最小高度）。只看第一段的话，断言会读到另一条规则然后莫名其妙地红/绿。
    """
    out: list[str] = []
    start = 0
    while True:
        i = css.find(selector + " {", start)
        if i < 0:
            break
        brace = css.index("{", i)
        depth = 0
        for j in range(brace, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[brace + 1 : j])
                    start = j
                    break
        else:
            raise AssertionError(f"规则没有闭合：{selector}")
    assert out, f"没找到规则：{selector}"
    return out


def _css_rule(css: str, selector: str) -> str:
    """取选择器后面第一段 `{...}` 的内容（不含花括号）。"""
    return _css_rules(css, selector)[0]


def _all_decls(rule: str, prop: str) -> list[str]:
    """取一段规则里某个属性的**所有**值。

    回退写法（`height: calc(100vh - X)` 紧跟 `height: calc(100dvh - X)`）
    意味着同一个属性有两条声明，而**每一条都要算对**：
    只扣 dvh 那条的话，不支持 dvh 的老浏览器仍然会被压住 ——
    而"规则里出现过 var(--tabbar-h)"这种检查完全看不出这个差别。
    """
    # 不用 f-string：正则里那个 `}` 在 f-string 里必须写成 `}}`，
    # 一不小心就变成一个看不出问题的正则（这个坑踩过一次）
    pattern = r"(?<![a-z-])" + prop + r":\s*([^;}]+)"
    return [v.strip() for v in re.findall(pattern, rule)]


def _media_block(css: str, query: str, must_contain: str | None = None) -> str:
    """取某个 `@media (...)` 后面那一整块（含内部规则）。

    同一份样式里可以有好几个同条件断点（fx.css 就有两个 899px 块）。
    取第一个是碰运气 —— 用 `must_contain` 指定块里必须出现的片段，
    找不到就报错，而不是拿另一块去断言然后莫名其妙地红。
    """
    start = 0
    while True:
        i = css.find(query, start)
        assert i >= 0, f"没找到媒体查询：{query}" + (f"（含 {must_contain}）" if must_contain else "")
        brace = css.index("{", i)
        depth = 0
        for j in range(brace, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    block = css[brace + 1 : j]
                    if must_contain is None or must_contain in block:
                        return block
                    start = j
                    break
        else:
            raise AssertionError(f"媒体查询没有闭合：{query}")


def _strip_comments(text: str) -> str:
    """去掉注释（CSS 只有 /* */，TS/TSX 还有 //）。

    断言"某个类名不该出现"时必须先剥注释：这一轮删掉 .campus-scroll / .campus-bar
    之后，文件里保留了好几段解释"它们为什么被删"的注释 ——
    不剥的话，注释反而会让断言红，等于罚了写清楚原因的人。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def _last_display(rule: str) -> str | None:
    """取一段规则里**浏览器实际会采用**的 display 值。

    两个坑，反证时都踩过：
      · 同一段里写两次 display，后写的赢 —— 只查"出现过 display:none"会假绿；
      · 只要有一条带 `!important`，它就赢了后面所有不带 !important 的 ——
        所以不能简单取最后一条。
    """
    hits = re.findall(r"display:\s*([a-z-]+)(\s*!important)?", rule)
    if not hits:
        return None
    forced = [v for v, imp in hits if imp]
    return forced[-1] if forced else hits[-1][0]


# ==================== ① 手机不再有那套 3 列网格 ====================


def test_sidebar_is_hidden_on_phone():
    """≤900 时竖排侧栏必须整块消失。

    它不是"缩小"或"折起来"——390px 宽里没有竖排导航的立足空间。
    留着它（哪怕 display 成横向一行）就还是那 216px。
    """
    block = _media_block(_read(CONSOLE_CSS), "@media (max-width: 900px)", "console-nav")
    for rule in _css_rules(block, ".console-nav"):
        assert _last_display(rule) == "none", (
            f"手机上侧栏又回来了（display: {_last_display(rule)}）—— 首屏会立刻少掉 216px"
        )


def test_tabbar_only_exists_on_phone():
    """tabbar 默认不渲染：桌面上导航是竖排侧栏，两条同时出现是最典型的移动端翻车。"""
    css = _read(CONSOLE_CSS)
    # 基础规则里它是关着的
    base_rule = _css_rule(css, ".tabbar,\n.tab-sheet,\n.tab-sheet-scrim")
    assert _last_display(base_rule) == "none"
    # 只有 ≤900 那一块把它打开
    block = _media_block(css, "@media (max-width: 900px)", "tabbar")
    assert _last_display(_css_rule(block, ".tabbar")) == "grid"


# ==================== ② 让位高度只有一个来源 ====================


def test_tabbar_height_is_a_single_token():
    """--tabbar-h 只能定义一次。

    定义两处（比如媒体查询里再写一个 72px）时，后加载的那份说了算，
    而"让位"的那几处还按前一份算 —— 差 12px 就够让最后一条待办点不到。
    """
    css = _read(CONSOLE_CSS)
    assert css.count("--tabbar-h:") == 1, "tab 高度被定义了不止一次"


def test_every_offset_uses_that_token():
    """两处让位（内容区、最短页高）必须用变量，不许再写数字。

    ★ 首页工作台**不在**这个名单里：它 2026-11-13 起不挂 tab，
      高度反过来要求「不许出现 var(--tabbar-h)」—— 见 ④ 的
      `test_home_does_not_mount_the_tabbar`。别把这条当成漏改补回去。
    """
    css = _read(CONSOLE_CSS)
    block = _media_block(css, "@media (max-width: 900px)", "tabbar")

    scroll = _css_rules(block, ".console-scroll")
    assert any("var(--tabbar-h)" in r for r in scroll), (
        "内容区没用 tab 高度变量让位 —— 最后一条内容会被压在 tab 底下"
    )

    shell = _css_rules(block, ".console-shell")
    assert any("var(--tabbar-h)" in r for r in shell), (
        "最短页高没扣 tab —— 空页面会多出一条假滚动条"
    )


def test_token_matches_the_tab_it_describes():
    """变量值必须等于 tab 自身的高度（命中区 + 上下内边）。

    这条是防"改了 tab 项高度、忘了改令牌"。数字对不上时，
    让位与遮挡之间就差那几个像素 —— 而它只在真机上最后一行才会露出来。

    ★ 上下内边**从 CSS 里读**，不再写死 +8。
      2026-11-13 第二轮把 padding 从 4 改成 3、命中区从 52 改成 44，
      写死 8 的那版会红一次：红得住，但下次再调还得手改一遍预期，
      改着改着就会有人直接把断言删掉。读出来才对得上"令牌描述 tab"这个本意。
    """
    css = _read(CONSOLE_CSS)
    m = re.search(r"--tabbar-h:\s*(\d+)px", css)
    assert m, "--tabbar-h 不是像素值"
    total = int(m.group(1))

    block = _media_block(css, "@media (max-width: 900px)", "tabbar")
    item = _css_rule(block, ".tabbar-item")
    mh = re.search(r"min-height:\s*(\d+)px", item)
    assert mh, "tab 项没有 min-height，触控下限就没了"
    item_h = int(mh.group(1))

    assert item_h >= 44, f"tab 项只有 {item_h}px，低于 44 的触控下限"

    # padding: 3px 6px calc(3px + env(...)) → 上 3 / 下 3
    bar = _css_rule(block, ".tabbar")
    pad = re.search(r"padding:\s*(\d+)px\s+\d+px\s+calc\((\d+)px", bar)
    assert pad, f"tabbar 的 padding 不是「上 / 左右 / calc(下 + 安全区)」三值写法：{bar[:80]}"
    top, bottom = int(pad.group(1)), int(pad.group(2))

    assert total == item_h + top + bottom, (
        f"--tabbar-h={total} 与 tab 实际高度 {item_h}(命中区)+{top}+{bottom} 不一致，让位会算错"
    )


# ==================== ③ 格数与清单切片数必须一致 ====================


def test_grid_columns_match_primary_count():
    """CSS 的列数 == 前 N 项 + 「更多」。

    两边各写各的数字时，谁也不会报错：清单多一项，第 6 个 tab 悄悄挤进第 5 格；
    少一项，最后一格空着。两种都只有截图才看得出来。
    """
    nav = _read(NAV_TS)
    m = re.search(r"TAB_PRIMARY_COUNT\s*=\s*(\d+)", nav)
    assert m, "清单里没有 TAB_PRIMARY_COUNT"
    primary = int(m.group(1))

    # ★ 2026-11-14（iOS 内测改造）：tab 不再由"前 N 项切片"决定，而是每项显式标
    #   `mobileTab: 1..N`。于是多了一层必须钉住的耦合：**标记出来的格位数量与编号
    #   必须正好是 1..TAB_PRIMARY_COUNT**。
    #   缺号 → 中间留一个空格位；重号 → 排序后有一项被挤掉；多一项 → 第 6 格换行。
    #   三种都只表现为"底部导航看着怪"，接口全 200、控制台无报错。
    #   只匹配对象字面量里的 `mobileTab: 1,`（注释里出现过 `mobileTab: 1..4` 这种说明，
    #   不加 `[,}]` 边界会把它算成一项 —— 这正是 topics/05 里"X in src 被同类名误命中"）。
    slots = [int(s) for s in re.findall(r"mobileTab:\s*(\d+)\s*[,}]", nav)]
    assert slots, "没有任何一项标记 mobileTab —— 手机底部 tab 会是空的"
    assert sorted(slots) == list(range(1, primary + 1)), (
        f"mobileTab 应为 1..{primary} 连续，实际是 {sorted(slots)}"
    )

    # 互斥配置：mobileTab 说"进 tab"，desktopOnly 说"移动端别出现"，两个同时写没有意义
    for blk in re.findall(r"\{[^{}]*desktopOnly:\s*true[^{}]*\}", nav):
        assert "mobileTab" not in blk, f"同时标了 mobileTab 和 desktopOnly，配置矛盾：{blk[:60]}"

    css = _read(CONSOLE_CSS)
    block = _media_block(css, "@media (max-width: 900px)", "tabbar")
    rule = _css_rule(block, ".tabbar")
    cols = re.search(r"grid-template-columns:\s*repeat\((\d+)", rule)
    assert cols, "tabbar 没有定义列数"
    assert int(cols.group(1)) == primary + 1, (
        f"CSS 排了 {cols.group(1)} 列，但清单是 {primary} 项 + 更多 = {primary + 1}"
    )


def test_tabbar_reuses_the_shared_nav_list():
    """侧栏、底部 tab、「更多」面板三份必须同源。

    抄第二份清单是最省事的写法，也是最容易漂的 ——
    "手机上怎么找不到 X" 就是这么来的：桌面加了，手机那份没人改。
    """
    nav = _read(NAV_TS)
    assert "export const NAV" in nav
    assert "export function visibleNav" in nav

    for path in (LAYOUT_TSX, TABBAR_TSX):
        src = _read(path)
        assert '@/lib/nav' in src, f"{path.name} 自己写了份导航清单"

    tab = _read(TABBAR_TSX)
    # ★ 2026-11-14 改：旧断言要求 `TAB_PRIMARY_COUNT` 出现在 TabBar 里（那是"切片"模型的
    #   证据）。现在模型换成按 `mobileTab` 筛选、按 `desktopOnly` 排除，两个筛选函数
    #   都必须来自 lib/nav —— 否则"手机上怎么找不到 X"会从字符串层面重新长出来。
    assert "mobileTabNav(" in tab and "mobileMoreNav(" in tab, (
        "底部 tab 没有从共享清单按 mobileTab / desktopOnly 筛选"
    )
    assert "@/lib/nav" in tab


def test_learning_path_owns_a_mobile_tab_slot():
    """「学习路径」必须占一个一级 tab 格位（2026-11-14 iOS 内测改造的起因）。

    改造前它在 NAV 里排第 9 —— 桌面顺序是按"什么时候做什么"排的，合理；
    但手机底部 tab 当时取 `slice(0, 4)`，于是它的入口在「更多」面板里还要往下翻。
    而它的产品定位是「和对话平行」（申报书 3.4），实际却连一级入口都没有。

    这条守的是**产品要求**，不是代码风格：有人把 `mobileTab: 2` 删掉、
    或者顺手把它标成 `desktopOnly`，手机上就再也找不到它，而所有测试照样绿
    （拓扑没坏，只是入口消失了）。
    """
    nav = _read(NAV_TS)
    m = re.search(r'\{ href: "/path"[^}]*\}', nav)
    assert m, "nav.ts 里找不到 /path 那一条，正则失效"
    entry = m.group(0)
    assert "desktopOnly" not in entry, "学习路径被移出移动端了 —— 它与「对话」同等重要"
    assert re.search(r"mobileTab:\s*\d+", entry), (
        "学习路径没有 mobileTab 格位，手机上会退到「更多」里"
    )


def test_console_only_pages_are_kept_off_mobile():
    """移出移动端的三个模块必须真的标了 desktopOnly（且桌面仍可见）。

    2026-11-14 决定：每日委托 / 总结 / 四六级 只留桌面。
    理由分别是「任务生成后实际落在对话」「对比型报表窄屏读不出结论」「备考要排计划、手机不适用」。
    这一条双向守：既防止有人改回去，也防止有人顺手把它们从 NAV 里整条删掉
    （那样桌面侧栏会少三个入口 —— "移出移动端"从来不是"删功能"）。
    """
    nav = _read(NAV_TS)
    for href in ("/delegate", "/report", "/cet"):
        m = re.search(r'\{ href: "' + href + r'"[^}]*\}', nav)
        assert m, f"nav.ts 里找不到 {href}，它应该还在桌面侧栏里"
        entry = m.group(0)
        assert "desktopOnly: true" in entry, f"{href} 没有标 desktopOnly，手机上会重新出现"


# ==================== ④ 首页刻意不挂 ====================


def test_home_does_not_mount_the_tabbar():
    """首页（/）不挂底部 tab —— 用户 2026-11-13 决定：「影响整体性」。

    这一条**方向是反的**，别急着按老习惯改回去：
      · 11-13 下午曾为了让「对话」入口常驻而在首页单独挂过一次；
      · 同一天晚上用户看过真页面后要求撤掉 —— 工作台是整块满屏卡片，
        底部压一条 50px 的导航把它切成两截。
    控制台那 8 个页面仍由 ConsoleLayout 统一挂，所以 tab 没被废弃，
    只是不在一级页面出现。

    断言查两件事，缺一都不算撤干净：
      · 注释剥掉后没有 `<ConsoleTabBar`（只留 import 的话组件不在 DOM 里，
        而字符串检查照样通过 —— 反证抓到过这一条）；
      · fx.css 里首页窄屏高度不再减 `--tabbar-h`（不然底部空出 50px 白边，
        比原来被压住还难看）。
    """
    home = _strip_comments(_read(HOME_TSX))
    assert "ConsoleTabBar" not in home, (
        "首页又挂回底部 tab 了：用户明确要求主页面不要，工作台会被切成两截"
    )

    block = _media_block(_read(FX_CSS), "@media (max-width: 899px)", "workbench-main")
    heights = _all_decls(_css_rule(block, "main.workbench-main"), "height")
    assert heights, "首页工作台没有设高度"
    for h in heights:
        assert "var(--tabbar-h)" not in h, (
            f"首页已经没 tab 了，高度还在扣它（{h}）—— 底部会空出一条白边"
        )
        # ★ 2026-11-14（第九批）：纸铺到视口边缘，上下各 8px 的外边距归零，
        #   高度算式从「满屏减 16px」变成「满屏减安全区」。这里查的是
        #   "没有多扣外边距"，而不是某个具体数字 —— 否则下次调边距又假红。
        assert not re.search(r"-\s*16px", h), (
            f"首页高度还在减上下 8px 外边距（{h}）—— 纸已贴边，减了会空一条"
        )
        assert "safe-area-inset-top" in h, (
            f"首页高度没减顶部安全区（{h}）—— 刘海会把内容压住"
        )


def test_home_still_has_a_way_into_the_console():
    """撤掉 tab 之后，手机从首页进控制台不能没路。

    用户接受撤 tab，是因为还能进得去 —— 侧栏（手机是抽屉）和首页的入口卡片。
    这条钉住侧栏还在渲染：真把 Sidebar 也删了，手机上就只剩「更多」那条死路。
    """
    home = _strip_comments(_read(HOME_TSX))
    assert "<Sidebar" in home, "首页连侧栏都不挂了：手机上没有任何进控制台的入口"
    nav = _read(NAV_TS)
    assert '"/reminders"' in nav or "/reminders" in nav, (
        "共享导航清单里没有待办 —— 侧栏和 tab 都靠它，删了等于两个入口一起断"
    )


def test_tabbar_closes_on_navigation():
    """换页要收面板。

    不收的话，从「更多」里点设置，面板会盖在新页面上 —— 看起来像没跳成功。
    """
    tab = _read(TABBAR_TSX)
    assert "setMoreOpen(false)" in tab
    assert "pathname]" in tab or "[pathname]" in tab, "面板的收起没有跟着路由走"


# ==================== ⑤ 工具集页：和其它页一样住进外壳 ====================


def test_tools_page_lives_in_the_console_group():
    """工具集必须住在 (console) 里 —— 那是外壳（侧栏 + tab + 滚动）唯一的挂载点。

    用户 2026-11-13 的原话：「我不想点开工具集这个模块还要跳到一个独立的页面，
    想要和跳转设置这些一样」。不在这个组里的表现是**整屏换掉**：
    宽屏侧栏消失、手机底部 tab 消失，想换功能只能先点「← 回首页」。

    断言查两件事：
      · 文件在 (console)/tools 下（路由组决定外壳，别的都不算数）；
      · 页面用 PageShell 出页头 —— 不用它的话就没有 pg-head，
        进来第一眼和设置页长得不一样，"一样"只做了一半。
    """
    assert TOOLS_PAGE.exists(), f"工具集不在 (console) 组里：{TOOLS_PAGE}"
    page = _read(TOOLS_PAGE)
    assert "<PageShell" in page, "工具集页没有用 PageShell 出页头：和别的页对不齐"


def test_tools_page_does_not_mount_its_own_tabbar():
    """外壳已经有一条 tab，页面不许再加一条。

    两条 fixed 在同一个 bottom:0 上会完全重叠：看起来只有一条，
    但点击落到上面那条、无障碍树里读到两个导航，键盘 Tab 也会走两遍。
    这是「原来单独挂一条」的遗留，并进外壳后必须拆掉。
    """
    page = _read(TOOLS_PAGE)
    assert "ConsoleTabBar" not in _strip_comments(page), (
        "工具集页又自己挂了一条 tab：外壳里已经有一条，两条会叠在同一个位置"
    )


def test_legacy_campus_group_is_gone():
    """旧的 (campus) 路由组不能再出现。

    两个组各放一份 /tools 会直接撞路由（Next 构建期报错）；
    更常见的是"本地删了、服务器上还在"——2026-10-30 那次双路由事故就是这么来的，
    所以这条断言同时钉住仓库里没有它。
    """
    assert not LEGACY_CAMPUS_GROUP.exists(), (
        f"(campus) 组又回来了：{LEGACY_CAMPUS_GROUP} —— 会和 (console)/tools 撞路由"
    )


def test_back_link_is_gone_entirely():
    """「← 回首页」整块删除，不是隐藏。

    它 2026-11-07 被加进来的唯一理由是：这一页不套外壳、宽屏没有侧栏、
    PWA 独立窗口没有后退键 —— 不给入口就把人关在页面上。
    并进 (console) 之后三个前提全部消失（宽屏有侧栏、手机有 tab），
    再留着就是一个占首屏、功能重复的按钮。

    ★ 先剥注释再判：文件里保留了说明"为什么删"的注释，
      不剥的话注释反而会让这条断言红。
    """
    hub = _strip_comments(_read(TOOLS_HUB))
    assert "campus-back" not in hub and "回首页" not in hub, (
        "工具集页还留着返回入口 —— 侧栏和底部 tab 已经是出路了"
    )
    css = _strip_comments(_read(CAMPUS_CSS))
    for dead in (".campus-bar", ".campus-back", ".campus-brand"):
        assert dead not in css, f"{dead} 的样式还留着：DOM 都没了，它只会误导下一个人"


def test_tools_page_does_not_build_its_own_scroll():
    """工具集不许再自带滚动容器 —— 否则是外壳里套外壳。

    `.campus-scroll` 是 `height:100dvh`，比父容器（外壳扣掉内边距后）还高：
    结果是一条内层滚动条 + 一条外层滚动条，滚到底还会把滚动接力给外层
    （iOS 上表现为整页弹一下）。底部让位也会同时存在两份，改一处忘一处
    就是"最后一条永远压在 tab 底下"。
    """
    hub = _strip_comments(_read(TOOLS_HUB))
    assert "campus-scroll" not in hub, "工具集组件里还有 .campus-scroll：双层滚动"
    css = _strip_comments(_read(CAMPUS_CSS))
    assert ".campus-scroll" not in css, ".campus-scroll 的规则还在（不再有元素用它）"


def test_modal_scroll_lock_follows_the_scroll_container():
    """弹窗锁滚动必须打到**真正滚的那一层**。

    `scrollLock.ts` 只负责给 body 打 `campus-modal-open`，锁哪一层由 CSS 决定。
    它原来打的是 `.campus-scroll`（页面自己的容器），容器搬走后这一条会**静默失效**：
    弹窗开着，背后的工具集照样滚 —— 没有报错、没有红，只有手感不对。

    所以断言两半：类还在打、目标已经是 `.console-scroll`。
    """
    lock = _read(SRC / "campus" / "logic" / "scrollLock.ts")
    assert "campus-modal-open" in _strip_comments(lock), (
        "锁滚动的类名被改了，CSS 那条选择器就再也命中不到"
    )

    css = _strip_comments(_read(CAMPUS_CSS))
    assert "body.campus-modal-open .console-scroll" in css, (
        "锁滚动的目标没跟着搬：弹窗打开时背后的页面还能滚"
    )


def test_gpa_table_anchors_its_sr_only_label():
    """GPA 表格必须自带定位基准，否则它会把整个文档撑高 26px。

    证据链（2026-11-13 并进外壳后实测）：
      · /tools 的 document.scrollHeight 是 870，window.scrollTo 真能滚 26px；
      · 同样外壳下的 /reminders 与 /settings 都是 844 / 滚不动 —— 不是外壳的锅；
      · 溢出元素只有一个：GPA 表头里 Tailwind 的 `sr-only`（1px，position:absolute）。
        表格没有定位祖先时它相对**初始包含块**定位，静态位置落在第 869px。

    html 是 overflow:hidden 的固定视口，这一截滚轮滚不动，但**程序能滚**：
    焦点跳到视口外的输入框时浏览器会把整页推上去，fixed 的底部 tab 会跟背景错位。
    """
    rule = _css_rule(_strip_comments(_read(CAMPUS_CSS)), ".campus-table")
    assert "position: relative" in rule, (
        "GPA 表格没有定位基准：表头那个 sr-only 会把文档撑高，整页能被程序滚动"
    )
