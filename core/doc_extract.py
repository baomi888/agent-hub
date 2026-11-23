from __future__ import annotations

"""聊天附件内容抽取：把上传件变成视觉模型能直接消费的文本 + 图片。

聊天附件不再只有图片：Word / PDF / Excel / PPT / 纯文本都要能参与回答。
本模块只做"抽内容"，不做鉴权与落盘——路径的白名单校验由调用方
（`api.chat._resolve_upload_path`）负责，这里只处理已经认主的文件。

设计上的几条硬约束：

* **可选依赖全部降级**：PyMuPDF（fitz）、Pillow 装了就用，没装/坏了也不能让上传分析报错；
* **硬上限**：单文件 12000 字、整次 40000 字、每文件 4 张图、整次 8 张图，
  超出留截断标记。附件是用户可控输入，不卡住就是给 token 账单和内存开洞；
* **旧二进制格式**（.doc/.xls/.ppt）没有纯标准库解析器，上传仍放行，
  但给模型一段"无法抽取文字，请另存为新格式"的提示，不假装读到了内容。
"""

import base64
import io
import mimetypes
import os
import re
import zipfile
from dataclasses import dataclass, field

# 文本/图片硬上限（防打爆内存与 token）
MAX_FILE_TEXT_CHARS = 12000
MAX_TOTAL_TEXT_CHARS = 40000
MAX_FILE_IMAGES = 4
MAX_TOTAL_IMAGES = 8
# PDF 最多翻这么多页；再往后大概率是附录，不值得烧 token
MAX_PDF_PAGES = 30
# 单张图压缩后仍超过 2MB 就跳过（base64 会再涨 1/3）
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
# 单张媒体文件太小的基本是 1x1 占位图；阈值只挡真正的图标，
# 纯色小图仍可能只有两三百字节，不该被误杀
_MIN_MEDIA_BYTES = 128

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".jsonl", ".rtf"}
_NO_PARSER_EXTS = {".doc", ".xls", ".ppt"}
_NO_PARSER_NOTE = "（旧版二进制格式，服务端无法抽取文字；请另存为 docx / xlsx / pptx 后重新上传）"

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class FileContent:
    """单个附件抽出来的内容。"""

    filename: str
    text: str = ""
    images: list[str] = field(default_factory=list)  # data URL
    truncated: bool = False


@dataclass
class ExtractResult:
    """整批附件的抽取结果。"""

    files: list[FileContent] = field(default_factory=list)
    total_text: int = 0
    total_images: int = 0

    @property
    def any_content(self) -> bool:
        return bool(self.files)


def _safe_name(name: str) -> str:
    """文件名进 prompt 前先去掉换行，避免伪造【附件结束】这类标记。"""
    return re.sub(r"[\r\n]+", " ", str(name or "附件")).strip()[:120] or "附件"


def _trunc_mark(reason: str) -> str:
    return f"\n…（内容已截断：{reason}）"


