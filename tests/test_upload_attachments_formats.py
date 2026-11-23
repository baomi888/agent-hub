from __future__ import annotations

"""守卫测试：聊天附件多格式上传 + 内容抽取 + 知识库新格式白名单。

全部真实调用被测代码（upload_attachments / doc_extract / rag_core.load_document /
chat_stream），外部依赖（鉴权、DB、LLM、配额）换成确定性桩。
"""

import asyncio
import base64
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.datastructures import Headers  # noqa: E402

import api.chat as chat  # noqa: E402
import api.kb as kb_api  # noqa: E402
import core.doc_extract as doc_extract  # noqa: E402
import core.session as session  # noqa: E402
import kb.rag_core as rc  # noqa: E402
import main as m  # noqa: E402
from core import config  # noqa: E402
from core.auth import get_current_user  # noqa: E402

OWNER = "u_attach"
SID = "conv_attach"

# 1x1 透明 PNG，够直接当图片附件用
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "AAH/q842iQAAAABJRU5ErkJggg=="
)


def _uf(name: str, data: bytes = b"x", content_type: str | None = None) -> UploadFile:
    kwargs = {"filename": name, "file": io.BytesIO(data)}
    if content_type:
        kwargs["headers"] = Headers({"content-type": content_type})
    return UploadFile(**kwargs)


def _big_png() -> bytes:
    """生成 >1KB 的 PNG，用来走 docx 内嵌图片的体积过滤。"""
    from PIL import Image

    im = Image.new("RGB", (80, 80), (12, 34, 56))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _docx_bytes(text: str = "Word 正文测试：苞米种植密度") -> bytes:
    """最小合法 docx：一段正文 + word/media 里一张图。"""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>" + text + "</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("word/document.xml", document)
        z.writestr("word/media/image1.png", _big_png())
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    """最小合法 xlsx：sharedStrings + 一张工作表。"""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="成绩" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{ns}"><si><t>姓名</t></si><si><t>分数</t></si>'
            "<si><t>张三</t></si><si><t>98</t></si></sst>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{ns}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
            "</sheetData></worksheet>",
        )
    return buf.getvalue()


def _pptx_bytes() -> bytes:
    """最小合法 pptx：一页带一段文字。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>"
            "<a:t>第一页：光合作用</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>",
        )
    return buf.getvalue()


def _pdf_bytes(path: str) -> None:
    """用 PyMuPDF 造一份带文字层的 PDF（测试环境已装，生产代码不依赖它）。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF attachment text: photosynthesis 42")
    doc.save(path)
    doc.close()


@pytest.fixture
def upload_dir(monkeypatch, tmp_path):
    d = tmp_path / "chat_uploads"
    d.mkdir()
    monkeypatch.setattr(chat, "CHAT_UPLOAD_DIR", str(d))
    monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: 0)
    return str(d)


# ==================== 份数闸 ====================


def test_normal_user_10_attachments_400_and_zero_disk(monkeypatch, upload_dir):
    monkeypatch.setattr(chat.quota, "_is_admin", lambda uid: False)
    files = [_uf(f"p{i}.png", _PNG, "image/png") for i in range(10)]

    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat.upload_attachments(files, OWNER))

    assert ei.value.status_code == 400
    assert "最多上传 9 个附件" in ei.value.detail
    assert os.listdir(upload_dir) == [], "超限必须在落盘前就拒绝"


def test_normal_user_9_attachments_allowed(monkeypatch, upload_dir):
    monkeypatch.setattr(chat.quota, "_is_admin", lambda uid: False)
    files = [_uf(f"p{i}.png", _PNG, "image/png") for i in range(9)]

    out = asyncio.run(chat.upload_attachments(files, OWNER))

    assert len(out["files"]) == 9
    assert all(os.path.isfile(f["path"]) for f in out["files"])
    assert len(os.listdir(upload_dir)) == 9


def test_admin_12_attachments_allowed(monkeypatch, upload_dir):
    monkeypatch.setattr(chat.quota, "_is_admin", lambda uid: True)
    files = [_uf(f"p{i}.png", _PNG, "image/png") for i in range(12)]

    out = asyncio.run(chat.upload_attachments(files, "admin_uid"))

    assert len(out["files"]) == 12
    assert len(os.listdir(upload_dir)) == 12


# ==================== 格式白名单与抽取 ====================


def test_non_image_document_attachment_allowed(monkeypatch, upload_dir):
    monkeypatch.setattr(chat.quota, "_is_admin", lambda uid: False)
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    out = asyncio.run(
        chat.upload_attachments([_uf("笔记.docx", _docx_bytes(), mime)], OWNER)
    )
    assert len(out["files"]) == 1
    assert os.path.isfile(out["files"][0]["path"])


def test_unsupported_attachment_rejected_with_format_list(monkeypatch, upload_dir):
    monkeypatch.setattr(chat.quota, "_is_admin", lambda uid: False)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat.upload_attachments([_uf("木马.exe", b"MZ")], OWNER))
    assert ei.value.status_code == 400
    assert "pdf" in ei.value.detail and "docx" in ei.value.detail
    assert os.listdir(upload_dir) == []


def test_docx_text_and_media_image_extracted(monkeypatch, upload_dir):
    out = asyncio.run(chat.upload_attachments([_uf("笔记.docx", _docx_bytes())], OWNER))
    result = doc_extract.extract_attachments(
        out["files"], resolver=chat._resolve_upload_path
    )

    assert len(result.files) == 1
    assert "Word 正文测试" in result.files[0].text
    assert "【附件：笔记.docx】" in doc_extract.format_text_for_prompt(result)
    assert result.total_images >= 1, "docx 内嵌图应作为 image_url 送模型"
    assert doc_extract.extract_images(result)[0].startswith("data:image/")


