from __future__ import annotations

"""论坛互动体系（点赞 / 收藏 / 关注 / 通知 / @）的存储层。

与 core/forum.py 的分工：forum 管"帖子与板块"，这里管"人跟帖、人跟人的关系"。

身份口径（2026-11-23 定）：
  - **读**（看点赞数、看有没有收藏）免登录，走调用方传进来的 uid（可能是匿名）；
  - **写**（点赞、收藏、关注、@）必须登录 —— 由 api 层用 get_current_user 挡，
    这里只接收一个已经确认是真账号的 uid。
  - 所以表里的 `uid` 一定来自登录用户；帖子的 `post_id` 可能是匿名帖，
    这没关系 —— 看的人是登录的、发的人是匿名的，两者不冲突。

为什么单独成表而不是塞进 kb_posts：
  - 点赞/收藏是"人对帖的多对多"，塞进 kb_posts 要加列再拆 JSON，查"我收藏的"
    要扫全表反序列化，比独立表慢一个量级；
  - 关注是"人对人"，跟帖子无关，本来就没地方放；
  - 通知是"事件流"，要能追加、能标已读、能清理，结构完全不同。

命名与 forum.py / kb_board.py 一致：函数里 `_ensure()` 幂等建表，
不放进 session._init_db 的大清单（那是全站表，动它要连带考虑老库迁移）。
"""

import time

from core import session as store


def _now() -> int:
    return int(time.time())


# ============================================================
# 建表（幂等）
# ============================================================

def _ensure() -> None:
    with store._get_conn() as conn:  # noqa: SLF001
        conn.executescript(
            """
            -- 点赞：一个人对一条帖一条。主键复合，天然去重，也天然只算一次。
            -- 只对顶层帖（root_id=0 的主题）点赞，回复不单独点赞 ——
            -- 论坛的互动粒度是"这个话题"，不是"这一层楼"。
            CREATE TABLE IF NOT EXISTS forum_likes (
                post_id INTEGER NOT NULL,
                uid     TEXT NOT NULL,
                at      INTEGER NOT NULL,
                PRIMARY KEY (post_id, uid)
            );
            CREATE INDEX IF NOT EXISTS idx_flike_uid ON forum_likes(uid, at DESC);

            -- 收藏：一个人对一条帖一条。语义与点赞不同（收藏是"回头再看"的
            -- 清单，点一下不算收藏），所以分表，不能复用同一张表加 kind。
            CREATE TABLE IF NOT EXISTS forum_favorites (
                post_id INTEGER NOT NULL,
                uid     TEXT NOT NULL,
                at      INTEGER NOT NULL,
                PRIMARY KEY (post_id, uid)
            );
            CREATE INDEX IF NOT EXISTS idx_ffav_uid ON forum_favorites(uid, at DESC);

            -- 关注话题：与关注作者分开，用户可以只跟进一条求助帖。
            CREATE TABLE IF NOT EXISTS forum_topic_follows (
                post_id INTEGER NOT NULL,
                uid     TEXT NOT NULL,
                at      INTEGER NOT NULL,
                PRIMARY KEY (post_id, uid)
            );
            CREATE INDEX IF NOT EXISTS idx_ftfollow_uid ON forum_topic_follows(uid, at DESC);

            -- 关注：A 关注 B。follower=发起者，followee=被关注的人。
            -- 被关注者可能是匿名（anon:xxx）吗？匿名没有稳定身份，关注它
            -- 换设备就断了，所以这里只允许关注登录用户；匿名帖的作者
            -- 不提供"关注"入口（见 api 层）。
            CREATE TABLE IF NOT EXISTS user_follows (
                follower TEXT NOT NULL,
                followee TEXT NOT NULL,
                at       INTEGER NOT NULL,
                PRIMARY KEY (follower, followee)
            );
            CREATE INDEX IF NOT EXISTS idx_ufollow_followee
                ON user_follows(followee, at DESC);

            -- 通知：一条事件一行。kind 枚举：
            --   like    有人赞了我的帖
            --   favorite 有人收藏了我的帖
            --   follow  有人关注了我
            --   mention 有人 @ 了我
            --   reply   有人回复了我的帖（楼层）
            -- actor 是触发者（登录 uid），target_uid 是收件人，post_id 可空
            -- （关注这种没有帖的事件）。
            CREATE TABLE IF NOT EXISTS notifications (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                target_uid TEXT NOT NULL,
                kind       TEXT NOT NULL,
                actor_uid  TEXT NOT NULL,
                post_id    INTEGER,
                read_at    INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notif_target
                ON notifications(target_uid, id DESC);
            CREATE INDEX IF NOT EXISTS idx_notif_unread
                ON notifications(target_uid, read_at) WHERE read_at IS NULL;
            """
        )


# ============================================================
# 点赞
# ============================================================

