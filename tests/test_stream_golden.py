"""两条路径的帧序列金标准：rag / llm。

与 ``test_baseline_chat_stream.py`` 的分工
------------------------------------------
baseline 断言的是**手写的帧名列表**，防的是「少一帧、顺序错」；
快照断言的是**完整 payload**，还能防「字段少了 / 多了一个键 / 嵌套值变了」。
两者互补，但快照需要能一键重录，混在一起容易被误当成「改断言」。

为什么只有两条路径
------------------
录制的前提是那条路能跑通。agent / vision / hybrid 需要假 ``build_agent`` /
假 vision LLM / 假 ``HybridOrchestrator``（见 issue #24），在那之前硬录只会
录到一条 error 帧序列，毫无价值。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # golden_stream

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from golden_stream import assert_golden, frames_of  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

import api.chat as chat  # noqa: E402
import core.config as config  # noqa: E402
import core.session as session  # noqa: E402
import kb.rag_core as rc  # noqa: E402
import main as m  # noqa: E402
from core.auth import get_current_user  # noqa: E402

OWNER = "u_golden"
SID = "conv_golden"

# 固定两条：一条高分、一条中等。score 由 assess_grounding 判成 high / medium，
# strength 落到 sources 里——这两个值必须稳定，否则快照每次都变。
DOCS = [
    (Document(page_content="强相关片段", metadata={"source": "资料.txt"}), 0.4),
    (Document(page_content="沾边片段", metadata={"source": "别的.txt"}), 1.4),
]


class _FakePipe:
    """假检索管道。只提供 kb/retrieval.py 真正会调的四个名字。"""

    def retrieve_with_score(self, query, top_k, owner=None):
        return DOCS

    # 2026-11-26：原先还有一行 `format_refs = staticmethod(rc.RagPipeline.format_refs)`，
    # 但生产侧只有 `_format_refs`（全仓无 `format_refs` 调用），该行让整个文件在收集阶段
    # 就 AttributeError，测试从未真正跑过。删掉重复别名。
    _format_refs = staticmethod(rc.RagPipeline._format_refs)
    build_sources = staticmethod(rc.RagPipeline.build_sources)


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """产 3 个固定 delta。快照里 token 帧的数量和内容都来自这里。"""

    async def astream(self, messages):
        for t in ("你", "好", "呀"):
            yield _Chunk(t)


def _fake_add(owner, sid, role, content, *a, **k):
    """用户 / 助手各一个固定 id。恒返回同一个值的话，
    done 里把两个 id 弄反也测不出来。"""
    return 1000 if role == "user" else 1001


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        session, "_db_path", lambda: os.path.join(str(tmp_path), "sessions.db")
    )
    session._init_db()

    monkeypatch.setattr(
        chat.store, "get_conversation", lambda uid, sid: {"id": sid, "kb_id": "kb_g"}
    )
    monkeypatch.setattr(chat.store, "count_messages", lambda uid, sid: 1)
    monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: [])
    monkeypatch.setattr(chat.store, "add_message", _fake_add)
    monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)

    monkeypatch.setattr(chat.pipelines, "exists", lambda owner, kid: True)
    monkeypatch.setattr(chat.pipelines, "owner_of", lambda kid: OWNER)
    monkeypatch.setattr(chat.pipelines, "get_or_create", lambda owner, kid: _FakePipe())

    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _FakeLLM())
    monkeypatch.setattr(chat, "_generate_title", lambda q: "固定标题")
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", False)
    monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)

    m.app.dependency_overrides[get_current_user] = lambda: OWNER
    with TestClient(m.app) as c:
        yield c
    m.app.dependency_overrides.clear()


def _snapshot(client, name: str, **payload) -> None:
    body = {"sid": SID, "question": "苞米的种植密度是多少", "top_k": 3}
    body.update(payload)
    r = client.post("/api/chat/stream", json=body)
    assert r.status_code == 200, (r.status_code, r.text)
    assert_golden(name, frames_of(r.text))


# ==================== 金标准 ====================


def test_rag_frame_sequence_matches_golden(client):
    """RAG：status → sources → grounding → status → status → token×3 → title → done。"""
    _snapshot(client, "rag", mode="rag")


def test_llm_frame_sequence_matches_golden(client):
    """纯对话：status → token×3 → title → done（没有 sources / grounding）。"""
    _snapshot(client, "llm", mode="llm")
