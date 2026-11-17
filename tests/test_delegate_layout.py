"""每日委托页的版面重排（2026-11-17）。

评审两条：
  ① 「执行历史换一种显示方式，现在占据太多区域了」；
  ② 「新建委托应该放在『我的委托』的下面」。

第 ②③ 条在手机上是一件事的两面：原来是 grid2 两栏（左列表+历史、右表单常驻），
两栏在手机上塌成一栏 → 顺序变成「我的委托 → 执行历史（最多 50 行）→ 新建表单」，
表单被推到三四屏之外，用户以为这页只能看不能建。

改法：
  · 两栏收成一条纵列：我的委托 → 新建委托 → 执行历史；
  · 执行历史默认只露 3 条 + 一行「近 N 次 · 成功 X · 失败 Y」，其余收进「查看全部」；
  · 表单在宽屏走两列（字段横跨 980px 会长得像正文），≤760 塌回一列。

这里钉的是"顺序 / 折叠 / 两列"这三件靠肉眼才看得出来的事。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
PAGE = SRC / "app" / "(console)" / "delegate" / "page.tsx"
CONSOLE_CSS = SRC / "app" / "styles" / "console.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    src = re.sub(r"/\*.*?\*/", "", _read(p), flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_page_is_single_column():
    """两栏没了：grid2 会把表单推到右栏（手机上又被历史挤到最底）。"""
    src = _code(PAGE)
    assert '"grid2"' not in src, "委托页又在用两栏了"
    assert 'className="del-page"' in src, "缺单列容器 .del-page"


def test_form_sits_between_list_and_history():
    """顺序必须是 我的委托 → 新建委托 → 执行历史。"""
    src = _code(PAGE)
    i_list = src.index("我的委托")
    i_form = src.index("del-form-grid")
    i_hist = src.index("执行历史")
    assert i_list < i_form < i_hist, (
        "顺序不对（我的委托 %d / 表单 %d / 历史 %d）—— 表单必须紧跟在列表下面"
        % (i_list, i_form, i_hist)
    )


def test_history_is_collapsed_by_default():
    """执行历史默认只露 HIST_PEEK 条，其余靠按钮展开。"""
    src = _code(PAGE)
    m = re.search(r"const\s+HIST_PEEK\s*=\s*(\d+)", src)
    assert m, "缺 HIST_PEEK 常量"
    assert int(m.group(1)) <= 5, "默认露出的条数太多，等于没折叠"
    assert re.search(r"histOpen\s*\?\s*runItems\s*:\s*runItems\.slice\(0,\s*HIST_PEEK\)", src), (
        "渲染时没有按 histOpen 折叠"
    )
    assert re.search(r"aria-expanded=\{histOpen\}", src), "展开按钮缺 aria-expanded"
    assert "查看全部" in src, "缺「查看全部 N 条」的入口"


def test_history_summarises_success_and_failure():
    """一行汇总：把"数失败次数"这件事从用户手里接过来。"""
    src = _code(PAGE)
    assert re.search(r'status\s*===\s*"succeeded"', src), "没算成功次数"
    assert re.search(r'status\s*===\s*"failed"', src), "没算失败次数"
    assert "runOk" in src and "runBad" in src, "汇总没接进页面"


def test_form_is_two_columns_on_desktop_one_on_mobile():
    """宽屏两列、窄屏一列；靠 .wide 显式跨行，别指望自动排布。"""
    src = _code(PAGE)
    # 9 处：名称、触发时间、生成指令、语气/长度、启用开关、删除工具、两段提示、动作条
    assert len(re.findall(r'\bwide\b', src)) >= 8, (
        "标了 wide 的字段太少，网格里会漏成半列"
    )
    css = _code(CONSOLE_CSS)
    assert re.search(
        r"\.del-form-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2", css
    ), "缺两列定义"
    assert re.search(r"\.del-form-grid\s*>\s*\.wide\s*\{[^}]*grid-column:\s*1\s*/\s*-1", css), (
        "缺 .wide 的跨行规则"
    )
    # 塌回一列的那一档
    tail = css[css.index(".del-form-grid") :]
    assert re.search(r"@media\s*\(max-width:\s*760px\)\s*\{[^}]*\.del-form-grid", tail, re.S), (
        "≤760 没有塌回一列 —— 手机上两列会把字压成两三个一行"
    )
