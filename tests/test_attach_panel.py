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


# ==================== 方案 A：最近使用图片网格（2026-11-25 用户确认） ====================
# 用户诉求：「点+就能有预览的一部分图片供选择」。网页读不到整本相册（沙箱），
# 但用户选过的图片能缓存下来复用 —— 这是纯网页最接近 App 相册网格的形态。
# 三条必须同时在：缓存模块（IndexedDB）+ 面板第一屏网格 + 点选走同一条 addFiles。

RECENT_TS = SRC / "lib" / "recentImages.ts"


def test_recent_images_module_exists_and_bounded():
    """缓存模块要用 IndexedDB、有数量上限、失败静默降级、可清空。"""
    src = _strip_comments(_read(RECENT_TS))
    assert "indexedDB.open" in src, "最近图片没有用 IndexedDB 落盘"
    assert "MAX_ITEMS" in src and "excess" in src, "缓存没有数量上限：IndexedDB 会无限膨胀"
    # 失败降级：openDb 的 onerror 返回 null，调用方判空后 return（不能抛到主流程）
    assert src.count("resolve(null)") >= 2, "IndexedDB 失败没有静默降级"
    # 可清空（隐私：用户能一键删除本地缓存）
    assert "export async function clearRecentImages" in src, "缓存没有清空入口"


def test_mobile_sheet_shows_recent_grid_before_actions():
    """手机面板第一屏铺最近图片，点选走 addFiles（不另起状态分叉）。"""
    src = _strip_comments(_read(CHATAREA))
    # 面板里必须有最近区块，且在「待发送预览」之前（第一屏就能看到）
    assert "attach-sheet-recent" in src, "手机面板没有最近使用区块"
    assert src.count("attach-sheet-recent-item") >= 1, "最近图片没有可点选的缩略图按钮"
    recent_pos = src.index("className=\"attach-sheet-recent\"")
    preview_pos = src.index("className=\"attach-sheet-preview-row\"")
    assert recent_pos < preview_pos, "最近图片区块不在待发送预览之前：第一屏看不到"
    # 点选走 addFiles 同一条链路（去重/上限/提示复用）
    assert "pickRecentImage" in src and "addFiles([file])" in src, (
        "最近图片没有走 addFiles：会绕开 9 个上限和去重"
    )
    # objectURL 生命周期：打开读缓存建 URL、关闭/卸载 revoke，否则内存泄漏
    assert src.count("URL.revokeObjectURL") >= 3, "objectURL 回收点不足（关闭/清空/选图）"
    # 缓存写入只在 addFiles 里、且非阻塞
    assert "saveRecentImages" in src and "void saveRecentImages" in src, (
        "选过的图片没有写进最近缓存"
    )
    # 清理按钮接到 clearRecent
    assert "onClick={clearRecent}" in src, "最近图片没有清空按钮"


def test_recent_grid_css_is_mobile_only_with_touch_targets():
    css = _strip_comments(_read(KB_CSS))
    m = re.search(r"@media \(max-width: 899px\)\s*\{[\s\S]*?\.attach-sheet-recent-row\s*\{([\s\S]*?)\n  \}", css)
    assert m, "最近图片网格没有放进手机媒体查询"
    assert "overflow-x: auto" in m.group(1), "最近图片横排不能滑动"
    # 缩略图 60px >= 44px 触控基准（硬约束 39）
    item = re.search(r"\.attach-sheet-recent-item\s*\{([\s\S]*?)\n  \}", css)
    assert item, "最近图片项缺样式"
    assert "60px" in item.group(1), "最近图片缩略图低于触控基准"


# ==================== 截图框选 + 聊天语音输入（2026-11-25 用户反馈） ====================
# 用户两条反馈：① 电脑端截图只能截我们自己的平台、不能选区域；
# ② 要加一个语音识别模块。两条都基于已有的浏览器/后端能力，守卫按「数据 + 逻辑」双层。

CROPPER_TSX = SRC / "components" / "chat" / "ScreenshotCropper.tsx"


def test_screenshot_can_pick_window_and_then_crop_region():
    src = _strip_comments(_read(CHATAREA))
    # ★ 逻辑断言：preferCurrentTab 会把选择器锁在当前标签页，这正是用户说的
    #   「截不到平台以外」。不能只断言 getDisplayMedia 存在（原来也有）。
    assert "getDisplayMedia({ video: true, audio: false })" in src, (
        "截图没有放开 displaySurface 选择，仍可能锁在当前标签页"
    )
    assert "preferCurrentTab" not in src, "preferCurrentTab 还在：又锁回当前标签页了"
    # 数据链路：整帧存 canvas → dataURL 给框选层 → 确认时按框裁切 → addFiles
    assert "shotCanvasRef" in src and "canvas.toDataURL" in src, "整帧画面没有交给框选层"
    assert "<ScreenshotCropper" in src and "confirmScreenshot" in src, "框选结果没接回附件链路"
    assert "ctx.drawImage(source, rect.x, rect.y" in src, "确认时没有按选区裁切原始像素"
    assert "addFiles([file])" in src, "裁剪结果没有走 addFiles（会绕开 9 个上限和去重）"


