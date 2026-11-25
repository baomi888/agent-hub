"""守卫「点开附件预览」：图片本地放大、PDF 内嵌、文档走临时解析且绝不入库。

用户口径：像豆包/DeepSeek 一样点一下就能看。反证重点：
  · 后端只能抽正文，不能复用资料库上传链路（否则会把临时文件塞进用户的库）；
  · 两处入口（输入行附件条 / 手机附件面板）都要能点，消息气泡里的也要能点；
  · 图片不该走后端（blob URL 直接放大，零延迟）；
  · 触控热区扩区不能抢到旁边删除按钮的点区。
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from api import capture
from core.auth import get_current_user

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_preview_endpoint_is_temporary_and_bounded():
    src = _read(ROOT / "api" / "capture.py")
    assert '@router.post("/preview")' in src, "没有附件预览端点"
    block = re.search(r'@router\.post\("/preview"\)[\s\S]*?\n@router\.post\("/transcribe"\)', src)
    assert block, "预览端点结构变了，守卫定位不到函数体"
    body = block.group(0)
    assert "Depends(get_current_user)" in body, "预览端点没有鉴权"
    assert "SUPPORTED_EXT" in body and "PREVIEW_MAX_BYTES" in body, "没有格式白名单与大小上限"
    assert "413" in body, "超限没给 413，前端拿不到可行动的提示"
    assert "doc_extract.extract_attachments" in body, "没有复用聊天附件的抽取口径"
    assert "mkstemp" in body and "os.unlink" in body, "临时文件写完关闭再读，读完必须删"
    # 反证：不能出现资料库写入链路
    for forbidden in ("pipelines.", "source_path(", "save_stream(", "insert_file("):
        assert forbidden not in body, f"预览意外写入了资料库：{forbidden}"


def test_front_end_wires_preview_into_all_entry_points():
    modal = _read(SRC / "components" / "chat" / "AttachmentPreviewModal.tsx")
    assert "createPortal" in modal and 'aria-modal="true"' in modal, "预览不是可访问的 portal 弹层"
    assert 'kind === "image"' in modal and "apv-img" in modal, "图片没有走本地大图预览"
    assert '<iframe className="apv-frame"' in modal, "PDF 没有内嵌查看器"
    assert "api.capture.previewAttachment" in modal, "文档没有走后端临时解析"
    assert '"Escape"' in modal and "onMouseDown" in modal, "Esc / 点遮罩关闭缺失"

    chat = _read(SRC / "components" / "ChatArea.tsx")
    assert chat.count("setPreviewAttachment(a)") >= 2, "输入行与手机面板两处入口必须都能点开"
    assert "previewAttachment &&" in chat, "预览弹窗没有挂到聊天页"
    bubble = _read(SRC / "components" / "chat" / "Bubble.tsx")
    assert "onPreviewAttachment" in bubble and "onClick={() => onPreviewAttachment(a)}" in bubble, (
        "已发消息里的附件点不开"
    )

    api_src = _read(SRC / "lib" / "api.ts")
    assert '"/api/capture/preview"' in api_src and "previewAttachment:" in api_src, "前端没接预览接口"
    assert "AttachmentPreviewData" in _read(SRC / "lib" / "types.ts"), "预览响应没有类型"

    css = _read(SRC / "app" / "styles" / "kb.css")
    assert re.search(r"\.apv-mask\s*\{[\s\S]*?z-index:\s*96", css), "预览层级低于拍照/截图弹层"
    assert ".apv-img.is-zoomed" in css and ".attach-preview-trigger" in css, "大图放大或附件触发样式缺失"
    touch = _read(SRC / "app" / "styles" / "touch.css")
    hit = re.search(r"\.attach-preview-trigger::before,[\s\S]*?\.msg-file-btn::before\s*\{([\s\S]*?)\n  \}", touch)
    assert hit and "inset: -4px 0" in hit.group(1), "附件热区没补到 44 或会抢删除按钮点区"


@pytest.fixture()
def client():
    main.app.dependency_overrides[get_current_user] = lambda: "preview-user"
    yield TestClient(main.app, raise_server_exceptions=True, follow_redirects=False)
    main.app.dependency_overrides.clear()


def test_txt_preview_returns_real_text(client):
    r = client.post(
        "/api/capture/preview",
        files={"file": ("笔记.txt", "知识就是力量".encode("utf-8"))},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["filename"] == "笔记.txt"
    assert "知识就是力量" in data["text"]
    assert data["truncated"] is False


def test_docx_preview_extracts_body(client):
    """用最小 docx 走真实 zip+xml 抽取，确认不是只返回空壳。"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>DOCX预览正文</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    r = client.post("/api/capture/preview", files={"file": ("课件.docx", buf.getvalue())})
    assert r.status_code == 200, r.text
    assert "DOCX预览正文" in r.json()["text"]


def test_unsupported_empty_and_oversize_are_rejected(client, monkeypatch):
    bad = client.post("/api/capture/preview", files={"file": ("木马.exe", b"MZ")})
    assert bad.status_code == 400, bad.text
    assert "在线预览" in bad.json()["detail"]

    empty = client.post("/api/capture/preview", files={"file": ("空.txt", b"")})
    assert empty.status_code == 400

    # 把上限临时压小，避免真的构造 20MB 请求拖慢全量
    monkeypatch.setattr(capture, "PREVIEW_MAX_BYTES", 3)
    big = client.post("/api/capture/preview", files={"file": ("大.txt", b"abcd")})
    assert big.status_code == 413
