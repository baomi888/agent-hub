"""P2（计划主题识别加固）+ P3（新建会话自动绑定资料库）回归测试。

背景（2026-10-16 线上复现的 14 张截图）：
  · 计划卡片退化成「目标定义 / 现状盘点 / 里程碑拆解」的通用模板，
    因为"计划再细一点"这类追问没有主语，关键词匹配必然落空；
  · 传过资料的库还在，新开一个会话模型却说"当前会话没绑定资料库"。

不联网、不依赖真实 LLM / Chroma：
  1. detect_plan_topic：按给定顺序命中、无关键词返回 None、空输入；
  2. _resolve_plan_topic：本轮命中则不查历史；本轮命不中时从历史（近→远）续上主题；
     回看条数上限、开关置 0 关闭、查库异常静默；
  3. _augment_plan：plan=False 原样返回；plan=True 注入头部 + 主题专属时间轴提示；
  4. api.sessions.create：唯一库自动绑、多库不绑、显式 kb_id 优先、开关关闭不绑、
     归属查询异常静默。

可从任意位置运行：pytest tests/test_plan_and_bind.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402
import core.session as session  # noqa: E402
import kb.pipelines as pipelines  # noqa: E402
from api.chat import (  # noqa: E402
    PLAN_TOPICS,
    _augment_plan,
    _resolve_plan_topic,
    detect_plan_topic,
)
from api.sessions import SessionCreate, create  # noqa: E402


def _patch_session_db(monkeypatch, tmp_path):
    """把会话库挪到临时文件并重建表，避免污染真实库。"""
    db = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(session, "_db_path", lambda: db)
    session._init_db()


# ==================== 1. detect_plan_topic（纯函数）====================


def test_detect_plan_topic_from_question():
    t = detect_plan_topic("帮我做一个六级备考计划")
    assert t is not None and t["name"] == "学习计划", t


def test_detect_plan_topic_none_without_keyword():
    # 这正是截图里的原话：没有主语，任何主题词都不命中
    assert detect_plan_topic("计划再细一点") is None


def test_detect_plan_topic_checks_texts_in_order():
    # 先本轮、后历史：本轮命不中才轮到历史续主题
    t = detect_plan_topic("计划再细一点", "我们国庆去云南自驾")
    assert t is not None and t["name"] == "旅行行程", t


def test_detect_plan_topic_skips_empty_inputs():
    assert detect_plan_topic("", None, "   ") is None


def test_detect_plan_topic_exam_names_without_intent_words():
    """只报成绩、不带"备考/学习"字样的说法也要命中学习计划。

    线上探针 2026-10-16 复现的缺口："六级"此前不在词表里，
    "我这次六级只考了473分" 一个词都命不中 → 主题续不上 → 退回通用 hint，
    计划里因此混出 DAY 1/DAY 2，前端卡片抬头也退回"结构化计划"。
    """
    for q in (
        "我这次六级只考了473分，挺受打击的。",
        "四级没过线，好烦",
        "四六级刚考完",
        "CET6 只考了 473",
        "期末还有两周就考了",
        "想考证，但不知道从哪开始",
    ):
        t = detect_plan_topic(q)
        assert t is not None, q
        assert t["name"] == "学习计划", q


def test_plan_topics_align_with_frontend():
    """服务端词表是权威，前端那份只用于给卡片打标签 —— 两边不能漂移。

    漂移的后果很隐蔽：后端算出了主题（进 prompt 的 hint 是对的），
    前端却认不出，卡片抬头仍旧显示通用的"结构化计划"。
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "components"
        / "chat"
        / "plan.tsx"
    )
    text = src.read_text(encoding="utf-8")
    for t in PLAN_TOPICS:
        assert 'name: "{}"'.format(t["name"]) in text, "前端缺主题：{}".format(t["name"])
    study = next(t for t in PLAN_TOPICS if t["name"] == "学习计划")
    missing = [k for k in study["keys"] if f'"{k}"' not in text]
    assert not missing, f"前端学习计划缺关键词：{missing}"


# ==================== 2. _resolve_plan_topic（含历史回看）====================


def test_resolve_plan_topic_hits_without_history(monkeypatch):
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("本轮已命中主题，不该再查历史")

    monkeypatch.setattr(session, "get_messages", boom)
    t = asyncio.run(_resolve_plan_topic("u1", "s1", "帮我列一份考研复习计划"))
    assert t is not None and t["name"] == "学习计划", t
    assert calls["n"] == 0


def test_resolve_plan_topic_from_history(monkeypatch):
    monkeypatch.setattr(config, "PLAN_TOPIC_HISTORY_MESSAGES", 6)
    rows = [
        {"role": "user", "content": "我想备考六级"},
        {"role": "assistant", "content": "先说你四级多少分"},
        {"role": "user", "content": "四级 473"},
    ]
    monkeypatch.setattr(session, "get_messages", lambda *_a, **_k: rows)
    t = asyncio.run(_resolve_plan_topic("u1", "s1", "计划能不能详细一点"))
    assert t is not None and t["name"] == "学习计划", t


