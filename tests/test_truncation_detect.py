"""A1 回归：输出被长度上限截断时必须被看见（2026-10-20 审计修复）。

修复前的事实：全仓 `grep -rn "finish_reason" --include=*.py` **零命中**。
上游在最后一帧给的 `finish_reason="length"` 被完整收到、完整丢掉。
后果不是报错，而是把半句话当成完整答案返回并落库——
不抛异常、不打日志、前端照常渲染。用户问「列出全部 12 条」，答到第 7 条就停，
界面显示正常结束。

本测试锁死五件事：
  1) 有截断信号 → 有 truncation 帧 + acc.truncated=True；
  2) 最后一帧 content 为空也要能读到信号（这是最容易写错的地方：
     旧实现遇到空 delta 就 continue，把信号一起跳过了）；
  3) 没有 metadata 的供应商 → 一律当"未截断"，**不许**误报；
  4) 开关置 0 → 完全退回旧行为；
  5) 落库能记住 truncated 标记。
"""

from __future__ import annotations

import asyncio

import pytest

from api.chat import _Acc, _is_truncation, _stream_llm, _TRUNCATED_REASONS
from core import config


class _Chunk:
    """最小的 AIMessageChunk 替身：只提供 _stream_llm 会读的几个属性。"""

    def __init__(self, text: str = "", reason: str | None = None, where: str = "response_metadata"):
        self.content = text
        self.response_metadata: dict = {}
        self.generation_info: dict = {}
        self.additional_kwargs: dict = {}
        if reason is not None:
            getattr(self, where)["finish_reason"] = reason


class _FakeLLM:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _messages):
        for c in self._chunks:
            yield c


class _Req:
    """is_disconnected 恒为 False：模拟客户端一直在。"""

    async def is_disconnected(self) -> bool:
        return False


def _drain(chunks):
    acc = _Acc()

    async def go():
        out = []
        async for frame in _stream_llm(_FakeLLM(chunks), [], _Req(), acc):
            out.append(frame)
        return out

    return acc, asyncio.run(go())


def test_truncation_on_length_is_reported():
    acc, frames = _drain([_Chunk("好的，以下是"), _Chunk("前三条"), _Chunk("", reason="length")])
    assert acc.truncated is True
    assert acc.finish_reason == "length"
    body = "".join(frames)
    assert "event: truncation" in body
    # 提示必须可操作，不能只说"被截断"
    assert "继续" in body


def test_truncation_detected_on_empty_final_chunk():
    """最后一帧 content 为空是常态：信号必须照样读得到。

    这是旧实现最容易漏的一处——它遇到空 delta 就 continue，
    如果只在"有 delta 的分支"里读 finish_reason，就永远读不到。
    """
    acc, frames = _drain([_Chunk("正文"), _Chunk("", reason="max_tokens")])
    assert acc.truncated is True
    assert "event: truncation" in "".join(frames)


def test_normal_finish_does_not_trigger():
    acc, frames = _drain([_Chunk("完整"), _Chunk("回答", reason="stop")])
    assert acc.truncated is False
    assert "event: truncation" not in "".join(frames)


def test_missing_metadata_never_false_alarms():
    """取不到信号时必须按"未截断"处理。

    反过来（取不到就报警）会让所有正常回答被标残缺——比不检测更糟。
    某些兼容层会直接省略 response_metadata，这是真实存在的情况。
    """
    acc, frames = _drain([_Chunk("没有元信息的回答")])
    assert acc.truncated is False
    assert "event: truncation" not in "".join(frames)


def test_switch_off_restores_old_behaviour(monkeypatch):
    monkeypatch.setattr(config, "TRUNCATION_DETECT_ENABLED", False)
    acc, frames = _drain([_Chunk("正文"), _Chunk("", reason="length")])
    assert acc.truncated is False
    assert "event: truncation" not in "".join(frames)


@pytest.mark.parametrize("reason", ["length", "max_tokens", "max_output_tokens", "LENGTH"])
def test_reason_vocabulary_is_provider_agnostic(reason):
    """换供应商不该回来改判断：几个常见叫法都要认。"""
    assert _is_truncation(reason, "test") is True


@pytest.mark.parametrize("reason", [None, "", "stop", "tool_calls", "content_filter"])
def test_non_truncation_reasons(reason):
    assert _is_truncation(reason, "test") is False


def test_reason_from_generation_info_also_works():
    """不同 LangChain 版本把结束原因放在不同字段，三个位置都要找。"""
    acc, _ = _drain([_Chunk("正文"), _Chunk("", reason="length", where="generation_info")])
    assert acc.truncated is True


def test_vocabulary_covers_known_providers():
    assert {"length", "max_tokens"} <= _TRUNCATED_REASONS


def test_persisted_truncated_flag(tmp_path, monkeypatch):
    """落库要能记住"这条是残缺的"——否则事后无法统计有多少回答被截断。"""
    from core import session as store

    path = str(tmp_path / "t.db")
    monkeypatch.setattr(store, "_db_path", lambda: path)
    store._init_db()

    conv = store.create_conversation("u1")
    store.add_message("u1", conv["id"], "user", "问")
    store.add_message("u1", conv["id"], "assistant", "半句话", truncated=True)
    store.add_message("u1", conv["id"], "assistant", "完整回答")

    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT content, truncated FROM messages WHERE role='assistant' ORDER BY id"
        ).fetchall()
    assert [(r["content"], r["truncated"]) for r in rows] == [("半句话", 1), ("完整回答", 0)]
