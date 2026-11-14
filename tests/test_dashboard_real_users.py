"""真实用户使用数据视图的回归（2026-11-15）。

口径依据：`docs/数据看板_2026-10-27/口径与数据字典.md` §2.8 / §7.8。

这个文件锁的是**四件事**，每一件坏了都不会报错、只会让数字悄悄错：

  1. **四桶互斥且穷尽**：真实 / 内部 / 已注销 / 未登录 加起来必须等于同区间
     的全量调用数。对不上账的看板，用户拿计算器一减就再也不信了。
  2. **内部账号与管理员都不进"真实"**。管理员靠 config 即时排除（不依赖标记），
     这样"新配一个管理员却忘了他会被算成真实用户"这种漏法不成立。
  3. **已注销账号不能算成真实用户**：它们的 uid 还在日志里，但 users 里已经没有行。
     算进去会让"活跃用户数"包含一批再也不会上线的人。
  4. **标记是双向的且可撤销**：取消标记后数字必须变回来 —— 否则标错一次就永久污染。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import account, apilog, config, dashboard, quota, session, timeutil, user_feedback
from core.auth import create_token

ADMIN_NAME = "devadmin"
USER_NAME = "normaluser"
PASSWORD = "secret123"

# 一个"日志里有 uid、users 里没有行"的 uid —— 模拟已注销账号
GONE_UID = "uid-gone-0001"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 干净进程内状态（同 test_dashboard.py 的 env，不碰真实库）。"""
    path = str(tmp_path / "realusers.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    account._ensure_tables(force=True)
    quota._ensure_tables()
    quota._ensure_rate_table()
    apilog._READY.clear()
    user_feedback._READY.clear()
    apilog._BUF.clear()
    apilog.ensure()
    user_feedback.ensure()
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [ADMIN_NAME])
    monkeypatch.setattr(config, "ADMIN_UIDS", [])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    yield path
    apilog._BUF.clear()
    apilog._READY.clear()
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


def _mk_user(name: str) -> str:
    u = account.get_user_by_username(name)
    if u:
        return u["id"]
    return account.create_user(name, PASSWORD)["id"]


def _seed(uid: str | None, n: int, path: str = "/api/tasks", day_index: int = -1) -> None:
    """往 api_call_logs 里塞 n 条。

    `day_index=-1` 是区间内最近的一天（今天），`-2` 是前天 ——
    用 `timeutil.day_bounds` 取边界，绝不手算 86400（会有跨日/时区错位）。
    """
    days = timeutil.recent_days(7)
    day = days[day_index]
    s, _e = timeutil.day_bounds(day)
    ts = s + 3600  # 当天的 01:00，稳稳落在这一天里
    with session._get_conn() as conn:  # noqa: SLF001
        conn.executemany(
            "INSERT INTO api_call_logs (ts, path, method, status, dur_ms, uid) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(ts, path, "GET", 200, 12, uid) for _ in range(n)],
        )


# ==================== A. 四桶分解 ====================


def test_four_buckets_add_up_to_total(env):
    """四桶之和 = 全量调用数。这条是整块视图的可信度基础。"""
    real_uid = _mk_user("realuser")
    qa_uid = _mk_user("qa_probe")
    account.set_internal(qa_uid, True)
    _seed(real_uid, 5)
    _seed(qa_uid, 3)
    _seed(GONE_UID, 2)  # 有 uid，users 里没有行
    _seed(None, 4)  # 未登录: uid 为空

    calls = dashboard.real_user_view(7)["calls"]
    assert calls["real"] == 5
    assert calls["internal"] == 3, "被标记账号的调用没有进 internal 桶"
    assert calls["orphan"] == 2, "uid 不在 users 里的调用没有进 orphan 桶"
    assert calls["anonymous"] == 4, "uid 为空的调用没有进 anonymous 桶"
    assert calls["total"] == 14
    assert (
        calls["real"] + calls["internal"] + calls["orphan"] + calls["anonymous"]
    ) == calls["total"], "四桶没有做到互斥且穷尽 —— 用户一减就会发现对不上账"
    assert calls["share"] == round(5 / 14, 4)


