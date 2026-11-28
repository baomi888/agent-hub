"""共享账号业务的平台隔离。测试身份只由测试代码构造，不开放模拟登录端点。"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_wechat_login import finish
from test_wechat_login import wxenv as wxenv

from core import account, config, session
from core import external_accounts as external
from core.auth import has_usable_password
from core.external_accounts import ExternalAuthError, ExternalIdentity


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "_db_path", lambda: str(tmp_path / "external.db"))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    monkeypatch.setattr(config, "AUTH_PBKDF2_ROUNDS", 1000)
    session._init_db()
    external.ensure_tables()


def flow(provider, *, app_id="same-app", uid="", action="login"):
    state = external.begin_flow("test-browser", app_id, action, uid, provider=provider)
    return external.consume_flow(state, "test-browser", app_id, uid, provider=provider)


def complete(provider, *, app_id="same-app", subject="same-subject", uid="", action="login", nickname=""):
    identity = ExternalIdentity(provider, app_id, subject, nickname)
    return external.complete(identity, flow(provider, app_id=app_id, uid=uid, action=action))


def test_same_external_id_on_two_platforms_does_not_merge_accounts(environment):
    wechat = complete("wechat")
    alipay = complete("alipay")
    assert wechat["id"] != alipay["id"]
    assert wechat["username"].startswith("wx_") and wechat["nickname"] == "微信用户"
    assert alipay["username"].startswith("ali_") and alipay["nickname"] == "支付宝用户"
    for provider, user in (("wechat", wechat), ("alipay", alipay)):
        assert complete(provider)["id"] == user["id"]
        assert not has_usable_password(user["password_hash"])
        assert external.identity_exists(ExternalIdentity(provider, "same-app", "same-subject"))
        assert external.is_bound(user["id"], "same-app", provider=provider)
    assert not external.is_bound(wechat["id"], "same-app", provider="alipay")
    assert not external.is_bound(alipay["id"], "same-app", provider="wechat")


@pytest.mark.parametrize("provider,other", [("wechat", "alipay"), ("alipay", "wechat")])
def test_wrong_callback_platform_cannot_consume_state(environment, provider, other):
    state = external.begin_flow("browser", "same-app", "login", provider=provider)
    with pytest.raises(ExternalAuthError) as exc:
        external.consume_flow(state, "browser", "same-app", "", provider=other)
    assert exc.value.code == "flow_invalid"
    saved = external.consume_flow(state, "browser", "same-app", "", provider=provider)
    assert saved["provider"] == provider
    with pytest.raises(ExternalAuthError):
        external.consume_flow(state, "browser", "same-app", "", provider=provider)


@pytest.mark.parametrize("kind", ["platform", "app", "missing_platform"])
def test_verified_identity_must_match_flow_platform_and_application(environment, kind):
    saved = flow("wechat")
    identity = ExternalIdentity(
        "alipay" if kind == "platform" else "wechat", "other-app" if kind == "app" else "same-app", "person"
    )
    if kind == "missing_platform":
        saved.pop("provider")
    with pytest.raises(ExternalAuthError) as exc:
        external.complete(identity, saved)
    assert exc.value.code == "identity_invalid"
    with session._get_conn() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM external_identities").fetchone()[0] == 0


def test_application_ids_remain_separate_within_same_platform(environment):
    one = complete("alipay", app_id="app-one", nickname="同名")
    two = complete("alipay", app_id="app-two", nickname="同名")
    assert one["id"] != two["id"]
    assert complete("alipay", app_id="app-one")["id"] == one["id"]
    assert not external.is_bound(one["id"], "app-two", provider="alipay")


def test_one_internal_user_can_bind_both_and_unlink_only_one(environment):
    uid = account.create_user("existing_user", "Strong_password_9")["id"]
    for provider in ("wechat", "alipay"):
        assert complete(provider, uid=uid, action="bind")["id"] == uid
        assert complete(provider)["id"] == uid
    version = account.get_token_valid_after(uid)
    complete("wechat", uid=uid, action="unlink")
    assert account.get_token_valid_after(uid) == version + 1
    assert not external.is_bound(uid, "same-app", provider="wechat")
    assert external.is_bound(uid, "same-app", provider="alipay")
    assert complete("alipay")["id"] == uid


@pytest.mark.parametrize("provider", ["wechat", "alipay"])
def test_replacement_and_conflict_rules_apply_to_both_platforms(environment, provider):
    uid = account.create_user("first_user", "First_password_9")["id"]
    other = account.create_user("second_user", "Second_password_9")["id"]
    complete(provider, uid=uid, action="bind", subject="original")
    complete(provider, uid=other, action="bind", subject="taken")
    for subject in ("taken", "new"):
        with pytest.raises(ExternalAuthError) as exc:
            complete(provider, uid=uid, action="bind", subject=subject)
        assert exc.value.code == "already_bound"
    complete(provider, uid=uid, action="unlink", subject="original")
    complete(provider, uid=uid, action="bind", subject="new")
    assert complete(provider, subject="new")["id"] == uid
    assert complete(provider, subject="taken")["id"] == other


@pytest.mark.parametrize("provider", ["wechat", "alipay"])
def test_disabled_account_rejected_after_platform_verification(environment, provider):
    user = complete(provider)
    saved = flow(provider)
    account.set_status(user["id"], 0)
    with pytest.raises(ExternalAuthError) as exc:
        external.complete(ExternalIdentity(provider, "same-app", "same-subject"), saved)
    assert exc.value.code == "account_unavailable"
    with session._get_conn() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1


@pytest.mark.parametrize("provider", ["wechat", "alipay"])
def test_no_password_cannot_unlink_even_when_both_platforms_are_bound(environment, provider):
    user = complete("wechat")
    complete("alipay", uid=user["id"], action="bind")
    with pytest.raises(ExternalAuthError) as exc:
        complete(provider, uid=user["id"], action="unlink")
    assert exc.value.code == "last_login_method"
    assert external.is_bound(user["id"], "same-app", provider=provider)


@pytest.mark.parametrize("provider", ["wechat", "alipay"])
def test_signup_switch_does_not_block_existing_platform_login(environment, monkeypatch, provider):
    user = complete(provider)
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    assert complete(provider)["id"] == user["id"]
    with pytest.raises(ExternalAuthError) as exc:
        complete(provider, subject="new-person")
    assert exc.value.code == "signup_closed"


def test_alipay_state_is_rejected_by_wechat_route_before_calling_provider(wxenv):
    _, cli, _, _, calls, _ = wxenv
    browser = "b" * 43
    cli.cookies.set("zhisui_wechat_browser", browser, domain="testserver.local", path="/api/auth/wechat")
    state = external.begin_flow(browser, config.WECHAT_APP_ID, "login", provider="alipay")
    assert "reason=flow_invalid" in finish(cli, state).headers["location"]
    assert calls == []
    assert external.consume_flow(state, browser, config.WECHAT_APP_ID, "", provider="alipay")["provider"] == "alipay"
    # 浏览器不能通过提交 provider 改变微信入口的归属。
    assert cli.post("/api/auth/wechat/start", json={"provider": "alipay"}).status_code == 422


def test_unknown_platform_is_not_accepted(environment):
    with pytest.raises(ExternalAuthError) as exc:
        external.begin_flow("browser", "same-app", "login", provider="unknown")
    assert exc.value.code == "provider_invalid"


def test_old_wechat_flow_migrates_once_and_keeps_existing_binding(environment):
    user = complete("wechat")
    state, browser = "old-state", "old-browser"
    with session._get_conn() as db:
        db.execute("DROP TABLE external_auth_flows")
        db.execute("""CREATE TABLE external_auth_flows(
            digest TEXT PRIMARY KEY,browser_digest TEXT NOT NULL,app_id TEXT NOT NULL,
            action TEXT NOT NULL,user_id TEXT NOT NULL,auth_version INTEGER NOT NULL,expires INTEGER NOT NULL)""")
        db.execute(
            "INSERT INTO external_auth_flows VALUES (?,?,?,'login','',0,?)",
            (
                hashlib.sha256(state.encode()).hexdigest(),
                hashlib.sha256(browser.encode()).hexdigest(),
                "same-app",
                int(time.time()) + 600,
            ),
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: external.ensure_tables(), range(2)))
    external.ensure_tables()
    with pytest.raises(ExternalAuthError):
        external.consume_flow(state, browser, "same-app", "", provider="alipay")
    saved = external.consume_flow(state, browser, "same-app", "", provider="wechat")
    assert saved["provider"] == "wechat"
    assert external.complete(ExternalIdentity("wechat", "same-app", "same-subject"), saved)["id"] == user["id"]
