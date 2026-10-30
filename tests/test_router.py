"""执行前路由（core/router.py）单测。

覆盖三类东西：
  1. 规则打分——高精度取向，重点是"别把闲聊/单步任务判成流程"；
  2. 模型判断——返回值的归一、非法值、超时、异常，全部必须降级而不是抛；
  3. 接线——`_pick_mode` 把 flow 映射成 hybrid，引擎关掉时退回 agent。

设计说明：`decide()` 是 async，这里用 `asyncio.run` 直接跑，不引入
pytest-asyncio —— 项目其它测试都是同步的，为 6 个用例引一个插件不划算。
"""

from __future__ import annotations

import asyncio

import pytest

from core import config
from core import router as route_engine


class _FakeLLM:
    """假模型：可控返回 / 可控延迟 / 可控异常，并记录被调用次数。"""

    def __init__(self, content: str | None = None, *, delay: float = 0.0, exc=None):
        self.content = content
        self.delay = delay
        self.exc = exc
        self.calls = 0
        self.last_messages = None

    async def ainvoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return type("_Resp", (), {"content": self.content})()


def _decide(question: str, *, has_kb: bool, llm=None):
    return asyncio.run(route_engine.decide(question, has_kb=has_kb, llm=llm))


# ==================== 1. 规则打分 ====================


class TestRule:
    @pytest.mark.parametrize(
        "question",
        [
            "先把我这份笔记整理成考点，然后汇总成一张表",
            "第一步读文档，第二步提取要点，最后制成复习卡片",
            "帮我批量导入这些论文并保存到资料库",
            "把这三份文档依次读一遍，然后按主题分类，最后生成报告",
            "先查一下天气，然后整理成周报",
            "先做这个，然后呢？",
            "读一遍然后总结",
        ],
    )
    def test_flow_cases(self, question):
        assert route_engine.classify_flow_open(question) == "flow"

    @pytest.mark.parametrize(
        "question",
        [
            # 2026-10-29 修掉的四个误判：全是单字弱词被"优先/先生/最后/按照"命中
            "优先看第三章",
            "先生，请问图书馆几点关门",
            "最后一个问题：这题怎么做",
            "按照你说的再讲一遍",
            # 单步任务不该背流程壳（"整理成"已从流程词里移除）
            "帮我把这份笔记整理成知识卡片",
            # 问"流程/步骤"是在问知识，不是要你执行（步骤/流程已降为 1 分）
            "这个算法的步骤有哪些",
            "混合模式的流程是怎样的",
            # 开放型问句
            "如何评价量子计算的发展前景",
            "帮我调研一下竞品",
            "先解释一下什么是 RRF",
            "你好",
        ],
    )
    def test_open_cases(self, question):
        assert route_engine.classify_flow_open(question) == "open"

    def test_score_shape(self):
        """打分是 (流程分, 开放分)，阈值 2 分且必须高于开放分。"""
        assert route_engine.score_flow("批量导入并保存") == (4, 0)
        assert route_engine.score_flow("你好") == (0, 0)
        # 先 + 再 是配对句式，计 2 分；"分析"再抵掉 1 分，仍过阈值
        assert route_engine.score_flow("先分析一下再汇总") == (3, 1)

    def test_flow_threshold_is_two(self):
        """只有 1 分（单个中特征）不允许判 flow —— 这是"宁可漏判"的落点。"""
        assert route_engine.classify_flow_open("帮我生成报告") == "open"
        assert route_engine.FLOW_MIN_SCORE == 2


# ==================== 2. 规则优先：命中就不问模型 ====================


class TestRuleShortCircuit:
    def test_rule_hit_skips_model(self):
        fake = _FakeLLM('{"route":"agent","reason":"x"}')
        decision = _decide("先读文档，然后汇总", has_kb=False, llm=fake)
        assert decision.route == "flow"
        assert decision.source == "rule"
        assert fake.calls == 0, "规则命中时不应该再花一次模型调用"

    def test_rule_miss_calls_model(self):
        fake = _FakeLLM('{"route":"agent","reason":"查天气"}')
        decision = _decide("上海明天天气", has_kb=False, llm=fake)
        assert decision.route == "agent"
        assert decision.source == "model"
        assert fake.calls == 1


# ==================== 3. 模型判断与两条硬约束 ====================


class TestModelDecision:
    def test_model_rag_with_kb(self):
        fake = _FakeLLM('{"route":"rag","reason":"指向我的资料"}')
        decision = _decide("我笔记里的里程碑是哪几个", has_kb=True, llm=fake)
        assert (decision.route, decision.source) == ("rag", "model")

    def test_model_rag_without_kb_degrades_to_agent(self):
        """没有库却要检索：必然检索不到，代码里直接改判。"""
        fake = _FakeLLM('{"route":"rag","reason":"x"}')
        decision = _decide("我笔记里的里程碑", has_kb=False, llm=fake)
        assert decision.route == "agent"

    def test_model_llm_with_kb_is_respected(self):
        """2026-10-30 改：绑库时模型判 llm **不再**被强制改判 rag。

        原约束（10-29）是"绑了库一律给引用，闲聊也去检索"，前提是 auto 是唯一入口。
        现在 agent 有 kb_search、用户也能手选「纯对话」，强制改判的收益没了，
        代价却还在：闲聊白等约 700ms，还会挂上无关引用。
        """
        fake = _FakeLLM('{"route":"llm","reason":"寒暄"}')
        decision = _decide("你好", has_kb=True, llm=fake)
        assert (decision.route, decision.source) == ("llm", "model")
        assert "强制检索" not in decision.reason

    def test_model_llm_without_kb(self):
        fake = _FakeLLM('{"route":"llm","reason":"寒暄"}')
        decision = _decide("你好", has_kb=False, llm=fake)
        assert decision.route == "llm"

    def test_uppercase_and_spaces_normalized(self):
        fake = _FakeLLM('```json\n{"route":" RAG ","reason":"x"}\n```')
        decision = _decide("这份文档讲了什么", has_kb=True, llm=fake)
        assert decision.route == "rag"

    def test_prompt_carries_only_bool_no_kb_id(self):
        """安全边界：路由只该知道"有没有库"，不该知道"哪个库"。"""
        fake = _FakeLLM('{"route":"agent","reason":"x"}')
        _decide("上海天气", has_kb=True, llm=fake)
        joined = "\n".join(m.content for m in fake.last_messages)
        assert "has_kb" not in joined  # 模板里是中文标签，英文键不该出现
        assert "true" in joined.lower()
        assert "kb-" not in joined