def test_resolve_plan_topic_from_history_exam_score_only(monkeypatch):
    """截图场景原样复现：第一轮只报六级分数（不带意图词），第二轮无主语追问。"""
    monkeypatch.setattr(config, "PLAN_TOPIC_HISTORY_MESSAGES", 6)
    rows = [
        {"role": "user", "content": "我这次六级只考了473分，挺受打击的。"},
        {"role": "assistant", "content": "别灰心，先看看差在哪。"},
    ]
    monkeypatch.setattr(session, "get_messages", lambda *_a, **_k: rows)
    t = asyncio.run(_resolve_plan_topic("u1", "s1", "计划再细一点"))
    assert t is not None and t["name"] == "学习计划", t


def test_resolve_plan_topic_prefers_most_recent(monkeypatch):
    monkeypatch.setattr(config, "PLAN_TOPIC_HISTORY_MESSAGES", 6)
    rows = [
        {"role": "user", "content": "帮我规划一下云南自驾的路线"},  # 旧：旅行
        {"role": "user", "content": "算了，先把六级备考计划做出来"},  # 新：学习（更近）
    ]
    monkeypatch.setattr(session, "get_messages", lambda *_a, **_k: rows)
    t = asyncio.run(_resolve_plan_topic("u1", "s1", "先给我个每日安排"))
    assert t is not None and t["name"] == "学习计划", t


def test_resolve_plan_topic_respects_history_limit(monkeypatch):
    monkeypatch.setattr(config, "PLAN_TOPIC_HISTORY_MESSAGES", 1)
    rows = [
        {"role": "user", "content": "我想考研"},  # 更旧，被上限截掉
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "今晚吃什么"},  # 最近一条，命不中
    ]
    monkeypatch.setattr(session, "get_messages", lambda *_a, **_k: rows)
    assert asyncio.run(_resolve_plan_topic("u1", "s1", "列个计划")) is None


def test_resolve_plan_topic_disabled_when_zero(monkeypatch):
    monkeypatch.setattr(config, "PLAN_TOPIC_HISTORY_MESSAGES", 0)
    monkeypatch.setattr(
        session,
        "get_messages",
        lambda *_a, **_k: [{"role": "user", "content": "六级备考"}],
    )
    # 置 0 = 只看本轮，退回 P2 之前的行为
    assert asyncio.run(_resolve_plan_topic("u1", "s1", "计划再细一点")) is None


def test_resolve_plan_topic_db_error_is_silent(monkeypatch):
    monkeypatch.setattr(config, "PLAN_TOPIC_HISTORY_MESSAGES", 6)

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(session, "get_messages", boom)
    # 读历史失败不能让整轮请求挂掉：拿不到主题就当没主题
    assert asyncio.run(_resolve_plan_topic("u1", "s1", "计划再细一点")) is None


# ==================== 3. _augment_plan（提示词注入）====================


def test_augment_plan_uses_topic_hint():
    raw = "给我列一份计划"
    out = _augment_plan(raw, True, detect_plan_topic("考研复习计划"))
    assert out.startswith(raw)
    assert "【格式要求】" in out
    # 主题专属提示应替换掉默认提示（学习计划按周划分、阶段按科目编号）
    assert "第1周 ~ 第N周" in out
    assert "按科目或模块编号" in out


def test_augment_plan_no_topic_falls_back_to_default():
    out = _augment_plan("随便列个计划", True, None)
    assert "【格式要求】" in out
    assert "第1周 / 第N天 / DAY N" in out


# ==================== 4. 新建会话自动绑定资料库（P3）====================


def test_create_autobinds_single_kb(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTO_BIND_SINGLE_KB", True)
    monkeypatch.setattr(pipelines, "owned_kb_ids", lambda _uid: {"kb_solo"})
    conv = create(SessionCreate(), uid="u1")
    assert conv["kb_id"] == "kb_solo"


def test_create_no_autobind_when_multiple_kbs(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTO_BIND_SINGLE_KB", True)
    monkeypatch.setattr(pipelines, "owned_kb_ids", lambda _uid: {"a", "b"})
    # 多库时绑错比不绑更糟（会检索到不相干的段落），交给用户手动选
    assert create(SessionCreate(), uid="u1")["kb_id"] is None


def test_create_no_autobind_when_no_kb(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTO_BIND_SINGLE_KB", True)
    monkeypatch.setattr(pipelines, "owned_kb_ids", lambda _uid: set())
    assert create(SessionCreate(), uid="u1")["kb_id"] is None


def test_create_explicit_kb_wins(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTO_BIND_SINGLE_KB", True)
    monkeypatch.setattr(pipelines, "owned_kb_ids", lambda _uid: {"kb_solo"})
    conv = create(SessionCreate(kb_id="kb_explicit"), uid="u1")
    assert conv["kb_id"] == "kb_explicit"


def test_create_autobind_disabled(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTO_BIND_SINGLE_KB", False)
    monkeypatch.setattr(pipelines, "owned_kb_ids", lambda _uid: {"kb_solo"})
    assert create(SessionCreate(), uid="u1")["kb_id"] is None


def test_create_autobind_survives_lookup_error(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AUTO_BIND_SINGLE_KB", True)

    def boom(_uid):
        raise RuntimeError("owners.json gone")

    monkeypatch.setattr(pipelines, "owned_kb_ids", boom)
    # 归属表读不出来也不能拦住建会话
    assert create(SessionCreate(), uid="u1")["kb_id"] is None
