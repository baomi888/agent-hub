# -*- coding: utf-8 -*-
"""用户画像标签体系（30 条定稿词表）回归测试。

覆盖四件事，每件都对应一个"不做会静默退化、不会报错"的风险：

  1. **词表本身**：30 条、名字唯一、七大类齐全、parent 指向存在的标签、
     type 都在合法集合内 —— 表写错了画像就歪了，而页面不会崩、只会推荐得莫名其妙。
  2. **同义词归一**："六级 / CET6 / cet6" 必须落成同一条"英语四六级"。
     不归一的话 `user_tags` 里是三条记录、权重各 1，画像被摊薄成"这人好像
     对英语有点兴趣但不多"。
  3. **手选标签的幂等与可取消**：反复保存不会把权重越堆越高；
     取消勾选只从 picks 里删，`user_tags` 那条保留（它可能同时是聊出来的证据）。
  4. **模板推荐的排序**：手选权重（3.0）必须压过聊天噪声（1.0）。

跑法：
  python -m pytest -q tests/test_tag_taxonomy.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config  # noqa: E402  先让 chdir / load_dotenv 跑完
import core.session as session  # noqa: E402
from core import tag_taxonomy as tx  # noqa: E402
from core.templates_catalog import (  # noqa: E402
    TAG_TO_TEMPLATES,
    get_catalog,
    recommend_for_tags,
)


def _fresh_db(monkeypatch, tag: str) -> None:
    """把会话库挪到独立临时库并重建表，避免污染真实 data/。"""
    tmp = tempfile.mkdtemp(prefix=f"tags_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(session, "_db_path", lambda: db)
    session._init_db()


def _weights(owner: str) -> dict[str, float]:
    return {r["tag"]: r["weight"] for r in session.get_user_tags(owner)}


# ---------- 1. 词表结构 ----------

def test_taxonomy_shape():
    assert len(tx.USER_PROFILE_TAGS) == 30
    assert len(set(tx.USER_PROFILE_TAGS)) == 30  # 无重名
    assert tx.CATEGORY_ORDER == [
        "升学考研", "求职实习", "课程考试", "科研论文", "编程技术", "语言考证", "校园生活",
    ]
    for name in tx.USER_PROFILE_TAGS:
        t = tx.TAGS_BY_NAME[name]
        assert t["category"] in tx.CATEGORY_ORDER, name
        assert t["type"] in tx.TYPE_ORDER, name
        if t["parent"] is not None:
            assert t["parent"] in tx.TAGS_BY_NAME, f"{name} 的父标签不存在"
            assert t["parent"] != name
        assert t["desc"], f"{name} 缺少说明"


def test_taxonomy_covers_every_category():
    """七大类都要有标签，否则前端会出现一个空分组。"""
    for cat in tx.CATEGORY_ORDER:
        assert tx.tags_of_category(cat), f"{cat} 下没有标签"


def test_taxonomy_payload_for_frontend():
    payload = tx.taxonomy()
    assert payload["total"] == 30
    assert [g["category"] for g in payload["groups"]] == tx.CATEGORY_ORDER
    assert sum(len(g["tags"]) for g in payload["groups"]) == 30
    # pickable 已排除"能力/状态"，但仍占 28 条
    assert "时间管理" not in payload["pickable"]
    assert "心态调节" not in payload["pickable"]
    assert len(payload["pickable"]) == 28


# ---------- 2. 同义词归一 ----------

def test_normalize_maps_aliases_to_canonical():
    for raw in ["六级", "四级", "CET6", "cet-6", "cet6", "四六级", "英语六级"]:
        assert tx.normalize_tag(raw) == "英语四六级", raw
    assert tx.normalize_tag("python") == "Python"  # ASCII 大小写
    assert tx.normalize_tag("  考研数学 ") == "考研数学"  # 首尾空白
    assert tx.normalize_tag("考研  数学") == "考研 数学"  # 内部空白压成一个（不入词表，属未知）


def test_normalize_keeps_unknown_verbatim():
    """归不上要原样保留：add_user_tags 是通用写入口，检索偏置会写词表外主题。"""
    assert tx.normalize_tag("数学") == "数学"
    assert tx.normalize_tag("随便什么") == "随便什么"
    assert tx.normalize_tag("") == ""
    assert tx.normalize_tag(None) == ""


def test_normalize_tags_dedup_and_order():
    assert tx.normalize_tags(["六级", "英语四六级", "CET6", "Python"]) == ["英语四六级", "Python"]
    assert tx.normalize_tags("Python") == ["Python"]  # 传成字符串不该按字符遍历
    assert tx.normalize_tags(None) == []


def test_filter_known_tags_is_the_hard_gate():
    assert tx.filter_known_tags(["考研数学", "越界标签", "六级"]) == ["考研数学", "英语四六级"]
    assert tx.filter_known_tags(["a", "b", "c", "考研数学"], limit=2) == ["考研数学"]
    assert tx.filter_known_tags([]) == []


# ---------- 3. 落库：归一生效、手选幂等、可取消 ----------

def test_add_user_tags_normalizes_before_write(monkeypatch):
    _fresh_db(monkeypatch, "norm")
    session.add_user_tags("u1", ["六级", "英语四六级", "CET6"])
    # 三种写法归一成一条，且**同一次调用内先去重** → 只 +1（不是 1+1+1=3）
    assert _weights("u1") == {"英语四六级": 1.0}
    # 跨调用才是累加：第二次出现权重变 2
    session.add_user_tags("u1", ["CET-6"])
    assert _weights("u1") == {"英语四六级": 2.0}


def test_add_user_tags_keeps_unknown_topics(monkeypatch):
    """词表外的主题词必须照写 —— 检索偏置（D 块）就是靠这些词工作的。"""
    _fresh_db(monkeypatch, "unknown")
    session.add_user_tags("u1", ["数学", "英语"], delta=0.5)
    assert _weights("u1") == {"数学": 0.5, "英语": 0.5}


def test_pick_user_tags_is_idempotent(monkeypatch):
    _fresh_db(monkeypatch, "pick_idem")
    assert session.pick_user_tags("u1", ["考研备考", "英语四六级"]) == ["考研备考", "英语四六级"]
    assert _weights("u1") == {"考研备考": 3.0, "英语四六级": 3.0}

    # 再存一次同样的选择：权重不动（否则连点五下就会把某个标签顶成"绝对真爱"）
    session.pick_user_tags("u1", ["考研备考", "英语四六级"])
    assert _weights("u1") == {"考研备考": 3.0, "英语四六级": 3.0}
    assert session.get_user_tag_picks("u1") == ["考研备考", "英语四六级"]


def test_pick_user_tags_removal_keeps_weight_row(monkeypatch):
    """取消勾选只影响 picks 回显，不删 user_tags —— 那条可能也是聊出来的证据。"""
    _fresh_db(monkeypatch, "pick_rm")
    session.pick_user_tags("u1", ["考研备考", "英语四六级"])
    session.pick_user_tags("u1", ["考研备考"])
    assert session.get_user_tag_picks("u1") == ["考研备考"]
    assert _weights("u1") == {"考研备考": 3.0, "英语四六级": 3.0}


def test_pick_user_tags_drops_unknown_and_caps(monkeypatch):
    _fresh_db(monkeypatch, "pick_guard")
    # 词表外的一律丢弃；超过 MAX_PICKS 只取前 N（HTTP 层会 400，这里是兜底）
    assert session.pick_user_tags("u1", ["越界标签"]) == []
    assert _weights("u1") == {}
    many = tx.USER_PROFILE_TAGS[: session.MAX_PICKS + 3]
    assert len(session.pick_user_tags("u1", many)) == session.MAX_PICKS


def test_pick_user_tags_owner_isolation(monkeypatch):
    _fresh_db(monkeypatch, "pick_iso")
    session.pick_user_tags("u1", ["考研备考"])
    assert session.get_user_tag_picks("u2") == []
    assert session.get_user_tags("u2") == []


# ---------- 4. 模板推荐 ----------

def test_recommend_orders_by_weight():
    """手选权重 3.0 必须压过聊天权重 1.0 —— 否则聊得多的人盖过选得准的人。"""
    got = recommend_for_tags({"英语四六级": 3.0, "考研备考": 3.0}, limit=3)
    assert [r["id"] for r in got] == ["kaoyan", "english"]
    assert got[0]["matched"] == ["考研备考"]

    got2 = recommend_for_tags({"英语四六级": 3.0, "考研备考": 1.0}, limit=3)
    assert got2[0]["id"] == "english"
    assert got2[0]["score"] == 3.0


def test_recommend_ignores_unknown_and_zero_weight():
    assert recommend_for_tags({"数学": 9.0, "英语": 9.0}) == []  # 词表外不参与
    assert recommend_for_tags({"英语四六级": 0}) == []
    assert recommend_for_tags({}) == []


def test_recommend_every_id_exists_in_catalog():
    ids = {t["id"] for t in get_catalog()}
    for tag, tids in TAG_TO_TEMPLATES.items():
        assert tag in tx.TAGS_BY_NAME, f"映射里出现词表外的标签：{tag}"
        for tid in tids:
            assert tid in ids, f"{tag} → {tid} 模板不存在"


# ---------- 5. HTTP 层（TestClient + dependency_overrides）----------

def _client(monkeypatch, tag: str, owner: str = "u1"):
    """临时库 + 假登录态。返回 (client, teardown)。"""
    from fastapi.testclient import TestClient

    import main
    from core.auth import get_current_user

    _fresh_db(monkeypatch, tag)
    main.app.dependency_overrides[get_current_user] = lambda: owner
    return TestClient(main.app), main


def _reset(main) -> None:
    main.app.dependency_overrides.clear()


def test_api_get_taxonomy(monkeypatch):
    client, main = _client(monkeypatch, "api_tax")
    try:
        r = client.get("/api/profile/tags")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 30
        assert len(body["groups"]) == 7
    finally:
        _reset(main)


def test_api_put_normalizes_and_reads_back(monkeypatch):
    client, main = _client(monkeypatch, "api_put")
    try:
        r = client.put("/api/profile/tags", json={"tags": ["六级", "考研备考"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["picks"] == ["英语四六级", "考研备考"]  # 归一后落库
        assert body["has_tags"] is True
        # 大类聚合：两条都在，权重各 3
        cats = {c["category"]: c["weight"] for c in body["categories"]}
        assert cats == {"语言考证": 3.0, "升学考研": 3.0}

        r2 = client.get("/api/profile")
        assert r2.status_code == 200
        assert r2.json()["picks"] == ["英语四六级", "考研备考"]
    finally:
        _reset(main)


def test_api_put_rejects_unknown_tag(monkeypatch):
    """悄悄丢弃会让前端以为存上了 —— 必须 400 并点名。"""
    client, main = _client(monkeypatch, "api_bad")
    try:
        r = client.put("/api/profile/tags", json={"tags": ["考研数学", "外星语言学"]})
        assert r.status_code == 400
        assert "外星语言学" in r.json()["detail"]
        assert client.get("/api/profile").json()["picks"] == []  # 一条都没写进去
    finally:
        _reset(main)


def test_api_put_rejects_empty_and_too_many(monkeypatch):
    client, main = _client(monkeypatch, "api_bounds")
    try:
        assert client.put("/api/profile/tags", json={"tags": []}).status_code == 400
        too_many = tx.USER_PROFILE_TAGS[: session.MAX_PICKS + 1]
        assert client.put("/api/profile/tags", json={"tags": too_many}).status_code == 400
    finally:
        _reset(main)


def test_api_profile_owner_isolation(monkeypatch):
    """owner 一律取自登录态：换个人登录看不到别人的标签。"""
    client, main = _client(monkeypatch, "api_iso", owner="u1")
    try:
        client.put("/api/profile/tags", json={"tags": ["考研备考"]})
        from core.auth import get_current_user

        main.app.dependency_overrides[get_current_user] = lambda: "u2"
        assert client.get("/api/profile").json()["picks"] == []
    finally:
        _reset(main)


def test_api_templates_recommended_follows_picks(monkeypatch):
    """整条链路：手选 → user_tags → 模板广场「为你推荐」。"""
    client, main = _client(monkeypatch, "api_rec")
    try:
        assert client.get("/api/templates/").json()["recommended"] == []
        client.put("/api/profile/tags", json={"tags": ["英语四六级"]})
        body = client.get("/api/templates/").json()
        assert body["recommended"][0]["id"] == "english"
        assert body["recommended"][0]["matched"] == ["英语四六级"]
        assert len(body["templates"]) == 8  # 目录本身没被动过
    finally:
        _reset(main)