def test_pdf_text_extracted_from_real_pdf(tmp_path):
    path = str(tmp_path / "paper.pdf")
    _pdf_bytes(path)
    result = doc_extract.extract_attachments(
        [{"filename": "paper.pdf", "path": path, "type": "application/pdf"}],
        resolver=lambda p: p,
    )
    assert "PDF attachment text" in result.files[0].text


def test_xlsx_text_extracted_from_real_xlsx(tmp_path):
    path = tmp_path / "成绩.xlsx"
    path.write_bytes(_xlsx_bytes())
    result = doc_extract.extract_attachments(
        [{"filename": "成绩.xlsx", "path": str(path), "type": "application/vnd.ms-excel"}],
        resolver=lambda p: p,
    )
    assert "工作表：成绩" in result.files[0].text
    assert "张三" in result.files[0].text


def test_pptx_text_extracted_via_rag_core(tmp_path):
    path = tmp_path / "课件.pptx"
    path.write_bytes(_pptx_bytes())
    docs = rc.load_document(str(path), "课件.pptx")
    assert "光合作用" in "\n".join(d.page_content for d in docs)


# ==================== 上限 ====================


def test_extraction_hard_limits(tmp_path):
    attachments = []
    for i in range(4):
        p = tmp_path / f"big{i}.txt"
        p.write_text("啊" * 15000, encoding="utf-8")
        attachments.append({"filename": p.name, "path": str(p), "type": "text/plain"})
    for i in range(12):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(_PNG)
        attachments.append({"filename": p.name, "path": str(p), "type": "image/png"})

    result = doc_extract.extract_attachments(attachments, resolver=lambda p: p)

    assert result.total_text <= 40000 + 200, "整次文本上限没生效"
    assert result.total_images == 8, "整次图片上限没生效"
    assert any("单文件上限" in f.text for f in result.files[:4])
    assert any("整次上限" in f.text for f in result.files[:4])


# ==================== 知识库格式白名单 ====================


def test_kb_upload_accepts_new_formats_without_count_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(kb_api.quota, "guard", lambda *a, **k: None)
    monkeypatch.setattr(kb_api.quota, "consume", lambda *a, **k: 0)
    monkeypatch.setattr(kb_api.pipelines, "ensure_source_dir", lambda kb_id: str(tmp_path))
    monkeypatch.setattr(
        kb_api.pipelines, "safe_source_name", lambda name, fallback_ext="": name
    )
    monkeypatch.setattr(
        kb_api.pipelines,
        "source_path",
        lambda kb_id, name: os.path.join(str(tmp_path), name),
    )

    files = [_uf(f"table{i}.xlsx", _xlsx_bytes()) for i in range(6)] + [
        _uf(f"slide{i}.pptx", _pptx_bytes()) for i in range(6)
    ]
    saved, _ = asyncio.run(kb_api._validate_and_save(OWNER, "kb_x", files, "append"))

    assert len(saved) == 12, "知识库上传不做份数限制"
    assert all(os.path.isfile(path) for path, _ in saved)


def test_defaults_endpoint_exposes_updated_extensions():
    exts = asyncio.run(m.get_defaults())["supported_extensions"]
    assert ".xlsx" in exts and ".pptx" in exts and ".docx" in exts
    assert set(exts) == set(rc.SUPPORTED_EXT)


# ==================== 附件真的参与回答 ====================


class _Chunk:
    def __init__(self, content):
        self.content = content


class _PlainLLM:
    async def astream(self, messages):
        yield _Chunk("普通模型被调用了")


class _CaptureVisionLLM:
    def __init__(self):
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        yield _Chunk("已读附件")


@pytest.fixture
def chat_client(monkeypatch, tmp_path, upload_dir):
    monkeypatch.setattr(session, "_db_path", lambda: os.path.join(str(tmp_path), "sessions.db"))
    session._init_db()
    monkeypatch.setattr(
        chat.store, "get_conversation", lambda uid, sid: {"id": sid, "kb_id": None}
    )
    monkeypatch.setattr(chat.store, "count_messages", lambda uid, sid: 1)
    monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: [])

    def _fake_add(owner, sid, role, content, *a, **k):
        return 1000 if role == "user" else 1001

    monkeypatch.setattr(chat.store, "add_message", _fake_add)
    monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _PlainLLM())
    monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", False)
    vision = _CaptureVisionLLM()
    monkeypatch.setattr(chat, "get_vision_llm", lambda *a, **k: vision)

    m.app.dependency_overrides[get_current_user] = lambda: OWNER
    with TestClient(m.app) as c:
        yield c, vision
    m.app.dependency_overrides.clear()


def test_document_attachment_reaches_vision_llm(chat_client, upload_dir):
    client, vision = chat_client
    uploaded = asyncio.run(
        chat.upload_attachments([_uf("分析.docx", _docx_bytes())], OWNER)
    )["files"]
    body = {
        "sid": SID,
        "question": "这份 Word 里写了什么",
        "mode": "rag",
        "attachments": uploaded,
    }
    r = client.post("/api/chat/stream", json=body)
    assert r.status_code == 200, r.text
    assert "附件理解模式" in r.text

    messages = vision.messages
    assert messages is not None, "带附件必须走 get_vision_llm"
    text = "\n".join(
        p.get("text", "") for p in messages[-1].content if isinstance(p, dict)
    )
    assert "【附件：分析.docx】" in text
    assert "Word 正文测试" in text
    assert "这份 Word 里写了什么" in text
    image_blocks = [
        p for p in messages[-1].content
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert image_blocks, "docx 内嵌图应作为 image_url 一起发送"
