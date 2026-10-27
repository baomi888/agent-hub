"""A3 回归：多 worker 下限流不能变成 N 倍（2026-10-20 审计修复）。

修复前的机制：`quota._HITS` 是模块级字典。`uvicorn --workers 4` fork 出 4 个进程，
各自一份滑窗，`RATE_PER_MINUTE=10` 的实际放行量变成 **40/min**。
不报错、日志干净——而 `--workers 4` 正是压测报告给出的 P0 建议，
也就是说"不修这个问题，第一个 P0 优化会静默拆掉一道护栏"。

本测试不真起多个进程，而是直接模拟"别的 worker 已经消耗了额度"：
往 `rate_buckets` 写秒桶行，等价于另一个进程记的账。这样能精确控制场景，
也不会因为进程调度而 flaky。

锁死五件事：
  1) 别的 worker 的额度算进全局（共享开关开时）；
  2) 共享开关关掉 → 完全退回旧的单层行为（证明开关是真的）；
  3) 本进程窗口满了照样拒（本地这道没被削弱）；
  4) 账本读不到时退回单层、不抛非预期异常；
  5) 记账按秒桶而不是每请求一行（写放大是这次设计最在意的代价）。
"""

from __future__ import annotations

import time

import pytest

from core import config, quota, session


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时库 + 干净的进程内状态。"""
    path = str(tmp_path / "rate.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    quota._ensure_rate_table()
    # 管理员名单清空：默认值是真实项目作者账号，会影响 _is_admin 判定
    monkeypatch.setattr(config, "ADMIN_USERNAMES", [])
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 5)
    monkeypatch.setattr(config, "RATE_LIMIT_SHARED", True)
    for d in (quota._HITS, quota._pending, quota._last_flush, quota._SHARED_CACHE):
        d.clear()
    yield
    for d in (quota._HITS, quota._pending, quota._last_flush, quota._SHARED_CACHE):
        d.clear()


def _other_worker_used(uid: str, n: int) -> None:
    """模拟另一个 worker 在同一窗口内放行了 n 次。"""
    sec = int(time.time())
    with session._get_conn() as conn:
        conn.execute(
            "INSERT INTO rate_buckets (user_id, sec, n) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, sec) DO UPDATE SET n = n + excluded.n",
            (uid, sec, n),
        )


def test_other_worker_count_is_honoured(env):
    """别的 worker 用掉了全额度 → 本进程第一次就该被拒。

    修复前：本进程滑窗是空的，会老老实实放行 5 次，全局变成 10 次。
    """
    _other_worker_used("u1", 5)
    with pytest.raises(quota.RateLimited):
        quota.hit("u1")


def test_partial_usage_from_other_worker_shrinks_budget(env):
    """别的 worker 用了 3 个，本进程只剩 2 个额度（而不是 5 个）。"""
    _other_worker_used("u1", 3)
    assert quota.hit("u1") == 1
    assert quota.hit("u1") == 2
    with pytest.raises(quota.RateLimited):
        quota.hit("u1")


def test_shared_switch_off_restores_single_layer(env, monkeypatch):
    """开关关掉后必须回到旧行为：看不见别的 worker 的账。"""
    monkeypatch.setattr(config, "RATE_LIMIT_SHARED", False)
    _other_worker_used("u1", 5)
    for i in range(5):
        assert quota.hit("u1") == i + 1
    with pytest.raises(quota.RateLimited):
        quota.hit("u1")


def test_local_window_still_hard_caps(env):
    """本地这道不能被共享判定替代：它才是单进程下的主防线。"""
    for i in range(5):
        assert quota.hit("u1") == i + 1
    with pytest.raises(quota.RateLimited):
        quota.hit("u1")
    # 本地满了以后，即使账本被清空也不放行
    with session._get_conn() as conn:
        conn.execute("DELETE FROM rate_buckets")
    quota._SHARED_CACHE.clear()
    with pytest.raises(quota.RateLimited):
        quota.hit("u1")


def test_ledger_error_degrades_instead_of_raising(env, monkeypatch):
    """账本读不到 → 退回单层（本地未满就放行），不能把异常漏出去。

    方向要清楚：全局查询的作用是**加严**。读不到就不加严。
    反过来（读不到就拒）会让限流器自己变成故障源。
    """
    quota._SHARED_CACHE.clear()

    def boom(*_a, **_kw):
        raise RuntimeError("sqlite unavailable")

    monkeypatch.setattr(quota, "_get_conn", boom)
    assert quota.hit("u1") == 1  # 不抛


def test_writes_are_bucketed_not_per_request(env):
    """记账必须是秒桶，不能每请求一行——否则限流自己成了写入热点。"""
    for _ in range(5):
        quota.hit("u1")
    with session._get_conn() as conn:
        rows = conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(n),0) AS s FROM rate_buckets").fetchone()
    assert rows["c"] == 1, "同一秒内的多次放行应合并成一行"
    assert rows["s"] >= 1


def test_unlimited_when_limit_is_zero(env, monkeypatch):
    """limit=0 表示不限：这条既有语义不能被新逻辑改掉。"""
    monkeypatch.setattr(config, "RATE_PER_MINUTE", 0)
    _other_worker_used("u1", 999)
    for _ in range(20):
        quota.hit("u1")
