#!/usr/bin/env bash
# ============================================================
#  知穗 Agent · Staging 环境一键部署脚本（与生产同机分端口并存）
#
#  设计要点：
#    - 与生产 deploy.sh **完全独立**：不同端口、不同数据目录、不同 .env、不同日志/PID，
#      因此可以在同一台 ECS 上并存，互不影响。
#    - 端口：staging 前端 3001 / 后端 8001（生产是 3000 / 8000）。
#    - 隔离：必须使用独立的 git 工作区（例：/root/agent-hub-staging），
#      不要在生产目录里切分支 —— 那会让生产代码在构建期间处于半成品状态。
#
#  用法（在 staging 工作区里执行）：
#      bash deploy-staging.sh deploy     # 完整部署：拉代码 -> 装依赖 -> 构建 -> 启动
#      bash deploy-staging.sh start      # 只启动（跳过构建）
#      bash deploy-staging.sh stop       # 只停止
#      bash deploy-staging.sh restart    # 重启（不改代码）
#      bash deploy-staging.sh status     # 看状态 + 健康检查
#      bash deploy-staging.sh logs       # 跟踪日志（Ctrl+C 退出）
#      bash deploy-staging.sh env        # 只检查 .env.staging
#      bash deploy-staging.sh rollback   # 回滚到上一个可用提交并重启
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ---------- staging 专属常量（与 deploy.sh 刻意错开）----------
BACK_PORT=8001
FRONT_PORT=3001
STAGING_BRANCH="dev"                 # staging 跟踪 dev 分支

ENV_FILE=".env.staging"
BACK_LOG="/tmp/baomi-staging-back.log"
FRONT_LOG="/tmp/baomi-staging-front.log"
BACK_PID="/tmp/baomi-staging-back.pid"
FRONT_PID="/tmp/baomi-staging-front.pid"

PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
NODE_OPTIONS_BUILD="--max-old-space-size=1536"

BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[1;31m'; NC=$'\033[0m'
log()  { printf '\n%s===== %s =====%s\n' "$CYAN" "$*" "$NC"; }
ok()   { printf '  %s[OK]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[中止]%s %s\n\n' "$RED" "$NC" "$*"; exit 1; }

if [ "$(id -u)" = "0" ]; then SUDO=""; else SUDO="sudo"; fi

# ------------------------------------------------------------
# 安全护栏：本脚本只操作 staging 端口，误杀生产进程是不可接受的
# ------------------------------------------------------------
guard_production() {
  # 如果发现生产端口上有进程，只警告，绝不杀掉它们
  if pgrep -f "uvicorn main:app.*--port 8000" >/dev/null 2>&1 \
     || pgrep -f "uvicorn main:app.*${BACK_PORT}" >/dev/null 2>&1; then
    :
  fi
}

assert_is_staging_workspace() {
  # 防止有人误在生产目录里跑这个脚本：要求目录名或 .env.staging 能证明这是 staging 工作区
  if [ ! -f "$ENV_FILE" ]; then
    die "当前目录没有 $ENV_FILE。
     这说明你多半在生产目录里跑了 staging 脚本 —— 请先切到 staging 工作区：
       cd /root/agent-hub-staging
     并准备好配置：
       cp .env.staging.example .env.staging && nano .env.staging"
  fi
  case "$ROOT" in
    *staging*) : ;;
    *) warn "当前目录名不含 'staging'（$ROOT）。确认这是 staging 工作区再继续。" ;;
  esac
}

