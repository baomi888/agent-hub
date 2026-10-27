"""启动期数据表迁移的回归钉。

背景：`_ensure_daily_tables()` 是懒执行的（刻意不在 import 时跑），所以「刚部署完、
还没人访问过 delegations」时 `PRAGMA table_info(delegations)` 会查不到新列，
容易被误判成部署失败。main.py 的 lifespan 里补了一次启动期迁移来消除这个歧义。

这里不真连库、不真预热：把 warmup / 迁移 / 配置检查都 mock 掉，只断言
「进入 lifespan 时确实调用过一次 _ensure_daily_tables」。
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class TestStartupMigration(unittest.TestCase):
    def test_lifespan_runs_daily_table_migration(self):
        """进入 lifespan 时应触发一次 daily 表建表/补列。"""
        calls: list[int] = []

        async def run_lifespan() -> None:
            with (
                mock.patch.object(main.pipelines, "warmup", lambda: None),
                mock.patch.object(main.config, "validate", lambda: []),
                mock.patch.object(
                    main.daily, "_ensure_daily_tables", lambda: calls.append(1)
                ),
            ):
                async with main.lifespan(main.app):
                    pass

        asyncio.run(run_lifespan())
        self.assertEqual(
            len(calls), 1, "lifespan 应在 yield 前调用一次 _ensure_daily_tables"
        )

    def test_lifespan_survives_migration_failure(self):
        """迁移失败（如库被锁）只告警，不能把整个后端启动带崩。"""

        async def run_lifespan() -> None:
            with (
                mock.patch.object(main.pipelines, "warmup", lambda: None),
                mock.patch.object(main.config, "validate", lambda: []),
                mock.patch.object(
                    main.daily, "_ensure_daily_tables", side_effect=RuntimeError("locked")
                ),
            ):
                # 不抛异常即通过：能正常进入并退出上下文 = 启动未被阻断
                async with main.lifespan(main.app):
                    pass

        asyncio.run(run_lifespan())


if __name__ == "__main__":
    unittest.main(verbosity=2)
