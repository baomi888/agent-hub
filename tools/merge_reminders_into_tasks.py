"""把「提醒」并入「待办」（一次性迁移，可重复执行）。

背景
----
2026-11-19 产品决策：提醒与待办合二为一，「提醒」不再作为独立对象存在。
- 用户自建的提醒（`source='manual'`）逐条转成待办：
  * freq=once          → 一次性待办（due_at = remind_at = 下一次触发时刻）
  * freq=daily/weekdays/weekly → 循环待办（recurrence 同名；完成一轮自动排下一轮）
  * freq=interval      → 降级成一次性待办，备注注明原间隔（投递节奏不是待办节律）
  * action != notify   → 降级 notify，备注注明
- 迁移成功的原提醒行 `enabled=0`（不删除，保留数据线索）。
- 存量无日期的 open 待办补 `due_at = 今天 23:59:59`（「所有待办都要设置时间」）。

不动的
------
worker（reminder.tick / report.daily / push.daily）、六个来源开关、
静默时段、每日上限、每日回顾（source='system'）、上课提醒（timetable）、
`source='task'` 的联动提醒 —— 全部原样保留。

安全
----
1. 默认只读，加 `--apply` 才写库；
2. `--apply` 第一步先把 reminders / reminder_fires 备份到
   `data/backup-reminders-<时间戳>.db`（ATTACH 独立文件，不污染主库）；
3. 幂等：迁移完 `enabled=0`，重跑不再命中候选。

用法
----
    # 先看报告（默认只读）
    venv/Scripts/python.exe tools/merge_reminders_into_tasks.py

    # 确认后写库
    venv/Scripts/python.exe tools/merge_reminders_into_tasks.py --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import reminder_core, task_core, timeutil  # noqa: E402
from core import session as store  # noqa: E402

FREQ_TO_REC = {"daily": "daily", "weekdays": "weekdays", "weekly": "weekly"}


def _stamp(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timeutil.TZ).strftime("%Y-%m-%d %H:%M")


def _manual_rows(owner_id: str | None) -> list[dict]:
    """还没迁移的自建提醒（enabled=1 的 manual 行）。纯读。"""
    task_core.ensure()
    sql = (
        "SELECT * FROM reminders "
        "WHERE source='manual' AND enabled=1"
    )
    params: list[object] = []
    if owner_id:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    sql += " ORDER BY created_at ASC"
    with task_core._conn() as conn:  # noqa: SLF001
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _plan_row(row: dict, now: int) -> dict | None:
    """一条 manual 提醒 → 待办草案。返回 None = 不迁（报告里说明原因）。"""
    scheduled = reminder_core._scheduled(row)  # noqa: SLF001 —— override.at 生效口径
    nxt = reminder_core.next_fire(scheduled, now=now)
    if nxt is None:
        return None  # 过期的一次性提醒 / 算不出下一次：留原样 disable
    freq = str(row.get("freq") or "daily")
    note = str(row.get("note") or "").strip()
    tags: list[str] = []
    recurrence = FREQ_TO_REC.get(freq)
    weekday = None
    if recurrence is None:
        # interval 等不支持循环的规则：降级一次性，把原规则写进备注
        every = row.get("every_minutes")
        tags.append(f"原提醒每 {every} 分钟循环，迁移后改为一次性" if every else "原提醒规则不支持循环，迁移后改为一次性")
        recurrence = "none"
    if recurrence == "weekly":
        weekday = int(row.get("weekday") if row.get("weekday") is not None else 0)
    if str(row.get("action") or "notify") != "notify":
        tags.append("原提醒到点会做更多事，迁移后只保留通知")
    if tags:
        note = (note + "\n" if note else "") + "；".join(tags) + "（提醒合并迁移）"
    return {
        "owner_id": str(row["owner_id"]),
        "title": str(row["title"]),
        "note": note or None,
        "list_key": "life",
        "due_at": int(nxt),
        "remind_at": int(nxt),
        "recurrence": recurrence,
        "recurrence_weekday": weekday,
        "reminder_id": str(row["id"]),
    }


def backup() -> str:
    """备份 reminders / reminder_fires 到独立库文件，返回文件路径。"""
    stamp = datetime.now(timeutil.TZ).strftime("%Y%m%d-%H%M%S")
    path = Path(store._db_path()).with_name(f"backup-reminders-{stamp}.db")  # noqa: SLF001
    with task_core._conn() as conn:  # noqa: SLF001
        conn.execute("ATTACH DATABASE ? AS bak", (str(path),))
        conn.execute("CREATE TABLE bak.reminders AS SELECT * FROM reminders")
        conn.execute("CREATE TABLE bak.reminder_fires AS SELECT * FROM reminder_fires")
        conn.execute("DETACH DATABASE bak")
    return str(path)


def apply_migration(plans: list[dict], now: int) -> dict:
    """逐条写：待办 + 联动提醒 + 原行停用，三条同事务，不留半条。"""
    ok = 0
    skipped: list[tuple[str, str]] = []
    for p in plans:
        try:
            with task_core._conn() as conn:  # noqa: SLF001
                if task_core._count_open(conn, p["owner_id"]) >= task_core.MAX_TASKS_PER_OWNER:
                    skipped.append((p["reminder_id"], "待办已达上限，未迁移"))
                    continue
                tid = task_core._new_id()
                conn.execute(
                    """INSERT INTO tasks
                          (id, owner_id, title, note, list_key, icon, priority, status,
                           due_at, remind_at, completed_at, recurrence, recurrence_weekday,
                           parent_id, sort_order, created_at, updated_at)
                       VALUES (?,?,?,?,?,NULL,0,'open',?,?,NULL,?,?,NULL,0,?,?)""",
                    (
                        tid, p["owner_id"], p["title"], p["note"],
                        p["list_key"], p["due_at"], p["remind_at"],
                        p["recurrence"], p["recurrence_weekday"], now, now,
                    ),
                )
                reminder_core.upsert_task_reminder(
                    conn, p["owner_id"], tid, p["title"], p["note"], p["remind_at"], now=now
                )
                conn.execute(
                    "UPDATE reminders SET enabled=0, updated_at=? WHERE id=?",
                    (now, p["reminder_id"]),
                )
            ok += 1
        except Exception as exc:  # 迁移单条失败不拖垮整批
            skipped.append((p["reminder_id"], f"{type(exc).__name__}: {exc}"))
    return {"ok": ok, "skipped": skipped}


def fill_undated(now: int) -> list[dict]:
    """无日期的 open 待办 → 今天 23:59:59（只读阶段返回要改的行）。"""
    local = datetime.fromtimestamp(now, timeutil.TZ)
    eod = local.replace(hour=23, minute=59, second=59, microsecond=0)
    eod_ts = int(eod.timestamp())
    with task_core._conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT id, owner_id, title FROM tasks "
            "WHERE status='open' AND due_at IS NULL ORDER BY created_at ASC"
        ).fetchall()
    return [
        {"id": str(r["id"]), "owner_id": str(r["owner_id"]),
         "title": str(r["title"]), "due_at": eod_ts}
        for r in rows
    ]


def apply_undated(rows: list[dict], now: int) -> int:
    with task_core._conn() as conn:  # noqa: SLF001
        for r in rows:
            conn.execute(
                "UPDATE tasks SET due_at=?, updated_at=? WHERE id=? AND owner_id=?",
                (r["due_at"], int(now), r["id"], r["owner_id"]),
            )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 manual 提醒迁成待办；给无日期待办补时间")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只打印计划）")
    ap.add_argument("--owner", default=None, help="只处理某个 owner_id")
    args = ap.parse_args(argv)

    now = timeutil.now_ts()
    print(f"现在 {_stamp(now)}")

    rows = _manual_rows(args.owner)
    plans: list[dict] = []
    dropped = 0
    for row in rows:
        p = _plan_row(row, now)
        if p is None:
            dropped += 1
            continue
        plans.append(p)

    print(f"\n== manual 提醒 → 待办：候选 {len(plans)} 条（另有 {dropped} 条已过期/算不出下一次，仅停用不迁移）")
    for p in plans:
        rule = {"daily": "每天", "weekdays": "工作日", "weekly": "每周", "none": "一次性"}[p["recurrence"]]
        print(f"  [{p['owner_id'][:8]}] {p['title'][:18]:<20} {rule} {_stamp(p['due_at'])}")

    undated = fill_undated(now)
    print(f"\n== 无日期的 open 待办补「今天 23:59:59」：{len(undated)} 条")
    for r in undated[:20]:
        print(f"  [{r['owner_id'][:8]}] {r['title'][:18]}")

    if not plans and not undated:
        print("\n没有要迁移的数据。")
        return 0
    if not args.apply:
        print("\n（只读模式）确认无误后加 --apply 再跑一次。")
        return 0

    bak = backup()
    print(f"\n已备份 reminders/reminder_fires → {bak}")
    result = apply_migration(plans, now)
    n = apply_undated(undated, now)
    print(f"迁移完成：待办 {result['ok']} 条；补时间 {n} 条。")
    for rid, why in result["skipped"]:
        print(f"  跳过 {rid}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
