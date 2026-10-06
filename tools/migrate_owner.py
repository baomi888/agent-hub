# -*- coding: utf-8 -*-
"""把改造前的存量数据认领给指定账号。

背景
----
多用户改造之后，会话按 owner_id 隔离、知识库按归属表隔离，
而改造前留下的数据里 owner_id 是 NULL、知识库也没有主人 ——
按"默认拒绝"的口径，它们**谁都看不见**。这是刻意的安全选择：
宁可暂时看不到，也不能让第一个注册的人顺手把别人的历史认领走。

所以需要一个显式动作把它们交给该拿的人，这就是本脚本。

用法（在服务器上，项目根目录执行）
----------------------------------
    # 1) 先演练：只报数，不写盘
    /root/venv/bin/python tools/migrate_owner.py --username 你的用户名

    # 2) 确认数字对得上，再真正落盘
    /root/venv/bin/python tools/migrate_owner.py --username 你的用户名 --apply

注意
----
- 服务正在跑也能执行：SQLite 是 WAL，归属表有独立的写锁。
  但为了让前端立刻看到，执行完最好重启一次后端。
- 认领是**不可逆**的：一旦归到某人名下，其他人就再也看不到了。
  所以认领对象一定要确认清楚，一次只认领给一个人。
- 已经归属的数据不受影响，只补 NULL / 无主的那些。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import account, session as store  # noqa: E402
from kb import pipelines  # noqa: E402


def _count_orphans() -> tuple[int, int]:
    """统计待认领的会话数与知识库数（不改动任何数据）。"""
    with store._get_conn() as conn:
        conv = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE owner_id IS NULL"
        ).fetchone()["n"]

    try:
        cols = [c.name for c in pipelines.chroma_client().list_collections()]
    except Exception:
        cols = []
    kb = sum(
        1 for c in cols
        if c.startswith("kb_") and pipelines.owner_of(c[3:]) is None
    )
    return conv, kb


def main() -> int:
    ap = argparse.ArgumentParser(description="把改造前的存量会话/知识库认领给某个账号")
    ap.add_argument("--username", required=True, help="要认领给谁（必须先注册过）")
    ap.add_argument("--apply", action="store_true", help="真正写入；不加则只演练")
    args = ap.parse_args()

    user = account.get_user_by_username(args.username)
    if not user:
        print(f"找不到用户：{args.username}")
        print("先在页面上注册一个账号，再回来执行本脚本。")
        return 1
    uid = user["id"]

    conv, kb = _count_orphans()
    print("=" * 56)
    print("存量数据认领" + ("" if args.apply else "（演练，不写盘）"))
    print("=" * 56)
    print(f"  目标账号    {args.username}  (id={uid})")
    print(f"  待认领会话  {conv} 条")
    print(f"  待认领知识库 {kb} 个")

    if conv == 0 and kb == 0:
        print("\n没有需要认领的数据（可能已经认领过了）。")
        return 0

    if not args.apply:
        print("\n确认无误后加 --apply 再执行一次。")
        return 0

    n_conv = store.claim_orphan_conversations(uid)
    n_kb = pipelines.claim_orphan_kbs(uid)
    print(f"\n已认领：会话 {n_conv} 条，知识库 {n_kb} 个 -> {args.username}")
    print("建议重启后端让前端立刻看到（bash deploy.sh restart）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
