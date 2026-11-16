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
# 论坛页（一级 tab，但不在 (console) 路由组里 —— 2026-11-16 补挂底栏）
FORUM_TSX = SRC / "app" / "forum" / "page.tsx"
FORUM_CSS = SRC / "app" / "styles" / "forum-tb.css"
# 对话页左栏（桌面轨道 / 手机抽屉）。它不在 (console) 外壳里，自己拿一份导航 ——
# 2026-11-15 之前是硬编码 4 项，手机上进得来出不去。
SIDEBAR_TSX = SRC / "components" / "Sidebar.tsx"
COMPONENTS_CSS = SRC / "app" / "styles" / "components.css"
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


# 一条 NAV 记录：`{` 到 `}` 之间没有嵌套花括号，里面带一个 `href: "..."`。
# 先剥注释再匹配，否则"解释某个入口为什么被移出"的注释会被当成真记录
# （`{ href: "/x" ... }` 这种就写在注释里过）。
_NAV_ENTRY_RE = re.compile(r"\{[^{}]*href:\s*\"([^\"]+)\"[^{}]*\}")


def _nav_entries(nav: str) -> dict[str, str]:
    """把 nav.ts 解析成 {href: 这条记录的原文}，用来在测试里复算筛选结果。

    为什么不直接在 TS 里跑：前端没有测试运行器，而"抽屉里到底有几项"
    恰恰是这次事故的判据。用 6 行 Python 复算，比"断言源码里出现了某个函数名"
    更能发现"某个入口悄悄消失了"。
    """
    out: dict[str, str] = {}
    for m in _NAV_ENTRY_RE.finditer(_strip_comments(nav)):
        out[m.group(1)] = m.group(0)
    # 硬约束：解析不到就说明正则失效了，不能靠"空集合也满足所有条件"混过去
    # 12 = 2026-11-15 现状（13 减掉已下线的「学习路径」）。这条路只防"正则失效"，
    # 不防"少了一条" —— 少一条由上面对 /knowledge /forum /checkin 的点名断言兜底。
    assert len(out) >= 12, f"nav.ts 只解析出 {len(out)} 条，正则可能失效：{sorted(out)}"
    return out


