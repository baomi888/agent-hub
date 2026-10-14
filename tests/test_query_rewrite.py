# -*- coding: utf-8 -*-
"""条件式查询改写自测（纯函数 + 打桩 LLM，不需要网络）。

覆盖三件事：
  1. 触发判定 needs_rewrite：只在"像追问"（有历史 + 短问题 / 含指代词）时才改写；
  2. 关闭开关或无需改写时，rewrite_query 原样返回问题，零额外开销；
  3. 改写失败 / 结果不可信时一律退回原问题，绝不阻塞检索。

运行：python tests/test_query_rewrite.py
"""
import os
import sys

# 从项目根导入（直接跑 tests/ 下的脚本时 sys.path 里没有根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kb.rag_core as rc
from core import config

HIST = [{"role": "user", "content": "介绍一下混合检索里的 RRF 融合算法"}]


def test_no_history_never_rewrites():
    assert rc.needs_rewrite("那它呢", []) is False


def test_short_question_with_history_is_followup():
    assert rc.needs_rewrite("那它呢", HIST) is True


def test_pronoun_cue_with_history_is_followup():
    assert rc.needs_rewrite("这个方案有什么缺点", HIST) is True


def test_standalone_question_not_rewritten():
    assert rc.needs_rewrite("帮我讲一下向量数据库的索引原理", HIST) is False


def test_disabled_switch_returns_original(monkeypatch):
    monkeypatch.setattr(config, "QUERY_REWRITE_ENABLED", False)
    assert rc.rewrite_query("那它呢", HIST) == "那它呢"


def test_not_needed_returns_original(monkeypatch):
    monkeypatch.setattr(config, "QUERY_REWRITE_ENABLED", True)
    q = "帮我解释一下向量检索的基本原理和实现"
    assert rc.rewrite_query(q, HIST) == q


def test_llm_failure_falls_back(monkeypatch):
    monkeypatch.setattr(config, "QUERY_REWRITE_ENABLED", True)

    class Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("network down")

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: Boom())
    assert rc.rewrite_query("那它呢", HIST) == "那它呢"


def test_implausible_output_falls_back(monkeypatch):
    monkeypatch.setattr(config, "QUERY_REWRITE_ENABLED", True)

    class FakeResp:
        content = "好" * 300  # 模型跑偏成回答，过长不可信

    class FakeLLM:
        def invoke(self, *_a, **_k):
            return FakeResp()

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: FakeLLM())
    assert rc.rewrite_query("那它呢", HIST) == "那它呢"


def test_success_returns_rewritten(monkeypatch):
    monkeypatch.setattr(config, "QUERY_REWRITE_ENABLED", True)

    class FakeResp:
        content = "RRF 融合算法有什么缺点"

    class FakeLLM:
        def invoke(self, *_a, **_k):
            return FakeResp()

    monkeypatch.setattr(rc, "get_llm", lambda *a, **k: FakeLLM())
    assert rc.rewrite_query("这个有什么缺点", HIST) == "RRF 融合算法有什么缺点"


if __name__ == "__main__":
    import inspect

    cases = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo.clear()

    ok = 0
    for fn in cases:
        mp = _MP()
        try:
            if "monkeypatch" in inspect.signature(fn).parameters:
                fn(mp)
            else:
                fn()
            print(f"  PASS  {fn.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
        finally:
            mp.undo()
    print(f"\n{ok}/{len(cases)} passed")