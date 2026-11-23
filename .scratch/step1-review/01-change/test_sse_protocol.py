"""api/sse_protocol.py：SSE 帧格式的唯一真相。

为什么值得单独钉死
------------------
改造前帧名与 payload 形状散在 ``api/chat.py`` 的 59 处调用里，改一个字段永远
不知道有没有漏——漏了也不报错，前端只是少渲染一块。现在编码集中到一处，
就用字节级断言把它锁住；再加一条结构断言，防止有人绕过去直接拼字符串。

不测业务语义（那是 api/chat.py 的测试），只测「一帧长什么样」。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-dummy-for-import")

import pytest  # noqa: E402

from api import sse_protocol as sse  # noqa: E402


def _parse(frame: str):
    """还原成 (name, payload)，同时校验分隔符与结尾。"""
    assert frame.endswith("\n\n"), "每帧必须以空行结尾，否则前端 split('\\n\\n') 会粘帧"
    head, sep, data = frame.partition("\n")
    assert sep == "\n"
    assert head.startswith("event: "), head
    assert data.startswith("data: "), data
    return head[len("event: "):], json.loads(data[len("data: "):].rstrip("\n"))


def test_status_frame_exact_bytes():
    assert sse.status("检索中") == 'event: status\ndata: {"text": "检索中"}\n\n'


def test_token_frame_exact_bytes():
    assert sse.token("你") == 'event: token\ndata: {"text": "你"}\n\n'


def test_error_frame_exact_bytes():
    assert sse.error("上游超时") == 'event: error\ndata: {"detail": "上游超时"}\n\n'


def test_title_frame_exact_bytes():
    assert sse.title("新标题") == 'event: title\ndata: {"title": "新标题"}\n\n'


def test_sources_frame_wraps_items():
    name, payload = _parse(sse.sources([{"id": 1, "source": "a.txt"}]))
    assert name == "sources"
    assert payload == {"items": [{"id": 1, "source": "a.txt"}]}


def test_grounding_is_passed_through_untouched():
    """grounding 的字段由 assess_grounding 决定，协议层不做任何翻译。"""
    data = {"strength": "high", "best": 0.8, "weak": False}
    name, payload = _parse(sse.grounding(data))
    assert name == "grounding"
    assert payload == data


def test_truncation_has_actionable_hint():
    name, payload = _parse(sse.truncation("length"))
    assert name == "truncation"
    assert payload["reason"] == "length"
    assert "继续" in payload["hint"], "只说被截断，用户不知道下一步做什么"


def test_truncation_defaults_to_length():
    _, payload = _parse(sse.truncation(None))
    assert payload["reason"] == "length"


def test_done_payload_passthrough():
    data = {"answer": "a", "refs": "", "truncated": False}
    name, payload = _parse(sse.done(data))
    assert name == "done"
    assert payload == data


@pytest.mark.parametrize(
    "frame,expected_name",
    [
        (sse.tool({"name": "kb_search"}), "tool"),
        (sse.approval({"request_id": "x"}), "approval"),
        (sse.workflow_node({"phase": "enter", "node_id": "n1"}), "workflow_node"),
        (sse.mode_switch({"rule": "r", "reason": "why"}), "mode_switch"),
        (sse.hybrid_start({"run_id": "rid", "mode": "agent"}), "hybrid_start"),
    ],
)
def test_structural_frames_child(frame, expected_name):
    name, _ = _parse(frame)
    assert name == expected_name


def test_chinese_is_not_ascii_escaped():
    """ensure_ascii=False：前端拿到的是中文，不是 \\u4e2d 这种转义。"""
    frame = sse.status("正在生成答案")
    assert "正在生成答案" in frame
    assert "\\u" not in frame


def test_single_trailing_blank_line():
    """帧尾只能有一个空行。多一个空行会让前端解析出空事件。"""
    frame = sse.status("x")
    assert frame.endswith("\n\n") and not frame.endswith("\n\n\n")


def test_chat_module_has_no_manual_frame_building():
    """结构断言：api/chat.py 不许再自己拼帧（绕过协议层就会重新散开）。"""
    import inspect

    import api.chat as chat

    src = inspect.getsource(chat)
    assert "_sse(" not in src, "又在 chat.py 里手写 _sse 了"
    # 文档字符串里会引用帧格式（那是协议说明），这里只挡"真的在拼帧"
    assert 'f"event: ' not in src and "f'event: " not in src, "疑似手拼了帧格式"
