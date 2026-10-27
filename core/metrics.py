"""进程内指标：让"它到底表现如何"有一个能查的数。

为什么不用 prometheus_client
----------------------------
多一个外部依赖，而需要的东西只有三样：计数器、直方图、文本导出。
标准库 + 一把锁三十行就能覆盖，且不需要在部署链路里多一个 exporter 进程。
将来要换 prometheus_client，只需替换 render_prometheus() 的实现，调用方不动。

为什么这些指标值得埋
--------------------
在这个模块出现之前，系统只有两种信号：活着 / 死了。
于是 RETRIEVE_MIN_SIM=0.25、RETRIEVE_LOW_SIM=0.45、MMR λ=0.65 这七个检索参数
**没有一个有数据支撑**，全是拍出来的。有了 top1 相似度分布与闸门拦截计数，
下次调参才是基于分布的判断，而不是"感觉答得太硬"。

进程内意味着什么（如实标注）
----------------------------
多 worker 下每个进程各有一份，`/api/metrics` 只反映**被路由到的那个进程**。
对 Prometheus 采集来说这是正确的（应该抓每个实例或每个 worker 端口），
对"我手动 curl 看一眼"来说则是不完整的。别把它当成全局真值。

热路径纪律
----------
inc/observe 加锁自增，单次约 0.5μs。相对一次 LLM 调用（百毫秒级）可忽略。
但**不要**放进 `_stream_llm` 的每帧循环里——那里每 token 执行一次，应该只在收尾打点。
"""

from __future__ import annotations

import math
import threading
import time

_LOCK = threading.Lock()

# name -> {(label_key_tuple): value}
_COUNTERS: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
# name -> {"buckets": [...], "counts": {...}, "sum": float, "count": int, "labels": {...}}
_HISTOGRAMS: dict[str, dict] = {}

# 指标元信息：给 /api/metrics 的 # HELP / # TYPE 用，也防止拼错名字
_HELP: dict[str, str] = {}

_STARTED_AT = time.time()


def _key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _fmt_labels(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


# ---------- 计数 ----------


def counter(name: str, help_text: str = "") -> None:
    """声明一个计数器（幂等）。没声明的名字也能自增，但没有 HELP 文本。"""
    with _LOCK:
        _COUNTERS.setdefault(name, {})
        if help_text:
            _HELP.setdefault(name, help_text)


def inc(name: str, labels: dict[str, str] | None = None, n: float = 1) -> None:
    """自增。埋点热路径用的就是它，保持极简。"""
    if not _enabled():
        return
    k = _key(labels)
    with _LOCK:
        bucket = _COUNTERS.setdefault(name, {})
        bucket[k] = bucket.get(k, 0.0) + n


# ---------- 直方图 ----------

# 相似度的默认桶：0.1 起步。低于 0.1 基本等于无关，不需要细分
_DEFAULT_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9)


def histogram(name: str, buckets: tuple[float, ...] | None = None, help_text: str = "") -> None:
    """声明一个直方图（幂等）。重复声明的桶会被忽略，保留第一次的。"""
    with _LOCK:
        if name not in _HISTOGRAMS:
            bs = tuple(sorted(buckets or _DEFAULT_BUCKETS))
            _HISTOGRAMS[name] = {
                "buckets": bs,
                "series": {},  # label_key -> {"counts": {b: n}, "sum": float, "count": int}
            }
        if help_text:
            _HELP.setdefault(name, help_text)


