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

import json
import os
import re
import sqlite3
import time
import uuid

from core import config
from core import session as _session
from core.auth import hash_password, verify_password
from core.session import _get_conn

# 用户名规则：字母/数字/下划线/点/连字符/中日韩文字，3~32 位
# 不含空格是刻意的——用户名出现在知识库 id、会话标题里，带空格会一路脏下去
_NAME_RE = re.compile(r"^[\w.\-]+$")

MIN_NAME = 3
MAX_NAME = 32
# 8 位起：6 位 + 纯数字在撞库字典里几乎是必中项（2026-11 安全加固）。
MIN_PASSWORD = 8
MAX_PASSWORD = 128

# 撞库字典里排最前的口令。只收"一眼就该拒"的，不做复杂度规则——
# 复杂度规则逼出来的是 Abcd1234! 这种人类记不住、字典里却靠前的口令。
_WEAK_PASSWORDS = frozenset(
    {
        "12345678",
        "123456789",
        "1234567890",
        "11111111",
        "88888888",
        "00000000",
        "password",
        "password1",
        "passw0rd",
        "qwerty123",
        "qwertyui",
        "admin123",
        "abc12345",
        "abcd1234",
        "a1234567",
        "aa123456",
        "iloveyou",
        "woaini1314",
        "1qaz2wsx",
        "asd123456",
    }
)

# 登录名最多改几次。正常人改名一两次，反复改多半是出了问题，
# 而"没有邮箱/手机号可找回"意味着改完忘了只能找管理员翻审计日志。
MAX_NAME_CHANGES = 3


# ---------- 内部工具 ----------


def _public(row) -> dict:
    """把数据库行转成可以安全返回给前端的 dict。

    刻意不包含 password_hash——漏一次就是全站口令泄露，靠自觉不如靠结构。
    也不包含 token_valid_after / name_change_count：前者是安全闸门，后者是
    风控计数，都不该让前端看见（前端只需要知道"还能不能改"）。
    """
    return {
        "id": row["id"],
        "username": row["username"],
        # 空串 = 没设过，前端回落到 username / 首字母头像
        "nickname": row.get("nickname") or "",
        "avatar": row.get("avatar") or "",
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        # 只给"还能改几次"这个结果，不给原始计数：用户需要知道还剩多少，
        # 不需要知道内部是怎么记的（也方便以后改计数策略而不动前端）
        "name_changes_left": max(
            0, MAX_NAME_CHANGES - int(row.get("name_change_count") or 0)
        ),
    }


def normalize_name(name: str) -> str:
    """用户名归一化：去首尾空白。"""
    return (name or "").strip()


def check_name(name: str) -> str | None:
    """校验用户名，合法返回 None，不合法返回给用户看的中文提示。"""
    n = normalize_name(name)
    if len(n) < MIN_NAME or len(n) > MAX_NAME:
        return f"登录名需 {MIN_NAME}~{MAX_NAME} 个字符"
    if not _NAME_RE.match(n):
        return "登录名只能包含字母、数字、中文、下划线、点或连字符"
    return None


def check_password(pwd: str, username: str = "") -> str | None:
    """校验口令，合法返回 None，不合法返回给用户看的中文提示。

    只卡长度 + 一小撮结构性弱口令（纯重复字符、字典头部、与登录名相同），
    不做大小写/符号混排的复杂度规则：那是自用型部署里最容易被"Abcd1234!"
    敷衍过去的规则，真正有用的是长度和"别用撞库字典里的前几名"。

    ``username`` 可选：注册/改名时传进来，拒绝"登录名就是密码"。
    """
    if len(pwd) < MIN_PASSWORD or len(pwd) > MAX_PASSWORD:
        return f"密码需 {MIN_PASSWORD}~{MAX_PASSWORD} 位"
    low = pwd.casefold()
    if low in _WEAK_PASSWORDS:
        return "这个密码太常见了，换一个不容易被猜到的"
    if len(set(pwd)) == 1:
        return "密码不能是同一个字符重复"
    if pwd.isdigit():
        return "密码不能全是数字，请混入字母"
    if username and low == normalize_name(username).casefold():
        return "密码不能和登录名相同"
    return None


def is_reserved_admin_name(name: str) -> bool:
    """该名字是否被配置为管理员（ADMIN_USERNAMES）。

    两个入口都要过这一关，这正是本函数放在 account 而不是 api 的原因：
      - 注册：否则第一个抢注者直接变成管理员；
      - **改登录名**：否则任何已登录用户把名字改成 "苞米呀" 就白拿管理员权限。

    第二条比第一条更容易漏——注册是"新增账号"，改名是"改一个已有账号"，
    写改名逻辑时脑子里想的是"这个名字有没有被占用"，不会想到"这个名字有没有权限"。
    """
    n = normalize_name(name).casefold()
    if not n:
        return False
    return any(n == str(x).casefold() for x in (config.ADMIN_USERNAMES or ()))


