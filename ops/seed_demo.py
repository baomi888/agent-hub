#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""演示账号：建一个现成账号并灌入可用数据，评委打开就有东西看。

为什么需要：现场注册要等配额、要现场填计划，讲三分钟还在注册页；
给一个已备考 3 天、有计划、有词卡、有分数曲线的账号，打开就是成品。

用法（服务器上一条命令）：
    /root/venv/bin/python ops/seed_demo.py

幂等：账号已存在就复用；已有备考计划就跳过灌数据，不会越跑越多。
口令会打印在最后，现场直接用。
"""

from __future__ import annotations

import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import account  # noqa: E402
from core import quota  # noqa: E402
from core import session as store  # noqa: E402
from service import cet  # noqa: E402
from service import checkin  # noqa: E402
from service import daily  # noqa: E402
from service import path as path_svc  # noqa: E402

DEMO_USER = "demo"
DEMO_PASS = "zhisui2026"

# 一小批真实会考的六级高频词，比 abcd 之类的占位词更像回事
WORDS: list[tuple[str, str]] = [
    ("sustainable", "可持续的"), ("allocate", "分配；拨出"), ("derive", "源自；推导"),
    ("inevitable", "不可避免的"), ("reluctant", "不情愿的"), ("comprehensive", "综合的"),
    ("diminish", "减少；削弱"), ("advocate", "提倡；拥护者"), ("coherent", "连贯的"),
    ("substitute", "替代；代替品"), ("reveal", "揭示"), ("undertake", "承担；着手"),
    ("exaggerate", "夸大"), ("elaborate", "详尽的；阐述"), ("fundamental", "根本的"),
    ("consistent", "一致的"), ("controversial", "有争议的"), ("emphasize", "强调"),
    ("anticipate", "预料"), ("accumulate", "积累"), ("compensate", "补偿"),
    ("distinguish", "区分"), ("efficient", "高效的"), ("generate", "产生"),
    ("implement", "实施"), ("initial", "最初的"), ("justify", "证明…正当"),
    ("maintain", "维持；主张"), ("obtain", "获得"), ("phenomenon", "现象"),
    ("reluctantly", "勉强地"), ("significant", "显著的"), ("transition", "过渡"),
    ("validate", "验证"), ("withstand", "经受住"), ("abstract", "抽象的；摘要"),
    ("beneficial", "有益的"), ("crucial", "关键的"), ("deteriorate", "恶化"),
    ("evaluate", "评估"), ("fluctuate", "波动"), ("guarantee", "保证"),
    ("incorporate", "纳入"), ("negotiate", "谈判"), ("overwhelm", "压倒"),
]


def ensure_user() -> dict:
    """有就复用，没有就建。"""
    found = account.get_user_by_username(DEMO_USER)
    if found:
        print(f"  账号已存在，复用：{DEMO_USER}")
        return found
    created = account.create_user(DEMO_USER, DEMO_PASS)
    print(f"  已创建账号：{DEMO_USER} / {DEMO_PASS}")
    return created


def seed(uid: str) -> None:
    daily._ensure_daily_tables()
    cet._ensure_cet_tables()
    checkin._ensure_checkin_tables()
    path_svc._ensure_path_tables()

    if cet.list_scores(uid) or cet.overview(uid)["plan"]:
        print("  已有备考数据，跳过灌数据（不会重复叠加）")
        return

    today = quota.day_key()

    # 1. 54 天备考计划（与申报书 B3 那份计划的长度一致）
    plan = cet.create_plan(uid, title="六级 54 天备考计划", days=54)
    print(f"  备考计划：{plan['title']}（{plan['total_days']} 天 × 5 模块）")

    # 2. 词汇本：45 词，其中前 15 个复习过两轮（step=2），其余复习过一轮（step=1）
    for i, (w, m) in enumerate(WORDS):
        item = cet.add_vocab(uid, w, m, source="六级核心词表")
        rounds = 2 if i < 15 else 1
        for _ in range(rounds):
            cet.review_vocab(uid, item["id"], True)
    print(f"  词汇本：{len(WORDS)} 词（15 个已复习两轮，30 个一轮）")

    # 3. 模考分数：两条，画出趋势 + 目标线
    cet.add_score(uid, "MOCK", 432, 500, note="首测")
    cet.add_score(uid, "MOCK", 473, 500, note="二战")
    print("  模考分数：432 → 473（目标 500）")

    # 4. 连续 3 天的每日总结（打卡页/学习路径的第 5 关靠它点亮）
    for i in range(3):
        d = (datetime.date.fromisoformat(today) - datetime.timedelta(days=i)).isoformat()
        with store._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_reports(report_id, owner_id, date, status, "
                "stats_json, content_json, model, generated_at, read_at) "
                "VALUES (?, ?, ?, 'ready', NULL, NULL, NULL, 0, NULL)",
                (f"demo-{uid[:8]}-{d}", uid, d),
            )
    print("  每日总结：最近 3 天（连续打卡 3 天）")

    # 5. 每日委托：一个启用的，演示"交给它自己跑"
    with store._get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO delegations(delegation_id, owner_id, name, kind, "
            "schedule_json, prompt, enabled, next_run_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'daily', ?, ?, 1, ?, ?, ?)",
            (
                f"demo-del-{uid[:8]}",
                uid,
                "每晚 21:00 复盘当日真题",
                '{"freq": "daily", "at": "21:00"}',
                "根据我今天的真题练习记录，指出错得最多的题型，并给明天 30 分钟能做完的补救任务。",
                0,
                0,
                0,
            ),
        )
    print("  每日委托：每晚 21:00 复盘（已启用）")


def main() -> None:
    print("===== 演示账号 =====")
    user = ensure_user()
    uid = user["id"]
    seed(uid)

    ov = path_svc.overview(uid)
    p = ov["progress"]
    # 知识库这一关不可能靠脚本点亮（要真上传文件），所以这里如实显示
    print(f"\n===== 学习路径 {p['done']} / {p['total']} 关（{p['percent']}%）=====")
    for ch in ov["chapters"]:
        names = "  ".join(
            ("[过]" if lv["status"] == "done" else "[当前]" if lv["status"] == "current" else "[锁]")
            + lv["name"]
            for lv in ch["levels"]
        )
        print(f"  {ch['name']}：{names}")

    print("\n账号：", DEMO_USER, " 口令：", DEMO_PASS)
    print("★ 还有一关是脚本没法造的：登录后往知识库上传任意 1 份资料，")
    print("  前两关会自动点亮（资料是自己传的，不能预先编）。")


if __name__ == "__main__":
    main()
