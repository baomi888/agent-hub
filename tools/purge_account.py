"""停机维护用的完整账号清理入口；默认仅预览，不接收用户密码或平台密钥。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True, help="本站 users.id，不是微信 openid 或登录名")
    parser.add_argument("--apply", action="store_true", help="执行注销；不传只预览")
    parser.add_argument("--confirm-uid", default="", help="再次填写同一个 users.id")
    parser.add_argument("--writers-stopped", action="store_true", help="确认 API、worker 等写入进程已停止")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import account_deletion

    try:
        if not args.apply:
            print(json.dumps(account_deletion.preview(args.uid), ensure_ascii=False, indent=2))
            return 0
        result = account_deletion.purge(args.uid, confirm_uid=args.confirm_uid, writers_stopped=args.writers_stopped)
        if result["state"] == "done":
            print("账号注销完成：本站账号、第三方关联及可追溯的在线业务数据已清理。旧登录凭证已失效。")
        return 0
    except Exception as exc:
        # 不输出路径、清理清单、邮箱或数据库正文。
        print(
            f"注销未完成（{type(exc).__name__}）。请检查服务状态、存储权限和登记文件；"
            "如已有 pending 任务，修复后按同一 uid 重试。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