def like(post_id: int, uid: str) -> dict:
    """点赞，返回 `{liked, count}`。已点过则取消（toggle）。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "DELETE FROM forum_likes WHERE post_id = ? AND uid = ?",
            (int(post_id), uid),
        )
        liked = False
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO forum_likes (post_id, uid, at) VALUES (?, ?, ?)",
                (int(post_id), uid, _now()),
            )
            liked = True
        n = conn.execute(
            "SELECT COUNT(*) FROM forum_likes WHERE post_id = ?", (int(post_id),)
        ).fetchone()[0]
    return {"liked": liked, "count": int(n)}


def like_state(post_id: int, uid: str | None) -> dict:
    """当前用户对这条帖的点赞状态（uid 为 None = 未登录，恒未赞）。"""
    _ensure()
    if not uid:
        return {"liked": False, "count": count_likes(post_id)}
    with store._get_conn() as conn:  # noqa: SLF001
        liked = conn.execute(
            "SELECT 1 FROM forum_likes WHERE post_id = ? AND uid = ?",
            (int(post_id), uid),
        ).fetchone()
        n = conn.execute(
            "SELECT COUNT(*) FROM forum_likes WHERE post_id = ?", (int(post_id),)
        ).fetchone()[0]
    return {"liked": bool(liked), "count": int(n)}


def count_likes(post_id: int) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM forum_likes WHERE post_id = ?", (int(post_id),)
            ).fetchone()[0]
        )


def count_likes_batch(post_ids: list[int]) -> dict[int, int]:
    """一批帖子的点赞数（{post_id: count}），列表页一次性拿完，避免 N+1。"""
    _ensure()
    ids = [int(x) for x in dict.fromkeys(post_ids or []) if x]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            f"SELECT post_id, COUNT(*) AS n FROM forum_likes "
            f"WHERE post_id IN ({marks}) GROUP BY post_id",
            ids,
        ).fetchall()
    return {int(r["post_id"]): int(r["n"]) for r in rows}


# ============================================================
# 收藏
# ============================================================

def favorite(post_id: int, uid: str) -> dict:
    """收藏/取消收藏（toggle），返回 `{favorited, count}`。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "DELETE FROM forum_favorites WHERE post_id = ? AND uid = ?",
            (int(post_id), uid),
        )
        favorited = False
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO forum_favorites (post_id, uid, at) VALUES (?, ?, ?)",
                (int(post_id), uid, _now()),
            )
            favorited = True
        n = conn.execute(
            "SELECT COUNT(*) FROM forum_favorites WHERE post_id = ?", (int(post_id),)
        ).fetchone()[0]
    return {"favorited": favorited, "count": int(n)}


def favorite_state(post_id: int, uid: str | None) -> dict:
    _ensure()
    if not uid:
        return {"favorited": False, "count": count_favorites(post_id)}
    with store._get_conn() as conn:  # noqa: SLF001
        fav = conn.execute(
            "SELECT 1 FROM forum_favorites WHERE post_id = ? AND uid = ?",
            (int(post_id), uid),
        ).fetchone()
        n = conn.execute(
            "SELECT COUNT(*) FROM forum_favorites WHERE post_id = ?", (int(post_id),)
        ).fetchone()[0]
    return {"favorited": bool(fav), "count": int(n)}


def count_favorites(post_id: int) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM forum_favorites WHERE post_id = ?",
                (int(post_id),),
            ).fetchone()[0]
        )


def count_favorites_batch(post_ids: list[int]) -> dict[int, int]:
    """一批帖子的收藏数（{post_id: count}）。"""
    _ensure()
    ids = [int(x) for x in dict.fromkeys(post_ids or []) if x]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            f"SELECT post_id, COUNT(*) AS n FROM forum_favorites "
            f"WHERE post_id IN ({marks}) GROUP BY post_id",
            ids,
        ).fetchall()
    return {int(r["post_id"]): int(r["n"]) for r in rows}


def list_favorites(uid: str, limit: int = 50) -> list[int]:
    """某用户收藏的帖子 id 列表（新的在前）。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT post_id FROM forum_favorites WHERE uid = ? "
            "ORDER BY at DESC LIMIT ?",
            (uid, max(1, min(int(limit), 100))),
        ).fetchall()
    return [int(r[0]) for r in rows]


def follow_topic(post_id: int, uid: str) -> dict:
    """关注/取消关注一条话题，返回 `{following, count}`。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "DELETE FROM forum_topic_follows WHERE post_id = ? AND uid = ?",
            (int(post_id), uid),
        )
        following = False
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO forum_topic_follows (post_id, uid, at) VALUES (?, ?, ?)",
                (int(post_id), uid, _now()),
            )
            following = True
        n = conn.execute(
            "SELECT COUNT(*) FROM forum_topic_follows WHERE post_id = ?", (int(post_id),)
        ).fetchone()[0]
    return {"following": following, "count": int(n)}


def topic_follow_state(post_id: int, uid: str | None) -> dict:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        following = bool(
            uid
            and conn.execute(
                "SELECT 1 FROM forum_topic_follows WHERE post_id = ? AND uid = ?",
                (int(post_id), uid),
            ).fetchone()
        )
        n = conn.execute(
            "SELECT COUNT(*) FROM forum_topic_follows WHERE post_id = ?", (int(post_id),)
        ).fetchone()[0]
    return {"following": following, "count": int(n)}


