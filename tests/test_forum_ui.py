"""论坛中心的源码级不变量。

这一批断言全在"改坏了也不会报错"的位置上 —— 编译过、接口 200、页面能开，
但用户看到的东西是错的。每一条都对应一次真实的踩坑或一次明确的取舍：

  1. 整行可点 + 行内有链接 → 必须用**覆盖层链接**，不能用 `<a>` 包内容
     （`<a>` 里嵌 `<button>` 是非法嵌套，移动端会连带跳转）。
  2. 覆盖层链接会盖住同级链接 → 来源库 chip 必须抬 z-index，否则点不到。
  3. `.fm-root` 只写 min-height → 整页滑不动（html/body 都是 overflow:hidden）。
  4. 区块标记里的字必须 `nowrap`，否则两个字会被挤成上下两行，方块变一条。
  5. 楼层样式必须复用 `.kb-post`，不许在 forum.css 里再抄一套。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

FORUM_PAGE = SRC / "app" / "forum" / "page.tsx"
FORUM_DETAIL = SRC / "app" / "forum" / "[postId]" / "page.tsx"
FORUM_CSS = SRC / "app" / "styles" / "forum.css"
GLOBALS = SRC / "app" / "globals.css"
NAV = SRC / "lib" / "nav.ts"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _css_rule(src: str, selector: str) -> str:
    """取某个选择器的第一条声明块（够用了，这些选择器都只出现一次）。"""
    i = src.find(selector + " {")
    assert i >= 0, f"CSS 里找不到 {selector}"
    j = src.index("}", i)
    return src[i:j]


def test_row_is_clickable_via_an_overlay_link_not_a_wrapper():
    """整行可点必须靠末尾那张自闭合的覆盖层链接。

    `<a>` 里嵌 `<button>` 是非法嵌套（来源库 chip 也是 `<a>`），
    移动端会出现"点收藏连带跳走"。
    """
    s = _read(FORUM_PAGE)
    assert re.search(r'className="fm-hit"', s), "帖子行没有覆盖层链接"
    # 自闭合：以 /> 结尾，中间没有 children
    m = re.search(r"<Link\s+[^>]*className=\"fm-hit\"[^>]*/>", s, re.S)
    assert m, "覆盖层链接必须自闭合，不能包住内容"
    assert "</Link>" not in m.group(0)


def test_source_chip_sits_above_the_overlay():
    """来源库 chip 要抬 z-index，否则被整行的覆盖层挡住，点它等于点进帖子。"""
    css = _read(FORUM_CSS)
    rule = _css_rule(css, ".fm-src")
    assert "z-index" in rule, "来源库 chip 没抬层级，点不动"
    assert "position: relative" in rule, "z-index 对 static 元素不生效"
    hit = _css_rule(css, ".fm-hit")
    assert re.search(r"z-index:\s*1\b", hit), "覆盖层层级变了，chip 的 z-index 要跟着调"


def test_root_scrolls_instead_of_growing():
    """`.fm-root` 必须是固定高度 + 自己滚。

    只写 min-height 的话元素被内容撑高，scrollHeight == clientHeight，
    而 html/body 都是 overflow:hidden —— 浏览器和容器都滚不了，整页卡死。
    """
    rule = _css_rule(_read(FORUM_CSS), ".fm-root")
    assert re.search(r"height:\s*100dvh", rule), "少了 height:100dvh"
    assert "overflow-y: auto" in rule


def test_block_mark_text_does_not_wrap():
    """左栏方块里的两个字必须横排一行放得下。

    34px 宽 + 11px 字 + 无 nowrap 时，"答疑"会被挤成上下两排，方块变成一根
    竖条 —— 这是效果图第一版就踩过的。所以这里两条一起钉：不换行 **且** 宽度够。
    """
    rule = _css_rule(_read(FORUM_CSS), ".fm-mark")
    assert "white-space: nowrap" in rule
    m = re.search(r"width:\s*(\d+)px", rule)
    assert m, ".fm-mark 没写固定宽度，会被内容撑成不同大小"
    assert int(m.group(1)) >= 34, f"方块只有 {m.group(1)}px，两个字放不下"


def test_floors_reuse_the_board_post_classes():
    """详情页楼层复用 `.kb-post` 系列，不在 forum.css 里另抄一套。

    抄一份的后果是：以后调留言板的高亮色 / 回复按钮尺寸，论坛这里悄悄不跟着变。
    """
    detail = _read(FORUM_DETAIL)
    for cls in ("kb-post", "kb-post-h", "kb-post-b", "kb-post-act"):
        assert cls in detail, f"详情页没有复用 {cls}"

    css = _read(FORUM_CSS)
    # 允许出现 `.fm-floors > .kb-post` 这种覆盖，但不允许重新定义 .kb-post 本体
    assert not re.search(r"^\.kb-post\s*\{", css, re.M), "forum.css 重定义了 .kb-post"


def test_mobile_cat_picker_exists():
    """左栏在 ≤880 隐藏，必须有横向 chip 条补位，否则手机上选不了板块。"""
    page = _read(FORUM_PAGE)
    assert "fm-cats-m" in page, "手机上没有板块选择器"
    css = _read(FORUM_CSS)
    block = css[css.index("@media (max-width: 880px)") :]
    assert ".fm-cats-m" in block and "display: flex" in block, "chip 条在手机上没显示出来"
    assert ".fm-side" in block and "display: none" in block, "手机上左栏没隐藏"


def test_forum_css_loads_after_library_css():
    """forum.css 依赖 library.css 的顺序（楼层覆盖靠同优先级后到者赢）。"""
    g = _read(GLOBALS)
    assert g.index('"./styles/library.css"') < g.index('"./styles/forum.css"'), (
        "forum.css 被放到了 library.css 前面，楼层覆盖会失效"
    )


def test_forum_is_reachable_from_the_sidebar():
    """有页面没入口 = 没人能发现。标签必须两个字（手机 3 列网格会挤成两行）。"""
    nav = _read(NAV)
    assert 'href: "/forum"' in nav
    m = re.search(r'\{ href: "/forum"[^}]*label: "([^"]+)"', nav)
    assert m, "nav.ts 里的 /forum 缺少 label"
    assert len(m.group(1)) == 2, "导航标签超过两个字会在手机上换行、把导航撑高"


def test_stats_text_carries_a_unit():
    """统计数字后面必须带单位。

    线上第一版写的是「今日 8」，后面什么都没有 —— 截图里看着像文字被截断了，
    实际是文案漏了「条」。这种错误编译不会报、接口也不会错，只有人看见。
    """
    s = _read(FORUM_PAGE)
    # 注释里也会出现这些字样，先剥掉再判 —— 断言被注释误伤过一次，别再来一次
    code = re.sub(r"\{/\*.*?\*/\}", "", s, flags=re.S)
    assert "今日 8" not in code  # 防的是"数字直接结尾"那种写法
    # 两处（左栏底部 / 工具条右侧）都要是「今日新增 N 条」的形状
    assert code.count("今日新增") >= 2, "统计文案又退回成没有单位的「今日 N」了"
    for m in re.finditer(r"今日新增 \{fmtNum\(stat\.today\)\}", code):
        tail = code[m.end() : m.end() + 6]
        assert tail.startswith(" 条"), f"「今日新增 N」后面缺单位，实际是：{tail!r}"


def test_notice_bar_is_keyed_by_post_id():
    """顶部公告条的「关掉过」必须按公告 id 存。

    存成 true/false 的话，关过一次以后所有人**永远**看不到任何公告了 ——
    这类"一次选择变永久开关"的错误不会报错，只会让人以为公告功能没做。
    """
    s = _read(FORUM_PAGE)
    assert "NOTICE_KEY" in s, "公告条的本地标记不见了"
    i = s.index("const closeNotice")
    seg = s[i : i + 500]
    assert "String(notice.id)" in seg, "关掉时存的不是公告 id"
    # 只断言"id 来自 notice"是不够的：把写入值换成常量 "1"，那条断言照样绿
    # （反证时真踩到了）。必须咬住 setItem 的第二个参数就是 id。
    assert "localStorage.setItem(NOTICE_KEY, id)" in seg, (
        "写进本地的是别的值而不是这条公告的 id，下一条公告不会再出现"
    )


def test_notice_bar_shows_the_latest_one_only():
    """横幅只放最新一条：两条叠在一起就成了"公告区"，横幅反而没人读。"""
    s = _read(FORUM_PAGE)
    assert "pinned[0]" in s
    # 横幅里的标题、链接都必须来自同一条 notice
    seg = s[s.index('<div className="fm-notice">') : s.index("<div className=\"fm-wrap\">")]
    assert "notice.title" in seg and "{notice.id}" in seg
    assert "pinned[1]" not in seg, "横幅里塞了第二条公告，横杠会变成两行"


def test_pinned_rows_render_before_normal_topics():
    """置顶必须真的排在前面 —— `pinned` 只是数据，顺序由渲染决定。"""
    s = _read(FORUM_PAGE)
    a = s.index("{pinned.map((t) => renderRow(t, true))}")
    b = s.index("{topics.map((t) => renderRow(t))}")
    assert a < b, "公告被渲染到了普通帖子后面，置顶等于没做"


def test_compose_does_not_preselect_a_board_i_cannot_post_to():
    """点进「站务公告」再点发帖：下拉里没有这个选项，不能把它带进去。

    带进去的后果是 select 的 value 不在 options 里，框里显示空白，
    点发布才被后端 403 —— 用户会以为是坏了。
    """
    s = _read(FORUM_PAGE)
    i = s.index("const openCompose")
    seg = s[i : i + 700]
    assert "postable" in seg, "默认选板块时没检查这个板块我能不能发"


def test_solved_feature_leaves_no_trace_in_the_ui():
    """「已解决」在界面上必须一处都不剩。

    这类功能下线最容易漏的是中间那层：页面上的按钮删了，`api.ts` 里的封装还在，
    下一个人照着 `setForumSolved` 去写页面，就把后端已经删掉的端点又调起来了。
    """
    assert '"unsolved"' not in _read(FORUM_PAGE), "排序里还留着「还没解决」"
    assert "fm-tag ok" not in _read(FORUM_PAGE), "列表里还留着「已解决」标签"
    assert "setForumSolved" not in _read(FORUM_DETAIL), "详情页还在调标记接口"
    assert "can_solve" not in _read(FORUM_DETAIL), "详情页还依赖 can_solve 字段"
    assert "setForumSolved" not in _read(SRC / "lib" / "api.ts"), "api.ts 里还留着接口封装"
    assert "fm-solve" not in _read(FORUM_CSS), "forum.css 里还留着按钮样式"
