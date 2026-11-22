"""合并外壳 + 页头图标 + 额度搬家（2026-11-18 第 41 批）。

这一批动了三件用户能看见的事：
  ① 返回对话时不再有「极强的滑动效果」—— 大落差直接落位，不播动画；
  ② `/` 搬进 (console) 路由组，和 12 个功能页共用一套外壳（同一份 layout、
     同一个滚动容器、同一套页头语法、同一份底栏/浮层）；
  ③ 页头标题左侧加导航图标；「今天还能问 N 次」从对话顶栏搬进设置页。

这些坏起来的样子都很一致：编译不报错、接口全 200、桌面端看着还挺正常，
只有手机上、只有来回切页的那一下才露出来 —— 所以只能靠源码断言钉。

★ 断言写法约定（踩过的坑，别写成"绿着守空气"）：
  · 断言前先 `_code()` 剥注释：注释里常写着「删掉了什么/原来是 z-index:1」，
    直接读源码会命中注释，红的是注释不是行为；
  · 「某个东西不该存在」要同时有一条「该存在的东西存在」，
    否则删掉整段实现也是绿的。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
APP = SRC / "app"

CHAT_PAGE = APP / "(console)" / "page.tsx"
HOME_PAGE = APP / "page.tsx"
LAYOUT = SRC / "components" / "console" / "ConsoleLayout.tsx"
SHELL = SRC / "components" / "console" / "PageShell.tsx"
CHAT = SRC / "components" / "ChatArea.tsx"
SIDEBAR = SRC / "components" / "Sidebar.tsx"
SETTINGS = APP / "(console)" / "settings" / "page.tsx"
NAV = SRC / "lib" / "nav.ts"
CONSOLE_CSS = SRC / "app" / "styles" / "console.css"
CHAT_CSS = SRC / "app" / "styles" / "chat.css"


def _read(p: Path) -> str:
    assert p.exists(), f"文件不存在：{p}"
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    """剥掉注释：`/* ... */`（CSS / 块注释）与 `// ...` 行注释都去掉。

    本项目栽过两次：断言「某类名已删除」，命中的却是写着「已删除」的注释。
    """
    s = _read(p)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?<![:\w])//[^\n]*", "", s)
    return s


# ==================== ① 合并外壳 ====================


def test_chat_page_lives_in_the_console_group():
    """`/` 必须住在 (console) 里 —— 这是"一套外壳"的唯一判据。

    反证：把文件搬回 app/page.tsx，本用例变红（同时 test_console_nav 里
    那几条读同一路径的用例一起红，这正是我们想要的连锁）。
    """
    assert CHAT_PAGE.exists(), "对话页不在 (console) 路由组里：它又会变成第二套外壳"
    assert not HOME_PAGE.exists(), (
        "app/page.tsx 还在：两个 page.tsx 会撞路由，而 Next 不会报错，"
        "只会静默用其中一份"
    )
    layout = _code(LAYOUT)
    # 外壳要按路由分叉，三处例外缺一，对话页就会被塞进卡片/被裁
    for token in ("console-scroll--chat", "console-shell--chat", "console-page--chat"):
        assert token in layout, f"外壳没有给对话路由加 {token}"


def test_chat_page_does_not_build_its_own_shell():
    """页面里不许再出现第二份外壳：底栏、引导、公告、登录门都归外壳。

    这不是"代码整洁"问题：两份底栏会在手机上叠成两层，
    两份公告会各弹一次（它们的"已读"是同一个 localStorage key，
    现在看不出重叠只是因为两个实例读到同一个值）。
    """
    page = _code(CHAT_PAGE)
    # ★ 2026-11-22（方案 B）：侧栏搬进外壳了 —— 全局只准有一份。
    #   这条断言反过来钉：页面里再挂一份，桌面就会出现两条左栏。
    assert "<Sidebar" not in page, "对话页又自己挂了一份侧栏 —— 会和外壳那份叠成两条左栏"
    layout = _code(LAYOUT)
    assert "<Sidebar" in layout, "侧栏没有挂到外壳：所有页面都会失去左栏"
    for gone, why in (
        ("<ConsoleTabBar", "底栏"),
        ("page-fade", "入场动画（改由 (console)/template.tsx 统一播）"),
    ):
        assert gone not in page, f"对话页还自己挂了{why}"

    chat = _code(CHAT)
    for gone in ("<GuideModal", "<AnnounceModal"):
        assert gone not in chat, (
            f"ChatArea 还挂着 {gone}：外壳已经挂了一份，两个渲染点会叠"
        )
    assert "<FilePreviewModal" in chat, "反证：ChatArea 里该有的浮层被一起删了"


def test_only_the_chat_route_uses_a_div_as_page_container():
    """对话页自己就是 `<main>`，外壳不能再套一层 —— `<main><main>` 是无效结构。

    判据是外壳里那条三元表达式：isChat 走 div，其余走 main。
    """
    layout = _code(LAYOUT)
    assert re.search(
        r"isChat\s*\?\s*\(\s*<div className=\"console-page console-page--chat\">", layout
    ), "对话路由的内容容器不是 div：会套出 <main><main>"
    assert re.search(r"<main className=\"console-page\">\{children\}</main>", layout), (
        "其余页面的内容容器丢了 `<main>`：13 个页面会一起失去主区域语义"
    )


def test_logged_out_home_still_shows_the_landing_page():
    """未登录打开 `/` 看到落地页，而不是登录卡。

    合并之后所有页面都走 ConsoleLayout 的登录门，如果不再分流，
    第一次从搜索结果进来的人会先被要一个账号 —— 这正是 10 月那一版修掉的问题。
    """
    layout = _code(LAYOUT)
    block = layout[layout.index("if (!auth.me)") :]
    # 2026-11-22：原来切片到 `const navItems`，那份清单随 .console-nav 一起撤了，
    # 现在门后面紧跟的是侧栏插槽（const rail）。
    block = block[: block.index("const rail")]
    assert "if (isChat) return <>{children}</>" in block, (
        "未登录时对话页没有直出 children：落地页会被 AuthGate 顶掉"
    )
    assert "<AuthGate" in block, "其余页面也丢了登录门"


# ==================== ② 页头图标 ====================


def test_page_shell_takes_its_icon_from_the_nav_list():
    """页头图标必须来自 lib/nav —— 同一个功能三处一个图案。

    自己再画一套的后果很具体：「待办」在底栏是清单、在页头是铃铛，
    用户会以为导航里那个是另一件事（导航图标那套形状是刻意拉开距离的）。
    """
    shell = _read(SHELL)
    assert 'from "@/lib/nav"' in shell, "PageShell 没有引用导航清单"
    assert "NAV.find" in shell, "没有按路径去 NAV 里取图标"
    assert "item.href !== \"/\"" in shell or 'it.href !== "/"' in shell, (
        "取图标时没排除 `/`：对话页不是 PageShell，排除掉才不会被误配"
    )
    assert '<NavIcon name={glyph} className="pg-ic" />' in shell, (
        "h2 里没有渲染图标节点"
    )
    # 前缀匹配：/settings/install 要落到 /settings 的图标上
    assert 'here.startsWith(it.href + "/")' in shell, "只做了精确匹配，二级页会没有图标"


def test_every_nav_page_has_a_glyph_name():
    """导航里每一页（含只有桌面可见的）都能拿到一个图标名。"""
    nav = _read(NAV)
    names = set(re.findall(r'icon:\s*"([a-z-]+)"', nav))
    assert len(names) >= 10, f"导航里的图标名只有 {len(names)} 个，像是被改坏了"
    icons = _read(SRC / "components" / "NavIcon.tsx")
    declared = set(re.findall(r"^\s{2}([a-z-]+):\s*[A-Z]", icons, re.M))
    missing = names - declared
    assert not missing, f"这些图标名在 NavIcon 里没有对应图形：{sorted(missing)}"


def test_page_icon_scales_with_the_title():
    """图标尺寸用 em，跟着标题字号走：solo 页头 28/32，常规页头 20。"""
    css = _read(CONSOLE_CSS)
    assert ".pg-head h2 .pg-ic" in css, "没有 .pg-ic 规则（图标会撑满或消失）"
    rule = css[css.index(".pg-head h2 .pg-ic") :]
    rule = rule[: rule.index("}")]
    assert re.search(r"width:\s*0?\.\d+em", rule), f"图标宽度不是 em 相对值：{rule}"
    assert "flex: none" in rule, "图标会被 flex 压扁（标题长时先扁图标）"


def test_chat_topbar_has_the_same_icon_grammar():
    """对话顶栏也要有图标，且和控制台页头一样"图标在左、标题在右"。"""
    chat = _code(CHAT)
    assert '<NavIcon name="chat" className="chat-title-ic" />' in chat, "对话顶栏没有图标"
    assert 'className="chat-title min-w-0"' in chat, "标题没有包成 flex 容器，图标会另起一行"
    css = _read(CHAT_CSS)
    assert ".chat-title-ic {" in css and ".chat-title-text {" in css, "缺标题/图标的样式"


def test_chat_topbar_band_matches_the_page_header():
    """两条带子的高度要对得上，否则切页时正文起始线会跳。"""
    css = _read(CHAT_CSS)
    block = css[css.index(".chat-topbar {"):]
    block = block[: block.index("}", block.index("min-height"))]
    assert re.search(r"min-height:\s*50px", block), "手机档顶栏高度没对齐页头（50）"
    assert "@media (min-width: 900px)" in css, "桌面档没单独给 62"
    assert re.search(r"min-height:\s*62px", css), "桌面档顶栏高度没对齐页头（62）"


# ==================== ③ 额度搬进设置 ====================


def test_quota_is_gone_from_the_conversation():
    """对话区不再显示「今天还能问 N 次」（用户明确要求）。"""
    chat = _code(CHAT)
    for gone, why in (
        ("usage-chip", "顶栏额度胶囊"),
        ("今天还能问", "剩余次数那句话"),
    ):
        assert gone not in chat, f"对话区还有{why}"
    sidebar = _code(SIDEBAR)
    assert "今天还能问" not in sidebar, "会话侧栏里还留着「今天还能问 N 次」"
    assert "onOpenUsage" in sidebar, (
        "反证：侧栏的用量入口（账号名可点）被一起删了 —— 看板会没有入口"
    )


def test_settings_shows_remaining_quota():
    """设置页用「还剩 N 次」说人话，并且交代什么时候重置。"""
    page = _code(SETTINGS)
    assert 'id="quota"' in page, "设置页没有额度分组"
    assert "还剩 " in page or "`还剩" in page, "没有按剩余次数表述"
    assert "额度每天 0 点重置" in page, "没说重置时刻：用户第二天会以为额度没了"
    assert "Math.max(0, q.limit - q.used)" in page, "剩余次数算法没处理越界（会显示负数）"


def test_quota_card_collapses_on_phone():
    """三列额度在 390 上会挤成两行、进度条错位 —— 窄屏必须塌成一列。

    ★ 这条规则必须写在文件末尾：手机外壳那一段里还有一条更晚的
      `.grid3, .grid4 { repeat(2, ...) }`，同特异性下后写的赢。
      写在 .grid3 定义旁边等于没写（实测还是两列）。
    """
    css = _read(CONSOLE_CSS)
    tail = css[int(len(css) * 0.9) :]
    assert re.search(r"@media \(max-width: 900px\) \{\s*\.grid3 \{\s*grid-template-columns: minmax\(0, 1fr\);", tail), (
        "窄屏的单列规则不在文件末尾，压不过后面的两列规则"
    )


# ==================== ⑥ 账号卡三处微调（2026-11-18 用户验收）====================


def test_sidebar_online_status_removed():
    """侧栏底部的「在线 · deepseek-chat」整行删掉（用户验收要求）。

    ★ 这是删除型断言，最容易"绿着守空气"：光写 not in 的话，把整份 Sidebar
      删空也过。所以同时钉住「紧挨着的反馈入口与账号块还在」—— 删过头会红。
    ★ 2026-11-22 更新：原反证锚点之一是深色开关，但用户这一轮明确要求把
      「字号档位 + 深色开关」两块 UI 从侧栏撤走（高度让给会话列表），
      所以锚点换成「我的反馈」，并追加两条反向断言 + 设置页正断言：
      删掉的是**侧栏入口**，不是字号/主题这两项能力本身。
    """
    code = _code(SIDEBAR)
    assert "deepseek-chat" not in code, "侧栏又出现了 deepseek-chat 在线状态"
    assert "在线 ·" not in code, "侧栏又出现了「在线 ·」状态文案"
    assert 'className="status"' not in code, "侧栏的 .status 状态行样式钩子还在"
    # 反证配套：相邻结构仍完整（我的反馈、账号看板入口、退出按钮）
    assert "MyFeedbackLink" in code, "连「我的反馈」入口一起删了 —— 删过头"
    assert "onOpenUsage" in code, "连账号名看板入口一起删了 —— 看板没入口了"
    assert "onLogout" in code, "连退出按钮一起删了 —— 用户没法登出"
    # 侧栏底部不再有字号档位 / 深色开关（2026-11-22 用户要求）
    assert "fs-picker" not in code, "侧栏又出现了字号档位控件"
    assert "theme-chip" not in code, "侧栏又出现了深色模式开关"
    # 但两项偏好的入口必须在设置页 —— 只在侧栏删 UI，不能把能力一起删没
    page = _code(SETTINGS)
    assert "字号" in page and "深色纸面" in page, (
        "设置页「本地偏好」里的字号/主题开关没了 —— 两项偏好彻底没有入口了"
    )
    # .status 样式是这一行独占的，删干净；但顶栏 kb-chip 的绿点不能误伤
    comp = _code(SRC / "app" / "styles" / "components.css")
    assert not re.search(r"\.status\s*\{", comp), "components.css 里还留着 .status 样式"
    assert ".kb-chip .dot" in comp, "误删了顶栏知识库 chip 的绿点样式"


def test_sidebar_avatar_links_to_settings():
    """侧栏头像点开设置页（用户验收要求）。

    UserAvatar 内部是 aria-hidden 的 span，自己不可点、也没有可读名，
    所以必须由外层 Link 提供 href 与 aria-label —— 两样都钉。
    """
    code = _code(SIDEBAR)
    # 头像必须包在指向 /settings 的 Link 里；路径从 NAV 派生，不许在组件里硬编码
    m = re.search(
        r'<Link[^>]*href=\{SETTINGS_HREF\}[^>]*className="sb-avatar-link[^"]*"[^>]*>',
        code,
        flags=re.S,
    )
    assert m, "侧栏头像没有包成指向 SETTINGS_HREF 的 Link"
    assert re.search(r"SETTINGS_HREF\s*=\s*NAV\.find", code), (
        "设置路径没从 NAV 共享清单派生（Sidebar 里不许有第二份路径清单）"
    )
    assert '"/settings"' not in code, "Sidebar 里又硬编码了 /settings 字面量"
    block = m.group(0)
    assert 'aria-label="打开设置"' in block, "Link 没有可读名（头像本身 aria-hidden，读屏读不到）"
    # 头像本体仍在链接里，没被换成别的图标
    assert re.search(r"<UserAvatar\s", code[m.end() : m.end() + 300]), (
        "Link 里没有 UserAvatar —— 头像被换掉了"
    )
    # 账号名仍是看板入口，没被头像跳转覆盖（否则一处点击两件事打架）
    assert "onOpenUsage" in code, "账号名的看板入口被覆盖了"
    # 触控热区：只扩纵向，不与相邻账号名按钮的横向热区重叠
    touch = _read(SRC / "app" / "styles" / "touch.css")
    rb = re.search(r"\.sb-avatar-link::before\s*\{([^}]*)\}", touch)
    assert rb, "头像链接没有补触控热区"
    assert "inset: -10px 0" in rb.group(1), "头像热区横向扩张了，会和账号名按钮热区重叠"
