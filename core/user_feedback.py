"""用户反馈（DeepSeek 式的「几个按钮 + 一段话」），供开发者后台处理。

**为什么不复用 `feedbacks` 表**
两者语义不同，混用会让两边的查询互相污染：

  `feedbacks`       —— 挂在某条**消息**上的赞/踩（rating 只允许 up/down），
                       是"这个回答好不好"的评测信号，有唯一索引保证"一条消息一条反馈"
  `user_feedback`   —— 用户主动提的**一条意见**（分类 + 自由文本 + 处理状态），
                       是"产品哪里要改"的工单信号，同一个人可以提很多条、可以重复提

把"回答不满意"这个分类塞进 `feedbacks` 会导致：prompt 自进化（`service/prompt_evo.py`）
把用户的产品建议当成低分回答样本去优化提示词 —— 直接污染训练集。

字典与状态流转规则见 `docs/数据看板_2026-10-27/口径与数据字典.md` §4。
字典只在这里定义一份：前端渲染按钮、后端校验、导出 CSV 的表头全从它取，
避免"前端能点、后端 400"这类前后端各写一份字典的经典不一致。
"""

from __future__ import annotations

import time

# ==================== 字典（唯一来源，前端由 /api/admin/dict 拉）====================

# 顺序即前端按钮的展示顺序
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("bug", "出错了"),
    ("answer", "回答不满意"),
    ("feature", "功能建议"),
    ("ui", "界面与体验"),
    ("other", "其他"),
)

STATUSES: tuple[tuple[str, str], ...] = (
    ("open", "待处理"),
    ("doing", "处理中"),
    ("done", "已处理"),
    ("closed", "不处理"),
)

_CATEGORY_MAP = dict(CATEGORIES)
_STATUS_MAP = dict(STATUSES)

# 允许的状态流转。刻意**不允许回退**（done → doing）：
# 状态是给开发者看处理进度的，允许回退会让"处理耗时"这类后续统计失去意义。
# 真处理错了，用 closed 关掉重开一条新的（历史留着反而是证据）。
_TRANSITIONS: dict[str, set[str]] = {
    "open": {"doing", "done", "closed"},
    "doing": {"done", "closed"},
    "done": {"closed"},
    "closed": set(),
}

# 内容长度。下限 5 而不是 1：一个字的"卡"无法处理，只会让反馈列表变成噪声。
MIN_CONTENT = 5
MAX_CONTENT = 2000
MAX_CONTACT = 120
MAX_PAGE = 120
# 回复长度上限 1000：回复是"告诉提意见的人我们做了什么"，不是写文档。
# 下限只要 1 个字而不是 5 ——「已修复，感谢」这种短回复是正常且应当鼓励的，
# 拿反馈正文的下限去卡回复，会逼着人凑字数。
MIN_REPLY = 1
MAX_REPLY = 1000


def category_label(value: str) -> str:
    return _CATEGORY_MAP.get(value, value)


def status_label(value: str) -> str:
    return _STATUS_MAP.get(value, value)


def dict_payload() -> dict:
    """下发给前端的字典（前端不得硬编码，见文档 §4.1）。"""
    return {
        "categories": [{"value": v, "label": n} for v, n in CATEGORIES],
        "statuses": [{"value": v, "label": n} for v, n in STATUSES],
        "transitions": {k: sorted(v) for k, v in _TRANSITIONS.items()},
        "limits": {
            "min_content": MIN_CONTENT,
            "max_content": MAX_CONTENT,
            "max_contact": MAX_CONTACT,
            "max_page": MAX_PAGE,
            "min_reply": MIN_REPLY,
            "max_reply": MAX_REPLY,
        },
    }


# ==================== 建表 ====================

_READY: set[str] = set()

