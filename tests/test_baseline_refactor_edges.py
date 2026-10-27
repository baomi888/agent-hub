"""2026-10-16「剩余模块治理」为重构点补的边界/契约基线。

覆盖本轮两处**行为契约**：
  1. `hybrid/expr.py` —— 条件求值承诺"任何异常都退成 False"。此前只兜 `ExprError`，
     含 NUL 的表达式（`ast.parse` 抛 `ValueError`）与超深嵌套（`RecursionError`）
     会漏出去，把异常抛进编排主循环。本组用例钉住"必须退成 False / 必须抛 ExprError"。
  2. `api/delegate.py` —— 启用数量上限的统计口径（`_count_enabled`）与返回体契约（`_out`）。

全部离线：不触网、不连真库、不依赖运行中的服务。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import delegate as deleg
from hybrid.expr import ExprError, eval_condition, safe_eval

# ==================== hybrid/expr.py ====================


def test_eval_condition_normal_semantics_unchanged():
    """本轮只加宽异常兜底，正常命中/不命中的语义必须一字不变。"""
    assert eval_condition("shared.score > 0.5", {"shared": {"score": 0.8}}) is True
    assert eval_condition("shared.score > 0.5", {"shared": {"score": 0.2}}) is False
    assert eval_condition("", {"shared": {}}) is False  # 空表达式
    assert eval_condition("unknown_name > 1", {"shared": {}}) is False  # 未定义名字


def test_eval_condition_returns_false_on_null_byte():
    """源码含 NUL 时 ast.parse 抛的是 ValueError（不是 SyntaxError）——必须退成 False。"""
    expr = "shared.score\x00 > 0"
    assert eval_condition(expr, {"shared": {"score": 1}}) is False
    with pytest.raises(ExprError):
        safe_eval(expr, {"shared": {"score": 1}})


def test_eval_condition_returns_false_on_deep_nesting():
    """左深二元式会让 _eval 递归超过解释器上限（RecursionError）——必须退成 False。"""
    expr = "1" + "+1" * 3000
    assert eval_condition(expr) is False
    with pytest.raises(ExprError):
        safe_eval(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "[x for x in (1, 2)]",  # 推导式
        "(1).__class__",  # 私有属性
        "__import__('os')",  # 未定义/危险名字
        "1 if 1 else 0",  # 条件表达式
        "x := 1",  # 海象（语法上都可能过，求值白名单必须挡）
    ],
)
def test_eval_condition_rejects_disallowed_syntax(expr):
    """白名单之外一律按"条件不成立"处理，不留任何可执行口子。"""
    assert eval_condition(expr, {"x": 1}) is False


# ==================== api/delegate.py ====================


def test_count_enabled_ignores_disabled_and_missing():
    """启用上限的统计口径：只数 enabled 为真的条目。"""
    items = [{"enabled": True}, {"enabled": False}, {}, {"enabled": True}]
    assert deleg._count_enabled(items) == 2
    assert deleg._count_enabled([]) == 0


def _delegation_record(**over) -> dict:
    rec = {
        "delegation_id": "d1",
        "name": "晨报",
        "kind": "daily",
        "schedule_json": '{"freq": "daily", "at": "08:00"}',
        "prompt": "",
        "deliver_sid": "s1",
        "kb_id": None,
        "tone": "",
        "max_chars": 600,
        "enabled": True,
        "next_run_at": 1,
        "updated_at": 2,
    }
    rec.update(over)
    return rec


def test_out_keeps_both_key_name_sets_and_parses_schedule(monkeypatch):
    """两套键名并存（前端契约 + 内部原名）；schedule_json 字符串解析成对象。"""
    monkeypatch.setattr(deleg.pipelines, "name_of", lambda kb_id: None)
    out = deleg._out(_delegation_record())
    assert out["id"] == "d1" and out["delegation_id"] == "d1"
    assert out["instruction"] == "" and out["prompt"] == ""
    assert out["session_id"] == "s1" and out["deliver_sid"] == "s1"
    assert out["schedule"] == {"freq": "daily", "at": "08:00"}
    assert out["enabled"] is True


def test_out_tolerates_broken_schedule_json(monkeypatch):
    """schedule_json 解析失败时 schedule 置 None，不抛异常。"""
    monkeypatch.setattr(deleg.pipelines, "name_of", lambda kb_id: None)
    out = deleg._out(_delegation_record(schedule_json="{not json"))
    assert out["schedule"] is None


# ---- 输入契约：前端发的是 session_id / instruction，后端字段名是 deliver_sid / prompt ----
#
# 这一组是本轮"[object Object]"异常的回归钉。此前只有 `_out`（**输出**）做了加法兼容，
# **输入**没做 ⇒ 前端一提交就 422 ⇒ 错误体里 detail 是数组 ⇒ 前端 `new Error(detail)`
# 把它转成 "[object Object]"。任何一侧改回旧行为，这组用例立刻红。


def _frontend_body(**over):
    """照抄 frontend/src/app/delegate/page.tsx::submit 里拼的 body。"""
    body = {
        "name": "每日复盘",
        "kind": "study_plan",
        "schedule": {
            "freq": "daily",
            "at": "21:00",
            "weekday": None,
            "every_minutes": None,
            "tz": "Asia/Shanghai",
        },
        "session_id": "s1",
        "kb_id": None,
        "instruction": "给我每天都生成一次复盘",
        "tone": "neutral",
        "max_chars": 600,
        "enabled": True,
    }
    body.update(over)
    return body


def test_delegation_in_accepts_frontend_field_names():
    """前端 body 必须能直接过校验，并映射到内部的 deliver_sid / prompt。"""
    req = deleg.DelegationIn(**_frontend_body())
    assert req.deliver_sid == "s1"
    assert req.prompt == "给我每天都生成一次复盘"


def test_delegation_in_still_accepts_canonical_field_names():
    """加法兼容：旧调用方（文档/测试文档里的 deliver_sid / prompt）不能被打翻。"""
    req = deleg.DelegationIn(
        name="早上七点",
        kind="study_plan",
        schedule={"freq": "daily", "at": "07:00"},
        deliver_sid="s9",
        prompt="列今日三件事",
    )
    assert req.deliver_sid == "s9" and req.prompt == "列今日三件事"


def test_delegation_in_defaults_deliver_sid_to_none():
    """前端「默认会话」发的是 null —— 不能因此 422（null 由 resolver 兜）。"""
    req = deleg.DelegationIn(**_frontend_body(session_id=None))
    assert req.deliver_sid is None


def test_delegation_patch_accepts_frontend_field_names():
    """PATCH 同样吃前端的指令/会话字段名，否则「编辑指令」是静默不生效的。"""
    patch = deleg.DelegationPatch(
        schedule={"freq": "daily", "at": "21:00"},
        session_id="s2",
        instruction="改成每天写复盘",
        enabled=False,
    ).model_dump(exclude_unset=True)
    assert patch["deliver_sid"] == "s2"
    assert patch["prompt"] == "改成每天写复盘"
    # 没提交的字段不应出现在 exclude_unset 的结果里（PATCH 的"只改提交的"语义）
    assert "kind" not in patch and "name" not in patch


def test_normalize_kind_maps_legacy_alias_and_rejects_unknown():
    """kind=study 是前端历史写法，归一成规范值 study_plan；别的值仍 400。"""
    assert deleg._normalize_kind("study") == "study_plan"
    assert deleg._normalize_kind("review") == "review"
    with pytest.raises(HTTPException) as ei:
        deleg._normalize_kind("whatever")
    assert ei.value.status_code == 400


def test_resolve_deliver_sid_keeps_owned_session(monkeypatch):
    monkeypatch.setattr(deleg.store, "get_conversation", lambda uid, sid: {"id": sid})
    assert deleg._resolve_deliver_sid("u1", "s1") == "s1"


def test_resolve_deliver_sid_404_on_foreign_or_missing(monkeypatch):
    """别人的 / 不存在的 sid 一律 404（不泄露存在性）。"""
    monkeypatch.setattr(deleg.store, "get_conversation", lambda uid, sid: None)
    with pytest.raises(HTTPException) as ei:
        deleg._resolve_deliver_sid("u1", "someone-else")
    assert ei.value.status_code == 404


def test_resolve_deliver_sid_falls_back_to_latest_conversation(monkeypatch):
    """「默认会话」（null / 空串）落到最近更新的会话，而不是存一条永不投递的委托。"""
    monkeypatch.setattr(
        deleg.store, "list_conversations", lambda uid: [{"id": "newest"}, {"id": "older"}]
    )
    assert deleg._resolve_deliver_sid("u1", None) == "newest"
    assert deleg._resolve_deliver_sid("u1", "") == "newest"


def test_resolve_deliver_sid_400_when_no_conversation(monkeypatch):
    """一个会话都没有：明确 400 让用户先去建会话，而不是静默失败。"""
    monkeypatch.setattr(deleg.store, "list_conversations", lambda uid: [])
    with pytest.raises(HTTPException) as ei:
        deleg._resolve_deliver_sid("u1", None)
    assert ei.value.status_code == 400