def _ts_func(src: str, name: str) -> str:
    """取 `export function name(...) { ... }` 的函数体（朴素括号计数）。

    用来验"筛选逻辑本身"：只断言 nav.ts 里的**数据**是对的，还不够 ——
    把 `visibleNav(isAdmin)` 写成 `visibleNav(true)`（等于对所有人展示开发者页面），
    数据一个字没变，所有数据断言照样绿。反证第 ④ 条就是这么漏掉的。
    """
    i = src.find(f"export function {name}(")
    assert i >= 0, f"nav.ts 里没有 {name}()"
    brace = src.index("{", i)
    depth = 0
    for j in range(brace, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[brace + 1 : j]
    raise AssertionError(f"{name}() 没有闭合")


def _chat_rail(entries: dict[str, str], is_admin: bool, narrow: bool) -> list[str]:
    """`chatRailNav()` 的等价实现：排除「对话」自己、按 adminOnly / desktopOnly 过滤。"""
    rails: list[str] = []
    for href, body in entries.items():
        if href == "/":
            continue
        if "adminOnly: true" in body and not is_admin:
            continue
        if narrow and "desktopOnly: true" in body:
            continue
        rails.append(href)
    return rails


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
    # ★ 必须剥注释：nav.ts 里留着一行被注释掉的「学习路径」（含 mobileTab: 2），
    #   不剥会把它算成一个格位 —— "注释掉了但还在计数"这类假红/假绿最难查。
    nav = _strip_comments(_read(NAV_TS))
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


def test_learning_path_is_off_the_nav_but_its_page_survives():
    """「学习路径」的**导航入口**已按用户要求下线（2026-11-15），页面本身保留。

    来龙去脉值得记一笔：2026-11-14 为了让它有手机一级入口，专门把底部 tab
    从"取前四项"改成显式的 `mobileTab: 2`；一天后用户要求把入口整个拿掉。
    两件事都成立 —— 入口是产品决策，路由与 12 关的判定逻辑是资产，
    后者一行都不能跟着删（`service/path.py` + `tests/test_path.py` 仍在跑）。

    所以这条同时守两头：
      · 入口不许悄悄回来（加回来就要同步 TAB_PRIMARY_COUNT 与 CSS 列数，
        否则底部 tab 中间会留一个空格位 —— tests 会红在别处，很难定位）
      · 页面不许跟着被删（删入口顺手删页面，是"清理"最容易过头的方向）
    """
    nav = _strip_comments(_read(NAV_TS))
    assert 'href: "/path"' not in nav, (
        "「学习路径」的导航入口又回来了：请同步 mobileTab 编号 / TAB_PRIMARY_COUNT / CSS 列数"
    )
    page = SRC / "app" / "(console)" / "path" / "page.tsx"
    assert page.exists(), "学习路径的页面被连带删掉了 —— 入口下线不等于功能下线"


def test_admin_dashboard_has_a_mobile_entry():
    """管理员页面「看板」必须能在手机上进得去（2026-11-15 事故复盘）。

    事故：2026-11-14 的 iOS 内测把 `/dashboard` 标成 `desktopOnly`，当时写在
    方案里的理由是"已是 adminOnly，手机上永不需要"。结果管理员在手机上点开
    「更多」只有 4 项，找不到看板，报「数据面板再次无法显示」。
    查下来：后端 `/api/admin/overview` 200、`/api/auth/me` 的 `is_admin` 为 true、
    390 宽直接打开 `/dashboard` 渲染 13 张卡且横向溢出 0 —— 坏的只有这一个入口，
    而**所有测试照样绿**（拓扑没坏，入口消失了）。

    这条守的是"入口存在"，不是"页面好看"：
      · 不能 `desktopOnly`（会被 mobileMoreNav 过滤，手机上彻底消失）
      · 不能 `mobileTab`（会挤掉一个高频功能的一级格位）
      · 必须 `adminOnly`（普通用户不该看到这个入口）
    """
    nav = _strip_comments(_read(NAV_TS))
    m = re.search(r'\{[^{}]*href: "/dashboard"[^{}]*\}', nav, re.S)
    assert m, "nav.ts 里找不到 /dashboard 那一条，正则失效"
    entry = m.group(0)
    assert "desktopOnly" not in entry, (
        "看板又被移出移动端了 —— 管理员在手机上再也看不到数据面板（2026-11-15 报过）"
    )
    assert "mobileTab" not in entry, "看板不该占底部 tab 格位，它是低频的管理页"
    assert "adminOnly: true" in entry, "看板没有 adminOnly，普通用户会看到入口"


def test_more_panel_items_all_carry_a_hint():
    """「更多」面板里每一项都要有一句用途说明（2026-11-15）。

    起因：面板原先是 3 列网格，4 项排成 3+1、第二行空两格，用户的原话是
    "这个二级导航显示过于空旷"。空的另一半原因在内容本身：格子里只有两个字，
    「工具集」到底装了 GPA 还是番茄钟，看名字猜不出来。现在两列 + 每格一句 13px 说明。

    没有 hint 的项会退回"只有名称"的矮格子，把两列的节奏打断；
    hint 写成复述（「资料库」下面再写「资料库管理」）等于白占一行，所以也禁。
    """
    nav = _strip_comments(_read(NAV_TS))
    checked = 0
    for m in re.finditer(r"\{([^{}]*href:\s*\"[^\"]+\"[^{}]*)\}", nav, re.S):
        body = m.group(1)
        href = re.search(r'href:\s*"([^"]+)"', body).group(1)
        # 进了底部 tab 的、或彻底移出移动端的，都不在「更多」面板里
        if "mobileTab" in body or "desktopOnly" in body:
            continue
        label_m = re.search(r'label:\s*"([^"]+)"', body)
        label = label_m.group(1) if label_m else ""
        assert "hint:" in body, f"「更多」里的 {href} 没有 hint，面板会缺一块内容"
        hint_m = re.search(r'hint:\s*"([^"]+)"', body)
        assert hint_m, f"{href} 的 hint 是空字符串"
        hint = hint_m.group(1)
        assert hint != label, f"{href} 的 hint 只是复述了名称（{hint}）"
        assert len(hint) <= 12, f"{href} 的 hint 太长（{len(hint)} 字），窄格会被省略号截掉"
        checked += 1
    # 扫描型断言必须同时断言"扫到了几个"——路径/写法一变就红（topics/05 第 32 条）
    assert checked >= 4, f"只扫到 {checked} 个「更多」项，正则可能已经失效"


def test_more_panel_is_two_columns_not_three():
    """面板必须两列等宽（2026-11-15）：三列时每格只有 114px，塞不下用途说明。

    锁的是结果（两列）而不是写法，但 `flex: 0 0 calc(50%` 就是它的实现；
    如果有人换回 `grid-template-columns: repeat(3`，说明这次改动被整个回退了。
    """
    css = _strip_comments(_read(CONSOLE_CSS))
    grid = _css_rule(css, ".tab-sheet-grid")
    assert "flex-wrap: wrap" in grid, "面板不是 flex-wrap，末行那一项没法居中"
    assert "repeat(3" not in grid, "面板又变回三列了 —— 114px 一格放不下用途说明"
    item = _css_rule(css, ".tab-sheet-item")
    assert "calc(50%" in item, "面板项不是半宽，两列会退回三列"


def test_console_only_pages_are_kept_off_mobile():
    """桌面专属只剩「四六级」与「工作流」，委托 / 总结**必须**回到移动端。

    2026-11-14 决定把 每日委托 / 总结 / 四六级 只留桌面；
    2026-11-15 晚用户报「我的手机端的每日总结和每日委托去哪里了？看不到」，
    前两个被撤回移动端（详见 nav.ts 那两条的注释），四六级保持桌面专属。
    所以这条现在守两件事：

      · `/cet`、`/flow` 仍然标着 desktopOnly —— 四六级要排计划做模考，
        工作流要编辑 200 字符的 JSON，窄屏确实做不了；
      · `/delegate`、`/report` **不许** 再标 desktopOnly —— 这是撤回点，
        改回去等于把同一个 bug 再种一遍：手机上它们只有首屏卡这一条路，
        而首屏卡片只在 `messages.length === 0` 时渲染，聊过一句就全没了。
    """
    nav = _strip_comments(_read(NAV_TS))
    for href in ("/cet", "/flow"):
        m = re.search(r'\{[^{}]*href: "' + href + r'"[^{}]*\}', nav, re.S)
        assert m, f"nav.ts 里找不到 {href}，它应该还在桌面侧栏里"
        entry = m.group(0)
        assert "desktopOnly: true" in entry, f"{href} 没有标 desktopOnly，手机上会重新出现"

    for href in ("/delegate", "/report"):
        m = re.search(r'\{[^{}]*href: "' + href + r'"[^{}]*\}', nav, re.S)
        assert m, f"nav.ts 里找不到 {href}，它应该还在导航清单里"
        entry = m.group(0)
        assert "desktopOnly" not in entry, (
            f"{href} 又被标成 desktopOnly 了 —— 手机上会彻底没有入口"
            "（2026-11-15 用户报过「手机端看不到每日总结和每日委托」）"
        )
        assert "mobileTab" not in entry, (
            f"{href} 不该占底部 tab 格位：底栏只有 对话/论坛/打卡 + 更多，"
            "它该落在「更多」面板里"
        )


# ==================== ④ 首页挂底部 tab（2026-11-16 第二次反转）====================


def test_home_mounts_the_tabbar_and_gives_it_height():
    """首页（/）挂底部 tab，且高度必须与它成对。

    ★ 这条的方向改过两次，先看清楚再动手：
      · 2026-11-13 下午：为了让「对话」入口常驻，首页单独挂过一次 tab；
      · 同一天晚上：用户看过真页面后要求撤掉 —— 「主页面下面的 tag 去掉吧，
        影响整体性」。当时的形状是**满屏卡片 + 底部压一条独立的 50px 导航**，
        两条边线挨着，卡片看起来被切成"内容 + 工具栏"两截；
      · 2026-11-16：移动端 UI 评审认定「5 个一级入口里有 2 个没有底栏
        （对话 / 论坛）」是导航模型分裂的根因，用户要求按 P0 修复。
        恢复的做法不是把那条导航照抄回来，而是让导航**并进输入区**：
        去掉自己的投影（variant="dock"）、由输入区那条线当唯一分隔。

    断言查三件事，缺一都不算接对：
      · 注释剥掉后确实有 `<ConsoleTabBar`（只写 import 的话组件不在 DOM 里，
        而字符串检查照样通过 —— 反证抓到过这一条）；
      · 用的是 `variant="dock"`（否则首页会出现两条分隔线，回到"切成两截"）；
      · fx.css 里首页窄屏高度**减掉** `--tabbar-h` 与底部安全区
        （不减 = 底部空出 50px 白边，比被压住还难看；只减 tabbar-h 不减安全区
        = iPhone 上多出 34px。这是 11-13 撤 tab 那轮的同一个坑，方向反过来）。
    """
    home = _strip_comments(_read(HOME_TSX))
    assert "<ConsoleTabBar" in home, (
        "首页没有挂底部 tab：五个一级入口里「对话」是最常用的一个，"
        "没有底栏就意味着用户在三个页面之间要换三种导航模型"
    )
    assert 'variant="dock"' in home, (
        "首页的 tab 没用 dock 变体：它和输入区中间会多出一条投影，"
        "又变成 11-13 那次「切成两截」的观感"
    )

    block = _media_block(_read(FX_CSS), "@media (max-width: 899px)", "workbench-main")
    heights = _all_decls(_css_rule(block, "main.workbench-main"), "height")
    assert heights, "首页工作台没有设高度"
    for h in heights:
        assert "var(--tabbar-h)" in h, (
            f"首页挂了 tab 却没扣它的高度（{h}）—— 底部会空出一条 50px 的白边"
        )
        assert "safe-area-inset-bottom" in h, (
            f"首页高度没扣底部安全区（{h}）—— iPhone 上会多出 34px（导航自己让过一次）"
        )
        # ★ 2026-11-14（第九批）：纸铺到视口边缘，上下各 8px 的外边距归零，
        #   这里查的是"没有多扣外边距"，而不是某个具体数字。
        assert not re.search(r"-\s*16px", h), (
            f"首页高度还在减上下 8px 外边距（{h}）—— 纸已贴边，减了会空一条"
        )
        assert "safe-area-inset-top" in h, (
            f"首页高度没减顶部安全区（{h}）—— 刘海会把内容压住"
        )


def test_forum_mounts_the_tabbar_and_gives_it_height():
    """论坛（/forum）是一级 tab，必须有自己的底栏与让位。

    它是 `mobileTab: 2` 的入口，但页面不在 `(console)` 路由组里，
    从上线起就一直没挂过底栏 —— 用户在论坛想切「打卡」「更多」，
    只能先点左上角回工作台再绕一圈（2026-11-16 评审实测 .tabbar 为 null）。

    ★ 宽度断点必须是 900：`.tabbar` 在 ≤900 才出现，写成本文件主体的 760
      会在 761–900 这段（大屏手机横屏 / 小平板竖屏）出现"有导航、没让位"。
    """
    forum = _strip_comments(_read(FORUM_TSX))
    assert "<ConsoleTabBar" in forum, "论坛页没有挂底部 tab：它是一级入口，却没有底栏"

    css = _read(FORUM_CSS)
    assert "@media (max-width: 900px)" in css, (
        "论坛样式里没有 900 这一档：底栏在 ≤900 出现，让位断点必须与它对齐"
    )
    block = _media_block(css, "@media (max-width: 900px)", "tb-root")
    heights = _all_decls(_css_rule(block, ".tb-root"), "height")
    assert heights, "论坛的滚动容器 .tb-root 没有扣底部导航的高度"
    for h in heights:
        assert "var(--tabbar-h)" in h, (
            f"论坛 .tb-root 高度没扣 --tabbar-h（{h}）—— 最后一条帖子会被压在导航底下"
        )
        assert "safe-area-inset-bottom" in h, (
            f"论坛 .tb-root 高度没扣底部安全区（{h}）—— iPhone 上滚不到底"
        )


def test_home_tabbar_lives_inside_main_so_overlays_can_cover_it():
    """首页底栏必须挂在 `<main>` **里面** —— 它决定浮层能不能盖住底栏。

    `main.workbench-main` 带 `position:relative; z-index:1`，本身就是一个层叠
    上下文：里面的 z-index 写得再大，整棵树对外也只有一层。底栏原来挂在 main
    外面（根上下文，z-index 40），于是压在左抽屉（70）、资料库面板（70）、
    抽屉遮罩（60）之上 —— 抽屉与面板最下面 73px 被切掉，连遮罩都盖不住底栏
    （线上 393×852 实测：抽屉底部 elementFromPoint 命中 `.tabbar-item`）。

    挪进 main 之后三者同处一个上下文：40（底栏）< 60（遮罩）< 70（抽屉/面板），
    抽屉打开时底栏被遮罩压暗、被面板盖住，和 iOS 的 sheet 一样。

    这条只能靠源码顺序断言：层级错了编译不报错、接口全 200、桌面端还看不出来
    （底栏 ≤900 才出现），只有手机上开着抽屉的那一下才露出来。
    """
    home = _strip_comments(_read(HOME_TSX))
    assert "<ConsoleTabBar" in home, "首页没有挂底部 tab"
    i_tab = home.index("<ConsoleTabBar")
    i_main_end = home.index("</main>")
    assert i_tab < i_main_end, (
        "底部 tab 又挂到 <main> 外面了：main 是层叠上下文（z-index:1），"
        "外面的 z-index 40 会压住左抽屉（70）、资料库面板（70）与遮罩（60），"
        "抽屉最下面 73px 会被切掉 —— 而且桌面端看不出来，只有手机上开着抽屉才露"
    )


def test_tabbar_z_index_sits_below_the_drawers():
    """底栏 40 < 遮罩 60 < 抽屉/面板 70：三个数字必须保持这个顺序。

    上一条钉的是"在不在同一个上下文里"，这一条钉"同上下文里的先后"。
    两个都要：只挪位置、有人把抽屉的 70 改成 30，抽屉照样会被底栏切开；
    只改数字、底栏还在 main 外面，70 也照样被 1 压住。改动前实测到的正是后者。
    """
    console = _read(CONSOLE_CSS)
    tab_block = _media_block(console, "@media (max-width: 900px)", ".tabbar")
    tab_rule = _css_rule(tab_block, ".tabbar")
    assert "position: fixed" in tab_rule
    tab_z = _all_decls(tab_rule, "z-index")
    assert tab_z, "底栏没有 z-index：它会掉到浮层下面，抽屉打开时底栏穿在抽屉上"
    assert tab_z[0] == "40", f"底栏 z-index 变成了 {tab_z[0]}（约定是 40）"

    fx = _read(FX_CSS)
    back_z = _all_decls(_css_rule(fx, ".drawer-backdrop"), "z-index")[0]
    left_z = _all_decls(
        _css_rule(_media_block(fx, "@media (max-width: 899px)", ".side-left"), ".side-left"),
        "z-index",
    )[0]
    right_z = _all_decls(
        _css_rule(_media_block(fx, "@media (max-width: 1279px)", ".side-right"), ".side-right"),
        "z-index",
    )[0]
    for name, z in (("遮罩", back_z), ("左抽屉", left_z), ("资料库面板", right_z)):
        assert int(tab_z[0]) < int(z), (
            f"底栏（{tab_z[0]}）没有低于{name}（{z}）：{name}会被底栏切开一条"
        )


def test_home_lets_the_content_up_at_exactly_900():
    """正好 900 宽时首页也要让出底栏的高度。

    底栏的断点是 ≤900（console.css 的整块手机外壳，论坛测试也按 900 对齐），
    而窄屏布局切换是 ≤899 —— 差这 1px。实测 900×900：main 底边 900、底栏顶边
    849，输入区被压住 51px。这里只补高度：把 899 那档改成 900 会让侧栏在 900
    变成抽屉，而 JS 的 viewport 仍算 medium、不渲染汉堡键，抽屉永远打不开。
    """
    fx = _read(FX_CSS)
    block = _media_block(fx, "@media (min-width: 900px) and (max-width: 900px)", "workbench-main")
    heights = _all_decls(_css_rule(block, "main.workbench-main"), "height")
    assert heights, "900 这一档没有给首页设高度"
    for h in heights:
        assert "var(--tabbar-h)" in h, f"900 宽时首页高度没扣底栏（{h}）"


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


def test_home_rail_is_derived_from_the_shared_nav_list():
    """对话页左栏不许再自己写一份清单（2026-11-15 事故）。

    事故经过：`Sidebar.tsx` 里硬编码了 4 个入口（委托 / 总结 / 待办 / 设置），
    和 `lib/nav.ts` 毫无关系。而对话页在手机上**没有底部 tab** —— 这一栏就是
    唯一的模块入口。于是手机上资料库 / 论坛 / 学习路径 / 打卡 / 工具集 / 看板
    全部无路可走；更荒谬的是硬编码里那两个「委托 / 总结」在 NAV 里恰恰标着
    `desktopOnly`（手机上彻底不出现），同一份清单在两处给出了相反的答案。

    这条守的是"同源"本身：一旦有人又把 href 写回组件里，两处清单立刻可以再次
    漂移 —— 而漂移的表现是"页面全都正常、接口全都 200、某个入口就是不见了"。
    """
    sb = _strip_comments(_read(SIDEBAR_TSX))
    assert "@/lib/nav" in sb, "对话页左栏没有引用共享导航清单"
    assert "chatRailNav(" in sb, (
        "对话页左栏没有走 chatRailNav()：自己筛一遍必然漏掉 adminOnly / desktopOnly 的语义"
    )
    # 筛选逻辑本身也要验：只验 nav.ts 的数据，把 visibleNav(isAdmin) 改成
    # visibleNav(true) 这种"对所有人展示开发者页面"的错误一个都拦不住（反证第 ④ 条）。
    body = _ts_func(_strip_comments(_read(NAV_TS)), "chatRailNav")
    assert "visibleNav(isAdmin)" in body, "chatRailNav 没有按 isAdmin 过滤，管理员入口会漏给别人"
    assert 'it.href !== "/"' in body, "chatRailNav 把「对话」自己也列进去了（当前页不该出现在导航里）"
    assert re.search(r"!\s*\(\s*narrow\s*&&\s*it\.desktopOnly\s*\)", body), (
        "chatRailNav 没有在窄屏排除 desktopOnly —— 手机上会冒出 /flow 这种窄屏做不了的页面"
    )
    # 组件里再出现任何一条站内路径字面量，就是"第二份清单"重新长出来的信号
    stray = re.findall(r"\"(/[a-z][a-z0-9/_-]*)\"", sb)
    assert not stray, f"Sidebar.tsx 里还有硬编码的站内路径：{stray}"


def test_home_rail_covers_every_mobile_destination():
    """手机上能到的地方，抽屉里必须都有 —— 一个都不能少。

    2026-11-15 实测：抽屉里只有 4 项，`/knowledge`、`/forum`、`/path`、
    `/checkin`、`/tools`、`/dashboard` 全部缺失。所以这里不是抽查几个，
    而是把 nav.ts 复算一遍求差集：手机可达集合 - 抽屉集合 = ∅。
    同时反向确认 `desktopOnly` 在窄屏确实被排除（否则手机上会冒出
    /flow 这种"窄屏根本做不了"的页面）。
    """
    entries = _nav_entries(_read(NAV_TS))
    for is_admin in (False, True):
        rail = _chat_rail(entries, is_admin, narrow=True)
        reachable = [
            href
            for href, body in entries.items()
            if href != "/"
            and "desktopOnly: true" not in body
            and ("adminOnly: true" not in body or is_admin)
        ]
        assert reachable, "手机可达集合为空，解析肯定错了"
        missing = sorted(set(reachable) - set(rail))
        assert not missing, f"手机抽屉里缺这些入口（管理员={is_admin}）：{missing}"
        # 三个最容易"顺手被移出移动端"的页面单独点名，可读性优先于求差集
        for href in ("/knowledge", "/forum", "/checkin", "/reminders"):
            assert href in rail, f"手机抽屉里没有 {href}"
        # 反向：窄屏不该出现桌面专属页
        for href in ("/flow", "/cet"):
            assert href not in rail, f"窄屏抽屉里不该有 {href}（nav.ts 标了 desktopOnly）"
        # ★ 2026-11-15 晚撤回点：委托 / 总结必须回到手机抽屉里。
        #   它们曾经只靠首屏那两张卡当入口，而首屏在聊过一句之后就整块消失。
        for href in ("/delegate", "/report"):
            assert href in rail, f"手机抽屉里又没有 {href} 了"

    # 桌面拿全量：把 12 条都端上来，管理员多出看板与工作流
    desk_admin = _chat_rail(entries, True, narrow=False)
    assert len(desk_admin) == len(entries) - 1, (
        f"桌面轨道应当是「除对话外全量」，实际 {len(desk_admin)}/{len(entries) - 1}"
    )
    for href in ("/delegate", "/report", "/cet", "/flow", "/dashboard"):
        assert href in desk_admin, f"桌面轨道丢了 {href}"
    # 非管理员桌面不应该看见两个开发者页面
    desk_user = _chat_rail(entries, False, narrow=False)
    assert "/flow" not in desk_user and "/dashboard" not in desk_user


def test_home_rail_is_a_full_three_column_grid():
    """模块格是 3 列紧凑格，且**末行不许留空格子**。

    旧版是 flex-wrap + 末行居中，项数余 1 时（管理员手机 7 项 = 3+3+1）会剩下
    孤零零一格，读起来像页面没做完。用户 2026-11-15 的原话是「普通用户缺两个
    入口之后仍要整齐、间距一致、不出现空缺」—— 普通用户那边去掉「学习路径」
    正好 6 项 = 3×2，管理员那边余 1，所以要一个能同时照顾两种数的排法。
    现在换成 grid：余 1 时最后一项 `grid-column: 1 / -1` 通栏；余 2 时那一对居中。
    为什么不用 4 列把它填满：侧栏固定 240px，4 列每格 48px，「每日委托」四个字
    52px 就开始省略号 —— 为一个空格子换来两处截断不值。

    这条同时钉四件事，缺一件"整齐"就只剩一半：
      · 列数是 3（不是 2 / 4）
      · 余 1 的判据在 nav.ts 里算（不用 :nth-child 在 CSS 里猜）
      · 组件真的把那个类挂上去了
      · 图标有底衬（用户要的「填充色」就是这一块）
    """
    css = _strip_comments(_read(COMPONENTS_CSS))
    grid = _css_rule(css, ".side-nav")
    assert "display: grid" in grid, "模块格不是网格 —— 末行补位只能靠 grid-column 表达"
    cols = re.search(r"grid-template-columns:\s*repeat\((\d+)", grid)
    assert cols, f"模块格没有列定义：{grid[:90]}"
    tracks = int(cols.group(1))
    entry = _css_rule(css, ".side-entry")
    span = " ".join(_all_decls(entry, "grid-column"))
    m = re.search(r"span\s+(\d+)", span)
    assert m, f"模块格没有声明跨几条轨道（{span!r}）"
    assert tracks / int(m.group(1)) == 3, (
        f"{tracks} 条轨道 / 每格跨 {m.group(1)} 条 = {tracks / int(m.group(1))} 列，不是 3 列"
    )

    wide = _css_rule(css, ".side-nav--wide-last > .side-entry:last-child")
    assert "grid-column: 1 / -1" in wide, "余 1 时末项没有通栏，7 项会剩下孤零零一格"
    pair = _css_rule(css, ".side-nav--pair-last > .side-entry:nth-last-child(-n + 2)")
    assert "span 3" in pair, "余 2 时末两项没有各占半行，11 项右侧会空一格"

    assert "var(--fs-xs)" in " ".join(_all_decls(entry, "font-size")), (
        "模块格字号不是 --fs-xs（14px 会把「每日委托」截成「每日委…」）"
    )
    assert "nowrap" in entry, "标签允许换行的话，四个字的项会把整行撑高"

    # 图标底衬：NavIcon 是 1.85 描边的空心线，一排十一块排下来只有一层浅灰
    ico = _css_rule(css, ".side-ico")
    assert "var(--accent-soft)" in ico and "26px" in ico, (
        "图标没有浅金底衬 —— 用户要的「填充色」就是这一块，去掉等于把需求撤回"
    )

    body = _ts_func(_strip_comments(_read(NAV_TS)), "railLayout")
    assert re.search(r"count\s*%\s*3", body), "railLayout 没有按 3 列取余"
    assert '"wide"' in body and '"pair"' in body, (
        "railLayout 没同时处理余 1（通栏）和余 2（末两项各半行）"
    )
    sb = _strip_comments(_read(SIDEBAR_TSX))
    assert "railLayout(" in sb, "侧栏没有按 railLayout 算出的列数排"
    assert "side-nav--wide-last" in sb and "side-nav--pair-last" in sb, (
        "侧栏没有把两种补位类都挂到容器上"
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