# 后续追加的列。**只走 ALTER，不改上面的 CREATE TABLE** ——
# 两边都写会变成两条并行的 DDL 路径：老库靠 ALTER、新库靠 CREATE，
# 一旦哪边漏了一个，新旧库的列就对不上，而这种差异平时完全看不出来。
# 写法照 `service/daily.py` 给 delegations 补 last_error 那一套（幂等）。
_EXTRA_COLUMNS: dict[str, str] = {
    # 【2026-11-17 起废弃】原来这里存"唯一一条、可反复改写"的回复。
    #
    # 加进第二个管理员之后这个模型就塌了：两个人共用同一格，后写的覆盖先写的，
    # 谁写的看不出来，而且谁都能改对方的话。现在回复是**追加式线程**，落在
    # `user_feedback_replies` 表里，每条带作者、发出即定稿（`add_reply`）。
    #
    # 这两列只为老库兼容保留：写路径不再碰它们，读取时 `reply` / `replied_at`
    # 由最新一条回复合成（见 `_attach_replies`），老前端不改也能跑。
    "reply": "TEXT NOT NULL DEFAULT ''",
    "replied_at": "INTEGER",
}

# 一条回复 = 一次发言，不覆盖、不编辑。`admin_uid` 存下来是为了两件事：
#   1. 后台能认出"这条是我发的"（同一个人可能有多条）
#   2. 用户侧显示"是谁回的我"——用户明确要求看得到是哪个管理员
# 名字另存一份（不 JOIN users）：管理员改名之后，历史回复应当保留当时的署名，
# 而不是整串历史跟着变成新名字。
_REPLIES_DDL = """
    CREATE TABLE IF NOT EXISTS user_feedback_replies (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        fid        INTEGER NOT NULL,
        admin_uid  TEXT NOT NULL DEFAULT '',
        admin_name TEXT NOT NULL DEFAULT '',
        content    TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ufr_fid ON user_feedback_replies(fid, id);
"""


def _migrate_legacy_replies(conn) -> None:
    """把老库里那条 `reply` 搬进新表（幂等）。

    用 `INSERT ... SELECT ... WHERE NOT EXISTS` 一条语句完成"查重 + 插入"：
    分两步查再插，在多进程同时冷启动时会各插一条，同一条回复在用户侧显示两遍。

    作者留空：老数据没记是谁回的，编一个名字比留空更糟 —— 前端对空作者显示
    「管理员」，那是实话。
    """
    conn.execute(
        """
        INSERT INTO user_feedback_replies (fid, admin_uid, admin_name, content, created_at)
        SELECT id, '', '', reply, COALESCE(replied_at, updated_at, created_at, 0)
          FROM user_feedback
         WHERE reply IS NOT NULL AND TRIM(reply) <> ''
           AND NOT EXISTS (
                 SELECT 1 FROM user_feedback_replies r
                  WHERE r.fid = user_feedback.id AND r.content = user_feedback.reply
               )
        """
    )


def _migrate_columns(conn) -> None:
    """给老库补列（幂等，跑多少次都一样）。"""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(user_feedback)")}
    for name, ddl in _EXTRA_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE user_feedback ADD COLUMN {name} {ddl}")