# ---------- 建表 ----------

# 上一次建表时的库路径：用来判断"这个库我已经建过了"（见 _ensure_tables）
_TABLES_PATH: str | None = None


def _raw_conn():
    """不带建表守卫的连接。只给建表函数自己用（否则会递归）。"""
    return _get_conn()


def _conn():
    """业务用的连接：先把当前库的表建齐，再交出去。

    ★ 为什么不能只在 import 时建一次表：建表必须跟着**库路径**走，而路径在
    运行时会被换掉（`session._db_path` 是测试缝，`AGENT_HUB_DB` 是 e2e 缝）。
    只在 import 时跑一次的话，后换路径的一方拿到的是空壳库 —— 现象就是
    `no such table: users` / `no such table: usage`，而且单跑任何一个测试文件
    都正常，只有组合/全量跑才炸（先 import 的模块把"表已建好"这件事认领了，
    后换库的人就没机会再建）。

    代价是一次字符串比较，换掉一整类顺序敏感的故障。
    """
    _ensure_tables()
    return _get_conn()


def _ensure_tables(force: bool = False) -> None:
    """创建 users 表（幂等；换了库路径会重跑）。"""
    global _TABLES_PATH
    # ★ 走 `_session._db_path()` 而不是 from-import 的副本：换库是给
    #   `core.session._db_path` 重新赋值，按值 import 拿到的是旧函数，会一直
    #   指向真实库 —— 于是"换了库却没建表"，现象还是 `no such table`。
    path = _session._db_path()
    if not force and path == _TABLES_PATH:
        return
    with _raw_conn() as conn:
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

            -- 删号墓碑：见 core/session.py 同名表的注释。
            -- 这里再建一次，是因为测试会把库换到临时目录、只跑 account._ensure_tables；
            -- 不建的话 is_deleted() 会撞 `no such table`。
            CREATE TABLE IF NOT EXISTS deleted_users (
                uid        TEXT PRIMARY KEY,
                username   TEXT NOT NULL DEFAULT '',
                deleted_at INTEGER NOT NULL,
                reason     TEXT NOT NULL DEFAULT ''
            );
        """)
    _ensure_columns()
    # 建完才记路径：中途炸了下次还会重试，不会留下"以为建过"的假象
    _TABLES_PATH = path


def _ensure_columns() -> None:
    """给老库补列（幂等）。

    对应 sky-take-out 的 `employee.status`：1 正常 / 0 锁定。
    老库里没有这一列，直接 ALTER 补上并默认 1（已注册用户视为正常），
    这样升级不需要停机迁移脚本。
    """
    with _raw_conn() as conn:
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
        # ---- 账号自助（2026-11-03）：昵称 / 头像 / 改名次数 / 令牌水位 ----
        # 全部幂等补列，老库不停机。默认值刻意选"什么都没设过"的那一侧：
        # 昵称和头像空串 ⇒ 前端回落到 username 与首字母，存量用户升级后外观不变。
        if "nickname" not in cols:
            # 显示名。**不唯一、不参与登录**：唯一会让"小猪佩奇"这种名字第二天被占完。
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if "avatar" not in cols:
            # 只存一个字段，用前缀区分形态，以后加新种类不用再改表：
            #   ""            -> 没设过，前端用昵称首字母
            #   "icon:<name>" -> 内置图标（CornIcon 的 name，白名单见 AVATAR_ICONS）
            #   "img:<hash>"  -> 上传的图片（第 2 批）
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
        if "name_change_count" not in cols:
            # 登录名改过几次。设上限是因为没有邮箱/手机号可以找回，
            # 反复改名的人多半是出了问题，管理员需要从审计日志里追溯。
            conn.execute(
                "ALTER TABLE users ADD COLUMN name_change_count INTEGER NOT NULL DEFAULT 0"
            )
        if "token_valid_after" not in cols:
            # 鉴权代数（列名因兼容保留）：每次改密码 +1，令牌带着签发时的代数。
            # 用单调计数而不是时间戳，避免同秒连改两次时水位不变、旧令牌漏过，
            # 也不受服务器时钟回拨影响。0 = 从未触发，对存量令牌零影响。
            conn.execute(
                "ALTER TABLE users ADD COLUMN token_valid_after INTEGER NOT NULL DEFAULT 0"
            )


_ensure_tables()


# ---------- 读写 ----------


# 取用户行时统一用这一组列。加新列只改这里 —— 两个 SELECT 各写一遍的话，
# 迟早会出现"get_user 有、get_user_by_username 没有"的漂移，而缺字段的表现
# 是 _public 里默默取到空串，最难查。
_USER_COLS = (
    "id, username, password_hash, nickname, avatar, created_at, last_login_at, "
    "status, failed_count, lock_until, name_change_count, token_valid_after"
)


def get_user(uid: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT " + _USER_COLS + " FROM users WHERE id = ?",
            (uid,),
        ).fetchone()
        return dict(row) if row else None


def get_user_by_username(name: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT " + _USER_COLS + " FROM users WHERE username = ?",
            (normalize_name(name),),
        ).fetchone()
        return dict(row) if row else None


def is_deleted(uid: str) -> bool:
    """这个 uid 是不是被主动删掉的账号（墓碑表里有行）。

    只在 `get_user()` 查不到人时才会被调用（正常用户的鉴权路径不付这次查询），
    所以它必须便宜、且不能因为表缺失而抛异常 —— 老库没这张表时按"没删过"处理。
    """
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM deleted_users WHERE uid = ? LIMIT 1", (uid,)
            ).fetchone()
            return row is not None
    except sqlite3.OperationalError:
        return False


def delete_user(uid: str, reason: str = "") -> bool:
    """删除账号，并让它的旧令牌立刻失效。

    只删 users 行是**不够**的（这是 2026-11-13 线上实测出来的）：
    `core/auth.current_user_id` 在库里查不到人时按「代数 0」放行，为的是兼容
    测试里的临时 uid —— 结果是删掉的账号，浏览器里的旧 cookie 还能继续建会话、
    写 usage，一晚上留下 7 个孤儿会话和 60 条孤儿 usage，看板上还显示成"活跃用户"。

    所以这里同一事务写一条墓碑，由鉴权入口拦截。墓碑同时是审计记录：
    看板要解释"注册用户为什么掉了"，查 deleted_users 比翻日志可靠。

    返回 False 表示这个 uid 本来就不在 users 表里（重复删 / 拼错了 uid）。
    """
    with _conn() as conn:
        row = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO deleted_users (uid, username, deleted_at, reason) "
            "VALUES (?, ?, ?, ?)",
            (uid, row["username"], int(time.time()), (reason or "")[:200]),
        )
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    return True


def create_user(username: str, password: str) -> dict:
    """注册新账号，返回公开字段的 dict。

    重名抛 sqlite3.IntegrityError，由 API 层翻译成 409——这里不自己吞异常，
    因为"ename 被占用"在不同调用点可能需要不同的处理方式。
    """
    uid = uuid.uuid4().hex
    now = int(time.time())
    with _conn() as conn:
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
    with _conn() as conn:
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
    with _conn() as conn:
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
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE users SET status = ? WHERE id = ?", (int(status), uid)
        )
        return bool(cur.rowcount)


def touch_login(uid: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (int(time.time()), uid))


# ==================== 账号自助：昵称 / 头像 / 密码 / 登录名 ====================
# 需求与设计依据见 docs/账号自助_头像姓名密码_方案_2026-11-03.md。
# 分界线很清楚：
#   昵称、头像  —— 只影响"别人怎么看见我"，改错也不伤账号，不用密码、不限次
#   密码、登录名 —— 是凭证，必须验旧密码；登录名还额外限次（没有邮箱可找回）

# 昵称上限 20：再长会在侧栏账号块和设置页账号卡里折行，而它只是"别人怎么叫你"
NICKNAME_MAX = 20

# 允许做头像的内置图标（与前端 CornIcon 的 name 对齐）。
# 这是**后端白名单**，不是前端提示：avatar 会落库并在多处渲染，不校验的话
# 前端一个 typo 就能往库里写任意字符串；第 2 批加上传后更危险（能伪造路径）。
# 刻意排除 warn（警告三角）、trash / close / check / back / arrow / plus：
# 它们的语义是"一个操作"，做成头像会让人以为那是按钮。
AVATAR_ICONS = frozenset(
    {
        "book", "pen", "notes", "bolt", "briefcase", "globe", "target", "search",
        "bulb", "store", "tools", "clock", "calendar", "star", "link", "download",
        "upload", "refresh", "user", "corn",
    }
)

AVATAR_ICON_PREFIX = "icon:"

# 上传头像的 hash：sha256 前 16 位，小写十六进制。
# 只认这个形状是防穿越的第一道闸（另一道在 service/avatar.py::path_of）。
_AVATAR_HASH_RE = re.compile(r"^[0-9a-f]{16}$")

def check_nickname(nick: str) -> str | None:
    """校验昵称，合法返回 None。

    空串是合法的（= 清空，前端回落到 username）：用户设了昵称又反悔，
    需要一个"我不要昵称了"的操作，否则他只能想办法把昵称改成登录名，
    而这两者显示起来是一样的，等于没法撤销。
    """
    n = (nick or "").strip()
    if not n:
        return None
    if len(n) > NICKNAME_MAX:
        return f"昵称最多 {NICKNAME_MAX} 个字符"
    if any(c in n for c in "\r\n\t"):
        # 换行/制表符会破坏侧栏与账号卡的单行布局，且没有任何正当用途
        return "昵称不能包含换行或制表符"
    return None


def normalize_avatar(value: str) -> str | None:
    """校验并归一化头像值；非法返回 None（调用方翻成 400）。

    合法形态：
      ""             清空，前端回落首字母
      "icon:<name>"  name 必须在 AVATAR_ICONS 里
      "img:<hash>"   上传的头像，hash 是 16 位小写十六进制（内容 sha256 前 16 位）

    `img:` 之所以也走这里：用户传完图再选一个内置图标、然后又想换回刚才那张图，
    前端手上还有那个 hash（`me.user.avatar`），没必要逼他重新传一遍。
    但**文件是否存在由调用方另外校验**（`service.avatar.exists`）——
    这里只管格式，不碰磁盘，否则 core 层就依赖了存储布局。
    """
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith(AVATAR_ICON_PREFIX):
        return v if v[len(AVATAR_ICON_PREFIX) :] in AVATAR_ICONS else None
    if v.startswith("img:"):
        h = v[len("img:") :]
        return v if _AVATAR_HASH_RE.match(h) else None
    return None


def set_nickname(uid: str, nickname: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET nickname = ? WHERE id = ?", ((nickname or "").strip(), uid)
        )


def set_avatar(uid: str, avatar: str) -> str:
    """设置头像，**返回旧值**（调用方据此清理没人再引用的上传文件）。

    返回旧值而不是让调用方先查一次：换头像时"读旧值 → 写新值"必须落在同一个
    连接里，否则两个并发请求可能都读到同一个旧值，然后谁也删不掉那张图。
    """
    with _conn() as conn:
        row = conn.execute("SELECT avatar FROM users WHERE id = ?", (uid,)).fetchone()
        old = (row["avatar"] if row else "") or ""
        conn.execute("UPDATE users SET avatar = ? WHERE id = ?", (avatar or "", uid))
    return old


def is_avatar_referenced(avatar: str) -> bool:
    """还有没有别的用户在用这个头像值。

    多个用户可以选同一张图（同图内容 hash 相同、只存一份），所以换头像后
    不能无条件删文件 —— 先问一句"还有人用吗"。
    """
    v = (avatar or "").strip()
    if not v:
        return True
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE avatar = ? LIMIT 1", (v,)).fetchone()
        return row is not None


def get_token_valid_after(uid: str) -> int:
    """取账号当前鉴权代数（数据库列名 token_valid_after 为兼容保留）。0 = 从未改密。

    鉴权依赖会在**每一次**请求里读这个值。用主键查询换"绝不缓存"：
    缓存 30 秒意味着改完密码后旧会话还能多用 30 秒，而那正是这个功能
    唯一的用处。SQLite 本地主键查询是微秒级，不值得为它引入一份可能过期的副本。
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT token_valid_after FROM users WHERE id = ?", (uid,)
        ).fetchone()
        return int(row["token_valid_after"] or 0) if row else 0


