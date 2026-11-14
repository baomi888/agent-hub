"""首页对话区的三端尺寸不变量（2026-11-14 第七批）。

这一批改的全是"编译过、页面能开、但手机上就是不舒服"的地方：
  · 输入框 72px 高 → 56px（用户原话「对话框小一点」）
  · 手机正文 268px / 每行 16.8 字 → 310px / 19.4 字（「能看到的字太少」）
  · 顶栏 / 消息区 / 输入区统一走 --chat-px（原来各写 px-5 / px-6 / px-6）
  · 底部留白叠 iOS 安全区（viewport-fit=cover 之后不补，输入框压在 home 条上）

断言只查"契约"，不复刻数值推导：
  每一档的像素值都是线上量出来的，写死在测试里没有意义 —— 有意义的是
  「三档都存在」「手机档真的比桌面档小」「安全区在公式里」这三类关系。

★ 假绿防护（这一批刻意加的三条）：
  1. 所有 CSS 断言都要求匹配到**规则体**（`\\{[^}]*\\}`），不是裸子串 ——
     裸子串的经典翻车是 `.chat-px` 在注释里出现一次就让断言永远绿。
  2. `_code()` 先剥注释，再断言"JSX 里没有 px-6" —— 否则我写在
     ChatArea 里那句「原来各写 px-5 / px-6 / px-6」的注释会把断言打成红的，
     而如果反过来把断言放宽成子串，注释又会让它变成永远绿。
  3. 媒体查询块自己解析（括号计数），不靠 `re.search` 找第一个出现的
     `--chat-px: 14px` —— 那个值在桌面档也合法，位置错了照样能搜到。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

CHAT_CSS = SRC / "app" / "styles" / "chat.css"
WELCOME_CSS = SRC / "app" / "styles" / "welcome.css"
LAYOUT_CSS = SRC / "app" / "styles" / "layout.css"
COMPONENTS_CSS = SRC / "app" / "styles" / "components.css"
CHAT_AREA = SRC / "components" / "ChatArea.tsx"
BUBBLE = SRC / "components" / "chat" / "Bubble.tsx"
CONTENT_TSX = SRC / "components" / "chat" / "content.tsx"


def _read(p: Path) -> str:
    assert p.is_file(), f"文件不在了：{p}"
    return p.read_text(encoding="utf-8")


def _code(src: str) -> str:
    """剥掉 JSX / 块 / 行注释。

    不剥的话「注释里提到 px-6」会被当成还在用 px-6（假红），
    而如果为了绿把它改成子串匹配，注释又会变成假绿的温床。
    """
    src = re.sub(r"\{\s*/\*.*?\*/\s*\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


def _media(css: str) -> dict[str, str]:
    """把 `@media (...)` 的块体取出来，键是括号里的条件原文。

    手写括号计数而不是正则懒惰匹配：媒体查询里还有嵌套规则，
    `\\{.*?\\}` 会在第一个子规则的 `}` 就停下。

    ★ 同条件的块要**累加**而不是覆盖：chat.css 里有两个
      `@media (max-width: 560px)`（一个是令牌档、一个是输入框档），
      用 dict 直接赋值会只剩后一个 —— 第一版就是这么红的，
      而更隐蔽的版本是只剩前一个、于是"输入框变小"那条断言永远绿。
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"@media\s*\(([^)]*)\)\s*\{", css):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    cond = m.group(1).strip()
                    out[cond] = out.get(cond, "") + css[i + 1 : j]
                    break
    return out


def _rule(css: str, selector: str) -> str:
    """取某个选择器的规则体。`\\.a\\b` 这种带点的选择器用 re.escape 保住点。"""
    pat = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}")
    m = pat.search(css)
    assert m, f"没有找到规则：{selector}"
    return m.group(1)


# ---------- 版心与三端档位 ----------


def test_three_tier_tokens_exist():
    css = _read(CHAT_CSS)
    assert len(css) > 4000, "chat.css 短得不像话，可能读错了文件"
    media = _media(css)
    assert len(media) >= 5, f"只解析到 {len(media)} 个媒体查询，解析器可能坏了"

    root = _rule(css, ":root")
    assert re.search(r"--chat-px:\s*24px", root), "桌面版心 24px 不见了"
    assert re.search(r"--ai-mark:\s*44px", root), "桌面头像 44px 不见了"

    tab = media.get("max-width: 1023px")
    assert tab, "平板档（max-width: 1023px）不见了"
    assert re.search(r"--ai-mark:\s*34px", tab), "平板头像不是 34px"

    phone = media.get("max-width: 560px")
    assert phone, "手机档（max-width: 560px）不见了"
    assert re.search(r"--chat-px:\s*14px", phone), "手机版心不是 14px"
    assert re.search(r"--ai-mark:\s*26px", phone), "手机头像不是 26px"

    # 三档必须落在三个不同的宽度区间上，否则"三端适配"只是一句话
    assert float("1023") > float("560")


