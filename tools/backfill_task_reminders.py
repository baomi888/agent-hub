"""把「说了具体钟点、却只设了截止」的老待办补上联动提醒（一次性回填，可重复执行）。

为什么需要它
------------
2026-11-17 之前，一句话整理（`core/capture_parse.py`）只在原话出现
「提醒我 / 叫我 / 记得」时才填 `remind_at`。于是「明早 8 点起床」这种
**用户明说了钟点**的句子，落库时只有 `due_at`：

    due_at   = 2026-11-18 08:00
    remind_at= NULL          ← 不建联动提醒，到点不会响

这批数据已经写进去了，改代码救不了昨天那条。回填把它们补成
`remind_at = due_at` + 一条 `source='task'` 的联动提醒。

判据（保守，宁漏不误）
----------------------
只补**同时满足**下面四条的行：

1. `status='open'` —— 已完成的补了也没意义（联动提醒本来就随完成停用）；
2. `remind_at IS NULL` —— 用户已经设过的绝不覆盖；
3. `due_at > now` —— 过去的补上会立刻触发一条迟到提醒（`catch_up` 记 missed），
   等于凭空吵人一次；
4. 时刻**不是占位**：00:00 与 23:59 是「只说了哪天」的落库写法
   （见 `quickParse` 与 `capture_parse._SENTINEL_HM`），把它们当钟点补上，
   会让每一条只写日期的待办都在半夜响一声。

局限
----
判据只看库，不看原话 —— 原话在会话里，不在这张表。所以
「明天 9 点交作业」这种**钟点其实是截止时刻**的，也会被补上（9 点响一声）。
这是有意的取舍：宁可多响一次（用户能关），也不要该响的时候一声不吭。

用法
----
    # 先看报告（默认只读，不写库）
    venv/Scripts/python.exe tools/backfill_task_reminders.py

    # 确认后再写
    venv/Scripts/python.exe tools/backfill_task_reminders.py --apply

    # 只看某个人
    venv/Scripts/python.exe tools/backfill_task_reminders.py --owner <uid>
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

# 与 capture_parse / 前端 clockOf 同一套占位口径。三处各写一遍是有意的：
# 回填脚本不该 import 解析器（它连带 langchain），前端也 import 不到 Python。
SENTINEL_HM = ((0, 0), (23, 59))


def _hm(ts: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(int(ts), timeutil.TZ)
    return d.hour, d.minute


def _stamp(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timeutil.TZ).strftime("%Y-%m-%d %H:%M")


def candidates(now: int, owner_id: str | None = None) -> list[dict]:
    """要补的行。纯读，不改库。"""
    task_core.ensure()
    sql = (
        "SELECT id, owner_id, title, note, due_at FROM tasks "
        "WHERE status='open' AND remind_at IS NULL AND due_at IS NOT NULL AND due_at > ?"
    )
    params: list[object] = [int(now)]
    if owner_id:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    sql += " ORDER BY due_at ASC"

    with task_core._conn() as conn:  # noqa: SLF001 —— 只读，走的还是同一个库文件
        rows = conn.execute(sql, params).fetchall()

    out: list[dict] = []
    for row in rows:
        due = int(row["due_at"])
        if _hm(due) in SENTINEL_HM:
            continue
        out.append(
            {
                "id": str(row["id"]),
                "owner_id": str(row["owner_id"]),
                "title": str(row["title"]),
                "note": row["note"],
                "due_at": due,
            }
        )
    return out


def apply(rows: list[dict], now: int) -> int:
    """逐条写：`tasks.remind_at` 与联动提醒**同一事务**，不留半条。"""
    done = 0
    for row in rows:
        with task_core._conn() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE tasks SET remind_at=?, updated_at=? WHERE id=? AND owner_id=?",
                (row["due_at"], int(now), row["id"], row["owner_id"]),
            )
            reminder_core.upsert_task_reminder(
                conn,
                row["owner_id"],
                row["id"],
                row["title"],
                row["note"],
                row["due_at"],
                now=now,
            )
        done += 1
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="给只设了截止的老待办补联动提醒")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只打印计划）")
    ap.add_argument("--owner", default=None, help="只处理某个 owner_id")
    args = ap.parse_args(argv)

    now = timeutil.now_ts()
    rows = candidates(now, args.owner)

    print(f"现在 {_stamp(now)}；候选 {len(rows)} 条")
    for row in rows:
        print(f"  [{row['owner_id'][:8]}] {row['title'][:18]:<20} {_stamp(row['due_at'])}")

    if not rows:
        return 0
    if not args.apply:
        print("\n（只读模式）确认无误后加 --apply 再跑一次。")
        return 0

    done = apply(rows, now)
    print(f"\n已补 {done} 条：remind_at = due_at，并各建一条 source='task' 的联动提醒。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
