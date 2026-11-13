"""清理验收 / 压测留下的一次性账号。

验收脚本（.workbuddy/probe_cet_path_live.py）每次运行会注册一个新账号，
跑几次就会在正式库里留下 qa123456 这样的空壳用户与它们的备考数据。
本脚本按用户名模式识别并清掉，不碰真实用户。

用法：
    /root/venv/bin/python /root/ops/cleanup_qa.py            # 先看要删什么（不动手）
    /root/venv/bin/python /root/ops/cleanup_qa.py --apply     # 确认后真删

只删匹配 `qa` + 4~8 位数字 的用户，以及验收脚本早期用过的一次性名 `qa1022probe`。
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 默认用项目主库；BAOMI_DB 可覆盖，方便在临时库上先验证脚本本身
DB = os.environ.get("BAOMI_DB") or os.path.join(ROOT, "data", "sessions.db")

# 精确模式，避免误伤真实用户（比如 qaqa、qa-team 这类都不会命中）
PATTERN = re.compile(r"^qa\d{4,8}$")
EXTRA = {"qa1022probe"}

# owner_id 属于用户的行（按依赖顺序，先子后父）
OWNED_TABLES = [
    "cet_tasks",
    "cet_plans",
    "vocab_items",
    "cet_scores",
    "path_progress",
    "checkin_shares",
]


def main() -> int:
    apply_changes = "--apply" in sys.argv
    if not os.path.exists(DB):
        print(f"[中止] 找不到数据库：{DB}")
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT id, username, created_at FROM users").fetchall()
    targets = [u for u in users if PATTERN.match(u["username"]) or u["username"] in EXTRA]

    if not targets:
        print("没有匹配到一次性账号，无需清理。")
        return 0

    print(f"命中 {len(targets)} 个一次性账号：")
    for u in targets:
        counts = {}
        for t in OWNED_TABLES:
            try:
                counts[t] = conn.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE owner_id = ?", (u["id"],)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                counts[t] = "-"  # 该表在此版本里不存在，跳过
        detail = "  ".join(f"{k}={v}" for k, v in counts.items() if v not in (0, "-"))
        print(f"  - {u['username']:<14} {detail or '(无业务数据)'}")

    if not apply_changes:
        print("\n这是预演，什么都没删。确认无误后加 --apply 再跑一次。")
        return 0

    ids = [u["id"] for u in targets]
    marks = ",".join("?" * len(ids))
    convo_ids = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM conversations WHERE owner_id IN ({marks})", ids
        ).fetchall()
    ]
    if convo_ids:
        cmarks = ",".join("?" * len(convo_ids))
        conn.execute(f"DELETE FROM messages WHERE conversation_id IN ({cmarks})", convo_ids)
        conn.execute(f"DELETE FROM conversations WHERE id IN ({cmarks})", convo_ids)

    for t in OWNED_TABLES:
        try:
            conn.execute(f"DELETE FROM {t} WHERE owner_id IN ({marks})", ids)
        except sqlite3.OperationalError:
            pass

    # 删号前先立墓碑（与 core/account.delete_user 同一套约定）。
    # 为什么非写不可：鉴权在库里查不到人时按「代数 0」放行（为兼容测试临时 uid），
    # 所以只 DELETE users 行的话，这些账号浏览器里的旧 cookie 还能继续建会话、
    # 写 usage —— 2026-11-13 就是这样留下 7 个孤儿会话 + 60 条孤儿 usage 的。
    now = int(time.time())
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO deleted_users (uid, username, deleted_at, reason) "
            "VALUES (?, ?, ?, ?)",
            [(u["id"], u["username"], now, "cleanup_qa") for u in targets],
        )
    except sqlite3.OperationalError:
        # 老库还没有这张表：建一次再补写，别因为缺表就跳过吊销
        conn.execute(
            "CREATE TABLE IF NOT EXISTS deleted_users ("
            " uid TEXT PRIMARY KEY, username TEXT NOT NULL DEFAULT '',"
            " deleted_at INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '')"
        )
        conn.executemany(
            "INSERT OR REPLACE INTO deleted_users (uid, username, deleted_at, reason) "
            "VALUES (?, ?, ?, ?)",
            [(u["id"], u["username"], now, "cleanup_qa") for u in targets],
        )

    conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    conn.commit()
    print(f"\n已删除 {len(ids)} 个账号及其数据（会话 {len(convo_ids)} 条）。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
