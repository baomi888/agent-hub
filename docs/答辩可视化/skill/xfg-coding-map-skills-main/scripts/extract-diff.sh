#!/usr/bin/env bash
# extract-diff.sh — 采集两个基线之间的代码变更，供"迭代对比 / PR 讲解"场景构建代码地图数据
#
# 用法:
#   extract-diff.sh <repo-path> <baseline-ref> [current-ref]
# 示例:
#   extract-diff.sh . v1.2.0            # v1.2.0 → 工作区 HEAD
#   extract-diff.sh . origin/main HEAD  # PR 场景
#
# 输出段落:
#   [1] 变更文件状态 (name-status)  → 决定 TREE 徽标 A新增/M修改/D删除
#   [2] 每文件行数   (numstat)      → 决定 header stats 和深入阅读优先级
#   [3] 汇总         (shortstat)    → header stats 总数
#   [4] 变更目录分布                → 决定全景图 subgraph 划分
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "用法: extract-diff.sh <repo-path> <baseline-ref> [current-ref]" >&2
  exit 1
fi

repo=$1; base=$2; cur=${3:-HEAD}
cd "$repo"

echo "## 基线: $base → $cur"
echo
echo "[1] 变更文件状态"
git diff --name-status "$base".."$cur"
echo
echo "[2] 每文件行数（列1=新增行 列2=删除行）"
git diff --numstat "$base".."$cur" | sort -rn
echo
echo "[3] 汇总"
git diff --shortstat "$base".."$cur"
echo
echo "[4] 变更目录分布（按变更文件数倒序）"
git diff --name-only "$base".."$cur" | xargs -I{} dirname {} | sort | uniq -c | sort -rn | head -20
