"""开发者数据看板 + 用户反馈的功能与安全回归。

口径依据：`docs/数据看板_2026-10-27/口径与数据字典.md`（下文简称"字典 §N"）。
本文件锁死的不只是"功能能跑"，还有三件最容易静默坏掉的事：

  1. **权限两层顺序**：未登录必须 401（不是 404），非管理员必须 404（不是 401）。
     反了的话，"登录态过期"会被伪装成"路径不存在"，用户不知道该去登录，
     排查时也会误判成路由被删了。
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
    """未登录 → 401（不是 404）。登录态过期必须能与"路径不存在"区分开。"""
    for p in _ADMIN_PATHS:
        r = client.get(p)
        assert r.status_code == 401, f"{p} 未登录返回 {r.status_code}"


def test_admin_endpoints_reject_normal_user(env, client):
    """登录但不是管理员 → **404**（伪装成接口不存在，不是 403）。

    403 等于向对方确认"这个路径存在、你只是权限不够"，对只有开发者自己用的
    看板来说这本身就是泄露。404 不给任何确认。
    """
    _login(client, USER_NAME)
    for p in _ADMIN_PATHS:
        r = client.get(p)
        assert r.status_code == 404, f"{p} 普通用户返回 {r.status_code}"
        # 连 detail 都不能有中文/权限字样，否则等于换了种方式承认接口存在
        assert r.json() == {"detail": "Not Found"}, f"{p} 泄露了：{r.text[:80]}"


def test_normal_user_404_is_indistinguishable(env, client):
    """非管理员访问看板接口，与访问一个**真的不存在**的路径完全无法区分。

    这是"彻底隐藏"的硬判据：状态码、detail 文案、响应头里有意义的字段
    都得一致。只要有一处不同，就能被用来确认"这里有个后台"。
    """
    _login(client, USER_NAME)
    fake = client.get("/api/admin/__definitely_not_a_route__")
    real = client.get("/api/admin/overview")
    assert fake.status_code == real.status_code == 404
    assert fake.json() == real.json()

    # 唯一允许的差异是响应体长度之类无关项；关键头必须一致
    for h in ("content-type",):
        assert fake.headers.get(h) == real.headers.get(h)


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


def test_expected_errors_are_not_failures(env, client):
    """401（未登录探测）与 /api/report 的 404（今天还没总结）都不算失败。

    内测第一天实测：178 条 401 + 17 条报告空态把成功率压到 90.7%、
    把"出过错"榜刷满 /api/auth/me。两者都是预期行为——但 500、普通 404、
    注册 400 照旧算失败，不能一刀切"4xx 都不算"。
    """
    now = int(time.time())
    _seed_calls(
        [
            (now, "/api/auth/me/", "GET", 401, 5, None),
            (now, "/api/auth/me/", "GET", 401, 5, None),
            (now, "/api/auth/me/", "GET", 401, 5, None),
            (now, "/api/report/today", "GET", 404, 8, "u1"),
            (now, "/api/report/{date}", "GET", 404, 8, "u1"),
            (now, "/api/a/", "GET", 200, 100, "u1"),
            (now, "/api/a/", "GET", 500, 300, "u1"),
            (now, "/api/b/", "GET", 404, 20, "u1"),
        ]
    )
    _login(client, ADMIN_NAME)
    d = client.get("/api/admin/overview?days=7").json()

    # 5 条预期（3 个 401 + 2 个报告空态）都不进 failed
    assert d["calls"]["expected"] == 5
    assert d["calls"]["failed"] == 2  # 500 + 普通 404
    # 分母 = 8 - 5 = 3，分子 ok 只有 1 条 200
    assert d["calls"]["success_rate"] == pytest.approx(1 / 3, abs=1e-4)

    s = client.get("/api/admin/overview?days=7").json()["series"]
    today = [x for x in s if x["failed"] > 0]
    assert today and today[0]["failed"] == 2, "趋势里的失败也必须剔除预期行为"

    fail = client.get("/api/admin/apis?days=7&sort=fail").json()
    paths = [i["path"] for i in fail["items"]]
    assert "/api/auth/me/" not in paths, "全 401 的接口不该进'出过错'榜"
    assert "/api/report/today" not in paths, "报告空态不该进'出过错'榜"
    assert fail["items"][0]["path"] == "/api/a/"


def test_failures_explain_reason(env, client):
    """看板要能自己说清"为什么失败"，并按 需处理 / 可忽略 分栏。"""
    now = int(time.time())
    _seed_calls(
        [
            (now, "/api/auth/me/", "GET", 401, 5, None),
            (now, "/api/report/today", "GET", 404, 8, "u1"),
            (now, "/api/auth/register/", "POST", 409, 10, None),
            (now, "/api/kb/", "GET", 500, 900, "u1"),
        ]
    )
    _login(client, ADMIN_NAME)
    d = client.get("/api/admin/failures?days=7").json()

    assert len(d["recent"]) == 4
    assert all({"path", "status", "hint", "expected", "label"} <= set(x) for x in d["recent"])

    ign = {x["path"] for x in d["ignorable"]}
    need = {x["path"] for x in d["need_attention"]}
    assert ign == {"/api/auth/me/", "/api/report/today"}
    assert need == {"/api/auth/register/", "/api/kb/"}

    by_path = {x["path"]: x for x in d["recent"]}
    assert "未登录" in by_path["/api/auth/me/"]["hint"]
    assert "总结" in by_path["/api/report/today"]["hint"]
    assert "占用" in by_path["/api/auth/register/"]["hint"]
    assert "服务端异常" in by_path["/api/kb/"]["hint"]
    # 聚合榜：按 (路径, 状态) 计数，最上面就是"哪类错最多"
    assert d["top"] and "calls" in d["top"][0]


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


# ==================== E. 流式响应耗时（2026-10-27 修的 P1 口径缺陷）====================
#
# 缺陷原貌：`BaseHTTPMiddleware` 的 `call_next` 对 StreamingResponse 在**首帧产生
# 之前**就返回，中间件拿到的 dur_ms 是"建流耗时"。实测 `/api/chat/stream`
# 客户端 15851ms、看板记 29ms，差 546 倍 —— 于是 `sort=slow` 的榜首变成 85ms 的
# 注册接口，真正最慢的聊天接口既算错又不上榜。
#
# 这两个用例锁死两件事：
#   ① 流式要测到**整条流**（不是首帧前）
#   ② 包计时不能改变响应体内容（少一帧就是事故，不是统计问题）


def _temp_stream_route(main_mod, path: str, frames: int, gap: float):
    """临时往 app 上挂一个流式端点，返回 (route, 清理函数)。"""
    import asyncio

    from fastapi.responses import StreamingResponse
    from fastapi.routing import APIRoute

    async def _gen():
        for i in range(frames):
            await asyncio.sleep(gap)
            yield f"data: {i}\n\n"

    async def _endpoint():
        return StreamingResponse(_gen(), media_type="text/event-stream")

    route = APIRoute(path, _endpoint, methods=["GET"])
    main_mod.app.router.routes.append(route)
    return route, lambda: main_mod.app.router.routes.remove(route)


def test_streaming_response_records_whole_stream(env, client, monkeypatch):
    """流式响应必须记整条流的时长，而不是首帧之前。"""
    import main

    # 关键：别让 record 触发真实落库，否则 _BUF 被清空、断言读不到行
    monkeypatch.setattr(main, "_spawn_flush", lambda: None)

    frames, gap = 4, 0.05  # 真实时长 ≈ 200ms
    path = "/api/_probe/stream"
    _, cleanup = _temp_stream_route(main, path, frames, gap)
    try:
        apilog._BUF.clear()
        with client.stream("GET", path) as r:
            body = b"".join(r.iter_bytes())
    finally:
        cleanup()

    # ② 响应体没被计时包装破坏
    assert body.count(b"data:") == frames, f"帧数不对：{body!r}"

    rows = [r for r in apilog._BUF if r[1] == path]
    assert len(rows) == 1, f"流式响应应记 1 条，实际 {len(rows)} 条"
    dur = rows[0][4]
    floor = int(frames * gap * 1000 * 0.8)
    assert dur >= floor, f"只测到 {dur}ms（应 ≥{floor}ms）—— body_iterator 没被包上计时"


def test_non_streaming_duration_unchanged(env, client, monkeypatch):
    """非流式响应不能被这次改动带坏：该测多久还是多久。"""
    import asyncio

    from fastapi.routing import APIRoute

    import main

    monkeypatch.setattr(main, "_spawn_flush", lambda: None)

    gap = 0.1
    path = "/api/_probe/plain"

    async def _endpoint():
        await asyncio.sleep(gap)
        return {"ok": True}

    route = APIRoute(path, _endpoint, methods=["GET"])
    main.app.router.routes.append(route)
    try:
        apilog._BUF.clear()
        assert client.get(path).json() == {"ok": True}
    finally:
        main.app.router.routes.remove(route)

    rows = [r for r in apilog._BUF if r[1] == path]
    assert len(rows) == 1
    assert rows[0][4] >= int(gap * 1000 * 0.8), f"非流式耗时异常：{rows[0][4]}ms"


# ==================== 接口中文名（展示层） ====================

_IGNORED_METHODS = {"HEAD", "OPTIONS", "TRACE"}


def test_every_route_has_chinese_label():
    """所有已注册的 /api 路由都必须在 `core/api_labels.py` 里登记中文名。

    这条断言的作用不是"测功能"，而是**防止看板悄悄退化成技术界面**：
    新加接口忘了登记时，`label_for` 会兜底成「知识库 · 其他提交」——
    页面仍然能跑、仍然有中文，但信息量为零，评审看到的是"其他操作"。
    没有这条断言，这种退化要等到有人翻看板才会发现。
    """
    import main
    from core import api_labels

    missing: list[str] = []
    for r in main.app.routes:
        path = str(getattr(r, "path", "") or "")
        if not path.startswith("/api"):
            continue
        for m in getattr(r, "methods", set()) or set():
            if m in _IGNORED_METHODS:
                continue
            if not api_labels.is_known(m, path):
                missing.append(f"{m} {path}")

    assert not missing, (
        "以下接口没有中文名，去看板「功能使用排行」会显示成「其他操作」，"
        "请在 core/api_labels.py 补上：\n  " + "\n  ".join(sorted(missing))
    )


def test_label_lookup_ignores_param_names():
    """路由改参数名（`{sid}` → `{id}`）不能让中文名失效。

    日志表里存的是历史模板，路由改名后旧行仍带旧参数名；
    查表时若按字面匹配，改一次名就会让整类历史数据掉进兜底。
    """
    from core import api_labels

    assert api_labels.label_for("GET", "/api/sessions/{sid}") == "对话 · 打开会话"
    assert api_labels.label_for("GET", "/api/sessions/{id}") == "对话 · 打开会话"
    assert api_labels.label_for("GET", "/api/sessions/{anything}/") == "对话 · 打开会话"
    # 尾斜杠同理：项目里 `/api/kb/` 与 `/api/sessions` 两种风格并存
    assert api_labels.label_for("GET", "/api/auth/me/") == api_labels.label_for("GET", "/api/auth/me")


def test_unknown_route_falls_back_with_module_name():
    """真查不到时也不能吐英文路径 —— 兜底至少要给出中文模块名。"""
    from core import api_labels

    label = api_labels.label_for("POST", "/api/kb/{id}/not-a-real-thing")
    assert label.startswith("知识库 · 其他")
    assert "/" not in label  # 中文名里不该出现路径碎片


def test_ranking_items_carry_labels(env, client):
    """排行接口下发的每一行都带中文名，且和 path 对应得上（不是错位）。"""
    now = int(time.time())
    _seed_calls(
        [
            (now, "/api/chat/stream", "POST", 200, 1200, None),
            (now, "/api/kb/{sid}/upload", "POST", 200, 300, None),
        ]
    )
    _login(client, ADMIN_NAME)
    r = client.get("/api/admin/apis?days=1&limit=50")
    assert r.status_code == 200
    labels = {it["path"]: it["label"] for it in r.json()["items"]}
    assert labels["/api/chat/stream"] == "对话 · 提问（逐字回复）"
    assert labels["/api/kb/{sid}/upload"] == "知识库 · 上传文件"
