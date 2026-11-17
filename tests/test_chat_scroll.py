"""对话页的滚动策略（2026-11-18）。

用户报的现象：「功能响应较快，但返回对话框时会出现极强的滑动效果」。

根因不是玄学：`ChatArea` 的程序化滚动是**匀速追平** —— 不管落差多大，
都在固定 280ms 内走完。那套速度是为"流式跟随"调的（一行 33px、每帧 1~2px，
观感是内容自己往上淌）。可它同时被用在了"进会话"上：从别的页面回到对话时
`messages` 从空到满，`scrollHeight` 一口气多出几千像素，
5000px / 280ms ≈ 18px/ms —— 一屏内容在一瞬间刷过去。

修法是把落差分两类：
  · 小落差（内容在长）= 跟随 → 播动画；
  · 大落差（换了一个视野：进会话 / 切会话 / 回到最新）= 直接落位。
界线是一屏的 0.9 倍（`JUMP_SCREENS`）。

这些断言守的就是这条界线：动画还在（否则流式会一顿一顿），
但大落差不再走动画（否则又变成一次白光）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT = ROOT / "frontend" / "src" / "components" / "ChatArea.tsx"


def _code() -> str:
    s = CHAT.read_text(encoding="utf-8")
    assert s, "ChatArea.tsx 是空的？"
    # 剥注释：这批的注释里大量出现 `programScroll`、`settleScroll`，
    # 直接读源码会命中注释（本项目栽过两次的那种假绿/假红）
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?<![:\w])//[^\n]*", "", s)
    return s


def test_large_jumps_skip_the_animation():
    """一屏以上的落差必须直接落位（`JUMP_SCREENS` 那条守卫）。"""
    src = _code()
    m = re.search(r"const JUMP_SCREENS = ([\d.]+);", src)
    assert m, "没有 JUMP_SCREENS 常量：落差分类无从谈起"
    ratio = float(m.group(1))
    assert 0 < ratio <= 1, f"JUMP_SCREENS = {ratio}，超过一屏就判不出'换视野'了"

    # 守卫必须出现在 programScroll 内部、rAF 循环之前
    body = src[src.index("const programScroll") : src.index("const settleScroll")]
    assert re.search(r"Math\.abs\(target - el\.scrollTop\) > el\.clientHeight \* JUMP_SCREENS", body), (
        "programScroll 里没有大落差判断：进会话时仍会用 280ms 匀速刷过去"
    )
    jump_at = body.index("JUMP_SCREENS")
    raf_at = body.index("requestAnimationFrame")
    assert jump_at < raf_at, "大落差判断写在了动画之后，等于没写"


def test_animation_still_exists_for_streaming():
    """小落差仍然要走动画 —— 一刀切掉会退回"一抽一抽"的老毛病。"""
    src = _code()
    assert "const SCROLL_DURATION_MS = 280;" in src, "流式跟随的固定时长没了"
    assert re.search(r"const speed = Math\.max\(\s*0\.02,\s*Math\.abs\(diff\) / SCROLL_DURATION_MS\s*\)", src), (
        "匀速追平的算式被改了：那是 11-02 那一轮按 732 个采样点调出来的"
    )
    assert "programScroll(el, Math.max(floor, want));" in src, (
        "流式跟随（气泡底边贴视口底）不再走 programScroll：动画路径整条断了"
    )


def test_switching_conversations_lands_instantly():
    """切会话 / 回到对话那一下必须直接落位，而不是"滚过去"。"""
    src = _code()
    m = re.search(
        r"if \(firstId === prevFirstRef\.current\) return;(.{0,600}?)\}, \[firstId, settleScroll\]\)",
        src,
        re.S,
    )
    assert m, "切换会话那条 effect 没了（或不再依赖 settleScroll）"
    body = m.group(1)
    assert "settleScroll(el, el.scrollHeight)" in body, (
        "切会话还在用 programScroll：落差几千像素时会播一次白光"
    )
    assert "programScroll(" not in body, "同一条 effect 里又调回了 programScroll"


def test_settle_scroll_cancels_pending_frames():
    """直接落位要顺手撤掉还排着的动画帧，否则下一帧会把位置拽回去。"""
    src = _code()
    body = src[src.index("const settleScroll") :]
    body = body[: body.index("}, []);")]
    assert "cancelAnimationFrame(scrollRafRef.current)" in body, (
        "settleScroll 没撤动画帧：正在跑的 280ms 会把人拽回中途"
    )
    assert "appliedTopRef.current = el.scrollTop" in body, (
        "settleScroll 没登记落点：onScroll 会把这一下当成用户接管，stick 被关掉"
    )


def test_scroll_container_has_a_stable_hook():
    """消息区要有 `chat-scroll` 这个类：探针与截图脚本靠它定位。

    没有它的时候只能按 `overflow-y-auto` 猜，改一次样式就找不到容器。
    """
    src = _code()
    assert re.search(r'className="chat-scroll[ "]', src), "滚动容器没有稳定类名 chat-scroll"
