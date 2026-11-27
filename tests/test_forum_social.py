"""论坛互动体系（2026-11-23）：点赞 / 收藏 / 关注 / 通知 / @。

这里锁的是数据层（core/forum_social）的行为：
  1. 点赞/收藏/关注都是 toggle，且复合主键保证"一个人对一条帖只算一次"；
  2. 自己不能关注自己、自己触发的不产生通知；
  3. 通知的未读计数、标已读、清理（删号/删帖）；
  4. @ 解析：登录用户正文里 @ 到真实用户 → mention 通知；匿名/查不到 → 跳过。
"""

from __future__ import annotations

from api.forum import _MENTION_RE
from core import forum_social


def _reset() -> None:
    """清空互动表，隔离用例。先 _ensure 建表，否则第一次跑会 no such table。"""
    from core import session as store

    forum_social._ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        for t in ("forum_likes", "forum_favorites", "user_follows", "notifications"):
            conn.execute(f"DELETE FROM {t}")


def test_like_is_toggle_and_unique():
    _reset()
    assert forum_social.like(1, "u1") == {"liked": True, "count": 1}
    assert forum_social.like(1, "u1") == {"liked": False, "count": 0}
    # 第二个人赞：count 独立累计
    forum_social.like(1, "u2")
    assert forum_social.like_state(1, "u1") == {"liked": False, "count": 1}


def test_favorite_is_toggle():
    _reset()
    assert forum_social.favorite(2, "u1")["favorited"] is True
    assert forum_social.favorite(2, "u1")["favorited"] is False
    assert forum_social.list_favorites("u1") == []


def test_follow_rejects_self():
    _reset()
    try:
        forum_social.follow("u1", "u1")
        raise AssertionError("自己关注自己应当抛 ValueError")
    except ValueError as e:
        assert "自己" in str(e)


def test_follow_toggle_and_follower_count():
    _reset()
    assert forum_social.follow("u1", "u2")["following"] is True
    assert forum_social.follow("u3", "u2")["count"] == 2  # u2 有两个粉丝
    assert forum_social.follow("u1", "u2")["following"] is False
    assert forum_social.follow_state("u1", "u2")["following"] is False
    assert forum_social.follow_state("u1", "u2")["count"] == 1


def test_notify_skips_self_and_invalid_kind():
    _reset()
    forum_social.notify("u1", "like", "u1")  # 自己赞自己：不落
    forum_social.notify("u2", "like", "u1", 1)
    data = forum_social.list_notifications("u1")
    assert data["items"] == []  # 自己触发的不落给自己
    assert data["unread"] == 0
    # 非法 kind
    try:
        forum_social.notify("u2", "bogus", "u1")
        raise AssertionError("非法 kind 应当抛 ValueError")
    except ValueError:
        pass


def test_notification_unread_and_mark_read():
    _reset()
    forum_social.notify("u2", "like", "u1", 1)
    forum_social.notify("u2", "mention", "u3", 1)
    assert forum_social.unread_count("u2") == 2
    forum_social.mark_read("u2")
    assert forum_social.unread_count("u2") == 0


def test_purge_user_and_post():
    _reset()
    forum_social.like(1, "u1")
    forum_social.favorite(1, "u1")
    forum_social.follow("u1", "u2")
    forum_social.notify("u2", "like", "u1", 1)
    forum_social.purge_user("u1")
    assert forum_social.like_state(1, "u1")["count"] == 0
    assert forum_social.list_favorites("u1") == []
    assert forum_social.follow_state("u1", "u2")["count"] == 0
    assert forum_social.list_notifications("u2")["unread"] == 0

    _reset()
    forum_social.like(1, "u1")
    forum_social.notify("u2", "like", "u1", 1)
    forum_social.purge_post(1)
    assert forum_social.like_state(1, "u1")["count"] == 0
    assert forum_social.list_notifications("u2")["items"] == []


def test_mention_regex():
    s = "@张三 说 @李四_123，还有 @abc 和@王五"
    assert _MENTION_RE.findall(s) == ["张三", "李四_123", "abc", "王五"]
    # 空正文 / 无 @
    assert _MENTION_RE.findall("没有提及") == []
    assert _MENTION_RE.findall("") == []
