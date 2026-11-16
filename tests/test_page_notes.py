"""说明文下沉：页头只留一句主张，其余收进 ⓘ（2026-11-16 P1）。

背景：评审量到四页各在页头摊 2–4 行说明（每屏约 60px），讲的是"这页是干什么的"——
而那属于首次进入该听一次的事。设置页那段更越界：把「权重 70 / 权重来自手选（每次 +3）」
这种内部打分模型念给用户听，他既改不了也用不上。

改法：`sub` 变成一句不超过 20 字的主张常驻，原文挪进 `detail`，由 ⓘ 按需挂载。

这里钉四件事（都是"编译不报错、样式也不坏"的那种）：
  1. 主张真的短（≤20 字）—— 否则等于只是把长句换了个位置；
  2. 四页都确实传了 detail —— 只改 sub 会把口径直接丢掉；
  3. detail 是**条件挂载**，不是 display:none 藏起来；
  4. 设置页的权重数字与算法说明不再出现在界面上。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
SHELL = SRC / "components" / "console" / "PageShell.tsx"
INFO = SRC / "components" / "console" / "InfoNote.tsx"
TOUCH = SRC / "app" / "styles" / "touch.css"

PAGES = {
    "reminders": SRC / "app" / "(console)" / "reminders" / "page.tsx",
    "knowledge": SRC / "app" / "(console)" / "knowledge" / "page.tsx",
    "checkin": SRC / "app" / "(console)" / "checkin" / "page.tsx",
}

# 主张上限。评审给的是 20 字，这里按 20 钉，含标点。
MAX_CLAIM = 20


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _read_code(p: Path) -> str:
    """剥掉注释再断言 —— 注释里写着"不是 display:none"，用原文匹配会自己红自己。"""
    src = _read(p)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def test_claims_are_short():
    """每页常驻的那句必须 ≤20 字，否则只是把长句换了位置。"""
    bad = []
    for name, path in PAGES.items():
        src = _read(path)
        m = re.search(r'sub="([^"]+)"', src)
        assert m, "%s 找不到 sub=\"...\" 形式的主张" % name
        claim = m.group(1).strip()
        if len(claim) > MAX_CLAIM:
            bad.append("%s: %d 字 → %s" % (name, len(claim), claim))
    assert not bad, "主张太长了：\n" + "\n".join(bad)


def test_every_page_kept_its_detail():
    """四页都要把原文挪进 detail，只改 sub 等于把口径删了。"""
    for name, path in PAGES.items():
        src = _read(path)
        assert re.search(r"\bdetail=", src), "%s 没有 detail，说明文被删掉了" % name


def test_detail_is_mounted_conditionally():
    """detail 必须"打开才渲染"，不能靠 display:none 藏（藏起来的孩子还在无障碍树里）。"""
    src = _read_code(INFO)
    assert re.search(r"detail\s*:", src), "InfoNote 没有 detail 参数"
    assert re.search(r"\bopen\s*\?\s*\(", src), "InfoNote 里 detail 不是条件渲染"
    # 兜底：代码里不许出现 display:none 这种藏法（注释已剥掉）
    assert "display:none" not in src.replace(" ", ""), "InfoNote 用 display:none 藏内容"


def test_shell_wires_detail_to_info_note():
    """PageShell 传了 detail 时走 InfoNote，没传时仍是原来的单行 pg-sub。"""
    src = _read(SHELL)
    assert "InfoNote" in src, "PageShell 没有引用 InfoNote"
    assert re.search(r"sub\s*&&\s*detail", src), "PageShell 没有按 detail 分流"
    assert 'className="pg-sub"' in src, "PageShell 丢了纯文本那一支"


def test_info_button_has_touch_room():
    """ⓘ 视觉 18px，热区必须扩到 44（伪元素扩，不改宽高）。"""
    src = _read(TOUCH)
    assert ".info-note-btn::before" in src, "touch.css 里没有给 ⓘ 补热区"


def test_settings_no_longer_explains_weights():
    """设置页不再把内部打分模型念给用户听。"""
    src = _read_code(SRC / "app" / "(console)" / "settings" / "page.tsx")
    assert "权重来自手选" not in src, "权重算法说明还在设置页"
    assert "权重 ${c.weight}" not in src, "还在界面上显示权重数字"
