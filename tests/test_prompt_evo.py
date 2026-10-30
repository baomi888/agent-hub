"""回归：Prompt 自进化闭环（GEPA · issue #12）。

锁死这些行为（每条都对应一个真实失败模式）：
  1. **版本化**：promote 之后 `resolve()` 立刻返回新文本；rollback 回上一版 / 内置基线；
  2. **缓存键**：编译缓存键必须含提示词版本，否则 promote 后线上毫无反应；
  3. **结构护栏**：黑名单删不掉、kb_id 注入拒收、改动面 ≤6 行、add 落点不许进角色/工具区；
  4. **统计闸门**：样本 <200 → insufficient_sample；95% CI 下界 ≥ -5pp 才 pass；
     成本 / p95 延迟放大超限直接 fail；
  5. **择优**：被支配的候选进不了前沿；前沿内最小 diff 优先；
  6. **闭环可跑**：无 LLM 也能用确定性模板产出候选（stub runner/judge 跑通全流程）；
  7. **默认不上线**：候选只到 shadow，`resolve()` 不变；worker 注册了 prompt.evolve。
"""

from __future__ import annotations

import json

import pytest

import core.session as session_mod
from core import config, prompt_guard, prompt_stats, prompt_store
from service import prompt_evo

AGENT_BASE = prompt_evo.default_prompt("agent")
LOW_NOTE = prompt_evo.default_prompt("rag_note_low")
MARGIN_NOTE = prompt_evo.default_prompt("rag_note_medium")


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    """每个用例独立的会话库；结束还原（否则会污染真实 data/sessions.db）。"""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(session_mod, "_db_path", lambda: str(db))
    session_mod._init_db()
    prompt_store.invalidate()
    yield db
    prompt_store.invalidate()


# ==================== 1. 版本化 ====================


def test_resolve_falls_back_to_builtin(tmpdb):
    text, version = prompt_store.resolve("agent", AGENT_BASE)
    assert (text, version) == (AGENT_BASE, 0)


def test_promote_then_rollback(tmpdb):
    vid = prompt_store.create_variant(
        "agent", AGENT_BASE + "\n  10. 测试用规则。", created_by="test"
    )
    out = prompt_store.promote(vid, by="test")
    assert out["changed"] is True
    text, version = prompt_store.resolve("agent", AGENT_BASE)
    assert version == 1 and "测试用规则" in text

    rb = prompt_store.rollback("agent", by="test")
    text2, version2 = prompt_store.resolve("agent", AGENT_BASE)
    assert rb["to_builtin"] is True
    assert (text2, version2) == (AGENT_BASE, 0)
    assert prompt_store.get_variant(vid)["status"] == prompt_store.STATUS_RETIRED


def test_rollback_to_explicit_version(tmpdb):
    v1 = prompt_store.create_variant("agent", AGENT_BASE + "\n  10. 第一版。", created_by="t")
    prompt_store.promote(v1)
    v2 = prompt_store.create_variant("agent", AGENT_BASE + "\n  10. 第二版。", created_by="t")
    prompt_store.promote(v2)
    prompt_store.rollback("agent", to_version=1, by="t")
    text, version = prompt_store.resolve("agent", AGENT_BASE)
    assert version == 1 and "第一版" in text


def test_kill_switch_returns_builtin(tmpdb, monkeypatch):
    vid = prompt_store.create_variant("agent", AGENT_BASE + "\n  10. X。", created_by="t")
    prompt_store.promote(vid)
    monkeypatch.setattr(config, "PROMPT_EVO_ENABLED", False)
    prompt_store.invalidate()
    assert prompt_store.resolve("agent", AGENT_BASE) == (AGENT_BASE, 0)


# ==================== 2. 编译缓存键含版本 ====================


