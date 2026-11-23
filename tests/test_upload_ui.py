"""上传区 UI 的两条不变量（截图反馈：文件多了看不到提交按钮）。

行为断言很难在无浏览器单测里做，这里钉住实现要点，避免样式/接线被改回
「附件无限堆高、按钮被顶出视口」或「管理员也被 9 个上限卡住」。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def test_chat_attachment_cap_and_notice():
    chat = _read("components/ChatArea.tsx")
    assert "maxAttachments?: number" in chat
    assert "if (maxAttachments !== undefined && next.length >= maxAttachments)" in chat
    assert "多出来的 ${overflow} 个没有加上" in chat, "超限时必须当场提示，不能等后端 400"
    page = _read("app/(console)/page.tsx")
    assert "auth.me?.is_admin ? undefined : MAX_ATTACHMENTS" in page, "管理员不能受 9 个上限"


def test_chat_accepts_doc_formats():
    chat = _read("components/ChatArea.tsx")
    assert ".pdf,.doc,.docx,.md,.txt" in chat, "聊天框 accept 没放开文档格式"
    panel = _read("components/KnowledgePanel.tsx")
    assert ".doc,.docx,.csv,.json,.jsonl,.xlsx,.pptx" in panel, "知识库面板 accept 没放开新格式"


def test_attachment_lists_scroll_instead_of_pushing_send_button():
    kb = _read("app/styles/kb.css")
    assert "max-height: 96px" in kb and "overflow-y: auto" in kb, (
        "聊天附件条必须有内部滚动，否则附件多了会把发送键顶出视口"
    )
    panel = _read("components/KnowledgePanel.tsx")
    assert "max-h-40" in panel and "overflow-y-auto" in panel, (
        "知识库已选文件列表必须内部滚动，否则「上传到…」按钮会看不见"
    )
