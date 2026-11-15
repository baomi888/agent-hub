"""`X-Verify` 自动化流量打标的回归（2026-11-15）。

锁三件事：

  1. **声明不够，还要身份**：普通账号自己加 `X-Verify: 1` 必须无效 ——
     否则任何真实用户都能让自己的调用从看板上消失，那不是清洗数据，是给了
     一个把证据藏起来的开关。
  2. **内部账号发才生效**：我们的截图/取证脚本用的是内部账号的登录态，
     打标之后这些请求要进 `internal` 桶，不再污染真实用户。
  3. **打标是请求级的**：同一个内部账号里，没带头的请求不受影响（人点还是人点）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import account, apilog, config, quota, session, user_feedback, verify_tag

PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "verify.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables(force=True)
    quota._ensure_tables()
    quota._ensure_rate_table()
    apilog._READY.clear()
    apilog._BUF.clear()
    user_feedback._READY.clear()
    apilog.ensure()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [])
    monkeypatch.setattr(config, "ADMIN_UIDS", [])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    verify_tag.invalidate()
    yield path
    apilog._BUF.clear()
    apilog._READY.clear()
    verify_tag.invalidate()


@pytest.fixture()
def client(env):
    import main

    return TestClient(main.app)


def _mk(name: str, internal: bool = False) -> str:
    u = account.get_user_by_username(name)
    uid = u["id"] if u else account.create_user(name, PASSWORD)["id"]
    if internal:
        account.set_internal(uid, True)
    verify_tag.invalidate()
    return uid


def _login(client: TestClient, uid: str, name: str) -> None:
    from core.auth import create_token

    client.cookies.set("baomi_session", create_token(uid, 7, name))


def _rows(**where) -> list:
    sql = "SELECT path, uid, internal, client FROM api_call_logs"
    args: list = []
    if where:
        sql += " WHERE " + " AND ".join(k + " = ?" for k in where)
        args = list(where.values())
    with session._get_conn() as conn:  # noqa: SLF001
        return conn.execute(sql, args).fetchall()


# ==================== A. 判定矩阵 ====================


def test_header_without_internal_identity_is_ignored(env):
    """普通账号自报 `X-Verify: 1` 无效 —— 否则真实用户能把自己从统计里抹掉。"""
    uid = _mk("plainuser")
    assert verify_tag.is_verify("1", uid) is False


def test_header_from_internal_account_is_honored(env):
    uid = _mk("shotbot", internal=True)
    assert verify_tag.is_verify("1", uid) is True


def test_header_from_admin_is_honored(env, monkeypatch):
    """管理员不需要 is_internal 标记，按 config 即时排除（与看板口径一致）。"""
    uid = _mk("boss")
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["boss"])
    verify_tag.invalidate()
    assert verify_tag.is_verify("1", uid) is True


def test_anonymous_verify_traffic_is_tagged(env):
    """未登录的验证流量（截登录页）也打标：它本来就不进真实用户，
    打标只是让这批自动化请求能被单独看见。"""
    assert verify_tag.is_verify("1", None) is True


@pytest.mark.parametrize("value", ["0", "", "true", "2", None])
def test_non_one_values_are_not_a_declaration(env, value):
    uid = _mk("shotbot2", internal=True)
    assert verify_tag.is_verify(value, uid) is False


def test_cache_invalidate_picks_up_new_marking(env):
    """刚标为内部就该生效：缓存失效函数必须真的让下一次判定重新读库。"""
    uid = _mk("newly_marked")
    assert verify_tag.is_verify("1", uid) is False
    account.set_internal(uid, True)
    # 不 invalidate 时可能还在缓存期（60s），这正是接口层要显式失效的原因
    verify_tag.invalidate()
    assert verify_tag.is_verify("1", uid) is True


# ==================== B. 端到端：中间件真的写进库 ====================


def test_normal_user_request_is_not_tagged(client, env):
    uid = _mk("plainuser")
    _login(client, uid, "plainuser")
    client.get("/api/auth/me/", headers={"X-Verify": "1"})
    apilog.flush()
    rows = [r for r in _rows() if r["path"] == "/api/auth/me/"]
    assert rows, "请求没有被采集（先看 apilog.should_record）"
    assert all(int(r["internal"]) == 0 for r in rows), "普通用户的请求被打了标 —— 他能把自己藏起来"


def test_internal_account_request_is_tagged(client, env):
    uid = _mk("shotbot", internal=True)
    _login(client, uid, "shotbot")
    client.get("/api/auth/me/", headers={"X-Verify": "1"})
    apilog.flush()
    rows = [r for r in _rows() if r["path"] == "/api/auth/me/"]
    assert rows and all(int(r["internal"]) == 1 for r in rows)


def test_same_account_without_header_stays_real(client, env):
    """请求级打标：同一个内部账号，不带头的请求不会被顺手标掉。

    反过来也说明"按账号排除"是不够的：内部账号里也可能有人在真实使用。
    """
    uid = _mk("shotbot", internal=True)
    _login(client, uid, "shotbot")
    with session._get_conn() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM api_call_logs")
    apilog._BUF.clear()

    client.get("/api/auth/me/")  # 无头
    apilog.flush()
    rows = [r for r in _rows() if r["path"] == "/api/auth/me/"]
    assert rows and all(int(r["internal"]) == 0 for r in rows)


# ==================== C. UA 自动识别（11-15 补的第二层判据）====================
#
# 为什么必须补这一层：截图脚本走的是**浏览器导航**，加不了自定义请求头，
# 所以第一层 `X-Verify` 对它 100% 失效。实测 `苞米呀`（真人账号）7 天 12772 次
# 调用里 85.6% 是同一个 IP 的 `HeadlessChrome` —— 账号维度完全正常，只有 UA 露馅。

# 线上真实出现过的那条 UA（HeadlessChrome + Edg 后缀，容易被当成普通 Edge 放过）
HEADLESS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) HeadlessChrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0"
)


@pytest.mark.parametrize(
    "ua, want",
    [
        (None, ""),
        ("", ""),
        # 真浏览器：一条都不能误判 —— 判错的代价是真人从"真实用户"里消失且没人发现
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
            "browser",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
            " AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "browser",
        ),
        (
            "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
            "browser",
        ),
        # App 内置 WebView 的 UA 里可能顺带出现 java/ 之类，不能被标记词误伤
        (
            "Mozilla/5.0 (Linux; Android 4.0) AppleWebKit/537.36 java/1.0",
            "browser",
        ),
        (HEADLESS_UA, "headless"),
        ("curl/8.4.0", "script"),
        ("python-requests/2.31.0", "script"),
        ("Go-http-client/1.1", "script"),
    ],
)
def test_classify_ua(ua, want):
    """UA 归类矩阵：宁可漏判（数字偏高还能查），也不能误判（真人被藏起来）。"""
    assert verify_tag.classify_ua(ua) == want


def test_headless_ua_is_tagged_without_header_or_identity(env):
    """★ 核心回归：普通账号 + 无头浏览器 → 直接打标。

    不需要 `X-Verify`，也不需要内部身份。若这里要求身份，`苞米呀` 那 12772 次
    调用就永远洗不干净 —— 他恰恰是个普通账号。
    """
    assert verify_tag.is_verify(None, "some-normal-uid", HEADLESS_UA) is True
    # 反过来：同一句话换成真浏览器，没有请求头就不能打标，否则真人会消失
    assert verify_tag.is_verify(None, "some-normal-uid", "Mozilla/5.0 (iPhone)") is False


def test_headless_browser_is_tagged_end_to_end(client, env):
    """端到端：无头浏览器的请求要进 internal 桶，并且 client 列要留下证据。

    `client` 列不是装饰：没有它，事后只能像 11-15 那样回头翻 nginx 日志按秒做匹配，
    而日志只留 14 天。
    """
    uid = _mk("plainuser")
    _login(client, uid, "plainuser")
    client.get("/api/auth/me/", headers={"User-Agent": HEADLESS_UA})
    apilog.flush()
    rows = [r for r in _rows() if r["path"] == "/api/auth/me/"]
    assert rows, "请求没有被采集（先看 apilog.should_record）"
    assert all(int(r["internal"]) == 1 for r in rows), "无头浏览器的请求没有被自动打标"
    assert all(r["client"] == "headless" for r in rows), "client 列没落库，事后无法复核"


def test_normal_browser_is_recorded_as_browser(client, env):
    """真人浏览器要落成 `browser` 且不打标 —— 这是 real 桶的唯一合法门票。"""
    uid = _mk("plainuser")
    _login(client, uid, "plainuser")
    client.get(
        "/api/auth/me/",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    apilog.flush()
    rows = [r for r in _rows() if r["path"] == "/api/auth/me/"]
    assert rows and all(r["client"] == "browser" for r in rows)
    assert all(int(r["internal"]) == 0 for r in rows)