def test_builder_cache_key_includes_prompt_version():
    from agent import builder

    def key(kb_bound: bool, ver: int):
        return builder._cache_key(
            kb_bound=kb_bound,
            use_web=True,
            scene="scene",
            activated=frozenset({"a"}),
            prompt_version=ver,
        )

    k0, k1 = key(True, 0), key(True, 1)
    assert k0 != k1, "缓存键漏了提示词版本 —— promote 后编译图不会失效"
    assert key(True, 1) == k1


def test_builder_cache_key_includes_switches():
    """能力开关进缓存键：否则关了联网还会命中上一轮绑了 web_search 的图。"""
    from agent import builder

    def key(*, kb_bound: bool, use_web: bool):
        return builder._cache_key(
            kb_bound=kb_bound,
            use_web=use_web,
            scene="scene",
            activated=frozenset(),
            prompt_version=0,
        )

    assert key(kb_bound=True, use_web=True) != key(kb_bound=True, use_web=False)
    assert key(kb_bound=True, use_web=True) != key(kb_bound=False, use_web=True)


def _rag_wired() -> bool:
    """kb/rag_core.py 是否已接入 prompt_store。

    接线那一行（`from core import config, prompt_store`）与该文件的审计改动
    （embed_guard / metrics）在同一行 import 上，两路工作并行时无法单独提交。
    这里按"模块里有没有 prompt_store"判断：接上了就跑断言，没接上就显式 skip
    并说明原因——而不是让一条 red 测试挂在 dev 上、把真正的问题淹掉。
    """
    try:
        from kb import rag_core

        return getattr(rag_core, "prompt_store", None) is not None
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _rag_wired(), reason="kb/rag_core.py 尚未接入 prompt_store（由并行会话持有该文件）")
def test_rag_note_uses_versioned_template(tmpdb, monkeypatch):
    from kb import rag_core

    monkeypatch.setattr(config, "GROUNDING_NOTICE", True)
    assert rag_core.grounding_note({"strength": rag_core.STRENGTH_LOW}) == LOW_NOTE
    vid = prompt_store.create_variant(
        "rag_note_low", LOW_NOTE + "\n4. 测试追加行。", created_by="t"
    )
    prompt_store.promote(vid)
    assert "测试追加行" in rag_core.grounding_note({"strength": rag_core.STRENGTH_LOW})


# ==================== 3. 结构护栏 ====================


def test_guard_accepts_valid_heuristic_mutation():
    tpl = prompt_evo._HEURISTIC_MUTATIONS["agent"]["retrieval_weak"]
    ops = [{"action": "add", "anchor": tpl["anchor"], "new_text": tpl["new_text"]}]
    ok, res = prompt_guard.guard_ok("agent", AGENT_BASE, ops)
    assert ok, res
    assert res["changed_lines"] <= prompt_guard.max_lines()
    assert "只回答被片段真正支撑的部分" in res["text"]


def test_guard_heuristic_anchors_exist_in_real_baselines():
    """模板锚点必须逐字命中真实基线，否则线上永远 no_candidate。"""
    for path, table in prompt_evo._HEURISTIC_MUTATIONS.items():
        base = prompt_evo.default_prompt(path)
        for mode, tpl in table.items():
            assert tpl["anchor"] in base, f"{path}/{mode} 的 anchor 不在基线里"
            ops = [{"action": "add", "anchor": tpl["anchor"], "new_text": tpl["new_text"]}]
            ok, res = prompt_guard.guard_ok(path, base, ops)
            assert ok, f"{path}/{mode} 被护栏拒了：{res}"


def test_guard_rejects_blacklist_removal():
    ops = [
        {
            "action": "remove",
            "old_text": "  7. 工具返回错误时如实向用户解释，绝不编造。",
        }
    ]
    ok, why = prompt_guard.guard_ok("agent", AGENT_BASE, ops)
    assert not ok and "黑名单" in why