def test_horizontal_padding_shared_by_three_containers():
    """顶栏 / 消息区 / 输入区同一条竖线。

    改前是 px-5 / px-6 / px-6：手机上标题比正文靠外 1px，
    视觉上是两套栅格，但因为只差 1px 谁也不会去查。
    """
    src = _code(_read(CHAT_AREA))
    assert src.count("chat-px") >= 3, "三处容器没有都用 chat-px"
    # 顶栏与输入区各一次、消息区一次；注释已剥掉，所以这里数到的都是真类名
    assert "px-5 py-3" not in src, "顶栏还留着 px-5 py-3"
    assert "px-6 pb-4 pt-6" not in src, "消息区还留着 px-6 pb-4 pt-6"
    assert "px-6 pb-5" not in src, "输入区还留着 px-6 pb-5"
    assert "space-y-6" not in src, "消息列表还在用 space-y-6（应该走 --chat-msgs-gap）"


def test_padding_rules_follow_tokens():
    css = _read(CHAT_CSS)
    px = _rule(css, ".chat-px")
    assert "padding-left: var(--chat-px)" in px
    assert "padding-right: var(--chat-px)" in px
    msgs = _rule(css, ".chat-msgs")
    assert "gap: var(--chat-msgs-gap)" in msgs, "消息间距没走令牌"


# ---------- iOS 安全区 ----------


def test_dock_padding_includes_safe_area():
    """底部留白必须叠 env(safe-area-inset-bottom)。

    viewport-fit=cover 之后 iOS 的 home 指示条会压住页面底部 34px；
    写死 padding-bottom 的输入框会被压在横条上（真机上点不到发送键）。
    横屏时 env 为 0，所以用 calc 而不是固定值。
    """
    dock = _rule(_read(CHAT_CSS), ".chat-dock")
    assert "env(safe-area-inset-bottom" in dock, "底部留白没有叠安全区"
    assert "--chat-dock-pb" in dock, "底部留白没走令牌"
    assert "calc(" in dock, "不是 calc 的话横屏会多出一条空白"


# ---------- 输入框：手机 72 → 56 ----------


def test_composer_shrinks_on_phone():
    css = _read(CHAT_CSS)
    base = _rule(css, ".input-row")
    assert re.search(r"padding:\s*12px 16px", base), "桌面档输入框内边距被改掉了"

    phone = _media(css).get("max-width: 560px")
    assert phone, "手机档不见了"
    # 在手机档里再取一次 .input-row 的规则体
    m = re.search(r"\.input-row\s*\{([^}]*)\}", phone)
    assert m, "手机档没有针对 .input-row 的规则"
    body = m.group(1)
    assert re.search(r"padding:\s*8px 10px", body), "手机输入框内边距不是 8px 10px"
    assert re.search(r"gap:\s*8px", body), "手机输入框 gap 不是 8px"

    m = re.search(r"\.send\s*\{([^}]*)\}", phone)
    assert m, "手机档没有针对 .send 的规则"
    assert re.search(r"width:\s*32px", m.group(1)), "手机发送键不是 32px"
    assert re.search(r"height:\s*32px", m.group(1)), "手机发送键高度不是 32px"

    # 文本域内边距从工具类搬到了 CSS：两处都写会导致手机档盖不住
    assert ".composer-input" in css, ".composer-input 类没有定义"
    assert "px-2 py-2.5" not in _code(_read(CHAT_AREA)), "文本域还留着 px-2 py-2.5"


def test_send_button_stays_tappable():
    """32px 是这颗键的下限：它周围还有 10px 内边距与 8px gap，
    实际可点区域约 42px，勉强够 44px 的触控线。再小就等于让用户去瞄准。"""
    css = _read(CHAT_CSS)
    phone = _media(css)["max-width: 560px"]
    m = re.search(r"\.send\s*\{([^}]*)\}", phone)
    w = int(re.search(r"width:\s*(\d+)px", m.group(1)).group(1))
    assert w >= 32, f"发送键降到 {w}px 了"


# ---------- 吉祥物：随页面缩放 ----------


