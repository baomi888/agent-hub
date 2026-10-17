"""只读分析 main 与 dev 的合并冲突（纯内存，不写任何文件）。

思路：不用 `git merge-file`（它要落盘），改用 `git merge-tree` 的输出 + 三方 diff
hunk 位置比对，判断每处冲突是「同区域撞车」还是「不同区域可自动合并」。

严格只读：只跑 git 查询命令，不修改工作区、不创建分支、不写仓库文件。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFLICT_FILES = [
    "agent/tools.py",
    "api/chat.py",
    "core/config.py",
    "kb/importer.py",
    "kb/rag_core.py",
]


def git(*args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.stdout


def hunks(ref_a: str, ref_b: str, path: str) -> list[tuple[int, int, int]]:
    """返回 [(起点旧行, 旧行数, 新行数)]，来自 git diff 的 @@ 头。"""
    out = git("diff", f"{ref_a}...{ref_b}", "--", path)
    res = []
    for ln in out.split("\n"):
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            res.append((int(m.group(1)), int(m.group(2) or 1), int(m.group(4) or 1)))
    return res


def add_del(ref_a: str, ref_b: str, path: str) -> tuple[int, int]:
    out = git("diff", "--numstat", f"{ref_a}...{ref_b}", "--", path).strip()
    if not out:
        return (0, 0)
    p = out.split("\t")
    try:
        return (int(p[0]), int(p[1]))
    except (ValueError, IndexError):
        return (0, 0)


def merge_tree_conflicts(main_ref: str, other_ref: str) -> list[str]:
    out = git("merge-tree", "--write-tree", "--name-only", main_ref, other_ref)
    found, keep = [], False
    for ln in out.split("\n"):
        s = ln.strip()
        if s.startswith("CONFLICT"):
            keep = True
            m = re.search(r"(?:Merge conflict in|in)\s+(\S+)\s*$", s)
            if m:
                found.append(m.group(1))
            continue
        if (
            keep
            and s
            and not s.startswith(("Auto-merging", "error"))
            and "/" in s
            or s.endswith((".py", ".md", ".json", ".ts", ".tsx"))
        ):
            if s not in found:
                found.append(s)
    return sorted(set(x for x in found if x and not x.startswith("CONFLICT")))


def line_overlap(a: list[tuple[int, int, int]], b: list[tuple[int, int, int]]) -> list[tuple]:
    """两侧 hunk 在旧行号上是否重叠 —— 重叠=真撞车，不重叠=多半能自动合。"""
    hits = []
    for s1, c1, _ in a:
        e1 = s1 + max(c1, 1) - 1
        for s2, c2, _ in b:
            e2 = s2 + max(c2, 1) - 1
            if s1 <= e2 and s2 <= e1:
                hits.append((s1, e1, s2, e2))
    return hits


def main() -> int:
    mainr = "origin/main"
    devr = "origin/dev"
    featr = "origin/feat/ai-module-20261013"

    base = git("merge-base", mainr, devr).strip()
    print("=" * 76)
    print("合并冲突只读分析")
    print("=" * 76)
    print(f"main  : {git('rev-parse', '--short', mainr).strip()}")
    print(f"dev   : {git('rev-parse', '--short', devr).strip()}")
    print(f"feat  : {git('rev-parse', '--short', featr).strip()}")
    print(f"分叉点: {base[:7]}")
    print(
        f"main 独有: {git('rev-list', '--count', f'{devr}..{mainr}').strip()} 个提交；"
        f"dev 独有: {git('rev-list', '--count', f'{mainr}..{devr}').strip()} 个提交；"
        f"feat 独有: {git('rev-list', '--count', f'{mainr}..{featr}').strip()} 个提交"
    )

    for label, ref in (("main + dev", devr), ("main + feat", featr)):
        print(f"\n{'-' * 76}\n{label}\n{'-' * 76}")
        conf = merge_tree_conflicts(mainr, ref)
        if not conf:
            print("  ✅ 无冲突，可直接合并")
            continue
        print(f"  ⚠️ {len(conf)} 个文件冲突：")
        for c in conf:
            print(f"     - {c}")

    print(f"\n{'=' * 76}")
    print("逐文件冲突性质（main + dev）")
    print("=" * 76)
    for f in CONFLICT_FILES:
        mh = hunks(devr, mainr, f)  # main 侧相对分叉点的 hunk
        dh = hunks(mainr, devr, f)  # dev 侧相对分叉点的 hunk
        ma, md = add_del(devr, mainr, f)
        da, dd = add_del(mainr, devr, f)
        ov = line_overlap(mh, dh)
        print(f"\n### {f}")
        print(f"  main 侧: +{ma}/-{md} 行, {len(mh)} 个 hunk")
        print(f"  dev  侧: +{da}/-{dd} 行, {len(dh)} 个 hunk")
        print(
            f"  行号重叠: {len(ov)} 处"
            + (" ← 真撞车" if ov else " ← 两侧改的是不同区域，多半能自动合")
        )
        for s1, e1, s2, e2 in ov[:5]:
            print(f"      main[{s1}-{e1}] × dev[{s2}-{e2}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
