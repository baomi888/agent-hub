"""从 X3 金标集派生一份「临时词表」，让匹配器现在就能跑起来。

为什么可以这么派生
------------------
金标集的 `reason` 列是组员按「X 是 Y 口语别名；A/B 不动」写的（94 条里 85 条是这个句式，
另外 9 条写的是「缩写」「英文别名」）。也就是说**别名 → 标准词这张对应表，
金标集里本来就有**，只是散在自然语言里。

把它抽出来，就得到一份 90 多条的真实词表，足够把「加载 → 匹配 → 打分」整条链路跑通。
X1 的 vocab_v1.csv 一到，换文件即可，代码一行不动。

⚠️ 这份临时词表**不能当正式词表**：
  · domain / definition 是空的（金标集没有这两列）
  · 反例是空的（金标集的 must_not_replace 是「本句里别动这些词」，
    不等于「这个词条的通用反例」，混进来会让反例检查变成自证）
所以它只用于**跑通链路 + 定位匹配器缺陷**，不用于上线。

用法：
    python ops/derive_temp_vocab.py
    python ops/derive_temp_vocab.py --golden data/golden/golden_v1.csv --out data/vocab/temp_from_golden.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 「X 是 Y 口语别名 / 缩写 / 英文别名 / 别称 / 俗称 / 简称」
# 用非贪婪 + 明确后缀，避免把整句吃了。
_ALIAS_PAT = re.compile(
    r"([^，,；;、（）()\s]{1,12}?)是([^，,；;、（）()\s]{1,20}?)(?:的)?(?:口语别名|英文别名|别名|缩写|别称|俗称|简称|全称)"
)


def derive(golden_path: str) -> tuple[list[dict], dict]:
    rows = list(csv.DictReader(open(golden_path, encoding="utf-8-sig")))
    pairs: dict[str, set[str]] = {}
    unmatched: list[tuple[int, str]] = []
    multi = 0
    for lineno, r in enumerate(rows, start=2):
        found = _ALIAS_PAT.findall(r.get("reason") or "")
        if not found:
            unmatched.append((lineno, (r.get("reason") or "")[:50]))
            continue
        if len(found) > 1:
            multi += 1
        for alias, canon in found:
            pairs.setdefault(canon.strip(), set()).add(alias.strip())

    out = []
    for i, (canon, aliases) in enumerate(sorted(pairs.items()), start=1):
        out.append({
            "term_id": f"TMP{i:04d}",
            "canonical": canon,
            "domain": "",
            "definition": "",
            "aliases": "|".join(sorted(aliases)),
            "negative_aliases": "",
        })
    stats = {
        "金标条数": len(rows),
        "抽出的词条": len(out),
        "一条金标里多个别名": multi,
        "抽不出别名的金标": len(unmatched),
        "抽不出的样例": unmatched[:5],
        "别名总数": sum(len(v) for v in pairs.values()),
    }
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="从 X3 金标集派生临时词表")
    ap.add_argument("--golden", default=os.path.join(ROOT, "data/golden/golden_v1.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data/vocab/temp_from_golden.csv"))
    args = ap.parse_args()

    rows, stats = derive(args.golden)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["term_id", "canonical", "domain",
                                          "definition", "aliases", "negative_aliases"])
        w.writeheader()
        w.writerows(rows)

    print(f"已写出 {args.out}")
    for k, v in stats.items():
        print(f"  {k}：{v}")
    if stats["抽不出别名的金标"]:
        print("\n  ⚠️ 这些条目的 reason 不符合句式，需要人工补词表：")
        for lineno, txt in stats["抽不出的样例"]:
            print(f"     行 {lineno}：{txt}")
    print("\n提醒：这份词表 domain/definition/反例都是空的，只用于跑通链路，不能上线。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
