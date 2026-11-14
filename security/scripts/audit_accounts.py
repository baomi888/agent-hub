#!/usr/bin/env python3
"""知穗 · 账号调用审计（只读）。

给的是"从库里能直接算出来的事实"，不猜：调用量、每分钟峰值、429、孤儿身份、
每日用量与上限的比值。所有查询都是 SELECT，连 WAL 也只读，不改一行数据。

用法
----
    python3 security/scripts/audit_accounts.py --db data/sessions.db
    python3 security/scripts/audit_accounts.py --db data/sessions.db --top 30

口径（改这里要同步改 security/2026-11-14_账号异常与反代审计.md §0）
--------------------------------------------------------------------
* "调用"= `api_call_logs` 的一行（中间件采样，非账本；进程被 kill -9 时最多丢 50 条）
* "额度"= `usage` 表按日累计；上限取 `core/config.py` 的默认值（线上 .env 未覆盖）
* "孤儿身份"= 在调用/会话里留下 uid，但 `users` 里没有、`deleted_users` 里也没有墓碑
* 时间一律 Asia/Shanghai（与 `core.timeutil` 一致）
"""

from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys

TZ = datetime.timezone(datetime.timedelta(hours=8))

# 与 core/config.py 保持一致的默认上限。审计时若线上 .env 覆盖了，改这里。
LIMITS = {"ask": 60, "embed": 1000, "auto": 120, "upload": 2000}


def f(ts) -> str:
    if not ts:
        return "-"
    return datetime.datetime.fromtimestamp(int(ts), TZ).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser(description="知穗账号调用审计（只读）")
    ap.add_argument("--db", default="data/sessions.db")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    conn.row_factory = sqlite3.Row
    q = lambda s, *a: conn.execute(s, a).fetchall()

    users = {r["id"]: dict(r) for r in q("select id, username, status, created_at, last_login_at from users")}
    r = q("select count(*) n, min(ts) a, max(ts) b from api_call_logs")[0]
    print("== 概览 ==")
    print("用户 %d（停用 %d） / 调用 %d 条 / %s ~ %s" % (
        len(users), sum(1 for u in users.values() if u["status"] == 0), r["n"], f(r["a"]), f(r["b"])))

    print("\n== 1. 调用量 Top%d（429 = 撞到每分钟限流）==" % args.top)
    rows = q(
        """select uid, count(*) n,
                  sum(case when status=429 then 1 else 0 end) r429,
                  min(ts) a, max(ts) b,
                  count(distinct date(ts,'unixepoch','+8 hours')) days
           from api_call_logs where uid is not null and uid<>''
           group by uid order by n desc limit ?""", args.top)
    for x in rows:
        u = users.get(x["uid"], {})
        print("  %-8s %-16s n=%-6d 429=%-3d days=%-3d %s ~ %s" % (
            x["uid"][:8], (u.get("username") or "【已删】")[:16], x["n"], x["r429"], x["days"],
            f(x["a"]), f(x["b"])))

    print("\n== 2. 每分钟峰值 Top10（人类点击上限约 6~10 次/分钟）==")
    rows = q(
        """select uid, ts/60 m, count(*) c from api_call_logs
           where uid is not null and uid<>'' group by uid, m order by c desc limit 10""")
    for x in rows:
        u = users.get(x["uid"], {})
        print("  %-8s %-16s %s  %d 次/分钟" % (
            x["uid"][:8], (u.get("username") or "【已删】")[:16], f(x["m"] * 60)[:16], x["c"]))

    print("\n== 3. 每日用量 vs 上限（只列 >= 上限 10% 的）==")
    for kind, lim in LIMITS.items():
        rows = q("select user_id, day, count from usage where kind=? and count>=? order by count desc limit 10",
                 kind, max(1, lim // 10))
        if not rows:
            print("  %-6s 无（单日最高 %s）" % (
                kind, q("select max(count) m from usage where kind=?", kind)[0]["m"] or 0))
            continue
        print("  %-6s 上限 %d" % (kind, lim))
        for x in rows:
            u = users.get(x["user_id"], {})
            print("     %-8s %-16s %s %d (%.0f%%)" % (
                x["user_id"][:8], (u.get("username") or "【已删】")[:16], x["day"], x["count"],
                100.0 * x["count"] / lim))

    print("\n== 4. 孤儿身份（有调用痕迹 / 无 users 行 / 无墓碑）==")
    rows = q(
        """select a.uid, count(*) n, min(a.ts) a, max(a.ts) b from api_call_logs a
           where a.uid is not null and a.uid<>''
             and a.uid not in (select id from users)
             and a.uid not in (select uid from deleted_users)
           group by a.uid order by n desc""")
    print("  共 %d 个，累计 %d 次调用" % (len(rows), sum(x["n"] for x in rows)))
    for x in rows[:10]:
        print("     %-8s n=%-5d %s ~ %s" % (x["uid"][:8], x["n"], f(x["a"]), f(x["b"])))
    if len(rows) > 10:
        print("     ...（其余 %d 个省略）" % (len(rows) - 10))

    print("\n== 5. 注册/登录失败分布（撞库信号）==")
    for line in q(
        """select path, status, count(*) n from api_call_logs
           where path like '/api/auth/%' group by path, status order by path, status"""):
        print("  %-24s %s %d" % (line["path"], line["status"], line["n"]))

    print("\n== 6. 同一秒的并发登录（同一秒 >5 次的时刻）==")
    rows = q(
        """select ts, count(*) c from api_call_logs
           where path='/api/auth/login/' group by ts having c>5 order by c desc limit 10""")
    if not rows:
        print("  无")
    for x in rows:
        print("  %s  %d 次/秒" % (f(x["ts"]), x["c"]))

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
