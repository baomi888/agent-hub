"""P2 六项的守卫（2026-11-16）。

这六项的共同点是：**改坏了也不会编译报错**，只会让手感变差或让用户在某个
边缘路径上掉坑。所以每条至少钉一件事，并且都做过反证（改回旧写法 → 变红）。

  1. 骨架屏：五处文字加载态换成占位块；「正在加载…」不许再以 <p> 形式出现；
  2. 更多面板：奇数项时末项跨满整行（管理员 7 项的"右下缺角"）；
  3. 返回键：打开面板压一条历史、popstate 关面板；换页时**不许**再退一次；
  4. 转场：一级 tab 只淡入、二级页 8px 推入；位移上限 8px（放大就会扯坏 fixed 浮层）；
  5. 触觉：网页发消息 + 壳按白名单处理；失败要静默；
  6. 账号 ID：常驻直接显示（2026-11-18 去掉折叠）、完整值可选可复制、卡片底部跨满整行。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
MOBILE = ROOT / "mobile"
STYLES = SRC / "app" / "styles"

SKELETON = SRC / "components" / "Skeleton.tsx"
TABBAR = SRC / "components" / "console" / "ConsoleTabBar.tsx"
TRANSITION = SRC / "components" / "PageTransition.tsx"
CONSOLE_TPL = SRC / "app" / "(console)" / "template.tsx"
CONSOLE_CSS = STYLES / "console.css"
HAPTICS = SRC / "lib" / "haptics.ts"
APP_SHELL = MOBILE / "App.tsx"
MOBILE_PKG = MOBILE / "package.json"
SETTINGS = SRC / "app" / "(console)" / "settings" / "page.tsx"

# 五处加载态：文件 → 该页应该出现的骨架组件
LOADING_SITES = {
    SRC / "app" / "(console)" / "cet" / "page.tsx": "SkeletonLines",
    SRC / "app" / "(console)" / "flow" / "page.tsx": "SkeletonLines",
    SRC / "app" / "forum" / "page.tsx": "SkeletonCards",
    SRC / "app" / "forum" / "[postId]" / "page.tsx": "SkeletonLines",
    SRC / "components" / "library" / "KbBoard.tsx": "SkeletonCards",
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """去掉 // 与 /* */ 注释。

    必须去：注释里谈「正在加载…」「8px」都是合法的，留着会让断言被自己的
    说明文字命中（P1 那批踩过一次：注释写"不许再出现 X"，结果断言命中注释）。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    src = re.sub(r"\{\s*/\*.*?\*/\s*\}", "", src, flags=re.S)
    return src


# ---------------------------------------------------------------- 1. 骨架屏


def test_text_loading_state_is_gone():
    """五处文字加载态都换成了骨架组件。

    断言的是「该文件里既不再有那句文字、又确实用了骨架」——
    只查后者会被"两个都留着"骗过去（骨架写在旁边，文字仍然先闪一下）。

    ★ 判据只针对**标签里的文字**，不是「短语在整个文件里不存在」：
      骨架自己的 `label="正在加载话题"` 也含这句话，而那正是给读屏听的、
      必须保留的东西。用 `<p>…</p>` 的内容来判，才不会误伤它。
    """
    phrases = ("正在加载…", "正在加载话题", "正在加载留言…")
    hits = 0
    for path, comp in LOADING_SITES.items():
        code = _strip_comments(_read(path))
        assert "Skeleton" in code, f"{path.name} 里没有骨架组件"
        assert f"<{comp}" in code, f"{path.name} 该用 {comp}"
        # 只看被标签包起来的文字（含 <p>、<span>、<div> 里的裸文字）
        for tag in re.finditer(r"<(\w+)[^>]*>([^<>]*)</\1>", code, flags=re.S):
            inner = tag.group(2)
            for phrase in phrases:
                assert phrase not in inner, f"{path.name} 里还留着文字态「{phrase}」"
        hits += 1
    # 扫描下限：五个文件都得真的被读到，防止路径写错导致"零个也通过"
    assert hits == 5, f"只检查到 {hits} 处加载态"


def test_skeleton_has_screen_reader_label():
    """骨架不能只给视觉用户看：读屏要听到一句 label。

    只留灰条、不写 sr-only 的话，读屏用户会遇到一块完全空白的区域 ——
    比原来那行「正在加载…」更糟。
    """
    src = _read(SKELETON)
    assert "sr-only" in src, "骨架没有 sr-only 文案"
    assert "aria-busy" in src, "骨架没有 aria-busy"
    # 两个组件都要有（列表型 + 行型），别只做一个
    assert "export function SkeletonLines" in src
    assert "export function SkeletonCards" in src