def change_password(uid: str, old_password: str, new_password: str) -> str | None:
    """改密码。成功返回 None，失败返回给用户看的中文提示。

    把鉴权代数 +1 是**这个功能的重点**：不换代等于只换了
    门锁没换钥匙，已经登进来的会话（有效期 AUTH_TTL_DAYS=7 天）照旧能用，
    而用户会以为"我把人赶走了"。

    当前这台不会掉线：API 层紧接着会带新代数重签一枚 Cookie。
    其它令牌仍带旧代数，下一次请求就会被鉴权依赖拒绝。
    """
    row = get_user(uid)
    if not row:
        return "账号不存在"
    if int(row.get("lock_until") or 0) > int(time.time()):
        return "账号已被锁定，请稍后再试"
    if not verify_password(old_password, row["password_hash"]):
        _record_login_failure(uid)
        return "原密码不对"
    # 旧密码已证明本人身份；后续即使新密码不合规，也只该报新密码的问题，
    # 不应让此前登录失败累计的计数继续挂着。
    _clear_login_failure(uid)
    err = check_password(new_password)
    if err:
        return err
    if old_password == new_password:
        return "新密码和原密码相同"
    old_hash = row["password_hash"]
    new_hash = hash_password(new_password)
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, token_valid_after = token_valid_after + 1 "
            "WHERE id = ? AND password_hash = ?",
            (new_hash, uid, old_hash),
        )
    if cur.rowcount != 1:
        # 两个并发改密请求都验过了同一份旧密码：只允许第一个落库。
        # 第二个若也返回成功，用户会误以为自己的新密码生效，实际却被另一边覆盖。
        return "密码刚刚已被修改，请重新登录后再试"
    audit("password_change", uid, {})
    return None


