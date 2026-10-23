# -*- coding: utf-8 -*-
"""行为基线（characterization tests）：流式问答的 SSE 事件序列。

走真 HTTP 层（TestClient），但把外部依赖全部换掉：鉴权用 dependency_overrides
放行、会话库落临时文件、检索换成假 pipeline、LLM 换成产固定 token 的假实现。

钉住的是前端真正在消费的东西：**事件名与出现顺序**、以及 done 的字段集合。
`_stream_llm` 里那个"每帧查一次断连"的调优最容易被这类断言挡住——一旦帧序列
或少一帧，前端就会缺一段渲染。
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="baomi_stream_baseline_")
config.PROJECT_ROOT = os.path.join(_TMP, "proj")
config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
config.DATA_DIR = os.path.join(_TMP, "proj", "data")
os.makedirs(config.DATA_DIR, exist_ok=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

import core.session as session  # noqa: E402
from core.auth import get_current_user  # noqa: E402

import api.chat as chat  # noqa: E402
import kb.rag_core as rc  # noqa: E402
import main as m  # noqa: E402

OWNER = "u_stream"
SID = "conv_stream"

DOCS = [
    (Document(page_content="强相关片段", metadata={"source": "资料.txt"}), 0.4),
    (Document(page_content="沾边片段", metadata={"source": "别的.txt"}, ), 1.4),
]


class _FakePipe:
    def retrieve_with_score(self, query, top_k, owner=None):
        return DOCS

    _format_refs = staticmethod(rc.RagPipeline._format_refs)
    build_sources = staticmethod(rc.RagPipeline.build_sources)


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """产 3 个固定 delta 的假流式模型。"""

    async def astream(self, messages):
        for t in ("你", "好", "呀"):
            yield _Chunk(t)


def _frames(body: str):
    """把 SSE 响应体切成 (事件名, data 文本) 列表。"""
    out = []
    for raw in body.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        name = ""
        data = ""
        for line in raw.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        out.append((name, data))
    return out


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "_db_path", lambda: os.path.join(str(tmp_path), "sessions.db"))
    session._init_db()

    monkeypatch.setattr(chat.store, "get_conversation", lambda uid, sid: {"id": sid, "kb_id": "kb_x"})
    monkeypatch.setattr(chat.store, "count_messages", lambda uid, sid: 1)
    monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: [])
    monkeypatch.setattr(chat.store, "add_message", lambda *a, **k: 123)
    monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)

    monkeypatch.setattr(chat.pipelines, "exists", lambda owner, kid: True)
    monkeypatch.setattr(chat.pipelines, "owner_of", lambda kid: OWNER)
    monkeypatch.setattr(chat.pipelines, "get_or_create", lambda owner, kid: _FakePipe())

    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _FakeLLM())
    monkeypatch.setattr(chat, "_generate_title", lambda q: "固定标题")
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", False)
    # 配额只是"能不能问"的前置闸门，与 SSE 帧序列无关；而且它会去查 users 表
    # （临时会话库里没有），放行即可
    monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)

    m.app.dependency_overrides[get_current_user] = lambda: OWNER
    with TestClient(m.app) as c:
        yield c
    m.app.dependency_overrides.clear()


def _body(client, **payload):
    body = {"sid": SID, "question": "苞米的种植密度是多少", "top_k": 3}
    body.update(payload)
    r = client.post("/api/chat/stream", json=body)
    assert r.status_code == 200, (r.status_code, r.text)
    return r.text


# ==================== RAG 路径 ====================

def test_rag_stream_event_sequence(client):
    frames = _frames(_body(client, mode="rag"))
    names = [n for n, _ in frames]

    assert names == [
        "status",    # RAG 模式，正在检索知识库
        "sources",   # 引用溯源条目
        "grounding",  # 依据强度（前端据此挂"依据较弱"提示条）
        "status",    # 检索到 N 条片段
        "status",    # 正在生成答案...
        "token", "token", "token",
        "title",
        "done",
    ]

    import json

    done = json.loads([d for n, d in frames if n == "done"][0])
    # plan_topic 为 2026-10-16 合入「计划主题识别」特性后新增的 done 字段
    assert set(done) == {"answer", "refs", "title", "mode", "message_id", "run_id", "plan_topic"}
    assert done["answer"] == "你好呀"
    assert done["mode"] == "rag"
    assert done["message_id"] == 123
    assert done["run_id"] is None  # 仅 hybrid 模式有值
    assert done["title"] == "固定标题"
    assert done["plan_topic"] is None  # 非计划类问题（问的是种植密度）

    srcs = json.loads([d for n, d in frames if n == "sources"][0])["items"]
    assert [s["source"] for s in srcs] == ["资料.txt", "别的.txt"]
    assert [s["strength"] for s in srcs] == ["high", "medium"]

    gr = json.loads([d for n, d in frames if n == "grounding"][0])
    assert gr["strength"] == "high"
    assert gr["weak"] is False
    assert gr["best"] == pytest.approx(0.8)


def test_rag_stream_rewrite_frame_only_when_query_changes(client, monkeypatch):
    """追问被改写时才多发一帧 status；没有历史时不改写、也不发这帧。

    B4（2026-10-23）起这条 status 的文案把「做了什么」和「最终检索词」
    合成了一句（以前只说改写）。状态条是覆盖式的，拆成两条用户只看到最后一条。
    """
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: "改写后的问题")
    frames = _frames(_body(client, mode="rag"))
    texts = [__import__("json").loads(d)["text"] for n, d in frames if n == "status"]
    assert texts[1] == "已结合上下文改写，检索词：改写后的问题"


def test_rag_stream_enhance_frame_reports_swapped_terms(client, monkeypatch, tmp_path):
    """B4 端到端：口语词在真实 SSE 链路上被换掉，并在状态条里报出来。

    走真 HTTP + 真帧解析，只把词表换成临时文件——否则要依赖仓库里那份
    每天在改的 data/vocab，断言第二天就红。
    """
    import json

    import core.config as cfg
    from service import enhance as enhance_svc

    vocab = tmp_path / "vocab.csv"
    vocab.write_text("canonical,aliases\n高等数学,高数\n", encoding="utf-8-sig")
    monkeypatch.setattr(cfg, "ENHANCE_VOCAB_PATH", str(vocab))
    monkeypatch.setattr(cfg, "ENHANCE_CHAT_ENABLED", True)
    enhance_svc.reset_cache()

    frames = _frames(_body(client, mode="rag", question="高数怎么复习"))
    texts = [json.loads(d)["text"] for n, d in frames if n == "status"]
    assert any("高数→高等数学" in t for t in texts), texts


def test_llm_stream_event_sequence(client):
    frames = _frames(_body(client, mode="llm", kb_id_override=None))
    assert [n for n, _ in frames] == [
        "status", "token", "token", "token", "title", "done",
    ]
    import json

    done = json.loads(frames[-1][1])
    assert done["answer"] == "你好呀"
    assert done["mode"] == "llm"
    assert done["refs"] == ""


def test_stream_missing_conversation_is_404(client, monkeypatch):
    monkeypatch.setattr(chat.store, "get_conversation", lambda uid, sid: None)
    r = client.post("/api/chat/stream", json={"sid": "不存在", "question": "问题"})
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在：不存在"
