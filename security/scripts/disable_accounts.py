#!/usr/bin/env python3
"""账号禁用/封堵工具（安全审计 2026-11-14）。

为什么单独写一个脚本而不是直接 UPDATE
--------------------------------------
"禁用"在这套系统里有两种完全不同的实现，用错一种等于没禁用：

1. **账号还在**（users 有行）—— 两件事都要做，缺一不可：
   * `status = 0`   ：只挡**下一次登录**（`account.login()` 最后一步才看 status）；
   * `token_valid_after += 1` ：挡**已经发出去的 cookie**。鉴权依赖每次请求都拿
     令牌里的代数与库里的代数比对，+1 之后旧令牌立刻 401。
   只改 status 的话，用户在浏览器里那枚 7 天有效的 cookie 还能继续用一周。

2. **账号已不存在**（users 没行，但 uid 还在写 usage / api_call_logs）——
   这类"幽灵身份"是 2026-11-13 线上事故的残留：`core/auth.current_user_id` 在
   查不到用户时按"代数 0"放行，于是删过号的旧 cookie 照样能进。唯一的堵法是
   往 `deleted_users` 写一条墓碑，鉴权入口见到墓碑就 401。见
   `tests/test_deleted_user.py` 三条断言（含反证）。

用法
----
    python3 security/scripts/disable_accounts.py --db data/sessions.db --orphans            # 预演
    python3 security/scripts/disable_accounts.py --db data/sessions.db --orphans --apply    # 执行
    python3 security/scripts/disable_accounts.py --db data/sessions.db --uids uid1,uid2 --reason manual --apply

默认是**预演**：只打印将要改什么，不写库。加 `--apply` 才落盘。
输出是 JSON，便于留档与二次核对。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

# 管理员永不禁用：ADMIN_UIDS 与 ADMIN_USERNAMES 两套都在用（见 core/quota._is_admin）
DEFAULT_ADMIN_UIDS = {"963088555a424172b8c09f5ba575a74a"}


def _connect(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        sys.exit("数据库不存在: %s" % path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _orphan_uids(conn: sqlite3.Connection) -> list[str]:
    """有调用痕迹、但 users 里没有、且还没有墓碑的 uid。

    两列都要看：只查 api_call_logs 会漏掉"只建过会话没打过接口"的，
    只查 conversations 会漏掉"只刷过列表页"的（本次审计里 77 个孤儿大多属于后者）。

    ★ 刻意**不查 usage.user_id**：那张表的键不止 uid ——
      `signup:ip:1.2.3.4`、`remind:<uid>`、`pushtest:<uid>`，以及日历订阅那种
      "按 token 记账"的行。把它们当身份写进墓碑，除了脏数据没有任何作用。
    """
    rows = conn.execute(
        """
        select uid from api_call_logs where uid is not null and uid <> ''
        union select owner_id from conversations where owner_id is not null and owner_id <> ''
        """
    ).fetchall()
    ids = {r[0] for r in rows if r[0]}
    known = {r[0] for r in conn.execute("select id from users")}
    tomb = {r[0] for r in conn.execute("select uid from deleted_users")}
    return sorted(ids - known - tomb)


def _admin_uids(conn: sqlite3.Connection) -> set[str]:
    admin = set(DEFAULT_ADMIN_UIDS)
    env = os.environ.get("ADMIN_UIDS", "")
    admin |= {u.strip() for u in env.split(",") if u.strip()}
    names = {n.strip() for n in os.environ.get("ADMIN_USERNAMES", "").split(",") if n.strip()}
    if names:
        marks = ",".join("?" * len(names))
        for r in conn.execute(
            "select id from users where username in (%s)" % marks, tuple(names)
        ):
            admin.add(r[0])
    return admin


def disable(db: str, uids: list[str] | None, orphans: bool, reason: str, apply: bool) -> dict:
    conn = _connect(db)
    admin = _admin_uids(conn)

    if orphans:
        targets = _orphan_uids(conn)
        mode = "orphan_tombstone"
    else:
        targets = sorted({u.strip() for u in (uids or []) if u.strip()})
        mode = "user_disable"

    skipped_admin, skipped_missing, to_disable, to_tombstone = [], [], [], []
    for uid in targets:
        if uid in admin:
            skipped_admin.append(uid)
            continue
        row = conn.execute(
            "select id, username, status from users where id = ?", (uid,)
        ).fetchone()
        if row is None:
            to_tombstone.append(uid)
        else:
            to_disable.append({"uid": uid, "username": row["username"], "status": row["status"]})

    now = int(time.time())
    summary = {
        "db": db,
        "mode": mode,
        "reason": reason,
        "apply": apply,
        "counts": {
            "targets": len(targets),
            "disable": len(to_disable),
            "tombstone": len(to_tombstone),
            "skipped_admin": len(skipped_admin),
            "skipped_missing": len(skipped_missing),
        },
        "disable": to_disable,
        "tombstone": to_tombstone,
        "skipped_admin": skipped_admin,
    }

    if apply:
        with conn:
            for item in to_disable:
                # 两步必须同一事务：只落 status 会留下"能登进来但登不了"的错觉，
                # 只落代数会留下"重新登录又能进"的错觉。
                conn.execute(
                    "update users set status = 0, token_valid_after = token_valid_after + 1 "
                    "where id = ?",
                    (item["uid"],),
                )
            for uid in to_tombstone:
                conn.execute(
                    "insert or replace into deleted_users (uid, username, deleted_at, reason) "
                    "values (?, ?, ?, ?)",
                    (uid, "", now, reason[:200]),
                )
        # 审计留痕：与 core/account.audit() 同格式（JSONL 直接 grep）
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(db)), "audit.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": now,
                            "event": "security_disable",
                            "uid": ",".join([x["uid"] for x in to_disable][:50]),
                            "tombstoned": len(to_tombstone),
                            "reason": reason,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass
    conn.close()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="知穗账号禁用/封堵（默认预演）")
    ap.add_argument("--db", default="data/sessions.db", help="SQLite 路径")
    ap.add_argument("--uids", default="", help="逗号分隔的 uid 列表")
    ap.add_argument("--orphans", action="store_true", help="处理所有已删号但仍有令牌的幽灵身份")
    ap.add_argument("--reason", default="security_audit_20261114", help="写入审计的原因串")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只预演）")
    args = ap.parse_args()

    if not args.orphans and not args.uids:
        sys.exit("要么给 --orphans，要么给 --uids uid1,uid2")

    out = disable(
        args.db,
        args.uids.split(",") if args.uids else None,
        args.orphans,
        args.reason,
        args.apply,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
