#!/usr/bin/env bash
# 把三个常驻进程交给 systemd 托管：开机自启 + 崩溃自动拉起。
#
# 用法（在服务器上，一条命令）：
#   bash ops/install-systemd.sh
#
# 装完之后 `bash deploy.sh deploy` 会自动切到托管模式（判据是 unit 已 enable），
# 不再用 nohup 起第二份 —— 否则两个进程会抢同一个端口。
# 想退回 nohup 模式：systemctl disable baomi-backend baomi-frontend baomi-worker
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="/etc/systemd/system"
SERVICES="baomi-backend baomi-frontend baomi-worker"

if [ "$(id -u)" != "0" ]; then
  echo "[中止] 需要 root 权限（装 unit 要写 /etc/systemd/system）"
  exit 1
fi

command -v systemctl >/dev/null 2>&1 || { echo "[中止] 这台机器没有 systemd"; exit 1; }

NPM_BIN="$(command -v npm || true)"
if [ -z "$NPM_BIN" ]; then
  echo "[中止] 找不到 npm，先确认前端依赖装好了"
  exit 1
fi

echo "===== 1. 安装 unit ====="
for svc in $SERVICES; do
  src="$ROOT/ops/systemd/$svc.service"
  [ -f "$src" ] || { echo "[中止] 缺少 $src"; exit 1; }
  # 项目根不是 /root 时也能用：把模板里的 /root 换成实际路径
  sed "s#/root#$ROOT#g" "$src" > "$UNIT_DIR/$svc.service"
  # npm 路径同样按实际位置替换，避免 nvm 装的 node 起不来
  if [ "$svc" = "baomi-frontend" ]; then
    sed -i "s#^ExecStart=.*npm run start#ExecStart=$NPM_BIN run start#" "$UNIT_DIR/$svc.service"
  fi
  echo "  已写入 $UNIT_DIR/$svc.service"
done

echo "===== 2. 停掉旧进程（避免和 systemd 抢端口）====="
bash "$ROOT/deploy.sh" stop >/dev/null 2>&1 || true
sleep 1

echo "===== 3. 启用并启动 ====="
systemctl daemon-reload
systemctl enable --now $SERVICES
sleep 5

echo "===== 4. 自检 ====="
systemctl is-active baomi-backend baomi-frontend baomi-worker
bc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || echo 000)
fc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ 2>/dev/null || echo 000)
echo "  后端 /health -> HTTP $bc"
echo "  前端 /       -> HTTP $fc"
echo
echo "看日志： bash deploy.sh logs     （托管模式走 journalctl）"
echo "查状态： bash deploy.sh status"
echo
echo "★ 现在重启服务器，三个进程会自动起来 —— 以前不会。可以重启一次验一下。"
