"""P7 四六级备考工作区（/api/cet）服务层测试。

不联网、不依赖真实 API Key：
  · 每个测试 monkeypatch core.session._db_path 到独立临时库，测试结束自动还原；
  · 只测服务层（SQL + 业务规则），HTTP 层由 api/cet.py 的薄封装承担，风险低；
  · cet 没有模块级「已建表」标志，_ensure_cet_tables 每次 CREATE IF NOT EXISTS，切库安全。

覆盖：
  1. 计划：生成 N 天 × 5 模块、进度、勾选后连续天数
  2. 词汇本：SRS 记得前进一档 / 忘记回到第 1 档、到期过滤
  3. 分数：新增 / 列表 / 非法分值被拒
  4. owner 强制隔离：B 看不到 A 的计划与词卡
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.session  # noqa: E402
from service import cet  # noqa: E402


def _prep_db(monkeypatch, tag: str) -> None:
    tmp = tempfile.mkdtemp(prefix=f"cet_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    cet._ensure_cet_tables()


def test_create_plan_and_toggle(monkeypatch):
    _prep_db(monkeypatch, "plan")
    p = cet.create_plan("u1", days=10)
    assert p["total_days"] == 10

    ov = cet.overview("u1")
    assert ov["plan"]["id"] == p["id"]
    assert ov["progress"]["total"] == 50  # 10 天 × 5 模块
    assert ov["progress"]["done"] == 0
    assert len(ov["today_tasks"]) == 5
    assert ov["streak"] == 0

    t = ov["today_tasks"][0]
    assert cet.toggle_task("u1", t["id"], True) is True
    ov2 = cet.overview("u1")
    assert ov2["progress"]["done"] == 1
    assert ov2["streak"] == 1

    # 幂等：再次勾同一任务仍成功，done 不重复累加
    assert cet.toggle_task("u1", t["id"], True) is True
    assert cet.overview("u1")["progress"]["done"] == 1


def test_vocab_srs(monkeypatch):
    _prep_db(monkeypatch, "vocab")
    v1 = cet.add_vocab("u1", "abandon", "放弃")
    assert v1["existed"] is False
    # 同词去重：不新建，只把下次复习提前
    v2 = cet.add_vocab("u1", "abandon", "")
    assert v2["existed"] is True and v2["id"] == v1["id"]

    assert len(cet.list_vocab("u1", due_only=True)) == 1
    # 记得 → 间隔 3 天，当天不再到期
    r = cet.review_vocab("u1", v1["id"], True)
    assert r["interval_days"] == 3
    assert len(cet.list_vocab("u1", due_only=True)) == 0
    # 忘记 → 回到 1 天，并计一次错
    r2 = cet.review_vocab("u1", v1["id"], False)
    assert r2["interval_days"] == 1
    items = cet.list_vocab("u1", due_only=False)
    assert items[0]["wrong_count"] == 1


def test_scores(monkeypatch):
    _prep_db(monkeypatch, "score")
    cet.add_score("u1", "CET6", 473, 500)
    cet.add_score("u1", "MOCK", 490, 500)
    rows = cet.list_scores("u1")
    assert len(rows) == 2
    assert rows[0]["score"] == 473

    # 非法分值直接拒（避免手误把 9999 写进曲线）
    try:
        cet.add_score("u1", "CET6", 9999)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("超范围分数未被拒绝")


def test_owner_isolation(monkeypatch):
    _prep_db(monkeypatch, "owner")
    cet.create_plan("u1", days=7)
    cet.add_vocab("u1", "fragile", "脆弱的")

    ov_b = cet.overview("u2")
    assert ov_b["plan"] is None
    assert ov_b["progress"]["total"] == 0
    assert cet.list_vocab("u2", due_only=False) == []

    # 越权勾选他人任务：rowcount=0 → False（上层转 404）
    task = cet.overview("u1")["today_tasks"][0]
    assert cet.toggle_task("u2", task["id"], True) is False


# ==================== 备考档案的自定义能力（2026-10-27） ====================
#
# 内测反馈："连选四级还是六级都不能选，时间啥的也不能选"。
# 这组测试锁的是**四件事同时成立**：
#   ① 配置真的生效（级别 / 模块 / 强度 / 目标分都能改到任务上）
#   ② 改模块只动「今天及以后」，历史一条不碰
#   ③ 归档不影响连续天数（否则用户点一下"结束"就掉进度）
#   ④ 新建档案会自动归档旧的，不产生查不到的垃圾行

import pytest  # noqa: E402

from core import quota  # noqa: E402


def _count_tasks(where: str = "1=1", args: tuple = ()) -> int:
    with core.session._get_conn() as conn:
        return int(
            conn.execute(f"SELECT COUNT(*) FROM cet_tasks WHERE {where}", args).fetchone()[0]
        )


def test_plan_options_served_by_backend(monkeypatch):
    """选项由后端下发，前端不硬编码 —— 这条防的是"后端加了模块、前端选不到"。

    与标签体系同一个理由：选项散到前端之后，加一个模块要改两处，漏一处就错位。
    """
    _prep_db(monkeypatch, "opt")
    ov = cet.overview("u1")
    opts = ov["options"]
    assert opts["modules"] == list(cet.MODULE_NAMES)
    assert [x["value"] for x in opts["exam_types"]] == ["CET4", "CET6"]
    assert [x["value"] for x in opts["intensities"]] == ["light", "standard", "heavy"]
    # 空状态下也要有 options（配置向导要用）
    assert ov["plan"] is None


def test_custom_plan_respects_every_option(monkeypatch):
    """四项配置都要真的落到任务上，而不是只写进档案字段。"""
    _prep_db(monkeypatch, "custom")
    today = quota.day_key()
    p = cet.create_plan(
        "u1",
        exam_type="CET4",
        modules=["听力", "真题"],
        intensity="light",
        exam_date=cet._add_days(today, 9),  # 含今天共 10 天
        target_score=480,
    )
    assert p["exam_type"] == "CET4"
    assert p["exam_type_label"] == "四级"
    assert p["total_days"] == 10
    assert p["modules"] == ["听力", "真题"]
    assert p["target_score"] == 480

    ov = cet.overview("u1")
    # 只勾 2 个模块 → 每天正好 2 条，不是 5 条
    assert len(ov["today_tasks"]) == 2
    assert ov["progress"]["total"] == 20
    # 强度影响任务文案（light 与 standard 的听力文案不同）
    titles = {t["module"]: t["title"] for t in ov["today_tasks"]}
    assert titles["听力"] == cet._MODULE_CATALOG["听力"]["light"]
    assert titles["听力"] != cet._MODULE_CATALOG["听力"]["standard"]


def test_exam_date_wins_over_days(monkeypatch):
    """考试日优先于天数：用户关心"12 月 12 号考"，不是"我要 54 天"。"""
    _prep_db(monkeypatch, "date")
    today = quota.day_key()
    p = cet.create_plan("u1", exam_date=cet._add_days(today, 45), days=999)
    assert p["total_days"] == 46
    assert p["exam_date"] == cet._add_days(today, 45)

    # 只给天数时仍然反算考试日（保持旧行为）
    p2 = cet.create_plan("u1", days=7)
    assert p2["total_days"] == 7
    assert p2["exam_date"] == cet._add_days(today, 6)


def test_reject_past_exam_date(monkeypatch):
    """考试日不能早于今天，否则会生成一份"已过期"的计划。"""
    _prep_db(monkeypatch, "past")
    with pytest.raises(ValueError):
        cet.create_plan("u1", exam_date=cet._add_days(quota.day_key(), -1))


def test_reject_empty_modules(monkeypatch):
    """一个模块都不选 = 一份没有任务的计划，必须拒绝而不是静默生成空档案。"""
    _prep_db(monkeypatch, "emptymod")
    with pytest.raises(ValueError):
        cet.create_plan("u1", modules=["不存在的模块"])


def test_update_modules_only_touches_today_onwards(monkeypatch):
    """★ 核心规则：改模块只重建「今天及以后」，历史一条不碰。

    没有这条约束，用户取消一个模块就会连带删掉上个月的打卡记录，
    连续天数归零、学习路径关卡倒退 —— 改个设置不该有这种副作用。
    """
    _prep_db(monkeypatch, "update")
    today = quota.day_key()
    start = cet._add_days(today, -5)  # 5 天前开始
    cet.create_plan("u1", start_date=start, exam_date=cet._add_days(today, 10),
                    modules=["听力", "词汇"])

    # 历史 5 天（start..yesterday）× 2 模块 = 10 条；今天 2 条
    history_before = _count_tasks("task_date < ?", (today,))
    assert history_before == 10
    assert _count_tasks("task_date = ?", (today,)) == 2

    cet.update_plan("u1", {"modules": ["听力", "词汇", "阅读"]})

    # 历史一条没变，今天变成 3 条
    assert _count_tasks("task_date < ?", (today,)) == history_before
    assert _count_tasks("task_date = ?", (today,)) == 3
    # 明天（也属于"今天及以后"）同样变成 3 条
    assert _count_tasks("task_date = ?", (cet._add_days(today, 1),)) == 3


def test_update_keeps_completed_tasks_of_removed_module(monkeypatch):
    """取消某个模块时，**已完成**的该模块任务要保留 —— 那是已经发生的打卡事实。"""
    _prep_db(monkeypatch, "done")
    today = quota.day_key()
    cet.create_plan("u1", exam_date=cet._add_days(today, 4), modules=["听力", "阅读"])
    ov = cet.overview("u1")
    reading = next(t for t in ov["today_tasks"] if t["module"] == "阅读")
    assert cet.toggle_task("u1", reading["id"], True) is True
    streak_before = cet.overview("u1")["streak"]
    assert streak_before == 1

    cet.update_plan("u1", {"modules": ["听力"]})

    with core.session._get_conn() as conn:
        kept = conn.execute(
            "SELECT COUNT(*) FROM cet_tasks WHERE module = '阅读' AND done = 1"
        ).fetchone()[0]
    assert kept == 1, "已完成的阅读任务被删掉了 —— 等于篡改打卡记录"
    assert cet.overview("u1")["streak"] == streak_before
    # 未完成的阅读任务应当被清掉
    with core.session._get_conn() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM cet_tasks WHERE module = '阅读' AND done = 0"
        ).fetchone()[0]
    assert left == 0


def test_extend_exam_date_adds_tasks(monkeypatch):
    """考试日推后 → 尾巴上要补出新日期的任务，否则最后几天是空的。"""
    _prep_db(monkeypatch, "extend")
    today = quota.day_key()
    cet.create_plan("u1", exam_date=cet._add_days(today, 4), modules=["词汇"])  # 5 天
    assert cet.overview("u1")["progress"]["total"] == 5

    cet.update_plan("u1", {"exam_date": cet._add_days(today, 9)})  # 10 天
    ov = cet.overview("u1")
    assert ov["plan"]["total_days"] == 10
    assert ov["progress"]["total"] == 10
    # 最后一天真的有任务（不是空壳）
    assert _count_tasks("task_date = ?", (cet._add_days(today, 9),)) == 1


def test_archive_keeps_streak_and_history(monkeypatch):
    """★ 结束计划 = 归档。连续天数与历史任务都必须还在。

    硬删会让 `_streak_days` 归零、`service/path.py` 的关卡倒退 ——
    用户点一下"结束这个计划"不该丢掉已经学过的记录。
    """
    _prep_db(monkeypatch, "archive")
    cet.create_plan("u1", days=7)
    ov = cet.overview("u1")
    assert cet.toggle_task("u1", ov["today_tasks"][0]["id"], True) is True
    assert cet.overview("u1")["streak"] == 1
    tasks_before = _count_tasks()

    assert cet.archive_plan("u1") is True

    ov2 = cet.overview("u1")
    assert ov2["plan"] is None
    assert ov2["today_tasks"] == []       # 归档后不再显示今日任务
    assert ov2["streak"] == 1             # ★ 连续天数不受影响
    assert _count_tasks() == tasks_before  # ★ 任务行一条没少
    # 归档第二次 → False（上层转 404）
    assert cet.archive_plan("u1") is False


def test_new_plan_archives_previous(monkeypatch):
    """再建一份档案 → 旧档案归档，任务留在库里（是历史，不是垃圾）。"""
    _prep_db(monkeypatch, "renew")
    p1 = cet.create_plan("u1", days=5)
    p2 = cet.create_plan("u1", days=3)
    assert p1["id"] != p2["id"]

    with core.session._get_conn() as conn:
        rows = {
            r["id"]: r["archived_at"]
            for r in conn.execute("SELECT id, archived_at FROM cet_plans").fetchall()
        }
    assert rows[p1["id"]] is not None, "旧档案没有被归档"
    assert rows[p2["id"]] is None

    # 旧计划的任务仍在（5 天 × 5 = 25 条），新计划 3×5 = 15 条
    assert _count_tasks("plan_id = ?", (p1["id"],)) == 25
    ov = cet.overview("u1")
    assert ov["plan"]["id"] == p2["id"]
    assert ov["progress"]["total"] == 15      # 只看当前档案的进度
    assert len(ov["today_tasks"]) == 5        # 不会把旧档案的今日任务混进来


def test_migration_adds_columns_on_old_db(monkeypatch):
    """老库（只有 6 列）第一次被访问时自动补 5 列，且跑两次不报错。"""
    tmp = tempfile.mkdtemp(prefix="cet_old_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    with core.session._get_conn() as conn:
        conn.execute(
            "CREATE TABLE cet_plans (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
            "title TEXT NOT NULL, start_date TEXT NOT NULL, exam_date TEXT, "
            "total_days INTEGER NOT NULL, created_at INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO cet_plans VALUES ('p_old', 'u1', '旧计划', '2026-10-01', '2026-11-23', 54, 1)"
        )

    cet._ensure_cet_tables()
    cet._ensure_cet_tables()  # 幂等：第二次跑不该抛"列已存在"

    with core.session._get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(cet_plans)").fetchall()}
    assert {"exam_type", "target_score", "modules", "intensity", "archived_at"} <= cols

    # 老行迁移后拿到的是合理初值，不是 None
    ov = cet.overview("u1")
    assert ov["plan"]["exam_type"] == "CET6"
    assert ov["plan"]["modules"] == list(cet.MODULE_NAMES)
    assert ov["plan"]["intensity"] == "standard"
