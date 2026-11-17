"""把历史里的自动化调用从「真实用户」里摘出来（一次性回填，可重复执行）。

为什么需要它
------------
2026-11-15 之前，写库时的自动化判定只有一层：脚本自报 `X-Verify: 1` 且身份可信。
可截图/取证脚本走的是**浏览器导航**，加不了自定义请求头 —— 于是它带着真人账号
`苞米呀` 的 cookie 把全站翻了一遍：7 天 12772 次调用、单小时峰值 1444 次，
占「真实用户」桶的 96%。数据已经写进去了，只改代码不会让昨天的数字变干净。

判据（保守，宁漏不误）
----------------------
nginx 的 access_log 里有 UA，但 apilog 当时没存 UA，也没有 IP。能对上的只有
**路径 + 秒**，而路径两侧形态不同：

    api_call_logs.path  = `/api/sessions/{sid}`     ← 路由模板
    access_log 里的路径 = `/api/sessions/9f3c.../`  ← 真实路径

所以先把两侧都归一成「形状」（`{xxx}`、uuid、纯数字、长 hex 段 → `*`），
再按 `(形状, 秒)` 对齐。标记条件刻意加严：

  **只有该 `(形状, 秒±1)` 上出现过自动化 UA、且**没有**任何普通浏览器请求时才标。**

同一秒里既有脚本又有真人时一律不标（那种情况下分不清哪条是谁的，
宁可让数字偏高 —— 偏高下次还能查出来，误标会让真人凭空消失且没人发现）。

局限性（写在前面，免得后人误以为是精确对账）
--------------------------------------------
- 只覆盖 nginx 还留着日志的那十几天（当前 15 天，`ls /var/log/nginx/baomi-access.log*`）。
- 直连 `127.0.0.1:8000` 的请求不进 nginx，回填不到。
- 秒级对齐在极端情况下会漏（脚本与日志的响应完成时刻跨秒）。

用法
----
    # 先看报告（默认，只读打开库，不写）
    /root/venv/bin/python tools/backfill_auto_calls.py

    # 确认后再写
    /root/venv/bin/python tools/backfill_auto_calls.py --apply

    # 只回填某几天
    /root/venv/bin/python tools/backfill_auto_calls.py --days 3 --apply
"""

from __future__ import annotations

import argparse
import glob
import gzip
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 以 `python tools/xxx.py` 运行时 sys.path[0] 是 tools/，`import core` 会失败。
# 把仓库根目录插进去 —— 与 tools/mark_internal_users.py 同一套做法。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 判"这条请求是什么客户端"的唯一实现放在 core/verify_tag.py，回填直接复用：
# 两边各写一份正则，迟早会出现"线上判成脚本、回填判成浏览器"的错位。
from core import verify_tag  # noqa: E402