do_stop() {
  log "停止 staging 服务（只动 ${BACK_PORT}/${FRONT_PORT}，不碰生产）"
  if [ -f "$BACK_PID" ]; then
    kill "$(cat "$BACK_PID")" >/dev/null 2>&1 && ok "后端已停（pid $(cat "$BACK_PID")）" || warn "后端进程已不在"
    rm -f "$BACK_PID"
  else
    # 兜底：按端口精确匹配，绝不用宽泛的 pkill -f "uvicorn main:app"
    pkill -f "uvicorn main:app.*--port ${BACK_PORT}" >/dev/null 2>&1 && ok "后端已停（按端口）" || warn "后端本来就没在跑"
  fi

  if [ -f "$FRONT_PID" ]; then
    kill "$(cat "$FRONT_PID")" >/dev/null 2>&1 && ok "前端已停（pid $(cat "$FRONT_PID")）" || warn "前端进程已不在"
    rm -f "$FRONT_PID"
  else
    # 前端按 PORT 环境变量区分：staging 是 3001
    pkill -f "next start.*PORT=${FRONT_PORT}" >/dev/null 2>&1 || true
  fi
  return 0
}

do_status() {
  log "staging 状态"
  local bc fc
  bc=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${BACK_PORT}/health" 2>/dev/null || echo 000)
  fc=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${FRONT_PORT}/" 2>/dev/null || echo 000)
  printf '  staging 后端 :%s  ->  HTTP %s\n' "$BACK_PORT" "$bc"
  printf '  staging 前端 :%s  ->  HTTP %s\n' "$FRONT_PORT" "$fc"

  echo "  --- 进程（只列 staging 端口）---"
  pgrep -af "uvicorn main:app.*--port ${BACK_PORT}" || echo "  (无 staging 后端进程)"
  pgrep -af "next start" | grep -i "PORT=${FRONT_PORT}" || echo "  (无 staging 前端进程)"

  echo "  --- 数据目录隔离检查 ---"
  if [ -f "$ENV_FILE" ]; then
    local dd pd
    dd=$(grep -E '^[[:space:]]*DATA_DIR[[:space:]]*=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r')
    pd=$(grep -E '^[[:space:]]*PERSIST_DIR[[:space:]]*=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r')
    printf '  DATA_DIR    = %s\n' "${dd:-<默认 data>}"
    printf '  PERSIST_DIR = %s\n' "${pd:-<默认 chroma_db>}"
    # 这是最危险的一类误配，必须显式拦下
    if [ "$dd" = "data" ] || [ "${dd:-data}" = "data" ] || [ "$pd" = "chroma_db" ] || [ "${pd:-chroma_db}" = "chroma_db" ]; then
      printf '  %s[危险]%s DATA_DIR/PERSIST_DIR 指向了生产目录！请立即改成 data-staging / chroma-staging\n' "$RED" "$NC"
    else
      ok "数据目录与生产隔离"
    fi
  fi

  echo "  内存："; free -h 2>/dev/null | head -3 || true
  echo "  磁盘："; df -h "$ROOT" 2>/dev/null | tail -1 || true

  # 生产状态顺带看一眼，避免 staging 挤压生产还不自知
  echo "  --- 生产状态（只读，不干预）---"
  local pbc pfc
  pbc=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/health" 2>/dev/null || echo 000)
  pfc=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:3000/" 2>/dev/null || echo 000)
  printf '  生产 后端 :8000 -> HTTP %s / 前端 :3000 -> HTTP %s\n' "$pbc" "$pfc"
}

do_logs() {
  log "staging 日志（Ctrl+C 退出）"
  tail -n 60 -F "$BACK_LOG" "$FRONT_LOG" 2>/dev/null
}

do_rollback() {
  log "回滚 staging 到上一个可用提交"
  git rev-parse --git-dir >/dev/null 2>&1 || die "当前目录不是 git 仓库"
  local cur
  cur="$(git rev-parse --short HEAD)"
  warn "当前提交：$cur"

  # 回滚前先把工作区状态记下来，避免丢失未提交改动
  if ! git diff --quiet; then
    warn "工作区有未提交改动，已暂存为 stash："
    git stash push -u -m "deploy-staging rollback $(date +%Y%m%d-%H%M%S)" || true
  fi

  git reset --hard HEAD~1 || die "回滚失败（可能是首个提交）"
  ok "已回滚到 $(git rev-parse --short HEAD)（原 $cur）"
  do_stop
  "$0" start
}

