"""论坛中心的源码级不变量。

这一批断言全在"改坏了也不会报错"的位置上 —— 编译过、接口 200、页面能开，
但用户看到的东西是错的。每一条都对应一次真实的踩坑或一次明确的取舍。

⚠️ **2026-11-14 第 17 轮把列表皮肤从 `fm-*` 换成了贴吧三栏的 `tb-*`**
（`styles/forum-tb.css`），这份文件当时没跟着改，于是出现两种病：
一半断言直接红（`fm-hit`、`fm-cats-m` 这些类名不存在了），另一半**照样绿**
却是在检查一份已经不上屏的死 CSS —— 后者更危险，它让人以为有人在看着。
这次统一把"列表"的断言指到 `forum-tb.css` / `tb-*`，`forum.css` 只保留
它还在负责的部分（详情页外壳 + 楼层复用 `library.css`）。

  1. 列表用**明确的链接**（标题 + 回复），不用覆盖层 `<a>`
     （`<a>` 里嵌 `<button>` 是非法嵌套，移动端会连带跳转）。
  2. `.tb-root` 只写 min-height → 整页滑不动（html/body 都是 overflow:hidden）。
  3. 左栏名字必须 nowrap + 省略号，否则"课程答疑"折行把整栏撑高。
  4. 手机上左栏不能 display:none，要变成横向 chip 条，否则选不了板块。
  5. 楼层样式必须复用 `.kb-post`，不许在论坛皮肤里再抄一套。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

FORUM_PAGE = SRC / "app" / "forum" / "page.tsx"
FORUM_DETAIL = SRC / "app" / "forum" / "[postId]" / "page.tsx"
FORUM_CSS = SRC / "app" / "styles" / "forum.css"
FORUM_TB = SRC / "app" / "styles" / "forum-tb.css"
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


def test_row_uses_explicit_links_instead_of_an_overlay():
    """整行可点靠末尾一张覆盖层链接 —— 这条路后来被放弃了。

    覆盖层会盖住行内的其它链接（来源库 chip），得给每个同级链接单独抬
    z-index；手机端还容易误触。贴吧版改成两个明确入口：标题 + 「回复」。
    代价是行尾空白点不动，换来的是 `<a>` 里不再嵌 `<button>` 这种非法嵌套。

    所以这里钉两件事：清单里没有覆盖层；标题那个入口真的在。
    """
    s = _read(FORUM_PAGE)
    assert 'className="tb-post"' in s, "帖子行的容器不是 tb-post"
    assert "tb-hit" not in s, "覆盖层链接又回来了"
    assert re.search(r'<h3 className="tb-title">\s*<Link href=\{topicHref\(t\.id\)\}', s), (
        "标题不是链接 —— 用户点标题进不去帖子"
    )
    # 操作行里的三个入口：回复（Link）、我发的 / 被举报（button）、来源库（Link）
    seg = s[s.index('<div className="tb-acts">') :]
    seg = seg[: seg.index("</article>")]
    assert 'className="tb-act reply"' in seg, "「回复」入口不见了"
    assert "<button" in seg, "操作行里没有按钮"


def test_source_chip_truncates_instead_of_pushing_the_row():
    """来源库 chip 要能省略，不能把整行挤变形。

    库名是用户自己起的（「2026 秋季学期高等数学 B 班复习资料」这种），
    不设上限时它会一路顶到右边，把「1 条回复」压成两行。
    覆盖层那条老理由（要抬 z-index 才点得到）已经随覆盖层一起消失了，
    但"来源库名字长短不该改变行高"这条仍然成立。
    """
    rule = _css_rule(_read(FORUM_TB), ".tb-act.src")
    assert "max-width" in rule, "来源库 chip 没设宽度上限，长库名会把整行挤变形"
    assert "text-overflow: ellipsis" in rule, "超长库名没有省略号，会硬切成半个字"
    assert "white-space: nowrap" in rule, "库名会折行，行高跟着跳"


def test_root_scrolls_instead_of_growing():
    """`.tb-root` 必须是固定高度 + 自己滚。

    只写 min-height 的话元素被内容撑高，scrollHeight == clientHeight，
    而 html/body 都是 overflow:hidden —— 浏览器和容器都滚不了，整页卡死。
    """
    rule = _css_rule(_read(FORUM_TB), ".tb-root")
    assert re.search(r"height:\s*100dvh", rule), "少了 height:100dvh"
    assert "overflow-y: auto" in rule


def test_rail_name_does_not_wrap():
    """左栏每行的板块名要横排一行、放不下就省略。

    左栏宽 208px，图标 + 名字 + 计数挤在一行；不给 nowrap，"课程答疑"会折成
    两行，那一行变高，整栏的节奏跟着乱（而左栏是整页的骨架）。
    min-width: 0 一起钉 —— 没有它，flex 子项不会收缩，省略号永远不出现。
    """
    rule = _css_rule(_read(FORUM_TB), ".tb-bar-n")
    assert "white-space: nowrap" in rule
    assert "text-overflow: ellipsis" in rule
    assert "min-width: 0" in rule, "少了 min-width:0，flex 子项不会收缩，省略号不生效"


def test_floors_reuse_the_board_post_classes():
    """详情页楼层复用 `.kb-post` 系列，不在论坛皮肤里另抄一套。

    抄一份的后果是：以后调留言板的高亮色 / 回复按钮尺寸，论坛这里悄悄不跟着变。
    """
    detail = _read(FORUM_DETAIL)
    for cls in ("kb-post", "kb-post-h", "kb-post-b", "kb-post-act"):
        assert cls in detail, f"详情页没有复用 {cls}"

    for css_path in (FORUM_CSS, FORUM_TB):
        css = _read(css_path)
        # 允许出现 `.tb-detail .kb-post` 这种覆盖，但不允许重新定义 .kb-post 本体
        assert not re.search(r"^\.kb-post\s*\{", css, re.M), f"{css_path.name} 重定义了 .kb-post"


def test_mobile_rail_becomes_a_chip_strip():
    """手机上左栏不能藏起来 —— 要变成横向 chip 条。

    藏起来（display:none）就没人能选板块了：手机上那一屏是唯一的入口。
    所以钉三件事：760 断点里 `.tb-rail` 横向滚、整栏不许 display:none、
    板块名那几个附属块（标题/图标/角标）在窄屏上让位。
    """
    page = _read(FORUM_PAGE)
    assert 'className="tb-rail"' in page, "页面里没有左栏"

    css = _read(FORUM_TB)
    block = css[css.index("@media (max-width: 760px)") :]
    rule = block[block.index(".tb-rail {") :]
    rule = rule[: rule.index("}")]
    assert "overflow-x: auto" in rule, "手机上的板块条不会横向滚"
    assert "display: none" not in rule, "手机上把左栏藏了 —— 那就选不了板块"

    # 右栏的让位在 1280 断点：三栏在 1280 以下就塞不下了（左 200 + 右 296
    # 会把中间的通栏压到 600 以下，正文一行放不下几个字）。它比 760 那一档
    # 更早发生，所以判在 1280 块里 —— 取到下一个 @media 为止，别扫到 760 去。
    i = css.index("@media (max-width: 1280px)")
    wide = css[i : css.index("@media", i + 10)]
    assert ".tb-side { display: none" in wide, "1280 以下右侧说明栏该让位（三栏塞不下）"


def test_forum_css_loads_after_library_css():
    """论坛皮肤依赖 library.css 的顺序（楼层覆盖靠同优先级后到者赢）。"""
    g = _read(GLOBALS)
    assert g.index('"./styles/library.css"') < g.index('"./styles/forum.css"'), (
        "forum.css 被放到了 library.css 前面，楼层覆盖会失效"
    )


def test_forum_skins_load_in_order():
    """tb 皮肤必须在旧 forum.css 之后引入。

    `forum-tb.css` 里的 `.tb-detail` / `.tb-dhead` 要覆盖旧外壳，
    两者优先级相同，靠的就是"后到者赢"。顺序一颠倒，详情页标题会被旧样式吃掉。
    """
    g = _read(GLOBALS)
    assert g.index('"./styles/forum.css"') < g.index('"./styles/forum-tb.css"'), (
        "forum-tb.css 排到了 forum.css 前面，tb 外壳覆盖不掉旧外壳"
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
    # 2026-11-14 起「今日新增」只剩工具条右侧一处：左栏也有过一条，
    # 和上面的「全部话题 N」是同一个数字，一屏里说两遍。这里钉住的是
    # "只要出现就必须带单位"，不是"必须出现两次"。
    assert code.count("今日新增") >= 1, "统计文案又退回成没有单位的「今日 N」了"
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
    # 横幅里的标题、链接都必须来自同一条 notice。
    # 开标签用正则而不是写死 `<div className="tb-notice">`：横幅上挂了 ref
    # （底部"另有 N 条在顶部"要跳回来），写死的话加个属性这条就假红
    m = re.search(r'<div className="tb-notice"[^>]*>', s)
    assert m, "公告条不见了"
    # 横幅现在在信息流**里面**（不再是一个独立的外壳层），所以右边界是排序栏
    seg = s[m.start() : s.index('<div className="tb-sorts">')]
    assert "{notice.title}" in seg and "topicHref(notice.id)" in seg
    assert "pinned[1]" not in seg, "横幅里塞了第二条公告，横杠会变成两行"


def test_pinned_rows_render_before_normal_topics():
    """置顶必须真的排在前面 —— `pinned` 只是数据，顺序由渲染决定。"""
    s = _read(FORUM_PAGE)
    a = s.index("{listPins.map((t) => renderPost(t, true))}")
    b = s.index("{topics.map((t) => renderPost(t))}")
    assert a < b, "公告被渲染到了普通帖子后面，置顶等于没做"


def test_notice_post_is_not_repeated_in_the_list():
    """公告条已经说了的那一条，列表里不再重复。

    手机 390 宽时，同一句标题会在相隔约 150px 的地方出现两次：
    顶部横幅一次、列表首行一次。只去掉摘要不够，重复的是标题本身。

    但**不能简单地把它从列表里删掉**：用户点一次关闭，那一期公告就再也
    找不到入口了。所以关闭后要回到列表 —— 这一条断言钉的是这个条件分支，
    不是"少渲染一行"。
    """
    s = _read(FORUM_PAGE)
    assert "const listPins = notice ? pinned.filter((t) => t.id !== notice.id) : pinned;" in s, (
        "公告条和列表首行又同时显示同一条公告了；或者关闭公告条后那一条没有回到列表"
    )
    # 列表、空态、渲染条件三处都必须跟着 listPins 走，留一处用 pinned 就会
    # 出现"列表空着但空态不出现"这种对不上的状态
    assert s.count("listPins.length + topics.length") == 2, (
        "空态与列表的渲染条件没有一起换成 listPins"
    )


def test_empty_body_renders_no_empty_paragraph():
    """正文为空时不能渲染一个空 `<p>`。

    「标题 + 一张图」现在是合法发帖（正文选填），此时 `excerpt` 是空串。
    去掉这个 `&&`，列表里每条纯图帖下面都会多出一段看不见但**占着行高**的空白
    —— 看着像内容加载失败，实际是排版多了一段。
    """
    s = _read(FORUM_PAGE)
    assert "{t.excerpt && <p className=\"tb-text\">{t.excerpt}</p>}" in s, (
        "正文为空时也会渲染摘要段落了；纯图帖下面会多出一段空白"
    )


def test_zero_reply_rows_drop_the_counter():
    """0 回复的帖子不挂那个"0 回复"。

    一圈灰 0 挂在右侧，比"这条没人回"本身更显眼，还把标题列挤窄。
    0 条的时候就只写「回复」两个字。
    """
    s = _read(FORUM_PAGE)
    i = s.index('className="tb-act reply"')
    seg = s[i : i + 420]
    assert "t.reply_n > 0 ?" in seg, "回复数又变回无条件渲染了，0 回复会显示「0 条回复」"
    assert "条回复" in seg and '"回复"' in seg, "0 回复时那个入口没退回成「回复」"


def test_hot_column_skips_topics_without_replies():
    """右栏不列 0 回复的话题。

    热榜按 reply_n 排序，全是 0 时它退化成"最新几条"，而那几条此刻正在
    中间那栏原样列着 —— 右栏于是变成列表的复读机。
    """
    s = _read(FORUM_PAGE)
    assert "hot.filter((t) => t.reply_n > 0)" in s, "右栏又变成列表的复读机了"
    # 只有 1 条"有人接话"时也不算榜：一栏只有一条，等于把列表的某一行再抄一遍。
    # 阈值是这一栏存在的理由，删掉它就退回"1 条也单列"
    assert "hotLive.length >= 3" in s, "热帖栏在只有一两条时也会占掉一整列"
    i = s.index("hotLive.length >= 3")
    seg = s[i:]
    assert "hotLive.map(" in seg and "hot.map(" not in seg, "右栏渲染的还是未过滤的 hot"


def test_mobile_chip_strip_fades_instead_of_cutting():
    """手机上的板块条要右端渐隐，且滚到底时撤掉。

    七个板块一定放不下。不给遮罩，最后一个板块看起来是"被切坏了"
    （截图上「学习经验」只剩「学习经」）；一直留着遮罩，最后一个 chip
    又会永远淡着半边 —— 两个都要，所以两侧都得钉。
    """
    css = _read(FORUM_TB)
    block = css[css.index("@media (max-width: 760px)") :]
    rule = block[block.index(".tb-rail {") :]
    rule = rule[: rule.index("}")]
    # 不能用 `"mask-image" in rule`：`-webkit-mask-image` 里也含这个子串，
    # 把标准那一行删掉、只剩前缀版本，断言照样是绿的（反证时抓到的）。
    assert re.search(r"(?<!-)mask-image:\s*linear-gradient", rule), (
        "板块条右端没有渐隐，最后那个板块会被硬切"
    )
    # 选择器要连大括号一起钉：只判 ".tb-rail.at-end" 这个子串的话，
    # 把它改名成 .at-endX 仍然是绿的（反证时抓到的）
    assert re.search(r"\.tb-rail\.at-end\s*\{", css), "滚到尽头时没有撤掉遮罩的规则"
    assert re.search(r"\.tb-rail\.at-end[^{]*\{[^}]*mask-image:\s*none", css), (
        "滚到尽头时遮罩没撤掉，最后一个 chip 会一直淡着半边"
    )
    # 页面必须真的算过滚动位置。只判 "有 chipEnd 这个词" 太弱 —— 把算法改成
    # 恒真/恒假，那条断言照样绿
    page = _read(FORUM_PAGE)
    assert re.search(r"el\.scrollLeft \+ el\.clientWidth >= el\.scrollWidth", page), (
        "没算过板块条有没有滚到底，遮罩会一直留着"
    )
    assert 'addEventListener("scroll"' in page, "没监听横向滚动，滚到底了遮罩也不撤"


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


# ---------------------------------------------------------------------------
# 2026-11-14 那一轮视觉优化的取舍。下面几条钉的都是"看起来像少了个字段、
# 于是下一个人顺手补回来"的地方 —— 补回来不会报错，只是同一句话在一屏里
# 说三遍，读的人开始跳读整页。
# ---------------------------------------------------------------------------


def test_detail_hides_the_zero_reply_counter():
    """0 条回复时，标题下不挂「0 条回复」。

    列表里 0 回复早就不显示计数了（"一圈灰 0 比没人回本身更显眼"），
    详情页却还无条件写着「站务公告 · 0 条回复」—— 同一条规则两个页面两个口径。
    那句话的信息量是零：它只是用一行字宣布"这里什么都没有"。

    下架（`gone`）时也一样：那行字改成「已下架」，不该再挂回复数。
    """
    s = _read(FORUM_DETAIL)
    assert "{topic.reply_n > 0 && !gone && <span>{topic.reply_n} 条回复</span>}" in s, (
        "详情页又开始无条件渲染回复数了；0 回复时它会写着「0 条回复」"
    )


def test_entry_says_the_unit_out_loud():
    """列表里的回复数写「1 条回复」，不写「1 回复」。

    「1 回复」是名词短语压在数字后面，手机端掉到第二行时读起来像一句
    独立的备注；「1 条回复」才是一句话，也才对得上详情页的写法。
    """
    s = _read(FORUM_PAGE)
    assert "条回复" in s, "回复数又退回成「1 回复」了"
    assert "${t.reply_n} 条回复" in s, "列表里的回复数没带单位"


def test_pill_buttons_give_press_feedback():
    """胶囊按钮按下要有回应。

    这一排原来只有 hover 上浮，按下去什么都不发生 —— 手指点下去没有反应，
    会以为没点到。全局 .btn-new 靠背景变深回应，论坛这排靠轻微下陷，
    同时把阴影收掉，才像"陷下去"而不是"缩小了"。
    """
    css = _read(FORUM_TB)
    for sel in (".tb-new:active", ".tb-act:active", ".tb-pub:not(:disabled):active"):
        assert sel in css, f"{sel} 没有按下样式，点下去不会有任何回应"
    assert "scale(0.97)" in css, "按下没有变形；暖纸上只改阴影几乎看不出来"


def test_sort_tabs_do_not_reuse_the_chip_shape():
    """排序用下划线，不再用胶囊。

    板块 chip 和排序 tab 上下紧挨着，两排原来都是同款胶囊 + 同款淡金选中底，
    眼睛分不清哪排是选板块、哪排是换排序；把板块改成淡金底之后，
    两排干脆一模一样了。排序是"同一批内容的另一种看法"，用轻得多的
    下划线，两排就读成两个维度。
    """
    css = _read(FORUM_TB)
    tab = _css_rule(css, ".tb-sort")
    assert "border-radius: 999px" not in tab, "排序又变回胶囊了，和板块 chip 撞成同款"
    on = css[css.index(".tb-sort.on::after") :]
    on = on[: on.index("}")]
    assert "bottom: -1px" in on, "排序的选中指示不是那条下划线"


def test_list_bottom_accounts_for_the_notice_it_moved_upstairs():
    """列表到底时，得交代"还有一条在顶上"。

    左栏「全部话题 3」把横幅里那条也算进去了，列表却只有两行 ——
    差一行的时候人第一反应是"是不是没加载完"，不是"哦有一条在顶上"。
    所以句号旁边补一句，而且必须能点回顶部：只说"在顶部"不给入口，
    等于让用户自己滚回去找。
    """
    s = _read(FORUM_PAGE)
    assert "const noticeAside = notice ? pinned.length - listPins.length : 0;" in s, (
        "列表底没有交代被横幅吸走的那一条，左栏计数和列表行数又对不上了"
    )
    assert "onClick={jumpToNotice}" in s, "那句提示只是个装饰，点不动"
    assert "另有 {noticeAside} 条公告在顶部" in s, "句子里写的不是实际被吸走的条数"
    assert "ref={noticeRef}" in s, "没有指向公告条的锚点，跳回去无目标"

    css = _read(FORUM_TB)
    rule = _css_rule(css, ".tb-end-up")
    # 两侧是 flex:1 的延伸线，不给 flex:none + nowrap 就会被压成两行字
    assert "flex: none" in rule, "窄屏上这句提示会被两侧的线挤成两行"
    assert "white-space: nowrap" in rule, "窄屏上这句提示会折行"
    assert "rotate(-90deg)" in css, "箭头没有转成向上，指向和「在顶部」相反"


# ---------------------------------------------------------------------------
# 2026-11-14 第 18 轮：发图。下面两条钉的是"图上去了但没显示/显示错"的位置。
# ---------------------------------------------------------------------------


def test_list_cover_uses_the_image_endpoint():
    """列表封面必须走 `/api/forum/img/<hash>`，不能自己拼别的路径。

    接口只返回哈希（同一张图多人发只存一份），URL 由前端拼。拼错的表现是
    封面位置一块空白 —— 元素在、`<img>` 也在，只是 404，`.tb-cover` 的高度
    还在，看起来像"图丢了"。
    """
    s = _read(FORUM_PAGE)
    assert "forumImgUrl(t.images[0])" in s, "列表封面没走统一的图片地址函数"
    assert "t.images.length > 0" in s, "没有图的帖子也会渲染一块空的封面位"


def test_author_avatar_uses_the_same_priority_as_settings():
    """列表卡的头像必须是「图片 > 图标 > 首字母」，和设置页同一套优先级。

    第 17 轮视觉重写时这里只取了首字母，把 `t.author.avatar` 整个丢了 ——
    用户在设置里传的头像到了列表变成一个「玉」字方块，点进详情页又是真头像。
    同一份数据两处长不一样，用户只会得出一个结论：头像设置坏了。
    """
    s = _read(FORUM_PAGE)
    assert "avatarImageUrl(t.author.avatar)" in s, (
        "列表卡又只认首字母了；上传过头像的用户会看到自己的头像变成一个字"
    )
    assert "avatarIconName(t.author.avatar)" in s, "内置图标那一档也丢了"
    # 三个分支的顺序：图片在前，图标其次，首字母兜底
    i_img = s.index("avatarImageUrl(t.author.avatar)")
    i_icon = s.index("avatarIconName(t.author.avatar)")
    assert i_img < i_icon, "优先级倒了：应该先判图片，再判内置图标"
    seg = s[i_img : i_img + 700]
    assert "<img src={avImg}" in seg, "有图的时候没有真的渲染 <img>"


# ---------------------------------------------------------------------------

SMALL_FONT_EXEMPT = (".campus-week", ".campus-daycell")


def _css_font_sizes():
    """遍历所有真正会被打包的 CSS，产出 (文件, 选择器, px)。

    `_backup/` 是历史版本、不进构建，扫进来只会得到一堆假警报。
    """
    for path in sorted((SRC).rglob("*.css")):
        if "_backup" in path.parts or "node_modules" in path.parts:
            continue
        sel = ""
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.endswith("{") and not line.startswith("@"):
                sel = line[:-1].strip()
            m = re.search(r"font-size:\s*([\d.]+)px", line)
            if m:
                yield path, sel, float(m.group(1))


def test_no_text_is_smaller_than_eleven_px():
    """全站不该再有 9px / 10px 的字。

    10px 汉字在 1x 屏上是 10 个物理像素，一横一竖挤进同一个像素格 ——
    那不是"小一档"，是糊。「休 / 班」「条回复」「仅观看」都踩过。

    唯一豁免是校园周视图那几格：容器是算出来的 41–45px 栅格，
    11px 的「休」加内边距会把「周一 8」顶出格子，一溢出就是整列错位。
    豁免靠选择器前缀认，不靠行号 —— 行号一改就失效。
    """
    offenders = []
    for path, sel, px in _css_font_sizes():
        if px >= 11:
            continue
        if any(sel.startswith(p) or f" {p}" in f" {sel}" for p in SMALL_FONT_EXEMPT):
            continue
        offenders.append(f"{path.relative_to(ROOT)}:{sel or '<无选择器>'}={px}px")
    assert not offenders, "这些地方的字还在 11px 以下：\n  " + "\n  ".join(offenders)


def test_badge_token_is_at_least_eleven_px():
    """`--fs-badge` 是 11px，不是 10px。

    这个值一改全站 40 多处角标跟着动，是最省事的那个开关；
    改回去也最省事，所以单独钉一条。
    """
    css = _read(SRC / "app" / "styles" / "tokens.css")
    m = re.search(r"--fs-badge:\s*([\d.]+)rem", css)
    assert m, "--fs-badge 不见了"
    assert float(m.group(1)) >= 0.6875, f"--fs-badge 掉到 {m.group(1)}rem（<11px）了"
