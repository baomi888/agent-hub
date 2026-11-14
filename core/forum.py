from __future__ import annotations

"""论坛中心（/forum）的跨库聚合层。

它和 `core/kb_board.py` 的分工：
  - `kb_board` 管**一个库**的那面墙：留言、楼中楼、游标翻页；
  - `forum` 管**跨库的广场**：板块、排序、话题列表。

两者共用同一张 `kb_posts` 表，靠 `cat` 区分：
  - `cat = ''` → 库内留言。只在库详情页的留言板里出现，**不进论坛**；
  - `cat != ''` → 论坛话题。可以有 `title`，可以被标「已解决」。

为什么不用「库留言自动进论坛」：
留言板的设计前提是"说一句"（300 字、没有标题），把它搬进论坛只能靠截正文
当标题、靠猜分板块。一个满屏都是"第三章中值定理那块……"当标题的列表，比没有
论坛更糟。真的想讨论，从论坛选一个库作为来源发布——那条会同时挂在库下面
（`list_threads` 按 kb_id 取，不看 cat），两边都能看见。

`kb_id = ''` 表示这帖不挂任何库（"周三晚图书馆缺两人"挂不到哪个库上）。
这也是为什么没有给 kb_posts 建第二个表：`kb_id` 是 TEXT NOT NULL，
空串天然就是一个合法值，加一张表要重写全部回复/删除/统计逻辑，收益为零。
"""

import time

from core import kb_board
from core import session as store

# 板块白名单。key 落库，label 只在展示层用 —— 改文案不用动数据。
#
# 为什么按"话题类型"分而不是按课程分：9 门官方课 + 同学分享的库会越滚越多，
# 按库分栏最后左栏会变成第二个资料广场。库的关系用帖子上的来源 chip 表达就够了。
CATS: tuple[tuple[str, str, str], ...] = (
    # 站务公告排第一：它是"官方说的话"，进论坛第一眼该看到的是规则和更新，
    # 而不是别人的提问。左栏、手机 chip 条、发帖面板都读这个顺序。
    ("meta", "站务公告", "论坛规则、版本更新、维护通知"),
    ("ask", "课程答疑", "听不懂的地方、习题、考点"),
    ("res", "求资料", "真题、笔记、课件"),
    ("exp", "学习经验", "选课、复习、踩坑"),
    ("team", "组队自习", "找人一起、约时间地点"),
    ("kbfb", "库内容反馈", "资料有错、缺页、建议补充"),
)

CAT_KEYS: frozenset[str] = frozenset(k for k, _, _ in CATS)
CAT_LABEL: dict[str, str] = {k: label for k, label, _ in CATS}

# 一页多少个话题。20 条在 1080 宽下刚好一屏多一点，翻一次页不会"翻过去还是
# 同一片内容"；手机上一屏 5~6 条，四屏。
PAGE = 20

# 摘要长度。手机列表一行放 34 个汉字左右，110 字刚好三行，再多就被截断成
# 四行半 —— 那行高会把每一条帖子撑成一块，列表就失去了"扫一眼"的能力。
EXCERPT = 110

# 标题兜底长度。24 是因为再长在 390 宽的手机上就会换行、把下面那条的标题挤开，
# 而行数不齐的列表看起来像加载错了。
MAX_TITLE_FALLBACK = 24


def _one_line(s: str) -> str:
    """把正文压成一行：换行、连续空白都收成一个空格。

    不这么做的话，一个带空行的帖子在列表里会占掉三行高度，而且看不出
    "这是一整段话"还是"三个短句"。
    """
    return " ".join((s or "").split())


def excerpt_of(row) -> str:
    """列表里显示的那段字。标题为空时前端拿它兜底当标题，所以这里不打省略号
    —— 截断标记由调用方（前端 CSS 的 line-clamp）负责。"""
    return _one_line(row["body"])[:EXCERPT]


def title_of(row) -> str:
    """话题标题。真没有就用正文前 24 字顶上。"""
    t = _one_line(row["title"] or "")
    return t[:MAX_TITLE_FALLBACK] if t else _one_line(row["body"])[:MAX_TITLE_FALLBACK]


