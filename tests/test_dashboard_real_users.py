"""真实用户使用数据视图的回归（2026-11-15）。

口径依据：`docs/数据看板_2026-10-27/口径与数据字典.md` §2.8。

这个文件锁的是**五件事**，每一件坏了都不会报错、只会让数字悄悄错：

  1. **五桶互斥且穷尽**：真实 / 被动访问 / 内部 / 已注销 / 未登录 加起来必须等于
     同区间的全量调用数。对不上账的看板，用户拿计算器一减就再也不信了。
  2. **"真实用户"必须真的发起过对话**：只按"调过任意接口"计活跃，用无头浏览器
     把界面跑一遍就能凭空造出几个"人均上百次"的用户（2026-11-15 实测：
     三个测试号 300 次调用、0 次对话，面板显示"3 个真实用户、人均 100 次"）。
  3. **内部账号、管理员、自动化打标流量都不进"真实"**。管理员靠 config 即时排除，
     这样"新配一个管理员却忘了他会被算成真实用户"这种漏法不成立。
  4. **已注销账号不能算成真实用户**：它们的 uid 还在日志里，但 users 里已经没有行。
  5. **标记是双向的且可撤销**：取消标记后数字必须变回来 —— 否则标错一次就永久污染。
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

CHAT = dashboard.CHAT_PATH


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


def _seed(
    uid: str | None,
    n: int,
    path: str = "/api/tasks",
    day_index: int = -1,
    internal: int = 0,
    client: str = "",
) -> None:
    """往 api_call_logs 里塞 n 条。

    `day_index=-1` 是区间内最近的一天（今天），`-2` 是前天 ——
    用 `timeutil.day_bounds` 取边界，绝不手算 86400（会有跨日/时区错位）。

    `client` 是客户端类型（browser/headless/script）。**默认空串**是有意的：
    历史行（加列之前）就是空串，测试默认走"老旧数据"这条路，才能顺带保证
    升级不会把过去的数据全判成自动化。
    """
    days = timeutil.recent_days(7)
    day = days[day_index]
    s, _e = timeutil.day_bounds(day)
    ts = s + 3600  # 当天的 01:00，稳稳落在这一天里
    with session._get_conn() as conn:  # noqa: SLF001
        conn.executemany(
            "INSERT INTO api_call_logs (ts, path, method, status, dur_ms, uid, internal, client) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (ts, path, "POST" if path == CHAT else "GET", 200, 12, uid, internal, client)
                for _ in range(n)
            ],
        )


def _seed_chat(uid: str, n: int = 1, day_index: int = -1, client: str = "") -> None:
    """给账号塞 n 次对话 —— 这是"真实用户"的唯一判据。"""
    _seed(uid, n, path=CHAT, day_index=day_index, client=client)


# ==================== A. 五桶分解 ====================


def test_five_buckets_add_up_to_total(env):
    """五桶之和 = 全量调用数。这条是整块视图的可信度基础。"""
    real_uid = _mk_user("realuser")
    passive_uid = _mk_user("lurker")
    qa_uid = _mk_user("qa_probe")
    account.set_internal(qa_uid, True)
    _seed_chat(real_uid, 1)
    _seed(real_uid, 4)  # 聊过的人，其余调用也归"真实用户"
    _seed(passive_uid, 3)  # 只浏览、没对话 -> passive
    _seed(qa_uid, 3)
    _seed(GONE_UID, 2)  # 有 uid，users 里没有行
    _seed(None, 4)  # 未登录: uid 为空

    calls = dashboard.real_user_view(7)["calls"]
    assert calls["real"] == 5, "聊过的账号的其余调用没有算进真实用户"
    assert calls["passive"] == 3, "只浏览不对话的调用没有单列出来"
    assert calls["internal"] == 3, "被标记账号的调用没有进 internal 桶"
    assert calls["orphan"] == 2, "uid 不在 users 里的调用没有进 orphan 桶"
    assert calls["anonymous"] == 4, "uid 为空的调用没有进 anonymous 桶"
    assert calls["total"] == 17
    assert (
        calls["real"]
        + calls["passive"]
        + calls["internal"]
        + calls["orphan"]
        + calls["anonymous"]
    ) == calls["total"], "五桶没有做到互斥且穷尽 —— 用户一减就会发现对不上账"
    assert calls["share"] == round(5 / 17, 4)


def test_browsing_without_chat_is_not_a_real_user(env):
    """★ 核心回归：只翻页面、一次对话都没发起的账号不算真实用户。

    这条如果不成立，用无头浏览器把界面跑一遍就会造出"人均上百次调用"的假用户
    （2026-11-15 的事故形态）。所以宁可让它红，也不能让它悄悄变绿。
    """
    uid = _mk_user("browser_only")
    _seed(uid, 50, path="/api/kb/")
    _seed(uid, 30, path="/api/sessions/")

    v = dashboard.real_user_view(7)
    assert v["calls"]["real"] == 0, "只浏览的账号被算成了真实用户"
    assert v["calls"]["passive"] == 80
    assert v["users"]["active"] == 0
    assert v["by_user"] == []
    assert all(s["calls"] == 0 for s in v["series"]), "逐日趋势里出现了假的真实调用"


def test_a_chat_makes_every_call_of_that_account_real(env):
    """聊过一次之后，**当天所有调用**都算真实用户 —— 数字才和"人真的在用"对得上。

    反过来说：如果只统计对话本身，`real` 会从几十次掉到个位数，
    页面扇出的读接口又变成"没人产生"的幽灵流量。
    """
    uid = _mk_user("chatty")
    _seed_chat(uid, 2)
    _seed(uid, 9, path="/api/kb/")
    v = dashboard.real_user_view(7)
    assert v["calls"]["real"] == 11
    assert v["calls"]["chat"] == 2, "对话次数应当只数 /api/chat/stream"
    assert v["calls"]["passive"] == 0
    assert v["users"]["active"] == 1
    assert v["users"]["per_user"] == 11.0


def test_headless_client_calls_are_not_real(env):
    """★ 核心回归（11-15 第二次事故的形态）：截图脚本带着**真人 cookie** 跑无头浏览器。

    账号维度完全正常（有对话、非内部），只有 UA 露馅 —— 这正是上一版口径漏掉的那类：
    `苞米呀` 7 天 12772 次调用里 85.6% 是同 IP 的 `HeadlessChrome`，把"真实用户"
    桶的 96% 变成了脚本的页面扇出。所以这些调用必须进 internal，一条都不能留在 real。

    同时锁住"账要平"：被排除的调用不能凭空消失，必须出现在 internal 桶里。
    """
    uid = _mk_user("swept_by_script")
    _seed_chat(uid, 1)  # 真人当天真的聊过一次 -> 账号本身是真实用户
    _seed(uid, 5, client="browser")  # 他自己翻的页
    _seed(uid, 40, client="headless")  # 截图脚本扫出来的

    v = dashboard.real_user_view(7)
    calls = v["calls"]
    assert calls["real"] == 6, "无头浏览器的调用被算进了真实用户 —— 这正是上次的事故"
    # ★ 上面那条查的是「五桶 CASE」，而「趋势 / 按账号 / 按功能」走的是另一条
    #   `_real_where`。两条 SQL 都带着 client 条件，但**只断言其中一条**的话：
    #   把 `_real_where` 里的条件删掉，五桶照样是 6，趋势里却会多出 40 次 ——
    #   2026-11-15 的反证脚本就是这样抓到本断言原来写在错的那条 SQL 上。
    assert sum(s["calls"] for s in v["series"]) == 6, "逐日趋势里混进了自动化调用"
    assert [u["calls"] for u in v["by_user"]] == [6], "按账号明细里混进了自动化调用"
    assert sum(f["calls"] for f in v["by_feature"]) == 6, "按功能明细里混进了自动化调用"
    assert calls["internal"] == 40
    assert calls["automated"]["total"] == 40
    assert calls["automated"]["headless"] == 40
    assert calls["automated"]["script"] == 0
    assert calls["automated"]["accounts"] == 1, "剔除量来自几个账号必须能看出来"
    assert (
        calls["real"]
        + calls["passive"]
        + calls["internal"]
        + calls["orphan"]
        + calls["anonymous"]
    ) == calls["total"], "把调用排除出 real 之后，它必须在 internal 桶里，否则账对不上"


def test_script_client_chat_does_not_make_a_real_user(env):
    """脚本跑一次对话不算"有人想聊"：`/api/chat/stream` 也要看客户端。

    否则健康检查/回归脚本每隔几分钟聊一句，就能把一个测试号永久洗成"活跃真实用户"。
    """
    uid = _mk_user("script_chatter")
    _seed(uid, 3, path=CHAT, client="script")
    _seed(uid, 7, path="/api/kb/", client="browser")

    v = dashboard.real_user_view(7)
    assert v["calls"]["real"] == 0, "脚本的对话把账号洗成了真实用户"
    assert v["calls"]["passive"] == 7, "真浏览的调用应当留在 passive，不该被一起排除"
    assert v["calls"]["internal"] == 3
    assert v["users"]["active"] == 0


def test_legacy_rows_without_client_are_still_real(env):
    """加列之前的历史行 `client` 是空串 —— 不能被当成自动化误杀。

    这一条是给"升级后老数据全变 0"兜底的：空串的语义是"当时没记录"，
    不是"当时是无头浏览器"。
    """
    uid = _mk_user("legacy")
    _seed_chat(uid, 1, client="")
    _seed(uid, 4, client="")
    assert dashboard.real_user_view(7)["calls"]["real"] == 5


def test_share_is_null_when_no_calls(env):
    """区间没有调用时 share 是 None，不是 0.0 —— 前者是"算不出"，后者是"算出来是零"。"""
    view = dashboard.real_user_view(7)
    assert view["calls"]["total"] == 0
    assert view["calls"]["share"] is None


def test_verify_tagged_requests_are_not_real(env):
    """请求级打标（`internal = 1`）的调用不算真实用户，哪怕账号是真的。

    这一条对应"脚本带着真人登录态跑截图"：按账号排除会连真人的使用一起抹掉，
    只按 UA 排除又能被伪造，所以判据落在请求上。
    """
    uid = _mk_user("mixed")
    _seed_chat(uid, 1)
    _seed(uid, 5, internal=1)  # 同一账号，其中 5 条是自动化
    v = dashboard.real_user_view(7)
    assert v["calls"]["real"] == 1
    assert v["calls"]["internal"] == 5
    assert v["calls"]["total"] == 6


def test_admin_excluded_without_explicit_flag(env):
    """管理员按 config 即时排除，不依赖 is_internal 标记。

    漏了这条的后果：新配一个管理员，他的调用会一直算成"真实用户在用产品"，
    而没人会想到去看 config。
    """
    admin_uid = _mk_user(ADMIN_NAME)  # 未标记，只在 ADMIN_USERNAMES 里
    _seed_chat(admin_uid, 1)
    _seed(admin_uid, 8)
    real_uid = _mk_user("realuser")
    _seed_chat(real_uid, 1)
    _seed(real_uid, 1)
    view = dashboard.real_user_view(7)
    assert view["calls"]["real"] == 2, "管理员被算成了真实用户"
    assert view["calls"]["internal"] == 9
    assert view["internal"]["admins"] == 1
    assert view["users"]["active"] == 1


# ==================== B. 标记的双向性 ====================


def test_marked_account_leaves_real_and_comes_back(env):
    """标记 → 数字变小；取消 → 数字复原。标错一次不该永久污染。"""
    uid = _mk_user("somescript")
    _seed_chat(uid, 1)
    _seed(uid, 6)
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


def test_account_snapshot_separates_registered_and_chatted(env):
    """注册账号数 ≠ 真实账号数：注册了没聊过的号不能冒充"真实用户"。"""
    a = _mk_user("talker")
    _seed_chat(a, 1)
    _mk_user("silent_one")
    _mk_user("silent_two")
    u = dashboard.real_user_view(7)["users"]
    assert u["total"] == 3, "注册账号总数（未标内部）"
    assert u["chatted"] == 1, "只有真的聊过的那个算真实账号"


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
    _seed_chat(a, 1)
    _seed(a, 3, path="/api/tasks")
    _seed_chat(b, 1)
    _seed(b, 2, path="/api/kb/public")
    # ★ 内部账号必须**真的产生调用**。只建号不 seed 的话，即使明细查询漏掉了
    #   is_internal 条件，两边的数字也不会分叉 —— 这条断言就变成永远为真
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
    _seed_chat(a, 1)
    _seed_chat(b, 1)
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
    _seed_chat(uid, 1, day_index=-1)
    _seed(uid, 5, day_index=-1)
    _seed(uid, 3, day_index=-2)
    items = dashboard.real_user_view(7)["by_user"]
    row = next(x for x in items if x["uid"] == uid)
    assert row["calls"] == 9
    assert row["days"] == 2


def test_returning_users_requires_two_days(env):
    """回的来客 = 至少两天有调用。一天来 100 次仍算"只来过一天"。"""
    a = _mk_user("two_days")
    b = _mk_user("one_day")
    _seed_chat(a, 1, day_index=-1)
    _seed(a, 1, day_index=-1)
    _seed(a, 1, day_index=-3)
    _seed_chat(b, 1, day_index=-1)
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
    for key in ("real", "passive", "internal", "orphan", "anonymous", "chat", "total"):
        assert key in d["calls"], "calls 缺字段: " + key
    for key in ("active", "returning", "total", "new", "chatted", "per_user"):
        assert key in d["users"], "users 缺字段: " + key
    assert len(d["series"]) == 7, "趋势必须覆盖区间内每一天（含 0 的那天）"
    assert d["range"]["days"] == 7


def test_mark_endpoint_changes_view(client):
    """走接口标记后，同一接口再拉一次，真实调用数必须立刻变小。"""
    _login(client, ADMIN_NAME)
    target = _mk_user("scripted")
    _seed_chat(target, 1)
    _seed(target, 5)
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
    """存量账号默认不是内部账号。默认值反过来会让「真实用户数」凭空变小。"""
    uid = _mk_user("oldaccount")
    with session._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT is_internal FROM users WHERE id = ?", (uid,)).fetchone()
    assert int(row[0]) == 0


def test_apilog_internal_column_is_migrated_on_old_table(env):
    """老库没有 api_call_logs.internal：ensure 要能补上，且默认 0。

    漏了迁移的后果不是报错（flush 吞异常），而是**看板从此不再更新**，
    所以在升级路径上必须有一条断言。
    """
    with session._get_conn() as conn:  # noqa: SLF001
        conn.execute("DROP TABLE IF EXISTS api_call_logs")
        conn.execute(
            "CREATE TABLE api_call_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts INTEGER NOT NULL, path TEXT NOT NULL, method TEXT NOT NULL,"
            " status INTEGER NOT NULL, dur_ms INTEGER NOT NULL, uid TEXT)"
        )
        conn.execute(
            "INSERT INTO api_call_logs (ts, path, method, status, dur_ms, uid)"
            " VALUES (?, '/api/tasks', 'GET', 200, 5, NULL)",
            (int(timeutil.now_ts()),),
        )
    apilog._READY.clear()
    apilog.ensure()
    with session._get_conn() as conn:  # noqa: SLF001
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_call_logs)")}
    assert "internal" in cols
    apilog.record("GET", "/api/tasks", 200, 5, None)
    assert apilog.flush() >= 1, "补列之后写入仍然失败（INSERT 的列数与表不匹配）"
