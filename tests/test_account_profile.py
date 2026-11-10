"""账号自助（昵称 / 头像 / 密码 / 登录名）回归测试。

设计依据见 docs/账号自助_头像姓名密码_方案_2026-11-03.md。

隔离方式与 tests/test_auth_gate.py 一致，但更严格一点：**每个用例一个独立库**。
为什么必须这样：改密码会写"令牌水位"，而水位是终身的；改名次数也是终身的。
共用一个库跑完十几个用例，后面的用例会被前面的副作用干扰，最后得到一堆
"顺序一变就红"的测试 —— 那比没有测试更糟。

★ 必须在 import 业务模块之前替换 `session._db_path`：`core.account` 在 import
阶段就会执行 `_ensure_tables()`，那时路径已经是真实库的话，测试会往
data/sessions.db 里建表（甚至建账号）。
"""

import io
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

_TMP = tempfile.mkdtemp(prefix="baomi_acct_test_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402
import core.session as session  # noqa: E402

# ---- 必须在 import main / account 之前 ----
# ★ 这一步只是"让 import 期建表落到临时库"，导完立刻还原（见下方）。
#   不还原的话，本模块会把全局 `session._db_path` 永久指向 _TMP/boot.db，
#   后面的模块在收集阶段拿到的是个只有空壳的库 —— 表现为
#   `ERROR tests/test_agent_guard.py: no such table: users`，
#   而且单跑任何一个文件都正常，只有全量跑才炸。
_ORIG_DB_PATH = session._db_path
session._db_path = lambda: os.path.join(_TMP, "boot.db")
session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import account, quota  # noqa: E402
from service import avatar as avatar_service  # noqa: E402
from service import daily  # noqa: E402

# 导入期结束，把全局路径还给别的模块（每个用例自己用 _fresh 切临时库）。
# 连接是按路径缓存的，路径变回去时会自动重连，这里不用清 _LOCAL。
session._db_path = _ORIG_DB_PATH


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    """每个用例恢复一份账号测试配置，别污染同一进程里的其它测试。

    这些值必须在 import 后、请求前设置（端点运行时才读 config），不能用模块级
    裸赋值：pytest 收集所有文件时，模块级赋值会一直留到整个会话结束，导致后面的
    “登录锁定”等测试被静默关闭。
    """
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    # 显式配一个管理员名：默认是空列表，"改名抢注管理员"这条闸不会被触发，
    # 断言就成了假通过（真实部署里这个名单来自 .env）
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["admin"])
    monkeypatch.setattr(config, "ADMIN_UIDS", [])
    monkeypatch.setattr(config, "AUTH_SECRET", "test-secret-account-profile")
    monkeypatch.setattr(config, "_AUTH_SECRET_FILE", os.path.join(_TMP, ".auth_secret"))
    monkeypatch.setattr(config, "AUTH_RETURN_TOKEN", False)
    # 本文件默认关掉失败锁定，单条安全测试会按需恢复阈值。
    monkeypatch.setattr(config, "AUTH_MAX_FAILS", 0)
    monkeypatch.setattr(config, "AUTH_LOCK_MINUTES", 0)


def _fresh(monkeypatch, tag: str) -> TestClient:
    """切到一个空库 + 空数据目录，返回独立 Cookie 罐子的客户端。"""
    path = os.path.join(_TMP, tag + ".db")
    monkeypatch.setattr(session, "_db_path", lambda p=path: p)
    # 连接是按线程缓存的（路径变了才重建），换库必须连缓存一起丢掉
    monkeypatch.setattr(session, "_LOCAL", threading.local())
    session._init_db()
    account._ensure_tables()
    # daily / quota 的表也在这个库里，缺了会在改密码（写 usage）时炸
    daily._daily_tables_ready = False
    daily._ensure_daily_tables()
    quota._ensure_tables()
    # ★ 数据目录也隔离：头像文件落在 ${DATA_DIR}/avatars，审计日志落在
    #   ${DATA_DIR}/audit.log。指向真实的 data/ 会两件事都发生：测试往生产
    #   目录写垃圾，以及"上一次跑留下的文件"让负向断言白过。
    data_dir = os.path.join(_TMP, "data_" + tag)
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    # 限流按用户名/uid 分桶，同一个 client 连续调会累积
    quota._HITS.clear()
    return TestClient(main.app)


def _register(cli: TestClient, name="alice", pwd="Alice_pass_9") -> tuple[str, str]:
    """注册并返回 (uid, token)。显式拿 token 而不是依赖 cookie 罐子 ——
    后面注册第二个账号时，罐子会被换成别人的。"""
    r = cli.post("/api/auth/register/", json={"username": name, "password": pwd})
    assert r.status_code == 200, r.text
    return r.json()["id"], cli.cookies.get("baomi_session")


