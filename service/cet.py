"""四六级备考工作区的业务逻辑。

为什么单独建一个 service：
  申报书里写「四六级备考模块已落地」，但此前实现只有「模板卡片 + 对话话题词」，
  没有可点进去的模块。这里把三件事沉淀成真实数据，而不是每次让模型重新生成：
    1. 54 天备考计划 —— 计划一旦生成即落库，任务可勾选、进度可追踪（不再是一次性文本）；
    2. 词汇本 —— 错词按 SRS（1/3/7/15/30 天）间隔复习，复习记录落库；
    3. 模考分数 —— 记录每次模考/真考分数，画出趋势与目标差距。

设计对齐 service/checkin.py：
  - 复用同一个 SQLite（sessions.db），新表懒建、幂等、**不在导入期写库**；
  - owner 强制隔离：所有查询 WHERE owner_id = uid；
  - 不引入排行榜/竞争性激励（与打卡页同一取舍）。
"""

from __future__ import annotations

import datetime
import time
import uuid
from typing import Any

from core import quota
from core import session as store

# ---------------------------------------------------------------------------
# 备考档案的配置项（2026-10-27 由"写死模板"改为"可自定义档案"）
#
# 原来这里是一个 `_MODULES` 常量，5 个模块的任务文案全部写死，谁打开都是同一套。
# 内测反馈："连选四级还是六级都不能选，时间啥的也不能选"。
# 现在拆成三层：模块名（用户勾选）/ 强度（决定任务量）/ 文案模板。
# ---------------------------------------------------------------------------

# 模块名是**唯一标识**，同时用于 cet_tasks.module 列和 cet_plans.modules 串。
# 顺序即展示顺序，不要随意调换（前端按这个顺序渲染勾选项）。
MODULE_NAMES: tuple[str, ...] = ("听力", "词汇", "阅读", "写作翻译", "真题")

# 用户可能这么叫，归一成上面的标准名。归一失败的模块名直接忽略（不报错）。
_MODULE_ALIASES = {
    "写作": "写作翻译",
    "翻译": "写作翻译",
    "作文": "写作翻译",
    "写译": "写作翻译",
    "单词": "词汇",
    "背单词": "词汇",
}

# 强度 → 每日任务文案。`{day}` 是第几天（1 起），没有占位符的模板也不要紧
# （str.format 只替换出现的占位符）。
_MODULE_CATALOG: dict[str, dict[str, str]] = {
    "听力": {
        "light": "听力精听 1 组（约 10 分钟）",
        "standard": "听力同义改写 第 {day} 组（每日 10–15 组）",
        "heavy": "听力精听 2 组 + 复盘错题",
    },
    "词汇": {
        "light": "高频词 15 个",
        "standard": "高频词 30 个 + 昨日错词复盘",
        "heavy": "高频词 50 个 + 昨日错词复盘",
    },
    "阅读": {
        "light": "真题阅读 1 篇",
        "standard": "真题阅读 2 篇 + 长难句 3 句",
        "heavy": "真题阅读 3 篇 + 长难句 6 句",
    },
    "写作翻译": {
        "light": "作文或翻译（二选一）",
        "standard": "作文 1 篇 或 翻译 1 段（二选一）",
        "heavy": "作文 1 篇 + 翻译 1 段",
    },
    "真题": {
        "light": "真题 1 套（按周推进）",
        "standard": "真题限时 1 套（按套推进）",
        "heavy": "真题 1 套 + 错题精析",
    },
}

INTENSITIES: tuple[str, ...] = ("light", "standard", "heavy")
_INTENSITY_LABELS = {"light": "轻量", "standard": "标准", "heavy": "加强"}
EXAM_TYPES: tuple[str, ...] = ("CET4", "CET6")
_EXAM_LABELS = {"CET4": "四级", "CET6": "六级"}
# 目标分推荐（官方及格线都是 425，但六级习惯上给个更高的默认值）
_DEFAULT_TARGET = {"CET4": 425, "CET6": 500}

# 计划天数上下限。上限刻意给到 400 而不是 180：四六级一年两考，最长间隔约 6 个月，
# 用户选"下一次考试"可能就是 180 天开外，卡在 180 会把人挡在门外。
_MIN_DAYS, _MAX_DAYS = 1, 400