def observe(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """记一个观测值。未声明的直方图会被自动声明（用默认桶）。"""
    if not _enabled():
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    if math.isnan(v) or math.isinf(v):
        return
    if name not in _HISTOGRAMS:
        histogram(name)
    k = _key(labels)
    with _LOCK:
        h = _HISTOGRAMS[name]
        series = h["series"].setdefault(
            k, {"counts": {b: 0 for b in h["buckets"]}, "sum": 0.0, "count": 0}
        )
        for b in h["buckets"]:
            if v <= b:
                series["counts"][b] += 1
        series["sum"] += v
        series["count"] += 1


# ---------- 导出 ----------


def snapshot() -> dict:
    """结构化快照（给前端 / 排障脚本用，不是 Prometheus 格式）。"""
    with _LOCK:
        counters = {
            name: {".".join(f"{k}={v}" for k, v in key) or "": val for key, val in bucket.items()}
            for name, bucket in _COUNTERS.items()
        }
        histograms = {}
        for name, h in _HISTOGRAMS.items():
            series = {}
            for key, s in h["series"].items():
                label = ".".join(f"{k}={v}" for k, v in key) or ""
                series[label] = {
                    "count": s["count"],
                    "sum": round(s["sum"], 6),
                    "mean": round(s["sum"] / s["count"], 6) if s["count"] else 0.0,
                    "buckets": {str(b): n for b, n in s["counts"].items()},
                }
            histograms[name] = series
    return {
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "counters": counters,
        "histograms": histograms,
        "help": dict(_HELP),
    }


def render_prometheus() -> str:
    """渲染成 Prometheus 文本格式（version=0.0.4）。

    计数器一律按 `<name>_total` 输出——这是 Prometheus 的命名约定，
    不遵守的话 Grafana 的 rate() 会给出意外结果。
    """
    lines: list[str] = []
    with _LOCK:
        for name in sorted(_COUNTERS):
            help_text = _HELP.get(name, name)
            total_name = name if name.endswith("_total") else f"{name}_total"
            lines.append(f"# HELP {total_name} {help_text}")
            lines.append(f"# TYPE {total_name} counter")
            for key, val in _COUNTERS[name].items():
                labels = dict(key)
                lines.append(f"{total_name}{_fmt_labels(labels)} {_num(val)}")

        for name in sorted(_HISTOGRAMS):
            h = _HISTOGRAMS[name]
            help_text = _HELP.get(name, name)
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} histogram")
            for key, s in h["series"].items():
                labels = dict(key)
                for b in h["buckets"]:
                    lb = dict(labels)
                    lb["le"] = _num(b)
                    lines.append(f"{name}_bucket{_fmt_labels(lb)} {s['counts'][b]}")
                lb = dict(labels)
                lb["le"] = "+Inf"
                lines.append(f"{name}_bucket{_fmt_labels(lb)} {s['count']}")
                lines.append(f"{name}_sum{_fmt_labels(labels)} {_num(s['sum'])}")
                lines.append(f"{name}_count{_fmt_labels(labels)} {s['count']}")

        lines.append("# HELP process_uptime_seconds 进程已运行秒数")
        lines.append("# TYPE process_uptime_seconds gauge")
        lines.append(f"process_uptime_seconds {_num(time.time() - _STARTED_AT)}")
    return "\n".join(lines) + "\n"


def _num(v: float) -> str:
    """整数值不要输出成 3.0，Prometheus 解析没问题但人看着别扭。"""
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return str(int(v))
    return f"{v:.6g}"


def reset() -> None:
    """清空全部**数据**（仅测试用）。

    刻意不清 `_HELP`：那是模块级 `counter()/histogram()` 声明的结果，
    属于"静态定义"而不是"运行时数据"。一起清掉的话，reset 之后
    /api/metrics 会退化成一堆没有 # HELP 的裸名字——测试里跑一次就会
    让后面对 HELP 的断言全灭（第一版就是这么挂的）。
    """
    with _LOCK:
        _COUNTERS.clear()
        for name in list(_HISTOGRAMS):
            _HISTOGRAMS[name]["series"].clear()


def _enabled() -> bool:
    from core import config

    return bool(getattr(config, "METRICS_ENABLED", True))


# ---------- 预声明：让 /api/metrics 一开始就有稳定的 # HELP ----------

counter("chat_total", "问答请求数（按执行路径）")
counter("chat_truncated", "输出被长度上限截断的问答数（按执行路径）")
counter("chat_client_disconnect", "SSE 客户端中途断连次数")
counter("retrieval_gate_blocked", "被 RETRIEVE_MIN_SIM 离群过滤丢掉的片段数")
counter("retrieval_empty", "检索结果为空（未命中）的次数")
counter("retrieval_cache", "检索缓存命中情况（kind=hit / neg）")
counter("retrieval_grounding", "依据强度分级计数（level=strong / medium / weak）")
counter("llm_error", "LLM 调用失败次数（按 describe_api_error 分类）")
counter("quota_rejected", "配额/限流拒绝次数（kind=rate / ask / embed / auto）")
counter("embed_guard_mismatch", "向量空间指纹不一致被拒的检索次数")

histogram(
    "retrieval_top1_similarity",
    _DEFAULT_BUCKETS,
    "检索返回的 top1 相似度分布（0~1，越大越相关）",
)