def ensure() -> None:
    """确保表存在（幂等、惰性，理由同 `core/apilog.ensure`）。"""
    from core import session as store

    path = store._db_path()  # noqa: SLF001
    if path in _READY:
        return
    with store._get_conn() as conn:  # noqa: SLF001
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                uid        TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                category   TEXT NOT NULL,
                content    TEXT NOT NULL,
                contact    TEXT NOT NULL DEFAULT '',
                page       TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'open',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            -- 三个索引：我的反馈（uid+时间）、按状态筛（后台列表）、按时间倒序（后台默认排序）
            CREATE INDEX IF NOT EXISTS idx_uf_uid ON user_feedback(uid, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_uf_status ON user_feedback(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_uf_created ON user_feedback(created_at DESC);
        """)
        _migrate_columns(conn)
        conn.executescript(_REPLIES_DDL)
        _migrate_legacy_replies(conn)
    _READY.add(path)


# ==================== 写 ====================


def _row(row) -> dict:
    d = dict(row)
    d["category_label"] = category_label(d["category"])
    d["status_label"] = status_label(d["status"])
    return d


def add(
    uid: str,
    username: str,
    category: str,
    content: str,
    contact: str = "",
    page: str = "",
) -> int:
    """写入一条反馈，返回 id。校验在 API 层做（这里只管存）。"""
    ensure()
    from core import session as store

    now = int(time.time())
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO user_feedback "
            "(uid, username, category, content, contact, page, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                uid,
                (username or "")[:64],
                category,
                content,
                (contact or "")[:MAX_CONTACT],
                (page or "")[:MAX_PAGE],
                now,
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def set_status(fid: int, status: str) -> tuple[bool, str]:
    """改状态。返回 (成功, 失败原因)。流转非法时返回 409 该用的那句话。"""
    ensure()
    if status not in _STATUS_MAP:
        return False, f"状态不合法：{status}"
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT status FROM user_feedback WHERE id = ?", (fid,)).fetchone()
        if not row:
            return False, f"反馈不存在：{fid}"
        old = str(row["status"])
        if old == status:
            return True, ""  # 幂等：重复点同状态不算错
        if status not in _TRANSITIONS.get(old, set()):
            return False, f"不允许从 {status_label(old)} 改为 {status_label(status)}"
        conn.execute(
            "UPDATE user_feedback SET status = ?, updated_at = ? WHERE id = ?",
            (status, int(time.time()), fid),
        )
        return True, ""
def add_reply(
    fid: int, admin_uid: str, admin_name: str, reply: str
) -> tuple[bool, str]:
    """给一条反馈**追加**一条回复。返回 (成功, 失败原因)。

    ★ 为什么只能追加、不能改也不能撤（2026-11-17 用户定）：
      加了第二个管理员之后，后台是多人共用的。原来的"一格回复、人人可改"
      会让后一个人静默覆盖前一个人的话，也看不出是谁说的。用户的要求是
      「自己发的回复不能被修改，别人只能在我的回复下面增加」—— 所以这里
      只有 INSERT，没有 UPDATE：说错了就再补一条更正，历史留着。

    ★ 为什么回复要顺手改状态：站在提意见的人那边，"有人回我了"就是他判断
      "这条被处理了"的全部依据。回复完状态还停在「待处理」，用户下次打开
      「我提过的」看到的仍是"待处理"，会以为回复是系统自动回的模板。

    ★ 为什么只在 open 时才推进到 doing、且**不走 `_TRANSITIONS` 校验**：
      回复不是"改状态"这个动作，它是另一件事。拿状态流转表去卡它，会得到
      「已处理 → 已处理」这类无意义的 409，让管理员回不了话。所以：
        · open   → 顺手推到 doing（表示"已接手并答复"）
        · 其它状态 → 原样不动（done/closed 已经有结论，不该被回复改写）

    ★ 空文本拒绝（老接口里空串 = 撤回）。不可撤回之后，"发一条空白"既不会
      出现在用户侧、又会在后台占一行，是纯粹的数据噪声。
    """
    ensure()
    text = (reply or "").strip()
    if not text:
        return False, "回复不能为空"
    if len(text) > MAX_REPLY:
        return False, f"回复太长，最多 {MAX_REPLY} 个字"

    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT status FROM user_feedback WHERE id = ?", (fid,)).fetchone()
        if not row:
            return False, f"反馈不存在：{fid}"
        old = str(row["status"])
        now = int(time.time())
        conn.execute(
            "INSERT INTO user_feedback_replies "
            "(fid, admin_uid, admin_name, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (fid, (admin_uid or "")[:64], (admin_name or "")[:64], text, now),
        )
        # 状态与 updated_at 一起推进：列表按 updated_at 排，回完话不动它，
        # 这条工单会沉到列表底部，"刚回过"的反而看不见
        conn.execute(
            "UPDATE user_feedback SET status = ?, updated_at = ? WHERE id = ?",
            ("doing" if old == "open" else old, now, fid),
        )
        return True, ""


# ==================== 读 ====================

def _replies_of(conn, fids: list[int]) -> dict[int, list[dict]]:
    """一次查出这一批反馈的全部回复，按 fid 分桶。

    不做"每行再查一次"（N+1）：后台一页 200 条会打 200 次库，而这个函数
    要跑在 `to_thread` 里、每次刷新都跑。`IN (...)` 的上限是 SQLite 的
    999 个变量，`list_all` 的 limit 封顶 200，够用。
    """
    out: dict[int, list[dict]] = {f: [] for f in fids}
    if not fids:
        return out
    holes = ",".join(["?"] * len(fids))
    rows = conn.execute(
        "SELECT * FROM user_feedback_replies "
        f"WHERE fid IN ({holes}) ORDER BY id ASC",
        fids,
    ).fetchall()
    for r in rows:
        out[int(r["fid"])].append(
            {
                "id": int(r["id"]),
                "admin_uid": str(r["admin_uid"] or ""),
                "admin_name": str(r["admin_name"] or ""),
                "content": str(r["content"] or ""),
                "created_at": int(r["created_at"] or 0),
            }
        )
    return out


def _attach_replies(conn, items: list[dict]) -> list[dict]:
    """给每条反馈挂上 `replies`，并把 `reply` / `replied_at` 合成成**最新一条**。

    兼容别名不是历史包袱，是刻意的：用户侧「我提过的」在旧版本里只认
    `reply` 一个字符串，线上正在跑的包还在用。让两个字段同时成立，
    发版顺序就不用绑死（后端先上、前端后上也不会出现"回了但用户看不到"）。
    """
    bucket = _replies_of(conn, [it["id"] for it in items])
    for it in items:
        reps = bucket.get(it["id"], [])
        it["replies"] = reps
        last = reps[-1] if reps else None
        it["reply"] = last["content"] if last else ""
        it["replied_at"] = last["created_at"] if last else None
    return items


def get(fid: int) -> dict | None:
    """按 id 取一条（管理员回复后回读用：回复可能顺手改了状态，
    前端需要拿到改完的结果，而不是自己猜）。"""
    ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT * FROM user_feedback WHERE id = ?", (fid,)).fetchone()
        if not row:
            return None
        return _attach_replies(conn, [_row(row)])[0]


def list_mine(uid: str, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """某用户自己的反馈（用户侧只关心"我提的有没有人看"）。"""
    ensure()
    from core import session as store

    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    with store._get_conn() as conn:  # noqa: SLF001
        total = conn.execute(
            "SELECT COUNT(*) FROM user_feedback WHERE uid = ?", (uid,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM user_feedback WHERE uid = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (uid, limit, offset),
        ).fetchall()
        items = _attach_replies(conn, [_row(r) for r in rows])
    return items, int(total)


def list_all(
    status: str = "", limit: int = 20, offset: int = 0
) -> tuple[list[dict], int]:
    """后台列表。可选按状态筛。"""
    ensure()
    from core import session as store

    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    where = ""
    args: list = []
    if status and status in _STATUS_MAP:
        where = " WHERE status = ?"
        args.append(status)
    with store._get_conn() as conn:  # noqa: SLF001
        total = conn.execute(f"SELECT COUNT(*) FROM user_feedback{where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM user_feedback{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        items = _attach_replies(conn, [_row(r) for r in rows])
    return items, int(total)


def counts() -> dict:
    """各状态条数。返回的键一定包含字典里全部状态（值为 0），
    否则前端渲染"处理中 0"时会因为缺键而漏一行。"""
    ensure()
    from core import session as store

    out = {k: 0 for k, _ in STATUSES}
    with store._get_conn() as conn:  # noqa: SLF001
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM user_feedback GROUP BY status"
        ):
            out[str(r["status"])] = int(r["n"])
    return out

