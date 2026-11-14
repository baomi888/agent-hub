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

**回复只做两级（楼中楼）**。照 Discuz / 微博 / YouTube 的做法，不是无限树：
  - 顶层帖（`root_id = 0`）+ 挂在它下面的回复（`root_id` = 顶层帖 id）；
  - 在回复里再点"回复"，新回复仍挂在**同一个顶层帖**下，只是 `reply_to_id`
    指向被点的那条，显示成「回复 @某某」；
  - 理由：手机缩进三次就没有正文宽度了（Discuz 的无限树在移动端几乎没人用，
    Discourse 也是 2026 年才把嵌套做成可选项）。而"回复某条回复"的真实诉求
    只是点名，一句 @ 就够，不需要再挖一层。
"""

import time

from core import session as store

# 单条最长字数。300 约等于手机输入框 8 行，够说完一件事，又不至于把
# 别人的一句话挤出一屏（留言板是"说一句"，不是发文章）。
MAX_LEN = 300

# 论坛话题正文的上限。论坛是"发一个帖子"，要说清背景、贴步骤、列选项，
# 300 字不够用；2000 字约等于手机输入 50 行，再长就该写成文档放进知识库了。
# 回复仍然走 MAX_LEN —— 盖楼是短句，不是第二篇文章。
TOPIC_LEN = 2000

# 话题标题上限。25 字在手机一行放得下、桌面列表里也不会撑破两行；
# 超了直接截断而不是报错 —— 标题被截断用户能忍，发帖失败不能忍。
MAX_TITLE = 60

# 一页多少条：手机上一屏大约放得下 6~8 条，30 条够翻三四屏。
# 这里数的是**顶层帖**，回复跟着所属的楼一起出来，不单独占一页的名额
# —— 否则一栋楼里聊了 20 句，第一页就只剩两栋楼了。
PAGE = 30

# 同一个人在同一库连续两条的最小间隔 / 每小时上限。都按 uid+kb_id 算，
# 不按 IP：校园网出口是同一个 IP，按 IP 会把整栋楼的人锁在一起。
COOLDOWN = 8
HOURLY_CAP = 30

# 「官方表情包」。只做前端插入用的候选，不落库、不校验 —— 存的是最终文本，
# 表情就是普通字符，所以以后想加一个表情不用改表、也不用改历史数据。
# 真要限制"只能用这几个"，那才是把好东西做窄了：用户打自己手机上的 😂
# 也不该被拒。
#
# 排序是按"用的时候怎么找"来的，不是按 Unicode：先是最常打的反应，
# 再是纯脸（情绪），最后是学习和校园场景。前端按 8 个一行铺，所以同一类
# 尽量落在同一行，看着才不散。
STICKERS: tuple[str, ...] = (
    # 常用反应（第一行）
    "🌽",
    "👍",
    "👏",
    "🙏",
    "💪",
    "🤝",
    "✅",
    "❌",
    # 情绪脸（第二~三行）
    "😂",
    "🤣",
    "🥲",
    "😭",
    "😅",
    "🤔",
    "😮",
    "😴",
    "🥹",
    "😤",
    "🫠",
    "😇",
    "🤯",
    "😬",
    "🫡",
    "🙃",
    # 语气与强调（第四~五行）
    "🔥",
    "💡",
    "❤️",
    "✨",
    "🎉",
    "📌",
    "⚡",
    "❗",
    "❓",
    "💯",
    "👀",
    "🤞",
    "🎯",
    "⏰",
    "🚀",
    "🍀",
    # 学习与校园（第六行）
    "📚",
    "📝",
    "✏️",
    "🧮",
    "📐",
    "🔬",
    "🎓",
    "☕",
)

_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS kb_posts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kb_id       TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        body        TEXT NOT NULL,
        created_at  INTEGER NOT NULL,
        deleted_at  INTEGER,
        deleted_by  TEXT,
        root_id     INTEGER NOT NULL DEFAULT 0,
        reply_to_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_kbpost_kb ON kb_posts(kb_id, id DESC);
    CREATE INDEX IF NOT EXISTS idx_kbpost_user ON kb_posts(user_id, created_at);
"""

# 注意：`idx_kbpost_root` 不在上面这段里 —— 老库的 kb_posts 没有 root_id 列，
# 同一条脚本里建索引会先于 ALTER 执行，直接报 `no such column`。
# 它在 `_ensure` 里补完列之后再建。

