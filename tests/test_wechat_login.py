"""微信直接注册：隔离库、模拟微信 HTTP，绝不使用真实 AppSecret 或连接微信。"""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import router as auth_router
from api.auth import signup_service
from api.email_login import router as email_login_router
from api.password_reset import router as reset_router
from api.registration import router as registration_router
from api.security_email import get_service
from api.security_email import router as security_router
from api.wechat_login import router as wechat_router
from core import account, config, external_accounts, security_mail, session, wechat
from core.auth import create_token, has_usable_password, verify_password
from core.external_accounts import ExternalIdentity
from core.security_email import SecurityEmailService
from core.verification import VerificationStore

# 仅加载本次涉及的真实路由，不导入 main 的 LLM、向量库或其他外部集成。
app = FastAPI()
for router in (auth_router, registration_router, email_login_router, reset_router, security_router, wechat_router):
    app.include_router(router, prefix="/api/auth")


@pytest.fixture
def wxenv(tmp_path, monkeypatch):
    path = tmp_path / "wechat.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(path))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "AUTH_SECRET", "isolated-wechat-tests-only-secret")
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    monkeypatch.setattr(config, "AUTH_PBKDF2_ROUNDS", 1000)
    session._init_db()
    clock, messages = [1800000000], []
    store = VerificationStore(path, b"v" * 32, clock=lambda: clock[0])
    store.migrate()
    service = SecurityEmailService(
        store,
        sender=lambda address, code, purpose: messages.append((address, code, purpose)) or {"ok": True},
        ready=lambda: True,
    )
    monkeypatch.setattr(config, "WECHAT_LOGIN_ENABLED", True)
    monkeypatch.setattr(config, "WECHAT_APP_ID", "wx_test_app")
    monkeypatch.setattr(config, "WECHAT_APP_SECRET", "fake-secret-only-for-tests")
    monkeypatch.setattr(config, "WECHAT_REDIRECT_URI", "https://testserver/api/auth/wechat/callback")
    monkeypatch.setattr(config, "SECURITY_EMAIL_ENABLED", True)
    monkeypatch.setattr(security_mail, "status", lambda: {"ready": True})
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[signup_service] = lambda: service
    external_accounts.ensure_tables()
    calls = []
    profile = {"openid": "person_A", "nickname": "小明"}
    real_client = httpx.Client

    def handler(request):
        calls.append(request)
        if request.url.path == "/sns/oauth2/access_token":
            return httpx.Response(200, json={"openid": profile["openid"], "access_token": "fake-access-token"})
        return httpx.Response(200, json=profile)

    monkeypatch.setattr(wechat.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    # TestClient 本身继承 httpx.Client，不通过上面的工厂创建。
    cli = TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))
    yield service, cli, messages, clock, calls, profile
    app.dependency_overrides.pop(get_service, None)
    app.dependency_overrides.pop(signup_service, None)


def begin(cli, action="login"):
    response = cli.post("/api/auth/wechat/start", json={"action": action})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorize_url"]).query)["state"][0]


def finish(cli, state):
    return cli.get(
        "/api/auth/wechat/callback?" + urlencode({"state": state, "code": "fake-code"}), follow_redirects=False
    )


def sign_in(cli, name="existing"):
    user = account.create_user(name, "Strong_existing_pass_9")
    cli.cookies.set("baomi_session", create_token(user["id"]), domain="testserver.local", path="/")
    return user


def count_users():
    with session._get_conn() as db:
        return db.execute("SELECT count(*) FROM users").fetchone()[0]


def test_auto_signup_no_email_no_default_password_and_repeat_login(wxenv):
    _, cli, _, _, calls, _ = wxenv
    response = finish(cli, begin(cli))
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()
    user = cli.get("/api/auth/me/").json()["user"]
    assert user["username"].startswith("wx_") and user["nickname"] == "小明"
    assert user["has_password"] is False and count_users() == 1
    row = account.get_user(user["id"])
    assert not has_usable_password(row["password_hash"])
    assert all(not verify_password(value, row["password_hash"]) for value in ("", "123456", user["username"]))
    assert "fake-access-token" not in response.headers["location"]
    assert cli.get("/api/auth/security-email").json()["bound"] is False
    cli.cookies.clear()
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").json()["user"]["id"] == user["id"]
    assert count_users() == 1 and len(calls) == 4


