"""反馈一键导出的回归（2026-11-18）。

导出是**把内部数据搬出系统**的动作，所以它坏起来比页面坏更贵：
多带一个手机号出去收不回来，少带一条回复会让老师以为我们没回。

这个文件锁六件事：

  1. **脱敏真的发生**：手机号只留 3+2 位、邮箱只留首字母、uid 只留前 8 位。
     用"原串不得出现在正文里"来断言，而不是"打码过的串出现了" ——
     后者只要打码函数改成原样返回也照样绿。
  2. **分组与编号**：按状态分组、组内按时间正序、全局连续编号。
  3. **统计矩阵能加总**：每个分类每行相加 = 该状态条数，总计 = 全部条数。
  4. **处理记录跟着走**：回复内容与作者都要在导出件里。
  5. **筛选生效**：`?status=done` 只导已处理。
  6. **审计留痕**：每次导出往 `${DATA_DIR}/audit.log` 追加一行 `feedback_export`，
     带条数。文档里写的是"要开导出，先补审计与脱敏"，这条就是那句话的守卫。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from core import account, config, quota, session, user_feedback
from core.auth import create_token

ADMIN_NAME = "devadmin"
USER_NAME = "normaluser"
PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "fexport.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables(force=True)
    quota._ensure_tables()
    quota._ensure_rate_table()
    user_feedback._READY.clear()
    user_feedback.ensure()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [ADMIN_NAME])
    monkeypatch.setattr(config, "ADMIN_UIDS", [])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    # 审计写盘的位置也要隔离：不隔离会往真实 data/ 目录里写测试数据
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    yield path
    user_feedback._READY.clear()


@pytest.fixture()
def client(env):
    import main

    return TestClient(main.app)


def _login(client: TestClient, username: str) -> str:
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))
    return uid


def _mk(uid: str, name: str, cat: str = "bug", contact: str = "", page: str = "/") -> int:
    return user_feedback.add(uid, name, cat, "这是一条用来测试导出的反馈正文。", contact, page)


# ==================== 1. 脱敏 ====================


def test_contact_and_uid_are_masked(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "张三", contact="13812345678", page="/settings")
    _mk(uid, "李四", contact="zhang@example.com")
    _mk(uid, "王五", contact="")

    body = client.get("/api/admin/feedback/export").text

    # 原串一个都不许留在导出件里
    assert "13812345678" not in body
    assert "zhang@example.com" not in body
    # 打码后的形态在（留头尾才够对上号）
    assert "138****78" in body
    assert "z***@example.com" in body
    assert "（未留）" in body
    # uid 只留前 8 位
    assert uid not in body
    assert f"{uid[:8]}…" in body


def test_mask_contact_pure_function():
    assert user_feedback.mask_contact("13812345678") == "138****78"
    assert user_feedback.mask_contact("138-1234-5678") == "138****78"
    assert user_feedback.mask_contact("a@b.com") == "a***@b.com"
    assert user_feedback.mask_contact("  ") == "（未留）"
    # 太短的非号码串：不留全貌（1 个字 + ***）
    assert user_feedback.mask_contact("微信abc") == "微***"


# ==================== 2/3. 分组、编号、矩阵 ====================


def test_grouped_by_status_with_continuous_numbering(client):
    uid = _login(client, ADMIN_NAME)
    id1 = _mk(uid, "甲", cat="ui")
    id2 = _mk(uid, "乙", cat="feature")
    id3 = _mk(uid, "丙", cat="other")
    user_feedback.set_status(id3, "done")

    body = client.get("/api/admin/feedback/export").text

    # 分组标题：待处理 2 条、已处理 1 条，空的组不出现
    assert "## 待处理（2 条）" in body
    assert "## 已处理（1 条）" in body
    assert "## 处理中（" not in body
    # 全局连续编号：待处理占 1、2，已处理是 3
    assert f"### 1. 界面与体验　#{id1}" in body
    assert f"### 2. 功能建议　#{id2}" in body
    assert f"### 3. 其他　#{id3}" in body


def test_summary_matrix_adds_up(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲", cat="bug")
    _mk(uid, "乙", cat="bug")
    _mk(uid, "丙", cat="ui")
    done = _mk(uid, "丁", cat="ui")
    user_feedback.set_status(done, "done")

    text, meta = user_feedback.export_markdown()
    assert meta["items"] == 4
    # 矩阵里「合计」行的待处理 = 3、已处理 = 1、总计 4
    tail = [ln for ln in text.splitlines() if ln.startswith("| **合计**")]
    assert tail, "统计矩阵缺合计行"
    cells = [c.strip() for c in tail[0].strip("|").split("|")]
    # 首列是「合计」，末列是总计
    assert cells[-1] == "**4**"
    assert cells[1] == "**3**"
    assert cells[3] == "**1**"


# ==================== 4. 处理记录 ====================


def test_replies_are_exported_with_author(client):
    uid = _login(client, ADMIN_NAME)
    fid = _mk(uid, "甲")
    user_feedback.add_reply(fid, uid, "苞米呀", "已记录，下个版本修。")

    body = client.get("/api/admin/feedback/export").text
    assert "**处理记录**" in body
    assert "苞米呀：已记录，下个版本修。" in body


# ==================== 5. 筛选 ====================


def test_status_filter(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "留在待处理")
    done = _mk(uid, "已经处理了")
    user_feedback.set_status(done, "done")

    body = client.get("/api/admin/feedback/export?status=done").text
    assert "已经处理了" in body
    assert "留在待处理" not in body
    assert "数据范围：已处理" in body


def test_empty_export_is_still_a_document(client):
    _login(client, ADMIN_NAME)
    r = client.get("/api/admin/feedback/export")
    assert r.status_code == 200
    assert "# 知穗 · 用户反馈汇总" in r.text
    assert "该范围内没有反馈" in r.text
    # 空的时候不该画一张全是 0 的矩阵（读者会以为数据丢了）
    assert "统计概览" not in r.text


# ==================== 6. 审计 + 权限 ====================


def test_audit_line_is_written(client, tmp_path):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲")
    _mk(uid, "乙")

    client.get("/api/admin/feedback/export")

    log = tmp_path / "audit.log"
    assert log.exists(), "导出没有留审计"
    recs = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    hits = [r for r in recs if r["event"] == "feedback_export"]
    assert len(hits) == 1
    assert hits[0]["uid"] == uid
    assert hits[0]["items"] == 2
    assert hits[0]["status"] == "all"


def test_export_needs_login_and_admin(client):
    _login(client, USER_NAME)
    assert client.get("/api/admin/feedback/export").status_code == 404
    client.cookies.clear()
    assert client.get("/api/admin/feedback/export").status_code == 401


def test_download_headers(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲")
    r = client.get("/api/admin/feedback/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.md"')
    assert r.headers["cache-control"] == "no-store"
    # 中文正文必须是 UTF-8 而不是转义后的 \uXXXX
    assert "用户反馈汇总" in r.text