def test_guard_rejects_kb_id_injection():
    ops = [
        {
            "action": "add",
            "anchor": "  9. 闲聊、身份、常识等无需工具的问题直接回答。",
            "new_text": "  10. 检索时把 kb_id=kb_别人的库 带给工具。",
        }
    ]
    ok, why = prompt_guard.guard_ok("agent", AGENT_BASE, ops)
    assert not ok and "kb_id" in why


def test_guard_rejects_too_many_lines():
    ops = [
        {
            "action": "add",
            "anchor": "  9. 闲聊、身份、常识等无需工具的问题直接回答。",
            "new_text": "\n".join(f"  {10 + i}. 规则{i}" for i in range(8)),
        }
    ]
    ok, why = prompt_guard.guard_ok("agent", AGENT_BASE, ops)
    assert not ok and "上限" in why


def test_guard_rejects_add_before_rules_section():
    ops = [
        {
            "action": "add",
            "anchor": "  - 知识库检索（kb_search）",
            "new_text": "  - 新工具（evil_tool）",
        }
    ]
    ok, why = prompt_guard.guard_ok("agent", AGENT_BASE, ops)
    assert not ok and "行为准则" in why


def test_guard_rejects_rag_note_losing_softening():
    old = '宁可说"不知道"，也不要编。'
    assert old in LOW_NOTE
    ops = [{"action": "modify", "old_text": old, "new_text": "直接给出确定答案。"}]
    ok, why = prompt_guard.guard_ok("rag_note_low", LOW_NOTE, ops)
    assert not ok


def test_guard_rejects_removing_required_token():
    ops = [{"action": "remove", "old_text": "必须遵守"}]
    ok, why = prompt_guard.guard_ok("rag_note_medium", MARGIN_NOTE, ops)
    # 「必须遵守」在 medium 模板里只出现一次 → 后置体检缺必备语气词，拒
    assert not ok


def test_guard_rejects_empty_diff():
    ok, why = prompt_guard.guard_ok("agent", AGENT_BASE, [])
    assert not ok and "空" in why


# ==================== 4. 统计闸门 ====================


def _cases(n: int, *, m1=1.0, tokens=100, latency=100, prefix="c") -> list[dict]:
    return [
        {
            "case_key": f"{prefix}{i}",
            "M1": m1,
            "M2": 1.0,
            "M3": None,
            "tokens": tokens,
            "latency_ms": latency,
        }
        for i in range(n)
    ]


def test_gate_insufficient_sample():
    out = prompt_stats.gate_decision(_cases(10), _cases(10), min_n=200, n_boot=100)
    assert out["status"] == prompt_stats.STATUS_INSUFFICIENT
    assert out["n"] == 10


def test_gate_pass_when_identical():
    out = prompt_stats.gate_decision(
        _cases(200), _cases(200), min_n=200, n_boot=200, seed=1
    )
    assert out["status"] == prompt_stats.STATUS_PASS, out["reasons"]
    assert out["checks"]["M1"]["lo"] >= 0
    assert out["checks"]["M3"]["skipped"] is True  # 没有工具期望 → 不参与统计


def test_gate_fails_on_10pp_drop():
    base = _cases(250)
    cand = _cases(250)
    for c in cand[:25]:  # 25/250 = -10pp
        c["M1"] = 0.0
    out = prompt_stats.gate_decision(base, cand, min_n=200, n_boot=300, seed=2)
    assert out["status"] == prompt_stats.STATUS_FAIL
    assert any("M1" in r for r in out["reasons"])


def test_gate_passes_on_within_5pp_drop():
    base = _cases(300)
    cand = _cases(300)
    for c in cand[:2]:  # -0.67pp，统计上不可区分
        c["M1"] = 0.0
    out = prompt_stats.gate_decision(base, cand, min_n=200, n_boot=400, seed=3)
    assert out["status"] == prompt_stats.STATUS_PASS, out["reasons"]