# 老库补列。线上 kb_posts 是 2026-11-13 白天建的，`CREATE TABLE IF NOT EXISTS`
# 对它一行都不生效 —— 不 ALTER 就会在第一次带回复的查询上炸 `no such column`。
# 列定义只在 `session._KB_POST_ADD_COLUMNS` 里写一份，这里只是再兜一次
# （测试换库时可能绕过 `session._init_db`，直接建表）。
_ADD_COLUMNS = store._KB_POST_ADD_COLUMNS  # noqa: SLF001 —— 同一份定义，不抄第二遍

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
        store._add_missing_columns(conn, "kb_posts", _ADD_COLUMNS)  # noqa: SLF001
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kbpost_root ON kb_posts(kb_id, root_id, id)"
        )
        # 论坛列表要按板块筛顶层帖；列同样是 ALTER 补的，索引也得等补完再建
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kbpost_forum ON kb_posts(root_id, cat, id DESC)"
        )
    _READY.add(path)


def _now() -> int:
    return int(time.time())


# 两条帖子的公共列。多一个字段要改两处 SQL 时，漏一处就是运行期 KeyError，
# 所以拼成常量。
_POST_COLS = "id, kb_id, user_id, title, cat, solved, body, created_at, root_id, reply_to_id"


def _row_to_post(r) -> dict:
    return {
        "id": int(r["id"]),
        "user_id": r["user_id"],
        "title": r["title"] or "",
        "cat": r["cat"] or "",
        "solved": bool(r["solved"]),
        "body": r["body"],
        "at": int(r["created_at"]),
        "root_id": int(r["root_id"] or 0),
        "reply_to_id": int(r["reply_to_id"] or 0),
    }


def list_threads(kb_id: str, limit: int = PAGE, before: int | None = None) -> list[dict]:
    """按时间倒序取一页**顶层帖**，每栋楼带上它的全部回复（回复按时间正序）。

    `before` 是游标（上一页最后一条顶层帖的 id），不是 offset：
    offset 分页在"边看边有人发"时会重复/漏掉条目，游标不会。

    两条查询而不是一条 JOIN：先拿这一页楼主的 id，再一次性 `root_id IN (...)`
    取它们的回复。JOIN 会把"一栋楼 20 条回复"摊成 20 行，还得在 Python 里
    去重合并楼层，读起来更绕；两条查询各自都能走索引，代价一样。
    """
    _ensure()
    limit = max(1, min(int(limit or PAGE), 100))
    sql = (
        f"SELECT {_POST_COLS} FROM kb_posts "
        "WHERE kb_id = ? AND deleted_at IS NULL AND root_id = 0"
    )
    args: list[object] = [kb_id]
    if before:
        sql += " AND id < ?"
        args.append(int(before))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    with store._get_conn() as conn:  # noqa: SLF001
        roots = [_row_to_post(r) for r in conn.execute(sql, args).fetchall()]
        if not roots:
            return []
        marks = ",".join("?" * len(roots))
        kids = conn.execute(
            f"SELECT {_POST_COLS} FROM kb_posts "
            f"WHERE kb_id = ? AND deleted_at IS NULL AND root_id IN ({marks}) "
            "ORDER BY id ASC",
            [kb_id, *[p["id"] for p in roots]],
        ).fetchall()

    by_root: dict[int, list[dict]] = {}
    for r in kids:
        by_root.setdefault(int(r["root_id"]), []).append(_row_to_post(r))
    for p in roots:
        p["replies"] = by_root.get(p["id"], [])
    return roots