def _h(token: str) -> dict:
    return {"X-Auth-Token": token}


# ==================== 昵称 ====================


def test_nickname_set_and_clear(monkeypatch):
    cli = _fresh(monkeypatch, "nick")
    uid, tok = _register(cli)

    # 初始：空串 = 没设过（前端据此回落到 username）
    r = cli.get("/api/auth/me/", headers=_h(tok))
    assert r.status_code == 200
    assert r.json()["user"]["nickname"] == ""

    # 首尾空白要被去掉，否则侧栏会出现"看不见的空格"
    r = cli.patch("/api/auth/profile", json={"nickname": "  小猪佩奇  "}, headers=_h(tok))
    assert r.status_code == 200, r.text
    assert r.json()["user"]["nickname"] == "小猪佩奇"
    # 昵称不影响登录名
    assert r.json()["user"]["username"] == "alice"

    # 空串是合法的"清空"，不是错误
    r = cli.patch("/api/auth/profile", json={"nickname": ""}, headers=_h(tok))
    assert r.status_code == 200
    assert r.json()["user"]["nickname"] == ""


def test_nickname_rejects(monkeypatch):
    cli = _fresh(monkeypatch, "nick_bad")
    _, tok = _register(cli)

    # 超长
    r = cli.patch("/api/auth/profile", json={"nickname": "x" * 21}, headers=_h(tok))
    assert r.status_code == 400, r.text
    # 换行会破坏侧栏单行布局
    r = cli.patch("/api/auth/profile", json={"nickname": "a\nb"}, headers=_h(tok))
    assert r.status_code == 400, r.text
    # 什么都没传：不该当成"清空"，该明确报错
    r = cli.patch("/api/auth/profile", json={}, headers=_h(tok))
    assert r.status_code == 400, r.text


# ==================== 头像 ====================


def test_avatar_icon_whitelist(monkeypatch):
    cli = _fresh(monkeypatch, "avatar")
    _, tok = _register(cli)

    r = cli.patch("/api/auth/profile", json={"avatar": "icon:corn"}, headers=_h(tok))
    assert r.status_code == 200, r.text
    assert r.json()["user"]["avatar"] == "icon:corn"

    # 清空 = 回落首字母头像
    r = cli.patch("/api/auth/profile", json={"avatar": ""}, headers=_h(tok))
    assert r.status_code == 200
    assert r.json()["user"]["avatar"] == ""


def test_avatar_rejects_non_whitelist(monkeypatch):
    """★ 这一组是负向断言里最重要的：avatar 会落库并在多处渲染。

    如果只信前端，`icon:../../etc/passwd` 这类值会一路进到图片 URL 上。
    """
    cli = _fresh(monkeypatch, "avatar_bad")
    _, tok = _register(cli)

    for bad in [
        "icon:nope",  # 白名单外
        "icon:../../etc/passwd",  # 路径穿越
        "icon:",  # 空前缀
        "warn",  # 缺前缀
        "img:deadbeef",  # 上传头像第 2 批才有，现在必须拒（否则前端显示破图）
        "<script>alert(1)</script>",
    ]:
        r = cli.patch("/api/auth/profile", json={"avatar": bad}, headers=_h(tok))
        assert r.status_code == 400, f"{bad!r} 应被拒，实际 {r.status_code} {r.text[:80]}"


def test_avatar_icons_cover_frontend_choices():
    """前后端白名单必须一致。

    不一致的两个方向都很隐蔽：前端多一个 = 用户点了报"参数不合法"；
    后端多一个 = 选项永远不显示（谁也不会发现少了一个）。
    """
    frontend = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
        "src",
        "components",
        "settings",
        "AvatarPicker.tsx",
    )
    with open(frontend, encoding="utf-8") as f:
        src = f.read()
    # 抓 AVATAR_ICON_CHOICES = [...] 里的字符串字面量
    start = src.index("AVATAR_ICON_CHOICES")
    end = src.index("] as const", start)
    block = src[start:end]
    names = {seg.strip().strip('"').strip(",") for seg in block.split('"')[1::2]}
    assert names, "没能从 AvatarPicker.tsx 里解析出图标名单"
    assert names == set(account.AVATAR_ICONS), (
        f"前后端头像名单不一致：仅前端 {sorted(names - set(account.AVATAR_ICONS))} / "
        f"仅后端 {sorted(set(account.AVATAR_ICONS) - names)}"
    )


# ==================== 密码 ====================


