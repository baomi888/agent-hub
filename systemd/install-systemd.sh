#!/usr/bin/env bash
# ============================================================
#  把 bemi / 知穗 Agent 从 nohup 迁移到 systemd
#
#  用法（在服务器项目目录执行）：
#      bash systemd/install-systemd.sh                # 默认 APP_DIR=/root
#      bash systemd/install-systemd.sh /root/baomiagent
#      bash systemd/install-systemd.sh --rollback     # 卸载，回退到手工启动
#
#  做的事：
#    1) 校验 APP_DIR 下确实有 main.py / venv / frontend 构建产物
#    2) 把 3 个 unit 模板的 __APP_DIR__ 替换成真实路径，装到 /etc/systemd/system/
#    3) 停掉现有 nohup 进程（释放 3000/8000），再 enable --now
#    4) 打印状态
#
#  ⚠️ 迁移后请**不要再**用 deploy.sh start/stop/restart（它仍是 nohup 那套），
#     日常改用：systemctl {status,restart,stop} agent-hub-{backend,frontend,worker}
#     deploy.sh 只保留「首次部署 / 重新构建」用途。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/systemd"
UNIT_DST="/etc/systemd/system"
SERVICES=(agent-hub-backend agent-hub-frontend agent-hub-worker)

BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[1;31m'; NC=$'\033[0m'
log()  { printf '\n%s===== %s =====%s\n' "$CYAN" "$*" "$NC"; }
ok()   { printf '  %s[OK]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[中止]%s %s\n\n' "$RED" "$NC" "$*"; exit 1; }

if [ "$(id -u)" != "0" ]; then
  exec sudo bash "$0" "$@"
fi

command -v systemctl >/dev/null 2>&1 || die "本机没有 systemd，无法迁移"

rollback() {
  log "回滚：停止并移除 systemd 服务"
  for s in "${SERVICES[@]}"; do
    systemctl disable --now "$s" >/dev/null 2>&1 || true
    rm -f "$UNIT_DST/$s.service"
    ok "已移除 $s"
  done
  systemctl daemon-reload
  warn "已回退。重新用 nohup 启动请执行： bash deploy.sh start"
}

if [ "${1:-}" = "--rollback" ]; then
  rollback
  exit 0
fi

APP_DIR="${1:-/root}"

# 后端 worker 数：WORKERS=4 bash systemd/install-systemd.sh 可显式指定；
# 不指定就按 CPU / 内存探测（与 deploy.sh 的 detect_workers 同一套规则）。
detect_workers() {
  local cpu mem_kb mem_gb w
  cpu=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
  mem_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 2097152)
  mem_gb=$(( mem_kb / 1024 / 1024 ))
  w=$cpu
  if [ "$w" -gt 4 ]; then w=4; fi
  if [ "$mem_gb" -lt 3 ] && [ "$w" -gt 2 ]; then w=2; fi
  if [ "$w" -lt 1 ]; then w=1; fi
  printf '%s' "$w"
}
WORKERS="${WORKERS:-$(detect_workers)}"
case "$WORKERS" in
  ''|*[!0-9]*) die "WORKERS 必须是正整数，当前是：$WORKERS" ;;
esac

log "0. 校验 APP_DIR"
[ -f "$APP_DIR/main.py" ] || die "找不到 $APP_DIR/main.py —— APP_DIR 传错了？"
[ -x "$APP_DIR/venv/bin/python" ] || die "找不到 $APP_DIR/venv/bin/python（后端 venv 不存在，先跑 deploy.sh）"
[ -d "$APP_DIR/frontend/.next" ] || die "找不到 $APP_DIR/frontend/.next（前端未构建，先跑 deploy.sh deploy）"
[ -x "$APP_DIR/frontend/node_modules/.bin/next" ] || warn "未找到 next 二进制，前端服务可能起不来"
ok "APP_DIR = $APP_DIR"

log "1. 停止现有 nohup 进程（释放 3000/8000）"
pkill -f "uvicorn main:app" >/dev/null 2>&1 && ok "后端已停" || warn "后端本来就没在跑"
pkill -f "next start"       >/dev/null 2>&1 && ok "前端已停" || warn "前端本来就没在跑"
pkill -f "next-server"      >/dev/null 2>&1 || true
pkill -f "worker.scheduler" >/dev/null 2>&1 && ok "worker 已停" || warn "worker 本来就没在跑"
rm -f /tmp/baomi-back.pid /tmp/baomi-front.pid /tmp/baomi-worker.pid

log "2. 安装 unit 文件（backend workers=$WORKERS）"
for s in "${SERVICES[@]}"; do
  src="$UNIT_SRC/$s.service"
  [ -f "$src" ] || die "缺少模板：$src"
  sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__WORKERS__|$WORKERS|g" "$src" > "$UNIT_DST/$s.service"
  ok "已安装 $UNIT_DST/$s.service"
done

log "3. 启用并启动"
systemctl daemon-reload
for s in "${SERVICES[@]}"; do
  systemctl enable --now "$s" >/dev/null 2>&1 || die "$s 启动失败，查看： journalctl -u $s -n 50 --no-pager"
done

log "4. 状态自检（等 5 秒）"
sleep 5
for s in "${SERVICES[@]}"; do
  state="$(systemctl is-active "$s" 2>/dev/null || true)"
  printf '  %-24s %s\n' "$s" "$state"
done
BC=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/health" 2>/dev/null || echo 000)
FC=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:3000/" 2>/dev/null || echo 000)
printf '  后端 /health -> HTTP %s\n' "$BC"
printf '  前端 /       -> HTTP %s\n' "$FC"

echo
if [ "$BC" = "200" ] && [ "$FC" != "000" ]; then
  printf '%s迁移完成%s\n' "$GREEN" "$NC"
else
  warn "有服务没起来，逐个看： journalctl -u agent-hub-backend -n 50 --no-pager"
fi

cat <<EOF

============================================================
 日常运维（systemd）
   systemctl status  agent-hub-backend agent-hub-frontend agent-hub-worker
   systemctl restart agent-hub-backend
   journalctl -u agent-hub-backend -f          # 实时日志
   journalctl -u agent-hub-worker  -n 100      # 最近 100 行

 回滚到 nohup
   bash systemd/install-systemd.sh --rollback
   bash deploy.sh start
============================================================
EOF