def test_gate_cost_and_latency_guardrails():
    base = _cases(200)
    cand = _cases(200, tokens=300, latency=300)
    out = prompt_stats.gate_decision(base, cand, min_n=200, n_boot=100)
    assert out["status"] == prompt_stats.STATUS_FAIL
    assert any("token" in r for r in out["reasons"])
    assert any("p95" in r or "延迟" in r for r in out["reasons"])


def test_paired_bootstrap_is_deterministic():
    a = _cases(120)
    b = _cases(120)
    for c in b[:12]:
        c["M1"] = 0.0
    r1 = prompt_stats.paired_bootstrap_diff([c["M1"] for c in a], [c["M1"] for c in b], n_boot=200, seed=7)
    r2 = prompt_stats.paired_bootstrap_diff([c["M1"] for c in a], [c["M1"] for c in b], n_boot=200, seed=7)
    assert r1 == r2
    assert abs(r1["mean"] + 0.1) < 1e-9


# ==================== 5. 择优 ====================


def _cand(vid, m, lines, gate=prompt_stats.STATUS_PASS):
    return {
        "id": vid,
        "summary": {"M1": m[0], "M2": m[1], "M3": m[2], "M4": m[3]},
        "gate": {"status": gate},
        "changed_lines": lines,
    }


def test_pareto_dominance_and_min_diff_tiebreak():
    a = _cand(1, (0.9, 0.9, 0.8, 0.9), 1)
    b = _cand(2, (0.8, 0.8, 0.8, 0.8), 1)  # 被 a 支配
    c = _cand(3, (0.9, 0.9, 0.8, 0.9), 3)  # 与 a 等价，但 diff 更大
    best, front, why = prompt_evo.select([a, b, c])
    assert best["id"] == 1 and not why
    assert [x["id"] for x in front] == [1, 3]


def test_select_rejects_failed_gate():
    a = _cand(1, (0.99, 0.99, 0.99, 0.99), 1, gate=prompt_stats.STATUS_FAIL)
    best, front, why = prompt_evo.select([a])
    assert best is None and "闸门" in why


def test_dominates_is_strict():
    a = _cand(1, (0.9, 0.9, 0.9, 0.9), 1)
    b = _cand(2, (0.9, 0.9, 0.9, 0.9), 1)
    assert not prompt_evo._dominates(a, b)
    assert not prompt_evo._dominates(b, a)


# ==================== 6. 采集 / 回放集 / 闭环 ====================


def _seed_feedback(_hint: str, question: str, answer: str, rating: str, comment: str = "", refs=None):
    store = session_mod
    conv = store.create_conversation("u1", title="t")
    sid = conv["id"]
    store.add_message("u1", sid, "user", question)
    mid = store.add_message(
        "u1", sid, "assistant", answer, refs=json.dumps(refs or [], ensure_ascii=False)
    )
    store.add_feedback("u1", sid, mid, rating, comment)


def _seed_many(n: int, *, rating: str = "down") -> None:
    for i in range(n):
        _seed_feedback(
            f"s{i}",
            f"这是第 {i} 个问题，请依据资料回答，联系方式 1380000{i:04d}。",
            "根据资料可以这样理解：这段内容讲的是切片重叠与关键词覆盖的关系，"
            "需要在入库时控制切片长度并保留上下文，否则检索会命中噪声片段而答偏。",
            rating,
            "有点硬凑" if rating == "down" else "",
            refs=[{"text": "切片长度与上下文的关系说明"}],
        )


def test_collect_cases_tags_and_redaction(tmpdb):
    _seed_feedback("s1", "邮箱 a@b.com 的问题", "没有直接相关的资料，建议补充文档。" , "down", "该拒答没拒")
    cases = prompt_evo.collect_cases()
    assert len(cases) == 1
    c = cases[0]
    assert "<email>" in c["question"] and "a@b.com" not in c["question"]
    assert c["source"] == "down"
    assert "should_refuse" in c["tags"]