def _read_text(path: str) -> str:
    """utf-8 → gbk → utf-8/replace，与知识库口径一致。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _rtf_text(path: str) -> str:
    """极简 RTF 去控制字。不追求还原格式，只要模型能读到正文。"""
    raw = _read_text(path)
    raw = re.sub(r"\\fonttbl[\s\S]*?\}", "", raw)
    raw = re.sub(r"\\colortbl[\s\S]*?;", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", raw)
    raw = re.sub(r"[{}]", "", raw)
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw)
    return re.sub(r"[ \t]+", " ", raw).strip()


def load_image_bytes(path: str) -> tuple[bytes, str]:
    """读取图片；Pillow 可用时压到长边 1024、JPEG q85，不可用则原样读。

    与 `api.chat` 原有逻辑一致，移到这里供图片附件与文档内嵌图片共用。
    """
    try:
        from PIL import Image

        with Image.open(path) as im:
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        pass
    mime, _ = mimetypes.guess_type(path)
    with open(path, "rb") as f:
        return f.read(), mime or "image/png"


def bytes_to_data_url(data: bytes, mime: str) -> str | None:
    """内嵌图片转 data URL：能压就压，压不了用原字节；过大则返回 None。"""
    if not data:
        return None
    out = data
    out_mime = mime or "image/png"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            out, out_mime = buf.getvalue(), "image/jpeg"
    except Exception:
        pass
    if len(out) > _MAX_IMAGE_BYTES:
        return None
    return f"data:{out_mime};base64,{base64.b64encode(out).decode()}"


def image_to_data_url(path: str) -> str:
    data, mime = load_image_bytes(path)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_FILE_TEXT_CHARS:
        return text, False
    return text[:MAX_FILE_TEXT_CHARS] + _trunc_mark(f"单文件上限 {MAX_FILE_TEXT_CHARS} 字"), True


def _pdf_content(path: str, image_budget: int) -> tuple[str, list[str], bool]:
    """PDF 文字 + 少量含图页面。PyMuPDF 不可用时只抽文字。"""
    text = ""
    truncated = False
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        parts: list[str] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                parts.append(t.strip())
            if sum(len(x) for x in parts) > MAX_FILE_TEXT_CHARS:
                truncated = True
                break
        text = "\n".join(parts)
    except Exception as e:
        text = f"（PDF 文字抽取失败：{type(e).__name__}）"

    images: list[str] = []
    if image_budget > 0:
        try:
            import fitz  # PyMuPDF，可选依赖

            doc = fitz.open(path)
            try:
                for i, page in enumerate(doc):
                    if len(images) >= image_budget or i >= MAX_PDF_PAGES:
                        break
                    try:
                        if not page.get_images(full=True):
                            continue
                        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
                        url = bytes_to_data_url(pix.tobytes("png"), "image/png")
                        if url:
                            images.append(url)
                    except Exception:
                        continue  # 单页渲染失败不影响其它页
            finally:
                doc.close()
        except Exception:
            pass  # fitz 没装或打开失败：退化为纯文字，不报错
    return text, images, truncated


def _docx_images(path: str, image_budget: int) -> list[str]:
    """从 docx 的 word/media 取内嵌图片（按扩展名粗筛，跳过图标）。"""
    out: list[str] = []
    if image_budget <= 0:
        return out
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(
                n for n in z.namelist()
                if n.startswith("word/media/")
                and os.path.splitext(n)[1].lower() in _IMAGE_EXTS
            )
            for name in names:
                if len(out) >= image_budget:
                    break
                try:
                    data = z.read(name)
                    if len(data) < _MIN_MEDIA_BYTES:
                        continue
                    mime = mimetypes.guess_type(name)[0] or "image/png"
                    url = bytes_to_data_url(data, mime)
                    if url:
                        out.append(url)
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _extract_one(path: str, ext: str, image_budget: int) -> tuple[str, list[str], bool]:
    """抽单个文件。返回 (text, data_urls, truncated)。"""
    if ext in _IMAGE_EXTS:
        if image_budget <= 0:
            return "", [], False
        return "", [image_to_data_url(path)], False

    if ext == ".pdf":
        return _pdf_content(path, image_budget)

    if ext in _TEXT_EXTS:
        text = _rtf_text(path) if ext == ".rtf" else _read_text(path)
        clipped, truncated = _clip(text)
        return clipped, [], truncated

    if ext in _NO_PARSER_EXTS:
        return _NO_PARSER_NOTE, [], False

    # docx / xlsx / pptx 的文字抽取与知识库共用一套 stdlib zip+xml 实现
    try:
        from kb import rag_core

        extractor = {
            ".docx": getattr(rag_core, "_docx_text", None),
            ".xlsx": getattr(rag_core, "_xlsx_text", None),
            ".pptx": getattr(rag_core, "_pptx_text", None),
        }.get(ext)
        text = extractor(path) if extractor else ""
    except Exception as e:
        text = f"（{ext} 文字抽取失败：{type(e).__name__}）"
    images = _docx_images(path, image_budget) if ext == ".docx" else []
    clipped, truncated = _clip(text)
    return clipped, images, truncated


def extract_attachments(
    attachments: list[dict],
    resolver=None,
) -> ExtractResult:
    """抽取整批附件。

    resolver(path) 由调用方提供（聊天侧是 `_resolve_upload_path`），返回安全路径或 None；
    不提供时只接受绝对路径，且仍要求文件真实存在。
    """
    result = ExtractResult()
    text_used = 0
    images_used = 0

    for a in attachments or []:
        name = _safe_name(a.get("filename") or a.get("name") or "附件")
        raw_path = a.get("path")
        if not isinstance(raw_path, str):
            continue
        try:
            path = resolver(raw_path) if resolver else (raw_path if os.path.isabs(raw_path) else None)
        except Exception:
            path = None
        if not path or not os.path.isfile(path):
            continue

        ext = os.path.splitext(path)[1].lower()
        text, images, truncated = _extract_one(path, ext, min(MAX_FILE_IMAGES, MAX_TOTAL_IMAGES - images_used))

        room = MAX_TOTAL_TEXT_CHARS - text_used
        if room <= 0:
            # 已经到顶：后续附件不再追加截断标记，否则 12 个附件会多出
            # 12 段标记本身把总字数又顶过上限。
            text = ""
            truncated = True
        elif len(text) > room:
            text = text[:room] + _trunc_mark(f"整次上限 {MAX_TOTAL_TEXT_CHARS} 字")
            truncated = True
        text_used += len(text)

        room_img = MAX_TOTAL_IMAGES - images_used
        if len(images) > room_img:
            images = images[:max(0, room_img)]
            truncated = True
        images_used += len(images)

        result.files.append(FileContent(filename=name, text=text, images=images, truncated=truncated))

    result.total_text = text_used
    result.total_images = images_used
    return result


def format_text_for_prompt(result: ExtractResult) -> str:
    """把抽取文本拼成带文件名的 prompt 片段。"""
    blocks: list[str] = []
    for fc in result.files:
        lines = [f"【附件：{fc.filename}】"]
        if fc.text.strip():
            lines.append(fc.text.strip())
        elif fc.images:
            lines.append("（此附件含图片，见下方图片内容）")
        else:
            lines.append("（未能从该附件抽取到文字）")
        if fc.truncated:
            lines.append("（内容已按长度/数量上限截断）")
        lines.append(f"【附件结束：{fc.filename}】")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def extract_images(result: ExtractResult) -> list[str]:
    """整批附件抽出的图片 data URL（已受总数上限约束）。"""
    return [url for fc in result.files for url in fc.images]
