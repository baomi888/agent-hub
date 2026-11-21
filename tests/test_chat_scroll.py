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
        r"const sessionChanged = sessionId !== prevSessionRef\.current;(.{0,1400}?)\}, \[sessionId, firstId, messages, settleScroll\]\)",
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
    # ★ 2026-11-21：必须覆盖**整个**流式期（含 content 为空的「思考中」阶段）。
    #   旧写法 `m.streaming && !!m.content` 在空回答那几秒不输出标记 →
    #   贴底守卫失效 → 刚锚到顶部的视野被 ResizeObserver 拽回底部。
    bubble = BUBBLE.read_text(encoding="utf-8")
    assert 'data-streaming={m.streaming ? "true" : undefined}' in bubble, (
        "Bubble 的 data-streaming 没覆盖思考阶段：空回答那几秒贴底守卫会失效"
    )
    assert "m.streaming && !!m.content" not in bubble, (
        "Bubble 又在等 content 有字才标记流式：思考中会被贴底 effect 抢走视野"
    )


def test_repin_skips_empty_state():
    """欢迎页比一屏高，贴底会把标题顶出视口（老注释里已经踩过）。"""
    body = _ro_body(_code())
    land = body.index("settleScroll(el, el.scrollHeight)")
    m = body.find('querySelector("[data-mid]")')
    assert m >= 0, "没有按 [data-mid] 排除空态"
    assert m < land, "空态判断写在贴底之后：欢迎页会被拽到底部"


# ---------------- 新一轮提问：回答一出现就锚到顶部（2026-11-21） ----------------
#
# 用户报的现象：「主页输入框再次提问后，第一眼看到的是底部，无法从前往后看，
# 只有等流式结束后才跳转到对应位置」。
#
# 实测（CDP 每 100ms 采样，上滚到顶后提问）：
#   t=111ms   top=4768/4768  ← 发送瞬间被拽到底部，屏幕只剩「思考中」+留白
#   t=3081ms  第一个字出现
#   t=3268ms  才锚定到 lastTop=65（回答顶部）
# 中间约 3 秒用户盯着的都是留白。
#
# 根因两处叠加：
#   ① 锚定判定 hasText 要求 content.length>0 —— 空回答（思考中）那几秒不锚定；
#      跟随分支却拿 anchoredPosRef（上一轮锚点）当 floor，把视野钉在上一轮底部。
#   ② Bubble 的 data-streaming 要求 !!m.content —— 思考阶段没有标记，
#      内容长高贴底 effect 的守卫失效，settleScroll 又把视野贴回底部。
#
# 修法：锚定只看「本轮正在流式」(isNewStream)，与有没有字无关；
#       标记覆盖整个流式期。下面四条守的就是这套语义不被改回去。


def _anchor_effect_body(src: str) -> str:
    """截出「新消息 / 新一段文字进来时的视野策略」那条 effect 的回调体。"""
    marker = "const hasText ="
    # 兼容旧名与新名：定位到 isNewStream/hasText 判定，向后取到锚定落地
    head_candidates = ["const isNewStream =", marker]
    start = -1
    for h in head_candidates:
        if h in src:
            start = src.index(h)
            break
    assert start >= 0, "锚定判定（isNewStream / hasText）没了"
    return src[start : src.index("if (performance.now() < anchorUntilRef.current) return;", start)]


def test_anchor_does_not_wait_for_text():
    """★ 核心：锚定不能等 content 有字 —— 思考阶段就该把新气泡顶到视口。"""
    src = _code()
    assert "const isNewStream =" in src, "锚定判定还是老名字 hasText：新语义没落地"
    # isNewStream 只看 streaming 与 role，不许再带 content.length / !!m.content
    decl = src[src.index("const isNewStream =") : src.index("const isNewStream =") + 160]
    assert "last.streaming" in decl and 'last?.role === "assistant"' in decl
    assert "content" not in decl, "锚定判定又依赖 content 了：思考阶段会被漏掉"
    # 触发点用的也必须是它，不能是另一个带 content 的旧变量
    assert "if (isNewStream) {" in src, "锚定触发没用 isNewStream"


