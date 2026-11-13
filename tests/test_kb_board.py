"""公开知识库留言板（论坛式留言）。

这里锁的是四件最容易静默坏掉的事：

  1. **只有公开库能留言**：私有库打过来必须 404（不是 403 —— 403 等于承认
     "这个 kb_id 是真的"）。库主收回公开后，已有的留言也要立刻跟着 404。
  2. **权限三层**：作者能删自己的、库主能删自己库里的、别人不行。
     少判一层就是"谁都能删别人的留言"。
  3. **只有文字**：空正文、超长都要在写库之前被拒，不能让前端 maxLength
     成为唯一的闸门（接口是可以直接调的）。
  4. **限频**：同一个人在同一库连发，第二条要被挡住 —— 否则一个人刷屏。

不启真 chroma：知识库的归属/公开状态用 monkeypatch 顶掉，
这份测试只关心留言本身的读写与权限。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.kb as kb_api
from core import account, config, kb_board, quota, session
from core.auth import create_token

KB = "kbboardtest01"
KB_OWNER = "kbowner"
PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 干净进程内状态（不碰真实 data/sessions.db）。"""
    path = str(tmp_path / "board.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._ensure_rate_table()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["devadmin"])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    # 建表是"按库路径记已初始化"的：换库必须清掉，否则临时库里没有 kb_posts
    kb_board._READY.clear()
    # 默认关掉冷却，让"连发两条"的用例只测它想测的东西
    monkeypatch.setattr(kb_board, "COOLDOWN", 0)
    yield path
    kb_board._READY.clear()


@pytest.fixture()
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def kb(monkeypatch):
    """把"这个库存在且已公开"这件事顶掉，不启 chroma。

    返回可变 state，用例可以中途把 public 改成 False 验"收回公开后留言不可见"。
    """
    state = {"public": True, "official": False, "owner": KB_OWNER}
    monkeypatch.setattr(
        kb_api.pipelines, "owner_of", lambda k: state["owner"] if k == KB else None
    )
    monkeypatch.setattr(
        kb_api.pipelines, "is_public", lambda k: bool(state["public"]) and k == KB
    )
    monkeypatch.setattr(
        kb_api.pipelines, "is_official", lambda k: bool(state["official"]) and k == KB
    )
    monkeypatch.setattr(kb_api.pipelines, "name_of", lambda k: "测试课程库")
    monkeypatch.setattr(kb_api.pipelines, "public_meta", lambda k: {"desc": "测试简介"})
    return state


def _as(client: TestClient, username: str) -> str:
    """造用户 + 直接注入 Cookie（不走注册接口，避免依赖 ALLOW_SIGNUP 开关）。"""
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def _post(client: TestClient, body: str) -> dict:
    return client.post(f"/api/kb/public/{KB}/board", json={"body": body})


def test_private_kb_board_is_404(client, env, kb):
    """私有库：读和写都必须 404，与"库不存在"长得一模一样。"""
    _as(client, "alice")
    kb["public"] = False
    assert client.get(f"/api/kb/public/{KB}/board").status_code == 404
    assert _post(client, "有人吗").status_code == 404


def test_unknown_kb_board_is_404(client, env, kb):
    _as(client, "alice")
    assert client.get("/api/kb/public/nosuchkb/board").status_code == 404


def test_post_then_list(client, env, kb):
    alice = _as(client, "alice")
    r = _post(client, "第三章的例题 2 是不是印错了？")
    assert r.status_code == 200
    post = r.json()["post"]
    assert post["body"].startswith("第三章")
    assert post["author"]["name"] == "alice"
    assert post["mine"] is True
    assert post["can_delete"] is True
    assert post["id"] > 0

    data = client.get(f"/api/kb/public/{KB}/board").json()
    assert data["total"] == 1
    assert data["name"] == "测试课程库"
    assert len(data["posts"]) == 1
    assert data["posts"][0]["id"] == post["id"]
    # 表情候选由后端下发，前端不硬编码 —— 空列表说明这条契约破了
    assert data["stickers"]
    assert alice  # 只为显式用一次，避免 lint 抱怨未使用


def test_empty_and_too_long_are_rejected(client, env, kb):
    _as(client, "alice")
    assert _post(client, "   ").status_code == 400
    assert _post(client, "长" * (kb_board.MAX_LEN + 1)).status_code == 400
    # 边界值本身必须能发（否则 MAX_LEN 就该减一）
    assert _post(client, "长" * kb_board.MAX_LEN).status_code == 200


def test_author_can_delete_own_post(client, env, kb):
    _as(client, "alice")
    pid = _post(client, "我先写一条").json()["post"]["id"]
    assert client.delete(f"/api/kb/public/{KB}/board/{pid}").status_code == 200
    data = client.get(f"/api/kb/public/{KB}/board").json()
    assert data["total"] == 0
    assert data["posts"] == []
    # 再删一次：已经软删了，应该 404 而不是 200（重复删不该被当成成功）
    assert client.delete(f"/api/kb/public/{KB}/board/{pid}").status_code == 404


def test_others_cannot_delete(client, env, kb):
    _as(client, "alice")
    pid = _post(client, "alice 的留言").json()["post"]["id"]

    _as(client, "bob")
    assert client.delete(f"/api/kb/public/{KB}/board/{pid}").status_code == 403
    assert client.get(f"/api/kb/public/{KB}/board").json()["total"] == 1


def test_kb_owner_can_delete_others(client, env, kb):
    """库主删自己库里的留言：这是"维护自己的板子"的能力，不是越权。"""
    _as(client, "alice")
    pid = _post(client, "跑题了").json()["post"]["id"]

    # 库主的 uid 是随机 id，不是用户名 —— 上一版直接把用户名当 uid 比对，
    # 结果库主本人被自己挡在 403 外面（测试抓到的就是这个坑）。
    kb["owner"] = _as(client, KB_OWNER)
    assert client.delete(f"/api/kb/public/{KB}/board/{pid}").status_code == 200
    assert client.get(f"/api/kb/public/{KB}/board").json()["total"] == 0


def test_post_requires_login(client, env, kb):
    client.cookies.clear()
    assert client.get(f"/api/kb/public/{KB}/board").status_code == 401
    assert _post(client, "匿名留言").status_code == 401


def test_cooldown_blocks_second_post(client, env, kb, monkeypatch):
    """连发两条要被挡：默认 8 秒冷却，第一条之后立刻发第二条必须 429。"""
    monkeypatch.setattr(kb_board, "COOLDOWN", 8)
    _as(client, "alice")
    assert _post(client, "第一条").status_code == 200
    second = _post(client, "第二条")
    assert second.status_code == 429
    assert "秒后再试" in second.json()["detail"]


def test_purge_removes_all_posts_of_kb(client, env, kb):
    """删库时留言一起物理删：库都没了，留一堆没人能看见的行只是垃圾。"""
    _as(client, "alice")
    _post(client, "一条")
    _post(client, "两条")
    assert kb_board.count_posts(KB) == 2
    assert kb_board.purge_kb(KB) == 2
    assert kb_board.count_posts(KB) == 0
