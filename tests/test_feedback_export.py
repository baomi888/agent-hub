"""反馈一键导出的回归（2026-11-18 建，2026-11-19 改 Word + 日期范围）。

导出是**把内部数据搬出系统**的动作，坏起来比页面坏更贵：
多带一个手机号出去收不回来，少导一条会让老师以为我们没收到。

这个文件锁七件事：

  1. **隐私根本不进导出件**：uid、姓名、联系方式、来源页、管理员回复、
     用户补充，一个都不出现。用"原串不得出现"来断言，而不是"打码串出现"
     —— 后者只要打码函数改成原样返回也照样绿。现在连打码函数都没有了：
     白名单字段在 `export_docx()` 入口就筛掉，从源头不给隐私留出口。
  2. **只导该导的**：反馈问题、提交日期、分类、解决状态四栏。
  3. **日期范围含端点、只填一端合法、都不填 = 全部**；日期格式错 400。
  4. **按状态分组、全局连续编号**。
  5. **筛选生效**：`?status=done` 只导已处理。
  6. **空导出仍是合法 Word**（不能返回 0 字节）。
  7. **审计留痕**：每次导出往 `${DATA_DIR}/audit.log` 追加一行
     `feedback_export`，带条数与日期范围。
  8. **纯文字排版 + 文件名**：导出件里不许有 `<w:tbl>`，正文 12pt、标题 18pt；
     文件名由日期自动生成（11-18~11-19 → 11-18-11-19用户反馈.docx）。

读 docx 用 zipfile + ElementTree，**不依赖 python-docx**（CI 的 venv 没装）。
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from core import account, config, quota, session, timeutil, user_feedback
from core.auth import create_token

ADMIN_NAME = "devadmin"
USER_NAME = "normaluser"
PASSWORD = "secret123"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

STATUS_CN = {"open": "待处理", "doing": "处理中", "done": "已处理", "closed": "不处理"}


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


def _mk(
    uid: str,
    name: str,
    cat: str = "bug",
    contact: str = "",
    page: str = "",
    content: str = "这是一条用来测试导出的反馈正文。",
    at: int | None = None,
) -> int:
    fid = user_feedback.add(uid, name, cat, content, contact, page)
    if at is not None:
        # add() 固定用当前时间；测试要造"哪天提的"就直接改库，不 monkeypatch
        # 全局 time.time（那会影响审计时间戳，牵一发动全身）。
        with session._get_conn() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE user_feedback SET created_at = ?, updated_at = ? WHERE id = ?",
                (at, at, fid),
            )
    return fid


def _at(day: str, hour: int = 10) -> int:
    return timeutil.day_bounds(day)[0] + hour * 3600


def _docx(blob: bytes) -> str:
    """把 docx 里的全部文字抽出来：只取 <w:t>，同一段的多个 run 拼成一行。"""
    assert blob[:2] == b"PK", "不是合法 docx（zip 头都不对）"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert z.testzip() is None
        root = ET.fromstring(z.read("word/document.xml"))
    return "\n".join(
        "".join((t.text or "") for t in p.iter(W + "t"))
        for p in root.iter(W + "p")
    )


def _heads(text: str) -> list[str]:
    """每条反馈的小标题：1. 2026-11-10 · 出错了 · 待处理"""
    return [line for line in text.splitlines() if re.match(r"^\d+\. ", line)]


def _filename(cd: str) -> str:
    """从 Content-Disposition 里取出 filename* 的中文名（后面可能还有 ASCII 兜底）。"""
    raw = cd.split("filename*=UTF-8''", 1)[1].split(";", 1)[0].strip()
    return unquote(raw)


# ==================== 1. 隐私根本不进导出件 ====================


def test_pii_and_dialogue_never_exported(client):
    uid = _login(client, ADMIN_NAME)
    fid = _mk(
        uid, "张Visible三", contact="13812345678", page="/settings-secret",
        content="反馈原文：设置页按钮点不动。",
    )
    user_feedback.add_reply(fid, uid, "管理Visible员", "这条回复绝不能被导出去")
    user_feedback.reply_own(uid, fid, "用户自己的补充也不能导出去")

    r = client.get("/api/admin/feedback/export")
    assert r.status_code == 200
    text = _docx(r.content)

    # 原串一个都不许在（不是"打码串在"——那个判据挡不住打码函数退化）。
    # ★ 不断言"联系方式/对话"这些词本身：生成器末尾那行说明「已隐去
    #   联系方式、不含对话记录」本来就该出现，断言词会误红。要断言的是
    #   **值**和**字段名栏**都没混进表格。
    for secret in (
        uid, "张Visible三", "13812345678", "/settings-secret",
        "这条回复绝不能被导出去", "用户自己的补充也不能导出去",
        "管理Visible员", "提交人", "来源页面", "处理记录",
    ):
        assert secret not in text, f"隐私/对话出现在导出件里：{secret}"
    assert "反馈原文：设置页按钮点不动。" in text


def test_only_whitelisted_fields(client):
    """每条只是一行小标题（日期·分类·状态）+ 一段正文，没有别的栏。"""
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲", at=_at("2026-11-10"))

    text = _docx(client.get("/api/admin/feedback/export").content)
    heads = _heads(text)
    assert len(heads) == 1
    assert "2026-11-10" in heads[0]
    assert "出错了" in heads[0]
    assert "待处理" in heads[0]
    assert "这是一条用来测试导出的反馈正文。" in text


def test_pure_text_no_table_and_readable_font(client):
    """用户明确要纯文字、不要表格、字要大：锁死无 tbl 且正文 ≥12pt。"""
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲")
    blob = client.get("/api/admin/feedback/export").content
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "<w:tbl>" not in xml, "导出又变回表格了"
    assert '<w:sz w:val="24"/>' in xml, "正文没有放大到 12pt"
    assert '<w:sz w:val="36"/>' in xml, "标题没有放大到 18pt"


# ==================== 3. 日期范围 ====================


def test_date_range_includes_both_ends(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "早", content="10号的问题", at=_at("2026-11-10"))
    _mk(uid, "中", content="11号的问题", at=_at("2026-11-11"))
    _mk(uid, "晚", content="12号的问题", at=_at("2026-11-12"))

    r = client.get(
        "/api/admin/feedback/export?start_date=2026-11-10&end_date=2026-11-11"
    )
    assert r.status_code == 200
    text = _docx(r.content)
    assert "10号的问题" in text and "11号的问题" in text, "端点那天被排除了"
    assert "12号的问题" not in text
    assert "2026-11-10 ~ 2026-11-11" in text


def test_fixed_single_day(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "早", content="10号的问题", at=_at("2026-11-10"))
    _mk(uid, "中", content="11号的问题", at=_at("2026-11-11"))

    r = client.get(
        "/api/admin/feedback/export?start_date=2026-11-11&end_date=2026-11-11"
    )
    text = _docx(r.content)
    assert "11号的问题" in text
    assert "10号的问题" not in text


def test_open_ended_ranges(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "早", content="10号的问题", at=_at("2026-11-10"))
    _mk(uid, "中", content="11号的问题", at=_at("2026-11-11"))
    _mk(uid, "晚", content="12号的问题", at=_at("2026-11-12"))

    only_from = _docx(
        client.get("/api/admin/feedback/export?start_date=2026-11-12").content
    )
    assert "12号的问题" in only_from and "11号的问题" not in only_from
    assert "~ 至今" in only_from

    only_to = _docx(
        client.get("/api/admin/feedback/export?end_date=2026-11-10").content
    )
    assert "10号的问题" in only_to and "11号的问题" not in only_to
    assert "最早 ~" in only_to


def test_all_when_dates_blank(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲", at=_at("2026-11-10"))
    _mk(uid, "乙", at=_at("2026-11-11"))
    r = client.get("/api/admin/feedback/export")
    text = _docx(r.content)
    assert "全部时间" in text
    assert len(_heads(text)) == 2


@pytest.mark.parametrize(
    "query",
    [
        "start_date=2026-11-32",
        "end_date=11/11/2026",
        "start_date=2026-11-11&end_date=2026-11-10",
    ],
)
def test_bad_date_range_returns_400(client, query):
    _login(client, ADMIN_NAME)
    assert client.get("/api/admin/feedback/export?" + query).status_code == 400


# ==================== 4. 分组与编号 ====================


def test_grouped_by_status_with_continuous_numbering(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲", cat="ui", content="待处理的那条")
    b = _mk(uid, "乙", cat="feature", content="已处理的那条")
    user_feedback.set_status(b, "done")

    text = _docx(client.get("/api/admin/feedback/export").content)
    heads = _heads(text)
    nums = [h.split(".", 1)[0] for h in heads]
    assert nums == ["1", "2"], "编号必须全局连续"
    assert "待处理的那条" in text and "待处理" in heads[0]
    assert "已处理的那条" in text and "已处理" in heads[1], "待处理必须排在已处理前面"


# ==================== 5. 状态筛选 ====================


def test_status_filter(client):
    uid = _login(client, ADMIN_NAME)
    _mk(uid, "甲", content="留在待处理")
    done = _mk(uid, "乙", content="已经处理了")
    user_feedback.set_status(done, "done")

    r = client.get("/api/admin/feedback/export?status=done")
    text = _docx(r.content)
    assert "已经处理了" in text
    assert "留在待处理" not in text
    assert "数据范围：已处理" in text


# ==================== 6. 空导出 ====================


def test_empty_export_is_still_a_document(client):
    _login(client, ADMIN_NAME)
    r = client.get("/api/admin/feedback/export")
    assert r.status_code == 200
    text = _docx(r.content)
    assert "知穗 · 用户反馈汇总" in text
    assert "该范围内没有反馈。" in text


# ==================== 7. 审计 + 权限 + 下载头 ====================


def test_audit_line_is_written(client, tmp_path):
    uid = _login(client, ADMIN_NAME)
    # 必须落在查询范围里：造"今天"再按 11-10~11 筛，导出 0 条是对的，
    # 审计记 0 也是对的 —— 红的是测试数据，不是功能
    _mk(uid, "甲", at=_at("2026-11-10"))
    _mk(uid, "乙", at=_at("2026-11-11"))

    client.get("/api/admin/feedback/export?start_date=2026-11-10&end_date=2026-11-11")

    log = tmp_path / "audit.log"
    assert log.exists(), "导出没有留审计"
    recs = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    hits = [r for r in recs if r["event"] == "feedback_export"]
    assert len(hits) == 1
    assert hits[0]["uid"] == uid
    assert hits[0]["items"] == 2
    assert hits[0]["start"] == "2026-11-10"
    assert hits[0]["end"] == "2026-11-11"


def test_export_needs_login_and_admin(client):
    _login(client, USER_NAME)
    assert client.get("/api/admin/feedback/export").status_code == 404
    client.cookies.clear()
    assert client.get("/api/admin/feedback/export").status_code == 401


def test_download_headers(client):
    _login(client, ADMIN_NAME)
    _mk("dummy", "甲")  # 不造数据也要能给下载头（空导出同样是合法文件）
    r = client.get("/api/admin/feedback/export?start_date=2026-11-10&end_date=2026-11-11")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    # 中文名走 filename*；ASCII filename 只是直接打开接口时的兜底。
    assert "filename*=UTF-8''" in cd
    assert 'filename="feedback.docx"' in cd, "缺 ASCII 兜底名"
    assert _filename(cd) == "11-10-11-11用户反馈.docx"
    assert r.headers["cache-control"] == "no-store"
    assert "wordprocessingml" in r.headers["content-type"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("", "全部用户反馈.docx"),
        ("start_date=2026-11-18", "11-18用户反馈.docx"),
        ("end_date=2026-11-19", "11-19用户反馈.docx"),
        ("start_date=2026-11-18&end_date=2026-11-19", "11-18-11-19用户反馈.docx"),
    ],
)
def test_filename_generated_from_dates(client, query, expected):
    """用户点名要的文件名规则：选 11-18~11-19 就叫 11-18-11-19用户反馈。"""
    _login(client, ADMIN_NAME)
    url = "/api/admin/feedback/export" + ("?" + query if query else "")
    cd = client.get(url).headers["content-disposition"]
    assert _filename(cd) == expected