def _now() -> int:
    return int(time.time())


def _today_start() -> int:
    """今天 00:00 的时间戳（本地时区）。

    用 `time.localtime` 拆而不是"现在 - 86400"：后者算出来的是"过去 24 小时"，
    在早上 8 点看会显示昨天下午的帖子算"今日"。
    """
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


# 回复数与最后活动时间。都是相关子查询，不是 JOIN：
# JOIN + GROUP BY 也能算，但一栋楼 30 条回复时结果集要摊成 30 行再聚合，
# 而子查询各自走 `idx_kbpost_root`，20 条话题 = 40 次索引查找，量级小得多。
_REPLY_N = (
    "(SELECT COUNT(*) FROM kb_posts r "
    " WHERE r.root_id = p.id AND r.deleted_at IS NULL)"
)
_LAST_AT = (
    "(SELECT MAX(r.created_at) FROM kb_posts r "
    " WHERE r.root_id = p.id AND r.deleted_at IS NULL)"
)

# root_id / reply_to_id 也带上：删单条回复要走同一条查询，而回复的 cat 是空的,
# 光看 cat 分不出"根本不存在的 id"和"这是一条回复"。
_LIST_COLS = (
    "p.id, p.kb_id, p.user_id, p.title, p.cat, p.body, p.created_at, "
    "p.root_id, p.reply_to_id, "
    f"{_REPLY_N} AS reply_n, COALESCE({_LAST_AT}, p.created_at) AS last_at"
)

# 两种排序。`new` 用 id 游标翻页（id 与时间同向，游标永远单调）；
# `hot` 用**分数**排序，分数一变游标就失效 —— 所以只给首屏，不假装能翻页
# （`has_more` 恒为 False）。热榜本来也就是看个 Top 20。
#
# 没有 `unsolved`：这个论坛是讨论与分享，没有"提问—解决"的闭环。
_ORDERS = {
    "new": "p.id DESC",
    "hot": "reply_n DESC, last_at DESC, p.id DESC",
}


def _base_where(cat: str | None, q: str = "") -> tuple[str, list[object]]:
    """论坛列表的公共 WHERE：顶层帖、没删、有板块。

    `cat != ''` 这一条是**论坛与留言板的分界线**，不能省。
    """
    sql = "WHERE p.deleted_at IS NULL AND p.root_id = 0 AND p.cat != ''"
    args: list[object] = []
    if cat:
        sql += " AND p.cat = ?"
        args.append(cat)
    if q:
        # 标题或正文包含。LIKE 不做全文索引，但论坛这一量级（几百条）
        # 全表扫一遍是微秒级，上 FTS5 要为它单独维护一张影子表和触发器，
        # 换不来任何可感知的收益。
        sql += " AND (p.title LIKE ? OR p.body LIKE ?)"
        args.extend([f"%{q}%", f"%{q}%"])
    return sql, args


def list_topics(
    cat: str | None = None,
    sort: str = "new",
    before: int | None = None,
    limit: int = PAGE,
    q: str = "",
    exclude: list[int] | None = None,
) -> tuple[list[dict], bool]:
    """一页话题，返回 `(rows, has_more)`。

    多取一条来判断"还有没有下一页"，而不是再发一次 COUNT：COUNT 要扫全表，
    而且两次查询之间有人发帖的话，两个数字对不上，用户会看到"还有更多"
    点下去却是空的。

    `exclude` 是被上面置顶拿走的 id。在 SQL 里排除而不是查回来再过滤：
    后者会让这一页少两条，翻页时"这一页比上一页短一截"看起来像丢数据。
    """
    kb_board._ensure()  # noqa: SLF001 —— 建表/补列的唯一入口，不抄第二份
    limit = max(1, min(int(limit or PAGE), 50))
    sort = sort if sort in _ORDERS else "new"

    where, args = _base_where(cat, (q or "").strip())
    # 游标只对时间序有效。热榜传了 before 也忽略 —— 悄悄按错误语义翻页，
    # 比干脆不支持更难查。
    if before and sort == "new":
        where += " AND p.id < ?"
        args.append(int(before))
    if exclude:
        marks = ",".join("?" * len(exclude))
        where += " AND p.id NOT IN (" + marks + ")"
        args.extend(int(x) for x in exclude)

    sql = f"SELECT {_LIST_COLS} FROM kb_posts p {where} ORDER BY {_ORDERS[sort]} LIMIT ?"
    args.append(limit + 1)

    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(sql, args).fetchall()

    has_more = len(rows) > limit
    return [dict(r) for r in rows[:limit]], has_more


