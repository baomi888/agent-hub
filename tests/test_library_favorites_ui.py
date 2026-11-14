"""资料库收藏与折叠的**源码级**不变量。

为什么用源码断言而不是端到端：这几条都是"结构约定"，不需要渲染就能判定，
跑一次几十毫秒。它们共同的失效方式是**静默**：

  · 有人把「全站公开库」改回默认展开 → 页面完全正常，只是用户自己的库被挤下去；
  · 有人把星标塞进官方卡那个 `<a>` 里 → 桌面端看着没问题，手机上点收藏会连带跳转
    （<a> 里放 <button> 是非法嵌套，各浏览器自行修正 DOM）；
  · 有人删掉"来源"那一行 → 收藏列表变成一堆没有出处的库名，用户分不清是官方还是同学的。

这三件事都不会报错、接口也不会红，所以只能在这里钉住。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
PANEL = SRC / "components" / "KnowledgePanel.tsx"
LIBRARY = SRC / "app" / "library" / "page.tsx"
DETAIL = SRC / "app" / "library" / "[kbId]" / "page.tsx"
LIB_CSS = SRC / "app" / "styles" / "library.css"
BOARD = SRC / "components" / "library" / "KbBoard.tsx"


def _read(p: Path) -> str:
    assert p.exists(), f"文件不见了，检查路径：{p}"
    return p.read_text(encoding="utf-8")


def test_panel_public_group_is_collapsed_by_default():
    """资料库面板的「全站公开库」默认收起。

    用户原话："一开始不要在这设置所有全站公开库，因为不是每个用户都需要全部"。
    默认展开的话，9 门官方课 + 同学分享会把 280px 侧栏顶满，
    用户自己那几个库得滚半天才看得见 —— 而这一点不会有任何报错。
    """
    s = _read(PANEL)
    assert "const [pubOpen, setPubOpen] = useState(false)" in s, (
        "全站公开库改回默认展开了？用户自己的库会被挤到看不见"
    )
    assert "aria-expanded={pubOpen}" in s, "折叠按钮丢了 aria-expanded，读屏用户不知道它能不能点"


def test_panel_lists_favorites_before_all_public():
    """「我的收藏库」必须排在「全站公开库」前面。

    收藏是用户自己挑的、日常要挂的那几个；全站列表是"逛"的。
    顺序反了，每次都要先略过一排自己不需要的库才能点到常用的。
    """
    s = _read(PANEL)
    fav = s.index("我的收藏库")
    # 用折叠按钮的类名定位，而不是"全站公开库"这个词：
    # 那个词在 Props 注释里也出现过，按词找会定位到文件前几行，断言变得永远成立。
    pub = s.index("kb-pub-toggle")
    assert fav < pub, "「我的收藏库」被排到「全站公开库」后面了"


def test_panel_favorite_rows_carry_source():
    """收藏行要写出来源（官方知识库 / 某某 的库）。"""
    s = _read(PANEL)
    assert "来源{k.source}" in s, "收藏行丢了来源署名，用户分不清官方还是同学的库"


def test_favorite_star_is_not_nested_in_link():
    """官方课程卡上的星标不能落在 `<a>` 里，整卡又必须可点。

    `<a>` 里放 `<button>` 是非法嵌套：桌面端点了可能只收藏，
    手机上会连带跳进详情页 —— 用户以为自己只点了个星。

    2026-11-13 晚改成了「覆盖层链接」：内容正常排在 `<div.lib-course>` 里，
    再盖一层自闭合的 `<Link className="lib-course-hit" />` 当点击膜，星标用
    z-index 抬到它上面。这条断言钉的就是"链接必须是自闭的"——
    一旦有人为了方便把内容重新包进 `<Link>`，按钮就又进 `<a>` 了。
    """
    s = _read(LIBRARY)
    i = s.index("lib-course-hit")
    j = s.rindex("<Link", 0, i)
    tag = s[j : s.index("/>", i) + 2]
    assert "</Link>" not in tag, "覆盖层链接把卡片内容包起来了 → 星标又落进 <a> 里了"
    assert "lib-course-tags" in s, "星标与「官方」徽标应该在同一组里排，靠 gap 控制间距"


def test_course_star_is_not_absolutely_positioned():
    """官方卡的星标不能再靠绝对定位 + margin 硬推位置。

    旧写法是 `right: 7px; margin-right: 36px`：位置写死，收藏数一变宽
    就往「官方」徽标上贴，用户在广场上看到的是"星和后面那个标叠在一起"。
    间距应该由 flex 的 gap 给，宽度由内容自己决定。
    """
    css = _read(LIB_CSS)
    assert ".lib-course-wrap .lib-fav-btn" not in css, (
        "星标又被绝对定位回右上角了 —— 带收藏数时会压到「官方」徽标"
    )
    tags = css.index(".lib-course-tags {")
    block = css[tags : css.index("}", tags)]
    assert "gap:" in block, "星标与徽标的间距必须由 gap 给，不要再用 margin 反着算"


def test_favorite_star_reacts_to_hover():
    """星标要有悬浮反馈：抬起来 + 放大，不能只换颜色。

    用户原话："点击的时候不是有那个悬浮吗？然后星星它是没有悬浮的，
    然后就很死板"。16px 的图标只变颜色，肉眼几乎看不出来。
    """
    css = _read(LIB_CSS)
    i = css.index(".lib-fav-btn:hover:not(:disabled) {")
    block = css[i : css.index("}", i)]
    assert "transform:" in block, "星标的 hover 只剩颜色变化了，看起来像块死图标"


def test_library_page_shows_source_and_upload_date():
    """收藏列表要同时有来源、上传日期、浏览数。"""
    s = _read(LIBRARY)
    assert "来源{k.source}" in s
    assert "上传</span>" in s or "}上传" in s, "收藏行丢了上传日期"
    assert "lib-src-tag" in s, "来源标样式丢了"


def test_detail_page_offers_fork_only_for_others():
    """详情页的「复制成我的库」只给别人的库；自己的库给了也没有意义。"""
    s = _read(DETAIL)
    assert "forkPublicKb" in s, "复制入口没了 —— 用户没法在官方库基础上加自己的资料"
    # 两处（收藏 + 复制）都要被 !data.mine 守住
    assert s.count("{!data.mine &&") >= 2, "自己的库应该既不能收藏也不能复制"
    assert "fmtWhen(data.uploads_at)" in s, "详情页丢了上传日期"


def test_detail_page_explains_fork_is_a_copy():
    """复制按钮旁边必须说明"复制一份"，不能只写"加入"。

    用户以为是往官方库里加东西，实际上改的是自己名下那一份 ——
    说不清的话，他会以为同学也能看到他加的资料，或者反过来以为官方库被他改了。
    """
    s = _read(DETAIL)
    assert "复制一份到你名下" in s, "复制入口的说明丢了，用户会误解成往官方库里加东西"


def test_unfav_star_is_hollow_and_faved_is_filled():
    """未收藏的星必须是空心、已收藏才填金。

    ★ 这里踩过一次坑：Star 图标的 path 上带着 fill="#F5B840"（presentation
    attribute，优先级最低）。原来的 CSS 只写了 `.on svg { fill: currentColor }`
    —— 作用在 <svg> 上，被 path 自己的 fill 挡掉，**从来没生效过**。
    结果是未收藏也显示成金色实心星，用户看到满屏星星，根本分不清自己收过哪些。
    所以按 path 收口，这条断言钉的就是"必须是 path 选择器"。
    """
    css = _read(LIB_CSS)
    base = css.index(".lib-fav-btn svg path {")
    block = css[base : css.index("}", base)]
    assert "fill: none" in block, "未收藏的星标记被填成实心了 —— 用户看不出自己收过哪些"
    assert "stroke: currentColor" in block, "空心星没有描边就什么都看不见"

    on = css.index(".lib-fav-btn.on svg path {")
    on_block = css[on : css.index("}", on)]
    assert "#f5b840" in on_block.lower(), "收藏后的星标没填金色，跟未收藏分不出来"


def test_board_reply_button_shows_on_own_posts():
    """留言板的「回复」不能只在别人的留言上渲染。

    用户第一次打开留言板时，看到的往往正好是自己发的那一条；
    条件写成 !p.mine 的话，他会以为"回复"这个功能根本没上线（实际发生过）。
    """
    s = _read(BOARD)
    seg = s[s.index("kb-post-acts") : s.index("kb-post-del")]
    assert "!p.mine" not in seg, "回复按钮又被限制成只能回别人，自己的那条看不到入口"
    assert "startReply(p)" in seg, "回复按钮的点击处理丢了"
    assert "kb-post-act-ico" in seg, "回复按钮的图标丢了，两个灰字挤在名字后面发现不了"


def test_start_reply_does_not_block_own_posts():
    """`startReply` 里不能再用 `p.mine` 提前 return。

    上面那条只钉住了**渲染**。第四批放开渲染条件时，这一处拦截漏了：
    自己的留言上「回复」按钮好好地在，点下去毫无反应 —— 比不渲染更像"功能坏了"。
    两处是两个独立的闸门，只开一个等于没开。
    """
    s = _read(BOARD)
    seg = s[s.index("const startReply = ") : s.index("const send = ")]
    # 注释里明写了"曾经这么拦过"，所以要剔掉行注释再断言 —— 断的是代码，不是说明文字
    code = "\n".join(ln for ln in seg.splitlines() if not ln.lstrip().startswith("//"))
    assert ".mine" not in code, "startReply 又把「自己的留言」挡掉了，点了没反应"
    assert "setReplyTo(" in code, "startReply 没有真的设置回复对象"
