"""开机动画 / 登录等待层（第 42 批）的守卫。

这套东西最容易出的不是"画得丑"，是三种"看着正常其实坏了"：

  1. 挂错层：动画挂在某个页面里 → 每次切页都重播一遍，而且从别的入口
     进来时又看不到。它必须只挂在 RootLayout（一个文档一份）。
  2. 挡死全站：这层是 fixed + z-index 9999 的不透明纸面，靠 JS 在 2.08s
     摘掉。脚本一旦没跑起来，整站永久被盖住，还得清缓存才能救。
  3. 深色下换了一张脸：底座永远是浅纸，但 mascot.css 的 .dark 规则会把
     高光去掉、纹理压淡。不还原的话同一个吉祥物在两套主题里长得不一样。

另外钉住"别处不许再手写一句『正在确认登录状态』"：四个入口共用一个组件，
下次改文案只改一处。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
BOOT_CSS = SRC / "app" / "styles" / "boot.css"
BOOT_TSX = SRC / "components" / "boot" / "BootSplash.tsx"
LAYOUT = SRC / "app" / "layout.tsx"
TOKENS = SRC / "app" / "styles" / "tokens.css"
MOBILE = ROOT / "mobile"

# 共用同一个等待层的四个入口：对话页、登录页、资料库列表、资料库详情
WAITING_PAGES = [
    SRC / "app" / "(console)" / "page.tsx",
    SRC / "app" / "login" / "page.tsx",
    SRC / "app" / "library" / "page.tsx",
    SRC / "app" / "library" / "[kbId]" / "page.tsx",
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    """剥掉注释再断言：注释里写着"删掉了什么"同样会命中。"""
    src = re.sub(r"/\*.*?\*/", "", _read(p), flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def test_css_is_actually_parsed():
    """先钉扫描量：下面的断言全靠这份 CSS，解析坏了会全部空过。"""
    css = _read(BOOT_CSS)
    assert css.count(".zs-boot-") >= 20, "boot.css 只解析到 %d 个类名" % css.count(".zs-boot-")
    assert len(re.findall(r"@keyframes\s+zs-boot-", css)) >= 7


def test_splash_lives_in_root_layout_only():
    """只在 RootLayout 挂一次：挂在页面里会每次切页重播。"""
    layout = _read(LAYOUT)
    assert "components/boot/BootSplash" in layout, "layout.tsx 没有引入开机层"
    assert "<BootSplash />" in layout, "layout.tsx 没有渲染 <BootSplash />"

    # 页面一律只用 AuthLoading，谁再 import 默认导出就是把动画搬进了页面
    offenders = []
    for p in SRC.rglob("*.tsx"):
        if p == BOOT_TSX or p == LAYOUT.resolve():
            continue
        body = _read(p)
        if re.search(r'import\s+BootSplash\s+from', body):
            offenders.append(str(p.relative_to(SRC)))
    assert not offenders, "这些地方也挂了整站开机层：%s" % ", ".join(offenders)


def test_no_page_repeats_the_waiting_line():
    """四个入口共用一个组件，不许各自再手写一句等待文案。"""
    for p in WAITING_PAGES:
        body = _read(p)
        assert "AuthLoading" in body, "%s 还在自己写等待层" % p.relative_to(SRC)
        assert "正在确认登录状态" not in body, "%s 又内联了一份等待文案" % p.relative_to(SRC)

    # 文案本体只有一处（BootSplash.tsx 里的 auth 分支）
    assert _read(BOOT_TSX).count("正在确认登录状态") == 1


def test_inapp_shell_does_not_double_play():
    """原生壳自己有启动动画，网页这层要直接隐藏，否则一前一后演两遍。"""
    css = _code(BOOT_CSS)
    m = re.search(r"\.in-app\s+\.zs-boot-splash\s*\{([^}]*)\}", css)
    assert m, "找不到 .in-app 下的开机层规则"
    assert "display: none" in m.group(1), "原生壳里没有隐藏网页开机层"


def test_splash_can_never_lock_the_site():
    """兜底放行：JS 没跑起来时靠纯 CSS 让开，而不是永久挡住整站。"""
    css = _code(BOOT_CSS)
    m = re.search(r"\.zs-boot-splash\s*\{([^}]*)\}", css)
    assert m, "找不到 .zs-boot-splash 主规则"
    assert "zs-boot-fail-open" in m.group(1), "开机层没有挂兜底放行动画"
    assert "@keyframes zs-boot-fail-open" in css, "兜底动画的关键帧被删了"

    frames = re.search(r"@keyframes\s+zs-boot-fail-open\s*\{(.*?)\n\}", css, flags=re.S)
    assert frames, "解析不到兜底关键帧的函数体"
    body = frames.group(1)
    for prop in ("opacity: 0", "visibility: hidden", "pointer-events: none"):
        assert prop in body, "兜底放行缺了 %s" % prop


def test_reduced_motion_kills_the_movement():
    """前庭敏感用户不该看到一串位移；减弱动效下元素要直接到位。"""
    css = _code(BOOT_CSS)
    m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*)", css, flags=re.S)
    assert m, "boot.css 没有 prefers-reduced-motion 分支"
    block = m.group(1)
    for sel in (
        ".zs-boot-char",
        ".zs-boot-logo-tile",
        ".zs-boot-line-fill",
        ".zs-boot-sprout",
        ".zs-boot-status",
    ):
        assert sel in block, "减弱动效没覆盖 %s" % sel
    assert "animation: none !important" in block


def test_timing_matches_between_web_and_shell():
    """网页 1700 淡出 / 2080 卸载；原生壳的最短展示也是 1700，交接才不打架。"""
    boot = _read(BOOT_TSX)
    assert "1700" in boot and "2080" in boot, "网页端时序被改动了"
    app = _read(MOBILE / "App.tsx")
    assert "1700" in app, "原生壳的最短展示时长和网页对不上了"


def test_slogan_is_the_same_sentence_on_both_sides():
    """两边写的是同一句话，别一处改了另一处忘。"""
    chars = re.findall(r'SLOGAN = \[([^\]]*)\]', _read(BOOT_TSX))
    mchars = re.findall(r'SLOGAN = \[([^\]]*)\]', _read(MOBILE / "BootOverlay.tsx"))
    assert chars and mchars, "有一边找不到 SLOGAN"
    assert chars[0] == mchars[0], "网页与原生壳的标语不一致：%s vs %s" % (chars[0], mchars[0])
    assert len(re.findall(r'"."', chars[0])) == 5, "标语应该正好 5 个字"


def test_dark_tile_keeps_the_light_mascot():
    """底座不跟主题走；里面的吉祥物也得按浅色那套画。"""
    tokens = _code(TOKENS)
    assert len(re.findall(r"--tile-paper\s*:", tokens)) == 1, "--tile-paper 被重复定义"
    m = re.search(r"--tile-paper\s*:\s*([^;]+);", tokens)
    assert m and m.group(1).strip() == "#F5F0E1", "--tile-paper 变成了 %s" % (m and m.group(1))

    # 深色块里不许覆盖它，否则底座就跟着纸面一起变黑了
    dark = re.search(r"\.dark\s*\{(.*?)\n\}", tokens, flags=re.S)
    assert dark, "tokens.css 里找不到 .dark 块"
    assert "--tile-paper" not in dark.group(1), "深色块把底座也改黑了"

    css = _code(BOOT_CSS)
    for prop in (".mascot-grain", ".mascot-shine", ".mascot-blush"):
        assert ".dark .zs-boot-logo-tile %s" % prop in css, (
            "深色底座里的吉祥物没还原 %s" % prop
        )
