"""回归：JWT 登录闭环（2026-10-20 按 sky-take-out 登录顺序补齐）。

锁死这些行为：
  1. 令牌载荷带 uid / username / jti，且每枚令牌的 jti 不同；
  2. 三种携带方式都能认出身份：X-Auth-Token / Authorization: Bearer / Cookie，且头部优先；
  3. 被吊销（jti 进黑名单）的令牌立刻 401，不必等过期；
  4. 登录顺序对齐 sky-take-out：账号不存在 / 密码错 → 401 同一句话；
     账号锁定 → 403（必须区分，用户要知道不是密码错）；
  5. AUTH_RETURN_TOKEN 默认关闭，响应体不回吐 token。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import core.account as account_mod
import core.session as session_mod
import main
from core import config
from core.auth import (
    CLAIM_JTI,
    CLAIM_UID,
    CLAIM_USERNAME,
    create_token,
    current_user_id,
    decode_claims,
    decode_token,
    extract_token,
)

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _ensure_tables() -> None:
    """每个用例前把「当前指向的那个库」的表补齐。

    坑在这里：pytest.ini 已经记过一次——不少测试会把 `core.session._db_path`
    改到自己的临时目录，而 `core.account` / `core.session` 的建表只在**首次
    import** 时跑一次。全量跑时先被别的模块导入，users 表就建在了真实库，
    临时库里没有，于是任何走到 users 表的请求都会 500（`no such table: users`）。

    单跑本文件不会触发（没人改过路径），全量跑必炸。所以这里兜一层：
    `CREATE TABLE IF NOT EXISTS` 幂等，对真实库也只是补上缺的列。
    """
    session_mod._init_db()
    account_mod._ensure_tables()
    yield


class _URL:
    scheme = "http"


class _Req:
    """够用的 Request 替身：只读 headers / cookies / state。"""

    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.url = _URL()
        self.state = type("S", (), {})()


# ---------- 1. 载荷 ----------


def test_claims_carry_uid_username_jti():
    tok = create_token("u-1", username="alice")
    c = decode_claims(tok)
    assert c is not None
    assert c[CLAIM_UID] == "u-1"
    assert c[CLAIM_USERNAME] == "alice"
    assert c[CLAIM_JTI]
    assert c["exp"] > c["iat"]
    # 标准字段仍保留，老令牌 / 老代码都还认
    assert c["sub"] == "u-1"


def test_jti_is_unique_per_token():
    a = decode_claims(create_token("u-1"))[CLAIM_JTI]
    b = decode_claims(create_token("u-1"))[CLAIM_JTI]
    assert a != b


def test_decode_token_keeps_uid_contract():
    """老接口形状不变：decode_token 仍只返回 uid。"""
    assert decode_token(create_token("u-9")) == "u-9"
    assert decode_token("a.b.c") is None
    assert decode_token("") is None
    assert decode_token(create_token("u-9", ttl_days=-1)) is None


# ---------- 2. 令牌携带方式 ----------


def test_header_token_wins_over_cookie():
    req = _Req(headers={config.AUTH_TOKEN_HEADER: "header-tok"}, cookies={"baomi_session": "cookie-tok"})
    assert extract_token(req) == "header-tok"


def test_authorization_bearer():
    req = _Req(headers={"authorization": "Bearer abc.def.ghi"})
    assert extract_token(req) == "abc.def.ghi"


def test_cookie_fallback():
    req = _Req(cookies={"baomi_session": "cookie-tok"})
    assert extract_token(req) == "cookie-tok"


def test_bearer_prefix_in_custom_header():
    req = _Req(headers={config.AUTH_TOKEN_HEADER: "Bearer xyz"})
    assert extract_token(req) == "xyz"


def test_current_user_from_header_sets_state():
    tok = create_token("u-2", username="bob")
    req = _Req(headers={config.AUTH_TOKEN_HEADER: tok})
    assert current_user_id(req) == "u-2"
    assert req.state.uid == "u-2"
    assert req.state.username == "bob"


def test_current_user_rejects_garbage():
    for bad in ("", "a.b.c", create_token("u-3", ttl_days=-1)):
        try:
            current_user_id(_Req(headers={config.AUTH_TOKEN_HEADER: bad}))
        except HTTPException as e:
            assert e.status_code == 401
        else:  # pragma: no cover
            raise AssertionError("应当 401")


# ---------- 3. 吊销 ----------


def test_revoked_token_is_401(monkeypatch):
    tok = create_token("u-4")
    monkeypatch.setattr("core.token_store.is_revoked", lambda jti: True)
    try:
        current_user_id(_Req(headers={config.AUTH_TOKEN_HEADER: tok}))
    except HTTPException as e:
        assert e.status_code == 401
        assert "退出" in str(e.detail)
    else:  # pragma: no cover
        raise AssertionError("被吊销的令牌应当 401")


def test_logout_revokes_jti(monkeypatch):
    seen = {}
    monkeypatch.setattr("core.token_store.revoke", lambda jti, exp: seen.update(jti=jti, exp=exp))
    tok = create_token("u-5")
    r = client.post("/api/auth/logout/", headers={config.AUTH_TOKEN_HEADER: tok})
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    assert seen["jti"] == decode_claims(tok)[CLAIM_JTI]


# ---------- 4. 登录顺序 ----------


def test_login_unknown_user_and_bad_password_share_message(monkeypatch):
    monkeypatch.setattr(account_mod, "login", lambda u, p: (None, account_mod.LOGIN_NO_USER))
    r1 = client.post("/api/auth/login/", json={"username": "nobody_a", "password": "x" * 8})
    monkeypatch.setattr(account_mod, "login", lambda u, p: (None, account_mod.LOGIN_BAD_PASSWORD))
    r2 = client.post("/api/auth/login/", json={"username": "nobody_b", "password": "x" * 8})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]


def test_login_locked_account_is_403(monkeypatch):
    monkeypatch.setattr(account_mod, "login", lambda u, p: (None, account_mod.LOGIN_LOCKED))
    r = client.post("/api/auth/login/", json={"username": "locked_user", "password": "x" * 8})
    assert r.status_code == 403
    assert "停用" in r.json()["detail"]


def test_login_success_sets_cookie_without_exposing_token(monkeypatch):
    monkeypatch.setattr(account_mod, "login", lambda u, p: ({"id": "u-7", "username": "carol"}, account_mod.LOGIN_OK))
    r = client.post("/api/auth/login/", json={"username": "carol", "password": "x" * 8})
    assert r.status_code == 200
    assert r.json()["id"] == "u-7"
    assert r.json()["token"] is None, "默认不应把 token 交给 JS"
    sc = r.headers.get("set-cookie", "")
    assert "baomi_session=" in sc and "httponly" in sc.lower()


def test_login_can_expose_token_when_enabled(monkeypatch):
    monkeypatch.setattr(account_mod, "login", lambda u, p: ({"id": "u-8", "username": "dave"}, account_mod.LOGIN_OK))
    monkeypatch.setattr(config, "AUTH_RETURN_TOKEN", True)
    r = client.post("/api/auth/login/", json={"username": "dave", "password": "x" * 8})
    tok = r.json().get("token")
    assert tok and decode_token(tok) == "u-8"
