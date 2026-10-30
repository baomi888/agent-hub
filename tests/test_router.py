"""执行前路由（core/router.py）+ 请求契约（api/chat.py）单测。

覆盖四类东西：
  1. 模型判定——workflow / agent 的归一与非法值处理；
  2. 兜底——超时、异常、非约定格式一律回落 agent，不许把路走死；
  3. 契约——`use_kb/use_web` 两个开关，以及旧 `mode` 的映射口径；
  4. 接线——`_plan_route` 把 workflow 映射成 hybrid，引擎关掉时退回 agent。

设计说明：`decide()` 是 async，这里用 `asyncio.run` 直接跑，不引入
pytest-asyncio —— 项目其它测试都是同步的，为几十个用例引一个插件不划算。

2026-10-30 变更：关键词规则层（`_STRONG/_MID/_SEQ/_OPEN/score_flow/
classify_flow_open`）随决策表 D9 删除，对应断言一并移除。原 24 条真实问句
现在全部交给模型判定，不再有"规则截走"这条路径。
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


def _decide(question: str, *, has_kb: bool, use_web: bool = False, llm=None):
    return asyncio.run(
        route_engine.decide(question, has_kb=has_kb, use_web=use_web, llm=llm)
    )


# ==================== 1. 模型判定 ====================


class TestModelDecision:
    def test_workflow(self):
        fake = _FakeLLM('{"route":"workflow","reason":"多步"}')
        decision = _decide("先把笔记整理成考点，然后汇总成表", has_kb=False, llm=fake)
        assert (decision.route, decision.source) == ("workflow", "model")
        assert fake.calls == 1

    def test_agent(self):
        fake = _FakeLLM('{"route":"agent","reason":"单步"}')
        decision = _decide("上海明天天气", has_kb=False, use_web=True, llm=fake)
        assert (decision.route, decision.source) == ("agent", "model")

    def test_uppercase_spaces_and_codeblock(self):
        """模型多嘴包了代码块、写了大小写，都不该影响解析。"""
        fake = _FakeLLM('```json\n{"route":" WORKFLOW ","reason":"x"}\n```')
        decision = _decide("先查再写", has_kb=True, llm=fake)
        assert decision.route == "workflow"

    def test_prompt_carries_only_bools_no_kb_id(self):
        """安全边界：路由只该知道"有没有库/能不能联网"，不该知道"哪个库"。"""
        fake = _FakeLLM('{"route":"agent","reason":"x"}')
        _decide("上海天气", has_kb=True, use_web=True, llm=fake)
        joined = "\n".join(m.content for m in fake.last_messages)
        assert "true" in joined.lower()
        assert "kb-" not in joined
        assert "kb_id" not in joined


# ==================== 2. 兜底：任何异常都不许把路走死 ====================


class TestFallback:
    def test_timeout_falls_back_to_agent(self, monkeypatch):
        monkeypatch.setattr(config, "ROUTER_TIMEOUT", 0.05)
        fake = _FakeLLM('{"route":"workflow"}', delay=0.3)
        decision = _decide("先读再汇总", has_kb=True, llm=fake)
        assert (decision.source, decision.route) == ("fallback", "agent")

    def test_exception_falls_back_to_agent(self):
        fake = _FakeLLM(exc=RuntimeError("upstream 500"))
        decision = _decide("随便问问", has_kb=False, llm=fake)
        assert (decision.source, decision.route) == ("fallback", "agent")

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "我觉得应该走 agent",
            '{"route":"flow"}',  # 旧枚举，现在非法
            '{"route":"rag"}',  # 旧枚举，现在非法
            '{"route":123}',
            "not json at all",
        ],
    )
    def test_bad_output_falls_back_to_agent(self, raw):
        fake = _FakeLLM(raw)
        decision = _decide("随便问问", has_kb=True, llm=fake)
        assert (decision.source, decision.route) == ("fallback", "agent")

    def test_router_disabled_always_agent(self, monkeypatch):
        """关掉路由 = 永远走智能体。没有工具时它就是一次普通问答。"""
        monkeypatch.setattr(config, "ROUTER_ENABLED", False)
        fake = _FakeLLM('{"route":"workflow"}')
        assert _decide("先把资料整理成表", has_kb=True, llm=fake).route == "agent"
        assert _decide("你好", has_kb=False, llm=fake).route == "agent"
        assert fake.calls == 0, "关闭路由后不该再调模型"


# ==================== 3. 契约：两个开关与旧 mode 的映射 ====================


def _req(**kw):
    from api.chat import ChatRequest

    kw.setdefault("sid", "s-1")
    kw.setdefault("question", "随便问问")
    return ChatRequest(**kw)


class TestResolveSwitches:
    """`_resolve_switches` 是旧 mode → 两个开关的唯一翻译处，口径写死在这里。"""

    def _sw(self, req, *, has_kb=False):
        from api.chat import _resolve_switches

        return _resolve_switches(req, has_kb)

    def test_new_fields_win(self):
        assert self._sw(_req(use_kb=True, use_web=False)) == (True, False, "fields")
        assert self._sw(_req(use_kb=False, use_web=True)) == (False, True, "fields")
        # 只传一个也算新契约：另一个按 False 处理，不回落到 mode
        assert self._sw(_req(use_kb=True, mode="rag")) == (True, False, "fields")

    @pytest.mark.parametrize(
        ("mode", "has_kb", "expect"),
        [
            ("rag", False, (True, False, "mode:rag")),
            ("llm", True, (False, False, "mode:llm")),
            ("agent", True, (True, True, "mode:agent")),
            ("agent", False, (False, True, "mode:agent")),
            ("hybrid", True, (True, True, "mode:hybrid")),
            ("auto", True, (True, True, "mode:auto")),
            ("auto", False, (False, True, "mode:auto")),
            # 脏值按 auto 处理，不让它穿透到下游分支
            ("__weird__", False, (False, True, "mode:auto")),
        ],
    )
    def test_legacy_mode_mapping(self, mode, has_kb, expect):
        assert self._sw(_req(mode=mode), has_kb=has_kb) == expect


# ==================== 4. 接线：_plan_route 的映射 ====================


class TestPlanRoute:
    def _plan(self, *, route: str, req=None, has_kb: bool = False, monkeypatch=None):
        from api import chat as chat_mod

        async def fake_decide(q, *, has_kb, use_web=False, llm=None):  # noqa: ARG001
            return route_engine.RouteDecision(route, "model", "test")

        monkeypatch.setattr(chat_mod.route_engine, "decide", fake_decide)
        req = req or _req(use_kb=True, use_web=True)
        kb_id = "kb-x" if has_kb else None
        return asyncio.run(chat_mod._plan_route(req, "任意问题", kb_id, has_kb))

    def test_workflow_maps_to_hybrid(self, monkeypatch):
        plan = self._plan(route="workflow", has_kb=True, monkeypatch=monkeypatch)
        assert (plan.mode, plan.route) == ("hybrid", "workflow")

    def test_workflow_degrades_when_hybrid_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "HYBRID_MODE_ENABLED", False)
        plan = self._plan(route="workflow", has_kb=True, monkeypatch=monkeypatch)
        assert (plan.mode, plan.route) == ("agent", "workflow")

    def test_agent_with_switches_goes_agent(self, monkeypatch):
        plan = self._plan(route="agent", has_kb=True, monkeypatch=monkeypatch)
        assert plan.mode == "agent"

    def test_agent_without_switches_goes_llm(self, monkeypatch):
        """两个开关都关：没有工具可绑，直接纯 LLM，省掉一层 ReAct 循环。"""
        plan = self._plan(
            route="agent",
            req=_req(use_kb=False, use_web=False),
            has_kb=True,
            monkeypatch=monkeypatch,
        )
        assert plan.mode == "llm"

    def test_explicit_legacy_modes_pass_through(self, monkeypatch):
        """显式 rag / llm / agent 不问路由：这是前端未改造期间的零行为变化口。"""
        for m in ("rag", "llm", "agent"):
            plan = self._plan(route="workflow", req=_req(mode=m), monkeypatch=monkeypatch)
            assert plan.mode == m

    def test_switches_reach_plan(self, monkeypatch):
        plan = self._plan(
            route="agent",
            req=_req(use_kb=True, use_web=False),
            has_kb=True,
            monkeypatch=monkeypatch,
        )
        assert (plan.use_kb, plan.use_web) == (True, False)
