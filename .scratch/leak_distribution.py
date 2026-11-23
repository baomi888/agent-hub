# -*- coding: utf-8 -*-
"""统计良性样本上"每段文本的指纹命中窗口数"分布，用于确定 N。

样本来源（全部是仓库里已有的文本，没有自造语料）：
  1. tests/test_prompt_leak_attack.py 的 22 条良性提问（BENIGN_PROMPTS）
  2. tests / .scratch 里出现的良性回答文本（"上海今天多云…" / "今天北京晴…"）
  3. README.md 与 docs/*.md 的段落（作为"正常中文长文本"的下界代理）

统计口径与 L3 一致：_normalize 后按 24 字滑窗查指纹库，合并成连续命中段，
记录每段文本的命中窗口总数与单段最大连续窗口数。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.prompt_security import (  # noqa: E402
    SHINGLE_LEN,
    _analyze_normalized,
    _fingerprint_runs,
    _normalize,
    _run_hit_count,
    _shingle_index,
    security_clause,
)

# 与 tests/test_prompt_leak_attack.py 的 BENIGN_PROMPTS 保持同一批（测试文件本身
# 会 import 大量东西，这里直接抄文本会造成漂移，所以运行时从测试模块读取）。
sys.path.insert(0, str(ROOT / "tests"))
import test_prompt_leak_attack as t  # noqa: E402

samples: list[tuple[str, str]] = []
for bid, payload in t.BENIGN_PROMPTS:
    samples.append((f"benign-prompt:{bid}", payload))
samples.append(("benign-answer:weather-1", "上海今天多云，气温 18~24℃，东南风 2 级。出门带件薄外套即可。"))
samples.append(("benign-answer:weather-2", "今天北京晴，气温 12 到 20 度，建议穿薄外套。"))

for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
    if not path.exists():
        continue
    for i, para in enumerate(path.read_text(encoding="utf-8").split("\n\n")):
        para = para.strip()
        if len(para) >= 40:  # 太短的片段没有统计意义
            samples.append((f"{path.name}#{i}", para))

shingles, _ = _shingle_index()
print(f"protected shingles: {len(shingles)}  (SHINGLE_LEN={SHINGLE_LEN})")
print(f"samples: {len(samples)}")

per_sample_total: list[int] = []
per_sample_maxrun: list[int] = []
hit_samples: list[tuple[str, int, int]] = []
for name, text in samples:
    runs = _fingerprint_runs(_normalize(text), shingles)
    total = sum(_run_hit_count(r) for r in runs)
    maxrun = max((_run_hit_count(r) for r in runs), default=0)
    per_sample_total.append(total)
    per_sample_maxrun.append(maxrun)
    if total:
        hit_samples.append((name, total, maxrun))

print(f"samples with any hit: {len(hit_samples)}")
print(f"max total hit windows per sample: {max(per_sample_total, default=0)}")
print(f"max consecutive run per sample: {max(per_sample_maxrun, default=0)}")
print("distribution(total windows -> #samples):")
from collections import Counter  # noqa: E402

for k, v in sorted(Counter(per_sample_total).items()):
    print(f"  {k}: {v}")
if hit_samples:
    print("hit samples:", hit_samples[:20])

# 断言：良性样本在 N=3 下不能出现 hard
for name, text in samples:
    sev, reason, spans, detail = _analyze_normalized(_normalize(text))
    if sev.value != "pass":
        print(f"[WARN] benign sample flagged: {name} severity={sev.value} reason={reason} detail={detail}")
print("done")
