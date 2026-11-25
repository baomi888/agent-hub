"""守卫豆包/DeepSeek 式输入面板（2026-11-25 codex 工作区改动）。

用户反馈：模仿 DeepSeek 的输入药丸 + 豆包的输入附件显示（手机底部面板：
拍照/相册/文件；桌面浮层：上传/拍照/截图提问）。此前 addFiles 的 9 个上限、
提示与发送键常驻是本批最容易被新 UI 覆盖的既有修复，必须一起钉住。

反证口径（硬约束 18）：
  · 数量下限：菜单项 >=3、面板动作 >=3、hidden file input >=3；
  · 不写 `not in` 永真断言 —— 每个「必须有」都配一条数量或结构断言；
  · 文案按实际字面量（"检索/智能联网"、"拍照/相册/文件"），
    不把参考图里的 DeepSeek 文案当成我们的产品文案。
"""

from pathlib import Path
import re

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(s: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/|(?<!:)//[^\n]*", "", s)


CHATAREA = SRC / "components" / "ChatArea.tsx"
KB_CSS = SRC / "app" / "styles" / "kb.css"


def test_chatarea_has_mobile_sheet_and_desktop_popover():
    src = _strip_comments(_read(CHATAREA))
    # 两套入口缺一不可：手机底部面板 + 桌面浮层（数量下限防「扫描缺数量」假绿）
    assert "attach-sheet-backdrop" in src and "attach-sheet" in src, "手机附件面板缺失"
    assert "attach-popover" in src, "桌面附件浮层缺失"
    assert src.count("attach-menu-item") >= 3, "桌面浮层菜单项不足 3 个"
    assert src.count("attach-sheet-actions") >= 1, "手机面板动作区缺失"
    # 三个入口的实际字面量（不是参考图里的「照片图库/选取文件」）
    for label in ("拍照", "相册", "文件"):
        assert f"<b>{label}</b>" in src, f"手机面板缺入口：{label}"
    for label in ("上传文件或图片", "截图提问"):
        assert label in src, f"桌面浮层缺入口：{label}"


def test_camera_and_screenshot_inputs():
    # ★ 用原文：JSX 的 accept="image/*" 里有 `/*`，会被 CSS 注释剥离正则误伤，
    #   把同一个 input 上的 capture="environment" 一起吃掉（本守卫第一版踩过）。
    src = _read(CHATAREA)
    # 手机拍照走 capture input，桌面走 getUserMedia 预览；截图走 getDisplayMedia
    assert 'capture="environment"' in src, "手机拍照没有 capture input"
    assert "getUserMedia" in src and "cameraVideoRef" in src, "桌面相机预览缺失"
    assert "getDisplayMedia" in src and "canvasToBlob" in src, "截图提问链路缺失"
    # 流必须停掉，否则摄像头灯常亮（用户会当成 bug）
    assert "stopMediaStream" in src and "getTracks" in src, "媒体流没有释放"
    # hidden input >=3：原始附件 + 相册 + 相机
    assert src.count('className="hidden"') >= 3, "hidden file input 数量不足"


def test_attachment_cap_survives_new_panel():
    """新面板不得吃掉上一批的 9 个上限 + 超限提示 + 发送键常驻。"""
    src = _strip_comments(_read(CHATAREA))
    assert "maxAttachments" in src, "新面板改动丢了 maxAttachments 上限"
    assert "一次最多上传" in src and "多出来的" in src, "超限提示文案丢失"
    assert "attach-items" in src, "附件预览滚动容器丢失（发送键会被顶出屏幕）"
    # attachNote 三分支都在：图片+文档 / 纯图片 / 纯文件
    assert "张图片 + " in src and "个文档将一起解析" in src, "混合附件提示丢失"
    assert "个文件将提取文字和图片内容" in src, "纯文件提示丢失"


def test_css_sheet_is_mobile_only_with_safe_touch_targets():
    css = _strip_comments(_read(KB_CSS))
    # 桌面默认隐藏、窄屏才显示 —— 用两条断言而不是一条「不在」永真
    assert re.search(r"\.attach-sheet,\s*\.attach-sheet-backdrop\s*\{\s*display:\s*none", css), (
        "桌面没有默认隐藏手机面板"
    )
    m = re.search(r"@media \(max-width: 899px\)\s*\{[\s\S]*?\.attach-sheet\s*\{([\s\S]*?)\n  \}", css)
    assert m, "没有 899px 以下的手机面板媒体查询"
    body = m.group(1)
    assert "position: fixed" in body and "display: block" in body, "手机面板不是 fixed 弹层"
    assert "env(safe-area-inset-bottom" in body, "手机面板没有避让 iPhone 底部横条"
    # 触控目标 >=44px（硬约束 39）：菜单项与动作按钮都要量到
    assert "min-height: 54px" in css, "浮层菜单项低于 44px 触控基准"
    assert "min-height: 88px" in css, "手机面板动作按钮低于 44px 触控基准"
