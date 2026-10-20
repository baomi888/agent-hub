"""A5 回归：指标埋点与导出格式（2026-10-20 审计新增）。

在它之前，系统只有"活着 / 死了"两种信号（/health + 心跳三态）。
于是 `RETRIEVE_MIN_SIM=0.25`、`RETRIEVE_LOW_SIM=0.45`、MMR λ=0.65 这七个检索参数
**没有一个有数据支撑**——下次要调，凭的还是"感觉答得太硬"。

本测试锁死五件事：
  1) 计数器按 labels 分桶，互不覆盖；
  2) 直方图桶是累计计数（Prometheus 语义），+Inf 桶等于总数；
  3) 文本导出符合 Prometheus 0.0.4 的形状（# HELP / # TYPE / _total 命名）；
  4) 健康值不污染指标（NaN / 非数字直接丢）；
  5) 开关关掉就完全不记录（热路径代价可控）。
"""

from __future__ import annotations

import math

import pytest

from core import config, metrics


@pytest.fixture(autouse=True)
def clean():
    metrics.reset()
    yield
    metrics.reset()


def test_counter_buckets_by_labels():
    metrics.inc("chat_total", {"path": "rag"})
    metrics.inc("chat_total", {"path": "rag"})
    metrics.inc("chat_total", {"path": "agent"})
    snap = metrics.snapshot()["counters"]["chat_total"]
    assert snap["path=rag"] == 2
    assert snap["path=agent"] == 1


def test_counter_without_labels():
    metrics.inc("retrieval_empty")
    metrics.inc("retrieval_empty", n=2)
    assert metrics.snapshot()["counters"]["retrieval_empty"][""] == 3


def test_histogram_is_cumulative():
    for v in (0.12, 0.31, 0.47, 0.83):
        metrics.observe("retrieval_top1_similarity", v)
    series = metrics.snapshot()["histograms"]["retrieval_top1_similarity"][""]
    buckets = {float(k): n for k, n in series["buckets"].items()}
    # le=0.2 只装 0.12；le=0.4 装 0.12+0.31；以此类推（累计语义）
    assert buckets[0.2] == 1
    assert buckets[0.4] == 2
    assert buckets[0.5] == 3
    assert buckets[0.9] == 4
    assert series["count"] == 4
    assert math.isclose(series["mean"], (0.12 + 0.31 + 0.47 + 0.83) / 4, rel_tol=1e-6)


def test_histogram_accepts_custom_buckets():
    metrics.histogram("custom_metric", (0.5, 1.0))
    metrics.observe("custom_metric", 0.7)
    series = metrics.snapshot()["histograms"]["custom_metric"][""]
    assert series["buckets"]["0.5"] == 0
    assert series["buckets"]["1.0"] == 1


@pytest.mark.parametrize("bad", [None, "abc", float("nan"), float("inf")])
def test_invalid_observations_are_dropped(bad):
    """一个 NaN 混进直方图会让 sum 变成 NaN，整个面板就废了——必须在入口挡掉。"""
    metrics.observe("retrieval_top1_similarity", bad)
    snap = metrics.snapshot()["histograms"]
    # 没被接收就不该产生序列
    assert snap.get("retrieval_top1_similarity", {}) == {}


def test_prometheus_text_shape():
    metrics.inc("chat_truncated", {"path": "rag"})
    metrics.observe("retrieval_top1_similarity", 0.62, {"kb": "kb_1"})
    text = metrics.render_prometheus()

    # 计数器必须导出成 _total（Prometheus 命名约定，否则 Grafana 的 rate() 结果不对）
    assert "# TYPE chat_truncated_total counter" in text
    assert 'chat_truncated_total{path="rag"} 1' in text
    # 直方图三件套齐全
    assert "# TYPE retrieval_top1_similarity histogram" in text
    assert 'retrieval_top1_similarity_bucket{kb="kb_1",le="+Inf"} 1' in text
    assert 'retrieval_top1_similarity_count{kb="kb_1"} 1' in text
    assert "retrieval_top1_similarity_sum{" in text
    # 每行都必须是 "name value" 形状（Prometheus 解析失败会静默丢整段）
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        assert len(line.rsplit(" ", 1)) == 2


def test_integer_values_render_without_decimal():
    metrics.inc("chat_total", {"path": "llm"})
    assert 'chat_total{path="llm"} 1' in metrics.render_prometheus()


def test_enable_switch(monkeypatch):
    monkeypatch.setattr(config, "METRICS_ENABLED", False)
    metrics.inc("chat_total", {"path": "rag"})
    metrics.observe("retrieval_top1_similarity", 0.5)
    snap = metrics.snapshot()
    assert snap["counters"].get("chat_total", {}) == {}
    assert snap["histograms"].get("retrieval_top1_similarity", {}) == {}


def test_declared_metrics_have_help_text():
    """预声明的指标必须带 HELP：/api/metrics 一抓就有人读，别让人看裸名字猜含义。"""
    metrics.inc("chat_total")
    text = metrics.render_prometheus()
    assert "# HELP chat_total_total" in text or "# HELP chat_total" in text
    snap = metrics.snapshot()
    assert snap["help"].get("chat_total")


def test_uptime_present():
    snap = metrics.snapshot()
    assert snap["uptime_seconds"] >= 0
    assert "process_uptime_seconds" in metrics.render_prometheus()
