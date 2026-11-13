"""删号必须吊销令牌 —— 2026-11-13 线上事故的回归。

## 事故长什么样

那天 `/api/auth/register` 返回 200 共 37 次，可 `users` 表当天新增 0 行；
这些"已经不存在"的 uid 却还在建会话、写 usage，`conversations` 里留下 7 个
孤儿 owner、`usage` 里 60 个孤儿 uid，看板上还把它们算成活跃用户。

## 根因

`core/auth.current_user_id` 在 users 表查不到人时按「代数 0」放行（本意是
兼容测试里的临时 uid）。于是 `DELETE FROM users` 并不能让令牌失效 ——
签名合法、代数对得上，一路绿。

## 本文件锁死三件事

1. `delete_user()` 删行 + 写墓碑，幂等（第二次返回 False）；
2. 删号后同一枚令牌**立刻** 401，不靠过期；
3. 反证：绕过 delete_user 直接 DELETE FROM users 时，旧令牌仍然能过 ——
   证明「现在是被墓碑拦住的」，而不是别的什么碰巧生效了。第 3 条是这份
   测试真正的价值，去掉墓碑它必须变绿、上面两条必须变红。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest
from fastapi import HTTPException

from core import account, session
from core.auth import create_token, current_user_id


class _URL:
    scheme = "http"


class _Req:
    """够用的 Request 替身：current_user_id 只读 headers / cookies / url / state。"""

    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.url = _URL()
        self.state = type("S", (), {})()


def _req(tok: str) -> _Req:
    return _Req(cookies={"baomi_session": tok})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库（绝不碰 data/sessions.db）。账号表由 account 自己建。"""
    path = str(tmp_path / "purge.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables()
    return path


def _make_user(name: str = "qa_purge_probe") -> str:
    return account.create_user(name, "secret123")["id"]


def test_delete_user_writes_tombstone_and_is_idempotent(env):
    uid = _make_user()
    tok = create_token(uid, username="qa_purge_probe")

    # 删之前：这枚令牌是真的能用
    assert current_user_id(_req(tok)) == uid

    assert account.delete_user(uid, reason="cleanup_qa") is True
    assert account.get_user(uid) is None
    assert account.is_deleted(uid) is True

    # 幂等：已经没了就返回 False，不抛异常（清理脚本重复跑不该炸）
    assert account.delete_user(uid) is False


def test_token_is_rejected_right_after_delete(env):
    uid = _make_user()
    tok = create_token(uid, username="qa_purge_probe")
    account.delete_user(uid, reason="cleanup_qa")

    with pytest.raises(HTTPException) as exc:
        current_user_id(_req(tok))
    assert exc.value.status_code == 401
    # 文案要说得清是"账号没了"，不是"密码错了"
    assert "注销" in exc.value.detail


def test_without_tombstone_the_token_still_passes(env):
    """反证：手工删行、不写墓碑 → 旧令牌照样通过。

    这条断言就是 bug 的现场快照：它现在是绿的，说明"拦人的是墓碑"这个因果成立。
    哪天有人把墓碑逻辑删了，上面两条会红、这条仍然绿 —— 正好指向真正的原因。
    """
    uid = _make_user()
    tok = create_token(uid, username="qa_purge_probe")
    with session._get_conn() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))

    assert account.get_user(uid) is None
    assert account.is_deleted(uid) is False
    assert current_user_id(_req(tok)) == uid


def test_cleanup_script_leaves_tombstone(tmp_path):
    """清理脚本删号后也要留墓碑 —— 否则跑完脚本，那些旧 cookie 继续能用。

    用独立进程跑：脚本自己 `sqlite3.connect`，不 import 项目模块，
    在同进程里调会把 pytest 的库路径和它自己的混在一起。
    """
    path = str(tmp_path / "cleanup.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL,"
        " password_hash TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " last_login_at INTEGER, status INTEGER DEFAULT 1);"
        # 脚本会先收这个账号名下的会话再删（否则留孤儿消息），空表也要有
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, owner_id TEXT,"
        " title TEXT NOT NULL, kb_id TEXT, created_at INTEGER NOT NULL,"
        " updated_at INTEGER NOT NULL);"
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (?,?,?,?)",
        ("uid-qa-1", "qa123456", "x", 0),
    )
    conn.commit()
    conn.close()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "ops", "cleanup_qa.py"), "--apply"],
        capture_output=True,
        text=True,
        env={**os.environ, "BAOMI_DB": path},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert conn.execute("SELECT uid, reason FROM deleted_users").fetchall() == [
            ("uid-qa-1", "cleanup_qa")
        ]
    finally:
        conn.close()
