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
    """三处让位（内容区、最短页高、首页工作台）都必须用变量，不许再写数字。"""
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

    # 首页工作台的高度在 fx.css 的 899 断点里（不是在 base.css）：
    # base.css 那条先加载，被 fx.css 整条覆盖，改它等于没改 —— 实测过。
    home = _media_block(_read(FX_CSS), "@media (max-width: 899px)", "workbench-main")
    heights = _all_decls(_css_rule(home, "main.workbench-main"), "height")
    assert heights, "首页工作台没有设高度"
    for h in heights:
        assert "var(--tabbar-h)" in h, (
            f"首页工作台这一档高度没扣 tab（{h}）—— 输入框那行提示会压在导航条底下"
        )


def test_token_matches_the_tab_it_describes():
    """变量值必须等于 tab 自身的高度（命中区 + 上下内边）。

    这条是防"改了 tab 项高度、忘了改令牌"。数字对不上时，
    让位与遮挡之间就差那几个像素 —— 而它只在真机上最后一行才会露出来。
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
    # 上下各 4px 内边
    assert total == item_h + 8, (
        f"--tabbar-h={total} 与 tab 项高度 {item_h}+8 不一致，让位会算错"
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
    assert "visibleNav(" in tab and "TAB_PRIMARY_COUNT" in tab, (
        "底部 tab 没有从共享清单切片 —— 前四项必须和桌面侧栏同序"
    )


# ==================== ④ 首页也要有 ====================


def test_home_has_the_same_tabbar():
    """首页（/）不走 ConsoleLayout，必须单独挂一次。

    不挂的表现：从待办点「对话」回到首页，底部那条当场消失，
    用户会以为导航坏了 —— 而这条路径是手机上每天走十几次的。
    """
    home = _read(HOME_TSX)
    # 断言的是**渲染**，不是"导入过"。只留 import 的话组件根本不在 DOM 里，
    # 而字符串检查照样通过（反证时抓到过这一条）。
    assert "<ConsoleTabBar" in home, "首页没有渲染底部 tab：从控制台回首页时导航会消失"
    assert '"@/components/console/ConsoleTabBar"' in home


def test_tabbar_closes_on_navigation():
    """换页要收面板。

    不收的话，从「更多」里点设置，面板会盖在新页面上 —— 看起来像没跳成功。
    """
    tab = _read(TABBAR_TSX)
    assert "setMoreOpen(false)" in tab
    assert "pathname]" in tab or "[pathname]" in tab, "面板的收起没有跟着路由走"
