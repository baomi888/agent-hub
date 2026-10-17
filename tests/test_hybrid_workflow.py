"""工作流引擎回归（混合架构要求 2）：假 LLM / 假 Agent 注入，不联网。

覆盖七项：
  1. 定义校验与模板渲染：validate_definition 图完整性 + render_template 占位符；
  2. 线性流：两节点顺序执行、产物进 shared、{question}/{shared.x} 模板渲染；
  3. 条件分支：表达式命中走分支、不命中落默认边、全不命中且无默认 → 失败上报；
  4. 节点级超时与流程级整体超时；
  5. retry 重试：先重试再谈失败；一直失败则上报 node_failed；
  6. on_fail 失败跳转：补偿分支优先于切换规则；
  7. Agent 融合：agent 节点内嵌执行；requires_agent 让出与断点续跑。

可从任意位置运行：python tests/test_hybrid_workflow.py
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hybrid.context import ExecEvent, TaskContext  # noqa: E402
from hybrid.engine import EngineResult  # noqa: E402
from hybrid.workflow import (  # noqa: E402
    WorkflowDef,
    WorkflowEngine,
    render_template,
    validate_definition,
)

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


def _ctx(goal: str = "测试目标") -> TaskContext:
    return TaskContext(owner_id="u1", goal=goal, current_mode="workflow")


async def _collect(agen) -> list[ExecEvent]:
    out = []
    async for ev in agen:
        out.append(ev)
    return out


def _run(engine: WorkflowEngine, ctx: TaskContext) -> list[ExecEvent]:
    return asyncio.run(_collect(engine.stream(ctx, lambda: None)))


def _types(events: list[ExecEvent]) -> list[str]:
    return [e.event_type for e in events]


# ==================== 假 LLM / 假 Agent ====================


class _FakeLLM:
    """ainvoke 返回固定文案；astream 吐两片 token；记录每次 prompt。"""

    def __init__(self, reply: str = "甲", delay: float = 0.0):
        self.reply = reply
        self.delay = delay
        self.prompts: list[str] = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        return SimpleNamespace(content=self.reply)

    async def astream(self, prompt):
        self.prompts.append(prompt)
        yield SimpleNamespace(content="流")
        yield SimpleNamespace(content="式")


class _FlakyLLM:
    """前 fail_times 次抛异常，之后正常。"""

    def __init__(self, fail_times: int = 1):
        self.fail_times = fail_times
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("偶发故障")
        return SimpleNamespace(content=f"第{self.calls}次成功")


class _FakeAgentEngine:
    """假 Agent 引擎：产出一个 token 事件，按 ok 决定成败。"""

    name = "agent"

    def __init__(self, text: str = "自主产出", ok: bool = True):
        self.result: EngineResult | None = None
        self._text = text
        self._ok = ok
        self.goals: list[str] = []

    async def stream(self, ctx, control, goal=None, output_key=None, node_id=None, **_):
        self.goals.append(goal or "")
        yield ExecEvent(
            event_type="token", mode="agent", node_id=node_id, payload={"text": self._text}
        )
        if self._ok:
            ctx.shared[output_key or "agent_result"] = self._text
            self.result = EngineResult(
                status="completed", signal="goal_satisfied", output_key=output_key
            )
        else:
            self.result = EngineResult(status="failed", error="agent炸了")


def _wf(data: dict) -> WorkflowDef:
    return validate_definition(data)


# ==================== 1. 定义校验与模板渲染 ====================


def test_1_validate_and_template() -> None:
    print("\n[1] validate_definition 图完整性 / render_template")
    base = {
        "id": "t",
        "entry": "a",
        "nodes": [{"id": "a", "type": "llm", "config": {}}],
        "edges": [],
    }
    check(_wf(base).entry_id() == "a", "合法定义通过校验")

    def _bad(mutate, expect: str) -> bool:
        import copy

        d = copy.deepcopy(base)
        mutate(d)
        try:
            validate_definition(d)
            return False
        except ValueError as e:
            return expect in str(e)

    check(_bad(lambda d: d["nodes"].append({"id": "a", "type": "llm"}), "重复"), "节点 id 重复被拒")
    check(
        _bad(lambda d: d["edges"].append({"source": "a", "target": "ghost"}), "不存在"),
        "边指向不存在节点被拒",
    )
    check(
        _bad(lambda d: d["nodes"][0].update({"on_fail": "ghost"}), "on_fail"),
        "on_fail 指向不存在被拒",
    )
    check(_bad(lambda d: d.update({"entry": "ghost"}), "入口"), "入口不存在被拒")
    check(_bad(lambda d: d.update({"nodes": []}), "至少"), "空节点列表被拒")

    ctx = _ctx("写周报")
    ctx.shared["plan"] = "三步走"
    ctx.shared["nested"] = {"x": "值"}
    check(render_template("目标：{question}", ctx) == "目标：写周报", "{question} 渲染")
    check(render_template("按{shared.plan}执行", ctx) == "按三步走执行", "{shared.x} 渲染")
    check(render_template("{shared.nested.x}", ctx) == "值", "嵌套 shared 渲染")
    check(render_template("[{shared.nope}]", ctx) == "[]", "缺失 key 渲染为空串（不炸）")
    check(render_template("{shared.plan.x}", ctx) == "", "中间值非 dict 渲染为空串")
    check(
        render_template("{unknown} {shared..}", ctx) == "{unknown} {shared..}", "未知占位符原样保留"
    )


# ==================== 2. 线性流 ====================


def test_2_linear_flow() -> None:
    print("\n[2] 线性流：顺序执行 / 产物进 shared / 模板渲染")
    wf = _wf(
        {
            "id": "t_linear",
            "entry": "a",
            "nodes": [
                {
                    "id": "a",
                    "type": "llm",
                    "config": {"prompt": "第一步：{question}", "output_key": "ka"},
                },
                {
                    "id": "b",
                    "type": "llm",
                    "config": {
                        "prompt": "参考[{shared.ka}]继续",
                        "output_key": "kb",
                        "stream": True,
                    },
                },
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
    )
    llm = _FakeLLM(reply="甲")
    engine = WorkflowEngine(wf=wf, llm=llm)
    ctx = _ctx("写方案")
    events = _run(engine, ctx)

    check(engine.result is not None and engine.result.status == "completed", "跑到底：completed")
    check(ctx.shared.get("ka") == "甲", "节点 a 产物写入 shared.ka")
    check(ctx.shared.get("kb") == "流式", "流式节点 b 产物拼接写入 shared.kb")
    check(llm.prompts[0] == "第一步：写方案", "{question} 渲染进 prompt")
    check(llm.prompts[1] == "参考[甲]继续", "{shared.ka} 渲染进下游 prompt（跨节点数据通道）")
    t = _types(events)
    check(
        t == ["node_enter", "node_exit", "node_enter", "token", "token", "node_exit"],
        "事件序列：enter/exit 成对 + 流式 token",
    )
    check(events[0].node_id == "a" and events[-1].node_id == "b", "事件携带 node_id")
    check(ctx.workflow_state["completed"] == ["a", "b"], "completed 记录两节点")
    check("next_node" not in ctx.workflow_state, "跑完后断点锚点清除")


# ==================== 3. 条件分支 ====================


def _branch_wf(reply: str) -> tuple[WorkflowDef, _FakeLLM]:
    wf = _wf(
        {
            "id": "t_branch",
            "entry": "a",
            "nodes": [
                {"id": "a", "type": "llm", "config": {"output_key": "kind"}},
                {"id": "b", "type": "llm", "config": {"output_key": "kb"}},
                {"id": "c", "type": "llm", "config": {"output_key": "kc"}},
            ],
            "edges": [
                {"source": "a", "target": "b", "condition": "shared.kind == '甲'"},
                {"source": "a", "target": "c"},
            ],
        }
    )
    return wf, _FakeLLM(reply=reply)


def test_3_condition_branch() -> None:
    print("\n[3] 条件分支：命中 / 默认兜底 / 全不命中")
    wf, llm = _branch_wf("甲")
    engine = WorkflowEngine(wf=wf, llm=llm)
    ctx = _ctx()
    _run(engine, ctx)
    check(engine.result.status == "completed", "命中条件边：跑完")
    check("kb" in ctx.shared and "kc" not in ctx.shared, "命中走 b 分支，c 未执行")

    wf2, llm2 = _branch_wf("乙")
    engine2 = WorkflowEngine(wf=wf2, llm=llm2)
    ctx2 = _ctx()
    _run(engine2, ctx2)
    check("kc" in ctx2.shared and "kb" not in ctx2.shared, "不命中落默认边 c")

    wf3 = _wf(
        {
            "id": "t_noroute",
            "entry": "a",
            "nodes": [
                {"id": "a", "type": "llm", "config": {"output_key": "kind"}},
                {"id": "b", "type": "llm", "config": {}},
            ],
            "edges": [{"source": "a", "target": "b", "condition": "shared.kind == '不存在的值'"}],
        }
    )
    engine3 = WorkflowEngine(wf=wf3, llm=_FakeLLM(reply="随便"))
    ctx3 = _ctx()
    events3 = _run(engine3, ctx3)
    r3 = engine3.result
    check(
        r3.status == "failed" and r3.signal == "node_failed",
        "全不命中且无默认边：failed + node_failed 上报",
    )
    check("无匹配" in (r3.error or ""), "错误说明分支无匹配")
    check("error" in _types(events3), "error 事件留痕")


# ==================== 4. 超时 ====================


def test_4_timeout() -> None:
    print("\n[4] 节点级超时 / 流程级整体超时")
    wf = _wf(
        {
            "id": "t_nto",
            "entry": "a",
            "nodes": [
                {"id": "a", "type": "llm", "timeout_seconds": 1, "config": {"output_key": "ka"}}
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(wf=wf, llm=_FakeLLM(reply="慢", delay=3.0))
    ctx = _ctx()
    _run(engine, ctx)
    r = engine.result
    check(r.status == "failed" and r.signal == "node_failed", "节点超时：failed + node_failed")
    check("超时" in (r.error or ""), "错误说明含超时")

    wf2 = _wf(
        {
            "id": "t_wto",
            "entry": "a",
            "timeout_seconds": 1,
            "nodes": [
                {"id": "a", "type": "llm", "config": {"output_key": "ka"}},
                {"id": "b", "type": "llm", "config": {"output_key": "kb"}},
                {"id": "c", "type": "llm", "config": {"output_key": "kc"}},
            ],
            "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
        }
    )
    engine2 = WorkflowEngine(wf=wf2, llm=_FakeLLM(reply="慢", delay=0.7))
    ctx2 = _ctx()
    events2 = _run(engine2, ctx2)
    r2 = engine2.result
    check(r2.status == "timeout", "整体超时护栏：timeout")
    check(ctx2.workflow_state["completed"] == ["a", "b"], "超时前两节点已完成")
    enters = [e.node_id for e in events2 if e.event_type == "node_enter"]
    check("c" not in enters, "节点 c 未进入（截止线拦在循环顶部）")


# ==================== 5. retry ====================


def test_5_retry() -> None:
    print("\n[5] retry：先重试再谈失败")
    wf = _wf(
        {
            "id": "t_retry",
            "entry": "a",
            "nodes": [{"id": "a", "type": "llm", "retry": 1, "config": {"output_key": "ka"}}],
            "edges": [],
        }
    )
    llm = _FlakyLLM(fail_times=1)
    engine = WorkflowEngine(wf=wf, llm=llm)
    ctx = _ctx()
    events = _run(engine, ctx)
    check(engine.result.status == "completed", "第一次失败第二次成功：整体 completed")
    check(llm.calls == 2, "实际调用了 2 次")
    check(ctx.shared.get("ka") == "第2次成功", "重试后的产物进 shared")
    check(
        any(e.event_type == "status" and "重试" in e.payload.get("text", "") for e in events),
        "重试过程有 status 事件留痕",
    )

    llm2 = _FlakyLLM(fail_times=99)
    engine2 = WorkflowEngine(wf=wf, llm=llm2)
    ctx2 = _ctx()
    _run(engine2, ctx2)
    r2 = engine2.result
    check(llm2.calls == 2, "一直失败：retry=1 共尝试 2 次后放弃")
    check(
        r2.status == "failed" and r2.signal == "node_failed", "重试耗尽：failed + node_failed 上报"
    )
    check("偶发故障" in (r2.error or ""), "错误带最后一次失败原因")


# ==================== 6. on_fail ====================


def test_6_on_fail() -> None:
    print("\n[6] on_fail 失败跳转：补偿分支优先于切换规则")
    wf = _wf(
        {
            "id": "t_onfail",
            "entry": "a",
            "nodes": [
                {"id": "a", "type": "llm", "on_fail": "b", "config": {"output_key": "ka"}},
                {"id": "b", "type": "llm", "config": {"output_key": "kb"}},
            ],
            "edges": [],
        }
    )
    # 节点 a 用必败 LLM，节点 b 换个引擎实例共享一个总是成功的 LLM 做不到（单 llm 注入），
    # 所以让 _FlakyLLM 只在第 1 次失败：a 失败后跳 b，b 一次成功。
    llm = _FlakyLLM(fail_times=1)
    engine = WorkflowEngine(wf=wf, llm=llm)
    ctx = _ctx()
    events = _run(engine, ctx)
    r = engine.result
    check(r.status == "completed", "a 失败跳 b，b 成功：整体 completed（不触发切换）")
    check(ctx.workflow_state["completed"] == ["a", "b"], "a 按失败完成、b 正常完成")
    check("kb" in ctx.shared, "补偿节点 b 的产物进 shared")
    exit_a = [e for e in events if e.event_type == "node_exit" and e.node_id == "a"]
    check(
        bool(exit_a)
        and exit_a[0].payload.get("status") == "failed"
        and exit_a[0].payload.get("on_fail") == "b",
        "a 的 exit 事件带 on_fail 去向",
    )


# ==================== 7. Agent 融合 ====================


def test_7_agent_node_and_requires_agent() -> None:
    print("\n[7] agent 节点内嵌 / requires_agent 让出与续跑")
    # --- agent 类型节点：流程内一等公民，不走切换规则 ---
    wf = _wf(
        {
            "id": "t_agent_node",
            "entry": "a",
            "nodes": [
                {"id": "a", "type": "llm", "config": {"output_key": "ka"}},
                {
                    "id": "g",
                    "type": "agent",
                    "config": {"goal": "用[{shared.ka}]做事", "output_key": "agent_result"},
                },
            ],
            "edges": [{"source": "a", "target": "g"}],
        }
    )
    fake_agent = _FakeAgentEngine(text="自主产出")
    engine = WorkflowEngine(
        wf=wf, llm=_FakeLLM(reply="原料"), agent_engine_factory=lambda: fake_agent
    )
    ctx = _ctx()
    events = _run(engine, ctx)
    check(engine.result.status == "completed", "agent 节点成功：整体 completed")
    check(ctx.shared.get("agent_result") == "自主产出", "Agent 产出回写 shared（唯一数据通道）")
    check(fake_agent.goals and "原料" in fake_agent.goals[0], "Agent 子目标渲染了上游 shared 产物")
    tok = [e for e in events if e.event_type == "token"]
    check(
        bool(tok) and tok[0].mode == "agent" and tok[0].node_id == "g",
        "Agent token 事件带模式与节点归属",
    )

    bad_agent = _FakeAgentEngine(ok=False)
    engine_b = WorkflowEngine(
        wf=wf, llm=_FakeLLM(reply="原料"), agent_engine_factory=lambda: bad_agent
    )
    ctx_b = _ctx()
    _run(engine_b, ctx_b)
    check(
        engine_b.result.status == "failed" and engine_b.result.signal == "node_failed",
        "agent 节点失败：按节点失败上报（无 on_fail 时交给切换判定）",
    )

    # --- requires_agent：让出 → Agent 接管 → 断点续跑 ---
    wf2 = _wf(
        {
            "id": "t_req",
            "entry": "s",
            "nodes": [
                {"id": "s", "type": "llm", "config": {"output_key": "ks"}},
                {"id": "w", "type": "llm", "requires_agent": True, "config": {"output_key": "kw"}},
                {"id": "e", "type": "llm", "config": {"output_key": "ke"}},
            ],
            "edges": [{"source": "s", "target": "w"}, {"source": "w", "target": "e"}],
        }
    )
    # 第一段：跑到 w 主动让出
    eng1 = WorkflowEngine(wf=wf2, llm=_FakeLLM(reply="第一段"))
    ctx2 = _ctx()
    ev1 = _run(eng1, ctx2)
    r1 = eng1.result
    check(
        r1.status == "switched" and r1.signal == "requires_agent" and r1.node_id == "w",
        "跑到 requires_agent 节点主动让出（switched + requires_agent）",
    )
    check(r1.output_key == "kw", "让出时带上节点声明的 output_key（Agent 产出落点）")
    check(ctx2.workflow_state.get("next_node") == "w", "断点锚在 w（续跑依据）")
    enters1 = [e.node_id for e in ev1 if e.event_type == "node_enter"]
    check(enters1 == ["s"], "w 未以普通节点执行（enter 只有 s）")

    # 模拟编排器：Agent 接管完成，回填产出与 handled 标记
    ctx2.shared["kw"] = "Agent 自主完成"
    ctx2.workflow_state["agent_handled"] = "w"

    # 第二段：新引擎实例从断点续跑，跳过 w 直接路由到 e
    eng2 = WorkflowEngine(wf=wf2, llm=_FakeLLM(reply="收尾"))
    ev2 = _run(eng2, ctx2)
    r2 = eng2.result
    check(r2.status == "completed", "收编回归后跑到终点 completed")
    enters2 = [e.node_id for e in ev2 if e.event_type == "node_enter"]
    check(enters2 == ["e"], "续跑跳过 w（不重跑），只执行 e")
    check(ctx2.shared.get("ke") == "收尾", "e 节点正常产出")
    check("agent_handled" not in ctx2.workflow_state, "handled 标记一次性消费")


def main() -> int:
    print("=" * 60)
    print("工作流引擎回归（分支 / 超时 / 重试 / 失败跳转 / Agent 融合）")
    print("=" * 60)
    test_1_validate_and_template()
    test_2_linear_flow()
    test_3_condition_branch()
    test_4_timeout()
    test_5_retry()
    test_6_on_fail()
    test_7_agent_node_and_requires_agent()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for m in FAILED:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
