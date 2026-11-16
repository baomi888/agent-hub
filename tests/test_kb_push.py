"""资料库在手机上改「推进去」（2026-11-16 P1）。

原状态：≤900 只是把桌面两栏竖着堆 —— 上面库列表（5 行），下面紧接着同一个库的详情，
库名在两端各出现一次；要点进一个库得先滑过整张列表，返回还得自己往回滚。
评审原话是"分不清我在列表里还是在库里"。

现在：列表点进库 = 整屏切换，详情左上角「‹ 我的资料库」退回；**桌面（≥900）两栏不变**。

这里钉四件事：
  1. 互斥规则必须在 ≤900 的媒体查询里 —— 掉到全局会把桌面右栏也藏掉，而且没人会发现；
  2. 点库时确实切到 detail 屏（只画了 class 不算）；
  3. 返回按钮存在、可点、有 aria-label（图标按钮只靠图形是读不出来的）；
  4. 库被删空时能退回列表（否则停在只有返回键的空卡上也不算错，但很怪）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
PAGE = SRC / "app" / "(console)" / "knowledge" / "page.tsx"
CSS = SRC / "app" / "styles" / "kbpage.css"


def _code(p: Path) -> str:
    src = p.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def test_mutual_exclusion_lives_in_mobile_query():
    """两条隐藏规则只能在 ≤900 里，且各只出现一次。"""
    css = _code(CSS)
    for rule in (
        ".kbp:not(.kbp--detail) .kbp-main",
        ".kbp--detail .kbp-side",
    ):
        assert css.count(rule) == 1, "%s 出现了 %d 次" % (rule, css.count(rule))
        i = css.index("@media (max-width: 900px)")
        # 该媒体查询块的结束位置：从 i 起数平衡花括号
        depth = 0
        end = None
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        assert end is not None, "找不到 @media (max-width: 900px) 的结尾"
        block = css[i:end]
        assert rule in block, "%s 不在 ≤900 媒体查询里（桌面也会被藏）" % rule


def test_back_button_hidden_on_desktop():
    """返回条默认 display:none，只在手机那一屏出现。"""
    css = _code(CSS)
    m = re.search(r"\.kbp-back\s*\{([^}]*)\}", css)
    assert m, "CSS 里找不到 .kbp-back"
    assert "display: none" in m.group(1), "桌面下 .kbp-back 必须隐藏"


def test_pick_switches_pane_on_narrow():
    """点库要真的切屏，不能只换个 class 名。"""
    src = _code(PAGE)
    assert 'useState<"list" | "detail">("list")' in src, "没有 mobilePane 状态"
    seg = src[src.index("const pickKb"):]
    seg = seg[: seg.index("};") + 2]
    assert "setActiveId" in seg, "pickKb 没选中库"
    assert 'setMobilePane("detail")' in seg, "pickKb 没切到详情屏"
    assert "matchMedia" in seg, "pickKb 没判断窄屏（桌面不该切屏）"


def test_back_button_is_reachable():
    """返回按钮要能被读屏读出来，并且真的把状态切回列表。

    注意别用 `<button[^>]*>` 抓标签：`onClick={() => ...}` 里的 `=>` 会让
    正则提前收尾，抓到的半截标签里当然没有 aria-label（第一版就栽在这）。
    """
    src = _code(PAGE)
    idx = src.index('className="kbp-back"')
    tag = src[idx: idx + 400]
    assert 'aria-label="返回全部资料库"' in tag, "返回按钮没有 aria-label"
    assert 'setMobilePane("list")' in tag, "返回按钮没有切回列表"


def test_empty_detail_falls_back_to_list():
    """库被删空时要退回列表。"""
    src = _code(PAGE)
    assert re.search(r"if \(!active\) setMobilePane\(\"list\"\)", src), (
        "active 为空时没有退回列表"
    )
