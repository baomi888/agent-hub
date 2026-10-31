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
                last_login_at INTEGER,
                status        INTEGER NOT NULL DEFAULT 1
            );
            -- NOCASE 让 ASCII 大小写视为同一个用户（Abc 和 abc 不能各注册一份）
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name ON users(username COLLATE NOCASE);
        """)
    _ensure_columns()


def _ensure_columns() -> None:
    """给老库补列（幂等）。

    对应 sky-take-out 的 `employee.status`：1 正常 / 0 锁定。
    老库里没有这一列，直接 ALTER 补上并默认 1（已注册用户视为正常），
    这样升级不需要停机迁移脚本。
    """
    with _get_conn() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "status" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN status INTEGER NOT NULL DEFAULT 1")
        # 登录失败锁定（2026-10-29 安全加固 S-03）：连续失败计数与解锁时刻。
        # 老库同样幂等补列，默认 0 表示"从未失败 / 未锁定"。
        if "failed_count" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN failed_count INTEGER NOT NULL DEFAULT 0"
            )
        if "lock_until" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN lock_until INTEGER NOT NULL DEFAULT 0")


_ensure_tables()


# ---------- 读写 ----------


def get_user(uid: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, last_login_at, status, "
            "failed_count, lock_until FROM users WHERE id = ?",
            (uid,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_username(name: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, last_login_at, status, "
            "failed_count, lock_until FROM users WHERE username = ?",
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


# 登录失败原因。对外**不**区分前三种，只在内部区分（日志 / 后续风控用）
LOGIN_OK = "ok"
LOGIN_NO_USER = "no_user"
LOGIN_BAD_PASSWORD = "bad_password"
LOGIN_LOCKED = "locked"

# 状态值（对应 sky-take-out 的 StatusConstant.ENABLE / DISABLE）
STATUS_ENABLE = 1
STATUS_DISABLE = 0


def _record_login_failure(uid: str) -> None:
    """记一次口令失败；达到阈值就锁定一段时间（S-03 撞库防护）。

    只惩罚"连续"失败：成功登录会清零，锁到期后计数也已归零。
    阈值 / 时长取 config，0 表示关闭（一键回退）。
    """
    max_fails = int(getattr(config, "AUTH_MAX_FAILS", 0) or 0)
    lock_minutes = int(getattr(config, "AUTH_LOCK_MINUTES", 0) or 0)
    if not max_fails or not lock_minutes:
        return
    with _get_conn() as conn:
        conn.execute("UPDATE users SET failed_count = failed_count + 1 WHERE id = ?", (uid,))
        row = conn.execute(
            "SELECT failed_count FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if row and int(row["failed_count"]) >= max_fails:
            conn.execute(
                "UPDATE users SET lock_until = ?, failed_count = 0 WHERE id = ?",
                (int(time.time()) + lock_minutes * 60, uid),
            )


def _clear_login_failure(uid: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET failed_count = 0, lock_until = 0 WHERE id = ?", (uid,)
        )


def login(username: str, password: str) -> tuple[dict | None, str]:
    """按 sky-take-out 的顺序校验账号，返回 (用户, 原因)。

    顺序是刻意对齐 `EmployeeServiceImpl.login` 的：

      1. 按用户名查库          → 查不到 = no_user
      2. 口令比对              → 不匹配 = bad_password
      3. 账号状态              → 锁定   = locked
      4. 全过 → 刷新 last_login_at，返回用户

    **与 sky 的刻意差异**：sky 把"账号不存在"和"密码错误"分成两个异常、给
    前端两句不同的话；这里对外统一成一句"用户名或密码不正确"。原因是本项目
    注册是自助开放的，区分这两者等于免费送一个"哪些用户名存在"的枚举接口。
    状态（locked）则必须区分——用户需要知道"不是密码错了，是号被停了"。

    第 1 步查不到用户时仍然跑一次等价耗时的散列，让两种失败的响应时间一致，
    否则时间差本身就泄露了"用户存不存在"。
    """
    row = get_user_by_username(username)
    if not row:
        verify_password(password, "pbkdf2_sha256$1$AA==$AA==")
        return None, LOGIN_NO_USER

    # 连续失败锁定（S-03）：锁定期内直接拒，不再跑一次昂贵的 PBKDF2。
    # 放在口令校验之前，撞库脚本连"试错"的机会都没有。
    now = int(time.time())
    lock_until = int(row.get("lock_until") or 0)
    if lock_until > now:
        return None, LOGIN_LOCKED

    if not verify_password(password, row["password_hash"]):
        _record_login_failure(row["id"])
        return None, LOGIN_BAD_PASSWORD
    if int(row.get("status", STATUS_ENABLE) or 0) == STATUS_DISABLE:
        return None, LOGIN_LOCKED
    _clear_login_failure(row["id"])
    touch_login(row["id"])
    return _public(row), LOGIN_OK


def authenticate(username: str, password: str) -> dict | None:
    """校验账号口令，通过返回公开 dict，否则 None。

    保留这个入口是给老调用方用的（语义就是"能不能登录"）；
    需要知道失败原因的用 login()。
    """
    user, reason = login(username, password)
    return user


def set_status(uid: str, status: int) -> bool:
    """启用 / 锁定账号，返回是否真的改到了行（对应 sky 的 startOrStop）。"""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET status = ? WHERE id = ?", (int(status), uid)
        )
        return bool(cur.rowcount)


def touch_login(uid: str) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (int(time.time()), uid))
