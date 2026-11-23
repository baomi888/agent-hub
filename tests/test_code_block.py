"""代码块卡片（提示词）显示守卫 —— 2026-11-23。

用户截图反馈：长提示词在深色 <pre white-space:pre> 里被横向截断，
只能看到半截英文；且不像豆包有复制/下载/展开。

修复：围栏代码块改用 CodeBlock 卡片组件：
  · content.tsx 渲染 <CodeBlock> 而非裸 <pre>
  · CodeBlock 换行完整显示（pre-wrap + 任意处断行）
  · 卡片头三枚按钮：复制 / 下载 / 展开
  · 展开走 portal 全屏查看（避开抽屉 transform 裁切）

这些断言读源码静态守，剥注释后判，避免"注释里写了关键词"的假绿。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "frontend" / "src" / "components" / "chat" / "CodeBlock.tsx"
CONTENT = ROOT / "frontend" / "src" / "components" / "chat" / "content.tsx"
CSS = ROOT / "frontend" / "src" / "app" / "styles" / "components.css"


def _code(p: Path) -> str:
    s = p.read_text(encoding="utf-8")
    assert s, f"{p} 是空的？"
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?<![:\w])//[^\n]*", "", s)
    return s


def _css() -> str:
    s = CSS.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


# ---- 接线：渲染走 CodeBlock，不再裸 pre ----

def test_fenced_code_uses_codeblock_component():
    src = _code(CONTENT)
    # 直接找围栏分支里渲染的是 CodeBlock（剥注释后）
    block = src[src.index('if (/^\\s*```/.test(line))'):]
    block = block[: block.index("continue;")]
    assert "<CodeBlock" in block, "围栏代码块还在用裸 <pre>，没换成 CodeBlock 卡片"
    assert 'className="md-pre"' not in block, "围栏仍渲染 .md-pre（会横向截断）"
    assert "import CodeBlock" in src, "content.tsx 没有引入 CodeBlock"


# ---- 换行：核心修复，内容要完整可见 ----

def test_code_body_wraps_long_lines():
    css = _css()
    m = re.search(r"\.md-code-body\s*\{([^}]*)\}", css)
    assert m, "找不到 .md-code-body 样式"
    body = m.group(1)
    assert "white-space: pre-wrap" in body, "没开 pre-wrap：长提示词仍会横向截断"
    assert "overflow-wrap: anywhere" in body, "长英文单词/URL 不能在任意处断行"


def test_code_body_no_longer_uses_pre_nowrap():
    """旧 .md-pre code 的 white-space:pre 是截断根因，新卡片不能继承它。"""
    css = _css()
    body = re.search(r"\.md-code-body\s*\{([^}]*)\}", css).group(1)
    assert "white-space: pre;" not in body, "又回到不换行了"


# ---- 三枚按钮：复制 / 下载 / 展开 ----

def test_card_has_copy_download_expand_actions():
    src = _code(CODE)
    assert "copy(code)" in src, "复制按钮没接 copy(code)"
    assert 'aria-label="复制"' in src, "缺复制按钮"
    assert 'aria-label="下载"' in src, "缺下载按钮"
    assert 'aria-label="展开"' in src, "缺展开按钮"
    assert "new Blob([code]" in src, "下载没用 Blob 导出文本"


def test_download_filename_is_sanitized():
    """下载名不能让 lang 里的路径分隔符污染。"""
    src = _code(CODE)
    assert r"[^\w\u4e00-\u9fa5-]" in src, "下载名没做安全字符白名单"
    assert "prompt-${safe}.txt" in src, "下载名格式不对"


# ---- 展开用 portal，避开抽屉 transform ----

def test_expand_uses_portal():
    src = _code(CODE)
    assert "createPortal" in src, "展开没用 portal，右栏抽屉的 transform 会裁切全屏层"
    assert "document.body" in src, "portal 没挂到 body"
    assert 'aria-modal="true"' in src, "全屏层缺 modal 语义"


def test_expand_closes_on_escape():
    src = _code(CODE)
    assert 'e.key === "Escape"' in src, "Esc 关不掉全屏层"