def test_ui_reexports_skeleton():
    """老调用方（打卡/看板/委托/资料库）仍从 console/ui 取 SkeletonLines。

    搬家时漏了这条 re-export，tsc 会红 —— 但这条测试留着是为了钉住
    「别在 ui.tsx 里再复制一份实现」，两份定义迟早长得不一样。
    """
    ui = _read(SRC / "components" / "console" / "ui.tsx")
    assert 'export { SkeletonLines } from "../Skeleton";' in ui
    # 实现只许有一份：ui.tsx 里不该再出现硬编码的 skel 行
    assert 'className="skel"' not in ui, "ui.tsx 里又自己写了一份骨架"


# ---------------------------------------------------------------- 2. 更多面板


def test_odd_last_item_spans_full_row():
    """奇数项时最后一项跨满整行。"""
    css = _strip_comments(_read(CONSOLE_CSS))
    assert ".tab-sheet-item:last-child:nth-child(odd)" in css, "缺奇数末项跨行规则"
    m = re.search(
        r"\.tab-sheet-item:last-child:nth-child\(odd\)\s*\{([^}]*)\}", css, flags=re.S
    )
    assert m, "规则存在但解析不到内容"
    body = m.group(1)
    assert "flex-basis: 100%" in body.replace(" ", " "), "末项没有跨满整行"
    # 反证方向：这条规则不许被改成"三列"
    assert "33%" not in body, "又把末项改回三列宽度了"


def test_more_sheet_stays_two_columns():
    """两列这个决定有实测依据（2026-11-15：三列只剩 114px，hint 放不下）。

    钉住它，免得下次有人看到评审写"3 列×2 行"就照着改 ——
    改了 hint 会折成两行，比右下缺角更难看。
    """
    css = _strip_comments(_read(CONSOLE_CSS))
    m = re.search(r"\.tab-sheet-item\s*\{([^}]*)\}", css, flags=re.S)
    assert m, "找不到 .tab-sheet-item 规则"
    body = m.group(1)
    assert "calc(50% - var(--space-4))" in body, "更多面板不再是两列"


# ---------------------------------------------------------------- 3. 返回键


def test_sheet_pushes_history_and_closes_on_pop():
    src = _strip_comments(_read(TABBAR))
    assert "pushState" in src, "面板打开时没有压历史"
    assert "popstate" in src, "没有监听 popstate"
    assert "removeEventListener(\"popstate\"" in src, "popstate 监听没有卸载"
    # 必须只在面板打开时压栈，否则每次渲染都压一条，返回键要按十几次
    m = re.search(r"if \(!moreOpen\) return;\s*\n\s*const at = window\.location\.href;", src)
    assert m, "压栈没有用 moreOpen 做闸"


def test_sheet_does_not_back_on_navigation():
    """点面板里的链接跳走后，**不能**再 history.back()。

    少了这个判断，表现是"点设置又弹回首页"—— 因为换页时面板被自动关闭，
    cleanup 里那句 back() 会把刚跳过去的那一页退掉。
    """
    src = _strip_comments(_read(TABBAR))
    m = re.search(r"if \(window\.location\.href !== st\.href\) return;", src)
    assert m, "缺「URL 变了就不 back」的判断"
    # 这条判断必须出现在 back() 之前
    idx_guard = src.index("window.location.href !== st.href")
    idx_back = src.index("window.history.back()")
    assert idx_guard < idx_back, "判断写在了 back() 后面，等于没判断"


# ---------------------------------------------------------------- 4. 转场


def test_transition_direction_is_derived_from_nav():
    """一级 tab 的集合从 lib/nav 推导，不许手抄一份。"""
    src = _strip_comments(_read(TRANSITION))
    assert "from \"@/lib/nav\"" in src, "没有从导航清单取一级 tab"
    assert "mobileTab" in src, "没有按 mobileTab 判一级 tab"
    # 手抄路由的表现：出现 href 字面量数组
    assert '"/forum"' not in src, "手抄了路由字面量，会跟导航清单漂移"


def test_push_offset_is_capped_at_8px():
    """进二级页的位移上限 8px。

    放大到 20px+ 会让 transform 在动画期间把页面里的 fixed 浮层
    （遮罩、抽屉、toast）拽偏一截 —— 首页早年为这个坑只敢动 opacity。
    """
    css = _strip_comments(_read(CONSOLE_CSS))
    m = re.search(r"@keyframes page-enter-push\s*\{(.*?)\n\}", css, flags=re.S)
    assert m, "找不到 page-enter-push 关键帧"
    body = m.group(1)
    px = [int(n) for n in re.findall(r"translateX\((\d+)px\)", body)]
    assert px, "push 关键帧里没有横向位移"
    assert max(px) <= 8, f"位移放大到了 {max(px)}px，会扯坏 fixed 浮层"

    # tab 之间只淡入：终态关键帧不许带位移
    tab = _strip_comments(_read(CONSOLE_CSS))
    m2 = re.search(r"\.page-enter--tab\s*\{([^}]*)\}", tab, flags=re.S)
    assert m2, "找不到 .page-enter--tab"
    assert "page-fade-in" in m2.group(1), "切 tab 用的不是纯淡入"