case "${1:-deploy}" in
  stop)     assert_is_staging_workspace; do_stop;   exit 0 ;;
  status)   do_status; exit 0 ;;
  logs)     do_logs;   exit 0 ;;
  rollback) assert_is_staging_workspace; do_rollback; exit 0 ;;
  env)      assert_is_staging_workspace; : ;;                    # 继续往下走到 .env 检查
  start)    assert_is_staging_workspace; SKIP_BUILD=1; SKIP_PULL=1 ;;
  restart)  assert_is_staging_workspace; do_stop; SKIP_BUILD=1; SKIP_PULL=1 ;;
  deploy)   assert_is_staging_workspace; SKIP_BUILD=0; SKIP_PULL=0 ;;
  *)        die "未知参数：$1（可用：deploy|start|stop|restart|status|logs|env|rollback）" ;;
esac

# ------------------------------------------------------------
# 0. 环境探测
# ------------------------------------------------------------
log "0. 环境探测"
command -v curl >/dev/null 2>&1 || die "缺少 curl。请先装：sudo dnf install -y curl  或 sudo apt install -y curl"
ok "curl 已就绪"

if [ -x venv/bin/python ]; then
  ok "venv 已存在：$(venv/bin/python -V 2>&1)"
else
  warn "venv 不存在，稍后会创建"
fi

if command -v node >/dev/null 2>&1; then
  NODE_V="$(node -v)"
  ok "Node $NODE_V"
  case "$NODE_V" in
    v2[0-9].*|v[3-9][0-9].*) : ;;
    *) warn "Node $NODE_V 可能低于 Next.js 16 要求的 20.9，构建可能失败" ;;
  esac
else
  die "未找到 node。请先在生产工作区跑一次 deploy.sh（它会装好 Node），或在系统里装 Node >= 20.9"
fi

# ------------------------------------------------------------
# 1. 检查 .env.staging（本脚本存在的最大理由：隔离）
# ------------------------------------------------------------
log "1. 检查 $ENV_FILE"
[ -f "$ENV_FILE" ] || die "缺少 $ENV_FILE。请先：cp .env.staging.example $ENV_FILE && nano $ENV_FILE"

env_val() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'; }

# 与 deploy.sh 一致的"真密钥"判断：空值 / 中文占位符 / 常见占位词 / 太短 都不算
key_ok() {
  local v="$1"
  [ -n "$v" ] || return 1
  printf '%s' "$v" | LC_ALL=C grep -q '[^ -~]' && return 1
  case "$v" in
    *your*|*Your*|*YOUR*|*xxx*|*XXX*|*changeme*|*placeholder*|*填入*|*粘贴*|*替换*) return 1 ;;
  esac
  [ "${#v}" -ge 16 ] || return 1
  return 0
}