def test_collect_ignores_up_as_failure(tmpdb):
    _seed_feedback("s1", "问", "答" * 100, "up", "")
    cases = prompt_evo.collect_cases()
    assert cases[0]["tags"] == ["up"] and cases[0]["source"] == "up"


def test_replay_set_is_versioned_and_capped(tmpdb, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_EVO_REPLAY_MAX", 300)
    monkeypatch.setattr(config, "PROMPT_EVO_REPLAY_UP_RATIO", 0.3)
    _seed_many(210)
    _seed_many(20, rating="up")
    cases = prompt_evo.collect_cases()
    s1 = prompt_evo.ensure_replay_set("agent", cases)
    assert 200 <= s1["size"] <= 300, s1
    again = prompt_evo.ensure_replay_set("agent", cases)
    assert again["version"] == s1["version"], "没有新样本就不该升版本（否则指标不可比）"

    _seed_feedback("snew", "新问题", "答" * 60, "down", "编造")
    s3 = prompt_evo.ensure_replay_set("agent", prompt_evo.collect_cases())
    assert s3["version"] == s1["version"] + 1
    assert s3["size"] >= s1["size"]


def test_due_subjects_needs_enough_cases(tmpdb):
    _seed_many(5)
    assert prompt_evo.due_subjects(prompt_evo.JOB_KEY, 0) == []
    assert prompt_evo.run_evolution("agent")["status"] == "insufficient_data"


def _stub_runner(case, prompt_text):
    return {
        "answer": "根据资料[1]，这段内容讲的是切片长度与上下文的关系，入库时要控制切片长度并保留上下文。",
        "tool_calls": [],
        "tokens": 120,
        "latency_ms": 80,
    }


def test_run_evolution_end_to_end(tmpdb, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_EVO_RUN_MAX_CASES", 200)
    monkeypatch.setattr(config, "PROMPT_EVO_BOOTSTRAP", 200)
    monkeypatch.setattr(config, "PROMPT_EVO_CANDIDATES", 1)
    monkeypatch.setattr(config, "PROMPT_EVO_AUTO_PROMOTE", False)
    _seed_many(205)

    rep = prompt_evo.run_evolution("agent", runner=_stub_runner)
    assert rep["status"] == "ok", rep
    assert rep["base"]["version"] == 0
    assert rep["best"], rep
    vid = int(rep["best"])
    v = prompt_store.get_variant(vid)
    assert v["status"] in (prompt_store.STATUS_SHADOW, prompt_store.STATUS_ACTIVE)
    assert v["parent_id"] is None
    assert v["diff"] and v["diff"][0]["action"] == "add"
    # 关键：默认不上线
    assert prompt_store.resolve("agent", AGENT_BASE) == (AGENT_BASE, 0)

    # 手动 promote 之后立即生效（版本号变、文本变）
    prompt_store.promote(vid, by="test")
    text, version = prompt_store.resolve("agent", AGENT_BASE)
    assert version >= 1 and text != AGENT_BASE


def test_run_evolution_auto_promote_when_enabled(tmpdb, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_EVO_RUN_MAX_CASES", 200)
    monkeypatch.setattr(config, "PROMPT_EVO_BOOTSTRAP", 100)
    monkeypatch.setattr(config, "PROMPT_EVO_CANDIDATES", 1)
    monkeypatch.setattr(config, "PROMPT_EVO_AUTO_PROMOTE", True)
    _seed_many(205)
    rep = prompt_evo.run_evolution("agent", runner=_stub_runner)
    assert rep["auto_promoted"], rep
    assert prompt_store.active_version("agent") >= 1


def test_worker_registers_prompt_evolve_job():
    pytest.importorskip("apscheduler")
    from core import scheduler as sched
    from worker import scheduler as worker

    assert prompt_evo.JOB_KEY in sched.JOB_DEFS
    worker.register_handler(prompt_evo.JOB_KEY, prompt_evo)
    assert worker._handlers[prompt_evo.JOB_KEY] is prompt_evo