def count_posts(kb_id: str) -> int:
    """总条数（顶层 + 回复）。卡片上的「N 条留言」用它，用户看到的是总热度。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*) FROM kb_posts WHERE kb_id = ? AND deleted_at IS NULL", (kb_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def stats(kb_id: str) -> tuple[int, int]:
    """`(总条数, 顶层帖数)`，一条查询。

    总条数给卡片上的「N 条留言」（用户关心的是热闹程度，回复也算）；
    顶层帖数只用来判断"还有没有更早的楼"—— 不能用总条数：一栋楼里聊了 20 句时
    总数是 21，而这一页只带回 1 栋楼，`has_more` 会永远为真，用户一直点
    「加载更早」却什么也加载不出来。
    """
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN root_id = 0 THEN 1 ELSE 0 END) "
            "FROM kb_posts WHERE kb_id = ? AND deleted_at IS NULL",
            (kb_id,),
        ).fetchone()
    if not row:
        return 0, 0
    return int(row[0] or 0), int(row[1] or 0)


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


def add_post(
    kb_id: str,
    uid: str,
    body: str,
    reply_to: int | None = None,
    title: str = "",
    cat: str = "",
    limit: int = MAX_LEN,
) -> dict:
    """写一条留言或一条回复。正文合法性由 API 层先校验（这里只兜一次空值）。

    `reply_to` 是被回复的那条帖 id。两级封顶的处理就在这里：
      - 回复**顶层帖** → 新帖挂在这栋楼下（root_id = 它的 id）；
      - 回复**某条回复** → 新帖仍挂在这栋楼下（root_id 取它的 root_id），
        只把 reply_to_id 指向那条回复，前端显示成「回复 @某某」。
    所以不管点的是哪一层，库里都只有两层，列表结构永远可预测。

    `title` / `cat` 是论坛中心（2026-11-14）加的两个字段：
      - 库内留言两个都给空串，行为和以前完全一致；
      - 论坛话题给标题 + 板块。`kb_id` 为空串表示这帖不挂任何库。
    回复行一律不带 title/cat（它们只属于顶层帖，回复上的值是垃圾）。
    """
    text = (body or "").strip()
    if not text:
        raise ValueError("留言不能为空")
    if len(text) > limit:
        raise ValueError(f"最多 {limit} 字，现在 {len(text)} 字")

    root_id = 0
    reply_id = 0
    if reply_to:
        target = get_post(int(reply_to))
        # 三条都必须成立：存在、没删、在同一个库。
        # 只判"存在"的话，拿别的库的帖 id 也能挂上去 —— 留言就会串库。
        # 空 kb_id（论坛里的独立话题）同理：只有同为空串的帖能互相回复，
        # 拿一个挂库的帖 id 到这里会被挡掉。
        if not target or target.get("deleted_at") or target["kb_id"] != kb_id:
            raise ValueError("要回复的留言不存在或已被删除")
        root_id = int(target["root_id"] or target["id"])
        reply_id = int(target["id"])

    gap = _now() - last_post_at(kb_id, uid)
    if gap < COOLDOWN:
        raise TooFast(max(1, COOLDOWN - gap))
    if recent_count(kb_id, uid) >= HOURLY_CAP:
        raise TooFast(0)

    _ensure()
    now = _now()
    clean_title = (title or "").strip()[:MAX_TITLE]
    clean_cat = (cat or "").strip()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO kb_posts "
            "(kb_id, user_id, body, created_at, root_id, reply_to_id, title, cat) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kb_id, uid, text, now, root_id, reply_id, clean_title, clean_cat),
        )
        pid = int(cur.lastrowid or 0)
    return {
        "id": pid,
        "user_id": uid,
        "title": clean_title,
        "cat": clean_cat,
        "solved": False,
        "body": text,
        "at": now,
        "root_id": root_id,
        "reply_to_id": reply_id,
    }


def get_post(post_id: int) -> dict | None:
    """单条（含软删的，删除/回复校验要用到 `deleted_at`）。

    返回的键与 `list_threads` 一致（时间叫 `at` 不叫 `created_at`）：同一个
    结构喂给同一个视图函数，少一层"这里要记得改名"的转换。
    """
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        r = conn.execute(
            f"SELECT {_POST_COLS}, deleted_at FROM kb_posts WHERE id = ?",
            (int(post_id),),
        ).fetchone()
    if not r:
        return None
    post = _row_to_post(r)
    post["kb_id"] = r["kb_id"]
    post["deleted_at"] = r["deleted_at"]
    return post


def replies_of(root_id: int) -> list[dict]:
    """某一栋楼下的全部回复（正序）。发完回复后前端要拿到整栋楼的最新样子。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            f"SELECT {_POST_COLS} FROM kb_posts "
            "WHERE root_id = ? AND deleted_at IS NULL ORDER BY id ASC",
            (int(root_id),),
        ).fetchall()
    return [_row_to_post(r) for r in rows]


def delete_post(post_id: int, by_uid: str, kb_id: str | None = None) -> int:
    """软删一条留言。返回**真的删掉了几条**（0 = 不存在/已删/库不匹配）。

    删顶层帖时连它的回复一起删：一栋楼没了开头那句，剩下的回复就是断章
    取义 —— 而且它们仍然计入总数，会出现"显示 3 条、列表里一条都没有"。
    删单条回复时只影响它自己（回复下面不会再挂东西，两级封顶的副作用之一）。

    调用方负责判权限：作者本人，或者库主/管理员。
    带 `kb_id` 是为了防止"用 A 库的帖 id 去删 B 库的帖"——路径上的库和
    帖子的库必须是同一个，不然越权检查会被一条错路径绕过去。
    """
    _ensure()
    sql = (
        "UPDATE kb_posts SET deleted_at = ?, deleted_by = ? "
        "WHERE deleted_at IS NULL AND (id = ? OR root_id = ?)"
    )
    args: list[object] = [_now(), by_uid, int(post_id), int(post_id)]
    if kb_id is not None:
        sql += " AND kb_id = ?"
        args.append(kb_id)
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(sql, args)
        return int(cur.rowcount or 0)


def purge_kb(kb_id: str) -> int:
    """库被删除时清掉它的全部留言（物理删）。

    这里不留软删：展示对象本身已经不在了，留一堆没人能看见的行只是垃圾。
    与"删帖留痕"不冲突——那条规则是为了还能看见的库。
    """
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute("DELETE FROM kb_posts WHERE kb_id = ?", (kb_id,))
        return cur.rowcount or 0
