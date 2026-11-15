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
    sql = "SELECT path, uid, internal FROM api_call_logs"
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