def test_new_stream_reclaims_stick_for_all_entry_points():
    """重新生成 / L4 重发绕过 submit()，要靠锚定 effect 自己复位 stickRef。"""
    src = _code()
    body = _anchor_effect_body(src)
    m = re.search(r"if \(isNewStream && !stickRef\.current\) \{(.{0,200}?)\}", body, re.S)
    assert m, "没有「新一轮强制接管视野」的兜底：上滚后重试不锚定"
    seg = m.group(1)
    assert "stickRef.current = true" in seg, "兜底没把 stick 设回 true"
    assert "setAtBottom(true)" in seg, "兜底没同步收掉「回到最新」按钮"
    # 复位必须发生在 stickRef 门控**之前**，否则自己把自己挡了
    assert seg.index("stickRef.current = true") < body.index("if (!stickRef.current) return;")


def test_stream_marker_covers_thinking_phase():
    """思考阶段必须有 data-streaming：否则贴底守卫会抢走刚锚好的视野。"""
    bubble = BUBBLE.read_text(encoding="utf-8")
    assert 'data-streaming={m.streaming ? "true" : undefined}' in bubble
    # 且 thinking-loader 分支要渲染在带标记的那个 div 里（跨组件结构守卫）
    assert "thinking-loader" in bubble and "m.streaming && !m.content" in bubble


def test_scroll_to_bottom_anchor_uses_relative_delta():
    """锚定仍用「相对当前位置的增量」，不受 tail 异步写入影响（老契约不回退）。"""
    src = _code()
    body = _anchor_effect_body(src)
    assert "const delta = rect.top - elRect.top - ANCHOR_GAP;" in body, (
        "锚定改回绝对 scrollTop 了：tail 异步会让它偏几百像素"
    )
    assert "el.scrollTop = target;" in body, "锚定没有同步落地（排 rAF 会被下个 token 取消）"


# ---------------- 2026-11-21 第六次修正：高度变化不能当成用户接管 ----------------
#
# 线上 CDP 实测（六级复习会话，390×844，每 100ms 采样）：
#   t=113ms  top=82389 / max=82432，锚定成功，lastTop=14
#   t=1364ms top=82332 / max=82332，气泡从「思考中」换成第一个字，高度少 100px，
#            scrollHeight 跟着缩，浏览器把贴底的 scrollTop 夹回新 max
#   t=1508ms 之后 top 一直是 82332，max 却涨到 83402 —— 跟随彻底停摆
#
# 根因：onScroll 靠「当前 scrollTop ≠ 上次写入值」推断用户接管。
# tail / 字体 / 状态条改高度时浏览器自己 clamp 的那一下，在位置层面和用户上滚
# 长得一模一样，于是 stick 被关掉，后面十几秒的流式内容再也不跟。
#
# 修法：用户接管改由输入事件（滚轮 / 指针 / 翻页键）显式判定；scroll 事件只读
# 来更新按钮。tail 改变高度时当场把视野放回锚点并认领这一下。


def test_scroll_event_no_longer_infers_user_takeover():
    """scroll 事件只能更新按钮，不能靠位置猜「用户在滚」。"""
    src = _code()
    body = src[src.index("const onScroll"): src.index("const takeOver")]
    assert "stickRef.current = near" not in body, (
        "又用位置推断接管了：浏览器 clamp 一下就会关掉 stick，整轮不再跟随"
    )
    assert "cancelAnimationFrame(scrollRafRef.current)" not in body, (
        "scroll 事件还在掐动画帧：clamp 会被当成用户上滚"
    )
    assert "setAtBottom((prev)" in body, "按钮状态不再随滚动更新"


def test_user_takeover_comes_from_input_events():
    """真实输入才冻结跟随，并且挂在滚动容器上。"""
    src = _code()
    body = src[src.index("const takeOver"): src.index("const scrollToBottom")]
    assert "stickRef.current = false" in body, "takeOver 没有关掉跟随"
    assert "addEventListener(\"keydown\"" in body, "翻页键不算接管"
    container = src[src.index('className="chat-scroll'):]
    assert "onWheel={takeOver}" in src, "滚轮没接到 takeOver"
    assert "onPointerDown=" in src, "触摸 / 拖拽没接到 takeOver"


