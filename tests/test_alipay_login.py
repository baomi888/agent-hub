"""支付宝完整登录闭环：仅替换 SDK 的网络传输，真实执行 RSA2 签名和验签。"""

import base64
import importlib
import json
import logging
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_wechat_login import begin as wx_begin
from test_wechat_login import sign_in
from test_wechat_login import wxenv as wxenv

from api.alipay_login import router as alipay_router
from api.auth import router as auth_router
from core import account, alipay, config, external_accounts, session
from core.auth import hash_password, verify_password


@pytest.fixture(scope="module")
def key_material():
    # 不读取 docs 中真实密钥；临时生成两对密钥，分别模拟应用和支付宝平台。
    from Crypto.PublicKey import RSA

    return RSA.generate(2048), RSA.generate(2048)


@pytest.fixture
def alienv(wxenv, tmp_path, monkeypatch, key_material):
    from Crypto.Hash import SHA256
    from Crypto.Signature import pkcs1_15

    application, server = key_material
    private_file, public_file = tmp_path / "application.txt", tmp_path / "platform.txt"
    # 覆盖开放平台常见的无 PEM 头 PKCS8 私钥和 SPKI 公钥。
    private_file.write_bytes(base64.b64encode(application.export_key(format="DER", pkcs=8)))
    public_file.write_bytes(base64.b64encode(server.public_key().export_key(format="DER")))
    for key, value in {
        "ALIPAY_LOGIN_ENABLED": True,
        "ALIPAY_APP_ID": config.WECHAT_APP_ID,
        "ALIPAY_REDIRECT_URI": "https://testserver/api/auth/alipay/callback",
        "ALIPAY_APP_PRIVATE_KEY_FILE": str(private_file),
        "ALIPAY_PUBLIC_KEY_FILE": str(public_file),
        "ALIPAY_SUBJECT_FIELD": "open_id",
    }.items():
        monkeypatch.setattr(config, key, value)
    calls, profile = [], {"subject": "member-A", "nickname": "支付宝小明", "failure": None}

    def transport(url, query_string, headers, params, charset, timeout):
        request = dict(parse_qsl(query_string))
        request.update(params)
        signature = base64.b64decode(request.pop("sign"))
        content = "&".join(f"{k}={v}" for k, v in sorted(request.items())).encode("utf-8")
        # 请求的授权码、auth_token 等全部在签名范围内。
        pkcs1_15.new(application.public_key()).verify(SHA256.new(content), signature)
        assert request["app_id"] == config.ALIPAY_APP_ID and request["sign_type"] == "RSA2"
        assert url == "https://openapi.alipay.com/gateway.do" and timeout == 10
        calls.append(request)
        method = request["method"]
        token_request = method == "alipay.system.oauth.token"
        if profile["failure"] == "timeout":
            raise TimeoutError("fake-code fake-token raw transport detail")
        identity = {"open_id": profile["subject"], "user_id": "uid-" + profile["subject"]}
        data = dict(identity)
        if token_request:
            assert request["grant_type"] == "authorization_code" and request["code"] == "fake-code"
            data["access_token"] = "fake-alipay-access-token"
            if profile["failure"] == "missing_identity":
                data.pop(config.ALIPAY_SUBJECT_FIELD)
            if profile["failure"] == "business":
                data = {"code": "40004", "sub_msg": "fake-code rejected"}
        else:
            assert method == "alipay.user.info.share" and request["auth_token"] == "fake-alipay-access-token"
            data.update(code="10000", nick_name=profile["nickname"])
            if profile["failure"] == "missing_profile_status":
                data.pop("code")
            if profile["failure"] == "profile_mismatch":
                data[config.ALIPAY_SUBJECT_FIELD] = "different-person"
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = base64.b64encode(pkcs1_15.new(server).sign(SHA256.new(raw)))
        if token_request and profile["failure"] == "tamper":
            raw = raw.replace(b"member-A", b"attacker")
        key = method.replace(".", "_") + "_response"
        if profile["failure"] == "unsigned":
            return b'{"' + key.encode() + b'":' + raw + b"}"
        return b'{"' + key.encode() + b'":' + raw + b',"sign":"' + signature + b'"}'

    sdk = importlib.import_module("alipay.aop.api.DefaultAlipayClient")
    monkeypatch.setattr(sdk, "do_post", transport)
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(alipay_router, prefix="/api/auth")
    cli = TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))
    assert alipay.enabled()
    return cli, calls, profile, wxenv[1]


def begin(cli, action="login"):
    result = cli.post("/api/auth/alipay/start", json={"action": action})
    assert result.status_code == 200, result.text
    url = urlsplit(result.json()["authorize_url"])
    assert url.scheme == "https" and url.netloc == "openauth.alipay.com"
    query = parse_qs(url.query)
    assert query["scope"] == ["auth_user"]
    return query["state"][0]


