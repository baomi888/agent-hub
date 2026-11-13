from __future__ import annotations

"""公开知识库留言板。

为什么要单独一个模块，而不是塞进 `kb/pipelines.py`：
留言是**用户产生的内容**，和向量索引没有关系 —— 它不参与检索，也不该
让"重建索引 / 换切片参数"顺手抹掉。两者唯一的关联是 `kb_id`。

为什么表放在会话库里（不新开一个 sqlite 文件）：
留言量级和会话是一个数量级，再开一个文件只多一处备份、迁移、连接要记得
—— 收益为零，成本是以后的每一次运维。

三条边界（API 层也各自守一遍）：
  1. 只有**已公开**的库能留言（官方库 or 用户主动公开的库）；
  2. 只收文字：没有图片、没有附件、没有链接预览；
  3. 软删：`deleted_at` 非空即不可见，但行还在 —— 用户投诉"我留言被删了"
     时，要说得清是谁、什么时候删的。
"""

import time

from core import session as store

# 单条最长字数。300 约等于手机输入框 8 行，够说完一件事，又不至于把
# 别人的一句话挤出一屏（留言板是"说一句"，不是发文章）。
MAX_LEN = 300

# 一页多少条：手机上一屏大约放得下 6~8 条，30 条够翻三四屏。
PAGE = 30

# 同一个人在同一库连续两条的最小间隔 / 每小时上限。都按 uid+kb_id 算，
# 不按 IP：校园网出口是同一个 IP，按 IP 会把整栋楼的人锁在一起。
COOLDOWN = 8
HOURLY_CAP = 30

# 「官方表情包」。只做前端插入用的候选，不落库、不校验 —— 存的是最终文本，
# 表情就是普通字符，所以以后想加一个表情不用改表、也不用改历史数据。
# 真要限制"只能用这几个"，那才是把好东西做窄了：用户打自己手机上的 😂
# 也不该被拒。
STICKERS: tuple[str, ...] = (
    "🌽",
    "👍",
    "👏",
    "🙏",
    "😂",
    "🥲",
    "🤔",
    "😮",
    "🔥",
    "💡",
    "❤️",
    "✅",
    "📌",
    "🎉",
)

_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS kb_posts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        kb_id      TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        body       TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        deleted_at INTEGER,
        deleted_by TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_kbpost_kb ON kb_posts(kb_id, id DESC);
    CREATE INDEX IF NOT EXISTS idx_kbpost_user ON kb_posts(user_id, created_at);
"""

# 建过表的库路径。测试会换 `_db_path`（AGENT_HUB_DB 缝），所以键是路径而不是
# 一个布尔值 —— 只记 True 的话，换库之后第一次写入会撞 `no such table`，
# 而且单跑测试绿、组合跑才炸（和 account._ensure_tables 里那段注释同一个坑）。
_READY: set[str] = set()


def _ensure() -> None:
    path = store._db_path()  # noqa: SLF001 —— 与 account 同一处测试缝，读它只为做缓存键
    if path in _READY:
        return
    with store._get_conn() as conn:
        conn.executescript(_TABLES_SQL)
    _READY.add(path)


def _now() -> int:
    return int(time.time())


def list_posts(kb_id: str, limit: int = PAGE, before: int | None = None) -> list[dict]:
    """按时间倒序取一页留言（最新的在前）。

    `before` 是游标（上一页最后一条的 id），不是 offset：
    offset 分页在"边看边有人发"时会重复/漏掉条目，游标不会。
    """
    _ensure()
    limit = max(1, min(int(limit or PAGE), 100))
    sql = (
        "SELECT id, kb_id, user_id, body, created_at FROM kb_posts "
        "WHERE kb_id = ? AND deleted_at IS NULL"
    )
    args: list[object] = [kb_id]
    if before:
        sql += " AND id < ?"
        args.append(int(before))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(sql, args).fetchall()
    return [
        {
            "id": int(r["id"]),
            "user_id": r["user_id"],
            "body": r["body"],
            "at": int(r["created_at"]),
        }
        for r in rows
    ]


def count_posts(kb_id: str) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*) FROM kb_posts WHERE kb_id = ? AND deleted_at IS NULL", (kb_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def counts_by_kb() -> dict[str, int]:
    """一次查出所有库的留言数，给广场卡片用。

    逐个库 count 会变成 N 次查询：广场首页有几十个库，每次刷新几十次
    "查一下有几条"，全都走同一条 `WHERE kb_id = ?`。一条 GROUP BY 就够。
    """
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT kb_id, COUNT(*) FROM kb_posts WHERE deleted_at IS NULL GROUP BY kb_id"
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def last_post_at(kb_id: str, uid: str) -> int:
    """这个人上次在这个库发帖的时间；没发过返回 0。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT MAX(created_at) FROM kb_posts WHERE kb_id = ? AND user_id = ?",
            (kb_id, uid),
        ).fetchone()
    return int(row[0]) if row and row[0] else 0


