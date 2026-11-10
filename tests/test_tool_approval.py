"""L4 真实人工确认：一次性 token 的绑定 / 过期 / 用后即焚。

本文件的核心断言是**模型自证不成立**：
`download_file` 这类写外部状态的工具，模型不带 token 时第一次被拦、
**同参数再调一次仍然被拦**，全程不执行。旧实现「同参数再调一次 + hash 命中
就放行」在这里会失败，所以这条测试不是为了覆盖代码，而是为了锁死语义。

同时覆盖：人工点击签发、绑定 (user, tool_name, args, turn)、过期、重放、
只读工具不走强确认、严格开关关闭时回退旧行为（对照用）。
"""

from __future__ import annotations

import time

import pytest

from core import approval_store, config, tool_guard

ARGS = {"url": "https://example.com/a.pdf"}


def _config(owner: str = "u1", turn: str = "t1", token: str = "") -> dict:
    return {
        "config": {
            "configurable": {
                "owner_id": owner,
                "turn_id": turn,
                "approval_token": token,
            }
        }
    }


def _run(
    calls: list[str],
    args: dict | None = None,
    cfg: dict | None = None,
) -> str:
    """直接调 guarded_run，绕开真实下载：只关心守卫是否放行。"""

    def fake_download(*a, **kw):
        calls.append("run")
        return "下载成功"

    return tool_guard.guarded_run(
        "download_file",
        fake_download,
        args or ARGS,
        None,
        (),
        cfg or _config(),
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    approval_store.reset()
    monkeypatch.setattr(config, "TOOL_APPROVAL_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_APPROVAL_REAL_CONFIRM_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_REPEAT_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_APPROVAL_TTL_SECONDS", 120)


def test_model_repeating_same_args_is_still_rejected():
    """L4 核心断言：模型「同参数再调一次」仍然被拒，且一次都没执行。"""
    calls: list[str] = []

    with tool_guard.guard_scope():
        first = _run(calls)  # 模型第一次调用
        retry = _run(calls)  # 模型自己再用完全相同的参数调一次

    assert calls == [], "模型未获人工确认，任何一次都不该真的执行"
    assert "本次未执行" in first
    assert "本次未执行" in retry
    assert "不要用相同参数再调用一次" in retry

    pending = approval_store.pending_for_turn("u1", "t1")
    assert len(pending) == 1, "同参数重试应复用同一条待确认请求，而不是刷屏"
    assert not approval_store.has_active_token(pending[0].request_id), "模型没有点击权，不该有 token"


def test_human_click_issues_single_use_token_and_executes_once():
    calls: list[str] = []

    with tool_guard.guard_scope():
        _run(calls)
        pending = approval_store.pending_for_turn("u1", "t1")[0]

    issued = approval_store.issue_token(pending.request_id, "u1")
    assert issued is not None
    token = issued["approval_token"]
    assert issued["turn_id"] == "t1"
    assert issued["args_hash"] == approval_store.args_hash(ARGS)

    with tool_guard.guard_scope():
        ok = _run(calls, cfg=_config(token=token))
        replay = _run(calls, cfg=_config(token=token))

    assert ok == "下载成功"
    assert len(calls) == 1
    assert "本次未执行" in replay
    assert len(calls) == 1, "token 用后即焚，重放不得再执行"


@pytest.mark.parametrize(
    "owner,turn,args",
    [
        ("u2", "t1", ARGS),  # 换用户
        ("u1", "t2", ARGS),  # 换轮次
        ("u1", "t1", {"url": "https://example.com/b.pdf"}),  # 换参数
    ],
)
def test_token_is_bound_to_user_tool_args_turn(owner, turn, args):
    calls: list[str] = []
    with tool_guard.guard_scope():
        _run(calls)
        pending = approval_store.pending_for_turn("u1", "t1")[0]
    token = approval_store.issue_token(pending.request_id, "u1")["approval_token"]

    with tool_guard.guard_scope():
        out = _run(calls, args=args, cfg=_config(owner=owner, turn=turn, token=token))

    assert out != "下载成功"
    assert "本次未执行" in out
    assert calls == []


def test_token_expires():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(config, "TOOL_APPROVAL_TTL_SECONDS", 1)
        calls: list[str] = []
        with tool_guard.guard_scope():
            _run(calls)
            pending = approval_store.pending_for_turn("u1", "t1")[0]
        token = approval_store.issue_token(pending.request_id, "u1")["approval_token"]
        time.sleep(1.1)
        with tool_guard.guard_scope():
            out = _run(calls, cfg=_config(token=token))
        assert out != "下载成功"
        assert "本次未执行" in out
        assert calls == []
    finally:
        monkeypatch.undo()


def test_issue_token_requires_same_user():
    with tool_guard.guard_scope():
        _run([])
        pending = approval_store.pending_for_turn("u1", "t1")[0]
    assert approval_store.issue_token(pending.request_id, "u2") is None
    assert approval_store.issue_token("does-not-exist", "u1") is None


def test_read_only_tools_keep_metadata_only_guard():
    """高风险分级：写外部状态的走强确认，只读的不走人工确认。"""
    from core.tool_registry import TOOL_META

    assert TOOL_META["download_file"].needs_approval is True
    for name in ("kb_search", "web_search", "get_weather"):
        assert TOOL_META[name].needs_approval is False

    calls: list[str] = []

    def fake_search(*a, **kw):
        calls.append("run")
        return "检索结果"

    cfg = {"config": {"configurable": {"owner_id": "u1", "turn_id": "t1"}}}
    with tool_guard.guard_scope():
        out = tool_guard.guarded_run("kb_search", fake_search, {"query": "x"}, None, (), cfg)
    assert out == "检索结果"
    assert calls == ["run"]


def test_strict_switch_off_falls_back_to_legacy_confirm_string(monkeypatch):
    """关闭严格开关 = 回到旧「模型带确认串再调一次」行为，用于 A/B 对照。

    这条测试同时说明：新旧差异是真实存在的，不是把同一段代码换个名字。
    """
    monkeypatch.setattr(config, "TOOL_APPROVAL_REAL_CONFIRM_ENABLED", False)
    calls: list[str] = []

    with tool_guard.guard_scope():
        first = _run(calls)
        assert "确认串" in first
        assert len(calls) == 0, "旧模式第一次也不执行（回确认串）"
        second = _run(calls)

    assert second == "下载成功", "旧模式下模型自证可通过——这正是要替换掉的行为"
    assert len(calls) == 1
