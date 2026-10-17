"""方案 C · A/B 块离线自测（不联网、不碰真实数据）。

A 块（入库级 topics metadata）：
  - _extract_topics_one：LLM 抽主题；失败 / 关闭开关都静默返回 []
  - _inject_topics：按来源分组，把主题写成 "|t1|t2|" 注入每片 metadata
  - topic_where：D 块预留的 Chroma where 条件生成（纯函数）

B 块（对话级 user_tags）：
  - session.add_user_tags / get_user_tags：累加权重、去重、owner 隔离
  - chat._extract_user_tags：受控词表过滤，越界标签丢弃，失败静默
  - chat._record_user_tags：fire-and-forget 落库路径（不抛异常、写入正确）
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

import core.config as config
import core.session as session
import kb.rag_core as rc

# ---------- fixtures ----------


def _patch_session_db(monkeypatch, tmp_path):
    """把会话库挪到临时文件并重建表，避免污染真实库。"""
    db = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(session, "_db_path", lambda: db)
    session._init_db()


# ---------- A 块：_extract_topics_one ----------


def test_extract_topics_one_parses_json(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", True)

    class FakeResp:
        content = '["线性代数", "考研数学"]'

    class FakeLLM:
        def invoke(self, *_a, **_k):
            return FakeResp()

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: FakeLLM())
    assert rc._extract_topics_one("文档内容：关于线性代数与考研数学") == ["线性代数", "考研数学"]


def test_extract_topics_one_accepts_fenced_json(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", True)

    class FakeResp:
        content = '```json\n["操作系统", "计算机网络"]\n```'

    class FakeLLM:
        def invoke(self, *_a, **_k):
            return FakeResp()

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: FakeLLM())
    assert rc._extract_topics_one("x") == ["操作系统", "计算机网络"]


def test_extract_topics_one_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", True)

    class Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("network down")

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: Boom())
    assert rc._extract_topics_one("内容") == []


def test_extract_topics_one_disabled_skips_llm(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", False)
    called = []
    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: called.append(1))
    assert rc._extract_topics_one("内容") == []
    assert called == []


def test_extract_topics_one_empty_text_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", True)
    assert rc._extract_topics_one("   ") == []


# ---------- A 块：_inject_topics ----------


def test_inject_topics_writes_pipe_delimited_per_source(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", True)
    monkeypatch.setattr(rc, "_extract_topics_one", lambda text: ["考研数学", "线性代数"])

    chunks = [
        Document(page_content="a1", metadata={"source": "a.pdf"}),
        Document(page_content="a2", metadata={"source": "a.pdf"}),
        Document(page_content="b1", metadata={"source": "b.txt"}),
        Document(page_content="c1", metadata={"source": ""}),  # 无来源 -> 跳过
    ]
    rc._inject_topics(chunks)
    assert chunks[0].metadata["topics"] == "|考研数学|线性代数|"
    assert chunks[1].metadata["topics"] == "|考研数学|线性代数|"
    assert chunks[2].metadata["topics"] == "|考研数学|线性代数|"
    assert "topics" not in chunks[3].metadata


def test_inject_topics_skips_when_extraction_fails(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", True)
    monkeypatch.setattr(rc, "_extract_topics_one", lambda text: [])

    chunks = [Document(page_content="a1", metadata={"source": "a.pdf"})]
    rc._inject_topics(chunks)
    assert "topics" not in chunks[0].metadata


def test_inject_topics_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(config, "TOPIC_EXTRACT_ENABLED", False)
    monkeypatch.setattr(rc, "_extract_topics_one", lambda text: ["x"])
    chunks = [Document(page_content="a1", metadata={"source": "a.pdf"})]
    rc._inject_topics(chunks)
    assert "topics" not in chunks[0].metadata


# ---------- A 块：topic_where（D 块预留，纯函数）----------


def test_topic_where_builds_contains_filter():
    assert rc.topic_where("数学") == {"topics": {"$contains": "|数学|"}}


def test_topic_where_strips_pipes():
    assert rc.topic_where("|数学|") == {"topics": {"$contains": "|数学|"}}


def test_topic_where_empty_returns_empty():
    assert rc.topic_where("") == {}
    assert rc.topic_where("   ") == {}


# ---------- B 块：session 读写 ----------


def test_add_user_tags_basic_and_read(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["考研数学", "编程开发"])
    tags = session.get_user_tags("u1")
    assert {t["tag"] for t in tags} == {"考研数学", "编程开发"}
    assert all(t["weight"] == 1.0 for t in tags)


def test_add_user_tags_accumulates_weight(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["考研数学"])
    session.add_user_tags("u1", ["考研数学", "深度学习"])
    tags = {t["tag"]: t["weight"] for t in session.get_user_tags("u1")}
    assert tags["考研数学"] == 2.0
    assert tags["深度学习"] == 1.0


def test_add_user_tags_owner_isolation(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["考研数学"])
    assert session.get_user_tags("u2") == []


def test_add_user_tags_dedup_within_call(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["考研数学", "考研数学", "编程开发"])
    tags = {t["tag"]: t["weight"] for t in session.get_user_tags("u1")}
    assert tags == {"考研数学": 1.0, "编程开发": 1.0}


def test_add_user_tags_empty_input_is_safe(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", [])
    session.add_user_tags("u1", None)  # type: ignore
    assert session.get_user_tags("u1") == []


# ---------- B 块：_extract_user_tags（受控词表）----------


def _enable_user_tag_llm(monkeypatch, content):
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", True)
    payload = content

    class FakeResp:
        content = payload

    class FakeLLM:
        def invoke(self, *_a, **_k):
            return FakeResp()

    import api.chat as chat

    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: FakeLLM())
    return chat


def test_extract_user_tags_returns_vocab_only(monkeypatch):
    chat = _enable_user_tag_llm(monkeypatch, '["考研数学", "编程开发", "乱七八糟的越界标签"]')
    out = chat._extract_user_tags("怎么复习考研数学", "建议做真题")
    assert out == ["考研数学", "编程开发"]  # 越界标签被丢弃


def test_extract_user_tags_empty_when_no_match(monkeypatch):
    chat = _enable_user_tag_llm(monkeypatch, "[]")
    assert chat._extract_user_tags("你好", "你好") == []


def test_extract_user_tags_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", True)
    import api.chat as chat

    class Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("x")

    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: Boom())
    assert chat._extract_user_tags("q", "a") == []


def test_extract_user_tags_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", False)
    import api.chat as chat

    monkeypatch.setattr(chat, "get_llm", lambda *a, **k: object())
    assert chat._extract_user_tags("q", "a") == []


# ---------- B 块：_record_user_tags（fire-and-forget 落库路径）----------


def test_record_user_tags_writes_to_db(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    import api.chat as chat

    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", True)
    monkeypatch.setattr(chat, "_extract_user_tags", lambda q, a: ["考研数学", "编程开发"])
    # 跑通异步落库路径（不抛异常、写入正确）
    asyncio.run(chat._record_user_tags("u1", "怎么复习", "做真题"))
    tags = {t["tag"] for t in session.get_user_tags("u1")}
    assert tags == {"考研数学", "编程开发"}


def test_record_user_tags_silent_on_failure(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    import api.chat as chat

    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", True)
    monkeypatch.setattr(
        chat, "_extract_user_tags", lambda q, a: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    # 即使抽取环节炸了，也绝不向外抛异常（fire-and-forget 静默）
    asyncio.run(chat._record_user_tags("u1", "q", "a"))
    assert session.get_user_tags("u1") == []
