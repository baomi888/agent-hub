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

import re
import time
from datetime import datetime

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

# 允许任意状态互转，**包括回退**（2026-11-19 用户明确要求）。
#
# 原来是不允许回退的（done → doing 被 409 拒掉），理由写的是"允许回退会让
# 处理耗时统计失去意义"。但那个统计至今没有实现，而"手滑点成已处理、就再也
# 改不回来"是每天都会发生的事 —— 用户的原话是「误点后也无法更正」。
# 两条路只能选一条：留一个假设中的指标，还是让状态可逆。选了后者。
#
# 注意这里只影响 `set_status`（人工点按钮）；`add_reply` 的"顺手推进"
# 仍走自己的简单判断（open → doing），不拿这张表去卡回复。
_TRANSITIONS: dict[str, set[str]] = {
    s: {o for o in _STATUS_MAP if o != s} for s in _STATUS_MAP
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
        created_at INTEGER NOT NULL,
        role       TEXT NOT NULL DEFAULT 'admin'
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
    # 回复表的 role：老表没有这一列，`CREATE TABLE IF NOT EXISTS` 也不会补。
    # 不补的话，任何一次 INSERT ... role 在**已有数据的库**上直接报 no such column，
    # 而新库一切正常 —— 只有线上会炸（2026-11-19 加用户追加功能时踩到）。
    rep = {r["name"] for r in conn.execute("PRAGMA table_info(user_feedback_replies)")}
    if rep and "role" not in rep:
        conn.execute(
            "ALTER TABLE user_feedback_replies ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'"
        )


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
    fid: int, admin_uid: str, admin_name: str, reply: str, role: str = "admin"
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
      **只对管理员成立**：用户自己补充一句（role="user"）不该被当成"已接手"，
      它只是把话说得更清楚，状态必须原样不动。

    ★ 空文本拒绝（老接口里空串 = 撤回）。不可撤回之后，"发一条空白"既不会
      出现在用户侧、又会在后台占一行，是纯粹的数据噪声。
    """
    ensure()
    text = (reply or "").strip()
    if not text:
        return False, "回复不能为空"
    if len(text) > MAX_REPLY:
        return False, f"回复太长，最多 {MAX_REPLY} 个字"
    kind = "user" if role == "user" else "admin"

    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT status FROM user_feedback WHERE id = ?", (fid,)).fetchone()
        if not row:
            return False, f"反馈不存在：{fid}"
        old = str(row["status"])
        now = int(time.time())
        conn.execute(
            "INSERT INTO user_feedback_replies "
            "(fid, admin_uid, admin_name, content, created_at, role)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fid, (admin_uid or "")[:64], (admin_name or "")[:64], text, now, kind),
        )
        # 状态与 updated_at 一起推进：列表按 updated_at 排，回完话不动它，
        # 这条工单会沉到列表底部，"刚回过"的反而看不见
        conn.execute(
            "UPDATE user_feedback SET status = ?, updated_at = ? WHERE id = ?",
            ("doing" if (kind == "admin" and old == "open") else old, now, fid),
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
                # 老数据没有这一列（迁移补的是默认值 'admin'），所以这里用 getattr
                # 式的取值：sqlite3.Row 支持 keys()，取不到就当管理员。
                "role": str(r["role"] or "admin") if "role" in r.keys() else "admin",
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
        # `reply` / `replied_at` 这两个别名只认**管理员**的回复：
        # 用户自己追加的一句也在这个桶里，取到它就会在「我提过的」里
        # 显示成「开发者回复：<用户自己的话>」—— 语义直接反了。
        admin_reps = [r for r in reps if r.get("role") != "user"]
        last = admin_reps[-1] if admin_reps else None
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


# ==================== 导出（2026-11-18）====================
#
# 导出的两个前置条件写在 api/admin.py 的旧注里，这里逐条兑现：
#   ① **脱敏**：联系方式打码、uid 只留前 8 位。导出的文件会被转发到群里、
#      塞进申报材料 —— 一旦原样带走手机号，收不回来。
#   ② **审计**：谁在什么时候导了多少条，落到 `data/feedback_export_audit.jsonl`。
#      不用建表：导出是低频动作，JSONL 追加不会失败、也不会引入懒迁移那种
#      "第一次跑才建表"的坑（硬约束 57）。
#
# 格式选 Markdown 而不是 CSV/Word：需求原话是「已排版好的文字」。CSV 打开是
# 一堆裸列名，还得再排一次版；Markdown 粘进 Word/飞书/微信都保留结构，
# 又是纯文本（可 diff、可在任何编辑器里改）。真需要 Word，另做一步转换即可，
# 不该让导出这一个动作背上 docx 依赖。


_EXPORT_ORDER = [k for k, _ in STATUSES]


def _fmt_ts(ts) -> str:
    """时间戳 → 业务时区的 `YYYY-MM-DD HH:MM`。没值时留空，不写 1970。"""
    if not ts:
        return ""
    from core import timeutil

    return datetime.fromtimestamp(int(ts), timeutil.TZ).strftime("%Y-%m-%d %H:%M")


def mask_contact(raw: str) -> str:
    """联系方式脱敏。手机/QQ 留头 3 尾 2，邮箱留首字母，其余一律打码。

    为什么留尾巴：反馈里同一个人的两条意见要对得上号，全打空就没法对。
    留 3+2 位既够对上号，又不足以拼出完整号码。
    """
    s = (raw or "").strip()
    if not s:
        return "（未留）"
    if "@" in s:
        name, _, domain = s.partition("@")
        head = name[:1] if name else ""
        return f"{head}***@{domain}"
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 7:
        return f"{digits[:3]}****{digits[-2:]}"
    return s[:1] + "***"


def export_items(status: str = "") -> list[dict]:
    """导出用的全量取数（不分页）。按创建时间**正序**：导出件是拿来通读的，
    先提的排前面才读得出"这个问题积了多久"。"""
    ensure()
    from core import session as store

    where = ""
    args: list = []
    if status and status in _STATUS_MAP:
        where = " WHERE status = ?"
        args.append(status)
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            f"SELECT * FROM user_feedback{where} ORDER BY created_at ASC, id ASC", args
        ).fetchall()
        return _attach_replies(conn, [_row(r) for r in rows])


def _summary_matrix(items: list[dict]) -> str:
    """分类 × 状态 的计数表。企业反馈汇总的第一屏都是这张表 ——
    它回答的是"哪一类问题最多、还有多少没处理"，比逐条读快得多。"""
    lines = [
        "| 分类 | " + " | ".join(status_label(s) for s in _EXPORT_ORDER) + " | 合计 |",
        "| --- | " + " | ".join("--:" for _ in _EXPORT_ORDER) + " | --: |",
    ]
    for cat, cat_label in CATEGORIES:
        cells = []
        total = 0
        for st in _EXPORT_ORDER:
            n = sum(1 for it in items if it["category"] == cat and it["status"] == st)
            total += n
            cells.append(str(n) if n else "-")
        if total:
            lines.append(f"| {cat_label} | " + " | ".join(cells) + f" | {total} |")
    grand = len(items)
    per_status = [sum(1 for it in items if it["status"] == st) for st in _EXPORT_ORDER]
    lines.append(
        "| **合计** | "
        + " | ".join(f"**{n}**" if n else "-" for n in per_status)
        + f" | **{grand}** |"
    )
    return "\n".join(lines)


def export_markdown(status: str = "", now: int | None = None) -> tuple[str, dict]:
    """生成可直接发出的反馈汇总（Markdown）。返回 (正文, 元信息)。

    元信息给调用方写审计用：`{items, total, status}` —— 不在这里写文件，
    因为 core 层不该知道日志目录在哪（部署目录与测试临时目录不同）。
    """
    items = export_items(status)
    per_status = {st: sum(1 for it in items if it["status"] == st) for st in _EXPORT_ORDER}
    stamp = _fmt_ts(now or int(time.time()))
    scope = status_label(status) if status in _STATUS_MAP else "全部状态"

    out: list[str] = []
    out.append("# 知穗 · 用户反馈汇总")
    out.append("")
    out.append(f"- 导出时间：{stamp}")
    out.append(f"- 数据范围：{scope} · 全部时间")
    out.append(
        "- 合计："
        + f"{len(items)} 条（"
        + " / ".join(f"{status_label(st)} {per_status[st]}" for st in _EXPORT_ORDER)
        + "）"
    )
    out.append("- 联系方式已脱敏；账号只保留前 8 位。")
    out.append("")

    if not items:
        out.append("> 该范围内没有反馈。")
        return "\n".join(out) + "\n", {"items": 0, "total": 0, "status": status}

    out.append("## 统计概览")
    out.append("")
    out.append(_summary_matrix(items))
    out.append("")

    seq = 0
    for st in _EXPORT_ORDER:
        group = [it for it in items if it["status"] == st]
        if not group:
            continue
        out.append(f"## {status_label(st)}（{len(group)} 条）")
        out.append("")
        for it in group:
            seq += 1
            out.append(f"### {seq}. {it['category_label']}　#{it['id']}")
            out.append("")
            who = (it.get("username") or "").strip() or "（未命名）"
            uid = str(it.get("uid") or "")
            out.append(f"- 提交人：{who}（{uid[:8]}…）· {_fmt_ts(it.get('created_at'))}")
            if it.get("page"):
                out.append(f"- 来源页面：{it['page']}")
            # 联系方式**总是写出来**（没留就写「（未留）」）：导出件是交给别人
            # 读的表格，"这一栏空了"和"我们没导这一栏"在读者眼里是两件事
            out.append(f"- 联系方式：{mask_contact(it.get('contact') or '')}")
            out.append("")
            # 正文按原样引用：用户写了几行就几行，不要压成一句
            for line in str(it.get("content") or "").splitlines() or [""]:
                out.append(f"> {line}" if line.strip() else ">")
            out.append("")
            replies = it.get("replies") or []
            if replies:
                out.append("**处理记录**")
                out.append("")
                for r in replies:
                    author = (r.get("admin_name") or "").strip() or "管理员"
                    out.append(f"- {_fmt_ts(r.get('created_at'))} · {author}：{r['content']}")
                out.append("")
    return "\n".join(out) + "\n", {"items": len(items), "total": len(items), "status": status}


# ==================== 按人会话（2026-11-18）====================
#
# 为什么后台要从"按条平铺"改成"按人分会话"（用户原话：像微信的回复）：
#
#   旧列表里，同一个用户提了 8 条就是 8 行长得一样的文字。读的人分不清
#   "这是一个人的连续抱怨"还是"8 个人在说同一个 bug" —— 这两种情况的处置
#   完全不同，一个要私下聊，一个要修代码。而且对话是碎片的：每条下面各挂
#   一段回复，用户那边看到的是 8 个不相干的片段，而不是"他们在跟我说话"。
#
#   IM 的形态本来就回答了这两个问题：左边列表是"谁在说话、说到哪了"，
#   右边是"我和他"这一整段。所以这里只做一件事 —— 把 uid 当成会话键，
#   把 feedback 当用户消息、replies 当管理员消息，按时间合流。
#
# ★ 不新建表：会话不是实体，它是**同 uid 的反馈**的另一种读法。
#   建一张 threads 表就得维护"最后一条消息"这一列的一致性（回复、状态、
#   删除三处都要同步），而它随时能从两张表算出来。

PREVIEW_CHARS = 46


def _brief(conn, uids: list[str]) -> dict[str, dict]:
    """uid → 昵称/头像/用户名。会话列表和对话头部都要用。

    一次 `IN (...)` 查完，不做 N+1。缺号（用户已注销）返回空 dict 由调用方
    兜底 —— 注销账号的反馈仍然要能读，那是历史。
    """
    if not uids:
        return {}
    marks = ",".join("?" * len(uids))
    out: dict[str, dict] = {}
    for r in conn.execute(
        f"SELECT id, username, nickname, avatar FROM users WHERE id IN ({marks})", uids
    ):
        out[str(r["id"])] = {
            "username": str(r["username"] or ""),
            "nickname": str(r["nickname"] or ""),
            "avatar": str(r["avatar"] or ""),
        }
    return out


def _who(brief: dict) -> str:
    """显示名：昵称优先，退用户名。两者都没有就是「已注销用户」。"""
    return brief.get("nickname") or brief.get("username") or ""


def threads(limit: int = 60, status: str = "") -> dict:
    """会话列表：每个 uid 一条，按最后活动时间倒序。

    `status` 过滤的是"这个人名下有这个状态的反馈"，不是"只显示这条反馈" ——
    筛「待处理」时出现的人，点进去仍然能看到他全部的话（否则读的人会以为
    他总共就说过一句）。这也是 IM 与工单列表的区别。
    """
    ensure()
    from core import session as store

    limit = max(1, min(200, int(limit)))
    with store._get_conn() as conn:  # noqa: SLF001
        where, args = "", []
        if status in _STATUS_MAP:
            # EXISTS 而不是 WHERE status = ?：后者会把这个人其它状态的反馈滤掉，
            # 聚合出来的条数和"未处理数"都会偏小
            where = (
                " WHERE EXISTS (SELECT 1 FROM user_feedback x"
                " WHERE x.uid = user_feedback.uid AND x.status = ?)"
            )
            args = [status]
        rows = conn.execute(
            "SELECT uid, COUNT(*) AS n,"
            " SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_n,"
            " SUM(CASE WHEN status = 'doing' THEN 1 ELSE 0 END) AS doing_n,"
            " GROUP_CONCAT(DISTINCT category) AS cats,"
            " MAX(created_at) AS user_at"
            f" FROM user_feedback{where} GROUP BY uid ORDER BY user_at DESC LIMIT ?",
            [*args, limit],
        ).fetchall()
        if not rows:
            return {"items": [], "total": 0}
        # 总人数要单独数：上面那条 LIMIT 之后只剩一页，
        # 拿 len(rows) 当"有多少人在反馈"会让列表头写「20 人」，实际有 47 个。
        people = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT uid) FROM user_feedback{where}", args
            ).fetchone()[0]
        )

        uids = [str(r["uid"]) for r in rows]
        marks = ",".join("?" * len(uids))
        brief = _brief(conn, uids)
        # 这批人的全部消息（反馈是几十条的量级，一次取完比 N 次"取最新一条"便宜）
        feed = conn.execute(
            "SELECT id, uid, content, created_at, category, status"
            f" FROM user_feedback WHERE uid IN ({marks}) ORDER BY created_at DESC, id DESC",
            uids,
        ).fetchall()
        rep = conn.execute(
            "SELECT r.fid, r.content, r.created_at, r.admin_name, f.uid"
            " FROM user_feedback_replies r JOIN user_feedback f ON f.id = r.fid"
            f" WHERE f.uid IN ({marks}) ORDER BY r.created_at DESC, r.id DESC",
            uids,
        ).fetchall()

    # 每个 uid 的最后一条消息（含回复），以及最后活动时间
    #
    # ★ 同一秒内**回复赢**：先跑回复、用 `>=`，再跑反馈、用 `>`。
    #   反过来的话，"用户刚提交、管理员当场回一句"这两件事 time() 是同秒，
    #   列表预览会停在用户的原话上 —— 而管理员明明已经回了
    #   （2026-11-18 测试抓到的，肉眼很难发现：只有同一秒才会出现）。
    last: dict[str, dict] = {}
    for r in rep:
        uid = str(r["uid"])
        cur = last.get(uid)
        if cur is None or int(r["created_at"]) >= cur["at"]:
            last[uid] = {
                "at": int(r["created_at"]),
                "from": "admin",
                "text": str(r["content"] or ""),
                "name": str(r["admin_name"] or "") or "管理员",
            }
    for r in feed:
        uid = str(r["uid"])
        cur = last.get(uid)
        if cur is None or int(r["created_at"]) > cur["at"]:
            last[uid] = {
                "at": int(r["created_at"]),
                "from": "user",
                "text": str(r["content"] or ""),
                "name": "",
            }

    items = []
    for r in rows:
        uid = str(r["uid"])
        b = brief.get(uid, {})
        lm = last.get(uid) or {"at": int(r["user_at"] or 0), "from": "user", "text": "", "name": ""}
        text = " ".join(lm["text"].split())
        items.append(
            {
                "uid": uid,
                "username": b.get("username", ""),
                "nickname": b.get("nickname", ""),
                "avatar": b.get("avatar", ""),
                "name": _who(b) or f"用户 {uid[:6]}",
                "n": int(r["n"]),
                "open_n": int(r["open_n"] or 0),
                "doing_n": int(r["doing_n"] or 0),
                "last_at": lm["at"],
                "last_from": lm["from"],
                "last_by": lm["name"],
                "preview": text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else ""),
                "categories": [c for c in str(r["cats"] or "").split(",") if c],
            }
        )
    items.sort(key=lambda x: x["last_at"], reverse=True)
    return {"items": items, "total": people}


def thread(uid: str, limit: int = 200) -> dict:
    """一个用户的全部反馈，两种读法一起返回（2026-11-19 起）。

    · `items`    —— **按条分组**：每条反馈一行，带着自己的状态和它的回复链。
                    后台就是照着这个渲染的：一条反馈一张卡，卡里自己带状态
                    控件和回复框。这是用户要的"点哪条回哪条"的数据基础。
    · `messages` —— **全局按时间**平铺，给"想从头读这段对话"的场景保留。
                    注意它不是 `items` 展开（顺序不同），两套都算出来是因为
                    它们是两个问题，不是同一份数据的两种写法。

    ★ 为什么分组不能靠前端算：`messages` 是全局时间序，按 fid 分组会丢掉
      "这条反馈下每条回复的先后"，而回复的先后就是"我答了、他又追问、
      我再答"的全部信息。后端手里有 fid 和 id，一次分组成本为零。
    """
    ensure()
    from core import session as store

    rows, total = list_mine(uid, max(1, min(200, int(limit))), 0)
    rows.reverse()  # list_mine 是倒序，读起来要从最早一条开始

    messages: list[dict] = []
    tickets: list[dict] = []
    for it in rows:
        fid = int(it["id"])
        base = {
            "fid": fid,
            "at": int(it["created_at"] or 0),
            "text": str(it["content"] or ""),
            "category": it["category"],
            "category_label": it["category_label"],
            "status": it["status"],
            "status_label": it["status_label"],
            "page": it.get("page") or "",
            "contact": it.get("contact") or "",
        }
        messages.append({"kind": "user", **base})
        chain: list[dict] = []
        for r in it.get("replies") or []:
            is_user = str(r.get("role") or "admin") == "user"
            msg = {
                # 用户自己追加的一句也是"左边"，但它不是一条新反馈 ——
                # 前端要靠 user_reply 区分"这条工单里我又补了一句"和"这是另一个问题"。
                "kind": "user_reply" if is_user else "admin",
                "fid": fid,
                "rid": int(r["id"]),
                "at": int(r.get("created_at") or 0),
                "text": str(r["content"] or ""),
                "author": "" if is_user else (r.get("admin_name") or "管理员"),
                "author_uid": "" if is_user else (r.get("admin_uid") or ""),
            }
            messages.append(msg)
            chain.append(msg)
        tickets.append({**base, "replies": chain})

    # ★ 必须**全局**按时间排，不能按"一条反馈 + 它的回复"成组输出。
    #   回复挂在那条反馈下面，但它的时间可能晚于这个人后来提的第二条反馈 ——
    #   成组输出会得到「A 的提问 → 对 A 的回复 → B 的提问」，可 B 明明早于那条回复，
    #   读起来像管理员在回答 B 的时候还没看到 B（实测过，顺序是错的）。
    #   同一秒内用户先说、管理员后答：kind 做二级键。
    order = {"user": 0, "user_reply": 0, "admin": 1}
    messages.sort(key=lambda m: (m["at"], order.get(m["kind"], 0), m["fid"]))
    with store._get_conn() as conn:  # noqa: SLF001
        b = _brief(conn, [uid]).get(uid, {})
    return {
        "uid": uid,
        "name": _who(b) or f"用户 {uid[:6]}",
        "username": b.get("username", ""),
        "nickname": b.get("nickname", ""),
        "avatar": b.get("avatar", ""),
        "total": int(total),
        "items": tickets,
        "messages": messages,
    }


def reply_thread(
    uid: str, admin_uid: str, admin_name: str, text: str, fid: int | None = None
) -> tuple[bool, str]:
    """在会话里回一句：默认挂到这个人**最新一条**反馈下面，也可以指定 fid。

    为什么不给会话单开一张消息表：现有的回复表已经带 fid、作者、时间，
    它缺的只是"这条回复属于哪段对话"—— 而那段对话就是 fid 对应的 uid。
    为此再加一张平行的表，等于让同一件事有两个写入口，迟早一边有数据
    一边没有（老库迁移还得把两边对齐）。

    ★ 为什么要能指定 fid（2026-11-19 用户要求）：一个人常常提三条互不相干
      的意见（"某个按钮坏了" / "想加日历视图" / "论坛能加 AI 总结吗"）。
      回复统一挂在最新一条上，另外两条永远等不到回答，而且用户看到的
      "已回复"会出现在他没问的那条下面 —— 这正是他要的"引用并直接回复"。
      指定 fid 时**必须校验它属于这个 uid**：不然给 A 会话发的消息会写进
      B 的反馈里，越权且无法撤回（回复不可改）。

    ★ 不指定 fid 时仍挂最新一条：老调用方（前端旧代码、导出脚本）不变，
      而"用户最后说的话才是他此刻在等的那句"这个判断依然成立。
    """
    ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        if fid is None:
            row = conn.execute(
                "SELECT id FROM user_feedback WHERE uid = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (uid,),
            ).fetchone()
            if not row:
                return False, "这个用户没有反馈"
            target = int(row["id"])
        else:
            row = conn.execute(
                "SELECT id FROM user_feedback WHERE id = ? AND uid = ?", (int(fid), uid)
            ).fetchone()
            if not row:
                return False, "这条反馈不属于该用户"
            target = int(fid)
    return add_reply(target, admin_uid, admin_name, text, role="admin")


def reply_own(uid: str, fid: int, text: str) -> tuple[bool, str]:
    """用户在自己某条反馈下**追加**一句（补充说明、补充截图地址等）。

    ★ 与 `add_reply` 的区别只有 role：不动状态、不署名（署的是用户自己）。
      复用同一张表而不是另开"用户消息表"：同一条反馈下的时间线只有一个来源，
      读的时候按 created_at 排就行；分两张表就得在查询里做归并排序，
      而这件事已经因为"成组输出"出过一次错（2026-11-18）。
    """
    ensure()
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT uid FROM user_feedback WHERE id = ?", (int(fid),)
        ).fetchone()
        if not row:
            return False, "反馈不存在"
        if str(row["uid"]) != uid:
            return False, "只能回复自己提的反馈"
    return add_reply(int(fid), uid, "", text, role="user")


def stats(days: int = 30) -> dict:
    """图表数据：分类分布（区间内新增）、状态积压（全量）、逐日新增（区间）。

    三个口径刻意不同，因为它们回答三个不同的问题：
      · 分类 —— "最近大家在抱怨什么"（只看区间，否则会被半年前的数据淹没）
      · 状态 —— "现在手上有多少没处理"（必须是全量，区间内的积压没有意义）
      · 趋势 —— "量在涨还是在落"（区间内的每日新增）
    前端在每张图上标出口径，不靠读者猜。
    """
    ensure()
    from core import session as store
    from core import timeutil

    days = max(1, min(365, int(days)))
    window = timeutil.recent_days(days)
    start = timeutil.day_bounds(window[0])[0]
    end = timeutil.day_bounds(window[-1])[1]

    by_cat = {k: 0 for k, _ in CATEGORIES}
    by_status = {k: 0 for k, _ in STATUSES}
    series = {d: 0 for d in window}
    total_all = 0
    with store._get_conn() as conn:  # noqa: SLF001
        for r in conn.execute(
            "SELECT category, COUNT(*) AS n FROM user_feedback"
            " WHERE created_at >= ? AND created_at < ? GROUP BY category",
            (start, end),
        ):
            by_cat[str(r["category"])] = int(r["n"])
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM user_feedback GROUP BY status"
        ):
            by_status[str(r["status"])] = int(r["n"])
        total_all = int(
            conn.execute("SELECT COUNT(*) FROM user_feedback").fetchone()[0]
        )
        for r in conn.execute(
            "SELECT created_at FROM user_feedback WHERE created_at >= ? AND created_at < ?",
            (start, end),
        ):
            d = timeutil.day_str(int(r["created_at"]))
            if d in series:
                series[d] += 1

    return {
        "days": days,
        "range": {"from": window[0], "to": window[-1]},
        "total_all": total_all,
        "in_range": sum(series.values()),
        "by_category": [
            {"value": k, "label": category_label(k), "n": by_cat.get(k, 0)}
            for k, _ in CATEGORIES
        ],
        "by_status": [
            {"value": k, "label": status_label(k), "n": by_status.get(k, 0)}
            for k, _ in STATUSES
        ],
        "series": [{"day": d, "n": series[d]} for d in window],
    }