def topic_followers(post_id: int) -> list[str]:
    """关注这条话题的所有人。

    原本只有「关注/取关/读状态」三个入口读写这张表 —— 关注了却永远收不到通知，
    等于摆了个数字。回复时要靠这个把关注者捞出来一起发通知。
    """
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT uid FROM forum_topic_follows WHERE post_id = ?", (int(post_id),)
        ).fetchall()
    return [str(r[0]) for r in rows]


# ============================================================
# 关注
# ============================================================

def follow(follower: str, followee: str) -> dict:
    """A 关注 B（toggle）。自己不能关注自己。返回 `{following, count}`。"""
    _ensure()
    if follower == followee:
        raise ValueError("不能关注自己")
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "DELETE FROM user_follows WHERE follower = ? AND followee = ?",
            (follower, followee),
        )
        following = False
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO user_follows (follower, followee, at) VALUES (?, ?, ?)",
                (follower, followee, _now()),
            )
            following = True
        # count 是"关注者数"（这个人有多少粉丝），展示在作者名旁边
        n = conn.execute(
            "SELECT COUNT(*) FROM user_follows WHERE followee = ?", (followee,)
        ).fetchone()[0]
    return {"following": following, "count": int(n)}


def follow_state(follower: str | None, followee: str) -> dict:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        following = False
        if follower and follower != followee:
            following = bool(
                conn.execute(
                    "SELECT 1 FROM user_follows WHERE follower = ? AND followee = ?",
                    (follower, followee),
                ).fetchone()
            )
        n = conn.execute(
            "SELECT COUNT(*) FROM user_follows WHERE followee = ?", (followee,)
        ).fetchone()[0]
    return {"following": following, "count": int(n)}


# ============================================================
# 通知
# ============================================================

def notify(
    target_uid: str,
    kind: str,
    actor_uid: str,
    post_id: int | None = None,
) -> None:
    """落一条通知。自己触发的不落（自己赞自己/自己回复自己不算通知）。"""
    _ensure()
    if not target_uid or target_uid == actor_uid:
        return
    if kind not in ("like", "favorite", "follow", "mention", "reply"):
        raise ValueError(f"未知通知类型：{kind}")
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO notifications "
            "(target_uid, kind, actor_uid, post_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target_uid, kind, actor_uid, post_id, _now()),
        )


def list_notifications(uid: str, limit: int = 50) -> list[dict]:
    """某人的通知，新的在前。附上未读总数。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT id, kind, actor_uid, post_id, read_at, created_at "
            "FROM notifications WHERE target_uid = ? ORDER BY id DESC LIMIT ?",
            (uid, max(1, min(int(limit), 100))),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE target_uid = ? AND read_at IS NULL",
            (uid,),
        ).fetchone()[0]
    return {
        "items": [
            {
                "id": int(r["id"]),
                "kind": r["kind"],
                "actor_uid": r["actor_uid"],
                "post_id": int(r["post_id"]) if r["post_id"] is not None else None,
                "read": bool(r["read_at"]),
                "at": int(r["created_at"]),
            }
            for r in rows
        ],
        "unread": int(unread),
    }


def unread_count(uid: str) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE target_uid = ? "
                "AND read_at IS NULL",
                (uid,),
            ).fetchone()[0]
        )


def mark_read(uid: str, before_id: int | None = None) -> int:
    """标已读。before_id 为空 = 全部标读；否则只标 id <= before_id 的。
    返回标了几条。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        if before_id is None:
            cur = conn.execute(
                "UPDATE notifications SET read_at = ? "
                "WHERE target_uid = ? AND read_at IS NULL",
                (_now(), uid),
            )
        else:
            cur = conn.execute(
                "UPDATE notifications SET read_at = ? "
                "WHERE target_uid = ? AND read_at IS NULL AND id <= ?",
                (_now(), uid, int(before_id)),
            )
    return int(cur.rowcount or 0)


# ============================================================
# 删号/删帖时的清理
# ============================================================

def purge_user(uid: str) -> None:
    """删号时清掉这个人的所有互动痕迹。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM forum_likes WHERE uid = ?", (uid,))
        conn.execute("DELETE FROM forum_favorites WHERE uid = ?", (uid,))
        conn.execute("DELETE FROM forum_topic_follows WHERE uid = ?", (uid,))
        conn.execute(
            "DELETE FROM user_follows WHERE follower = ? OR followee = ?", (uid, uid)
        )
        conn.execute(
            "DELETE FROM notifications WHERE target_uid = ? OR actor_uid = ?",
            (uid, uid),
        )


def purge_post(post_id: int) -> None:
    """物理删帖时清掉挂在这条帖上的点赞/收藏/通知。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM forum_likes WHERE post_id = ?", (int(post_id),))
        conn.execute("DELETE FROM forum_favorites WHERE post_id = ?", (int(post_id),))
        conn.execute("DELETE FROM forum_topic_follows WHERE post_id = ?", (int(post_id),))
        conn.execute(
            "DELETE FROM notifications WHERE post_id = ?", (int(post_id),)
        )