# 老库补列用：列名 → DDL（全部带默认值，因此旧行迁移后即是合理初值）
_PLAN_NEW_COLUMNS: dict[str, str] = {
    "exam_type": "TEXT NOT NULL DEFAULT 'CET6'",
    "target_score": "INTEGER",
    "modules": "TEXT NOT NULL DEFAULT '听力,词汇,阅读,写作翻译,真题'",
    "intensity": "TEXT NOT NULL DEFAULT 'standard'",
    "archived_at": "INTEGER",
}

# SRS 间隔（天）：答对前进一档，答错回到第 1 档
_SRS_STEPS = [1, 3, 7, 15, 30]


def _ensure_cet_tables() -> None:
    """懒建四张表（复用 sessions.db 连接，幂等）。

    新列统一走 `_migrate_plan_columns` 补，**不在 CREATE TABLE 里写** ——
    否则新库有列、老库没列，两条路径要各维护一份 DDL，改一处漏一处。
    现在只有一条路径：先建表（老口径），再查 PRAGMA 补齐缺的列。
    """
    with store._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cet_plans (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                title       TEXT NOT NULL,
                start_date  TEXT NOT NULL,
                exam_date   TEXT,
                total_days  INTEGER NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cet_plans_owner ON cet_plans(owner_id, created_at);

            CREATE TABLE IF NOT EXISTS cet_tasks (
                id          TEXT PRIMARY KEY,
                plan_id     TEXT NOT NULL,
                owner_id    TEXT NOT NULL,
                day_index   INTEGER NOT NULL,
                task_date   TEXT NOT NULL,
                module      TEXT NOT NULL,
                title       TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                done_at     INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_cet_tasks_owner_date ON cet_tasks(owner_id, task_date);
            """
        )
        _migrate_plan_columns(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vocab_items (
                id           TEXT PRIMARY KEY,
                owner_id     TEXT NOT NULL,
                word         TEXT NOT NULL,
                meaning      TEXT,
                source       TEXT,
                step         INTEGER NOT NULL DEFAULT 0,
                interval_days INTEGER NOT NULL DEFAULT 1,
                next_review  TEXT NOT NULL,
                wrong_count  INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL,
                last_review  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_vocab_owner_due ON vocab_items(owner_id, next_review);

            CREATE TABLE IF NOT EXISTS cet_scores (
                id         TEXT PRIMARY KEY,
                owner_id   TEXT NOT NULL,
                exam_type  TEXT NOT NULL,
                score      INTEGER NOT NULL,
                target     INTEGER,
                taken_at   TEXT NOT NULL,
                note       TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cet_scores_owner ON cet_scores(owner_id, taken_at);
            """
        )


def _migrate_plan_columns(conn: Any) -> None:
    """给 cet_plans 补 5 个配置列（幂等，跑几次都一样）。

    做法与 `service/daily.py` 给 delegations 补 last_error 的一致：
    先 PRAGMA 查现有列，缺哪个 ALTER 哪个。**不写迁移脚本、不停机**，
    老库第一次被访问时自动补齐。
    """
    have = {str(r["name"]) for r in conn.execute("PRAGMA table_info(cet_plans)").fetchall()}
    for name, ddl in _PLAN_NEW_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE cet_plans ADD COLUMN {name} {ddl}")


def _add_days(date_str: str, n: int) -> str:
    return (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- 计划


def _normalize_modules(raw: Any) -> list[str]:
    """把用户传的模块归一成标准名，去重保序。

    非法名字**忽略而不是报错** —— 前端版本不一致时多传一个旧模块名，
    不该让整个请求失败。但归一后一个不剩就必须报错：一份没有任务的计划毫无意义。
    """
    if raw is None:
        return list(MODULE_NAMES)
    if isinstance(raw, str):
        parts = raw.replace("，", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        parts = []
    out: list[str] = []
    for it in parts:
        name = _MODULE_ALIASES.get(str(it).strip(), str(it).strip())
        if name in MODULE_NAMES and name not in out:
            out.append(name)
    if not out:
        raise ValueError("至少选择一个练习模块")
    return out


def _normalize_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().lower()
    for a in allowed:
        if a.lower() == v:
            return a
    return default


def _parse_date(value: Any) -> str | None:
    """宽松解析 yyyy-mm-dd；解析不了当"没给"处理（不报错，走默认）。"""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _normalize_target(value: Any, level: str) -> int:
    """目标分：没给就用该级别的推荐值；给了就夹在 1–710（四六级满分 710）。"""
    if value is None or str(value).strip() == "":
        return _DEFAULT_TARGET[level]
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("目标分必须是整数") from None
    if not 0 < n <= 710:
        raise ValueError("目标分应在 1–710 之间")
    return n


def _task_rows(
    plan_id: str, uid: str, start: str, total_days: int, modules: list[str], intensity: str
) -> list[tuple]:
    """按 (天数 × 选中模块) 生成任务行。只生成用户勾选的模块。"""
    rows: list[tuple] = []
    for i in range(total_days):
        d = _add_days(start, i)
        for m in modules:
            tpl = _MODULE_CATALOG.get(m, {}).get(intensity) or m
            rows.append(
                (uuid.uuid4().hex, plan_id, uid, i + 1, d, m, tpl.format(day=i + 1), 0, None)
            )
    return rows


def _plan_public(row: Any) -> dict[str, Any]:
    """档案行 → 前端可用的 dict（模块串拆成数组，补中文标签）。"""
    mods = [x.strip() for x in str(row["modules"] or "").replace("，", ",").split(",")]
    mods = [m for m in (_MODULE_ALIASES.get(x, x) for x in mods if x) if m in MODULE_NAMES]
    if not mods:  # 老库默认值缺失时的兜底，保证前端拿到的永远是可渲染的数组
        mods = list(MODULE_NAMES)
    level = str(row["exam_type"] or "CET6")
    inten = str(row["intensity"] or "standard")
    return {
        "id": str(row["id"]),
        "title": str(row["title"] or ""),
        "start_date": str(row["start_date"]),
        "exam_date": row["exam_date"],
        "total_days": int(row["total_days"]),
        "exam_type": level,
        "exam_type_label": _EXAM_LABELS.get(level, level),
        "target_score": row["target_score"],
        "modules": mods,
        "intensity": inten,
        "intensity_label": _INTENSITY_LABELS.get(inten, "标准"),
    }


def create_plan(
    uid: str,
    title: str | None = None,
    exam_date: Any = None,
    days: Any = 54,
    start_date: str | None = None,
    exam_type: Any = "CET6",
    modules: Any = None,
    intensity: Any = "standard",
    target_score: Any = None,
) -> dict[str, Any]:
    """建立一份备考档案并按配置生成每日任务。

    计划是**确定性生成**的：不调用大模型，同一输入结果稳定、可测试，也不消耗 token。
    模型的角色是「解释与调整」，不是「每次重新造计划」。

    `exam_date` 与 `days` 的优先级：**exam_date 优先**（用户关心的是"12 月 12 号考试"，
    而不是"我要 54 天"）；只给 days 时才反算考试日，保持旧调用的行为不变。

    已有生效中的档案会先被**归档**（不是硬删）—— 否则重复建档案会不断堆积任务行，
    而 `_latest_plan` 只取最新那份，旧的全成查不到的垃圾。
    """
    _ensure_cet_tables()
    start = _parse_date(start_date) or quota.day_key()
    mods = _normalize_modules(modules)
    inten = _normalize_choice(intensity, INTENSITIES, "standard")
    level = _normalize_choice(exam_type, EXAM_TYPES, "CET6")

    exam = _parse_date(exam_date)
    if exam is not None:
        span = (datetime.date.fromisoformat(exam) - datetime.date.fromisoformat(start)).days + 1
        if span < 1:
            raise ValueError("考试日不能早于今天")
        if span > _MAX_DAYS:
            raise ValueError(f"备考周期最长 {_MAX_DAYS} 天，请选近一点的考试日期")
        total_days = span
    else:
        try:
            total_days = int(days)
        except (TypeError, ValueError):
            total_days = 54
        total_days = max(_MIN_DAYS, min(total_days, _MAX_DAYS))
        exam = _add_days(start, total_days - 1)

    target = _normalize_target(target_score, level)
    title = (str(title or "").strip() or f"{_EXAM_LABELS[level]}备考计划")[:60]
    plan_id = uuid.uuid4().hex
    now = int(time.time())
    rows = _task_rows(plan_id, uid, start, total_days, mods, inten)
    with store._get_conn() as conn:
        # 旧档案整体退役（含任务保留），新档案立刻生效
        conn.execute(
            "UPDATE cet_plans SET archived_at = ? WHERE owner_id = ? AND archived_at IS NULL",
            (now, uid),
        )
        conn.execute(
            "INSERT INTO cet_plans (id, owner_id, title, start_date, exam_date, total_days, "
            "created_at, exam_type, target_score, modules, intensity, archived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (plan_id, uid, title, start, exam, total_days, now, level, target, ",".join(mods), inten),
        )
        conn.executemany(
            "INSERT INTO cet_tasks (id, plan_id, owner_id, day_index, task_date, module, title, done, done_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        created = conn.execute("SELECT * FROM cet_plans WHERE id = ?", (plan_id,)).fetchone()
    out = _plan_public(created)
    out["created_tasks"] = len(rows)
    return out


def _resync_tasks(
    conn: Any,
    plan_id: str,
    uid: str,
    start: str,
    exam: str,
    modules: list[str],
    intensity: str,
    today: str,
) -> dict[str, int]:
    """把「今天及以后」的任务对齐到新的 (考试日, 模块, 强度)。**历史一律不动。**

    一个函数同时处理三种情况：
      1. 区间变长（考试日推后）→ 补出新日期的任务；
      2. 模块被取消 → 删掉该模块**未完成**的任务；
      3. 考试日提前 → 删掉超出区间的**未完成**任务。

    ★ 已完成的任务即便不在新区间里也**保留**：那是已经发生过的事实，
      删掉就等于篡改用户的打卡记录（还会连带打坏连续天数）。
    ★ 起点取 `max(今天, 开始日)`：昨天之前的任务一条不碰，这是本设计的核心约束。
    """
    from_date = today if today > start else start
    existing = conn.execute(
        "SELECT id, task_date, module, done FROM cet_tasks WHERE plan_id = ? AND task_date >= ?",
        (plan_id, from_date),
    ).fetchall()
    have = {(str(r["task_date"]), str(r["module"])): r for r in existing}

    added_rows: list[tuple] = []
    cursor = datetime.date.fromisoformat(from_date)
    end = datetime.date.fromisoformat(exam)
    base = datetime.date.fromisoformat(start)
    while cursor <= end:
        ds = cursor.isoformat()
        idx = (cursor - base).days + 1
        for m in modules:
            if (ds, m) not in have:
                tpl = _MODULE_CATALOG.get(m, {}).get(intensity) or m
                added_rows.append(
                    (uuid.uuid4().hex, plan_id, uid, idx, ds, m, tpl.format(day=idx), 0, None)
                )
        cursor += datetime.timedelta(days=1)

    doomed = [
        str(r["id"])
        for (ds, m), r in have.items()
        if not int(r["done"] or 0) and (m not in modules or ds > exam)
    ]

    if added_rows:
        conn.executemany(
            "INSERT INTO cet_tasks (id, plan_id, owner_id, day_index, task_date, module, title, done, done_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            added_rows,
        )
    # 分批删：SQLite 单条语句的变量上限是 999，一年 5 模块能到 2000 条
    for i in range(0, len(doomed), 500):
        chunk = doomed[i : i + 500]
        conn.execute(
            f"DELETE FROM cet_tasks WHERE id IN ({','.join('?' * len(chunk))})", chunk
        )
    return {"added": len(added_rows), "removed": len(doomed)}


def update_plan(uid: str, changes: dict[str, Any] | None = None) -> dict[str, Any]:
    """改档案设置（PATCH 语义：只改传进来的字段）。

    两类改动的处理**刻意不同**：
      - 考试日 / 目标分 / 考试级别 → 只动档案字段，任务一条不碰；
      - 模块 / 强度 → 重建「今天及以后」的任务（见 `_resync_tasks`）。

    为什么不能统一"全量重建"：用户把"阅读"取消掉，如果连上个月的阅读打卡一起删了，
    连续天数和学习路径的关卡都会跟着倒退 —— 改个设置不该有这种副作用。
    """
    _ensure_cet_tables()
    ch = changes or {}
    today = quota.day_key()
    with store._get_conn() as conn:
        row = _latest_plan(conn, uid)
        if row is None:
            raise LookupError("还没有备考计划")
        cur = _plan_public(row)
        start = cur["start_date"]

        level = _normalize_choice(ch.get("exam_type", cur["exam_type"]), EXAM_TYPES, "CET6")
        inten = _normalize_choice(ch.get("intensity", cur["intensity"]), INTENSITIES, "standard")
        mods = _normalize_modules(ch["modules"]) if "modules" in ch else list(cur["modules"])
        target = _normalize_target(
            ch["target_score"] if "target_score" in ch else cur["target_score"], level
        )

        exam = _parse_date(ch["exam_date"]) if "exam_date" in ch else _parse_date(cur["exam_date"])
        if exam is None:
            exam = cur["exam_date"] or _add_days(start, 53)
        if exam < today:
            raise ValueError("考试日不能早于今天")
        span = (datetime.date.fromisoformat(exam) - datetime.date.fromisoformat(start)).days + 1
        if span > _MAX_DAYS:
            raise ValueError(f"备考周期最长 {_MAX_DAYS} 天")
        total_days = max(_MIN_DAYS, span)

        conn.execute(
            "UPDATE cet_plans SET exam_type = ?, target_score = ?, modules = ?, intensity = ?, "
            "exam_date = ?, total_days = ? WHERE id = ? AND owner_id = ?",
            (level, target, ",".join(mods), inten, exam, total_days, cur["id"], uid),
        )
        stats = _resync_tasks(
            conn,
            plan_id=cur["id"],
            uid=uid,
            start=start,
            exam=exam,
            modules=mods,
            intensity=inten,
            today=today,
        )
        fresh = conn.execute("SELECT * FROM cet_plans WHERE id = ?", (cur["id"],)).fetchone()
    out = _plan_public(fresh)
    out["task_changes"] = stats
    return out


def archive_plan(uid: str) -> bool:
    """结束当前备考计划（归档，不物理删除）。

    为什么不硬删：`_streak_days` 从 cet_tasks 里 done=1 的日期算，
    `service/path.py` 的学习路径关卡也要数 cet_plans / cet_tasks。
    硬删会让用户点一下"结束计划"就丢掉连续天数、学习路径还倒退几关。
    归档只是让 `_latest_plan` 查不到它，历史数据全部还在。
    """
    _ensure_cet_tables()
    with store._get_conn() as conn:
        cur = conn.execute(
            "UPDATE cet_plans SET archived_at = ? WHERE owner_id = ? AND archived_at IS NULL",
            (int(time.time()), uid),
        )
    return cur.rowcount > 0


def _latest_plan(conn: Any, uid: str) -> Any:
    """当前生效的档案。归档过的查不到，但数据仍在库里。"""
    return conn.execute(
        "SELECT * FROM cet_plans WHERE owner_id = ? AND archived_at IS NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (uid,),
    ).fetchone()


def _second_saturday(year: int, month: int) -> datetime.date:
    """某年某月的第二个周六。

    四六级笔试近年的惯例是 6 月 / 12 月的第二个周六。这里**按规则推算而不是
    写死日期** —— 写死的话每年都要改代码，而规则本身很稳定。
    推算结果只作为"快捷选项"，用户仍然可以自填，界面上也标了"以官方通知为准"。
    """
    first = datetime.date(year, month, 1)
    offset = (5 - first.weekday()) % 7  # weekday: 周一=0 … 周六=5
    return first + datetime.timedelta(days=offset + 7)


def exam_date_suggestions(today: str | None = None, count: int = 3) -> list[dict[str, str]]:
    """未来几场考试的候选日期（今天之后的才算）。"""
    t = datetime.date.fromisoformat(today or quota.day_key())
    out: list[dict[str, str]] = []
    for year in (t.year, t.year + 1, t.year + 2):
        for month in (6, 12):
            d = _second_saturday(year, month)
            if d > t:
                out.append({"date": d.isoformat(), "label": f"{year} 年 {month} 月"})
    return out[:count]


def plan_options(today: str | None = None) -> dict[str, Any]:
    """档案的可选项 —— 前后端唯一来源，前端**不许硬编码**这些名字。

    与标签体系同一个理由：选项散到前端之后，加一个模块要改两处，
    漏一处就出现"后端生成了、前端选不到"的错位。
    """
    return {
        "exam_types": [
            {"value": v, "label": _EXAM_LABELS[v], "default_target": _DEFAULT_TARGET[v]}
            for v in EXAM_TYPES
        ],
        "modules": list(MODULE_NAMES),
        "intensities": [
            {"value": "light", "label": "轻量", "hint": "每天约 30 分钟"},
            {"value": "standard", "label": "标准", "hint": "每天约 1 小时"},
            {"value": "heavy", "label": "加强", "hint": "每天约 2 小时"},
        ],
        # 快捷日期：按"6 月 / 12 月第二个周六"的惯例推算，可自填覆盖
        "exam_date_suggestions": exam_date_suggestions(today),
        "min_days": _MIN_DAYS,
        "max_days": _MAX_DAYS,
    }


def _streak_days(conn, uid: str) -> int:
    """连续学习天数：某天只要有任意任务被勾选，就算学了一天。"""
    rows = conn.execute(
        "SELECT DISTINCT task_date FROM cet_tasks WHERE owner_id = ? AND done = 1", (uid,)
    ).fetchall()
    dates = {r["task_date"] for r in rows}
    if not dates:
        return 0
    today = quota.day_key()
    cursor = today if today in dates else _add_days(today, -1)
    n = 0
    for _ in range(400):
        if cursor not in dates:
            break
        n += 1
        cursor = _add_days(cursor, -1)
    return n


def overview(uid: str) -> dict[str, Any]:
    """工作区首屏所需的全部数据（一次请求返回，减少移动端往返）。

    `options` 两个分支都要带上：空状态要拿它渲染配置向导。
    """
    _ensure_cet_tables()
    today = quota.day_key()
    options = plan_options(today)
    with store._get_conn() as conn:
        plan = _latest_plan(conn, uid)
        if plan is None:
            return {
                "plan": None,
                "progress": {"done": 0, "total": 0, "percent": 0},
                "today_tasks": [],
                "streak": _streak_days(conn, uid),
                "vocab_due": _vocab_due(conn, uid, today),
                "scores": _recent_scores(conn, uid),
                "today": today,
                "options": options,
            }
        info = _plan_public(plan)
        agg = conn.execute(
            "SELECT COUNT(*) AS total, SUM(done) AS done FROM cet_tasks WHERE plan_id = ?",
            (plan["id"],),
        ).fetchone()
        total = agg["total"] or 0
        done = agg["done"] or 0
        # ★ 必须带 plan_id：不带的话归档过的旧计划的今日任务也会混进来，
        #   表现为"结束了计划，今天仍然显示一堆任务"。
        today_rows = conn.execute(
            "SELECT id, module, title, done FROM cet_tasks "
            "WHERE plan_id = ? AND task_date = ? ORDER BY rowid",
            (plan["id"], today),
        ).fetchall()
        info["days_left"] = (
            (datetime.date.fromisoformat(str(info["exam_date"])) - datetime.date.fromisoformat(today)).days
            if info["exam_date"]
            else None
        )
        return {
            "plan": info,
            "progress": {
                "done": done,
                "total": total,
                "percent": round(done * 100 / total) if total else 0,
            },
            "today_tasks": [dict(r) for r in today_rows],
            "streak": _streak_days(conn, uid),
            "vocab_due": _vocab_due(conn, uid, today),
            "scores": _recent_scores(conn, uid),
            "today": today,
            "options": options,
        }


def _vocab_due(conn: Any, uid: str, today: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM vocab_items WHERE owner_id = ? AND next_review <= ?",
            (uid, today),
        ).fetchone()["n"]
    )


def _recent_scores(conn: Any, uid: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id, exam_type, score, target, taken_at, note FROM cet_scores "
            "WHERE owner_id = ? ORDER BY taken_at ASC LIMIT 30",
            (uid,),
        ).fetchall()
    ]


def toggle_task(uid: str, task_id: str, done: bool) -> bool:
    """勾选/取消某条任务。归属不符返回 False（上层转 404）。"""
    _ensure_cet_tables()
    with store._get_conn() as conn:
        cur = conn.execute(
            "UPDATE cet_tasks SET done = ?, done_at = ? WHERE id = ? AND owner_id = ?",
            (1 if done else 0, int(time.time()) if done else None, task_id, uid),
        )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- 词汇本


def list_vocab(uid: str, due_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    _ensure_cet_tables()
    today = quota.day_key()
    sql = "SELECT * FROM vocab_items WHERE owner_id = ?"
    args: list[Any] = [uid]
    if due_only:
        sql += " AND next_review <= ?"
        args.append(today)
    sql += " ORDER BY next_review ASC, created_at DESC LIMIT ?"
    args.append(limit)
    with store._get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def add_vocab(uid: str, word: str, meaning: str = "", source: str = "") -> dict[str, Any]:
    """加入词汇本。同一 owner 下重复单词不新建，只把下次复习提前到今天。"""
    _ensure_cet_tables()
    word = word.strip()
    if not word:
        raise ValueError("word 不能为空")
    today = quota.day_key()
    now = int(time.time())
    with store._get_conn() as conn:
        old = conn.execute(
            "SELECT id FROM vocab_items WHERE owner_id = ? AND word = ?", (uid, word)
        ).fetchone()
        if old:
            conn.execute(
                "UPDATE vocab_items SET next_review = ?, meaning = COALESCE(NULLIF(?, ''), meaning) "
                "WHERE id = ? AND owner_id = ?",
                (today, meaning, old["id"], uid),
            )
            return {"id": old["id"], "word": word, "existed": True}
        vid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO vocab_items (id, owner_id, word, meaning, source, step, interval_days, "
            "next_review, wrong_count, created_at, last_review) VALUES (?, ?, ?, ?, ?, 0, 1, ?, 0, ?, NULL)",
            (vid, uid, word, meaning, source, today, now),
        )
    return {"id": vid, "word": word, "existed": False}


def review_vocab(uid: str, item_id: str, remembered: bool) -> dict[str, Any] | None:
    """复习一张词卡：记得则进入下一档间隔，忘记则回到第 1 档并计一次错。"""
    _ensure_cet_tables()
    today = quota.day_key()
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT step, wrong_count FROM vocab_items WHERE id = ? AND owner_id = ?",
            (item_id, uid),
        ).fetchone()
        if row is None:
            return None
        step = int(row["step"])
        if remembered:
            step = min(step + 1, len(_SRS_STEPS) - 1)
            wrong = int(row["wrong_count"])
        else:
            step = 0
            wrong = int(row["wrong_count"]) + 1
        interval = _SRS_STEPS[step]
        conn.execute(
            "UPDATE vocab_items SET step = ?, interval_days = ?, next_review = ?, "
            "wrong_count = ?, last_review = ? WHERE id = ? AND owner_id = ?",
            (step, interval, _add_days(today, interval), wrong, today, item_id, uid),
        )
    return {"id": item_id, "step": step, "interval_days": interval, "next_review": _add_days(today, interval)}


# --------------------------------------------------------------------------- 分数


def list_scores(uid: str) -> list[dict[str, Any]]:
    _ensure_cet_tables()
    with store._get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, exam_type, score, target, taken_at, note FROM cet_scores "
                "WHERE owner_id = ? ORDER BY taken_at ASC",
                (uid,),
            ).fetchall()
        ]


def add_score(
    uid: str,
    exam_type: str,
    score: int,
    target: int | None = None,
    taken_at: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    _ensure_cet_tables()
    if score < 0 or score > 750:
        raise ValueError("score 应在 0–750 之间")
    sid = uuid.uuid4().hex
    now = int(time.time())
    taken = taken_at or quota.day_key()
    with store._get_conn() as conn:
        conn.execute(
            "INSERT INTO cet_scores (id, owner_id, exam_type, score, target, taken_at, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, uid, exam_type, score, target, taken, note, now),
        )
    return {"id": sid, "exam_type": exam_type, "score": score, "target": target, "taken_at": taken}


def delete_score(uid: str, score_id: str) -> bool:
    _ensure_cet_tables()
    with store._get_conn() as conn:
        cur = conn.execute("DELETE FROM cet_scores WHERE id = ? AND owner_id = ?", (score_id, uid))
    return cur.rowcount > 0
