"""运维建号 / 查 uid（管理员账号的唯一自助入口）。

为什么需要这个脚本
------------------
2026-10-29 安全加固后，`ADMIN_USERNAMES` 里的用户名在**自助注册入口被保留**——
任何人都不能注册它，防止"默认管理员名 + 开放注册"导致抢注提权。
代价是：管理员账号不能再从网页注册。本脚本补上这条运维路径。

同时它也是取 `ADMIN_UIDS` 的最方便方式：uid 是注册时生成的 uuid，
不可猜、不可抢注，是比用户名更可靠的管理员判据。

用法（服务器项目根目录，用项目 venv）
------------------------------------
    # 建一个普通账号（会交互式输入两遍密码）
    /root/venv/bin/python tools/create_user.py alice

    # 建一个管理员账号（名字先在 .env 的 ADMIN_USERNAMES 里配好）
    /root/venv/bin/python tools/create_user.py admin

    # 已有账号：只查 uid（把 uid 填进 .env 的 ADMIN_UIDS）
    /root/venv/bin/python tools/create_user.py alice --show-uid

安全约定
--------
* 密码只从终端读（getpass），**不接受命令行参数**——否则会留在 shell history 里；
* 同名账号已存在时不覆盖，只报 uid；
* 脚本不打印密码、不写日志。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import account, config  # noqa: E402


def _print_admin_hint(uid: str, username: str) -> None:
    names = {x.casefold() for x in (config.ADMIN_USERNAMES or ())}
    is_named_admin = username.casefold() in names
    is_uid_admin = uid in (config.ADMIN_UIDS or ())
    if is_uid_admin:
        print("该账号已在 ADMIN_UIDS 中，管理员身份已生效。")
    elif is_named_admin:
        print("该用户名在 ADMIN_USERNAMES 中，管理员身份已生效。")
    else:
        print("该账号是普通用户。要设为管理员，二选一：")
        print(f"  1) 在 .env 里加 ADMIN_UIDS={uid}（推荐，uid 不可猜）")
        print(f"  2) 在 .env 里加 ADMIN_USERNAMES={username}（旧方式，名字可猜）")
        print("改完 .env 后重启后端生效。")


def main() -> int:
    parser = argparse.ArgumentParser(description="创建账号 / 查询 uid")
    parser.add_argument("username", help="用户名（3~32 位，字母数字中文下划线点连字符）")
    parser.add_argument(
        "--show-uid",
        action="store_true",
        help="只查询已存在账号的 uid，不创建",
    )
    parser.add_argument(
        "--status",
        type=int,
        choices=(0, 1),
        default=1,
        help="1=正常（默认），0=停用",
    )
    args = parser.parse_args()

    name = account.normalize_name(args.username)
    existing = account.get_user_by_username(name)

    if args.show_uid:
        if not existing:
            print(f"账号不存在：{name}", file=sys.stderr)
            return 1
        print(f"username: {existing['username']}")
        print(f"uid:      {existing['id']}")
        _print_admin_hint(existing["id"], existing["username"])
        return 0

    if existing:
        print(f"账号已存在，不覆盖：{existing['username']}", file=sys.stderr)
        print(f"uid: {existing['id']}", file=sys.stderr)
        print("只查 uid 可用：--show-uid", file=sys.stderr)
        return 2

    err = account.check_name(name)
    if err:
        print(err, file=sys.stderr)
        return 1

    pwd1 = getpass.getpass("密码：")
    pwd2 = getpass.getpass("再输一次：")
    if pwd1 != pwd2:
        print("两次密码不一致", file=sys.stderr)
        return 1
    err = account.check_password(pwd1)
    if err:
        print(err, file=sys.stderr)
        return 1

    user = account.create_user(name, pwd1)
    if args.status != 1:
        account.set_status(user["id"], args.status)

    print(f"已创建：{user['username']}")
    print(f"uid:    {user['id']}")
    print(f"状态:   {'正常' if args.status == 1 else '停用'}")
    _print_admin_hint(user["id"], user["username"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
