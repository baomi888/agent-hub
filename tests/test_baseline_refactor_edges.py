# -*- coding: utf-8 -*-
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

from api import delegate as deleg
from hybrid.expr import ExprError, eval_condition, safe_eval


# ==================== hybrid/expr.py ====================


def test_eval_condition_normal_semantics_unchanged():
    """本轮只加宽异常兜底，正常命中/不命中的语义必须一字不变。"""
    assert eval_condition("shared.score > 0.5", {"shared": {"score": 0.8}}) is True
    assert eval_condition("shared.score > 0.5", {"shared": {"score": 0.2}}) is False
    assert eval_condition("", {"shared": {}}) is False          # 空表达式
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
        "[x for x in (1, 2)]",   # 推导式
        "(1).__class__",         # 私有属性
        "__import__('os')",      # 未定义/危险名字
        "1 if 1 else 0",         # 条件表达式
        "x := 1",                # 海象（语法上都可能过，求值白名单必须挡）
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
