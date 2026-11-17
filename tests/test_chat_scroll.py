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


# ---------------- 内容自己长高之后继续贴底（2026-11-18） ----------------
#
# 用户报的现象：「从外部入口强制刷新进主界面，会停在中间某条历史对话上」。
#
# 实测（390×844，线上 164 条的会话，探针每 250ms 采一次）：
#   t=4062ms  历史落地，settleScroll 把 scrollTop 设成 79319（= scrollHeight 79952 - 633）
#   t=4326ms  自托管字体换上来，scrollHeight 变 82405 —— scrollTop 一动不动
#   t=12652ms 字体全部 loaded，scrollHeight 82028/81754，scrollTop 仍是 79319
# 离末尾 1802px。落点本身是对的，错在**只落一次**：字体 swap 引起的重排
# 没有任何人负责，`settleScroll` 只跟着「首条消息换了」跑。
#
# 修法是一条 ResizeObserver：内容容器尺寸变了就再贴一次底。下面四条守的就是
# 「什么时候可以贴」——贴得太积极会毁掉流式跟随，也会把正在翻历史的用户拽回去。

BUBBLE = ROOT / "frontend" / "src" / "components" / "chat" / "Bubble.tsx"


def _ro_body(src: str) -> str:
    """截出那条 ResizeObserver effect 的回调体。

    ★ 前面（尾部留白那条 effect）已经有一个 `new ResizeObserver(apply)`，
      按 `new ResizeObserver` 找会命中它，截出来的是一大段无关代码 ——
      于是"守卫在贴底之前"这种顺序断言会拿错误的下标去比（本轮实测 3 条红）。
      用带回调签名的那个前缀定位。
    """
    head = "const ro = new ResizeObserver(() =>"
    assert head in src, "没有 ResizeObserver：字体换上来之后就没人管落点了"
    start = src.index(head)
    end = src.index("}, [settleScroll]);", start)
    return src[start:end]


def test_content_growth_repins_to_bottom():
    """内容长高之后必须重新落到底部，而且用的是「直接落位」而不是动画。"""
    body = _ro_body(_code())
    assert "settleScroll(el, el.scrollHeight)" in body, "内容长高了没有重新贴底"
    assert "programScroll(" not in body, (
        "用 programScroll 会在字体换上来那一刻播一次动画 —— 那正是用户说的『极强的滑动』"
    )
    assert "ro.observe(inner)" in body, (
        "观察的不是内容容器：滚动容器的盒子尺寸不随内容变，observe(el) 等于没装"
    )
    assert "ro.disconnect()" in body, "没在卸载时断开：换会话会留下一个盯旧节点的 observer"
    # 贴回底部之后「回到最新」按钮必须收掉：它只在 onScroll 里更新，
    # 而程序滚会被 onScroll 认领后直接 return（实测截图里按钮挂在最底下）。
    assert "setAtBottom(true)" in body, "贴回底部后没有把「回到最新」收掉"


def test_repin_yields_to_manual_history_reading():
    """用户自己上滚了就不许拽回来 —— 这是「不影响手动看历史」的守卫。"""
    body = _ro_body(_code())
    guard = body.index("stickRef.current")
    land = body.index("settleScroll(el, el.scrollHeight)")
    assert guard < land, "stickRef 判断写在贴底之后，等于没判"
    assert re.search(r"if \(!stickRef\.current\) return;", body), (
        "守卫不是早退：上滚的用户会被每一次重排拽回底部"
    )


def test_repin_skips_while_streaming():
    """流式期间交给锚定/跟随，这里不许插手；并且那条 DOM 标记必须还在。"""
    body = _ro_body(_code())
    land = body.index("settleScroll(el, el.scrollHeight)")
    m = body.find('data-streaming="true"')
    assert m >= 0, "没有按 DOM 标记跳过流式"
    assert m < land, "流式判断写在贴底之后：每来一行字都会和锚定抢 scrollTop"

    # 跨文件守卫：`data-streaming` 是这条早退的唯一依据，Bubble 不再渲染它
    # 就等于这条守卫永远不成立（测试仍绿，线上却和锚定打架）。
    bubble = BUBBLE.read_text(encoding="utf-8")
    assert 'data-streaming={m.streaming && !!m.content ? "true" : undefined}' in bubble, (
        "Bubble 不再输出 data-streaming=\"true\"：流式期间贴底的守卫失效了"
    )


def test_repin_skips_empty_state():
    """欢迎页比一屏高，贴底会把标题顶出视口（老注释里已经踩过）。"""
    body = _ro_body(_code())
    land = body.index("settleScroll(el, el.scrollHeight)")
    m = body.find('querySelector("[data-mid]")')
    assert m >= 0, "没有按 [data-mid] 排除空态"
    assert m < land, "空态判断写在贴底之后：欢迎页会被拽到底部"