# nginx 默认 combined 格式：
# 112.96.232.103 - - [15/Nov/2026:16:41:03 +0800] "GET /api/x HTTP/1.1" 200 12 "-" "UA"
LINE = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) \S+ "([^"]*)" "([^"]*)"'
)

TZ = timezone(timedelta(hours=8))


def shape(path: str) -> str:
    """把路径归一成「形状」：模板占位、uuid、纯数字、长 hex 段都换成 `*`。

    尾斜杠要去掉：`main.normalize_api_slash` 会把请求路径改成注册的那一种，
    于是库里是 `/api/kb/`、日志里是 `/api/kb`。形状里不该区分这两者。
    """
    p = (path or "").split("?")[0]
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return _shape_segments(p)


_RE_TEMPLATE = re.compile(r"^\{[^}]*\}$")
_RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_RE_NUM = re.compile(r"^\d+$")
_RE_HEX = re.compile(r"^[0-9a-f]+$", re.I)


def _shape_segments(p: str) -> str:
    out = []
    for seg in p.split("/"):
        if not seg:
            out.append("")
            continue
        if (
            _RE_TEMPLATE.match(seg)
            or _RE_UUID.match(seg)
            or _RE_NUM.match(seg)
            or (len(seg) >= 8 and _RE_HEX.match(seg))
        ):
            out.append("*")
        else:
            out.append(seg)
    return "/".join(out)


def _to_epoch(ts: str) -> int:
    """`15/Nov/2026:23:58:22 +0800` → Unix 秒。"""
    return int(datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z").timestamp())


def scan_logs(paths: list[str], wanted_days: set[int]) -> tuple[dict, set, Counter]:
    """扫日志，返回 (自动化形状键 → client 类型集合, 真人形状键集合, 统计)。

    键是 `(形状, 秒)`。同一个键既可能被脚本打过、也可能被真人打过（都在家、同一个 IP），
    所以两边都要收 —— 冲突的那些秒一律不标。
    """
    auto: dict[tuple[str, int], set[str]] = defaultdict(set)
    human: set[tuple[str, int]] = set()
    stat: Counter = Counter()
    for lg in paths:
        opener = gzip.open if lg.endswith(".gz") else open
        try:
            fh = opener(lg, "rt", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                m = LINE.match(line)
                if not m:
                    continue
                _ip, ts, _method, raw_path, _status, _ref, ua = m.groups()
                try:
                    ep = _to_epoch(ts)
                except ValueError:
                    continue
                # ★ 天序号必须按**东八区**算：`ep // 86400` 是 UTC 的天，
                # 而 `wanted_days` 是按本地时区算的 —— 直接用会让 08:00 之前的
                # 请求全部落到前一天（生产上表现为"早上的自动化流量回填不到"）。
                if (ep + 8 * 3600) // 86400 not in wanted_days:
                    continue
                if not raw_path.startswith("/api/"):
                    stat["skipped_non_api"] += 1
                    continue
                kind = verify_tag.classify_ua(ua)
                if not kind:
                    stat["skipped_no_ua"] += 1
                    continue
                key = (shape(raw_path), ep)
                if kind in verify_tag.AUTOMATED_CLIENTS:
                    auto[key].add(kind)
                    stat["auto_lines"] += 1
                    stat["auto_" + kind] += 1
                else:
                    human.add(key)
                    stat["human_lines"] += 1
    return auto, human, stat


def backfill(db: str, log_glob: str, days: int, apply: bool, limit_paths: int = 0) -> int:
    log_paths = sorted(glob.glob(log_glob))
    if limit_paths:
        log_paths = log_paths[:limit_paths]
    if not log_paths:
        print(f"没有找到日志：{log_glob}")
        return 2

    now = int(time.time())
    today = (now + 8 * 3600) // 86400  # 东八区的"天序号"
    wanted = {today - i for i in range(days)}

    print(f"扫描日志 {len(log_paths)} 个文件...")
    auto, human, stat = scan_logs(log_paths, wanted)
    print(
        f"  自动化行 {stat['auto_lines']}（无头 {stat['auto_headless']} / 脚本 {stat['auto_script']}）"
        f"｜真人行 {stat['human_lines']}｜非 /api 跳过 {stat['skipped_non_api']}"
        f"｜无 UA 跳过 {stat['skipped_no_ua']}"
    )
    if not auto:
        print("日志里没有自动化流量，没什么可回填的。")
        return 0

    start = (today - days + 1) * 86400 - 8 * 3600
    end_excl = (today + 1) * 86400 - 8 * 3600

    uri = f"file:{db}?mode={'rw' if apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, ts, path, uid, internal, client FROM api_call_logs"
        " WHERE ts >= ? AND ts < ? AND internal = 0",
        (start, end_excl),
    ).fetchall()
    print(
        f"区间 {datetime.fromtimestamp(start, TZ).date()} ~ "
        f"{datetime.fromtimestamp(end_excl - 1, TZ).date()} 内待检查的调用 {len(rows)} 条"
    )

    hit: list[tuple[str, int]] = []
    per_uid: Counter = Counter()
    per_client: Counter = Counter()
    ambiguous = 0
    for r in rows:
        sh = shape(str(r["path"]))
        ts = int(r["ts"])
        kinds: set[str] = set()
        conflict = False
        for d in (-1, 0, 1):
            key = (sh, ts + d)
            if key in human:
                conflict = True
                break
            kinds |= auto.get(key, set())
        if conflict:
            if auto.get((sh, ts), set()):
                ambiguous += 1
            continue
        if not kinds:
            continue
        kind = "headless" if "headless" in kinds else "script"
        hit.append((kind, int(r["id"])))
        per_uid[str(r["uid"] or "-")] += 1
        per_client[kind] += 1

    print(
        f"可回填 {len(hit)} 条（无头 {per_client['headless']} / 脚本 {per_client['script']}）"
        f"｜同秒脚本与真人并存、放弃标记 {ambiguous} 条"
    )

    # 按 uid 报明细，人眼能核对"是不是把真人误伤了"
    if per_uid:
        names = {}
        for row in conn.execute("SELECT id, username FROM users").fetchall():
            names[str(row["id"])] = str(row["username"])
        print("\n按账号：")
        for uid, n in per_uid.most_common(15):
            name = names.get(uid, "(已注销)") if uid != "-" else "(未登录)"
            print(f"  {name:<18} {n:>6d} 次")

    if not apply:
        print("\n（dry-run，没有写库。加 --apply 才写）")
        conn.close()
        return 0

    if not hit:
        conn.close()
        return 0

    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    bak = f"{db}.bak-backfill-{stamp}"
    conn.close()
    shutil.copy2(db, bak)
    print(f"\n已备份数据库 → {bak}")

    conn = sqlite3.connect(db, timeout=30)
    conn.execute("BEGIN")
    conn.executemany("UPDATE api_call_logs SET internal = 1, client = ? WHERE id = ?", hit)
    conn.commit()
    print(f"写入完成：{len(hit)} 条标为自动化")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填历史自动化调用的打标")
    ap.add_argument("--db", default=os.environ.get("BAOMI_DB", "/root/data/sessions.db"))
    ap.add_argument("--logs", default="/var/log/nginx/baomi-access.log*")
    ap.add_argument("--days", type=int, default=15, help="回填最近多少天（受日志保留期限制）")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    args = ap.parse_args(argv)
    return backfill(args.db, args.logs, max(1, min(90, args.days)), args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
