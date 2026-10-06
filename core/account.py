# -*- coding: utf-8 -*-
"""账号：users 表与口令校验。

为什么和会话分开两个文件
-----------------------
虽然共用同一个 SQLite 文件（data/sessions.db），但关注点不同：
session 管"聊了什么"，这里管"是谁"。连接直接复用 session 的 helper，
不另开一套 SQLite 参数（WAL / row_factory / 自动提交必须完全一致，
否则会出现一边读到另一边没提交的脏东西）。

为什么用用户名不用邮箱
---------------------
这是课堂 demo 型部署，没有邮件服务，找回密码无从谈起。邮箱字段留而不用，
只会让注册多一道门槛。将来要接第三方登录时，加一张 identities 表即可，
users 表一行都不用改。
"""

import re
import sqlite3
import time
import uuid

from core import config
from core.auth import hash_password, verify_password
from core.session import _get_conn

# 用户名规则：字母/数字/下划线/点/连字符/中日韩文字，3~32 位
# 不含空格是刻意的——用户名出现在知识库 id、会话标题里，带空格会一路脏下去
_NAME_RE = re.compile(r"^[\w.\-]+$")

MIN_NAME = 3
MAX_NAME = 32
MIN_PASSWORD = 6
MAX_PASSWORD = 128


# ---------- 内部工具 ----------

def _public(row) -> dict:
    """把数据库行转成可以安全返回给前端的 dict。

    刻意不包含 password_hash——漏一次就是全站口令泄露，靠自觉不如靠结构。
    """
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def normalize_name(name: str) -> str:
    """用户名归一化：去首尾空白。"""
    return (name or "").strip()


def check_name(name: str) -> str | None:
    """校验用户名，合法返回 None，不合法返回给用户看的中文提示。"""
    n = normalize_name(name)
    if len(n) < MIN_NAME or len(n) > MAX_NAME:
        return f"用户名需 {MIN_NAME}~{MAX_NAME} 个字符"
    if not _NAME_RE.match(n):
        return "用户名只能包含字母、数字、中文、下划线、点或连字符"
    return None


def check_password(pwd: str) -> str | None:
    """校验口令，合法返回 None，不合法返回给用户看的中文提示。

    只卡长度，不卡复杂度：这是自用型部署，复杂度规则逼出来的往往是
    "Abcd1234!" 这种人类记不住、撞库字典里却排前几的口令。
    真要提速安全，加第二因子比加规则有用。
    """
    if len(pwd) < MIN_PASSWORD or len(pwd) > MAX_PASSWORD:
        return f"密码需 {MIN_PASSWORD}~{MAX_PASSWORD} 位"
    return None


# ---------- 建表 ----------

def _ensure_tables() -> None:
    """首次运行时创建 users 表（幂等）。"""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                username      TEXT NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at    INTEGER NOT NULL,
                last_login_at INTEGER
            );
            -- NOCASE 让 ASCII 大小写视为同一个用户（Abc 和 abc 不能各注册一份）
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name ON users(username COLLATE NOCASE);
        """)


_ensure_tables()


# ---------- 读写 ----------

def get_user(uid: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, last_login_at FROM users WHERE id = ?",
            (uid,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_username(name: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, last_login_at FROM users WHERE username = ?",
            (normalize_name(name),),
        ).fetchone()
        return dict(row) if row else None


def create_user(username: str, password: str) -> dict:
    """注册新账号，返回公开字段的 dict。

    重名抛 sqlite3.IntegrityError，由 API 层翻译成 409——这里不自己吞异常，
    因为"ename 被占用"在不同调用点可能需要不同的处理方式。
    """
    uid = uuid.uuid4().hex
    now = int(time.time())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, normalize_name(username), hash_password(password), now, now),
        )
    row = get_user(uid)
    return _public(row)


def authenticate(username: str, password: str) -> dict | None:
    """校验账号口令，通过返回公开 dict，否则 None。

    用户名不存在和口令错返回同一个结果，避免被用来枚举注册用户。
    """
    row = get_user_by_username(username)
    if not row:
        # 走一次等价耗时的散列，让"用户不存在"和"密码错"的响应时间一致
        verify_password(password, "pbkdf2_sha256$1$AA==$AA==")
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    touch_login(row["id"])
    return _public(row)


def touch_login(uid: str) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (int(time.time()), uid))


def count_users() -> int:
    with _get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def list_users() -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, created_at, last_login_at FROM users ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