def test_password_change_kills_old_tokens(monkeypatch):
    """改密码的核心不变量：**改之前签发的令牌必须立刻失效**。

    这条是"改密码"这个功能的全部意义所在。不测它，功能看着能用：
    新密码能登、旧密码登不上 —— 但已经登进来的会话照样能翻所有对话。
    """
    cli = _fresh(monkeypatch, "pwd")
    uid, old_tok = _register(cli)

    r = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "def45678"},
        headers=_h(old_tok),
    )
    assert r.status_code == 200, r.text

    new_tok = cli.cookies.get("baomi_session")
    assert new_tok and new_tok != old_tok, "改密码后应重签 Cookie"

    # 旧令牌失效
    assert cli.get("/api/auth/me/", headers=_h(old_tok)).status_code == 401
    # 新令牌可用（否则用户改完自己就被踢了）
    assert cli.get("/api/auth/me/", headers=_h(new_tok)).status_code == 200

    # 新旧密码的登录结果
    assert (
        cli.post("/api/auth/login/", json={"username": "alice", "password": "def45678"}).status_code
        == 200
    )
    assert (
        cli.post("/api/auth/login/", json={"username": "alice", "password": "Alice_pass_9"}).status_code
        == 401
    )


def test_password_change_rejects(monkeypatch):
    cli = _fresh(monkeypatch, "pwd_bad")
    _, tok = _register(cli)

    # 旧密码错
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "wrong", "new_password": "def45678"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text
    # 新密码太短
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "123"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text
    # 新旧相同
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "Alice_pass_9"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text

    # 负向：以上三次失败都不该让旧令牌失效（否则一输错密码自己就被踢）
    assert cli.get("/api/auth/me/", headers=_h(tok)).status_code == 200


def test_second_password_change_same_second_invalidates_first_reissue(monkeypatch):
    """同一秒连续改两次密码，第一次重签的令牌也必须失效。

    ★ 这是时间戳水位最容易漏掉的边界：若把当前秒直接写进库，两次改密会得到
    完全相同的值，第一枚 af 令牌看起来仍然“不早于水位”。鉴权代数必须单调 +1。
    """
    cli = _fresh(monkeypatch, "pwd_same_second")
    _, old_tok = _register(cli)
    fixed_now = int(account.time.time())
    monkeypatch.setattr(account.time, "time", lambda: fixed_now)

    r1 = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "def45678"},
        headers=_h(old_tok),
    )
    assert r1.status_code == 200, r1.text
    first_reissue = cli.cookies.get("baomi_session")
    assert first_reissue and first_reissue != old_tok

    r2 = cli.post(
        "/api/auth/password",
        json={"old_password": "def45678", "new_password": "ghi789012"},
        headers=_h(first_reissue),
    )
    assert r2.status_code == 200, r2.text
    second_reissue = cli.cookies.get("baomi_session")
    assert second_reissue and second_reissue != first_reissue

    # 第一枚重签令牌是“第二次改密之前签发”的，必须被作废。
    assert cli.get("/api/auth/me/", headers=_h(first_reissue)).status_code == 401
    assert cli.get("/api/auth/me/", headers=_h(second_reissue)).status_code == 200


def test_password_change_returns_replacement_for_bearer_clients(monkeypatch):
    """AUTH_RETURN_TOKEN 客户端没有 Cookie，改密响应必须带回可用的新令牌。"""
    cli = _fresh(monkeypatch, "pwd_bearer")
    _, old_tok = _register(cli)
    monkeypatch.setattr(config, "AUTH_RETURN_TOKEN", True)

    r = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "def45678"},
        headers=_h(old_tok),
    )
    assert r.status_code == 200, r.text
    new_tok = r.json().get("user", {}).get("token")
    assert new_tok and new_tok != old_tok

    assert cli.get("/api/auth/me/", headers=_h(old_tok)).status_code == 401
    assert cli.get("/api/auth/me/", headers=_h(new_tok)).status_code == 200


def test_credential_change_bad_password_counts_and_success_clears(monkeypatch):
    """已登录不是绕过口令爆破计数的理由。

    改密码 / 改登录名都要求旧密码；错误尝试要和登录入口一样累计 failed_count。
    一旦密码验证成功，则应立即清掉历史计数，避免“输对新密码也被前几次错误拖累”。
    """
    cli = _fresh(monkeypatch, "pwd_fail_count")
    uid, tok = _register(cli)
    monkeypatch.setattr(config, "AUTH_MAX_FAILS", 5)
    monkeypatch.setattr(config, "AUTH_LOCK_MINUTES", 15)

    r = cli.post(
        "/api/auth/password",
        json={"old_password": "wrong", "new_password": "def45678"},
        headers=_h(tok),
    )
    assert r.status_code == 400
    assert int(account.get_user(uid)["failed_count"]) == 1

    r = cli.post(
        "/api/auth/username",
        json={"password": "wrong", "new_username": "alice_next"},
        headers=_h(tok),
    )
    assert r.status_code == 400
    assert int(account.get_user(uid)["failed_count"]) == 2

    # 旧密码正确：哪怕新密码随后被规则拒绝，也应清掉此前的失败计数。
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "123"},
        headers=_h(tok),
    )
    assert r.status_code == 400
    row = account.get_user(uid)
    assert int(row["failed_count"]) == 0
    assert int(row["lock_until"]) == 0