def test_share_is_null_when_no_calls(env):
    """区间没有调用时 share 是 None，不是 0.0 —— 前者是"算不出"，后者是"算出来是零"。"""
    view = dashboard.real_user_view(7)
    assert view["calls"]["total"] == 0
    assert view["calls"]["share"] is None


def test_admin_excluded_without_explicit_flag(env):
    """管理员按 config 即时排除，不依赖 is_internal 标记。

    漏了这条的后果：新配一个管理员，他的调用会一直算成"真实用户在用产品"，
    而没人会想到去看 config。
    """
    admin_uid = _mk_user(ADMIN_NAME)  # 未标记，只在 ADMIN_USERNAMES 里
    _seed(admin_uid, 9)
    _seed(_mk_user("realuser"), 2)
    view = dashboard.real_user_view(7)
    assert view["calls"]["real"] == 2, "管理员被算成了真实用户"
    assert view["calls"]["internal"] == 9
    assert view["internal"]["admins"] == 1
    assert view["users"]["active"] == 1


# ==================== B. 标记的双向性 ====================


def test_marked_account_leaves_real_and_comes_back(env):
    """标记 → 数字变小；取消 → 数字复原。标错一次不该永久污染。"""
    uid = _mk_user("somescript")
    _seed(uid, 7)
    assert dashboard.real_user_view(7)["calls"]["real"] == 7

    assert account.set_internal(uid, True) is True
    v1 = dashboard.real_user_view(7)
    assert v1["calls"]["real"] == 0
    assert v1["calls"]["internal"] == 7

    assert account.set_internal(uid, False) is True
    assert dashboard.real_user_view(7)["calls"]["real"] == 7


def test_set_internal_unknown_uid_returns_false(env):
    """uid 不存在返回 False（接口层据此 404），不能静默当成功。"""
    assert account.set_internal("nope-not-a-uid", True) is False


def test_internal_list_includes_accounts_with_zero_calls(env):
    """被标记但区间内 0 次调用的账号也要列出来 —— 否则标错了没有入口取消。"""
    account.set_internal(_mk_user("silent_qa"), True)
    view = dashboard.real_user_view(7)
    names = [x["username"] for x in view["internal"]["list"]]
    assert "silent_qa" in names
    assert view["internal"]["marked"] == 1


# ==================== C. 两个统计维度 ====================


def test_bucket_and_detail_queries_agree(env):
    """`calls.real` 必须等于三个明细查询各自的合计。

    这是**两段互相独立的 SQL**：分桶用 CASE 表达式（`_bucket_counts`），
    明细用 `_real_where` 拼条件（series / by_feature / by_user）。
    任何一方改了判定条件另一方没跟上，就会出现「卡片写 11 次、按功能表加起来 8 次」
    这种页面 —— 每个数字单看都对，放在一起对不上账。
    """
    a = _mk_user("u_agree_a")
    b = _mk_user("u_agree_b")
    qa = _mk_user("qa_agree")
    account.set_internal(qa, True)
    _seed(a, 4, path="/api/tasks")
    _seed(b, 3, path="/api/kb/public")
    # ★ 内部账号必须**真的产生调用**。只建号不 seed 的话，即使明细查询漏掉了
    #   is_internal 条件，两边的数字也不会分叉 —— 这条断言就变成永远为真
    #   （第一次写成本文件时就是这个漏洞，反证 18/18 全绿才发现）
    _seed(qa, 9, path="/api/tasks")
    _seed(GONE_UID, 5)  # 已注销：只能进 orphan
    _seed(None, 6)  # 未登录：只能进 anonymous

    v = dashboard.real_user_view(7)
    real = v["calls"]["real"]
    assert real == 7
    assert sum(x["calls"] for x in v["by_feature"]) == real
    assert sum(x["calls"] for x in v["by_user"]) == real
    assert sum(s["calls"] for s in v["series"]) == real