def finish(cli, state, **extra):
    return cli.get(
        "/api/auth/alipay/callback?" + urlencode({"state": state, "auth_code": "fake-code", **extra}),
        follow_redirects=False,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_bare_callback_is_available_for_platform_url_check(alienv, monkeypatch, enabled):
    cli, calls, _, _ = alienv
    monkeypatch.setattr(config, "ALIPAY_LOGIN_ENABLED", enabled)
    result = cli.get("/api/auth/alipay/callback", follow_redirects=False)
    assert result.status_code == 200 and result.text == "ok"
    assert result.headers["cache-control"] == "no-store"
    assert "set-cookie" not in result.headers and not calls
    with session._get_conn() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    # 伪造或残缺的授权参数不能走探测响应。
    result = cli.get("/api/auth/alipay/callback?auth_code=fake-code", follow_redirects=False)
    assert result.status_code == 303 and "alipay_result=failed" in result.headers["location"]


def test_auto_register_no_email_no_password_and_repeat_login(alienv):
    cli, calls, _, _ = alienv
    response = finish(cli, begin(cli))
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()
    user = cli.get("/api/auth/me/").json()["user"]
    assert user["username"].startswith("ali_") and user["nickname"] == "支付宝小明"
    assert user["has_password"] is False
    assert not verify_password("123456", account.get_user(user["id"])["password_hash"])
    with session._get_conn() as db:
        assert (
            db.execute("SELECT subject FROM external_identities WHERE user_id=?", (user["id"],)).fetchone()[0]
            == "open_id:member-A"
        )
        assert db.execute("SELECT count(*) FROM security_emails WHERE user_id=?", (user["id"],)).fetchone()[0] == 0
    cli.cookies.clear()
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").json()["user"]["id"] == user["id"] and len(calls) == 4


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("tamper", "identity_invalid"),
        ("unsigned", "identity_invalid"),
        ("profile_mismatch", "identity_invalid"),
        ("missing_profile_status", "provider_failed"),
        ("missing_identity", "identity_invalid"),
        ("business", "provider_failed"),
        ("timeout", "provider_unavailable"),
    ],
)
def test_invalid_provider_results_cannot_create_accounts_and_state_cannot_replay(alienv, failure, reason, caplog):
    cli, calls, profile, _ = alienv
    profile["failure"] = failure
    state = begin(cli)
    with caplog.at_level(logging.DEBUG):
        response = finish(cli, state)
    assert "reason=" + reason in response.headers["location"]
    before = len(calls)
    assert "reason=flow_invalid" in finish(cli, state).headers["location"] and len(calls) == before
    with session._get_conn() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    assert "fake-alipay-access-token" not in caplog.text
    assert "raw transport detail" not in caplog.text
    assert "fake-alipay-access-token" not in response.text


@pytest.mark.parametrize("kind", ["browser", "expired", "duplicate", "wrong_app", "wechat_state"])
def test_invalid_flows_do_not_call_alipay(alienv, kind):
    cli, calls, _, wxcli = alienv
    state = begin(cli)
    extra = {}
    if kind == "browser":
        cli.cookies.clear()
    elif kind == "expired":
        with session._get_conn() as db:
            db.execute("UPDATE external_auth_flows SET expires=0")
    elif kind == "wrong_app":
        extra["app_id"] = "wrong-app"
    elif kind == "wechat_state":
        state = wx_begin(wxcli)
    elif kind == "duplicate":
        result = cli.get(
            f"/api/auth/alipay/callback?state={state}&auth_code=fake-code&auth_code=other", follow_redirects=False
        )
        assert "reason=flow_invalid" in result.headers["location"] and calls == []
        return
    assert "reason=flow_invalid" in finish(cli, state, **extra).headers["location"] and calls == []


