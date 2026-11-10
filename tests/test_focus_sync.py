"""校园工具集 · 专注记录同步（/api/campus/focus）测试。

不联网、不依赖真实 API Key，每个用例指向独立临时库：

覆盖：
  1. 会话「按 id 幂等」：重复上报不重复计（断网重试的核心保证）
  2. owner 强制隔离：B 读不到 A 的记录，也无法用同 id 覆盖 A 的行
  3. 任务「后写胜」：updatedAt 更小的上报不会盖掉服务端的较新版本
  4. 区间过滤与 batch 上限
  5. HTTP 层：未登录 401、参数不合法 400、PATCH 不存在的任务 404
  6. 斜杠：带与不带尾斜杠都要命中，不能 3xx（项目踩过的重定向黑洞）
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import core.account  # noqa: E402
import core.session  # noqa: E402
import main  # noqa: E402
from service import focus  # noqa: E402

BASE = "/api/campus/focus"


def _prep_db(monkeypatch, tag: str) -> None:
    tmp = tempfile.mkdtemp(prefix=f"focus_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    core.account._ensure_tables()  # 登录鉴权要 users 表


def _session(sid: str, started: int, seconds: int = 1500, **kw) -> dict:
    row = {
        "id": sid,
        "taskId": "t_1",
        "taskName": "高等数学",
        "category": "数学",
        "startedAt": started,
        "endedAt": started + seconds * 1000,
        "seconds": seconds,
        "plannedSeconds": 1500,
        "direction": "countdown",
        "mode": "normal",
        "pausedCount": 0,
        "endReason": "finished",
    }
    row.update(kw)
    return row


def _task(tid: str, name: str, updated: int, **kw) -> dict:
    row = {
        "id": tid,
        "name": name,
        "category": "数学",
        "color": "#c8892a",
        "archived": False,
        "createdAt": updated - 10_000,
        "lastUsedAt": updated,
        "updatedAt": updated,
    }
    row.update(kw)
    return row


# ==================== 服务层 ====================


def test_session_upsert_is_idempotent(monkeypatch):
    """同一批记录推两次，库里只能有一条 —— 断网重试靠的就是这个。"""
    _prep_db(monkeypatch, "idem")
    rows = [_session("s_1", 1_700_000_000_000), _session("s_2", 1_700_000_100_000)]

    first = focus.upsert_sessions("u_a", rows)
    assert first == {"accepted": 2, "received": 2}

    second = focus.upsert_sessions("u_a", rows)
    assert second["received"] == 2
    assert second["accepted"] == 0, "重复上报不应再写入"

    got = focus.list_sessions("u_a")
    assert len(got) == 2
    assert {r["id"] for r in got} == {"s_1", "s_2"}


def test_session_upsert_drops_bad_rows(monkeypatch):
    _prep_db(monkeypatch, "dirty")
    rows = [
        _session("s_ok", 1_700_000_000_000),
        {"id": "", "startedAt": 1, "endedAt": 2},          # 没有 id
        {"id": "s_no_time", "taskName": "无时间"},           # 没有时间戳
        "not-a-dict",
        _session("s_neg", 1_700_000_200_000, seconds=-100),  # 负时长夹到 0
    ]
    res = focus.upsert_sessions("u_dirty", rows)
    assert res["received"] == 2  # 只有两条合格
    got = {r["id"]: r for r in focus.list_sessions("u_dirty")}
    assert set(got) == {"s_ok", "s_neg"}
    assert got["s_neg"]["seconds"] == 0


def test_session_owner_isolation(monkeypatch):
    _prep_db(monkeypatch, "iso")
    focus.upsert_sessions("owner_a", [_session("s_shared", 1_700_000_000_000)])

    # B 用同一个 id 上报：不能被写进去，也看不到 A 的记录
    res = focus.upsert_sessions("owner_b", [_session("s_shared", 1_700_000_000_000)])
    assert res["accepted"] == 0
    assert focus.list_sessions("owner_b") == []

    # A 的记录仍在
    rows = focus.list_sessions("owner_a")
    assert len(rows) == 1 and rows[0]["id"] == "s_shared"


def test_session_range_filter(monkeypatch):
    _prep_db(monkeypatch, "range")
    base = 1_700_000_000_000
    focus.upsert_sessions(
        "u_r",
        [
            _session("s_a", base),
            _session("s_b", base + 86_400_000),
            _session("s_c", base + 2 * 86_400_000),
        ],
    )
    got = focus.list_sessions("u_r", base, base + 86_400_000)
    assert [r["id"] for r in got] == ["s_a"], "区间左闭右开"

    all_rows = focus.list_sessions("u_r")
    assert len(all_rows) == 3
    assert all_rows[0]["startedAt"] <= all_rows[-1]["startedAt"], "按时间升序"


def test_task_last_write_wins(monkeypatch):
    """updatedAt 更小的上报不能盖掉服务端更新的版本。"""
    _prep_db(monkeypatch, "lww")
    focus.upsert_tasks("u_t", [_task("t_1", "高等数学", 2_000)])

    # 旧数据回灌（比如另一台设备存着改名前的版本）
    focus.upsert_tasks("u_t", [_task("t_1", "旧名字", 1_000)])
    assert focus.list_tasks("u_t")[0]["name"] == "高等数学"

    # 新数据（改名）
    focus.upsert_tasks("u_t", [_task("t_1", "高数（上）", 3_000)])
    assert focus.list_tasks("u_t")[0]["name"] == "高数（上）"


def test_task_owner_isolation_and_patch(monkeypatch):
    _prep_db(monkeypatch, "task_iso")
    focus.upsert_tasks("owner_x", [_task("t_x", "A 的任务", 1_000)])

    # B 用同 id 上报更高 updatedAt，也不能改 A 的行
    focus.upsert_tasks("owner_y", [_task("t_x", "被 B 改过", 9_999)])
    assert focus.list_tasks("owner_x")[0]["name"] == "A 的任务"
    assert focus.list_tasks("owner_y") == []

    # PATCH 只能改自己的
    assert focus.patch_task("owner_y", "t_x", {"name": "黑客"}) is None
    patched = focus.patch_task("owner_x", "t_x", {"name": "A 的新名字", "archived": True})
    assert patched is not None
    assert patched["name"] == "A 的新名字"
    assert patched["archived"] is True
    assert patched["updatedAt"] >= 1_000


def test_task_patch_rejects_unknown_fields(monkeypatch):
    _prep_db(monkeypatch, "patch_empty")
    focus.upsert_tasks("u_p", [_task("t_p", "任务", 1_000)])
    assert focus.patch_task("u_p", "t_p", {"owner_id": "hacker"}) is None


# ==================== HTTP 层 ====================


def _login(c: TestClient, username: str, password: str = "pw123456") -> str:
    c.post("/api/auth/register/", json={"username": username, "password": password})
    r = c.post("/api/auth/login/", json={"username": username, "password": password})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["id"]


def test_http_requires_login(monkeypatch):
    _prep_db(monkeypatch, "http_auth")
    with TestClient(main.app) as c:
        assert c.get(f"{BASE}/sessions").status_code == 401
        assert c.get(f"{BASE}/tasks").status_code == 401
        assert c.post(f"{BASE}/sessions", json={"sessions": []}).status_code == 401


def test_http_roundtrip(monkeypatch):
    """两个「设备」同账户：A 推上去，B 拉下来，看到的是同一份。"""
    _prep_db(monkeypatch, "http_round")
    with TestClient(main.app) as a:
        _login(a, "focus_round")
        rows = [_session("s_http_1", 1_700_000_000_000), _session("s_http_2", 1_700_000_050_000)]
        pushed = a.post(f"{BASE}/sessions", json={"sessions": rows})
        assert pushed.status_code == 200, pushed.text
        assert pushed.json()["accepted"] == 2

        a.post(f"{BASE}/tasks", json={"tasks": [_task("t_http", "线性代数", 1_500)]})

    # 同一账号的另一个客户端（新的 TestClient，共用同一个临时库与 cookie 会话）
    with TestClient(main.app) as b:
        _login(b, "focus_round")
        got = b.get(f"{BASE}/sessions").json()["sessions"]
        assert len(got) == 2
        assert got[0]["taskName"] == "高等数学"
        assert got[0]["syncedAt"] is not None

        tasks = b.get(f"{BASE}/tasks").json()["tasks"]
        assert len(tasks) == 1 and tasks[0]["name"] == "线性代数"

        # 再推一遍同一批：不重复计
        again = b.post(f"{BASE}/sessions", json={"sessions": [_session("s_http_1", 1_700_000_000_000)]})
        assert again.json()["accepted"] == 0
        assert len(b.get(f"{BASE}/sessions").json()["sessions"]) == 2


def test_http_rejects_bad_body(monkeypatch):
    _prep_db(monkeypatch, "http_bad")
    with TestClient(main.app) as c:
        _login(c, "focus_bad")
        assert c.post(f"{BASE}/sessions", json={"sessions": "nope"}).status_code == 400
        assert c.post(f"{BASE}/tasks", json={"tasks": 1}).status_code == 400

        too_many = [{"id": f"s_{i}", "startedAt": 1, "endedAt": 2} for i in range(focus.MAX_BATCH + 1)]
        assert c.post(f"{BASE}/sessions", json={"sessions": too_many}).status_code == 400


def test_http_patch_missing_task(monkeypatch):
    _prep_db(monkeypatch, "http_patch")
    with TestClient(main.app) as c:
        _login(c, "focus_patch")
        r = c.patch(f"{BASE}/tasks/t_not_exist", json={"name": "x"})
        assert r.status_code == 404


def test_http_no_trailing_slash_redirect(monkeypatch):
    """带尾斜杠与不带都要直接命中 200，不能 3xx —— 项目踩过的重定向黑洞。"""
    _prep_db(monkeypatch, "http_slash")
    with TestClient(main.app) as c:
        _login(c, "focus_slash")
        for path in (f"{BASE}/sessions", f"{BASE}/sessions/", f"{BASE}/tasks", f"{BASE}/tasks/"):
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 200, (path, r.status_code)
