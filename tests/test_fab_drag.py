"""手机端「对话目录」页签：贴边拖动、不越界、不压正文（2026-11-16）

回归背景
--------
`globals.css` 里 `touch.css` 排在 `turnnav.css` **之后**。touch.css 为了把
触控热区补到 44px，给一批元素统一写了 `position: relative`，`.turn-fab`
也在名单里 —— 于是这枚本该贴右缘的页签被干成了流内元素：

    position: relative;  top: 50%;  transform: translateY(-50%);
    → 实际 rect: left=0, top=980（视口 844），贴在左下角压住「复制 / 检索」

`tsc` 不会报、页面也能渲染，只有真机截图能看出来。所以这里钉四条：

1. touch.css 里不许再出现 `.turn-fab` 的 position 声明（防止有人"顺手"加回去）；
2. turnnav.css 的 `.turn-fab` 必须是 `position: fixed`，且带 `touch-action: none`；
3. 组件必须用 pointer 事件 + setPointerCapture 拖，横向只有左/右两个落点；
4. 纵向上限必须跟输入区（`.chat-dock`）走，别把页签拖到输入框上。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TURNNAV = ROOT / "frontend" / "src" / "app" / "styles" / "turnnav.css"
TOUCH = ROOT / "frontend" / "src" / "app" / "styles" / "touch.css"
CHAT = ROOT / "frontend" / "src" / "app" / "styles" / "chat.css"
COMPONENT = ROOT / "frontend" / "src" / "components" / "chat" / "TurnOutline.tsx"


def _strip_comments(css: str) -> str:
    """CSS 注释里常写着"这里不是 display:none"之类，直接搜字面量会被自己绊倒"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _blocks(css: str, selector: str) -> list[str]:
    """取出 `selector { ... }` 的声明块（按花括号平衡，能处理嵌套/多行）"""
    out: list[str] = []
    pattern = re.compile(re.escape(selector) + r"\s*\{")
    for m in pattern.finditer(css):
        i = m.end()
        depth = 1
        start = i
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        if depth == 0:
            out.append(css[start : i - 1])
    return out


def test_turnnav_fab_is_fixed_not_relative():
    css = _strip_comments(TURNNAV.read_text(encoding="utf-8"))
    blocks = _blocks(css, ".turn-fab")
    assert blocks, "turnnav.css 里找不到 .turn-fab 声明"

    # 第一条就是主声明块（后面的 .turn-fab svg / .turn-fab-n 是子选择器，
    # 但 _blocks 按字面量匹配，所以这里显式挑"含 width"的那块）
    main = [b for b in blocks if "width" in b and "border-radius" in b]
    assert len(main) == 1, f"预期恰好一个主声明块，实际 {len(main)}"
    body = main[0]

    assert "position: fixed" in body, "页签必须 fixed —— absolute 会被 touch.css 覆盖"
    assert "position: relative" not in body and "position: absolute" not in body
    # 拖动时手指不能带着页面滚
    assert "touch-action: none" in body, "缺 touch-action:none，拖动会顺带滚动页面"


def test_touch_css_no_longer_positions_turn_fab():
    """负向断言：touch.css 里任何 .turn-fab 声明块都不许再写 position"""
    css = _strip_comments(TOUCH.read_text(encoding="utf-8"))
    blocks = _blocks(css, ".turn-fab")
    # touch.css 只应留下 `.turn-fab::before` 的扩展块
    for b in blocks:
        assert "position: relative" not in b, (
            "touch.css 又给 .turn-fab 写 position: relative 了 —— "
            "它排在 turnnav.css 之后，会把 fixed 顶掉，页签直接掉进文档流"
        )


