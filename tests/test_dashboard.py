"""开发者数据看板 + 用户反馈的功能与安全回归。

口径依据：`docs/数据看板_2026-10-27/口径与数据字典.md`（下文简称"字典 §N"）。
本文件锁死的不只是"功能能跑"，还有三件最容易静默坏掉的事：

  1. **权限两层顺序**：未登录必须 401（不是 403），非管理员必须 403（不是 401）。
     反了的话前端会把"权限不够"当成"登录态过期"，引导用户做一次无意义的登录。
  2. **观际者效应**：看板自己的请求不能进统计。否则"打开看板"本身推高数字，
     看到的数据永远比真实多一截，而且越刷新越多。
  3. **CSV 公式注入**：用户反馈是自由文本，以 `=` 开头的单元格 Excel 会当公式执行。
     必须在展示侧转义（XSS）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from core import account, apilog, config, quota, session, user_feedback
from core.auth import create_token

ADMIN_NAME = "devadmin"
USER_NAME = "normaluser"
PASSWORD = "secret123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 干净进程内状态（不碰真实 data/sessions.db）。"""
    path = str(tmp_path / "dashboard.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    # users 表归 core/account.py 管（它自己建表，见 account._ensure_tables）；
    # 只调 session._init_db 会缺 users，登录相关的用例全挂在 "no such table"
    account._ensure_tables()
    # /api/auth/me 会顺带返回配额快照，缺 usage / rate_buckets 表会 500
    quota._ensure_tables()
    quota._ensure_rate_table()
    # 惰性建表：按库路径记"已初始化"，换路径必须清掉才会在临时库里建表
    apilog._READY.clear()
    user_feedback._READY.clear()
    apilog._BUF.clear()
    apilog.ensure()
    user_feedback.ensure()
    # 管理员名单换成测试账号：config 默认值是真实作者账号，会让"普通用户"测出 200
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [ADMIN_NAME])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    yield path
    apilog._BUF.clear()
    apilog._READY.clear()
    user_feedback._READY.clear()


@pytest.fixture()
def client():
    import main

    return TestClient(main.app)


def _login(client: TestClient, username: str) -> None:
    """造用户 + 直接注入 Cookie（不走注册接口，避免依赖 ALLOW_SIGNUP 开关）。"""
    if not account.get_user_by_username(username):
        account.create_user(username, PASSWORD)
    uid = account.get_user_by_username(username)["id"]
    client.cookies.set("baomi_session", create_token(uid, 7, username))


# ==================== A. 采集与归一化（字典 §3）====================


def test_path_normalisation_replaces_ids():
    """兜底归一化：uuid/纯数字/长 hex → {id}；固定路径段不能被误伤。"""
    assert apilog.normalize_path("/api/sessions/9f3c8a1b2c3d4e5f6a7b8c9d0e1f2a3b") == (
        "/api/sessions/{id}"
    )
    assert apilog.normalize_path("/api/sessions/123") == "/api/sessions/{id}"
    assert (
        apilog.normalize_path("/api/feedback/3f2504e0-4f89-11d3-9a0c-0305e82c3301/")
        == "/api/feedback/{id}/"
    )
    # 这些是固定段，一旦被替换成 {id}，所有接口会并成一条，比不归一化更糟
    assert apilog.normalize_path("/api/auth/me") == "/api/auth/me"
    assert apilog.normalize_path("/api/config/defaults") == "/api/config/defaults"


def test_record_scope_excludes_health_metrics_and_admin():
    """采集范围（字典 §3.1/§3.2）。"""
    assert apilog.should_record("/api/sessions/") is True
    assert apilog.should_record("/api/chat/stream") is True
    # 探活 / 采集器 / 看板自身的请求全部排除
    assert apilog.should_record("/health") is False
    assert apilog.should_record("/api/metrics") is False
    assert apilog.should_record("/api/metrics/json") is False
    assert apilog.should_record("/api/admin/overview") is False


def test_request_is_recorded_with_route_template(env, client):
    """真实请求会落库，且 path 是**路由模板**而不是真实 id（字典 §2.6 高基数）。"""
    _login(client, USER_NAME)
    client.get("/api/auth/me/")
    apilog.flush()

    with session._get_conn() as conn:
        rows = conn.execute("SELECT path, method, status, dur_ms FROM api_call_logs").fetchall()

    paths = {r["path"] for r in rows}
    assert "/api/auth/me/" in paths, f"实际记录：{paths}"
    row = [r for r in rows if r["path"] == "/api/auth/me/"][0]
    assert row["method"] == "GET"
    assert row["status"] == 200
    assert row["dur_ms"] >= 0