def list_pinned(limit: int = 2) -> list[dict]:
    """置顶的站务公告（最新的在前）。

    为什么置顶而不是靠排序自然浮上来：公告发出去一周之后，它会被新提问压下去，
    而"论坛规则改了""维护时间"这种内容恰恰是随时该看得见的。所以由板块决定
    位置，不由时间决定。

    只取 2 条：置顶超过两条就不再是"公告"，而是"公告区"——列表第一屏会被
    官方内容占满，用户反而找不到别人问的问题。
    """
    kb_board._ensure()  # noqa: SLF001
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            f"SELECT {_LIST_COLS} FROM kb_posts p "
            "WHERE p.deleted_at IS NULL AND p.root_id = 0 AND p.cat = 'meta' "
            "ORDER BY p.id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(r) for r in rows]


def count_by_cat() -> dict[str, int]:
    """每个板块有多少话题。一条 GROUP BY，左栏的计数就齐了。"""
    kb_board._ensure()  # noqa: SLF001
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT cat, COUNT(*) FROM kb_posts "
            "WHERE deleted_at IS NULL AND root_id = 0 AND cat != '' GROUP BY cat"
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def stats() -> dict:
    """广场头部那几个数字。三条标量，一条 SQL 拿完。

    `today` 必须和 `topics` 同口径（只数主帖），否则页面上会出现
    「全部话题 3」旁边写着「今日新增 4 条」—— 4 是 3 个主帖加 1 条回复，
    数字比总数还大，读的人会以为哪边坏了。
    """
    kb_board._ensure()  # noqa: SLF001
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT "
            " SUM(CASE WHEN root_id = 0 AND cat != '' THEN 1 ELSE 0 END), "
            " SUM(CASE WHEN root_id != 0 THEN 1 ELSE 0 END), "
            " SUM(CASE WHEN root_id = 0 AND cat != '' AND created_at >= ? THEN 1 ELSE 0 END) "
            "FROM kb_posts WHERE deleted_at IS NULL",
            (_today_start(),),
        ).fetchone()
    return {
        "topics": int((row and row[0]) or 0),
        "replies": int((row and row[1]) or 0),
        "today": int((row and row[2]) or 0),
    }


def get_topic(post_id: int) -> dict | None:
    """单个话题（含软删的，权限判断要看 `deleted_at`）。"""
    kb_board._ensure()  # noqa: SLF001
    with store._get_conn() as conn:  # noqa: SLF001
        r = conn.execute(
            f"SELECT {_LIST_COLS}, p.deleted_at FROM kb_posts p WHERE p.id = ?",
            (int(post_id),),
        ).fetchone()
    return dict(r) if r else None


def purge_user(uid: str) -> int:
    """删号时清掉这个人发的话题与回复（物理删）。

    与 `kb_board.purge_kb` 同一个理由：展示对象已经不在了，留一堆没人能看见的
    行只是垃圾。区别是这里按人删 —— 一个人注销了，他开的楼跟着消失，
    别人在楼里的回复也一起走（楼没了开头那句，剩下的就是断章取义）。
    """
    kb_board._ensure()  # noqa: SLF001
    with store._get_conn() as conn:  # noqa: SLF001
        roots = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM kb_posts WHERE user_id = ? AND root_id = 0", (uid,)
            ).fetchall()
        ]
        n = 0
        for rid in roots:
            cur = conn.execute(
                "DELETE FROM kb_posts WHERE id = ? OR root_id = ?", (rid, rid)
            )
            n += int(cur.rowcount or 0)
        cur = conn.execute("DELETE FROM kb_posts WHERE user_id = ?", (uid,))
        n += int(cur.rowcount or 0)
    return n
