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
    # 开发者给这条反馈的回复。空串 = 还没回。
    # 存在反馈行上而不是单开一张 replies 表：一个工单一次正式回复就够了，
    # 多轮对话式的来回是"客服系统"的形态，这里不是。
    "reply": "TEXT NOT NULL DEFAULT ''",
    "replied_at": "INTEGER",
}


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
def set_reply(fid: int, reply: str) -> tuple[bool, str]:
    """给一条反馈写回复。返回 (成功, 失败原因)。

    ★ 为什么回复要顺手改状态：站在提意见的人那边，"有人回我了"就是他判断
      "这条被处理了"的全部依据。回复完状态还停在「待处理」，用户下次打开
      「我提过的」看到的仍是"待处理"，会以为回复是系统自动回的模板。

    ★ 为什么只在 open 时才推进到 doing、且**不走 `_TRANSITIONS` 校验**：
      回复不是"改状态"这个动作，它是另一件事。拿状态流转表去卡它，会得到
      「已处理 → 已处理」这类无意义的 409，让管理员回不了话。所以：
        · open   → 顺手推到 doing（表示"已接手并答复"）
        · 其它状态 → 原样不动（done/closed 已经有结论，不该被回复改写）

    清空回复（传空串）是合法的：回错了要能撤回，撤回后状态不回退
    （状态流转本身不允许回退，见 `_TRANSITIONS` 的注释）。
    """
    ensure()
    text = (reply or "").strip()
    if len(text) > MAX_REPLY:
        return False, f"回复太长，最多 {MAX_REPLY} 个字"

    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT status FROM user_feedback WHERE id = ?", (fid,)).fetchone()
        if not row:
            return False, f"反馈不存在：{fid}"
        old = str(row["status"])
        now = int(time.time())
        new_status = "doing" if old == "open" and text else old
        conn.execute(
            "UPDATE user_feedback SET reply = ?, replied_at = ?, status = ?, updated_at = ? "
            "WHERE id = ?",
            (text, now if text else None, new_status, now, fid),
        )
        return True, ""


# ==================== 读 ====================

def get(fid: int) -> dict | None:
    """按 id 取一条（管理员回复后回读用：回复可能顺手改了状态，
    前端需要拿到改完的结果，而不是自己猜）。"""
    ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT * FROM user_feedback WHERE id = ?", (fid,)).fetchone()
    return _row(row) if row else None


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
    return [_row(r) for r in rows], int(total)


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
    return [_row(r) for r in rows], int(total)


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

