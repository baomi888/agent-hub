"""路由统计（route_stats）：口径、聚合、接线。

为什么这组用例值得写
--------------------
"workflow 占多少"这个问题有**三个都能算出来的答案**，算错不会报错，只会给出
一个看起来合理的数：

  1. 拿 `metrics.chat_total{path}` 算 → 那是执行分支，不是路由判定；
  2. 拿全部问答做分母 → fastpath（显式 workflow_id / 两个开关都关）把比例稀释；
  3. 把 `workflow_no_def`（判了 workflow 但缺定义）算进 agent → 读成"路由偏好",
     实际是部署缺数据。

所以断言的重点不是"函数能跑"，而是**这几个口径各自落在哪个数上**。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import route_stats
from core import session as store

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    """每个用例一个空库：route_stats 建表跟着库路径走，这里验证它真的跟着走。"""
    db = tmp_path / "t.db"
    monkeypatch.setattr(store, "_db_path", lambda: str(db))
    route_stats._TABLES_PATH = None
    yield
    route_stats._TABLES_PATH = None


def test_record_and_summary_basic():
    route_stats.record("hybrid", "workflow", "fields:model")
    route_stats.record("hybrid", "workflow", "fields:model")
    route_stats.record("agent", "agent", "fields:model")
    route_stats.record("llm", "agent", "fields:fastpath:no_tools")

    s = route_stats.summary(days=1)
    assert s["workflow"] == 2
    assert s["agent"] == 1
    # fastpath 不进分母
    assert s["routed"] == 3
    assert s["workflow_pct"] == pytest.approx(66.7)
    # 但它进 total
    assert s["total"] == 4
    assert s["workflow_pct_all"] == pytest.approx(50.0)


def test_fastpath_is_not_counted_as_routed():
    """口径核心：没问模型的请求不能进分母。"""
    for _ in range(9):
        route_stats.record("llm", "agent", "fields:fastpath:no_tools")
    route_stats.record("hybrid", "workflow", "fields:model")

    s = route_stats.summary(days=1)
    assert s["routed"] == 1
    assert s["workflow_pct"] == 100.0, "fastpath 混进分母会把它稀释成 10%"
    assert s["total"] == 10


def test_fallback_counted_separately():
    route_stats.record("agent", "agent", "fields:fallback")
    route_stats.record("agent", "agent", "fields:model")
    route_stats.record("hybrid", "workflow", "fields:model")

    s = route_stats.summary(days=1)
    assert s["routed"] == 3
    assert s["fallback"] == 1
    assert s["fallback_pct"] == pytest.approx(33.3)


def test_workflow_no_def_is_not_mixed_into_agent():
    """判了 workflow 但缺定义 → 单独一档，不能被读成"路由偏爱 agent"。"""
    route_stats.record("hybrid", "workflow", "fields:model")
    route_stats.record("hybrid", route_stats.ROUTE_WF_NO_DEF, "fields:model")

    s = route_stats.summary(days=1)
    assert s["workflow"] == 1
    assert s["agent"] == 0, "降级事件不是 agent 判定"
    assert s["no_def"] == 1
    assert s["workflow_pct"] == 100.0


def test_by_day_totals_match_global_total():
    """逐日合计必须等于全局合计。

    这里防的是一个真实踩过的坑：`workflow_no_def` 分支原先在"逐日 total += n"
    之前就 continue，导致全局 total 含它、逐日合计不含它——两个数都对不上，
    而且不报错。"""
    route_stats.record("hybrid", "workflow", "fields:model", day="2026-11-16")
    route_stats.record("hybrid", route_stats.ROUTE_WF_NO_DEF, "fields:model", day="2026-11-16")
    route_stats.record("llm", "agent", "fields:fastpath:no_tools", day="2026-11-17")

    s = route_stats.summary(days=2, end="2026-11-17")
    assert s["total"] == 3
    assert sum(d["total"] for d in s["by_day"]) == s["total"]
    assert s["no_def"] == 1


def test_empty_db_returns_zeroes_not_crash():
    s = route_stats.summary(days=7)
    assert s["total"] == 0
    assert s["routed"] == 0
    assert s["workflow_pct"] == 0.0, "除零要变成 0，不能抛"
    assert len(s["by_day"]) == 7, "缺失日必须补零，否则前端画不出连续曲线"


def test_by_day_is_ascending_and_zero_filled():
    route_stats.record("hybrid", "workflow", "fields:model")
    s = route_stats.summary(days=3)
    days = [d["day"] for d in s["by_day"]]
    assert days == sorted(days)
    assert len(days) == 3
    assert s["by_day"][-1]["workflow"] == 1


def test_table_follows_db_path_switch(tmp_path, monkeypatch):
    """换库要能重建表（quota.py 同一个坑：只在 import 时建一次会 no such table）。"""
    route_stats.record("hybrid", "workflow", "fields:model")
    db2 = tmp_path / "other.db"
    monkeypatch.setattr(store, "_db_path", lambda: str(db2))
    # 不手动清 _TABLES_PATH：靠路径比较自动重建
    route_stats.record("agent", "agent", "fields:model")
    assert route_stats.summary(days=1)["routed"] == 1, "新库只该有刚写的那一条"


def test_record_swallows_errors(monkeypatch):
    """统计失败绝不能影响业务请求。"""

    def boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(route_stats, "_ensure_tables", boom)
    route_stats.record("hybrid", "workflow", "fields:model")  # 不抛即通过


def test_chat_wires_route_stats():
    """接线断言：只测 route_stats 本身证明不了它在生产路径上被调用过。

    这与 C-001（guard_scope 从未被调用）是同一类坑：模块写对了、测试全绿，
    但生产代码里根本没人调。
    """
    src = (REPO / "api" / "chat.py").read_text(encoding="utf-8")
    assert "route_stats.record" in src, "api/chat.py 没有调用 route_stats.record"
    assert "ROUTE_WF_NO_DEF" in src, "workflow 降级事件没有被单独记录"

    tree = ast.parse(src)
    # 调用点是 `asyncio.to_thread(route_stats.record, ...)`：`record` 在这里是
    # 一个**属性引用**（作为参数传进去），不是 `Call.func`，所以要数 Attribute 节点。
    refs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr == "record"
        and isinstance(n.value, ast.Name)
        and n.value.id == "route_stats"
    ]
    assert len(refs) >= 2, "应有两处：常规记录 + 降级记录"


def test_report_tool_runs_offline(tmp_path, monkeypatch, capsys):
    """工具要能在只有 DB 副本的机器上跑（不 import 项目模块也能出数）。"""
    import importlib.util

    db = tmp_path / "copy.db"
    monkeypatch.setattr(store, "_db_path", lambda: str(db))
    route_stats._TABLES_PATH = None
    route_stats.record("hybrid", "workflow", "fields:model")
    route_stats.record("agent", "agent", "fields:model")

    path = REPO / "tools" / "route_stats_report.py"
    spec = importlib.util.spec_from_file_location("route_stats_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    assert mod.main(["--db", str(db), "--days", "1"]) == 0
    out = capsys.readouterr().out
    assert "50.0%" in out
    assert "workflow" in out


def test_report_tool_rejects_missing_table(tmp_path, capsys):
    """空库要给出人能看懂的提示，而不是抛 sqlite3 异常。"""
    import importlib.util
    import sqlite3

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()

    path = REPO / "tools" / "route_stats_report.py"
    spec = importlib.util.spec_from_file_location("route_stats_report2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    assert mod.main(["--db", str(db)]) == 2
