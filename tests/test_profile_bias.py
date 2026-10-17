"""方案 C · C2(画像聚合) 与 D(检索偏置) 离线自测（不联网、不碰真实数据）。

C2 块（worker 定时聚合用户画像）：
  - session.get_owners_with_tags / save_user_profile / get_user_profile：快照读写、owner 隔离
  - service.daily.aggregate_user_profile：聚合 Top N 标签 + 权重和，落 user_profiles
  - service.daily.due_subjects / run_one 对 "profile.aggregate" 的路由

D 块（检索按画像偏置）：
  - rc._bias_rerank：按 topics 与画像标签重合度减距离重排；distance 原始值还原
  - rc._apply_profile_bias：取 owner 画像并调用 _bias_rerank；owner 隔离
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

import core.session as session
import kb.rag_core as rc
import service.daily as daily

# ---------- fixtures ----------


def _patch_session_db(monkeypatch, tmp_path):
    """把会话库挪到临时文件并重建表，避免污染真实库。"""
    db = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(session, "_db_path", lambda: db)
    session._init_db()


# ==================== C2 块：快照读写 ====================


def test_save_and_get_user_profile(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    assert session.get_user_profile("u1") is None
    tags = [{"tag": "数学", "weight": 3}, {"tag": "考研", "weight": 2}]
    session.save_user_profile("u1", tags, 5.0, 2, 123456)
    p = session.get_user_profile("u1")
    assert p is not None
    assert p["tag_count"] == 2
    assert p["weight_sum"] == 5.0
    assert p["top_tags"] == tags
    # owner 隔离：另一个用户无快照
    assert session.get_user_profile("u2") is None


def test_get_owners_with_tags(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["数学"])
    session.add_user_tags("u2", ["英语"])
    # u3 没沉淀过标签，不应出现
    owners = session.get_owners_with_tags()
    assert set(owners) == {"u1", "u2"}


# ==================== C2 块：聚合逻辑 ====================


def test_aggregate_user_profile_saves_snapshot(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["数学", "考研", "英语"])
    session.add_user_tags("u1", ["数学"])  # 同一标签累加权重
    res = daily.aggregate_user_profile("u1", 999)
    assert res["status"] == "succeeded"
    assert res["tag_count"] == 3
    p = session.get_user_profile("u1")
    assert p is not None
    assert p["tag_count"] == 3
    weights = {t["tag"]: t["weight"] for t in p["top_tags"]}
    assert weights["数学"] == 2.0  # 累加生效


def test_daily_due_subjects_profile(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["数学"])
    subs = daily.due_subjects("profile.aggregate", 999)
    assert subs == ["u1"]
    # 非 profile 的 job_key 不在此分支（返回空）
    assert daily.due_subjects("unknown.key", 999) == []


def test_daily_run_one_profile(monkeypatch, tmp_path):
    _patch_session_db(monkeypatch, tmp_path)
    session.add_user_tags("u1", ["数学"])
    res = daily.run_one("profile.aggregate", "u1", 999)
    assert res.get("status") == "succeeded"
    assert session.get_user_profile("u1") is not None


# ==================== D 块：检索偏置 ====================


def test_bias_rerank_promotes_hit_and_keeps_distance():
    docs = [
        (Document(page_content="e", metadata={"topics": "|英语|"}), 0.40),
        (Document(page_content="m", metadata={"topics": "|数学|"}), 0.45),
    ]
    out = rc._bias_rerank(docs, {"数学"}, 0.08)
    # 数学命中：0.45 - 0.08 = 0.37 < 0.40 → 排第一
    assert out[0][0].metadata["topics"] == "|数学|"
    assert out[0][1] == 0.45  # 原始 distance 必须还原（相似度换算不被污染）
    assert out[1][0].metadata["topics"] == "|英语|"
    assert out[1][1] == 0.40


def test_bias_rerank_bidirectional_match():
    # 画像标签"数学"（粗）应命中文档主题"数学分析"（细）：双向包含
    docs = [
        (Document(page_content="a", metadata={"topics": "|数学分析|"}), 0.45),
        (Document(page_content="b", metadata={"topics": "|英语|"}), 0.40),
    ]
    out = rc._bias_rerank(docs, {"数学"}, 0.08)
    assert out[0][0].metadata["topics"] == "|数学分析|"
    assert out[0][1] == 0.45  # 还原


def test_bias_rerank_no_profile_keeps_order():
    docs = [
        (Document(page_content="x", metadata={"topics": "|英语|"}), 0.40),
        (Document(page_content="y", metadata={"topics": "|数学|"}), 0.45),
    ]
    out = rc._bias_rerank(docs, set(), 0.08)
    assert [d.metadata["topics"] for d, _ in out] == ["|英语|", "|数学|"]
    assert [d2 for _, d2 in out] == [0.40, 0.45]


def test_bias_rerank_alpha_scales_push():
    # 大 alpha 也只减距离、不越界为负（返回仍是原始 distance）
    docs = [(Document(page_content="z", metadata={"topics": "|数学|"}), 0.05)]
    out = rc._bias_rerank(docs, {"数学"}, 0.08)
    assert out[0][1] == 0.05  # 还原；新距离应为 0.0（max(0,...)）但不影响返回


def test_apply_profile_bias_owner_isolation(monkeypatch):
    docs = [
        (Document(page_content="x", metadata={"topics": "|英语|"}), 0.40),
        (Document(page_content="y", metadata={"topics": "|数学|"}), 0.45),
    ]

    def fake_tags(owner, limit=30):
        return [{"tag": "数学", "weight": 5}] if owner == "u1" else []

    monkeypatch.setattr(session, "get_user_tags", fake_tags)
    out1 = rc._apply_profile_bias(docs, "u1")
    assert out1[0][0].metadata["topics"] == "|数学|"  # 有数学画像 → 数学排前
    out2 = rc._apply_profile_bias(docs, "u2")
    assert out2[0][0].metadata["topics"] == "|英语|"  # 无画像 → 原顺序


def test_apply_profile_bias_via_retrieve_signature():
    # retrieve_with_score 已接受 owner 形参（签名兼容），不传时行为不变
    import inspect

    sig = inspect.signature(rc.RagPipeline.retrieve_with_score)
    assert "owner" in sig.parameters