def test_tail_resize_reclaims_clamped_scroll():
    """tail 改高度后必须把视野放回锚点，并认领这一下 scrollTop。"""
    src = _code()
    start = src.index("const apply = () =>")
    body = src[start: src.index("const ro = new ResizeObserver(apply)", start)]
    assert "streamIdRef.current === lastId" in body, "只处理当前流式消息"
    assert "anchorAbsRef.current" in body, "没有绝对锚点：高度一变顶边就丢"
    assert "appliedTopRef.current = el.scrollTop" in body, (
        "clamp 后没认领：onScroll 会把它当成用户接管"
    )


def test_anchor_records_absolute_top():
    """锚定时要留下绝对坐标，供后续重排复用。"""
    body = _anchor_effect_body(_code())
    assert "anchorAbsRef.current = rect.top - elRect.top + el.scrollTop;" in body, (
        "只算了相对增量，没留下绝对顶边：tail 变化后无法把开头放回去"
    )


def test_session_switch_keyed_on_session_id():
    """换会话的主键是 sessionId，不能只靠首条消息 id。"""
    src = _code()
    start = src.index("const prevSessionRef")
    body = src[start: src.index("}, [sessionId, firstId, messages, settleScroll]);", start)]
    assert "sessionId !== prevSessionRef.current" in body, "换会话没按 sessionId 判定"
    assert "const streamActive =" in body, "没识别同一帧到来的新流：贴底会冲掉锚点"
    land = body.index("settleScroll(el, el.scrollHeight)")
    guard = body.index("if (streamActive)")
    assert guard < land, "贴底写在流式守卫之前：新会话第一条提问会被按到底部"


def test_repin_only_for_history_load():
    """贴底 RO 只负责历史加载，流过后不许再抢视野。"""
    body = _ro_body(_code())
    land = body.index("settleScroll(el, el.scrollHeight)")
    m = body.find("everStreamedRef.current")
    assert m >= 0 and m < land, (
        "本会话流过一次后仍在贴底：来源卡/操作按钮落地时会把视野反复拽到底"
    )


# ---------------- 2026-11-21 第七次修正：跟随目标必须是顶边锚定 ----------------
#
# 视频（12:18 手机录屏）+ 全程 CDP 探针（390×633，回答 6→499 字）共同定位：
# 旧跟随目标 want = 气泡底边 - 视口高，气泡长过一屏后顶边被顶出视口
# （lastTop 14 → -544），视野钉在回答末尾、开头一闪而过、还在持续上下跳。
# 上次只修了「思考阶段锚定」，没覆盖「回答持续增长」这一段，探针里回答卡在
# 54 字没涨，所以假绿。这次把跟随和锚定统一到同一个绝对顶边坐标。


def _follow_body(src: str) -> str:
    """取「跟随阶段」那段：从 bottomInView 注释锚点到 effect 结束。"""
    start = src.index("const node = last ? el.querySelector")
    return src[start: src.index("}, [messages, programScroll]);", start)]


def test_follow_targets_anchor_top_not_viewport_bottom():
    """流式跟随必须把气泡顶边钉在锚点，不许按底边贴视口底。"""
    body = _follow_body(_code())
    assert "topAbs" in body and "ANCHOR_GAP" in body, (
        "跟随没有用顶边绝对坐标：长回答时开头会被顶出视口"
    )
    # 顶边锚定分支必须由「本会话流过 + 有绝对锚点」门控；历史消息进入时
    # anchorAbsRef 为 null，走底边贴底（切会话落点）。老版本无条件用底边贴底，
    # 气泡长过一屏后顶边被顶出视口 —— 守卫确认流式分支优先用顶边。
    guard = body.index("anchorAbsRef.current != null")
    top_branch = body.index("topAbs", guard)
    bottom_branch = body.index("bottom - el.clientHeight")
    assert guard < top_branch < bottom_branch, (
        "流式分支没有优先用顶边锚定：长回答时开头会被顶出视口"
    )


def test_follow_keeps_top_within_viewport_for_long_answers():
    """气泡长过一屏时，跟随目标就是顶边锚定位本身（不再多滚）。"""
    body = _follow_body(_code())
    assert "Math.max(floor, want)" in body, (
        "长回答没锁住顶边锚定位：want 会随内容增长把顶边顶走"
    )
    assert "bottomInView" in body, (
        "短回答整条可见时没有收手，滚动条会空转"
    )