@pytest.mark.parametrize("provider", ["alipay", "wechat"])
@pytest.mark.parametrize("action,page", [("login", "/login"), ("bind", "/settings")])
def test_cancelled_authorization_ends_flow_at_original_page(alienv, wxenv, provider, action, page):
    if provider == "alipay":
        cli, calls, _, _ = alienv
        start, code_param = begin, "auth_code"
    else:
        _, cli, _, _, calls, _ = wxenv
        start, code_param = wx_begin, "code"
    if action == "bind":
        sign_in(cli)
    state = start(cli, action)
    path = f"/api/auth/{provider}/callback"
    result = cli.get(path, params={"state": state}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == f"{page}?{provider}_result=failed&reason=provider_failed"
    assert "set-cookie" not in result.headers
    repeat = cli.get(path, params={"state": state, code_param: "fake-code"}, follow_redirects=False)
    assert "reason=flow_invalid" in repeat.headers["location"] and not calls
    with session._get_conn() as db:
        assert db.execute("SELECT count(*) FROM external_identities").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == int(action == "bind")


def test_session_uses_version_confirmed_in_account_transaction(alienv, monkeypatch):
    cli, _, _, _ = alienv
    complete = external_accounts.complete

    def complete_then_revoke(identity, flow):
        user = complete(identity, flow)
        with session._get_conn() as db:
            db.execute("UPDATE users SET token_valid_after=token_valid_after+1 WHERE id=?", (user["id"],))
        return user

    monkeypatch.setattr(external_accounts, "complete", complete_then_revoke)
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").status_code == 401
    monkeypatch.setattr(external_accounts, "complete", complete)
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").status_code == 200


def test_binding_replacement_conflict_unlink_and_revocation(alienv):
    cli, _, profile, _ = alienv
    user = sign_in(cli)
    assert "alipay_result=bound" in finish(cli, begin(cli, "bind")).headers["location"]
    profile["subject"] = "member-B"
    assert "reason=already_bound" in finish(cli, begin(cli, "bind")).headers["location"]
    profile["subject"] = "member-A"
    old_token = cli.cookies.get("baomi_session")
    assert "alipay_result=unlinked" in finish(cli, begin(cli, "unlink")).headers["location"]
    assert cli.get("/api/auth/me/", headers={"X-Auth-Token": old_token}).status_code == 401
    profile["subject"] = "member-B"
    assert "alipay_result=bound" in finish(cli, begin(cli, "bind")).headers["location"]
    cli.cookies.clear()
    sign_in(cli, "another_user")
    assert "reason=already_bound" in finish(cli, begin(cli, "bind")).headers["location"]
    assert account.get_user(user["id"])


def test_passwordless_user_must_set_password_before_unlink(alienv):
    cli = alienv[0]
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    assert "reason=last_login_method" in finish(cli, begin(cli, "unlink")).headers["location"]
    with session._get_conn() as db:
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password("New_secret_9"), uid))
    assert "alipay_result=unlinked" in finish(cli, begin(cli, "unlink")).headers["location"]


def test_disabled_account_and_register_switch_are_enforced(alienv, monkeypatch):
    cli = alienv[0]
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    cli.cookies.clear()
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    assert finish(cli, begin(cli)).headers["location"] == "/"
    cli.cookies.clear()
    alienv[2]["subject"] = "new-person"
    assert "reason=signup_closed" in finish(cli, begin(cli)).headers["location"]
    alienv[2]["subject"] = "member-A"
    account.set_status(uid, 0)
    assert "reason=account_unavailable" in finish(cli, begin(cli)).headers["location"]


def test_identity_field_is_explicit_and_missing_nickname_uses_default(alienv, monkeypatch):
    cli, _, profile, _ = alienv
    monkeypatch.setattr(config, "ALIPAY_SUBJECT_FIELD", "user_id")
    profile["nickname"] = None
    finish(cli, begin(cli))
    user = cli.get("/api/auth/me/").json()["user"]
    assert user["nickname"] == "支付宝用户"
    with session._get_conn() as db:
        assert (
            db.execute("SELECT subject FROM external_identities WHERE user_id=?", (user["id"],)).fetchone()[0]
            == "user_id:uid-member-A"
        )


