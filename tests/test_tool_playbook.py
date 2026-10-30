"""A8 回归：TOOL_PLAYBOOK 不能是"假校验"（2026-10-20 审计修复）。

修复前的状态：`assert_playbook_complete()` 只检查 `defer=True` 的工具，
而当时 5 个工具**全是 `defer=False`**——对一个空集合做全称判断，恒为真。
校验函数存在、被调用、永远不报错，但一个字段都没验过。

修复后：
  * 5 个直接工具全部配 playbook（直接工具才是模型最容易选错的那批）；
  * 校验从「有没有」升级为「有四条小节」，写一句"查知识库用的"过不了；
  * 键集必须与 TOOL_META 一致（拼错的键会造成"没配"和"配了个不存在的"同时发生）。

本测试锁死四件事：
  1) 覆盖：每个工具都有非空 playbook；
  2) 结构：四条小节齐全；
  3) 无孤儿：playbook 里没有未注册的工具名；
  4) 校验本身会拒绝坏数据（否则又变成假校验）。
"""

from __future__ import annotations

import pytest

from core import tool_registry as tr


def test_every_tool_has_playbook():
    assert set(tr.TOOL_PLAYBOOK) == set(tr.TOOL_META)
    for name, text in tr.TOOL_PLAYBOOK.items():
        assert text.strip(), f"{name} 的 playbook 是空的"


@pytest.mark.parametrize("name", sorted(tr.TOOL_META))
def test_playbook_has_all_sections(name):
    text = tr.TOOL_PLAYBOOK[name]
    for section in tr.PLAYBOOK_SECTIONS:
        assert section in text, f"{name} 缺小节「{section}」"


def test_playbook_is_not_a_one_liner():
    """一行解释过不了关：能说清"何时不用"必然要占篇幅。

    这条是防"应付式填写"的最后一道——真实写出来的 playbook 都在数百字量级。
    """
    for name, text in tr.TOOL_PLAYBOOK.items():
        assert len(text) >= 120, f"{name} 的 playbook 太短（{len(text)} 字），大概率是应付"


def test_sections_constant_is_used():
    """小节清单是一处定义：改这里就该影响校验，而不是散落在断言里。"""
    assert tr.PLAYBOOK_SECTIONS == ("何时用", "何时不用", "参数", "失败怎么办")


def test_assertion_rejects_missing_playbook(monkeypatch):
    """去掉一个 playbook → 校验必须抛（证明它不再是恒真）。"""
    broken = dict(tr.TOOL_PLAYBOOK)
    broken.pop("get_weather")
    monkeypatch.setattr(tr, "TOOL_PLAYBOOK", broken)
    with pytest.raises(RuntimeError) as e:
        tr.assert_playbook_complete()
    assert "get_weather" in str(e.value)


def test_assertion_rejects_incomplete_sections(monkeypatch):
    broken = {"get_weather": "查天气用的。"}
    monkeypatch.setattr(
        tr,
        "TOOL_PLAYBOOK",
        {**{k: tr.TOOL_PLAYBOOK[k] for k in tr.TOOL_META}, "get_weather": broken["get_weather"]},
    )
    with pytest.raises(RuntimeError) as e:
        tr.assert_playbook_complete()
    assert "缺小节" in str(e.value)


def test_assertion_rejects_orphan_key(monkeypatch):
    """拼错工具名（kb-serach）会让"没配"与"配了个不存在的"同时发生。"""
    broken = dict(tr.TOOL_PLAYBOOK)
    broken["kb-serach"] = tr.TOOL_PLAYBOOK["kb_search"]
    monkeypatch.setattr(tr, "TOOL_PLAYBOOK", broken)
    with pytest.raises(RuntimeError) as e:
        tr.assert_playbook_complete()
    assert "kb-serach" in str(e.value)


def test_schema_exposes_playbook():
    """discover() 返回的 schema 要带上 playbook——第二层的用途就在这里。"""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(tr, "_activated_by_owner", {})
        name = tr.tool_names()[0]
        schema = tr._schema_of(name)
        assert schema["playbook"] == tr.TOOL_PLAYBOOK[name].strip()
        assert schema["refs"] == []
    finally:
        monkeypatch.undo()
