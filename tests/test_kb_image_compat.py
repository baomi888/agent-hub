"""知识库图片上传兼容性守卫（2026-11-23）。

用户反馈：「docx 上传资料库 / 图片上传资料库 的兼容性还是没解决」。

实测线上后端白名单 = (txt md pdf docx csv jsonl json xlsx pptx)，
**不含任何图片扩展名** → 传 png/jpg 直接 400「不支持的文件格式」。
前端 KnowledgePanel 的 accept 也没放图，所以连选择框里都看不到图片。

本文件钉四件事：
1. 图片扩展名必须在 SUPPORTED_EXT 里
2. 图片必须有 IMAGE_EXT 分组（load_document 靠它走图片卡片分支）
3. load_document 对真实图片产出非空文档，且不抛异常
4. /api/config/defaults 返回的扩展名与 SUPPORTED_EXT 一致（含图片）
"""

import io
import os
import struct
import tempfile
import unittest
import zlib


def _write_png(path: str, w: int = 8, h: int = 8) -> None:
    """写一个最小合法 PNG（不依赖 Pillow 生成测试素材）。"""
    raw = b"".join(b"\x00" + bytes([200, 150, 60] * w) for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(data)


def _tmpdir():
    """★ 本机沙箱下 tempfile.TemporaryDirectory 会 PermissionError（WinError 5），
    改用仓库内的 `_t/` 临时目录，避免测试因环境权限而假红。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(root, "_t")
    os.makedirs(base, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class TestKbImageCompat(unittest.TestCase):
    def test_image_extensions_in_supported(self):
        """负向：一张 png 必须在白名单里，否则上传直接 400。"""
        from kb import rag_core as rc

        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            self.assertIn(ext, rc.SUPPORTED_EXT, f"{ext} 不在 SUPPORTED_EXT，图片传不进知识库")

    def test_image_ext_group_exists(self):
        """IMAGE_EXT 必须存在且是 SUPPORTED_EXT 的子集（不能有孤儿分组）。"""
        from kb import rag_core as rc

        self.assertTrue(hasattr(rc, "IMAGE_EXT"), "缺少 IMAGE_EXT 分组")
        self.assertTrue(set(rc.IMAGE_EXT).issubset(set(rc.SUPPORTED_EXT)))

    def test_docx_still_supported(self):
        """docx 不能被这次改动碰掉（用户同时也报了 docx）。"""
        from kb import rag_core as rc

        self.assertIn(".docx", rc.SUPPORTED_EXT)

    def test_load_png_produces_document(self):
        """真实 png 要产出非空文档，不能是空字符串（空文档 = 搜不到 = 等于没传）。"""
        from kb.rag_core import load_document

        with _tmpdir() as td:
            p = os.path.join(td, "课件截图.png")
            _write_png(p)
            docs = load_document(p, "课件截图.png")
            self.assertEqual(len(docs), 1, "图片没产出一份文档")
            text = docs[0].page_content
            self.assertTrue(text.strip(), "图片卡片是空的")
            self.assertIn("课件截图.png", text, "卡片里没有文件名，搜不到")
            self.assertIn("图片", text)

    def test_load_png_broken_file_does_not_raise(self):
        """负向守卫：坏图（不是真图片）也不能抛异常，要退化出一张卡片。"""
        from kb.rag_core import load_document

        with _tmpdir() as td:
            p = os.path.join(td, "broken.png")
            with open(p, "wb") as f:
                f.write(b"not a real png at all")
            docs = load_document(p, "broken.png")
            self.assertEqual(len(docs), 1)
            self.assertIn("broken.png", docs[0].page_content)

    def test_defaults_reports_images(self):
        """/api/config/defaults 是前端读能力的地方，图片必须出现在里面。"""
        import asyncio

        # ★ 与 test_upload_attachments_formats.py 的取法一致：defaults 定义在 main.get_defaults；
        #   那边 `from api import m` 是模块别名残留，直接用 main 更稳。
        import main as app_main

        exts = asyncio.run(app_main.get_defaults())["supported_extensions"]
        from kb import rag_core as rc

        self.assertIn(".png", exts)
        self.assertIn(".jpg", exts)
        self.assertEqual(set(exts), set(rc.SUPPORTED_EXT))

    def test_frontend_upload_accept_matches_backend(self):
        """★ 本次真凶：lib/uploadLimits.ts 的 UPLOAD_ACCEPT 独立写死成 txt/md/pdf，
        后端收了 docx/图片它照样先拒 —— 校验在请求发出之前，后端再对也没用。

        这条断言把前端白名单和后端 SUPPORTED_EXT 逐项比对：
        任何一边加了新格式而另一边没跟，立刻变红。
        """
        from kb import rag_core as rc

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src_path = os.path.join(root, "frontend", "src", "lib", "uploadLimits.ts")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        # 取 UPLOAD_ACCEPT 数组里的字符串字面量（跳过注释行）
        import re

        m = re.search(r"UPLOAD_ACCEPT\s*=\s*\[(.*?)\]\s*as\s+const", src, re.S)
        self.assertIsNotNone(m, "找不到 UPLOAD_ACCEPT 数组")
        body = m.group(1)
        # 去掉 // 注释，避免注释里的 ".xxx" 混进白名单
        body = re.sub(r"//[^\n]*", "", body)
        front_ext = set(re.findall(r'"(\.[a-z0-9]+)"', body))
        self.assertTrue(front_ext, "UPLOAD_ACCEPT 解析为空")
        missing = set(rc.SUPPORTED_EXT) - front_ext
        extra = front_ext - set(rc.SUPPORTED_EXT)
        self.assertFalse(
            missing,
            f"前端 UPLOAD_ACCEPT 缺这些后端支持的格式（会被前端先拒掉）：{sorted(missing)}",
        )
        self.assertFalse(
            extra,
            f"前端 UPLOAD_ACCEPT 有后端不认的格式（会传到后端才报错）：{sorted(extra)}",
        )

    def test_frontend_accept_allows_images(self):
        """前端 KnowledgePanel 的 accept 必须含图片，否则选择框里根本看不到图。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        panel = os.path.join(root, "frontend", "src", "components", "KnowledgePanel.tsx")
        with open(panel, encoding="utf-8") as f:
            src = f.read()
        # 只认真正挂在 input 上的那一行 accept
        accept_lines = [ln for ln in src.splitlines() if "accept=" in ln and ".docx" in ln]
        self.assertTrue(accept_lines, "找不到 KnowledgePanel 的 accept 声明")
        self.assertIn(".png", accept_lines[0], "accept 没放图片扩展名")
        self.assertIn("image/", accept_lines[0], "accept 没放 image/*")


if __name__ == "__main__":
    unittest.main()