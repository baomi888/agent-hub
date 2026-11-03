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

import os
import sys
import tempfile
import threading

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

config.ALLOW_SIGNUP = True
# 显式配一个管理员名：默认是空列表，"改名抢注管理员"这条闸不会被触发，
# 断言就成了假通过（真实部署里这个名单来自 .env）
config.ADMIN_USERNAMES = ["admin"]
config.AUTH_SECRET = "test-secret-account-profile"
config._AUTH_SECRET_FILE = os.path.join(_TMP, ".auth_secret")
# 关掉失败锁定：本文件测的是改密码/改名，不想被"连续失败 N 次锁号"干扰
config.AUTH_MAX_FAILS = 0
config.AUTH_LOCK_MINUTES = 0

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import account, quota  # noqa: E402
from service import daily  # noqa: E402

# 导入期结束，把全局路径还给别的模块（每个用例自己用 _fresh 切临时库）。
# 连接是按路径缓存的，路径变回去时会自动重连，这里不用清 _LOCAL。
session._db_path = _ORIG_DB_PATH


def _fresh(monkeypatch, tag: str) -> TestClient:
    """切到一个空库并返回独立 Cookie 罐子的客户端。"""
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
    # 限流按用户名/uid 分桶，同一个 client 连续调会累积
    quota._HITS.clear()
    return TestClient(main.app)


def _register(cli: TestClient, name="alice", pwd="abc123") -> tuple[str, str]:
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
        "前后端头像名单不一致：仅前端 %s / 仅后端 %s"
        % (sorted(names - set(account.AVATAR_ICONS)), sorted(set(account.AVATAR_ICONS) - names))
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
        json={"old_password": "abc123", "new_password": "def456"},
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
        cli.post("/api/auth/login/", json={"username": "alice", "password": "def456"}).status_code
        == 200
    )
    assert (
        cli.post("/api/auth/login/", json={"username": "alice", "password": "abc123"}).status_code
        == 401
    )


def test_password_change_rejects(monkeypatch):
    cli = _fresh(monkeypatch, "pwd_bad")
    _, tok = _register(cli)

    # 旧密码错
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "wrong", "new_password": "def456"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text
    # 新密码太短
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "abc123", "new_password": "123"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text
    # 新旧相同
    r = cli.post(
        "/api/auth/password",
        json={"old_password": "abc123", "new_password": "abc123"},
        headers=_h(tok),
    )
    assert r.status_code == 400, r.text

    # 负向：以上三次失败都不该让旧令牌失效（否则一输错密码自己就被踢）
    assert cli.get("/api/auth/me/", headers=_h(tok)).status_code == 200


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
        json={"password": "abc123", "new_username": "alice2"},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "alice2"
    assert r.json()["user"]["name_changes_left"] == account.MAX_NAME_CHANGES - 1

    # 旧名登不上、新名能登
    assert (
        cli.post("/api/auth/login/", json={"username": "alice", "password": "abc123"}).status_code
        == 401
    )
    assert (
        cli.post("/api/auth/login/", json={"username": "alice2", "password": "abc123"}).status_code
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
        cli.post("/api/auth/login/", json={"username": "alice", "password": "abc123"}).status_code
        == 200
    )


def test_username_duplicate_and_reserved(monkeypatch):
    cli = _fresh(monkeypatch, "name_dup")
    _, tok = _register(cli, "alice")
    # 注册 bob 会把 Cookie 罐子换成 bob 的，所以后面一律显式带 token
    cli.post("/api/auth/register/", json={"username": "bob", "password": "bob123"})

    r = cli.post(
        "/api/auth/username", json={"password": "abc123", "new_username": "bob"}, headers=_h(tok)
    )
    assert r.status_code == 409, r.text
    # NOCASE 唯一索引：大小写不同也算重名
    r = cli.post(
        "/api/auth/username", json={"password": "abc123", "new_username": "BOB"}, headers=_h(tok)
    )
    assert r.status_code == 409, r.text

    # ★ 抢注管理员名必须被挡：否则改个名就白拿管理员权限
    for bad in ["admin", "ADMIN"]:
        r = cli.post(
            "/api/auth/username", json={"password": "abc123", "new_username": bad}, headers=_h(tok)
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
            json={"password": "abc123", "new_username": "alice_%d" % i},
            headers=_h(tok),
        )
        assert r.status_code == 200, r.text

    quota._HITS.clear()
    r = cli.post(
        "/api/auth/username", json={"password": "abc123", "new_username": "alice_x"}, headers=_h(tok)
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
        json={"old_password": "abc123", "new_password": "def456"},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text

    # ★ 改密码会作废旧令牌（水位）。所以这里必须重新登录拿令牌 ——
    #   顺手把"旧令牌真的失效了"变成断言，而不是默默 401 导致后面少写一条日志。
    assert cli.get("/api/auth/me/", headers=_h(tok)).status_code == 401
    quota._HITS.clear()
    r = cli.post("/api/auth/login/", json={"username": "alice", "password": "def456"})
    assert r.status_code == 200, r.text
    tok2 = cli.cookies.get("baomi_session")

    r = cli.post(
        "/api/auth/username",
        json={"password": "def456", "new_username": "alice9"},
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
    assert "def456" not in body
    assert "abc123" not in body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-q"]))
