"""把自测/演示账号标记为「内部」，让看板的真实用户视图只算真人。

为什么需要这个工具
------------------
`users.is_internal` 这一列默认 0，而存量库里早有几百个脚本注册的账号
（`*_probe`、`demo*`、`shot*`、`qelive*` …）。不批量回填的话，看板上的
「真实用户」视图等于「全量」，功能做完却看不出任何区别 —— 而手工一个个点
三百次没人会做，于是这个视图会永远停在"看起来没用"的状态。

用法
----
    # 1. 先看（默认就是 dry-run，不会写库）
    python tools/mark_internal_users.py

    # 2. 看着没问题再写
    python tools/mark_internal_users.py --apply

    # 3. 标错了 / 想重来
    python tools/mark_internal_users.py --unmark --apply
    python tools/mark_internal_users.py --unmark --apply --names demo_shiyan,qelive1023a

    # 4. 对着一份拷贝试算，别碰生产
    python tools/mark_internal_users.py --db data/sessions.db

判断规则：为什么只认"明显是脚本"的名字
--------------------------------------
**误标真实用户比漏标严重得多**：漏标只是让真实数据里混进几条自测数据，
数字偏大一点；误标会让一个真人的调用从「真实用户」里消失，而这个错误
不会报错、不会有人发现，只会让结论悄悄偏离。

所以规则刻意保守 —— 只命中那些**人工不会起**的名字（见下面的 RULES）。
像 `12345`、`111`、`456`、`包包` 这种虽然可能是自测，但真人也会这么起名，
一律**不标**，留给看板上的「标为内部」按钮人工判断。

管理员（ADMIN_UIDS / ADMIN_USERNAMES）不需要标记：看板在查询时即时排除，
本工具只把它们列出来提醒你别重复标。
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ==================== 规则（改这里就是改行为，改完先 dry-run）====================
#
# 每条都注明"命中的是哪种历史遗留"，方便将来看到陌生账号时判断该不该补规则。
RULES = [
    # 探针/联调脚本：probe、*_probe_2026、probeadmin、探针注册30417
    (r"probe", "探针脚本注册的账号"),
    # UI / 端到端截图脚本：uidemo*、cdemo*、hdemo*、shot*、stopprobe*、cap_*、toolshot*
    (r"demo", "演示/截图脚本注册的账号"),
    (r"^shot\d", "截图脚本按时间戳起名"),
    (r"^toolshot", "工具截图脚本"),
    (r"^cap_", "截图采集脚本"),
    # 接口/契约/回归测试：testuser、contracttest、flowuitest、e2e*、hyb_e2e_*
    (r"^test", "测试账号"),
    (r"test$", "测试账号"),
    (r"^e2e", "端到端测试"),
    (r"_e2e_", "端到端测试"),
    (r"^contracttest$", "契约测试"),
    (r"^flowuitest$", "流程 UI 测试"),
    # 验收/核对类：qa_ui_1014、zz_accept1014、zz_wb1014、welcomecheck、stdcheck27、uicheck
    (r"^qa_", "验收脚本"),
    (r"^zz_", "验收脚本（zz 前缀）"),
    (r"check", "核对脚本"),
    (r"^verify", "校验脚本"),
    (r"verify\d", "校验脚本"),
    # 线上演练/迁移：qelive1023a-d、dprobe1105、wlive711205、baomi_live_demo
    (r"^qelive", "线上演练脚本"),
    (r"^dprobe", "线上探针"),
    (r"^wlive", "线上演练"),
    (r"_live_", "线上演练"),
    # 调试/灰度：dbg*、sort_probe、marks_probe、cacheprobe1、merge_ui_1014、gate1001490
    (r"^dbg", "调试脚本"),
    (r"^gate\d", "闸门校验脚本"),
    (r"^merge_ui_", "UI 合并校验"),
    (r"^cacheprobe", "缓存探针"),
    # 早期联调：localtest*、geotester、probe_a、cetprobe*、onbprobe*、hyb_*
    (r"^localtest", "本地联调账号"),
    (r"^geotester", "地理功能联调"),
    (r"^hyb_", "混合架构联调"),
    # 安全审计留下的账号（security/scripts/disable_accounts.py 那批）
    (r"^sec_tester", "安全审计账号"),
    (r"^auditadmin", "安全审计管理员"),
    (r"^probeadmin$", "探针管理员"),
]

# 精确名单：名字本身看不出是脚本，但确实是自测号（人工确认过的）。
EXACT = {
    "demo",
    "testuser",
    "welcomecheck",
    "emptydemo",
    "cropdemo",
    "probe2v1107",
    "探针注册30417",
    "baomi_live_demo",
    "baomi_probe_2026",
    "stdcheck27",
    "plain27x",
}

# 明确**不标**的名单：像自测但可能是真人（或者是管理员账号，另有机制排除）。
# 写在这里是为了让"为什么这几个人没被标"有据可查，而不是靠记忆。
KEEP_REAL = {
    "12345",
    "1234",
    "123",
    "111",
    "456",
    "700",
    "777",
    "666",
    "321",
    "admin",  # 管理员，按配置排除，不需要标记
}

_COMPILED = [(re.compile(p, re.I), why) for p, why in RULES]


def classify(name: str) -> str:
    """返回命中的规则说明；不认识就返回空串（表示"当真人看"）。"""
    if name in KEEP_REAL:
        return ""
    if name in EXACT:
        return "人工确认过的自测号"
    for rx, why in _COMPILED:
        if rx.search(name):
            return why
    return ""


def _conn(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    ap = argparse.ArgumentParser(description="标记自测/演示账号为内部账号")
    ap.add_argument("--db", default="", help="库路径，默认取 config 里的主库")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只打印）")
    ap.add_argument("--unmark", action="store_true", help="反向：取消标记")
    ap.add_argument("--names", default="", help="只处理这些用户名（逗号分隔），配合 --unmark 用")
    ap.add_argument("--days", type=int, default=30, help="影响面按最近多少天算，默认 30")
    args = ap.parse_args()

    if args.db:
        db_path = args.db
    else:
        from core import session as store

        db_path = store._db_path()  # noqa: SLF001
    if not Path(db_path).exists():
        print("库不存在: " + db_path)
        return 2

    con = _conn(db_path)

    # 老库可能还没有这一列（没跑过新版本启动）——先看一眼，缺了就提示而不是崩掉
    cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if "is_internal" not in cols:
        print("!! 这个库还没有 users.is_internal 列。先启动一次服务（或跑一次 account._ensure_tables）再来。")
        return 3

    cutoff = int(time.time()) - max(1, args.days) * 86400
    rows = con.execute(
        "SELECT u.id AS uid, u.username AS username, u.created_at AS created_at,"
        "       u.is_internal AS is_internal,"
        "       (SELECT COUNT(*) FROM api_call_logs al WHERE al.uid = u.id AND al.ts >= ?) AS calls"
        "  FROM users u ORDER BY calls DESC, u.created_at ASC",
        (cutoff,),
    ).fetchall()

    total_calls = con.execute(
        "SELECT COUNT(*) FROM api_call_logs WHERE ts >= ?", (cutoff,)
    ).fetchone()[0]

    if args.unmark:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        targets = [r for r in rows if r["is_internal"] == 1 and (not names or r["username"] in names)]
        verb = "取消标记"
    else:
        targets = [r for r in rows if r["is_internal"] == 0 and classify(r["username"])]
        verb = "标为内部"

    print("库: " + str(db_path))
    print("窗口: 最近 %d 天，区间内总调用 %d 次" % (args.days, total_calls))
    print("待%s: %d 个账号" % (verb, len(targets)))
    print("")

    if not targets:
        print("没有需要处理的账号。")
        return 0

    hit_calls = sum(int(r["calls"] or 0) for r in targets)
    width = max([len(str(r["username"])) for r in targets] + [8])
    for r in targets:
        why = "已标记" if args.unmark else classify(str(r["username"]))
        print(
            "  %-*s  %7d 次  %5.1f 小时前注册  %s"
            % (width, r["username"], int(r["calls"] or 0), (time.time() - r["created_at"]) / 3600, why)
        )

    pct = (hit_calls / total_calls * 100) if total_calls else 0.0
    print("")
    print(
        "这些账号在区间内共有 %d 次调用，占全量的 %.1f%% —— 标记后真实用户视图会少这么多。"
        % (hit_calls, pct)
    )

    if not args.apply:
        print("")
        print("** 以上是预演，没有写库。** 确认无误后加 --apply 再跑一次。")
        return 0

    con.close()
    rw = sqlite3.connect(db_path)
    try:
        flag = 0 if args.unmark else 1
        rw.executemany(
            "UPDATE users SET is_internal = ? WHERE id = ?",
            [(flag, r["uid"]) for r in targets],
        )
        rw.commit()
    finally:
        rw.close()
    print("")
    print("已写入 %d 行（is_internal=%d）。看板刷新即可看到变化。" % (len(targets), flag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
