"""05 每日总结 + 委托 的核心测试（假 LLM，不依赖真实 API Key）。

覆盖 issues/05-daily-summary.md 验收：
  1. 假 LLM 下 summarize_day 结构完整；status 流转 pending→generating→ready
  2. collect_stats 的数字与直接 SQL 查询一致
  3. 溯源强制：source_refs 指向不存在 message 的结论被丢弃
  + 空日不调模型、plan_today 截断

DB 隔离：monkeypatch core.session._db_path 到临时目录，导入前生效。
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

import core.session as session

_TMP = tempfile.mkdtemp(prefix="daily_test_")


def _tmp_db() -> str:
    return os.path.join(_TMP, "sessions.db")


# 必须在 import service 之前把 DB 指到临时目录
session._db_path = _tmp_db  # type: ignore[assignment]
session._init_db()  # 在临时库建会话/消息表

from service import daily  # noqa: E402

daily._ensure_daily_tables()  # noqa: E402

from langchain_core.messages import AIMessage  # noqa: E402


class FakeLLM:
    """返回固定文本的假 LLM；invoke 返回带 .content 的对象。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.text)


UID = "u_test"
# 用「今天」做种子日期，add_message 的 created_at=now 才会落进当天
DATE = time.strftime("%Y-%m-%d")


@pytest.fixture
def seeded():
    with session._get_conn() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM daily_reports")
    c1 = session.create_conversation(UID, "c1")
    c2 = session.create_conversation(UID, "c2")
    m1 = session.add_message(UID, c1["id"], "user", "今天在调 nginx 中文路径")
    m2 = session.add_message(UID, c1["id"], "assistant", "建议复制到英文路径")
    m3 = session.add_message(UID, c1["id"], "user", "好，复制到 C:/nginx-sky")
    m4 = session.add_message(UID, c2["id"], "user", "复习了蒸馏检索")
    m5 = session.add_message(UID, c2["id"], "assistant", "两层检索：文档层+结论层")
    m6 = session.add_message(UID, c2["id"], "user", "明天写申报书")
    return {"c1": c1["id"], "c2": c2["id"], "ids": [m1, m2, m3, m4, m5, m6]}


def test_collect_stats_matches_sql(seeded):
    stats = daily.collect_stats(UID, DATE)
    assert stats["message_count"] == 6
    assert stats["conversation_count"] == 2
    assert stats["user_msg_count"] == 4
    assert stats["assistant_msg_count"] == 2
    # 与直接 SQL 交叉验证
    with session._get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM messages m JOIN conversations c "
            "ON m.conversation_id = c.id WHERE c.owner_id = ?",
            (UID,),
        ).fetchone()["c"]
    assert n == 6


def test_summarize_structure_and_traceability(seeded):
    ids = seeded["ids"]
    good = ids[1]  # 一条真实存在的消息 id
    fake = 999999  # 不存在的消息 id
    llm_text = json.dumps(
        [
            {"text": "用户今天在解决 nginx 中文路径问题", "source_refs": [good]},
            {"text": "用户决定改用英文路径副本", "source_refs": [fake]},
            {"text": "这条没有来源", "source_refs": []},
        ],
        ensure_ascii=False,
    )
    fake_llm = FakeLLM(llm_text)
    rep = daily.summarize_day(UID, DATE, llm=fake_llm)

    assert rep.status == "ready"
    assert fake_llm.calls == 1  # 确实调了一次模型
    assert len(rep.content) == 1  # 只有可溯源的那条被保留
    assert rep.content[0]["source_refs"] == [good]

    # 落库后能被取回
    got = daily.get_report(UID, DATE)
    assert got is not None
    assert got.status == "ready"
    assert len(got.content) == 1


def test_empty_day_no_llm_call():
    fake_llm = FakeLLM("SHOULD_NOT_BE_USED")
    rep = daily.summarize_day("u_empty", "2000-01-01", llm=fake_llm)
    assert rep.status == "empty"
    assert rep.content == []
    assert fake_llm.calls == 0  # 空日不应烧 token


def test_plan_today_truncates():
    deleg = {"prompt": "提醒我写申报书", "tone": "温和", "max_chars": 20}
    out = daily.plan_today(
        deleg,
        context="昨天定了两层检索方案",
        llm=FakeLLM("请记得今天写申报书技术方案，这段会超过二十个字用来测试截断逻辑是否生效"),
    )
    assert len(out) <= 20


def test_delegation_crud():
    d = daily.save_delegation(UID, "晨间提醒", prompt="列今日三件事", tone="轻快", max_chars=120)
    assert d["delegation_id"] == daily._delegation_id(UID, "晨间提醒")
    assert d["enabled"] == 1
    got = daily.get_delegation(UID, d["delegation_id"])
    assert got is not None and got["prompt"] == "列今日三件事"
    assert len(daily.list_delegations(UID)) >= 1
    assert daily.set_enabled(UID, d["delegation_id"], False) is True
    assert daily.get_delegation(UID, d["delegation_id"])["enabled"] == 0