def test_by_feature_counts_distinct_users_not_calls(env):
    """一个用户刷 10 次，用户数必须是 1 —— 否则"功能受欢迎"会被单人刷出来。"""
    a = _mk_user("user_a")
    b = _mk_user("user_b")
    _seed(a, 10, path="/api/tasks")
    _seed(b, 1, path="/api/tasks")
    _seed(None, 50, path="/api/tasks")  # 未登录的不能算进用户数
    items = dashboard.real_user_view(7)["by_feature"]
    row = next(x for x in items if x["path"] == "/api/tasks")
    assert row["calls"] == 11, "未登录调用混进了真实用户的次数"
    assert row["users"] == 2


def test_by_user_reports_active_days(env):
    """活跃天数按自然日去重，同一天调用 5 次仍然是 1 天。"""
    uid = _mk_user("day_user")
    _seed(uid, 5, day_index=-1)
    _seed(uid, 3, day_index=-2)
    items = dashboard.real_user_view(7)["by_user"]
    row = next(x for x in items if x["uid"] == uid)
    assert row["calls"] == 8
    assert row["days"] == 2


def test_returning_users_requires_two_days(env):
    """回的来客 = 至少两天有调用。一天来 100 次仍算"只来过一天"。"""
    a = _mk_user("two_days")
    b = _mk_user("one_day")
    _seed(a, 1, day_index=-1)
    _seed(a, 1, day_index=-3)
    _seed(b, 100, day_index=-1)
    u = dashboard.real_user_view(7)["users"]
    assert u["active"] == 2
    assert u["returning"] == 1
    assert u["single_day"] == 1
    assert u["avg_days"] == 1.5


# ==================== D. 接口层：权限与契约 ====================


def test_real_users_requires_login(client):
    r = client.get("/api/admin/real-users?days=7")
    assert r.status_code == 401, "未登录必须是 401（前端靠它区分该去登录）"


def test_real_users_hidden_from_normal_user(client):
    _login(client, USER_NAME)
    r = client.get("/api/admin/real-users?days=7")
    assert r.status_code == 404, "非管理员必须伪装成接口不存在，不能返回 403"


def test_real_users_payload_shape(client):
    _login(client, ADMIN_NAME)
    r = client.get("/api/admin/real-users?days=7&limit=5")
    assert r.status_code == 200
    d = r.json()
    for key in ("range", "calls", "users", "internal", "series", "by_feature", "by_user"):
        assert key in d, "响应缺字段: " + key
    assert len(d["series"]) == 7, "趋势必须覆盖区间内每一天（含 0 的那天）"
    assert d["range"]["days"] == 7


def test_mark_endpoint_changes_view(client):
    """走接口标记后，同一接口再拉一次，真实调用数必须立刻变小。"""
    _login(client, ADMIN_NAME)
    target = _mk_user("scripted")
    _seed(target, 6)
    before = client.get("/api/admin/real-users?days=7").json()
    assert before["calls"]["real"] == 6

    r = client.post("/api/admin/users/%s/internal" % target, json={"internal": True})
    assert r.status_code == 200 and r.json()["internal"] is True

    after = client.get("/api/admin/real-users?days=7").json()
    assert after["calls"]["real"] == 0
    assert after["calls"]["internal"] == 6


def test_mark_endpoint_404_for_unknown_uid(client):
    _login(client, ADMIN_NAME)
    r = client.post("/api/admin/users/not-a-uid/internal", json={"internal": True})
    assert r.status_code == 404


def test_mark_endpoint_requires_admin(client):
    _login(client, USER_NAME)
    r = client.post("/api/admin/users/someone/internal", json={"internal": True})
    assert r.status_code == 404, "普通用户不该能标记任何人（也不该知道这个接口存在）"


# ==================== E. 老库迁移 ====================


def test_is_internal_column_is_added_and_idempotent(env):
    """老库（没有 is_internal）跑一次 ensure 就补上，再跑一次不出错。"""
    with session._get_conn() as conn:  # noqa: SLF001
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    assert "is_internal" in cols
    account._ensure_tables(force=True)
    with session._get_conn() as conn:  # noqa: SLF001
        cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    assert "is_internal" in cols2


def test_existing_accounts_default_to_real(env):
    """存量账号默认是真实用户。默认值反过来会让「真实用户数」凭空变小。"""
    uid = _mk_user("oldaccount")
    with session._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT is_internal FROM users WHERE id = ?", (uid,)).fetchone()
    assert int(row[0]) == 0
