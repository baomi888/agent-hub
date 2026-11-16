"""首屏「继续上次」的三条不变量。

为什么值得单独守：这块内容**没有历史会话时必须整块不渲染**，而"最容易
被当成历史会话"的那一条，恰恰叫「新对话」—— 后端建会话时 title 默认就是
它（api/sessions.py:26），不是空串。于是"只判空"的写法会显示成
「继续上次 · 新对话 · 刚刚」，等于什么都没说，而且**不会报任何错**。

真实踩过：第一版按 `title.trim()` 过滤，本地截图里那一行就是「新对话」。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend/src/app/page.tsx"
AREA = ROOT / "frontend/src/components/ChatArea.tsx"
STRIP = ROOT / "frontend/src/components/chat/ResumeStrip.tsx"
SEDIMENT = ROOT / "core/sediment_store.py"


def _py_placeholders() -> set[str]:
    """后端沉淀模块里那串占位名（与建会话默认标题同一套口径）"""
    src = SEDIMENT.read_text(encoding="utf-8")
    m = re.search(r'title not in \(([^)]+)\)', src)
    assert m, "core/sediment_store.py 里找不到占位名元组"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _ts_placeholders() -> set[str]:
    """首页里那份常量数组"""
    src = PAGE.read_text(encoding="utf-8")
    m = re.search(r"const PLACEHOLDER = \[([^\]]+)\]", src)
    assert m, "page.tsx 里找不到 PLACEHOLDER 常量"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_placeholder_titles_match_between_frontend_and_backend():
    """占位名两边必须**逐字相等**。

    单边改动会静默失效：后端把默认名改成「未命名」而前端还排「新对话」，
    首页会把每个刚建的会话当成"上次聊的"；反过来前端漏一个，那条会话
    就永远排在候选第一位，真正的上一次永远出不来。
    """
    py, ts = _py_placeholders(), _ts_placeholders()
    assert py == ts, f"占位名不一致：后端 {sorted(py)} / 前端 {sorted(ts)}"
    # 「新对话」必须在里面 —— 它是后端的真实默认值，去掉这条守卫就失效了
    assert "新对话" in py


def test_resume_strip_renders_nothing_without_session():
    """没有会话时不能留下一个空标题行。

    头部标题（「继续上次」+ 分隔线）在 JSX 里，只要组件返回 null，
    整块 section 就不会挂载 —— 所以判据是"先 return null 再 return JSX"。
    """
    src = STRIP.read_text(encoding="utf-8")
    # 第一个 return 必须是 null（守卫在渲染之前）
    first_return = src.split("return", 1)[1][:40]
    assert "null" in first_return, "ResumeStrip 的第一条 return 不是 null —— 空态会渲染出标题"
    assert "welcome-section-head" in src, "标题结构不在，说明这块被改写成别的形态了"


def test_home_passes_resume_props_into_chat_area():
    """首页必须把两者都传给 ChatArea。

    漏传不会有编译错误（两个 prop 都是可选的），只会让整块永远不出现
    —— 这种"功能消失但没有测试变红"的形态最难发现。
    """
    page = PAGE.read_text(encoding="utf-8")
    assert "resumeSession={resumeSession}" in page
    assert "onResumeSession=" in page
    # 组件必须真的把它转给 ResumeStrip，否则只是把数据搬了个地方
    area = AREA.read_text(encoding="utf-8")
    assert "<ResumeStrip" in area and "session={resumeSession}" in area


def test_resume_strip_only_renders_when_today_strip_is_absent():
    """有「今天」条时整块不能挂 —— 那 118px 空白根本不存在。

    线上实测（2026-11-16，393×852 有安全区）：
      · 无今天条：欢迎页 529，可用 537 → 有空可以填
      · 有今天条 + 提醒条：577，**本来就超出 40px**
    加了这 71px 之后，那块被输入区压住 34px（visible:false / gapToDock:-34），
    要滑 52px 才看得见 —— 对最活跃的那批人等于白加一屏滚动。

    这个闸写在 JSX 的条件里，编译不会报错、样式也不会坏，只能源码断言。
    """
    area = AREA.read_text(encoding="utf-8")
    # 找 <ResumeStrip 之前最近的 40 字，必须带 todayHasAny 条件
    idx = area.find("<ResumeStrip")
    assert idx > 0, "ChatArea 里找不到 <ResumeStrip"
    before = area[max(0, idx - 200) : idx]
    assert "todayHasAny" in before, (
        "ResumeStrip 没有受 todayHasAny 控制 —— 有今天条的用户会多出一屏滚动"
    )