def change_username(uid: str, password: str, new_name: str) -> tuple[dict | None, str, int]:
    """改登录名。返回 (更新后的行, 错误文案, HTTP 建议码)，成功时错误为空串。

    要求输密码：登录名是凭证之一。只凭"已经登录的会话"就能改，等于只要偷到
    一台没锁屏的电脑，就能把账号名改成自己的——之后真正的用户再也登不进来。
    """
    row = get_user(uid)
    if not row:
        return None, "账号不存在", 401
    if int(row.get("lock_until") or 0) > int(time.time()):
        return None, "账号已被锁定，请稍后再试", 403
    if not verify_password(password, row["password_hash"]):
        _record_login_failure(uid)
        return None, "密码不对", 400
    _clear_login_failure(uid)

    name = normalize_name(new_name)
    err = check_name(name)
    if err:
        return None, err, 400
    if name.casefold() == str(row["username"]).casefold():
        return None, "新登录名和现在的一样", 400
    if int(row.get("name_change_count") or 0) >= MAX_NAME_CHANGES:
        # 403 而不是 429：429 是"你请求太快，等一会儿再来"，而这条是终身上限，
        # 等多久都没用。撞在一起会出现最糟的提示——用户看到"稍后再试"，
        # 等一分钟再试、再被拒，以为系统坏了。
        return None, f"登录名最多改 {MAX_NAME_CHANGES} 次，已经用完了", 403
    # 管理员名不能改进来：注册处已经挡了抢注，改名这条路不挡就等于没挡
    if is_reserved_admin_name(name):
        return None, "该登录名不可用，请更换一个", 403
    # 先查重，是为了给出"被占用"这句人话；并发抢注仍由唯一索引兜底（见下）
    if get_user_by_username(name):
        return None, "这个登录名已被占用", 409

    try:
        with _conn() as conn:
            cur = conn.execute(
                "UPDATE users SET username = ?, name_change_count = name_change_count + 1 "
                "WHERE id = ? AND username = ? AND name_change_count = ?",
                (name, uid, row["username"], int(row.get("name_change_count") or 0)),
            )
    except sqlite3.IntegrityError:
        # 两个请求同时通过上面的查重、只有先落库的能成功。
        # 不捕这个异常的话，用户看到的是 500，而实际原因只是"手慢了"。
        return None, "这个登录名已被占用", 409

    if cur.rowcount != 1:
        latest = get_user(uid)
        if not latest:
            return None, "账号不存在，请重新登录", 401
        if int(latest.get("name_change_count") or 0) >= MAX_NAME_CHANGES:
            return None, f"登录名最多改 {MAX_NAME_CHANGES} 次，已经用完了", 403
        # 同账号的另一个请求刚改过名。用旧快照继续写会让两个请求都报成功，
        # 但最终只留下后一个名字，用户无法判断自己现在到底叫什么。
        return None, "登录名刚刚被修改，请刷新后再试", 409

    audit("username_change", uid, {"from": row["username"], "to": name})
    return get_user(uid), "", 200


def audit(event: str, uid: str, detail: dict) -> None:
    """账号安全事件写一行 JSONL 到 `${DATA_DIR}/audit.log`。

    为什么落文件不建表：这类事件只在"用户忘了自己改成什么"时被管理员翻一次，
    查询频率极低，进主库只会多一张要跟着迁移的表；JSONL 还能直接 grep。

    ★ 路径在函数里现取 `config.DATA_DIR`：测试会 monkeypatch 它，
    在 import 时算死会写到真实数据目录去（本项目踩过这个坑）。
    """
    try:
        path = os.path.join(config.DATA_DIR, "audit.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"ts": int(time.time()), "event": event, "uid": uid, **detail}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # 审计写失败不能连带业务失败：改名已经落库了，此时抛异常会让用户
        # 以为没改成，然后去改第二次——白白消耗掉一次改名额度
        pass
