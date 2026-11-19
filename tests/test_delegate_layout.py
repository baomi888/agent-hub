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


def test_form_is_a_modal_not_embedded_in_page():
    """2026-11-19：表单从页面常驻改成点按钮才弹出的弹窗。

    原来表单 1000px 高常驻在「我的委托」和历史之间，手机上还在屏幕底部钉一条
    position:fixed 的动作条（用户看到的"黑条+按钮"）。现在：
      · 表单渲染只在 formOpen 为真时发生（{formOpen && <DialogShell ...>}）；
      · 走 DialogShell（portal / 焦点陷阱 / Esc / 焦点归还都现成）；
      · 旧的 form-actions / is-fixed / IntersectionObserver 探测全部删除。

    ★ 不能只看 "del-form-grid" 在不在 —— 弹窗里这个类名还在。要钉「表单被
      formOpen 门控」且「页面流里没有固定动作条」。
    """
    src = _code(PAGE)
    assert "DialogShell" in src, "表单没走 DialogShell（焦点/Esc/portal 会漏）"
    assert re.search(r"\{formOpen\s*&&\s*\(\s*<DialogShell", src), (
        "表单没有 formOpen 门控 —— 又变成常驻页面了"
    )
    assert "wide" in src, "委托表单字段多，必须用宽版弹窗（默认 460 放不下两列）"
    # 黑条三件套必须彻底消失
    for gone in ("form-actions", "is-fixed", "formInView", "IntersectionObserver", "formRef"):
        assert gone not in src, f"旧的固定动作条残留：{gone}"
    css = _code(CONSOLE_CSS)
    assert ".form-actions.is-fixed" not in css, "CSS 里固定动作条规则没删"
    assert ":has(.form-actions.is-fixed)" not in css, "CSS 里给固定条补的留白没删"


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


def test_all_form_entry_points_open_the_modal():
    """三条入口（新建按钮 / 示例预填 / 编辑 / 模板预填）都必须同时 setFormOpen。

    ★ 不能只测"button 存在"：改成弹窗后最常见的回归是某个入口只 setForm、
      忘了 setFormOpen —— 用户点了没反应，而按钮好端端在页面上。
    """
    src = _code(PAGE)
    # openForm 是唯一开关：setForm + setFormOpen(true) 打包在一个 callback 里
    assert re.search(r"const\s+openForm\s*=\s*useCallback", src), "缺 openForm 统一入口"
    assert re.search(r"setForm\(next\);\s*setFormOpen\(true\)", src), (
        "openForm 没同时打开弹窗"
    )
    # 新建按钮、示例、编辑三处都调 openForm，且页面里已无裸 setForm(...) 打开表单
    assert src.count("openForm(") >= 4, (
        "openForm 调用点不足 4（新建 / 示例 / 编辑 / 模板预填各一处）"
    )
    # 预览浮层 z60，弹窗遮罩 z97：预览时先关弹窗，否则预览被压在底下
    assert re.search(
        r"if\s*\(dryRun\)\s*\{[^}]*setFormOpen\(false\)", src, re.S
    ), "预览时没先关弹窗 —— 会被 portal 的遮罩盖住"


def test_wide_dialog_css_exists():
    """宽版弹窗样式必须在（否则 720px 的 class 没有对应规则，仍是 460）。"""
    css = _code(ROOT / "frontend" / "src" / "app" / "styles" / "account.css")
    assert re.search(r"\.acct-dlg--wide\s*\{[^}]*width:\s*min\(720px", css), (
        "缺 .acct-dlg--wide 宽度规则"
    )