def test_cropper_uses_portal_pointer_drag_and_source_scale():
    src = _strip_comments(_read(CROPPER_TSX))
    assert "createPortal(" in src and "document.body" in src, "框选层没有 portal 到 body，会被抽屉 transform 裁掉"
    # 三个指针事件缺一不可：down 起点 / move 跟随 / up 定框
    for handler in ("onPointerDown", "onPointerMove", "onPointerUp"):
        assert handler in src, f"框选缺 {handler}"
    assert "setPointerCapture" in src, "拖出图片边界后选区会丢"
    # ★ 换算逻辑：显示坐标必须乘比例才是源图像素（只验有按钮会漏掉错位）
    assert "display.scale" in src and "Math.round(box.x * display.scale)" in src, (
        "选区没有换算回原始像素，裁出来位置会偏"
    )
    assert "box.w >= 6" in src and "box.h >= 6" in src, "误触的小框也会变成一张空图"
    assert "整张使用" in src and "用这一块" in src, "缺整张/选区两个出口"


def test_cropper_css_is_above_camera_and_has_no_scroll():
    css = _strip_comments(_read(KB_CSS))
    mask = re.search(r"\.shot-crop-mask\s*\{([\s\S]*?)\n\}", css)
    assert mask, "框选遮罩没有样式"
    z = re.search(r"z-index:\s*(\d+)", mask.group(1))
    assert z and int(z.group(1)) >= 90, "框选遮罩层级低于拍照弹窗，会被压住"
    box = re.search(r"\.shot-crop-image-box\s*\{([\s\S]*?)\n\}", css)
    assert box and "touch-action: none" in box.group(1), "拖拽选区时页面会跟着滚"


def test_chat_voice_input_reuses_backend_asr_with_fallback():
    # ★ 用原文：JSX 的 accept="image/*" 会被注释剥离正则当成块注释起点，
    #   一路吃到语音按钮（本文件上面那条守卫踩过，别再用 stripped src）。
    src = _read(CHATAREA)
    assert "useVoiceInput" in src, "聊天框没有接语音识别 hook"
    assert "api.capture" in src and ".capabilities()" in src, "没有先问后端是否配了 ASR 密钥"
    gate = src.index("{voiceOn ? (")
    mic = src.index("voice-mic")
    assert gate < mic < gate + 1500, (
        "麦克风没有按 capabilities 门控：没配密钥时会渲染点了没反应的按钮"
    )
    # 结果是「追加」而不是覆盖，且走流式帧显示中间文本
    assert "setInput((prev)" in src and "onText:" in src, "转写结果没有追加到输入框"
    assert "onFrame:" in src and 'frame.type === "partial"' in src, "流式没有边说边出字"
    assert "voice.stop()" in src and "voice.start()" in src, "麦克风没有开始/停止闭环"
    # 提醒抽屉的 hook 必须保留两条路（流式 + 一句话降级），不能复制出第二套
    # 同样用原文：`${proto}//${host}` 里的 `//` 会被注释剥离正则吃掉整行
    hook = _read(SRC / "components" / "reminders" / "useVoiceInput.ts")
    assert "/api/capture/ws" in hook and "api.capture.transcribe" in hook, (
        "语音只有一条路：缺 APPID 时没法降级成一句话识别"
    )


def test_voice_mic_and_live_strip_css():
    css = _strip_comments(_read(KB_CSS))
    mic = re.search(r"\.voice-mic\s*\{([\s\S]*?)\n\}", css)
    assert mic, "麦克风按钮没有样式"
    assert "width: 40px" in mic.group(1) and "height: 40px" in mic.group(1), "麦克风与发送键不同高"
    # 手机档发送键在 chat.css 里缩到 32，麦克风必须同步缩，
    # 否则截图里会出现「麦克风大一圈、发送小一圈」的错位。
    mobile = re.search(r"@media \(max-width: 560px\)\s*\{[\s\S]*?\.voice-mic\s*\{([\s\S]*?)\n  \}", css)
    assert mobile, "麦克风没有跟随手机档发送键的 560 断点"
    assert "width: 32px" in mobile.group(1) and "height: 32px" in mobile.group(1), (
        "手机端麦克风与 32px 发送键不同高"
    )
    mobile_svg = re.search(r"@media \(max-width: 560px\)\s*\{[\s\S]*?\.voice-mic svg\s*\{([\s\S]*?)\n  \}", css)
    assert mobile_svg and "width: 14px" in mobile_svg.group(1), "手机端麦克风图标没跟发送键同步缩小"
    assert ".voice-mic.recording" in css and ".voice-mic-stop" in css, "录音中/停止态没有视觉区分"
    assert ".voice-live-strip" in css and "voice-live-pulse" in css, "边说边出字没有展示层"
    # 视觉缩到 32 后热区仍要补到 44（硬约束 39），但不能横扩：
    # 麦克风和发送中间只有 8px gap，两边都整圈扩会重叠。
    touch = _strip_comments(_read(SRC / "app" / "styles" / "touch.css"))
    hit = re.search(r"\.voice-mic::before\s*\{([\s\S]*?)\n  \}", touch)
    assert hit and "inset: -6px 0" in hit.group(1), "麦克风触控热区没补到 44 或会与发送键重叠"
    assert re.search(r"\n  \.voice-mic,\n", touch), "麦克风没有 position:relative，热区伪元素会跑位"