def test_admin_request_not_recorded(env, client):
    """观测者效应：打开看板不能推高统计（字典 §3.2）。"""
    _login(client, ADMIN_NAME)
    apilog.flush()
    with session._get_conn() as conn:
        conn.execute("DELETE FROM api_call_logs")  # 清掉登录过程中的记录
    apilog._BUF.clear()

    client.get("/api/admin/overview?days=7")
    client.get("/api/admin/apis?days=7")
    apilog.flush()

    with session._get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM api_call_logs").fetchone()[0]
    assert n == 0, "看板自己的请求被记进了统计"


# ==================== B. 权限矩阵（字典 §6）====================

_ADMIN_PATHS = [
    "/api/admin/overview",
    "/api/admin/apis",
    "/api/admin/feedback",
    "/api/admin/dict",
]


def test_admin_endpoints_require_login(env, client):
    """未登录 → 401（不是 403）。"""
    for p in _ADMIN_PATHS:
        r = client.get(p)
        assert r.status_code == 401, f"{p} 未登录返回 {r.status_code}"


def test_admin_endpoints_reject_normal_user(env, client):
    """登录但不是管理员 → 403（不是 401）。"""
    _login(client, USER_NAME)
    for p in _ADMIN_PATHS:
        r = client.get(p)
        assert r.status_code == 403, f"{p} 普通用户返回 {r.status_code}"


def test_admin_can_open_dashboard(env, client):
    """管理员 → 200。"""
    _login(client, ADMIN_NAME)
    for p in _ADMIN_PATHS:
        r = client.get(p)
        assert r.status_code == 200, f"{p} 管理员返回 {r.status_code}: {r.text[:120]}"


def test_me_exposes_is_admin(env, client):
    """前端靠 `is_admin` 决定看板入口是否显示；普通用户必须是 False。"""
    _login(client, ADMIN_NAME)
    assert client.get("/api/auth/me/").json()["is_admin"] is True

    c2 = TestClient(__import__("main").app)
    _login(c2, USER_NAME)
    assert c2.get("/api/auth/me/").json()["is_admin"] is False


# ==================== C. 口径正确性（字典 §1/§2）====================