def recent_count(kb_id: str, uid: str, window: int = 3600) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*) FROM kb_posts WHERE kb_id = ? AND user_id = ? AND created_at >= ?",
            (kb_id, uid, _now() - window),
        ).fetchone()
    return int(row[0]) if row else 0


class TooFast(Exception):
    """发得太快。带上还要等几秒，前端可以直接提示。"""

    def __init__(self, wait: int) -> None:
        super().__init__(f"慢一点，{wait} 秒后再发")
        self.wait = wait


def add_post(kb_id: str, uid: str, body: str) -> dict:
    """写一条留言。正文的合法性由 API 层先校验（这里只兜一次空值）。"""
    text = (body or "").strip()
    if not text:
        raise ValueError("留言不能为空")
    if len(text) > MAX_LEN:
        raise ValueError(f"留言最多 {MAX_LEN} 字")

    gap = _now() - last_post_at(kb_id, uid)
    if gap < COOLDOWN:
        raise TooFast(max(1, COOLDOWN - gap))
    if recent_count(kb_id, uid) >= HOURLY_CAP:
        raise TooFast(0)

    _ensure()
    now = _now()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO kb_posts (kb_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (kb_id, uid, text, now),
        )
        pid = int(cur.lastrowid or 0)
    return {"id": pid, "user_id": uid, "body": text, "at": now}


def get_post(post_id: int) -> dict | None:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT id, kb_id, user_id, body, created_at, deleted_at FROM kb_posts WHERE id = ?",
            (int(post_id),),
        ).fetchone()
    return dict(row) if row else None


def delete_post(post_id: int, by_uid: str, kb_id: str | None = None) -> bool:
    """软删一条留言。返回是否真的删掉了（已经删过 / 不存在 → False）。

    调用方负责判权限：作者本人，或者库主/管理员。
    带 `kb_id` 是为了防止"用 A 库的帖 id 去删 B 库的帖"——路径上的库和
    帖子的库必须是同一个，不然越权检查会被一条错路径绕过去。
    """
    _ensure()
    sql = "UPDATE kb_posts SET deleted_at = ?, deleted_by = ? WHERE id = ? AND deleted_at IS NULL"
    args: list[object] = [_now(), by_uid, int(post_id)]
    if kb_id is not None:
        sql += " AND kb_id = ?"
        args.append(kb_id)
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(sql, args)
        return cur.rowcount > 0


def purge_kb(kb_id: str) -> int:
    """库被删除时清掉它的全部留言（物理删）。

    这里不留软删：展示对象本身已经不在了，留一堆没人能看见的行只是垃圾。
    与"删帖留痕"不冲突——那条规则是为了还能看见的库。
    """
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute("DELETE FROM kb_posts WHERE kb_id = ?", (kb_id,))
        return cur.rowcount or 0