def test_bad_config_and_frontend_identity_submission_are_rejected(alienv, monkeypatch):
    cli = alienv[0]
    assert cli.post("/api/auth/alipay/start", json={"subject": "forged"}).status_code == 422
    assert cli.post("/api/auth/alipay/start", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert cli.post("/api/auth/alipay/start", json={"action": "bind"}).status_code == 401
    monkeypatch.setattr(config, "ALIPAY_PUBLIC_KEY_FILE", "missing-public-key.txt")
    assert cli.get("/api/auth/config/").json()["alipay_enabled"] is False
    assert cli.post("/api/auth/alipay/start", json={}).status_code == 503


def test_switching_account_during_binding_rejected_before_sdk_call(alienv):
    cli, calls, _, _ = alienv
    sign_in(cli, "original_user")
    state = begin(cli, "bind")
    cli.cookies.delete("baomi_session")
    sign_in(cli, "switched_user")
    assert "reason=flow_invalid" in finish(cli, state).headers["location"]
    assert calls == []


def test_signup_quota_and_start_rate_limit_prevent_new_sessions(alienv, monkeypatch):
    cli, _, profile, _ = alienv
    monkeypatch.setattr(config, "SIGNUP_GLOBAL_PER_DAY", 1)
    assert finish(cli, begin(cli)).headers["location"] == "/"
    cli.cookies.clear()
    profile["subject"] = "new-person"
    assert "reason=rate_limited" in finish(cli, begin(cli)).headers["location"]
    assert cli.get("/api/auth/me/").status_code == 401
    monkeypatch.setattr(config, "LOGIN_IP_PER_MINUTE", 1)
    assert cli.post("/api/auth/alipay/start", json={}).status_code == 429


def test_full_account_purge_removes_alipay_binding_and_next_login_registers_new_user(alienv, tmp_path, monkeypatch):
    from core import account_deletion

    cli = alienv[0]
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    old_token = cli.cookies.get("baomi_session")
    monkeypatch.setattr(config, "PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    registry_dir = tmp_path / "project" / "data"
    registry_dir.mkdir(parents=True)
    for name in ("owners", "names", "files", "public"):
        (registry_dir / f"kb_{name}.json").write_text("{}", encoding="utf-8")
    assert account_deletion.purge(uid, confirm_uid=uid, writers_stopped=True)["state"] == "done"
    assert cli.get("/api/auth/me/", headers={"X-Auth-Token": old_token}).status_code == 401
    cli.cookies.clear()
    assert finish(cli, begin(cli)).headers["location"] == "/"
    assert cli.get("/api/auth/me/").json()["user"]["id"] != uid


def begin_app(cli, action="login"):
    """App 内唤起：请求 state 自带 browser+uid，回调不依赖本站 cookie。"""
    result = cli.post("/api/auth/alipay/start", json={"action": action, "app_flow": True})
    assert result.status_code == 200, result.text
    state = parse_qs(urlsplit(result.json()["authorize_url"]).query)["state"][0]
    assert len(state) == 118
    return state


def test_app_flow_callback_without_cookie_completes_login(alienv):
    """支付宝 App 内回调没有本站 cookie，凭自包含 state 仍应完成登录。"""
    cli, calls, _, _ = alienv
    state = begin_app(cli)
    cli.cookies.clear()  # 模拟跳到支付宝 App 后丢失本站 cookie
    result = finish(cli, state)
    # 结果不再种进支付宝 WebView，而是暂存供原应用轮询领走。
    assert result.status_code == 200 and "请返回应用" in result.text
    assert len(calls) == 2


def test_app_flow_bind_without_cookie_and_account_switch_rejected(alienv):
    """App 绑定：无 cookie 可完成；但若回调方带另一账号登录态，必须拒绝。"""
    cli, calls, _, _ = alienv
    sign_in(cli, "owner")
    state = begin_app(cli, "bind")
    cli.cookies.clear()
    # 无登录态：凭 state 自带 uid 完成绑定
    assert finish(cli, state).status_code == 200
    assert len(calls) == 2

    # 换号后回调：带 B 的登录态点 A 发起的 state，必须拒绝
    sign_in(cli, "attacker")
    state2 = begin_app(cli, "bind")
    cli.cookies.delete("baomi_session")
    sign_in(cli, "owner2")
    result = finish(cli, state2)
    assert "reason=flow_invalid" in result.headers["location"]
    assert len(calls) == 2


def _poll(cli, state):
    return cli.get("/api/auth/alipay/poll", params={"state": state}, follow_redirects=False)


def test_app_flow_poll_delivers_session_to_origin_browser(alienv):
    """核心场景：支付宝里授权成功后，原浏览器轮询领回登录态并完成登录。"""
    cli, calls, _, _ = alienv
    state = begin_app(cli)  # 原浏览器种下 browser nonce
    origin_cookies = dict(cli.cookies)

    # 支付宝 WebView 里完成授权（无本站 cookie）
    cli.cookies.clear()
    assert finish(cli, state).status_code == 200

    # 切回原浏览器：只带 browser nonce，没有会话 cookie
    cli.cookies.clear()
    for k, v in origin_cookies.items():
        cli.cookies.set(k, v)
    assert cli.get("/api/auth/me/", follow_redirects=False).status_code == 401

    result = _poll(cli, state)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ok"
    assert result.json()["action"] == "login"
    # 会话已就地种上，原浏览器进入登录态
    assert cli.get("/api/auth/me/", follow_redirects=False).status_code == 200

    # 一次性：再次轮询拿不到，不能重复兑换
    assert _poll(cli, state).json()["status"] == "pending"


def test_app_flow_poll_rejects_foreign_browser(alienv):
    """别的浏览器拿到 state 也不能领走登录态（必须持有发起时的 browser nonce）。"""
    cli, calls, _, _ = alienv
    state = begin_app(cli)
    cli.cookies.clear()
    assert finish(cli, state).status_code == 200

    # 伪造一个 nonce：相当于另一个浏览器拿着偷来的 state 来领
    cli.cookies.set("zhisui_alipay_browser", "x" * 43)
    rejected = _poll(cli, state)
    assert rejected.status_code == 403
    # 结果仍留在服务端，直到真正的发起方来领
    cli.cookies.clear()
    assert _poll(cli, state).status_code == 403

