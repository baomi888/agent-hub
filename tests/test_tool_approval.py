"""L4 真实人工确认：一次性 token 的绑定 / 过期 / 用后即焚 / 跨进程。

三层分工：

1. **语义层**（`test_model_repeating_same_args_is_still_rejected` 等）
   核心断言是**模型自证不成立**：`download_file` 这类写外部状态的工具，
   模型不带 token 时第一次被拦、**同参数再调一次仍然被拦**，全程不执行。
   旧实现「同参数再调一次 + hash 命中就放行」在这里会失败——所以这几条不是
   为了覆盖代码，而是为了锁死语义。

2. **存储层（D-1，2026-11-17）**
   原来是三个进程内 dict，生产 `uvicorn --workers N` 下「触发 → 点击 → 重发」
   三个请求落在三个 worker，端到端成功率约 1/16。现在状态在 SQLite，
   所以这里必须有**真跨进程**的用例：起两个独立解释器，A 登记、B 签发、
   C 消费。同进程内 reload 模块**证明不了**这件事。

3. **接线层**
   `POST /api/chat/approval` 真存在、真按 uid 隔离、Agent 流真会吐
   `event: approval` 帧（用假 agent 从 configurable 里读 turn_id 反证接线：
   如果 `turn_id` 没注入，假 agent 就登记不到这一轮，帧也就不会出现）。

覆盖不到的地方（如实说明）：`--workers 4` 真起 uvicorn 的压测没做，
线上多 worker 的验证靠 `tools/` 下的手工取证脚本。这里用「独立进程 + 同一库」
锁住同一个不变量（状态跨进程可见），比压测更容易复现。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import agent.builder as builder  # noqa: E402
import agent.tools as agent_tools  # noqa: E402
import api.chat as chat  # noqa: E402
import core.session as session  # noqa: E402
import main as m  # noqa: E402
from core import approval_store, config, tool_guard  # noqa: E402
from core.auth import get_current_user  # noqa: E402

ARGS = {"url": "https://example.com/a.pdf"}
OWNER = "u_appr"
SID = "conv_appr"


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    """每个用例一个独立的库：不碰本地 data/sessions.db，也不跨用例串状态。"""
    monkeypatch.setattr(session, "_db_path", lambda: str(tmp_path / "sessions.db"))
    yield


def _config(owner: str = "u1", turn: str = "t1", token: str = "") -> dict:
    return {
        "config": {
            "configurable": {
                "owner_id": owner,
                "turn_id": turn,
                "approval_token": token,
            }
        }
    }


def _run(calls: list[str], args: dict | None = None, cfg: dict | None = None) -> str:
    """直接调 guarded_run，绕开真实下载：只关心守卫是否放行。"""

    def fake_download(*a, **kw):
        calls.append("run")
        return "下载成功"

    return tool_guard.guarded_run(
        "download_file",
        fake_download,
        args or ARGS,
        None,
        (),
        cfg or _config(),
    )


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setattr(config, "TOOL_APPROVAL_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_APPROVAL_REAL_CONFIRM_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_REPEAT_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "TOOL_APPROVAL_TTL_SECONDS", 120)
    approval_store.reset()


# ==================== 1. 语义层 ====================


def test_model_repeating_same_args_is_still_rejected():
    """L4 核心断言：模型「同参数再调一次」仍然被拒，且一次都没执行。"""
    calls: list[str] = []

    with tool_guard.guard_scope():
        first = _run(calls)  # 模型第一次调用
        retry = _run(calls)  # 模型自己再用完全相同的参数调一次

    assert calls == [], "模型未获人工确认，任何一次都不该真的执行"
    assert "本次未执行" in first
    assert "本次未执行" in retry

    pending = approval_store.pending_for_turn("u1", "t1")
    assert len(pending) == 1, "同参数重试应复用同一条待确认请求，而不是刷屏"
    assert not approval_store.has_active_token(
        pending[0].request_id
    ), "模型没有点击权，不该有 token"


def test_human_click_issues_single_use_token_and_executes_once():
    calls: list[str] = []

    with tool_guard.guard_scope():
        _run(calls)
        pending = approval_store.pending_for_turn("u1", "t1")[0]

    issued = approval_store.issue_token(pending.request_id, "u1")
    assert issued is not None
    token = issued["approval_token"]
    assert issued["turn_id"] == "t1"
    assert issued["args_hash"] == approval_store.args_hash(ARGS)

    with tool_guard.guard_scope():
        ok = _run(calls, cfg=_config(token=token))
        replay = _run(calls, cfg=_config(token=token))

    assert ok == "下载成功"
    assert len(calls) == 1
    assert "本次未执行" in replay
    assert len(calls) == 1, "token 用后即焚，重放不得再执行"


@pytest.mark.parametrize(
    "owner,turn,args",
    [
        ("u2", "t1", ARGS),  # 换用户
        ("u1", "t2", ARGS),  # 换轮次
        ("u1", "t1", {"url": "https://example.com/b.pdf"}),  # 换参数
    ],
)
def test_token_is_bound_to_user_tool_args_turn(owner, turn, args):
    calls: list[str] = []
    with tool_guard.guard_scope():
        _run(calls)
        pending = approval_store.pending_for_turn("u1", "t1")[0]
    token = approval_store.issue_token(pending.request_id, "u1")["approval_token"]

    with tool_guard.guard_scope():
        out = _run(calls, args=args, cfg=_config(owner=owner, turn=turn, token=token))

    assert out != "下载成功"
    assert "本次未执行" in out
    assert calls == []


def test_token_expires(monkeypatch):
    monkeypatch.setattr(config, "TOOL_APPROVAL_TTL_SECONDS", 1)
    calls: list[str] = []
    with tool_guard.guard_scope():
        _run(calls)
        pending = approval_store.pending_for_turn("u1", "t1")[0]
    token = approval_store.issue_token(pending.request_id, "u1")["approval_token"]
    time.sleep(1.1)
    with tool_guard.guard_scope():
        out = _run(calls, cfg=_config(token=token))
    assert out != "下载成功"
    assert "本次未执行" in out
    assert calls == []


def test_issue_token_requires_same_user():
    with tool_guard.guard_scope():
        _run([])
        pending = approval_store.pending_for_turn("u1", "t1")[0]
    assert approval_store.issue_token(pending.request_id, "u2") is None
    assert approval_store.issue_token("does-not-exist", "u1") is None


def test_read_only_tools_keep_metadata_only_guard():
    """高风险分级：写外部状态的走强确认，只读的不走人工确认。"""
    from core.tool_registry import TOOL_META

    assert TOOL_META["download_file"].needs_approval is True
    for name in ("kb_search", "web_search", "get_weather"):
        assert TOOL_META[name].needs_approval is False

    calls: list[str] = []

    def fake_search(*a, **kw):
        calls.append("run")
        return "检索结果"

    cfg = {"config": {"configurable": {"owner_id": "u1", "turn_id": "t1"}}}
    with tool_guard.guard_scope():
        out = tool_guard.guarded_run("kb_search", fake_search, {"query": "x"}, None, (), cfg)
    assert out == "检索结果"
    assert calls == ["run"]


def test_strict_switch_off_falls_back_to_legacy_confirm_string(monkeypatch):
    """关闭严格开关 = 回到旧「模型带确认串再调一次」行为，用于 A/B 对照。

    这条测试同时说明：新旧差异是真实存在的，不是把同一段代码换个名字。
    """
    monkeypatch.setattr(config, "TOOL_APPROVAL_REAL_CONFIRM_ENABLED", False)
    calls: list[str] = []

    with tool_guard.guard_scope():
        first = _run(calls)
        assert "确认串" in first
        assert len(calls) == 0, "旧模式第一次也不执行（回确认串）"
        second = _run(calls)

    assert second == "下载成功", "旧模式下模型自证可通过——这正是要替换掉的行为"
    assert len(calls) == 1


# ==================== 2. 存储层：D-1 跨进程 ====================

_XPROC = r'''
import json, os, sys
session_db = os.environ["APPR_TEST_DB"]
from core import session
session._db_path = lambda: session_db
from core import approval_store as a

ARGS = {"url": "https://example.com/a.pdf"}
HASH = a.args_hash(ARGS)
step = sys.argv[1]

if step == "create":
    p = a.create_pending("u1", "download_file", ARGS, os.environ.get("APPR_TURN", "t1"))
    print(json.dumps({"request_id": p.request_id, "user": p.user}))
elif step == "create2":
    # 第二个进程用完全相同的参数再登记一次：必须拿到同一个 request_id
    p = a.create_pending("u1", "download_file", ARGS, os.environ.get("APPR_TURN", "t1"))
    print(json.dumps({"request_id": p.request_id}))
elif step == "issue":
    out = a.issue_token(sys.argv[2], "u1")
    print(json.dumps(out))
elif step == "consume":
    print(json.dumps({"ok": a.consume(sys.argv[2], "u1", "download_file", HASH, sys.argv[3])}))
elif step == "pending":
    print(json.dumps({"n": len(a.pending_for_turn("u1", sys.argv[2]))}))
elif step == "rows":
    from core import session as s
    with s._get_conn() as conn:
        print(json.dumps({"n": conn.execute("SELECT COUNT(*) c FROM tool_approvals").fetchone()["c"]}))
'''


def _proc(tmp_path: Path, db: str, *args: str, turn: str = "t1") -> dict:
    """起一个**独立解释器**执行一步，返回解析后的 JSON。"""
    script = tmp_path / "_xproc_approval.py"
    if not script.exists():
        script.write_text(_XPROC, encoding="utf-8")
    env = dict(os.environ)
    env["APPR_TEST_DB"] = db
    env["APPR_TURN"] = turn
    env["PYTHONPATH"] = ROOT
    r = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=120,
    )
    assert r.returncode == 0, f"子进程失败：{r.stderr[-2000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_three_requests_in_three_processes_share_state(tmp_path):
    """D-1 正题：登记 / 签发 / 消费分别落在三个独立进程，全链路必须通。

    进程内 dict 版在这里**第一步就断**：进程 B 的 `issue_token` 看不到
    进程 A 写的 pending，直接返回 None（线上表现是 404）。
    """
    db = str(tmp_path / "shared.db")

    created = _proc(tmp_path, db, "create")
    rid = created["request_id"]

    # 另一个进程看得到同一轮 pending
    assert _proc(tmp_path, db, "pending", "t1")["n"] == 1

    issued = _proc(tmp_path, db, "issue", rid)
    assert issued and issued.get("approval_token"), "跨进程必须能签发出 token"
    assert issued["turn_id"] == "t1"
    token = issued["approval_token"]

    # 消费在第三个进程；重放必须 False
    assert _proc(tmp_path, db, "consume", token, "t1")["ok"] is True
    assert _proc(tmp_path, db, "consume", token, "t1")["ok"] is False
    assert _proc(tmp_path, db, "pending", "t1")["n"] == 0


def test_pending_is_deduped_across_processes(tmp_path):
    """两个进程用同参数重试：只能有一条 pending（否则用户看到两张一样的卡）。"""
    db = str(tmp_path / "dedupe.db")
    a = _proc(tmp_path, db, "create")
    b = _proc(tmp_path, db, "create2")
    assert a["request_id"] == b["request_id"]
    assert _proc(tmp_path, db, "rows")["n"] == 1


def test_expired_rows_are_deleted_not_just_filtered(monkeypatch, tmp_path):
    """过期不是"过滤掉"，而是真删：否则表会无限长。

    `pending_for_turn` 是读路径、只过滤不删；真正的 DELETE 挂在写路径上
    （下一次 create/issue/consume 顺手清）。所以这里用一个写操作触发它。
    """
    monkeypatch.setattr(config, "TOOL_APPROVAL_TTL_SECONDS", 1)
    first = approval_store.create_pending("u1", "download_file", ARGS, "t1")
    assert approval_store.get_pending(first.request_id) is not None
    time.sleep(1.1)
    # 读路径：不返回、但行还在
    assert approval_store.get_pending(first.request_id) is None
    assert approval_store.pending_for_turn("u1", "t1") == []
    # 写路径：顺手删掉
    approval_store.create_pending("u1", "download_file", {"url": "https://e.com/b.pdf"}, "t2")
    with session._get_conn() as conn:
        left = conn.execute(
            "SELECT COUNT(*) AS c FROM tool_approvals WHERE request_id = ?", (first.request_id,)
        ).fetchone()["c"]
    assert left == 0, "过期行必须在写路径被真的删除"


def test_consume_leaves_no_trace(monkeypatch):
    """消费成功后 token 与 pending 一起消失（用后即焚，不留可重放的残骸）。"""
    approval_store.create_pending("u1", "download_file", ARGS, "t1")
    rid = approval_store.pending_for_turn("u1", "t1")[0].request_id
    token = approval_store.issue_token(rid, "u1")["approval_token"]
    ah = approval_store.args_hash(ARGS)
    assert approval_store.consume(token, "u1", "download_file", ah, "t1") is True
    assert approval_store.consume(token, "u1", "download_file", ah, "t1") is False
    with session._get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM tool_approvals").fetchone()["c"]
    assert n == 0, "消费成功后 pending 与 token 都不该留痕"


def test_concurrent_consume_only_one_wins():
    """8 个线程同时带同一 token 消费：只能有一个 True（工具只能执行一次）。

    这条针对的是「先 SELECT 判断、再 DELETE」的实现：两个并发请求都读到这一行、
    都返回 True，下载就会跑两遍。正确的写法是一条
    `DELETE ... WHERE token=?` 再按 rowcount 判真假 —— 删与判是同一条语句。
    实现正确时这条**恒**为 1，不会有偶发红；实现错了才有概率被抓住。
    """
    import threading

    approval_store.create_pending("u1", "download_file", ARGS, "t1")
    rid = approval_store.pending_for_turn("u1", "t1")[0].request_id
    token = approval_store.issue_token(rid, "u1")["approval_token"]
    ah = approval_store.args_hash(ARGS)

    results: list[bool] = []
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()  # 尽量让 8 个线程同时到达 consume
        ok = approval_store.consume(token, "u1", "download_file", ah, "t1")
        with guard:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 8
    assert sum(1 for r in results if r) == 1, f"token 只能被消费一次，实际放行 {results.count(True)} 次"


# ==================== 3. 接线层：HTTP ====================


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "_db_path", lambda: str(tmp_path / "api.db"))
    session._init_db()
    monkeypatch.setattr(chat.store, "get_conversation", lambda uid, sid: {"id": sid, "kb_id": None})
    monkeypatch.setattr(chat.store, "count_messages", lambda uid, sid: 1)
    monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: [])
    monkeypatch.setattr(chat.store, "add_message", lambda *a, **k: 1000)
    monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_generate_title", lambda q: "固定标题")
    monkeypatch.setattr(config, "USER_TAG_HOOK_ENABLED", False)
    monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)
    m.app.dependency_overrides[get_current_user] = lambda: OWNER
    with TestClient(m.app) as c:
        yield c
    m.app.dependency_overrides.clear()


def test_route_exists_and_is_uid_scoped(client):
    """端点是真端点：别人的 request_id 必须 404，不能签出 token。"""
    assert any(getattr(r, "path", "") == "/approval" for r in chat.router.routes)

    pending = approval_store.create_pending(OWNER, "download_file", ARGS, "t1")
    r = client.post("/api/chat/approval", json={"request_id": pending.request_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approval_token"].startswith("apr:")
    assert body["turn_id"] == "t1"

    other = approval_store.create_pending("someone_else", "download_file", ARGS, "t1")
    r2 = client.post("/api/chat/approval", json={"request_id": other.request_id})
    assert r2.status_code == 404, "不能给别人的 pending 签发 token"
    assert client.post("/api/chat/approval", json={"request_id": "nope-nope"}).status_code == 404


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeAgent:
    """从 configurable 里读 turn_id 再登记 pending —— 用它反证接线。"""

    def __init__(self, seen: dict):
        self.seen = seen

    async def astream(self, messages, config=None, stream_mode=None):
        cfg = (config or {}).get("configurable") or {}
        self.seen.update(cfg)
        turn_id = str(cfg.get("turn_id") or "")
        if turn_id:
            approval_store.create_pending(
                str(cfg.get("owner_id") or ""), "download_file", ARGS, turn_id
            )
        yield ("messages", (_Chunk("这个文件需要你确认后才能下载。"), {}))


def _frames(body: str) -> list[tuple[str, dict]]:
    out = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        name = ""
        data = ""
        for line in raw.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if name:
            out.append((name, json.loads(data) if data else {}))
    return out


def test_agent_stream_emits_approval_frame(client, monkeypatch):
    """Agent 流收尾必须吐 `approval` 帧，且带前端重发所需的 question / turn_id。

    灯下黑防具：假 agent 是**从 configurable 里取** turn_id 才登记成功的。
    如果有人把 `"turn_id": turn_id` 从 configurable 里删掉，
    假 agent 拿不到 → 登记不到这一轮 → 前端一帧都收不到 → 这条立即红。
    """
    seen: dict = {}
    monkeypatch.setattr(builder, "build_agent", lambda *a, **k: _FakeAgent(seen))
    # 不联网：`mode=agent` 映射出 use_web=True，会先跑一轮服务端预取。
    # 这里只关心接线，预取桩成"没搜到"（生产里它失败也是这条降级路径）。
    monkeypatch.setattr(agent_tools, "prefetch_web_sources", lambda *a, **k: ("", 0))

    r = client.post(
        "/api/chat/stream",
        json={
            "sid": SID,
            "question": "帮我把这个文件下载下来",
            "mode": "agent",
            "turn_id": "turn-e2e",
        },
    )
    assert r.status_code == 200, r.text
    frames = _frames(r.text)

    assert seen.get("turn_id") == "turn-e2e", "turn_id 必须由服务端注入 configurable"
    assert "approval_token" in seen, "approval_token 也走同一条注入路径"

    approval = [d for n, d in frames if n == "approval"]
    assert len(approval) == 1, f"应恰好一帧确认请求，实际帧序：{[n for n, _ in frames]}"
    ev = approval[0]
    assert ev["tool_name"] == "download_file"
    assert ev["turn_id"] == "turn-e2e"
    assert ev["question"] == "帮我把这个文件下载下来"
    assert ev["request_id"], "前端点确认时要用它换 token"
    assert "approval_token" not in ev, "SSE 里绝不能下发 token（模型就在这条流的另一端）"

    # 前端拿 request_id 换 token 的那一步，用真端点走一遍
    got = client.post("/api/chat/approval", json={"request_id": ev["request_id"]})
    assert got.status_code == 200
    assert got.json()["approval_token"].startswith("apr:")


def test_approval_frame_absent_when_strict_mode_off(client, monkeypatch):
    """反向对照：严格模式关闭时不该出现 confirmation 帧（避免"永远等不到点击"）。"""
    monkeypatch.setattr(config, "TOOL_APPROVAL_REAL_CONFIRM_ENABLED", False)
    seen: dict = {}
    monkeypatch.setattr(builder, "build_agent", lambda *a, **k: _FakeAgent(seen))
    monkeypatch.setattr(agent_tools, "prefetch_web_sources", lambda *a, **k: ("", 0))
    r = client.post(
        "/api/chat/stream",
        json={"sid": SID, "question": "下载", "mode": "agent"},
    )
    assert r.status_code == 200
    assert [n for n, _ in _frames(r.text) if n == "approval"] == []