def test_missing_nickname_falls_back_and_same_nickname_does_not_merge(wxenv):
    _, cli, _, _, _, profile = wxenv
    profile.pop("nickname")
    finish(cli, begin(cli))
    first = cli.get("/api/auth/me/").json()["user"]
    assert first["nickname"] == "微信用户"
    cli.cookies.clear()
    profile["openid"] = "person_B"
    finish(cli, begin(cli))
    assert count_users() == 2
    assert cli.get("/api/auth/me/").json()["user"]["id"] != first["id"]


@pytest.mark.parametrize("kind", ["wrong_state", "foreign_browser", "expired", "duplicate"])
def test_invalid_flows_do_not_call_wechat_or_create_user(wxenv, kind):
    _, cli, _, _, calls, _ = wxenv
    state = begin(cli)
    if kind == "wrong_state":
        state = "x" * 43
    elif kind == "foreign_browser":
        cli.cookies.clear()
    elif kind == "expired":
        with session._get_conn() as db:
            db.execute("UPDATE external_auth_flows SET expires=0")
    elif kind == "duplicate":
        response = cli.get(
            f"/api/auth/wechat/callback?state={state}&state={state}&code=fake-code", follow_redirects=False
        )
        assert "reason=flow_invalid" in response.headers["location"]
        assert not calls and count_users() == 0
        return
    assert "reason=flow_invalid" in finish(cli, state).headers["location"]
    assert not calls and count_users() == 0


def test_state_is_single_use_and_new_browser_cannot_replay_it(wxenv):
    _, cli, _, _, calls, _ = wxenv
    state = begin(cli)
    finish(cli, state)
    cli.cookies.delete("baomi_session")
    assert "reason=flow_invalid" in finish(cli, state).headers["location"]
    assert len(calls) == 2 and count_users() == 1


def test_signup_switch_blocks_new_accounts_but_allows_existing_login(wxenv, monkeypatch):
    _, cli, _, _, _, _ = wxenv
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    cli.cookies.clear()
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").json()["user"]["id"] == uid
    cli.cookies.clear()
    wxenv[-1]["openid"] = "unregistered_person"
    assert "reason=signup_closed" in finish(cli, begin(cli)).headers["location"]
    assert count_users() == 1


def test_bind_existing_account_and_reject_switch_and_conflict(wxenv):
    _, cli, _, _, calls, _ = wxenv
    alice = sign_in(cli)
    state = begin(cli, "bind")
    cli.cookies.clear()
    sign_in(cli, "other_user")
    assert "reason=flow_invalid" in finish(cli, state).headers["location"]
    assert not calls
    cli.cookies.clear()
    cli.cookies.set("baomi_session", create_token(alice["id"]))
    assert "wechat_result=bound" in finish(cli, begin(cli, "bind")).headers["location"]
    assert count_users() == 2
    cli.cookies.clear()
    sign_in(cli, "third_user")
    assert "reason=already_bound" in finish(cli, begin(cli, "bind")).headers["location"]


def test_last_method_cannot_unlink_but_password_account_can(wxenv):
    _, cli, _, _, _, _ = wxenv
    finish(cli, begin(cli))
    assert "reason=last_login_method" in finish(cli, begin(cli, "unlink")).headers["location"]
    user = cli.get("/api/auth/me/").json()["user"]
    assert cli.get("/api/auth/wechat/status").json()["bound"]
    with session._get_conn() as db:
        from core.auth import hash_password

        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password("New_secret_9"), user["id"]))
    old = cli.cookies.get("baomi_session")
    assert "wechat_result=unlinked" in finish(cli, begin(cli, "unlink")).headers["location"]
    assert count_users() == 1 and not cli.get("/api/auth/wechat/status").json()["bound"]
    assert cli.get("/api/auth/me/", headers={"X-Auth-Token": old}).status_code == 401