def test_password_untouched_user_keeps_token(monkeypatch):
    """没改过密码的用户，令牌不能被水位逻辑误伤（水位默认 0）。

    这条防的是最坏的上线事故：一发布把所有在线用户全踢下线。
    """
    cli = _fresh(monkeypatch, "pwd_untouched")
    uid, tok = _register(cli)
    assert account.get_token_valid_after(uid) == 0
    assert cli.get("/api/auth/me/", headers=_h(tok)).status_code == 200


# ==================== 登录名 ====================


def test_username_change(monkeypatch):
    cli = _fresh(monkeypatch, "name")
    _, tok = _register(cli)

    r = cli.post(
        "/api/auth/username",
        json={"password": "Alice_pass_9", "new_username": "alice2"},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "alice2"
    assert r.json()["user"]["name_changes_left"] == account.MAX_NAME_CHANGES - 1

    # 旧名登不上、新名能登
    assert (
        cli.post("/api/auth/login/", json={"username": "alice", "password": "Alice_pass_9"}).status_code
        == 401
    )
    assert (
        cli.post("/api/auth/login/", json={"username": "alice2", "password": "Alice_pass_9"}).status_code
        == 200
    )
    # uid 不变，所以原来那枚令牌仍然有效（改名不该踢自己下线）
    assert cli.get("/api/auth/me/", headers=_h(tok)).status_code == 200


def test_username_requires_password(monkeypatch):
    """★ 登录名是凭证：只凭"已经登录了"就能改，等于拿到一台没锁屏的电脑就能夺号。"""
    cli = _fresh(monkeypatch, "name_pwd")
    _, tok = _register(cli)

    r = cli.post(
        "/api/auth/username",
        json={"password": "wrong", "new_username": "alice2"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text
    # 没改成功：原名还能登
    assert (
        cli.post("/api/auth/login/", json={"username": "alice", "password": "Alice_pass_9"}).status_code
        == 200
    )


def test_username_duplicate_and_reserved(monkeypatch):
    cli = _fresh(monkeypatch, "name_dup")
    _, tok = _register(cli, "alice")
    # 注册 bob 会把 Cookie 罐子换成 bob 的，所以后面一律显式带 token
    cli.post("/api/auth/register/", json={"username": "bob", "password": "bob12345"})

    r = cli.post(
        "/api/auth/username", json={"password": "Alice_pass_9", "new_username": "bob"}, headers=_h(tok)
    )
    assert r.status_code == 409, r.text
    # NOCASE 唯一索引：大小写不同也算重名
    r = cli.post(
        "/api/auth/username", json={"password": "Alice_pass_9", "new_username": "BOB"}, headers=_h(tok)
    )
    assert r.status_code == 409, r.text

    # ★ 抢注管理员名必须被挡：否则改个名就白拿管理员权限
    for bad in ["admin", "ADMIN"]:
        r = cli.post(
            "/api/auth/username", json={"password": "Alice_pass_9", "new_username": bad}, headers=_h(tok)
        )
        assert r.status_code == 403, f"{bad!r} 应被拒，实际 {r.status_code}"


def test_username_change_limit(monkeypatch):
    """改满 MAX_NAME_CHANGES 次后必须 403（而不是 429）。

    429 的语义是"太快了，等会儿再来"；这里是终身上限，等多久都没用。
    两者撞在一起时用户会看到"稍后再试"，然后一分钟后再被拒一次。
    """
    cli = _fresh(monkeypatch, "name_limit")
    _, tok = _register(cli)

    for i in range(account.MAX_NAME_CHANGES):
        quota._HITS.clear()  # 避开每分钟限流，只测终身上限
        r = cli.post(
            "/api/auth/username",
            json={"password": "Alice_pass_9", "new_username": f"alice_{i}"},
            headers=_h(tok),
        )
        assert r.status_code == 200, r.text

    quota._HITS.clear()
    r = cli.post(
        "/api/auth/username", json={"password": "Alice_pass_9", "new_username": "alice_x"}, headers=_h(tok)
    )
    assert r.status_code == 403, r.text
    assert "最多改" in r.json()["detail"]


# ==================== 登录与隔离 ====================


def test_profile_endpoints_require_login(monkeypatch):
    cli = _fresh(monkeypatch, "auth_required")
    for method, path, body in [
        ("patch", "/api/auth/profile", {"json": {"nickname": "x"}}),
        ("post", "/api/auth/password", {"json": {"old_password": "a", "new_password": "bbbbbb"}}),
        ("post", "/api/auth/username", {"json": {"password": "a", "new_username": "zzz"}}),
    ]:
        r = getattr(cli, method)(path, **body)
        assert r.status_code == 401, f"{method} {path} 未登录应 401，实际 {r.status_code}"


def test_audit_log_written(monkeypatch, tmp_path):
    """改密码/改名要留痕：没有邮箱可找回时，管理员只能靠这份日志。"""
    cli = _fresh(monkeypatch, "audit")
    _, tok = _register(cli)

    # 审计日志用临时目录：既让用例自洽，也避免测试记录写进真实 data/audit.log。
    # `account.audit()` 是调用时才读 config.DATA_DIR，所以这里改得动。
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    log = os.path.join(str(tmp_path), "audit.log")

    quota._HITS.clear()
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "Alice_pass_9", "new_password": "def45678"},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text

    # ★ 改密码会作废旧令牌（水位）。所以这里必须重新登录拿令牌 ——
    #   顺手把"旧令牌真的失效了"变成断言，而不是默默 401 导致后面少写一条日志。
    assert cli.get("/api/auth/me/", headers=_h(tok)).status_code == 401
    quota._HITS.clear()
    r = cli.post("/api/auth/login/", json={"username": "alice", "password": "def45678"})
    assert r.status_code == 200, r.text
    tok2 = cli.cookies.get("baomi_session")

    r = cli.post(
        "/api/auth/username",
        json={"password": "def45678", "new_username": "alice9"},
        headers=_h(tok2),
    )
    assert r.status_code == 200, r.text

    assert os.path.exists(log), "没有生成 audit.log"
    with open(log, encoding="utf-8") as f:
        body = f.read()
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 2, f"应恰好两条审计记录，实际 {len(lines)}：{body}"
    assert '"event": "password_change"' in lines[0]
    assert '"event": "username_change"' in lines[1]
    assert '"from": "alice"' in lines[1] and '"to": "alice9"' in lines[1]
    # ★ 负向：日志里绝不能出现口令明文，否则日志泄露 = 全站口令泄露
    assert "def45678" not in body
    assert "Alice_pass_9" not in body


def test_old_users_table_migrates_without_losing_account(monkeypatch, tmp_path):
    """上线前必须能直接升级老库，而不是只在新库里通过。"""
    path = str(tmp_path / "legacy.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    monkeypatch.setattr(session, "_LOCAL", threading.local())
    with session._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id            TEXT PRIMARY KEY,
                username      TEXT NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at    INTEGER NOT NULL,
                last_login_at INTEGER,
                status        INTEGER NOT NULL DEFAULT 1,
                failed_count  INTEGER NOT NULL DEFAULT 0,
                lock_until    INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX idx_users_name ON users(username COLLATE NOCASE);
            INSERT INTO users
                (id, username, password_hash, created_at, last_login_at)
            VALUES
                ('legacy-uid', 'olduser', 'pbkdf2_sha256$1$AA==$AA==', 1, 1);
            """
        )

    account._ensure_tables(force=True)
    row = account.get_user("legacy-uid")
    assert row is not None and row["username"] == "olduser"
    assert row["nickname"] == ""
    assert row["avatar"] == ""
    assert int(row["name_change_count"]) == 0
    assert int(row["token_valid_after"]) == 0


# ==================== 上传头像 ====================
#
# 这一组几乎全是负向：正向只证明"能传"，负向才证明"限制是真的"。
# 尤其注意两条 —— 6MB 文件不能留下半成品、20000×20000 的声明尺寸不能被解码。


def _jpeg(w: int = 800, h: int = 600, color=(200, 120, 40)) -> bytes:
    from PIL import Image

    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _png_rgba(w: int = 120, h: int = 120, alpha: int = 0) -> bytes:
    from PIL import Image

    im = Image.new("RGBA", (w, h), (255, 0, 0, alpha))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _png_declaring(w: int, h: int) -> bytes:
    """造一个"文件极小但声称自己是 W×H"的 PNG（解压炸弹的最小形态）。

    只有 IHDR + IEND，没有 IDAT：PIL 能读出尺寸但读不出像素 ——
    正的实现必须在 `load()` 之前就把尺寸判掉，否则这里会去解一张 3.2GB 的位图。
    """
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk + crc + iend


def _avatar_files() -> list[str]:
    d = os.path.join(config.DATA_DIR, "avatars")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def _tmp_uploads() -> set[str]:
    return {
        f
        for f in os.listdir(tempfile.gettempdir())
        if f.startswith("avatar_") and f.endswith(".upload")
    }


def _upload(cli, tok, data: bytes, name="me.jpg", mime="image/jpeg"):
    return cli.post(
        "/api/auth/avatar",
        files={"file": (name, data, mime)},
        headers=_h(tok),
    )


def test_avatar_upload_ok(monkeypatch):
    """正常上传：落盘是 192×192、体积很小、原图不残留。"""
    from PIL import Image

    cli = _fresh(monkeypatch, "av_ok")
    _, tok = _register(cli)
    before_tmp = _tmp_uploads()

    r = _upload(cli, tok, _jpeg(1600, 1200))
    assert r.status_code == 200, r.text
    avatar = r.json()["user"]["avatar"]
    assert avatar.startswith("img:"), avatar

    files = _avatar_files()
    assert len(files) == 1, files
    path = os.path.join(config.DATA_DIR, "avatars", files[0])
    with Image.open(path) as im:
        assert im.size == (192, 192), im.size  # 统一正方形
        assert im.format == "JPEG"
    # 体积断言：>30KB 说明压缩参数没生效（正常 12~18KB）
    assert os.path.getsize(path) < 30 * 1024, os.path.getsize(path)
    # ★ 原图必须已经删掉：这是"只存成品"的硬规矩，残留等于磁盘迟早被吃满
    assert _tmp_uploads() == before_tmp, "临时原图没清理"

    # 换过之后 me 里也应该是新值（前端靠它渲染）
    r2 = cli.get("/api/auth/me/", headers=_h(tok))
    assert r2.json()["user"]["avatar"] == avatar


def test_avatar_small_image_still_normalizes_to_192(monkeypatch):
    """小图也必须输出 192×192，不能因为 thumbnail 不放大就漏掉尺寸契约。"""
    from PIL import Image

    cli = _fresh(monkeypatch, "av_small")
    _, tok = _register(cli)
    r = _upload(cli, tok, _jpeg(32, 16), "tiny.jpg")
    assert r.status_code == 200, r.text

    path = os.path.join(config.DATA_DIR, "avatars", _avatar_files()[0])
    with Image.open(path) as im:
        assert im.size == (192, 192), im.size


def test_avatar_upload_too_big(monkeypatch):
    """6MB → 400，且不留半成品。

    ★ 这里断的是"不留垃圾"，不只是"返回 400"：`save_stream` 边写边累加，
    超限时半成品已经在磁盘上了，不删的话每次被刷都给服务器留 5MB。
    """
    cli = _fresh(monkeypatch, "av_big")
    _, tok = _register(cli)
    before_tmp = _tmp_uploads()

    r = _upload(cli, tok, b"\xff\xd8" + b"0" * (6 * 1024 * 1024))
    assert r.status_code == 400, r.text
    assert "大" in r.json()["detail"]
    assert _tmp_uploads() == before_tmp, "超限后留下了半成品"
    assert _avatar_files() == []


def test_avatar_declared_body_hard_limit_rejects_before_multipart(monkeypatch):
    """超大 Content-Length 要在 multipart 解析前拒绝，不能先收满再判业务大小。"""
    cli = _fresh(monkeypatch, "av_body_limit")
    _, tok = _register(cli)
    headers = _h(tok)
    headers["Content-Length"] = str(avatar_service.REQUEST_BODY_LIMIT + 1)

    r = cli.post("/api/auth/avatar", content=b"x", headers=headers)
    assert r.status_code == 413, r.text
    assert "5MB" in r.json()["detail"]


def test_avatar_upload_not_image(monkeypatch):
    """扩展名与 Content-Type 都能伪造 —— 判定只能靠"打得开"。

    注意这里的 Content-Type 是合法的 image/jpeg：正好证明前端/客户端声明的
    类型不构成任何保证。
    """
    cli = _fresh(monkeypatch, "av_fake")
    _, tok = _register(cli)

    r = _upload(cli, tok, b"this is definitely not an image", "me.jpg", "image/jpeg")
    assert r.status_code == 400, r.text
    assert "打不开" in r.json()["detail"], r.json()
    assert _avatar_files() == []


def test_avatar_rejects_svg_and_gif(monkeypatch):
    """SVG 是存储型 XSS 的入口，明确不收；GIF 也不收（会存成不会动的静图，白骗用户）。"""
    cli = _fresh(monkeypatch, "av_svg")
    _, tok = _register(cli)

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = _upload(cli, tok, svg, "x.svg", "image/svg+xml")
    assert r.status_code == 400, r.text
    assert "JPG" in r.json()["detail"]

    r = _upload(cli, tok, b"GIF89a", "x.gif", "image/gif")
    assert r.status_code == 400, r.text


def test_avatar_rejects_actual_format_even_when_mime_lies(monkeypatch):
    """MIME 是 image/jpeg，真实内容却是 GIF/BMP/TIFF，也必须拒。

    只看 Content-Type 等于把白名单交给攻击者填写；Image.open 打得开也不代表
    格式在允许列表里。这里固定住“解析后的 im.format 还要再过一道”的硬约束。
    """
    from PIL import Image

    cli = _fresh(monkeypatch, "av_disguised")
    _, tok = _register(cli)
    for fmt in ("GIF", "BMP", "TIFF"):
        buf = io.BytesIO()
        Image.new("RGB", (24, 24), (30, 120, 200)).save(buf, fmt)
        r = _upload(cli, tok, buf.getvalue(), "pretend.jpg", "image/jpeg")
        assert r.status_code == 400, f"{fmt}: {r.text}"
        assert "JPG" in r.json()["detail"], (fmt, r.json())


def test_avatar_accepts_real_webp(monkeypatch):
    """WebP 在声明白名单里，后端真实格式白名单也必须接受。"""
    from PIL import Image

    cli = _fresh(monkeypatch, "av_webp")
    _, tok = _register(cli)
    buf = io.BytesIO()
    Image.new("RGB", (96, 64), (40, 100, 220)).save(buf, "WEBP", quality=85)

    r = _upload(cli, tok, buf.getvalue(), "me.webp", "image/webp")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["avatar"].startswith("img:")


def test_avatar_bomb_rejected_before_decode(monkeypatch):
    """解压炸弹：文件几百字节、声称 20000×20000 → 必须在解码前拒掉。

    ★ 这条如果只断言"返回 400"是瞎的：真的解了码也会因为 OOM 之外的错误返回 400
    或者进程直接挂掉。所以额外断言**处理耗时**很短 —— 解码 20000×20000 不可能
    在 1 秒内完成。
    """
    import time

    cli = _fresh(monkeypatch, "av_bomb")
    _, tok = _register(cli)

    data = _png_declaring(20000, 20000)
    assert len(data) < 1000, len(data)
    t0 = time.monotonic()
    r = _upload(cli, tok, data, "bomb.png", "image/png")
    elapsed = time.monotonic() - t0
    assert r.status_code == 400, r.text
    assert "像素" in r.json()["detail"] or "尺寸" in r.json()["detail"], r.json()
    assert elapsed < 1.5, f"耗时 {elapsed:.2f}s，不像是在解码前拦下的"
    assert _avatar_files() == []


def test_avatar_transparent_gets_white_not_black(monkeypatch):
    """透明 PNG 垫白底。

    直接 convert("RGB") 会把全透明像素变成**黑色**，一个白底表情包传上去
    就变成黑框头像 —— 用户以为是我们把图弄坏了。
    """
    from PIL import Image

    cli = _fresh(monkeypatch, "av_alpha")
    _, tok = _register(cli)
    r = _upload(cli, tok, _png_rgba(120, 120, 0), "t.png", "image/png")
    assert r.status_code == 200, r.text

    path = os.path.join(config.DATA_DIR, "avatars", _avatar_files()[0])
    with Image.open(path) as im:
        px = im.convert("RGB").getpixel((96, 96))
    assert min(px) > 240, px  # 接近纯白


def test_avatar_get_and_cache_headers(monkeypatch):
    """取图：内容正确 + 可永久缓存 + 非法 hash 一律 404（不泄露目录结构）。"""
    cli = _fresh(monkeypatch, "av_get")
    _, tok = _register(cli)
    avatar = _upload(cli, tok, _jpeg()).json()["user"]["avatar"]
    h = avatar[len("img:") :]

    r = cli.get(f"/api/auth/avatar/{h}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers.get("cache-control", "")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.content[:2] == b"\xff\xd8"  # JPEG 魔数

    # 不存在的 hash / 形状不对的 hash / 路径穿越，全部 404
    for bad in ["0" * 16, "..", "..%2F..%2Fsessions", "ABCDEF0123456789", h + ".jpg"]:
        r = cli.get(f"/api/auth/avatar/{bad}")
        assert r.status_code == 404, f"{bad} -> {r.status_code}"


def test_avatar_replace_deletes_old_file(monkeypatch):
    """换图后旧文件要删：否则一年下来攒一堆没人用的碎图。"""
    cli = _fresh(monkeypatch, "av_replace")
    _, tok = _register(cli)

    a1 = _upload(cli, tok, _jpeg(color=(10, 200, 10))).json()["user"]["avatar"]
    a2 = _upload(cli, tok, _jpeg(color=(200, 10, 10))).json()["user"]["avatar"]
    assert a1 != a2
    h1, h2 = a1[len("img:") :], a2[len("img:") :]
    assert h1 + ".jpg" not in _avatar_files()
    assert h2 + ".jpg" in _avatar_files()

    # 从照片换回内置图标，文件也该清掉
    r = cli.patch("/api/auth/profile", json={"avatar": "icon:star"}, headers=_h(tok))
    assert r.status_code == 200, r.text
    assert _avatar_files() == []


def test_avatar_shared_hash_not_deleted(monkeypatch):
    """两个人用同一张图（同一个内容 hash），其中一个人换掉不能把另一个人的图删了。"""
    cli = _fresh(monkeypatch, "av_share")
    _, tok_a = _register(cli, "alice")
    cli.post("/api/auth/register/", json={"username": "bob", "password": "bob12345"})
    tok_b = cli.cookies.get("baomi_session")

    data = _jpeg(color=(0, 90, 200))
    a1 = _upload(cli, tok_a, data).json()["user"]["avatar"]
    a2 = _upload(cli, tok_b, data).json()["user"]["avatar"]
    assert a1 == a2, "同图应共用一份文件"

    r = cli.patch("/api/auth/profile", json={"avatar": "icon:book"}, headers=_h(tok_a))
    assert r.status_code == 200
    # alice 换走了，但 bob 还在用这张图 → 文件必须留着
    assert _avatar_files() == [a1[len("img:") :] + ".jpg"], _avatar_files()
    assert cli.get(f"/api/auth/avatar/{a2[len('img:') :]}").status_code == 200


def test_profile_rejects_missing_img_hash(monkeypatch):
    """PATCH 传 img:<不存在> 要拒。

    格式合法不代表文件在。放行的话用户能把自己的头像改成一张不存在的图，
    前端显示破图 —— 而他不知道自己做了什么。
    """
    cli = _fresh(monkeypatch, "av_missing")
    _, tok = _register(cli)

    r = cli.patch("/api/auth/profile", json={"avatar": "img:" + "0" * 16}, headers=_h(tok))
    assert r.status_code == 400, r.text
    # 格式不合法的 hash 同样拒
    for bad in ["img:xyz", "img:" + "A" * 16, "img:../../etc/passwd", "img:"]:
        r = cli.patch("/api/auth/profile", json={"avatar": bad}, headers=_h(tok))
        assert r.status_code == 400, f"{bad} -> {r.status_code}"


def test_avatar_upload_requires_login(monkeypatch):
    cli = _fresh(monkeypatch, "av_anon")
    r = cli.post("/api/auth/avatar", files={"file": ("a.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 401


def test_avatar_audit_written(monkeypatch):
    """上传也进审计：头像出过"审核"问题，得能查是谁什么时候换的。"""
    cli = _fresh(monkeypatch, "av_audit")
    _, tok = _register(cli)
    _upload(cli, tok, _jpeg())

    log = os.path.join(config.DATA_DIR, "audit.log")
    with open(log, encoding="utf-8") as f:
        body = f.read()
    assert "avatar_upload" in body


def test_avatar_side_boundary(monkeypatch):
    """单边边界：4000 收，4001 拒。

    ★ 只测"炸弹被拒"是不够的 —— 把阈值写成 1 也能让它通过。
      边界两侧各打一发，才说明那条线真的在最该在的位置。
      4000×4000 用纯色（压完几十 KB），不会撞上 5MB 的传输上限。
    """
    from PIL import Image

    cli = _fresh(monkeypatch, "av_side")
    _, tok = _register(cli)

    buf = io.BytesIO()
    Image.new("RGB", (4000, 4000), (210, 170, 110)).save(buf, "JPEG", quality=70)
    r = _upload(cli, tok, buf.getvalue(), "max.jpg")
    assert r.status_code == 200, r.text

    r = _upload(cli, tok, _png_declaring(4001, 4001), "over.png", "image/png")
    assert r.status_code == 400, r.text
    assert "4000" in r.json()["detail"], r.json()


def test_avatar_same_hash_concurrent_store_uses_independent_tmp(monkeypatch, tmp_path):
    """同一张图并发落盘不能争用固定的 `hash.tmp`。

    两边渲染结果相同、目标 hash 也相同，但临时文件必须各自独立；否则 Windows
    上常见“一个线程刚 rename，另一个线程 rename 时源文件已不存在”的 500。
    """
    data_dir = tmp_path / "data"
    avatar_dir = data_dir / "avatars"
    avatar_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    src = tmp_path / "same.jpg"
    src.write_bytes(_jpeg(320, 240))

    real_replace = avatar_service.os.replace
    barrier = threading.Barrier(2)

    def synchronized_replace(src_path, dst_path):
        # 强制两个线程都在各自写完临时文件后才进入 rename，稳定打中竞态窗口。
        barrier.wait(timeout=5)
        return real_replace(src_path, dst_path)

    monkeypatch.setattr(avatar_service.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: avatar_service.store(str(src)), range(2)))

    assert results[0] == results[1]
    hash_ = results[0][len("img:") :]
    assert (avatar_dir / f"{hash_}.jpg").is_file()
    assert not list(avatar_dir.glob("*.tmp")), "并发落盘留下了临时文件"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-q"]))
