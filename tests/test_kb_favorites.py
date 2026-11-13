"""公开库收藏。

钉住三件事：
  1. 幂等：重复收藏不涨数、重复取消不报错（网络重试很常见）；
  2. 我的收藏带**来源与作者**（"官方知识库" / "某某 的库"）——
     用户明确要求这一条，它是收藏列表能被看懂的前提；
  3. 库没了，收藏跟着没了：否则「我的收藏」里会出现点开就 404 的卡片。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.kb as kb_api
from core import config, kb_favs, session
from core.auth import create_token

KB = "kbfavtest01"
KB_OWNER = "kbowner"
PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "fav.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    monkeypatch.setattr(config, "ADMIN_UIDS", ())
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ())
    kb_favs._READY.clear()
    yield path
    kb_favs._READY.clear()


@pytest.fixture()
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def kb(monkeypatch):
    """两个公开库：一个官方、一个同学分享。只顶掉 pipelines，不启 chroma。"""
    state = {"official": True}
    monkeypatch.setattr(
        kb_api.pipelines, "owner_of", lambda k: "official_owner" if k == KB else None
    )
    monkeypatch.setattr(kb_api.pipelines, "is_public", lambda k: k == KB)
    monkeypatch.setattr(kb_api.pipelines, "public_meta", lambda k: {"at": 1763000000, "access": "preview"})
    monkeypatch.setattr(kb_api.pipelines, "is_official", lambda k: state["official"] and k == KB)
    monkeypatch.setattr(
        kb_api.pipelines,
        "list_public",
        lambda: [
            {
                "kb_id": KB,
                "name": "高等数学",
                "chunks": 120,
                "files": 8,
                "owner": "official_owner",
                "desc": "各章单元测试",
                "at": 1763000000,
                "official": state["official"],
                "access": "preview",
            }
        ],
    )
    monkeypatch.setattr(kb_api.pipelines, "list_files_view", lambda *a, **k: [])
    return state


def _as(client: TestClient, username: str) -> str:
    from core import account

    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def test_favorite_is_idempotent(client, env, kb):
    _as(client, "alice")
    r1 = client.put(f"/api/kb/public/{KB}/favorite")
    assert r1.status_code == 200
    assert r1.json()["favs"] == 1 and r1.json()["added"] is True

    r2 = client.put(f"/api/kb/public/{KB}/favorite")
    assert r2.json()["added"] is False
    assert r2.json()["favs"] == 1  # 没有涨成 2

    d1 = client.delete(f"/api/kb/public/{KB}/favorite")
    assert d1.json()["removed"] is True and d1.json()["favs"] == 0
    d2 = client.delete(f"/api/kb/public/{KB}/favorite")
    assert d2.status_code == 200 and d2.json()["removed"] is False


def test_favorites_list_carries_source_and_author(client, env, kb):
    _as(client, "alice")
    client.put(f"/api/kb/public/{KB}/favorite")

    out = client.get("/api/kb/favorites").json()["kbs"]
    assert len(out) == 1
    item = out[0]
    # 官方库：来源与署名都是"官方知识库"，不能出现"某位同学"
    assert item["official"] is True
    assert item["source"] == "官方知识库"
    assert item["owner_name"] == "官方"
    # 上传/分享日期跟着一起来（用户要求每个库都标）
    assert item["at"] == 1763000000
    assert item["favorited"] is True


def test_favorites_list_marks_user_source(client, env, kb, monkeypatch):
    """同学分享的库：来源写成"某某 的库"，作者是那个人。"""
    monkeypatch.setattr(
        kb_api.pipelines,
        "list_public",
        lambda: [
            {
                "kb_id": KB,
                "name": "考研数学",
                "chunks": 40,
                "files": 3,
                "owner": "u_bob",
                "desc": "",
                "at": 1763000000,
                "official": False,
                "access": "download",
            }
        ],
    )
    _as(client, "alice")
    client.put(f"/api/kb/public/{KB}/favorite")
    item = client.get("/api/kb/favorites").json()["kbs"][0]
    assert item["official"] is False
    assert item["source"].endswith(" 的库")
    assert item["access"] == "download"


def test_favorites_requires_login(client, env, kb):
    client.cookies.clear()
    assert client.get("/api/kb/favorites").status_code == 401
    assert client.put(f"/api/kb/public/{KB}/favorite").status_code == 401


def test_favorite_unknown_kb_is_404(client, env, kb):
    _as(client, "alice")
    assert client.put("/api/kb/public/nosuchkb/favorite").status_code == 404


def test_purge_clears_favorites(env):
    """删库时收藏一起删：留着就是一张点开 404 的卡片。"""
    kb_favs.add("kb_x", "u1")
    kb_favs.add("kb_x", "u2")
    assert kb_favs.purge_kb("kb_x") == 2
    assert kb_favs.count("kb_x") == 0