def test_bind_email_then_set_first_password_through_existing_verified_flow(wxenv):
    _, cli, messages, _, _, _ = wxenv
    finish(cli, begin(cli))
    user = cli.get("/api/auth/me/").json()["user"]
    send = cli.post("/api/auth/email/code/send", json={"purpose": "bind_email", "address": "wx@example.com"}).json()
    proof = cli.post(
        "/api/auth/email/code/verify", json={"request_id": send["request_id"], "code": messages[-1][1]}
    ).json()
    assert cli.post("/api/auth/security-email/bind", json=proof).status_code == 200
    wxenv[3][0] += 61
    send = cli.post("/api/auth/password/reset/email/send", json={"address": "wx@example.com"}).json()
    proof = cli.post(
        "/api/auth/password/reset/email/verify", json={"request_id": send["request_id"], "code": messages[-1][1]}
    ).json()
    response = cli.post(
        "/api/auth/password/reset",
        json={**proof, "new_password": "First_password_9", "confirm_password": "First_password_9"},
    )
    assert response.status_code == 200, response.text
    assert cli.get("/api/auth/me/").status_code == 401
    response = cli.post("/api/auth/login/", json={"username": user["username"], "password": "First_password_9"})
    assert response.status_code == 200 and response.json()["has_password"] is True


def test_disabled_and_deleted_accounts_and_new_signup_after_deletion(wxenv):
    _, cli, _, _, _, _ = wxenv
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    account.set_status(uid, 0)
    assert cli.get("/api/auth/me/").status_code == 403
    cli.cookies.clear()
    assert "reason=account_unavailable" in finish(cli, begin(cli)).headers["location"]
    assert count_users() == 1
    assert account.delete_user(uid)
    assert not external_accounts.is_bound(uid, config.WECHAT_APP_ID, provider="wechat")
    cli.cookies.clear()
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").json()["user"]["id"] != uid


