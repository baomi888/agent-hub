# -*- coding: utf-8 -*-
"""P6 打卡（/api/checkin）测试：服务层派生 + HTTP 端点 + owner 隔离 + 斜杠。

不联网、不依赖真实 API Key：
  · 每个测试用 monkeypatch 指向**独立临时库**并补齐 sessions / users / daily 表，
    彻底隔离（自动还原，不污染其它测试模块，也不被其它模块污染）。
  · 服务层用 daily._save_report 直接种「已生成总结」的日期，绕过真实 LLM。
  · HTTP 层对 generate 端点 monkeypatch service.daily.summarize_day 为返回 ready 报告。

覆盖：
  1. compute_streak / list_history：连续天数、最长连续、points、shared 标记
  2. owner 强制隔离：B 读不到 A 的打卡日期（数据层 + HTTP 层双验证）
  3. 斜杠：/api/checkin/* 无尾斜杠两种写法都要命中、不 3xx
  4. generate / share 端点可走通（write 路径）
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config  # noqa: E402
import core.session  # noqa: E402
import core.account  # noqa: E402
import core.quota  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from service import checkin as chk  # noqa: E402
from service import daily  # noqa: E402
from service.daily import Report  # noqa: E402

DATE = time.strftime("%Y-%m-%d")
DATE_Y = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))


def _prep_db(monkeypatch, tag: str) -> None:
    """指向一个独立临时库并补齐所有需要的表（sessions / users / daily_reports）。

    monkeypatch 在测试结束后自动还原 _db_path，因此不影响其它测试模块，
    也不会被其它模块覆盖——这是多文件 pytest 下唯一稳的隔离方式。
    """
    tmp = tempfile.mkdtemp(prefix=f"checkin_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    core.account._ensure_tables()  # _init_db 不建 users 表，登录鉴权需要它
    # daily._daily_tables_ready 是模块级全局标志：第一个临时库建完 daily_reports 后即
    # 置 True；切到新临时库时若不再重置，_ensure_daily_tables 会直接 return，导致新库缺表。
    # 每个测试都指向独立临时库，因此这里必须先把标志清回 False 再建表。
    daily._daily_tables_ready = False
    daily._ensure_daily_tables()  # daily_reports / delegations
    core.quota._ensure_tables()  # usage（generate 端点调 quota.consume 需要它）


def _save_ready(uid: str, date: str, n_points: int = 3) -> None:
    rid = daily._report_id(uid, date)
    daily._save_report(
        Report(
            rid,
            uid,
            date,
            "ready",
            {},
            [{"text": f"c{i}", "source_refs": []} for i in range(n_points)],
            "test",
            int(time.time()),
        )
    )


# ==================== 服务层（不联网） ====================


def test_service_streak_counts_ready_days(monkeypatch):
    _prep_db(monkeypatch, "svc_streak")
    _save_ready("svc_a", DATE)
    _save_ready("svc_a", DATE_Y)
    s = chk.compute_streak("svc_a")
    assert DATE in s["dates"] and DATE_Y in s["dates"]
    assert s["streak"] >= 2  # 今天与昨天都 ready → 至少连续 2 天
    assert s["longest"] >= 2


def test_service_streak_no_report_is_zero(monkeypatch):
    _prep_db(monkeypatch, "svc_zero")
    s = chk.compute_streak("svc_nobody")
    assert s["streak"] == 0
    assert s["dates"] == []


def test_service_history_points_and_shared(monkeypatch):
    _prep_db(monkeypatch, "svc_hist")
    _save_ready("svc_b", DATE, n_points=4)
    hist = chk.list_history("svc_b")
    assert len(hist) == 1
    assert hist[0]["date"] == DATE
    assert hist[0]["points"] == 4
    assert hist[0]["shared"] is False
    chk.mark_shared("svc_b", DATE)
    assert chk.list_history("svc_b")[0]["shared"] is True


def test_service_owner_isolation(monkeypatch):
    _prep_db(monkeypatch, "svc_iso")
    _save_ready("iso_svc_a", DATE)
    _save_ready("iso_svc_a", DATE_Y)
    sa = chk.compute_streak("iso_svc_a")
    sb = chk.compute_streak("iso_svc_b")
    assert DATE in sa["dates"] and DATE_Y in sa["dates"]
    assert DATE not in sb["dates"]  # B 没有 A 的日期
    assert "iso_svc_a" not in str(sb)  # 不应泄漏他人信息


# ==================== HTTP 层（TestClient） ====================


def _login(c: TestClient, username: str, password: str = "pw123456") -> str:
    c.post("/api/auth/register/", json={"username": username, "password": password})
    r = c.post("/api/auth/login/", json={"username": username, "password": password})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["id"]  # owner_id 是注册后生成的用户 id，不是用户名


def _seed(uid: str) -> None:
    _save_ready(uid, DATE)
    _save_ready(uid, DATE_Y)


def test_http_requires_login(monkeypatch):
    _prep_db(monkeypatch, "http_auth")
    with TestClient(main.app) as c:
        assert c.get("/api/checkin/streak").status_code == 401
        assert c.get("/api/checkin/history").status_code == 401


def test_http_streak_and_history(monkeypatch):
    def fake_summarize(uid, date, stats=None, llm=None):
        _save_ready(uid, date, n_points=2)
        return daily.get_report(uid, date)

    monkeypatch.setattr(daily, "summarize_day", fake_summarize)
    _prep_db(monkeypatch, "http_streak")

    with TestClient(main.app) as c:
        uid = _login(c, "http_a")
        _seed(uid)
        r = c.get("/api/checkin/streak")
        assert r.status_code == 200
        body = r.json()
        assert body["streak"] >= 2
        h = c.get("/api/checkin/history")
        assert h.status_code == 200
        assert len(h.json()["history"]) >= 2


def test_http_generate_and_share(monkeypatch):
    def fake_summarize(uid, date, stats=None, llm=None):
        _save_ready(uid, date, n_points=1)
        return daily.get_report(uid, date)

    monkeypatch.setattr(daily, "summarize_day", fake_summarize)
    _prep_db(monkeypatch, "http_gen")

    with TestClient(main.app) as c:
        uid = _login(c, "gen_a")
        g = c.post("/api/checkin/generate")
        assert g.status_code == 200
        assert g.json()["points"] == 1
        s = c.post("/api/checkin/share", json={"date": DATE})
        assert s.status_code == 200 and s.json()["ok"] is True
        h = c.get("/api/checkin/history").json()["history"]
        today = next((x for x in h if x["date"] == DATE), None)
        assert today is not None and today["shared"] is True


def test_http_owner_isolation(monkeypatch):
    def fake_summarize(uid, date, stats=None, llm=None):
        _save_ready(uid, date)
        return daily.get_report(uid, date)

    monkeypatch.setattr(daily, "summarize_day", fake_summarize)
    _prep_db(monkeypatch, "http_iso")

    with TestClient(main.app) as ca, TestClient(main.app) as cb:
        uid_a = _login(ca, "iso_http_a")
        uid_b = _login(cb, "iso_http_b")
        _seed(uid_a)
        rb = cb.get("/api/checkin/streak")
        assert rb.status_code == 200
        assert DATE not in rb.json()["dates"]  # B 读不到 A 的日期
        ra = ca.get("/api/checkin/streak")
        assert DATE in ra.json()["dates"]
        assert ra.json()["dates"] != rb.json()["dates"]


def test_http_slash_no_redirect(monkeypatch):
    """打卡端点不带尾斜杠，两种写法都要命中、不许 3xx（与项目其余端点统一）。"""
    _prep_db(monkeypatch, "http_slash")
    REDIRECTS = (301, 302, 303, 307, 308)
    with TestClient(main.app) as c:
        _login(c, "slash_a")
        for path in ("/api/checkin/streak", "/api/checkin/streak/",
                     "/api/checkin/history", "/api/checkin/history/"):
            for form in (path, path.rstrip("/") + "/" if path.endswith("/") else path + "/"):
                r = c.request("GET", form, follow_redirects=False)
                assert r.status_code not in REDIRECTS, f"{form} -> {r.status_code}"