def test_console_template_uses_transition():
    tpl = _read(CONSOLE_TPL)
    assert "PageTransition" in tpl, "console 的 template 没接转场组件"


# ---------------------------------------------------------------- 5. 触觉


def test_haptics_bridge_exists():
    src = _strip_comments(_read(HAPTICS))
    assert "ReactNativeWebView" in src, "没有走壳的桥"
    assert "postMessage" in src, "没有 postMessage"
    assert "navigator.vibrate" in src, "浏览器兜底丢了（安卓浏览器还能用）"
    assert "try {" in src and "catch" in src, "没有 try/catch，触觉失败会连累导航"


def test_shell_only_trusts_haptic_messages():
    src = _strip_comments(_read(APP_SHELL))
    assert "expo-haptics" in src, "壳没装触觉库"
    assert "onMessage={onMessage}" in src, "WebView 没接 onMessage"
    # 白名单判断：不认 t 就直接 return
    assert 'msg?.t !== "haptic"' in src, "没有按消息类型过滤"
    pkg = _read(MOBILE_PKG)
    assert "expo-haptics" in pkg, "package.json 里没有 expo-haptics"


# ---------------------------------------------------------------- 6. 关于


def test_account_id_shown_directly_without_collapse():
    src = _strip_comments(_read(SETTINGS))
    # 旧写法：登录名旁边只露前 8 位，完整值塞 title（手机上 hover 不到）
    assert ".slice(0, 8)" not in src, "还在 login 名旁边露前 8 位"
    # 2026-11-18 用户要求：账号 ID 直接显示，不许再藏进「关于」折叠
    assert "<details" not in src, "账号 ID 又被塞回 <details> 折叠了"
    assert "<summary" not in src, "账号 ID 又出现「关于」折叠标题了"
    assert "<div className=\"acct-about\">" in src, "账号 ID 没有常驻显示的容器"
    # 完整值要能复制（点一下整段选中，而不是靠悬停 title）
    assert "userSelect: \"all\"" in src, "完整 ID 不能一键选中复制"
    # ID 不许被彻底删掉 —— 报障时要靠它对账号
    assert "me?.user.id" in src, "账号 ID 被删了，报障时对不上账号"


def test_about_block_spans_full_row_in_both_layouts():
    """账号 ID 行在桌面（flex 行）与窄屏（grid）都必须跨满整行。

    ★ 这条是补的：第一版只写了 margin/padding，没管它作为 flex/grid 子项怎么排。
      结果是这一块被塞进「头像 + 名字」那一行的第二列，名字当场被压成
      一个字宽、`baomi` 竖排成「b / a / o / m / i」—— 截图一眼就能看出坏了，
      但**没有任何测试会红**（tsc 也不报）。所以两档布局各钉一条。
      2026-11-18 由 `<details>` 换成 `<div>`，跨行规则不变，守卫继续有效。
    """
    acct_css = _strip_comments(_read(STYLES / "account.css"))
    m = re.search(r"\.acct-about\s*\{([^}]*)\}", acct_css, flags=re.S)
    assert m, "找不到 .acct-about 规则"
    assert "flex: 1 1 100%" in m.group(1), "桌面 flex 行里没给满行宽度，会挤进名字列"

    console_css = _strip_comments(_read(CONSOLE_CSS))
    card = re.search(r"\.acct-card\s*\{([^}]*)\}", console_css, flags=re.S)
    assert card, "找不到 .acct-card 规则"
    assert "flex-wrap: wrap" in card.group(1), "卡片不换行，满行的「关于」会把名字列压扁"

    # 窄屏 grid：必须显式跨两列（自动排布会落进 auto 列）
    mobile = re.search(
        r"\.acct-card \.acct-about\s*\{([^}]*)\}", console_css, flags=re.S
    )
    assert mobile, "窄屏没有给「关于」单独定位"
    assert "grid-column: 1 / -1" in mobile.group(1), "窄屏没跨满两列"
    # 必须在某个窄屏媒体查询之后（console.css 里有多个 900 档，取规则前面最近的那个）
    media_before = [
        m.start()
        for m in re.finditer(r"@media \(max-width: 900px\)", console_css)
        if m.start() < mobile.start()
    ]
    assert media_before, "这条规则跑到窄屏媒体查询外面了（桌面也会生效）"