def test_concurrent_identity_creation_is_one_user_and_failed_binding_rolls_back(wxenv):
    identity = ExternalIdentity("wechat", config.WECHAT_APP_ID, "concurrent")
    flow = {"provider": "wechat", "app_id": identity.app_id, "action": "login", "user_id": "", "auth_version": 0}
    with ThreadPoolExecutor(max_workers=3) as executor:
        users = list(executor.map(lambda _: external_accounts.complete(identity, flow), range(3)))
    assert len({u["id"] for u in users}) == count_users() == 1
    with session._get_conn() as db:
        db.execute(
            "CREATE TRIGGER refuse_binding BEFORE INSERT ON external_identities BEGIN SELECT RAISE(ABORT,'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        external_accounts.complete(ExternalIdentity("wechat", identity.app_id, "other"), flow)
    assert count_users() == 1


def test_browser_cannot_submit_identity_and_cross_origin_start_is_rejected(wxenv):
    cli = wxenv[1]
    assert cli.post("/api/auth/wechat/start", json={"subject": "attacker"}).status_code == 422
    assert cli.post("/api/auth/wechat/start", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert cli.post("/api/auth/wechat/start", json={"action": "bind"}).status_code == 401
    assert count_users() == 0


def test_configuration_disabled_and_httpx_logs_do_not_leak_credentials(wxenv, monkeypatch, caplog):
    _, cli, _, _, _, _ = wxenv
    with caplog.at_level(logging.INFO, logger="httpx"):
        finish(cli, begin(cli))
    assert "fake-secret-only-for-tests" not in caplog.text
    assert "fake-access-token" not in caplog.text
    monkeypatch.setattr(config, "WECHAT_LOGIN_ENABLED", False)
    assert cli.get("/api/auth/config/").json()["wechat_enabled"] is False
    assert cli.post("/api/auth/wechat/start", json={}).status_code == 503


def test_plain_registration_still_requires_verified_email_and_password_login_works(wxenv):
    _, cli, messages, _, _, _ = wxenv
    body = {"username": "plain_user", "password": "Ordinary_pass_9"}
    assert cli.post("/api/auth/register/", json=body).status_code == 422
    send = cli.post("/api/auth/register/email/send", json={"address": "ordinary@example.com"}).json()
    proof = cli.post(
        "/api/auth/register/email/verify",
        json={
            "request_id": send["request_id"],
            "code": messages[-1][1],
        },
    ).json()
    response = cli.post(
        "/api/auth/register/",
        json={
            **body,
            "email": "ordinary@example.com",
            "email_grant": proof["grant"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["has_password"] is True
    cli.cookies.clear()
    assert cli.post("/api/auth/login/", json=body).status_code == 200


def test_wechat_error_does_not_create_an_account_or_reuse_the_flow(wxenv, monkeypatch):
    _, cli, _, _, _, _ = wxenv

    # 直接替换 HTTP 结果，保留路由、state 消费与平台适配器的真实执行顺序。
    def fail(*args, **kwargs):
        raise external_accounts.ExternalAuthError("provider_failed", "invalid code")

    monkeypatch.setattr(wechat, "_get", fail)
    state = begin(cli)
    assert "reason=provider_failed" in finish(cli, state).headers["location"]
    assert count_users() == 0
    assert "reason=flow_invalid" in finish(cli, state).headers["location"]


def test_verified_email_alone_never_allows_unlink_without_password(wxenv, monkeypatch):
    service, cli, _, clock, _, _ = wxenv
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    with sqlite3.connect(service.store.path) as db:
        db.execute("INSERT INTO security_emails VALUES (?,?,?,1)", (uid, "bound@example.com", clock[0]))
    monkeypatch.setattr(security_mail, "status", lambda: {"ready": False})
    assert "reason=last_login_method" in finish(cli, begin(cli, "unlink")).headers["location"]
    monkeypatch.setattr(security_mail, "status", lambda: {"ready": True})
    assert "reason=last_login_method" in finish(cli, begin(cli, "unlink")).headers["location"]


def test_change_binding_requires_unlink_before_binding_new_wechat(wxenv):
    _, cli, _, _, _, profile = wxenv
    user = sign_in(cli)
    assert "wechat_result=bound" in finish(cli, begin(cli, "bind")).headers["location"]
    profile["openid"] = "person_B"
    assert "reason=already_bound" in finish(cli, begin(cli, "bind")).headers["location"]
    with session._get_conn() as db:
        subject = db.execute("SELECT subject FROM external_identities WHERE user_id=?", (user["id"],)).fetchone()[0]
        assert subject == "person_A"
    profile["openid"] = "person_A"
    assert "wechat_result=unlinked" in finish(cli, begin(cli, "unlink")).headers["location"]
    profile["openid"] = "person_B"
    assert "wechat_result=bound" in finish(cli, begin(cli, "bind")).headers["location"]
    with session._get_conn() as db:
        subject = db.execute("SELECT subject FROM external_identities WHERE user_id=?", (user["id"],)).fetchone()[0]
        assert subject == "person_B"


def test_profile_identity_mismatch_and_rate_limit_do_not_create_users(wxenv, monkeypatch):
    _, cli, _, _, _, _ = wxenv
    original = wechat._get

    def mismatched(client, path, params):
        result = original(client, path, params)
        return {**result, "openid": "another_person"} if path == "/sns/userinfo" else result

    monkeypatch.setattr(wechat, "_get", mismatched)
    assert "reason=identity_invalid" in finish(cli, begin(cli)).headers["location"]
    assert count_users() == 0
    monkeypatch.setattr(wechat, "_get", original)
    monkeypatch.setattr(config, "SIGNUP_GLOBAL_PER_DAY", 1)
    assert finish(cli, begin(cli)).headers["location"] == "/"
    cli.cookies.clear()
    wxenv[-1]["openid"] = "another_person"
    assert "reason=rate_limited" in finish(cli, begin(cli)).headers["location"]
    assert count_users() == 1
