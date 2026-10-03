#!/usr/bin/env bash
# ============================================================
#  本地打包脚本（在自己的 Windows 电脑上运行，用 Git Bash）
#     用法：在项目根目录打开 Git Bash，执行   bash pack.sh
#  作用：把项目打成 baomi-deploy.tar.gz（约 3MB），
#        自动排除所有庞然大物：node_modules / .next / .git /
#        chroma_db / data / .workbuddy / ui-redesign / venv …
#
#  重要：默认排除 .env（里面有你的 API Key）。
#        服务器上第一次运行 deploy.sh 会由 .env.example 生成一份
#        空的 .env，你填好 Key 再跑一次即可。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

OUT="$PWD/baomi-deploy.tar.gz"
# 中间文件必须放在项目目录**外面**，否则 tar 会边打包边发现自己正在变，
# 报 "file changed as we read it" 并以退出码 1 中断（set -e 会让脚本直接退出）
# 用 mktemp -u 只取一个不冲突的文件名，不预创建文件（免得还要删）
# 必须用 /tmp 这种纯 POSIX 路径：Git Bash 下的 mktemp -t 会返回
# C:\Users\...\Temp/xxx 这种混合路径，GNU tar 会把 "C:" 当成远程主机而报
# "Cannot connect to C: resolve failed"。/tmp 在 Git Bash 下映射到 %TEMP%。
TMP="/tmp/baomi-deploy-$$.tar.gz"

tar \
  --exclude='node_modules' \
  --exclude='.next' \
  --exclude='.git' \
  --exclude='chroma_db' \
  --exclude='data' \
  --exclude='.workbuddy' \
  --exclude='.trae' \
  --exclude='ui-redesign' \
  --exclude='baomi-agent' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.log' \
  --exclude='.env' \
  --exclude='baomi-deploy*.tar.gz' \
  --exclude='.baomi-pack.tmp.tar.gz' \
  -czf "$TMP" .

mv -f "$TMP" "$OUT"

echo "============================================"
echo " 打包完成：$OUT"
ls -lh "$OUT" | awk '{print " 大小：" $5}'
echo "============================================"
echo " 下一步（在服务器上执行）："
echo "   mkdir -p /root/baomiagent"
echo "   tar -xzf baomi-deploy.tar.gz -C /root/baomiagent"
echo "   cd /root/baomiagent && bash deploy.sh"
echo "============================================"