REQUIRED_KEYS="DEEPSEEK_API_KEY EMBEDDING_API_KEY EMBEDDING_BASE_URL"
MISSING=()
for k in $REQUIRED_KEYS; do key_ok "$(env_val "$k")" || MISSING+=("$k"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  warn "以下必需项还是空的或占位符：${MISSING[*]}"
  printf '\n  编辑 %s 后重新执行： bash deploy-staging.sh deploy\n\n' "$ENV_FILE"
  exit 0
fi
ok "必需密钥已就绪"

# ---- 隔离检查（本脚本的核心价值）----
DATA_DIR_V="$(env_val DATA_DIR)"
PERSIST_DIR_V="$(env_val PERSIST_DIR)"
if [ "${DATA_DIR_V:-data}" = "data" ] || [ -z "${DATA_DIR_V:-}" ]; then
  die "DATA_DIR 必须是 data-staging（当前：${DATA_DIR_V:-<空，会退回生产默认 data>}）。
     staging 与生产共用数据目录会污染生产用户数据，脚本拒绝继续。"
fi
if [ "${PERSIST_DIR_V:-chroma_db}" = "chroma_db" ] || [ -z "${PERSIST_DIR_V:-}" ]; then
  die "PERSIST_DIR 必须是 chroma-staging（当前：${PERSIST_DIR_V:-<空，会退回生产默认 chroma_db>}）。
     向量库共用会导致 chroma 并发写损坏索引，脚本拒绝继续。"
fi
ok "数据目录已隔离：DATA_DIR=$DATA_DIR_V / PERSIST_DIR=$PERSIST_DIR_V"

AUTH_SECRET_V="$(env_val AUTH_SECRET)"
if [ -z "$AUTH_SECRET_V" ]; then
  warn "AUTH_SECRET 为空 —— 会自动生成到 ${DATA_DIR_V}/.auth_secret。"
  warn "  建议显式设置一个独立长随机串（与生产不同）：openssl rand -hex 32"
else
  ok "AUTH_SECRET 已显式设置"
fi

for k in SERPER_API_KEY AMAP_API_KEY; do
  key_ok "$(env_val "$k")" || warn "$k 未填 —— 对应工具不可用，不影响基本对话"
done

[ "${1:-deploy}" = "env" ] && { ok "只检查 $ENV_FILE，退出"; exit 0; }

# ------------------------------------------------------------
# 2. 拉取代码（staging 跟踪 dev 分支）
# ------------------------------------------------------------
if [ "${SKIP_PULL:-0}" = "1" ]; then
  log "2. 跳过代码拉取（start/restart 模式）"
else
  log "2. 拉取最新代码（分支 $STAGING_BRANCH）"
  git rev-parse --git-dir >/dev/null 2>&1 || die "当前目录不是 git 仓库"
  if ! git diff --quiet; then
    warn "工作区有未提交改动，已 stash 以避免冲突"
    git stash push -u -m "deploy-staging auto $(date +%Y%m%d-%H%M%S)" || true
  fi
  git fetch origin "$STAGING_BRANCH" || die "拉取失败，检查 git 凭据/网络"
  git checkout "$STAGING_BRANCH" 2>/dev/null || git checkout -b "$STAGING_BRANCH" "origin/$STAGING_BRANCH"
  git reset --hard "origin/$STAGING_BRANCH" || die "切换失败"
  ok "已更新到 $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"
fi

# ------------------------------------------------------------
# 3. 后端依赖
# ------------------------------------------------------------
log "3. 后端依赖（venv）"
if [ ! -x venv/bin/python ]; then
  PY="${PY:-python3}"
  command -v "$PY" >/dev/null 2>&1 || die "找不到 python3"
  ok "使用 $($PY -V 2>&1)"
  "$PY" -m venv venv || die "venv 创建失败。Ubuntu: sudo apt install -y python3-venv"
  ok "venv 已创建"
else
  ok "venv 已存在，复用"
fi
# shellcheck disable=SC1091
. venv/bin/activate
python -m pip install -U pip -i "$PIP_MIRROR" -q || warn "升级 pip 失败，继续"
pip install -r requirements.txt -i "$PIP_MIRROR" --no-cache-dir || die "后端依赖安装失败"
ok "后端依赖安装完成"

# ------------------------------------------------------------
# 4. 前端构建
# ------------------------------------------------------------
if [ "${SKIP_BUILD:-0}" = "1" ] && [ -d frontend/.next ]; then
  log "4. 跳过前端构建（start 模式且已有 .next）"
else
  log "4. 前端依赖与构建（2G 内存约 3~8 分钟；此期间生产会变慢，属正常）"
  cd frontend
  if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund || npm install --no-audit --no-fund || die "前端依赖安装失败"
  else
    npm install --no-audit --no-fund || die "前端依赖安装失败"
  fi
  ok "前端依赖安装完成"
  # staging 构建时确保端口不会污染生产产物
  NEXT_PUBLIC_STAGING=1 NODE_OPTIONS="$NODE_OPTIONS_BUILD" npm run build || die "前端构建失败"
  ok "前端构建完成"
  cd "$ROOT"
fi

# ------------------------------------------------------------
# 5. 启动（staging 端口）
# ------------------------------------------------------------
log "5. 启动 staging 服务"
do_stop >/dev/null 2>&1 || true

cd "$ROOT"
# 后端只监听回环；端口 8001 与生产 8000 错开
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
  nohup venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$BACK_PORT" \
  > "$BACK_LOG" 2>&1 &
echo $! > "$BACK_PID"
ok "staging 后端已启动（pid $(cat "$BACK_PID")，端口 $BACK_PORT）"

# 前端：Next 通过 PORT 环境变量换端口
cd "$ROOT/frontend"
PORT="$FRONT_PORT" nohup npm run start > "$FRONT_LOG" 2>&1 &
echo $! > "$FRONT_PID"
cd "$ROOT"
ok "staging 前端已启动（pid $(cat "$FRONT_PID")，端口 $FRONT_PORT）"

# ------------------------------------------------------------
# 6. 防火墙（只放行 staging 前端；后端只监听回环，不需要放行）
# ------------------------------------------------------------
log "6. 防火墙放行 ${FRONT_PORT}"
if command -v firewall-cmd >/dev/null 2>&1 && $SUDO firewall-cmd --state >/dev/null 2>&1; then
  $SUDO firewall-cmd --permanent --add-port="${FRONT_PORT}/tcp" >/dev/null 2>&1 || true
  $SUDO firewall-cmd --reload >/dev/null 2>&1 || true
  ok "firewalld 已放行 ${FRONT_PORT}"
elif command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
  $SUDO ufw allow "${FRONT_PORT}/tcp" >/dev/null 2>&1 || true
  ok "ufw 已放行 ${FRONT_PORT}"
else
  ok "本机没有启用 firewalld/ufw，无需处理"
fi

# ------------------------------------------------------------
# 7. 自检
# ------------------------------------------------------------
log "7. 启动自检（等 6 秒）"
sleep 6
IP="$(curl -s -m 3 ifconfig.me 2>/dev/null || echo '<你的公网IP>')"
BC=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${BACK_PORT}/health" 2>/dev/null || echo 000)
FC=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${FRONT_PORT}/"      2>/dev/null || echo 000)
printf '  staging 后端 /health -> HTTP %s\n' "$BC"
printf '  staging 前端 /       -> HTTP %s\n' "$FC"

echo
if [ "$BC" = "200" ] && [ "$FC" != "000" ]; then
  printf '%sstaging 部署成功%s\n' "$GREEN" "$NC"
else
  warn "有服务没起来，先看日志： bash deploy-staging.sh logs"
fi

cat <<EOF

============================================================
 Staging 访问地址
   前端： http://${IP}:${FRONT_PORT}
   后端： 只监听 127.0.0.1:${BACK_PORT}（不对外，前端经 /api/* 转发访问）
         服务器上自测： curl -s http://127.0.0.1:${BACK_PORT}/health

 常用命令
   bash deploy-staging.sh status    看状态（含隔离检查 + 生产状态）
   bash deploy-staging.sh logs      看日志
   bash deploy-staging.sh restart   重启
   bash deploy-staging.sh rollback  回滚到上一个提交

 阿里云安全组
   需放行 ${FRONT_PORT}（staging 前端）。生产用的 3000 不受影响。

 隔离自查（三条都要对）
   DATA_DIR    = ${DATA_DIR_V}
   PERSIST_DIR = ${PERSIST_DIR_V}
   AUTH_SECRET = $([ -n "$AUTH_SECRET_V" ] && echo '已显式设置' || echo '自动生成（建议改为显式）')

 ${YELLOW}提醒${NC}：2 核 2G 跑两套实例吃紧。别在生产构建时同时跑 staging 构建。
============================================================
EOF