def test_fab_bleeds_out_so_it_never_covers_text():
    """血出量与槽位：屏内可见宽度 + 2px 间隙，必须落在消息区让出的槽位里

    ★ 2026-11-16 更正过一个**假绿**：上一版这条断言写的是「本体宽 ≤ 30px」，
      依据是"390 宽下正文最右到 x=366"。但 366 量的是段落**最后一行**（多半没
      写满）；用 Range 逐行量满载行，真实右端是 376 —— 也就是说 22px 的可见宽度
      其实一直在啃行尾 8px，断言却是绿的。所以判据改成算式：不写死像素，
      而是要求「可见宽度 ≤ 槽位 - 间隙」，槽位值从 chat.css 里取。
    """
    tcss = _strip_comments(TURNNAV.read_text(encoding="utf-8"))
    ccss = _strip_comments(CHAT.read_text(encoding="utf-8"))
    body = [b for b in _blocks(tcss, ".turn-fab") if "width" in b and "border-radius" in b][0]

    width = int(re.search(r"width:\s*(\d+)px", body).group(1))
    bleed_m = re.search(r"right:\s*-(\d+)px", body)
    assert bleed_m, "缺右侧出血（right: -Npx），贴边会啃字"
    bleed = int(bleed_m.group(1))
    visible = width - bleed

    slot_m = re.search(r"--chat-fab-slot:\s*(\d+)px", ccss)
    assert slot_m, "chat.css 里缺 --chat-fab-slot（消息区要给页签让出的那条宽度）"
    slot = int(slot_m.group(1))

    assert visible + 2 <= slot, (
        f"页签屏内可见 {visible}px 超过了消息区让出的槽位（{slot}px，需留 2px 间隙）—— "
        f"正文满载行会被啃掉 {visible + 2 - slot}px"
    )

    # 停靠左侧时是同一条边，出血量必须一致，否则左边比右边宽/窄
    lb = _blocks(tcss, ".turn-fab--left")[0]
    left_bleed = int(re.search(r"left:\s*-(\d+)px", lb).group(1))
    assert left_bleed == bleed, "左右两侧的出血量不一致，页签换边后会变宽/变窄"

    # 停靠左侧时圆角要镜像，否则像"从左边戳出来的一截"
    assert "right: auto" in lb
    assert "0 var(--r-pill) var(--r-pill) 0" in lb, "左缘停靠时圆角方向没镜像"


def test_slot_only_applies_while_fab_is_shown():
    """槽位必须挂在 data-fab-side 上：页签不出现时这 28px 不能白留着

    页签是条件渲染的（问答不足 3 轮不出现，桌面换成页边刻度）。如果直接把
    `padding-right: var(--chat-fab-slot)` 写死在 .chat-msgpad 上，短对话右侧
    会凭空多出一道 28px 的白边，而且没有任何测试会红。
    """
    ccss = _strip_comments(CHAT.read_text(encoding="utf-8"))
    assert 'html[data-fab-side="right"] .chat-msgpad' in ccss, "右侧槽位没挂 data-fab-side"
    assert 'html[data-fab-side="left"] .chat-msgpad' in ccss, "左侧槽位没挂 data-fab-side"

    src = COMPONENT.read_text(encoding="utf-8")
    assert "dataset.fabSide" in src, "组件没把侧位同步到根元素，槽位永远不会生效"
    assert re.search(r"dataset\.fabSide\s*=\s*show\s*\?", src), (
        "同步侧位时必须判 show —— 页签藏着的时候要把属性清掉"
    )


def test_component_drags_with_pointer_and_clamps():
    src = COMPONENT.read_text(encoding="utf-8")

    assert "setPointerCapture" in src, "拖动必须抓指针，否则手指滑出按钮就断"
    assert "pointerId" in src, "要校验 pointerId —— 多指同时按时别的指头不能拖着它跑"
    assert "DRAG_SLOP" in src, "缺位移阈值：手指点按会漂 1~2px，不能算拖动"
    assert "Math.min(Math.max(" in src, "纵向没有 clamp，会被拖出可视范围"
    assert 'querySelector<HTMLElement>(".chat-dock")' in src, (
        "纵向下界要跟输入区走，写死像素会在键盘弹起/多行输入时失准"
    )


def test_component_only_two_horizontal_landings():
    """横向只能贴左或贴右：拖动过程中也不许停到正文区中间"""
    src = COMPONENT.read_text(encoding="utf-8")

    assert "e.clientX < window.innerWidth / 2" in src, (
        "横向落点必须按「手指在屏幕哪半边」二选一，不能直接用 clientX 当 left"
    )
    # 位置只由 side + ratio 决定
    assert 'side: "left" | "right"' in src
    assert "FAB_POS_KEY" in src and "localStorage.setItem" in src, "拖动结果要记住，否则刷新就回原位"


def test_component_keeps_click_semantics():
    """拖完不能顺手把目录弹出来；点了没拖才打开"""
    src = COMPONENT.read_text(encoding="utf-8")

    assert "skipClick" in src, "缺「拖完别触发 click」的闸"
    assert re.search(r"if\s*\(!d\.moved\)\s*return", src), "pointerup 里没有区分「拖动」和「点击」"
    assert "aria-haspopup=\"dialog\"" in src, "原有无障碍语义不能丢"