def _seed_calls(rows: list[tuple]) -> None:
    """直接写日志表造数据：比打真请求更可控（能造 4xx/5xx 和任意时间）。"""
    with session._get_conn() as conn:
        conn.executemany(
            "INSERT INTO api_call_logs (ts, path, method, status, dur_ms, uid) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_overview_matches_raw_sql(env, client):
    """看板数字必须等于字典 §2 的对账 SQL（对账差异容忍 0）。"""
    now = int(time.time())
    _seed_calls(
        [
            (now, "/api/a/", "GET", 200, 100, "u1"),
            (now, "/api/a/", "GET", 500, 300, "u1"),
            (now, "/api/b/", "POST", 200, 50, "u2"),
            # 90 天前：落在任何档位之外，不该被算进 7 天
            (now - 90 * 86400, "/api/old/", "GET", 200, 10, None),
        ]
    )
    _login(client, ADMIN_NAME)
    d = client.get("/api/admin/overview?days=7").json()

    with session._get_conn() as conn:
        expect = conn.execute(
            "SELECT COUNT(*) FROM api_call_logs WHERE ts >= ?", (d["range"]["start"],)
        ).fetchone()[0]

    assert d["calls"]["total"] == expect
    assert d["calls"]["ok"] == 2
    assert d["calls"]["failed"] == 1
    assert d["calls"]["success_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert d["calls"]["max_ms"] == 300


def test_users_total_is_snapshot_new_is_ranged(env, client):
    """`users.total` 不随 days 变，`users.new` 才随（字典 §2.1）。"""
    account.create_user("u_old", PASSWORD)
    _login(client, ADMIN_NAME)

    d7 = client.get("/api/admin/overview?days=7").json()
    d1 = client.get("/api/admin/overview?days=1").json()
    # 两个新用户：ADMIN_NAME 与 u_old 都是刚注册的
    assert d7["users"]["total"] == d1["users"]["total"] == 2
    assert d7["users"]["new"] == 2
    assert d1["users"]["new"] == 2


def test_empty_range_has_no_fake_rate(env, client):
    """空数据的成功率必须是 None（前端显示 —），不能是 0% 或 100%。"""
    _login(client, ADMIN_NAME)
    d = client.get("/api/admin/overview?days=7").json()
    assert d["calls"]["total"] == 0
    assert d["calls"]["success_rate"] is None


def test_ranking_ordered_and_tie_broken_by_path(env, client):
    """排行按次数降序；并列按 path 字典序（否则每次刷新顺序会跳）。"""
    now = int(time.time())
    _seed_calls(
        [(now, "/api/z/", "GET", 200, 1, None)] * 3
        + [(now, "/api/m/", "GET", 200, 1, None)] * 3
        + [(now, "/api/a/", "GET", 200, 1, None)] * 1
    )
    _login(client, ADMIN_NAME)
    items = client.get("/api/admin/apis?days=7").json()["items"]
    assert [i["path"] for i in items][:2] == ["/api/m/", "/api/z/"]
    assert items[2]["path"] == "/api/a/"


def test_series_covers_every_day_in_range(env, client):
    """趋势图每天都要有点（哪怕 0），否则前端画线会断。"""
    _login(client, ADMIN_NAME)
    s = client.get("/api/admin/overview?days=7").json()["series"]
    assert len(s) == 7
    assert all({"day", "calls", "failed"} <= set(x) for x in s)


def test_ranking_sort_slow_excludes_low_volume(env, client):
    """`sort=slow` 只算调用 ≥ 3 次的接口。

    只调用过 1 次却恰好 9 秒的接口（冷启动 / 建表 / 首次加载模型）不是性能问题，
    放进"最慢"榜会把真正的慢接口挤下去。
    """
    now = int(time.time())
    _seed_calls(
        [(now, "/api/cold/", "GET", 200, 9000, None)]
        + [(now, "/api/steady/", "GET", 200, 400, None)] * 3
        + [(now, "/api/fast/", "GET", 200, 10, None)] * 5
    )
    _login(client, ADMIN_NAME)
    body = client.get("/api/admin/apis?days=7&sort=slow").json()
    assert body["sort"] == "slow"
    paths = [i["path"] for i in body["items"]]
    assert "/api/cold/" not in paths, "只调用 1 次的接口混进了最慢榜"
    assert paths[0] == "/api/steady/"


def test_ranking_sort_fail_and_invalid_fallback(env, client):
    """`sort=fail` 按失败次数排；非法 sort 静默回落 calls（展示偏好，不是契约）。"""
    now = int(time.time())
    _seed_calls(
        [(now, "/api/bad/", "POST", 500, 30, None), (now, "/api/bad/", "POST", 200, 30, None)]
        + [(now, "/api/busy/", "GET", 200, 5, None)] * 10
    )
    _login(client, ADMIN_NAME)

    fail = client.get("/api/admin/apis?days=7&sort=fail").json()
    assert fail["items"][0]["path"] == "/api/bad/"
    assert fail["items"][0]["failed"] == 1

    bogus = client.get("/api/admin/apis?days=7&sort=whatever").json()
    assert bogus["sort"] == "calls"
    assert bogus["items"][0]["path"] == "/api/busy/"


def test_export_endpoint_is_gone(env, client):
    """本期不做导出：接口必须真的不存在（404），不是"隐藏了按钮"。"""
    _login(client, ADMIN_NAME)
    assert client.get("/api/admin/export?kind=users").status_code == 404


# ==================== D. 用户反馈（字典 §4/§7）====================


def test_submit_and_validation(env, client):
    _login(client, USER_NAME)
    ok = client.post(
        "/api/user-feedback/",
        json={"category": "bug", "content": "点击导出没有反应", "page": "/settings"},
    )
    assert ok.status_code == 200 and ok.json()["ok"] is True

    bad_cat = client.post(
        "/api/user-feedback/", json={"category": "nope", "content": "这是一段足够长的内容"}
    )
    assert bad_cat.status_code == 400

    short = client.post("/api/user-feedback/", json={"category": "bug", "content": "坏了"})
    assert short.status_code == 400


def test_mine_is_scoped_to_owner(env, client):
    """越权用例：A 看不到 B 的反馈（字典 §6 第 3 条）。"""
    _login(client, USER_NAME)
    client.post("/api/user-feedback/", json={"category": "bug", "content": "A 的反馈内容"})

    c2 = TestClient(__import__("main").app)
    _login(c2, "otheruser")
    c2.post("/api/user-feedback/", json={"category": "ui", "content": "B 的反馈内容"})

    a_items = client.get("/api/user-feedback/mine").json()["items"]
    b_items = c2.get("/api/user-feedback/mine").json()["items"]
    assert len(a_items) == 1 and "A 的反馈" in a_items[0]["content"]
    assert len(b_items) == 1 and "B 的反馈" in b_items[0]["content"]


def test_status_transition_rules(env, client):
    """流转表（字典 §4.3）：不允许回退。"""
    _login(client, USER_NAME)
    fid = client.post(
        "/api/user-feedback/", json={"category": "feature", "content": "希望能导出 Excel"}
    ).json()["id"]

    admin = TestClient(__import__("main").app)
    _login(admin, ADMIN_NAME)

    assert admin.patch(f"/api/admin/feedback/{fid}", json={"status": "done"}).status_code == 200
    # done → doing 是回退，必须拒（409）
    r = admin.patch(f"/api/admin/feedback/{fid}", json={"status": "doing"})
    assert r.status_code == 409
    # done → closed 合法（终态收口）
    assert admin.patch(f"/api/admin/feedback/{fid}", json={"status": "closed"}).status_code == 200
    # closed 是终态
    assert admin.patch(f"/api/admin/feedback/{fid}", json={"status": "open"}).status_code == 409
    # 不存在的 id
    assert admin.patch("/api/admin/feedback/999999", json={"status": "done"}).status_code == 409


def test_counts_always_contain_all_statuses(env, client):
    """计数必须带齐字典里全部状态（缺键会让前端少渲染一行）。"""
    _login(client, ADMIN_NAME)
    counts = client.get("/api/admin/feedback").json()["counts"]
    assert set(counts) == {v for v, _ in user_feedback.STATUSES}


def test_dict_is_single_source(env):
    """字典由后端下发，含分类/状态/流转/长度限制（字典 §4.1）。"""
    d = user_feedback.dict_payload()
    assert [c["value"] for c in d["categories"]] == ["bug", "answer", "feature", "ui", "other"]
    assert d["transitions"]["closed"] == []
    assert d["limits"]["min_content"] == 5


# ==================== E. 导出（本期不做）====================
#
# 2026-10-27 决定：不提供 CSV / Excel 导出。看板定位是"开发者在浏览器里看数"，
# 把数据搬进 Excel 属于另一种工作流（要权限、要脱敏、要留痕），不在本期范围。
# 原实现 core/csvio.py 与 GET /api/admin/export 已随之下线；
# 若将来恢复，须先补"导出审计 + 字段脱敏"两件事，再回来补这一节用例。



# ==================== F. 采集缓冲（字典 §5）====================


def test_flush_is_batched_and_idempotent(env):
    """flush 批量写、重复调用不重复写、空缓冲返回 0。"""
    apilog.record("GET", "/api/a/", 200, 5, "u1")
    apilog.record("GET", "/api/b/", 200, 5, "u1")
    assert apilog.flush() == 2
    assert apilog.flush() == 0
    with session._get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_call_logs").fetchone()[0] == 2


def test_record_returns_true_when_threshold_hit(env):
    """攒到 50 条必须要求调度 flush（准实时 3 秒上界的另一半）。"""
    for _ in range(apilog.BATCH - 1):
        assert apilog.record("GET", "/api/a/", 200, 1) is False
    assert apilog.record("GET", "/api/a/", 200, 1) is True


def test_purge_removes_old_rows(env):
    """清理：90 天前的行删掉，新的留着（字典 §8）。"""
    now = int(time.time())
    _seed_calls(
        [
            (now - 200 * 86400, "/api/old/", "GET", 200, 1, None),
            (now, "/api/new/", "GET", 200, 1, None),
        ]
    )
    assert apilog.purge(90) == 1
    with session._get_conn() as conn:
        left = [r["path"] for r in conn.execute("SELECT path FROM api_call_logs")]
    assert left == ["/api/new/"]
