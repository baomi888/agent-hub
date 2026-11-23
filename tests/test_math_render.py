"""数学公式渲染守卫（对应截图里 $$ 与孤立 ** 漏成原文的事故）。

真正的渲染断言在 tools/verify_math_render.mjs：它用 React SSR 跑前端
renderMarkdown，比在 Python 里读源码字符串可靠。这里只负责：
  1) 把 Node 守卫接进 pytest；
  2) 钉住两处关键实现，防止有人把守卫需要的逻辑删掉却仍让它"绿着守空气"。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "frontend" / "src" / "components" / "chat" / "content.tsx"
GUARD = ROOT / "tools" / "verify_math_render.mjs"


def test_node_guard_passes() -> None:
    proc = subprocess.run(
        ["node", str(GUARD.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, f"数学渲染守卫失败:\n{proc.stdout}\n{proc.stderr}"
    passed = [line for line in proc.stdout.splitlines() if line.startswith("PASS ")]
    assert len(passed) >= 9, f"守卫断言数量不足: {proc.stdout}"
    assert "FAIL" not in proc.stdout, proc.stdout


def test_implementation_keeps_both_fixes() -> None:
    src = CONTENT.read_text(encoding="utf-8")
    # ① 块级公式必须能打断普通段落，否则 $$ 被吞进 <p>
    assert "!/^\\s*\\$\\$/.test(lines[i])" in src
    assert "!/^\\s*\\\\\\[/.test(lines[i])" in src
    # ② 公式 token 与粗体在同一层解析，不能先 splitMath 把 ** 切断
    assert "function asMathToken" in src
    assert "const MD_INLINE_RE = new RegExp" in src
    assert "return renderInlinePlain(text, keyPrefix, cites, refAnchor);" in src
    # renderContent（用户消息/引用预览）仍用 splitMath，别顺手删掉
    assert "const segs = splitMath(text);" in src
