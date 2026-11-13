"""知识库浏览量（独立访客）。

口径是"多少人看过"，不是"被打开多少次"：
  - 同一个人重复打开只算 1；
  - 两个人打开算 2；
  - 换库要分开算。

这三条只要有任意一条写错，广场上"最多人用的库"就会变成一个
"谁刷新得勤谁第一"的排行榜，而排序一旦不可信，整个指标就没意义了。
"""

from __future__ import annotations

import pytest

from core import session, kb_views


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库（AGENT_HUB_DB 缝的同一套做法），绝不碰真实 data/sessions.db。"""
    path = str(tmp_path / "views.db")
    assert "views.db" in path
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    kb_views._READY.clear()
    yield path
    kb_views._READY.clear()


def test_same_viewer_counts_once(env):
    for _ in range(5):
        kb_views.touch("kb_a", "u1")
    assert kb_views.count("kb_a") == 1


def test_two_viewers_count_two(env):
    kb_views.touch("kb_a", "u1")
    kb_views.touch("kb_a", "u2")
    assert kb_views.count("kb_a") == 2


def test_counts_are_per_kb(env):
    kb_views.touch("kb_a", "u1")
    kb_views.touch("kb_b", "u1")
    kb_views.touch("kb_b", "u2")
    assert kb_views.count("kb_a") == 1
    assert kb_views.count("kb_b") == 2
    assert kb_views.counts() == {"kb_a": 1, "kb_b": 2}


def test_unknown_kb_is_zero(env):
    """从没被打开过的库要回 0，而不是抛 KeyError（广场上新库都是这种）。"""
    assert kb_views.count("never_seen") == 0
    assert kb_views.counts().get("never_seen") is None


def test_blank_ids_are_ignored(env):
    """空 uid 不该写进表：一条没有主体的浏览记录算谁的？"""
    kb_views.touch("kb_a", "")
    kb_views.touch("", "u1")
    assert kb_views.count("kb_a") == 0
    assert kb_views.counts() == {}


def test_purge_clears_kb_views(env):
    kb_views.touch("kb_a", "u1")
    kb_views.touch("kb_a", "u2")
    assert kb_views.purge_kb("kb_a") == 2
    assert kb_views.count("kb_a") == 0
