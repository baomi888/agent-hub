"""晋升闸门的统计口径（GEPA 的第 ③④ 层之间那道"能不能进前沿"的判定）。

用户 2026-10-20 拍板的验收口径
------------------------------
- **回放集 200~300 例**（不足 200 直接判 `insufficient_sample`，不做显著性判断）；
- **指标 M1–M4 的 95% 置信区间下界 ≥ -5pp** 才准晋升；
- M5（token 成本）/ M6（p95 延迟）是"越小越好"，用相对放大倍数卡（默认 1.15 / 1.20）。

为什么是**成对** bootstrap 而不是两独立样本
------------------------------------------
基线和候选跑的是**同一批 case**，两边逐条配对。配对后差的方差远小于
两边均值各自的方差（题目难度这个最大的噪声源被消掉了），
所以同样的样本量能测出更小的真实差异——否则 200 例根本分辨不出 5pp。

为什么用"下界 ≥ -5pp"而不是"点估计 ≥ -5pp"
-----------------------------------------
点估计会有一半概率落在真实值之上，等于 50% 的把握放行一个真实劣化 5pp 的候选。
取 95% CI 的下界，是要求"劣化到 5pp 这件事连统计误差都兜不住了才算过线"，
对应的是「宁可少晋升，不放劣化上线」。

纯 Python 实现（不引 numpy/scipy）
--------------------------------
样本量 ≤ 300、bootstrap 2000 次，纯 Python 逐次抽样在 1 秒量级；
为这点计算引一个数值库不划算，也会让离线测试多一层依赖。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

# 越大越好的质量指标；M5/M6 单独按倍数卡
QUALITY_METRICS = ("M1", "M2", "M3", "M4")
METRIC_NAMES = {
    "M1": "引用忠实度",
    "M2": "拒答正确率",
    "M3": "工具路由正确率",
    "M4": "回答质量",
}
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_INSUFFICIENT = "insufficient_sample"


def p95(values: Sequence[float]) -> float:
    """最近的 p95（nearest-rank）。样本小的时候不要插值，插值会给出不存在的延迟。"""
    vals = sorted(float(v) for v in (values or []))
    if not vals:
        return 0.0
    k = max(1, math.ceil(0.95 * len(vals)))
    return vals[min(k, len(vals)) - 1]


def mean(values: Sequence[float]) -> float:
    vals = [float(v) for v in (values or [])]
    return sum(vals) / len(vals) if vals else 0.0


def paired_bootstrap_diff(
    base: Sequence[float],
    cand: Sequence[float],
    *,
    alpha: float = 0.05,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """成对 bootstrap：同一批 case 上 (cand - base) 的均值分布。

    返回 ``{"n", "mean", "lo", "hi", "alpha", "n_boot"}``；n=0 时返回全 0。
    """
    n = min(len(base or []), len(cand or []))
    if n <= 0:
        return {"n": 0, "mean": 0.0, "lo": 0.0, "hi": 0.0, "alpha": alpha, "n_boot": 0}

    diffs = [float(cand[i]) - float(base[i]) for i in range(n)]
    point = sum(diffs) / n
    boots = max(1, int(n_boot))
    rng = random.Random(seed)
    means = [0.0] * boots
    for b in range(boots):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means[b] = s / n
    means.sort()
    lo_idx = min(boots - 1, max(0, int((alpha / 2.0) * boots)))
    hi_idx = min(boots - 1, max(0, int((1.0 - alpha / 2.0) * boots) - 1))
    return {
        "n": n,
        "mean": point,
        "lo": means[lo_idx],
        "hi": means[hi_idx],
        "alpha": alpha,
        "n_boot": boots,
    }


def _align(
    base_cases: Sequence[dict], cand_cases: Sequence[dict]
) -> list[tuple[dict, dict]]:
    """按 case_key 配对。缺一边的 case 直接丢（不许"补 0 分"充数）。"""
    cand_by_key = {str(c.get("case_key")): c for c in cand_cases or []}
    pairs = []
    for b in base_cases or []:
        k = str(b.get("case_key"))
        c = cand_by_key.get(k)
        if c is not None:
            pairs.append((b, c))
    return pairs


def gate_decision(
    base_cases: Sequence[dict],
    cand_cases: Sequence[dict],
    *,
    min_n: int = 200,
    alpha: float = 0.05,
    max_drop: float = 0.05,
    n_boot: int = 2000,
    max_cost_ratio: float = 1.15,
    max_latency_ratio: float = 1.20,
    seed: int = 0,
) -> dict:
    """晋升闸门。输入两边**逐条**判分明细（case_key + M1..M4 + tokens + latency_ms）。

    返回结构::

        {"status": "pass|fail|insufficient_sample", "n": 240, "reasons": [...],
         "checks": {"M1": {"n":.., "base":.., "cand":.., "diff":.., "lo":.., "hi":..,
                           "pass": bool, "skipped": bool}, ...},
         "cost": {...}, "latency": {...}}
    """
    pairs = _align(base_cases, cand_cases)
    reasons: list[str] = []
    if len(pairs) < int(min_n):
        return {
            "status": STATUS_INSUFFICIENT,
            "n": len(pairs),
            "reasons": [f"配对样本 {len(pairs)} < 最小样本量 {int(min_n)}，不做显著性判断"],
            "checks": {},
            "cost": {},
            "latency": {},
        }

    checks: dict[str, dict] = {}
    for m in QUALITY_METRICS:
        bs, cs = [], []
        for b, c in pairs:
            bv, cv = b.get(m), c.get(m)
            if bv is None or cv is None:  # 该指标在这条 case 上不适用（如无工具期望）
                continue
            bs.append(float(bv))
            cs.append(float(cv))
        if not bs:
            checks[m] = {"skipped": True, "n": 0, "pass": True}
            continue
        ci = paired_bootstrap_diff(bs, cs, alpha=alpha, n_boot=n_boot, seed=seed)
        ok = ci["lo"] >= -float(max_drop)
        checks[m] = {
            "name": METRIC_NAMES.get(m, m),
            "n": ci["n"],
            "base": round(mean(bs), 4),
            "cand": round(mean(cs), 4),
            "diff": round(ci["mean"], 4),
            "lo": round(ci["lo"], 4),
            "hi": round(ci["hi"], 4),
            "pass": ok,
            "skipped": False,
        }
        if not ok:
            reasons.append(
                f"{m} 置信区间下界 {ci['lo']:+.3f} < -{max_drop:.2f}"
                f"（{METRIC_NAMES.get(m, m)}：{mean(bs):.3f}→{mean(cs):.3f}）"
            )

    if all(v.get("skipped") for v in checks.values()):
        reasons.append("四个质量指标全部无可配对样本，无法判定")

    # 成本 / 延迟：相对放大倍数（方向相反，单独卡）
    base_tokens = [float(b.get("tokens") or 0) for b, _ in pairs]
    cand_tokens = [float(c.get("tokens") or 0) for _, c in pairs]
    base_lat = [float(b.get("latency_ms") or 0) for b, _ in pairs]
    cand_lat = [float(c.get("latency_ms") or 0) for _, c in pairs]
    cost = _ratio_check(base_tokens, cand_tokens, max_cost_ratio, "token 成本")
    latency = _ratio_check(base_lat, cand_lat, max_latency_ratio, "p95 延迟", use_p95=True)
    for chk, label in ((cost, "token 成本"), (latency, "p95 延迟")):
        if not chk.get("pass", True):
            reasons.append(f"{label}放大到 {chk['ratio']:.2f}×，超过 {chk['limit']:.2f}×")

    status = STATUS_PASS if not reasons else STATUS_FAIL
    return {
        "status": status,
        "n": len(pairs),
        "reasons": reasons,
        "checks": checks,
        "cost": cost,
        "latency": latency,
    }


def _ratio_check(
    base: Sequence[float],
    cand: Sequence[float],
    limit: float,
    label: str,
    *,
    use_p95: bool = False,
) -> dict:
    b = p95(base) if use_p95 else mean(base)
    c = p95(cand) if use_p95 else mean(cand)
    if b <= 0:
        # 基线为 0 时比值无意义（例如全部 case 都没调工具）：不判劣化，只留档
        return {"base": round(b, 2), "cand": round(c, 2), "ratio": None, "limit": limit, "pass": True}
    ratio = c / b
    return {
        "base": round(b, 2),
        "cand": round(c, 2),
        "ratio": round(ratio, 4),
        "limit": limit,
        "pass": ratio <= float(limit),
        "label": label,
    }


def summarize(
    per_case: Sequence[dict], *, gate: dict | None = None
) -> dict:
    """把逐条判分压成 summary（落 prompt_evals.summary_json）。

    每个指标都带 `n`：M3 这种"只在有工具期望的 case 上适用"的指标
    必须能看出它是"全过"还是"没测"。
    """
    cases = list(per_case or [])
    out: dict[str, Any] = {"n": len(cases)}
    for m in QUALITY_METRICS:
        vals = [float(c[m]) for c in cases if c.get(m) is not None]
        out[m] = round(mean(vals), 4) if vals else None
        out[f"{m}_n"] = len(vals)
    toks = [float(c.get("tokens") or 0) for c in cases]
    lat = [float(c.get("latency_ms") or 0) for c in cases]
    out["tokens_mean"] = round(mean(toks), 1)
    out["latency_p95_ms"] = round(p95(lat), 1)
    if gate is not None:
        out["gate"] = {
            "status": gate.get("status"),
            "n": gate.get("n"),
            "reasons": gate.get("reasons", []),
        }
    return out