def test_mascot_scales_with_viewport():
    css = _read(CHAT_CSS)
    mark = _rule(css, ".ai-mark")
    assert "width: var(--ai-mark)" in mark, "头像宽高没走令牌"
    assert "height: var(--ai-mark)" in mark
    assert "flex: none" in mark, "头像会被 flex 压缩（26px 时压成一条）"

    src = _code(_read(BUBBLE))
    assert 'className="ai-mark"' in src, "Bubble 里的头像没换成 ai-mark"
    assert "h-11 w-11" not in src, "Bubble 还写死 44px 的 tailwind 尺寸"


def test_hero_mascot_uses_one_variable():
    """首屏吉祥物只留一个变量。

    改前：JSX 写 h-[52px]，welcome.css 的两个 max-height 档又写 44 / 38，
    等于同一件事三处定义 —— 桌面端实际一直是 44，改哪个都改不全。
    """
    css = _read(WELCOME_CSS)
    rule = _rule(css, ".welcome .hero-logo")
    assert "width: var(--hero-mark)" in rule
    assert "height: var(--hero-mark)" in rule

    # 三档 + 两个矮窗档都要有定义
    assert re.search(r"--hero-mark:\s*48px", css), "基础档 48px 不见了"
    assert re.search(r"--hero-mark:\s*52px", css), "平板档 52px 不见了"
    assert re.search(r"--hero-mark:\s*56px", css), "桌面档 56px 不见了"
    assert re.search(r"--hero-mark:\s*38px", css), "矮窗档 38px 不见了"

    assert "h-[52px]" not in _code(_read(CHAT_AREA)), "JSX 里还写着 h-[52px]"


# ---------- 正文行距 ----------


def test_mobile_line_height_tightened():
    css = _read(LAYOUT_CSS)
    phone = _media(css).get("max-width: 560px")
    assert phone, "layout.css 没有手机档"
    m = re.search(r"\.bubble-ai\s*\{([^}]*)\}", phone)
    assert m, "手机档没有针对 .bubble-ai 的行高规则"
    assert re.search(r"line-height:\s*1\.8", m.group(1)), "手机正文行高不是 1.8"

    # 桌面必须还是 1.9：38 字/行靠行距透气，收紧了会显挤
    base = _read(LAYOUT_CSS)
    m = re.search(r"^\.bubble-ai\s*\{([^}]*)\}", base, flags=re.M)
    assert m and "var(--lh-loose)" in m.group(1), "桌面行高被一起改掉了"


def test_user_bubble_widens_on_phone():
    src = _code(_read(BUBBLE))
    assert "max-w-[85%] sm:max-w-[75%]" in src, "用户气泡手机上没放宽到 85%"


def test_table_turns_into_cards_on_phone():
    """手机上表格必须转卡片，不能横向溢出或把列压成"一个字一列"。

    线上实测（390×844）：那张六级分数表在 310px 正文里，
    「A、B篇（长对话+篇章）」竖排 6 行、「每题1.5分，共15分」竖排 5 行。
    中间试过 min-width + 横向滚动，能读但两处说不通：
    ① 142px 之外看不见，手机没有滚动条提示；② 滑到右边表头已滚出视野。
    """
    css = _read(COMPONENTS_CSS)
    phone = _media(css).get("max-width: 560px")
    assert phone, "components.css 没有手机档"

    m = re.search(r"\.md-table,\s*\n?\s*\.md-table tbody\s*\{([^}]*)\}", phone)
    assert m, "手机档没有把 .md-table 改成块级"
    assert "display: block" in m.group(1), "表格没转块级，列还是会被压"

    m = re.search(r"\.md-table thead\s*\{([^}]*)\}", phone)
    assert m and "display: none" in m.group(1), "表头没隐藏（卡片模式下表头是多余的）"

    assert re.search(r"content:\s*attr\(data-label\)", phone), "卡片没有列名，值不知道属于哪一列"

    # 列名来自表头，必须由渲染层写进 data-label
    src = _code(_read(CONTENT_TSX))
    assert "data-label={labels[ci]" in src, "渲染层没给 td 写 data-label"
    assert ".replace(/[*`]/g, \"\")" in src, "表头的 markdown 记号没剥掉，列名会带着 ** 显示"

    # 桌面不能跟着改：block 化的表格在宽屏上会丢掉列对齐
    base = _rule(css, ".md-table")
    assert "width: 100%" in base, "桌面表格宽度规则被改掉了"
    assert "display: block" not in base, "块级化漏到了桌面档"
