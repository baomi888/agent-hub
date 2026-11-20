"""邮箱验证码登录：匿名发码防枚举、证明跨用途隔离、换绑后旧码作废。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_email_registration import env as env

import main
from api.security_email import get_service
from core import account


@pytest.fixture
def login_env(env):
    s, cli, messages, clock = env
    user = account.create_user("loginer", "Original_pass_9")
    with sqlite3.connect(s.store.path) as db:
        db.execute("INSERT INTO security_emails VALUES (?,?,?,1)", (user["id"], "loginer@example.com", clock[0]))
    main.app.dependency_overrides[get_service] = lambda: s
    yield s, cli, messages, clock, user["id"]
    main.app.dependency_overrides.pop(get_service, None)


def login_proof(cli, messages, address="loginer@example.com"):
    """发码 → 取码 → 换会话；返回登录响应。"""
    response = cli.post("/api/auth/login/email/send", json={"address": address})
    assert response.status_code == 200, response.text
    code = messages[-1][1]
    return cli.post("/api/auth/login/email", json={"request_id": response.json()["request_id"], "code": code})


def test_login_by_email_happy_path(login_env):
    s, cli, messages, clock, uid = login_env
    result = login_proof(cli, messages)
    assert result.status_code == 200, result.text
    assert result.json()["username"] == "loginer"
    # 会话真种上了：/me 通，且最近登录时间被记账
    assert cli.get("/api/auth/me/").status_code == 200
    with sqlite3.connect(s.store.path) as db:
        last = db.execute("SELECT last_login_at FROM users WHERE id=?", (uid,)).fetchone()[0]
    assert last == clock[0]


def test_unknown_email_send_shape_is_identical_and_sends_nothing(login_env):
    s, cli, messages, _, _ = login_env
    known = cli.post("/api/auth/login/email/send", json={"address": "loginer@example.com"})
    before = len(messages)
    unknown = cli.post("/api/auth/login/email/send", json={"address": "stranger@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json().keys() == unknown.json().keys()
    assert known.json()["message"] == unknown.json()["message"]
    assert len(messages) == before  # 未绑定邮箱：一封都不发，但响应看起来一样
    stranger_login = cli.post(
        "/api/auth/login/email", json={"request_id": unknown.json()["request_id"], "code": "000000"}
    )
    assert stranger_login.status_code == 400


def test_reset_code_cannot_login_and_login_code_cannot_reset(login_env):
    """找回与登录的证明互不通用：purpose 比对拦下跨用途重放。"""
    s, cli, messages, clock, _ = login_env
    # 找回流程发码拿到的 request_id/code，拿去走登录端点
    reset_send = cli.post("/api/auth/password/reset/email/send", json={"address": "loginer@example.com"})
    reset_code = messages[-1][1]
    cross = cli.post(
        "/api/auth/login/email", json={"request_id": reset_send.json()["request_id"], "code": reset_code}
    )
    assert cross.status_code == 400
    # 反向：登录验证码拿去走找回 verify。冷却按地址共享（跨用途也一样），
    # 推进时钟绕开 60 秒重发间隔。
    clock[0] += 61
    login_send = cli.post("/api/auth/login/email/send", json={"address": "loginer@example.com"})
    login_code = messages[-1][1]
    assert (
        cli.post(
            "/api/auth/password/reset/email/verify",
            json={"request_id": login_send.json()["request_id"], "code": login_code},
        ).status_code
        == 400
    )


def test_wrong_code_locks_after_five_attempts(login_env):
    _, cli, messages, _, _ = login_env
    send = cli.post("/api/auth/login/email/send", json={"address": "loginer@example.com"})
    request_id = send.json()["request_id"]
    for _ in range(5):
        assert cli.post("/api/auth/login/email", json={"request_id": request_id, "code": "000000"}).status_code == 400
    retry = cli.post("/api/auth/login/email", json={"request_id": request_id, "code": messages[-1][1]})
    assert retry.status_code == 400


def test_disabled_account_gets_same_silent_shape(login_env):
    s, cli, messages, clock, uid = login_env
    with sqlite3.connect(s.store.path) as db:
        db.execute("UPDATE users SET status=0 WHERE id=?", (uid,))
    before = len(messages)
    send = cli.post("/api/auth/login/email/send", json={"address": "loginer@example.com"})
    assert send.status_code == 200
    assert send.json()["message"] == "如果该邮箱已绑定可用账号，验证码将发送到该邮箱，请检查收件箱或稍后重试。"
    assert len(messages) == before
    assert (
        cli.post("/api/auth/login/email", json={"request_id": send.json()["request_id"], "code": "123456"}).status_code
        == 400
    )


def test_rebind_during_flow_invalidates_pending_code(login_env):
    """发码后、验证前邮箱被换绑：旧码在消费瞬间因 binding_version 变化被拒。"""
    s, cli, messages, _, uid = login_env
    send = cli.post("/api/auth/login/email/send", json={"address": "loginer@example.com"})
    request_id, code = send.json()["request_id"], messages[-1][1]
    with sqlite3.connect(s.store.path) as db:
        db.execute(
            "UPDATE security_emails SET address=?, binding_version=binding_version+1 WHERE user_id=?",
            ("moved@example.com", uid),
        )
    result = cli.post("/api/auth/login/email", json={"request_id": request_id, "code": code})
    assert result.status_code == 400


def test_email_login_does_not_kill_other_sessions(login_env):
    """验证码登录是"一次正常登录"：不吊销同一账号的其他会话（踢人只在改密时）。"""
    _, cli, messages, _, _ = login_env
    other = TestClient(main.app)
    pwd = other.post("/api/auth/login/", json={"username": "loginer", "password": "Original_pass_9"})
    assert pwd.status_code == 200
    assert login_proof(cli, messages).status_code == 200
    assert other.get("/api/auth/me/").status_code == 200
