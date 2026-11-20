"""开发基线：真实 HTTP 账号流程及测试隔离，不调用外部服务。"""

import os
import socket
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def sandbox():
    raw = os.environ.get("ZHISUI_TEST_SANDBOX")
    assert raw, "请通过 tools/run_isolated_tests.py 运行，禁止直接访问用户数据"
    return Path(raw)


def test_paths_are_isolated_before_application_import(sandbox):
    from core import config, session

    for path in (
        config.DATA_DIR,
        config.PERSIST_DIR,
        config.DOWNLOAD_DIR,
        session._db_path(),
        config._AUTH_SECRET_FILE,
    ):
        assert Path(path).is_relative_to(sandbox)


def test_existing_database_and_env_are_blocked(sandbox):
    root = Path(__file__).resolve().parents[1]
    for name in ("data/sessions.db", ".env"):
        with pytest.raises(PermissionError, match="禁止访问"):
            (root / name).read_bytes()
    with pytest.raises(PermissionError, match="临时目录"):
        sqlite3.connect(str(root / "do-not-create.db"))


def test_external_network_is_blocked(sandbox):
    with socket.socket() as connection:
        with pytest.raises(PermissionError, match="回环网络"):
            connection.connect(("192.0.2.1", 443))


def test_existing_register_login_logout_and_password_change(sandbox):
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/me/").status_code == 401
    credentials = {"username": "hzl_baseline", "password": "Baseline_pass_9"}
    from registration_support import register_verified
    response = register_verified(client, credentials["username"], credentials["password"])
    assert response.status_code == 200, response.text
    assert "password_hash" not in response.json()
    assert client.get("/api/auth/me/").status_code == 200
    assert client.post("/api/auth/logout/").status_code == 200
    assert client.get("/api/auth/me/").status_code == 401
    assert client.post("/api/auth/login/", json=credentials).status_code == 200
    old_cookie = client.cookies.get("baomi_session")
    response = client.post(
        "/api/auth/password",
        json={"old_password": credentials["password"], "new_password": "Changed_pass_8"},
    )
    assert response.status_code == 200, response.text
    other = TestClient(main.app)
    other.cookies.set("baomi_session", old_cookie)
    assert other.get("/api/auth/me/").status_code == 401
    client.post("/api/auth/logout/")
    assert client.post("/api/auth/login/", json=credentials).status_code == 401
    credentials["password"] = "Changed_pass_8"
    assert client.post("/api/auth/login/", json=credentials).status_code == 200