# ==================== 4. 兜底：任何异常都不许把路走死 ====================


class TestFallback:
    def test_timeout_falls_back_to_agent_even_with_kb(self, monkeypatch):
        """超时回落：**有库也走 agent**（2026-10-30 改）。

        旧行为是「有库 → rag」，于是绑库用户只要碰到一次超时就被打进检索，
        问天气也会去翻资料库。agent 自带 kb_search，回落 agent 不会丢引用能力。
        """
        monkeypatch.setattr(config, "ROUTER_TIMEOUT", 0.05)
        fake = _FakeLLM('{"route":"rag"}', delay=0.3)
        decision = _decide("我资料里的东西", has_kb=True, llm=fake)
        assert decision.source == "fallback"
        assert decision.route == "agent"

    def test_timeout_without_kb_falls_back_to_agent(self, monkeypatch):
        monkeypatch.setattr(config, "ROUTER_TIMEOUT", 0.05)
        fake = _FakeLLM('{"route":"rag"}', delay=0.3)
        decision = _decide("随便问问", has_kb=False, llm=fake)
        assert (decision.source, decision.route) == ("fallback", "agent")

    def test_exception_falls_back(self):
        fake = _FakeLLM(exc=RuntimeError("upstream 500"))
        decision = _decide("随便问问", has_kb=False, llm=fake)
        assert decision.source == "fallback"
        assert decision.route == "agent"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "我觉得应该走 agent",
            '{"route":"hybrid"}',  # flow 之外的非法枚举
            '{"route":123}',
            "not json at all",
        ],
    )
    def test_bad_output_falls_back(self, raw):
        fake = _FakeLLM(raw)
        decision = _decide("随便问问", has_kb=True, llm=fake)
        assert decision.source == "fallback"
        # 输出格式不对同样回落 agent（有库也一样，理由见 _fallback_route）
        assert decision.route == "agent"

    def test_router_disabled_restores_old_behavior(self, monkeypatch):
        """应急开关语义：关掉路由 ＝ 退回改造前，**连方向也不能变**。

        这条和上面的回落是两件事：`ROUTER_ENABLED=0` 表示"当作没有路由"，
        此时有库仍应 rag；而判断失败是"路由在、只是没判断出来"，回落 agent。
        """
        monkeypatch.setattr(config, "ROUTER_ENABLED", False)
        fake = _FakeLLM('{"route":"agent"}')
        assert _decide("上海天气", has_kb=False, llm=fake).route == "agent"
        assert _decide("上海天气", has_kb=True, llm=fake).route == "rag"
        assert _decide("我笔记里写了啥", has_kb=True, llm=fake).route == "rag"
        assert fake.calls == 0, "关闭路由后不该再调模型"


# ==================== 5. 接线：_pick_mode 的映射 ====================


class TestPickMode:
    def _pick(self, req_mode: str, question: str, *, has_kb: bool, monkeypatch, route: str):
        from api import chat as chat_mod

        async def fake_decide(q, *, has_kb, llm=None):  # noqa: ARG001
            return route_engine.RouteDecision(route, "model", "test")

        monkeypatch.setattr(chat_mod.route_engine, "decide", fake_decide)
        kb_id = "kb-x" if has_kb else None
        return asyncio.run(chat_mod._pick_mode(req_mode, question, kb_id, has_kb))

    def test_flow_maps_to_hybrid(self, monkeypatch):
        assert (
            self._pick("auto", "先读再汇总", has_kb=False, monkeypatch=monkeypatch, route="flow")
            == "hybrid"
        )

    def test_flow_degrades_when_hybrid_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "HYBRID_MODE_ENABLED", False)
        assert (
            self._pick("auto", "先读再汇总", has_kb=False, monkeypatch=monkeypatch, route="flow")
            == "agent"
        )

    def test_explicit_modes_untouched(self, monkeypatch):
        for m in ("rag", "agent", "llm"):
            assert (
                self._pick(m, "任意问题", has_kb=False, monkeypatch=monkeypatch, route="flow") == m
            )

    def test_hybrid_request_respects_switch(self, monkeypatch):
        monkeypatch.setattr(config, "HYBRID_MODE_ENABLED", False)
        assert (
            self._pick("hybrid", "任意问题", has_kb=False, monkeypatch=monkeypatch, route="agent")
            == "agent"
        )
