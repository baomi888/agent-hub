"""论坛配图的上传与发帖接口（2026-11-14）。

锁的是"接口跑得通但数据不对"的那一层：
  1. 上传返回的 hash 能直接放进发帖的 `images`，详情里读得回来
  2. 图是**公开可读**的（`<img src>` 不带 cookie 也要能拿到）
  3. 不存在的 hash 发帖时被拒 —— 否则帖子上会挂一排破图，看起来像站坏了
  4. 超过 9 张被拒
  5. 路径穿越的 hash 进不了文件系统
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from core import account, config, kb_board, quota, session


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "forum_img.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    # ★ 图片目录也指到临时目录，否则测试会往真实 data/forum_img 里写文件
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    session._init_db()
    account._ensure_tables()
    quota._ensure_tables()
    quota._ensure_rate_table()
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    kb_board._READY.clear()
    monkeypatch.setattr(kb_board, "COOLDOWN", 0)
    yield path
    kb_board._READY.clear()


@pytest.fixture()
def app():
    import main

    return main.app


def _webp_bytes(size=(1200, 900)) -> bytes:
    im = Image.new("RGB", size, (90, 140, 200))
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=80)
    return buf.getvalue()


def _upload(c: TestClient, data: bytes | None = None) -> dict:
    payload = data if data is not None else _webp_bytes()
    r = c.post(
        "/api/forum/upload",
        files={"file": ("a.webp", payload, "image/webp")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _topic(c: TestClient, title: str, images: list[str] | None = None) -> dict:
    r = c.post(
        "/api/forum/topics",
        json={
            "title": title,
            "body": "正文一句",
            "cat": "ask",
            **({"images": images} if images is not None else {}),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["topic"]


# ==================== ① 上传 → 发帖 → 读回 ====================


def test_upload_returns_hash_and_url(env, app):
    with TestClient(app) as c:
        up = _upload(c)
    assert len(up["hash"]) == 16
    assert up["url"] == f"/api/forum/img/{up['hash']}"
    assert up["w"] == 1200 and up["h"] == 900
    assert up["rescued"] is False, "干净的 WebP 应当走快路径"


def test_uploaded_image_is_publicly_readable(env, app):
    """`<img src>` 不带任何 cookie 就要能拿到 —— 强制鉴权会让浏览器缓存失效，
    而且分享出去的帖子在别人那里全是破图。"""
    with TestClient(app) as c:
        up = _upload(c)
    with TestClient(app) as fresh:  # 全新客户端，没有 cookie
        r = fresh.get(up["url"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/webp")
    assert "immutable" in r.headers.get("cache-control", "")


def test_topic_keeps_images(env, app):
    with TestClient(app) as c:
        up = _upload(c)
        t = _topic(c, "带图的帖子", [up["hash"]])
        assert t["images"] == [up["hash"]]

        d = c.get(f"/api/forum/topics/{t['id']}").json()
        assert d["topic"]["images"] == [up["hash"]]


def test_topic_without_images_has_empty_array(env, app):
    """没图时给空数组而不是缺字段：前端不用判键存不存在。"""
    with TestClient(app) as c:
        t = _topic(c, "纯文字")
    assert t["images"] == []


def test_topic_can_have_empty_body(env, app):
    """只有标题、没有正文也能发。

    面板上写的是「正文 选填」。后端如果强制非空，用户点发布只会看到一句
    「正文不能为空」而不知道自己哪里错了 —— 而"只想发张图"和"标题已经说完"
    都是正常用法。这条是端到端测试抓出来的：单测里每条都传了正文，所以一直是绿的。
    """
    with TestClient(app) as c:
        r = c.post(
            "/api/forum/topics",
            json={"title": "周三晚缺两人", "body": "", "cat": "ask"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["topic"]["title"] == "周三晚缺两人"


def test_topic_with_only_an_image_is_allowed(env, app):
    """标题 + 一张图、一个字正文都不写 —— 拍板书最典型的发法。"""
    with TestClient(app) as c:
        up = _upload(c)
        r = c.post(
            "/api/forum/topics",
            json={
                "title": "今天的板书",
                "body": "",
                "cat": "exp",
                "images": [up["hash"]],
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["topic"]["images"] == [up["hash"]]


def test_images_survive_list_endpoint(env, app):
    """列表也要带 images，否则卡片上没有封面（详情有、列表没有会很怪）。"""
    with TestClient(app) as c:
        up = _upload(c)
        _topic(c, "列表封面", [up["hash"]])
        rows = c.get("/api/forum/topics").json()["posts"]
    hit = [p for p in rows if p["title"] == "列表封面"]
    assert hit and hit[0]["images"] == [up["hash"]]


# ==================== ② 拒绝 ====================


def test_more_than_nine_images_is_rejected(env, app):
    with TestClient(app) as c:
        # 必须真的传 10 张不同的图：内容相同会被去重成同一个 hash，
        # 那样构造出来的还是"1 张图"，测不到上限
        hashes = [_upload(c, _webp_bytes((100 + i, 100)))["hash"] for i in range(10)]
        assert len(set(hashes)) == 10
        r = c.post(
            "/api/forum/topics",
            json={
                "title": "十张图",
                "body": "正文",
                "cat": "ask",
                "images": hashes,
            },
        )
    assert r.status_code == 400
    assert "9" in r.json()["detail"]


def test_unknown_hash_is_rejected(env, app):
    """不存在的 hash 要当场拒。放过的话帖子会挂一排破图 —— 破图比没图更糟，
    它看起来像站坏了。"""
    with TestClient(app) as c:
        r = c.post(
            "/api/forum/topics",
            json={
                "title": "破图",
                "body": "正文",
                "cat": "ask",
                "images": ["0123456789abcdef"],
            },
        )
    assert r.status_code == 404


def test_malformed_hash_is_rejected(env, app):
    with TestClient(app) as c:
        r = c.post(
            "/api/forum/topics",
            json={
                "title": "坏参数",
                "body": "正文",
                "cat": "ask",
                "images": ["../../etc/passwd"],
            },
        )
    assert r.status_code == 400


def test_duplicate_hashes_are_collapsed(env, app):
    """同一张图选两次只留一个 —— 前端也会拦，但接口不能指望前端。"""
    with TestClient(app) as c:
        up = _upload(c)
        t = _topic(c, "重复图", [up["hash"], up["hash"]])
    assert t["images"] == [up["hash"]]


def test_traversal_hash_returns_404(env, app):
    with TestClient(app) as c:
        for bad in ["../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "notahash"]:
            r = c.get(f"/api/forum/img/{bad}")
            assert r.status_code == 404, f"{bad} 不该被读到"


# ==================== ③ 匿名也能传 ====================


def test_anonymous_can_upload(env, app):
    """免登录的产品，上传首图不该要求注册。"""
    with TestClient(app) as c:
        r = c.post(
            "/api/forum/upload",
            files={"file": ("a.webp", _webp_bytes((300, 300)), "image/webp")},
        )
    assert r.status_code == 200

    # 顺带确认身份是设备 cookie
    assert "baomi_anon" in r.headers.get("set-cookie", "")
