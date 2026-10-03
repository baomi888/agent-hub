#!/usr/bin/env bash
# ============================================================
#  苞米 Agent · 阿里云 ECS 一键部署脚本（2 核 2G 优化）
#
#  用法（在服务器上的项目目录里执行）：
#      bash deploy.sh            # 完整部署：环境准备 -> 装依赖 -> 构建 -> 启动
#      bash deploy.sh start      # 只启动
#      bash deploy.sh stop       # 只停止
#      bash deploy.sh restart    # 重启
#      bash deploy.sh status     # 看状态
#      bash deploy.sh logs       # 跟踪日志（Ctrl+C 退出）
#      bash deploy.sh env        # 只生成/检查 .env
#
#  说明：本脚本会自动补齐运行环境（Node >= 20.9、Python >= 3.10），
#        因为 Next.js 16 必须 Node 20+，LangChain 1.x 必须 Python 3.10+。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

BACK_PORT=8000
FRONT_PORT=3000
NODE_WANT_VER="v20.19.0"          # 国内走 npmmirror，比 NodeSource 快且稳
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[1;31m'; NC=$'\033[0m'
log()  { printf '\n%s===== %s =====%s\n' "$CYAN" "$*" "$NC"; }
ok()   { printf '  %s[OK]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[中止]%s %s\n\n' "$RED" "$NC" "$*"; exit 1; }

if [ "$(id -u)" = "0" ]; then SUDO=""; else SUDO="sudo"; fi

# ------------------------------------------------------------
# 子命令：stop / status / logs
# ------------------------------------------------------------
do_stop() {
  log "停止服务"
  pkill -f "uvicorn main:app" >/dev/null 2>&1 && ok "后端已停" || warn "后端本来就没在跑"
  pkill -f "next start"       >/dev/null 2>&1 && ok "前端已停" || warn "前端本来就没在跑"
  pkill -f "next-server"      >/dev/null 2>&1 || true
  rm -f /tmp/baomi-back.pid /tmp/baomi-front.pid
}

do_status() {
  log "服务状态"
  local bc fc
  bc=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${BACK_PORT}/health" 2>/dev/null || echo 000)
  fc=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${FRONT_PORT}/" 2>/dev/null || echo 000)
  printf '  后端 :%s  ->  HTTP %s\n' "$BACK_PORT" "$bc"
  printf '  前端 :%s  ->  HTTP %s\n' "$FRONT_PORT" "$fc"
  echo "  --- 进程 ---"
  pgrep -af "uvicorn main:app" || echo "  (无后端进程)"
  pgrep -af "next start"      || echo "  (无前端进程)"
  echo "  内存："; free -h | head -3
  echo "  磁盘："; df -h "$ROOT" | tail -1
}

do_logs() {
  log "日志（Ctrl+C 退出）"
  tail -n 60 -F /tmp/baomi-back.log /tmp/baomi-front.log 2>/dev/null
}

case "${1:-deploy}" in
  stop)    do_stop;   exit 0 ;;
  status)  do_status; exit 0 ;;
  logs)    do_logs;   exit 0 ;;
  restart) do_stop; "$0" start; exit 0 ;;
  env)     : ;;                       # 继续往下走到 .env 环节
  start)   SKIP_BUILD=1 ;;
  deploy)  SKIP_BUILD=0 ;;
  *)       die "未知参数：$1（可用：deploy|start|stop|restart|status|logs|env）" ;;
esac

# ------------------------------------------------------------
# 0. 系统信息 + 网络工具
# ------------------------------------------------------------
log "0. 环境探测"
command -v curl >/dev/null 2>&1 || die "缺少 curl。请先装：sudo dnf install -y curl  或  sudo apt install -y curl"
ok "curl 已就绪"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) NODE_ARCH="x64" ;;
  aarch64|arm64) NODE_ARCH="arm64" ;;
  *) die "不支持的 CPU 架构：$ARCH" ;;
esac
if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  ok "系统：${PRETTY_NAME:-$ARCH}"
else
  ok "系统：$ARCH"
fi
ok "架构：$ARCH（Node 用 $NODE_ARCH 包）"

# ------------------------------------------------------------
# 1. 补齐 Node（Next.js 16 硬性要求 >= 20.9）
# ------------------------------------------------------------
log "1. 检查 Node.js（Next.js 16 需要 >= 20.9）"
NODE_OK=0
if command -v node >/dev/null 2>&1; then
  NV="$(node -v | sed 's/^v//')"
  NMAJ="${NV%%.*}"
  if [ "${NMAJ:-0}" -ge 20 ] 2>/dev/null; then
    ok "已有 Node v$NV"
    NODE_OK=1
  else
    warn "现有 Node v$NV 太旧，需要升级到 20+"
  fi
else
  warn "未检测到 Node.js"
fi

if [ "$NODE_OK" = "0" ]; then
  echo "  -> 正在从 npmmirror 下载 Node ${NODE_WANT_VER}（约 25MB）…"
  TMP_NODE="/tmp/node-${NODE_WANT_VER}-linux-${NODE_ARCH}.tar.xz"
  if curl -fL --retry 3 --connect-timeout 15 -o "$TMP_NODE" \
      "https://cdn.npmmirror.com/binaries/node/${NODE_WANT_VER}/node-${NODE_WANT_VER}-linux-${NODE_ARCH}.tar.xz"; then
    $SUDO mkdir -p /usr/local
    $SUDO tar -xJf "$TMP_NODE" -C /usr/local --strip-components=1 \
      --exclude=CHANGELOG.md --exclude=LICENSE --exclude=README.md || die "解压 Node 失败"
    rm -f "$TMP_NODE"
    hash -r
    ok "Node 已安装：$(node -v) / npm $(npm -v)"
  else
    die "下载 Node 失败。请手动装 Node 20 后重跑本脚本，例如：
       curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - && sudo dnf install -y nodejs
     或
       curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt install -y nodejs"
  fi
fi

# ------------------------------------------------------------
# 2. 补齐 Python（LangChain 1.x 硬性要求 >= 3.10）
# ------------------------------------------------------------
log "2. 检查 Python（LangChain 1.x 需要 >= 3.10）"
pick_py() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}
PY="$(pick_py || true)"
if [ -z "$PY" ]; then
  warn "没有 >= 3.10 的 Python，尝试用包管理器安装 3.11 …"
  if command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3.11 python3.11-pip >/dev/null 2>&1 || warn "dnf 安装 python3.11 失败"
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3.11 python3.11-pip >/dev/null 2>&1 || warn "yum 安装 python3.11 失败"
  elif command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y >/dev/null 2>&1 || true
    $SUDO apt-get install -y python3.11 python3.11-venv >/dev/null 2>&1 || warn "apt 安装 python3.11 失败"
  fi
  hash -r
  PY="$(pick_py || true)"
fi
[ -n "$PY" ] || die "仍找不到 Python >= 3.10。
     阿里云 Linux 3 / CentOS 可执行： sudo dnf install -y python3.11 python3.11-pip
     Ubuntu 可执行：            sudo apt update && sudo apt install -y python3.11 python3.11-venv
     装完再重跑：bash deploy.sh"
ok "使用 $PY（$($PY -V 2>&1)）"

# ------------------------------------------------------------
# 3. 交换分区（2G 内存的保命符，否则 next build 必 OOM）
# ------------------------------------------------------------
log "3. 检查 swap（2 核 2G 必做，否则前端构建会内存溢出）"
if swapon --show 2>/dev/null | grep -q .; then
  ok "swap 已存在："; swapon --show | tail -n +1 | sed 's/^/     /'
else
  warn "没有 swap，创建 2G（约 1 分钟）"
  if $SUDO fallocate -l 2G /swapfile 2>/dev/null || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
    $SUDO chmod 600 /swapfile
    $SUDO mkswap /swapfile >/dev/null
    $SUDO swapon /swapfile
    grep -q '^/swapfile' /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
    ok "swap 已启用并写入 /etc/fstab"
  else
    warn "创建 swap 失败（不影响继续，但构建有 OOM 风险）"
  fi
fi

# ------------------------------------------------------------
# 4. .env（没有就生成模板并要求填好后再跑）
# ------------------------------------------------------------
log "4. 检查 .env（API Key）"
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    printf '\n%s请先填写 API Key，然后重新执行 bash deploy.sh%s\n\n' "$YELLOW" "$NC"
    printf '    nano .env      （或用 vim .env）\n\n'
    printf '  必须填的三项：\n'
    printf '    DEEPSEEK_API_KEY   /  EMBEDDING_API_KEY   /  SERPER_API_KEY\n\n'
    printf '  可选：DEEPSEEK_MODEL、EMBEDDING_MODEL、EMBEDDING_BASE_URL、AMAP_API_KEY\n'
    printf '  （其它项 CHUNK_SIZE / TOP_K / DATA_DIR 等保持默认即可）\n\n'
    exit 0
  else
    die "既没有 .env 也没有 .env.example，无法继续"
  fi
fi
env_val() { grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'; }
# 判断一个值是不是"真的密钥"：空值 / 中文占位符 / 常见占位词 / 太短 都不算
# （.env.example 里的占位文本是"在此粘贴你的 xxx_key"，必须能识别出来）
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
# 这三项和后端 core/config.py 的 validate() 保持一致
REQUIRED_KEYS="DEEPSEEK_API_KEY EMBEDDING_API_KEY EMBEDDING_BASE_URL"
MISSING=()
for k in $REQUIRED_KEYS; do key_ok "$(env_val "$k")" || MISSING+=("$k"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  warn "以下必需项还是空的或占位符：${MISSING[*]}"
  printf '\n  各项含义：\n'
  printf '    DEEPSEEK_API_KEY    DeepSeek 对话密钥\n'
  printf '    EMBEDDING_API_KEY   向量化密钥（阿里百炼 DashScope；DeepSeek 不提供 embedding）\n'
  printf '    EMBEDDING_BASE_URL  向量化服务地址，例：\n'
  printf '                        https://dashscope.aliyuncs.com/compatible-mode/v1\n'
  printf '\n  编辑好 .env 后重新执行： bash deploy.sh\n\n'
  exit 0
fi
ok ".env 关键项已就绪（模型 $(env_val DEEPSEEK_MODEL)）"
for k in SERPER_API_KEY AMAP_API_KEY; do
  key_ok "$(env_val "$k")" || warn "$k 未填 —— 对应的「联网搜索 / 天气」工具会不可用，不影响基本对话"
done
[ "${1:-deploy}" = "env" ] && { ok "只检查 .env，退出"; exit 0; }

# ------------------------------------------------------------
# 5. 后端：venv + 依赖
# ------------------------------------------------------------
log "5. 后端依赖（venv）"
if [ ! -x venv/bin/python ]; then
  if ! "$PY" -m venv venv 2>/tmp/baomi-venv-err.log; then
    warn "venv 创建失败，多半是缺 venv 组件，尝试自动补装 …"
    if command -v apt-get >/dev/null 2>&1; then
      $SUDO apt-get update -y >/dev/null 2>&1 || true
      $SUDO apt-get install -y "${PY}-venv" >/dev/null 2>&1 || $SUDO apt-get install -y python3-venv >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
      $SUDO dnf install -y "${PY}-pip" "${PY}-devel" >/dev/null 2>&1 || true
    fi
    hash -r
    "$PY" -m venv venv || {
      echo "  --- venv 报错详情 ---"; cat /tmp/baomi-venv-err.log 2>/dev/null | tail -5
      die "仍无法创建 venv。
     Ubuntu:  sudo apt install -y ${PY}-venv
     CentOS/阿里云Linux: sudo dnf install -y ${PY}-pip ${PY}-devel"
    }
  fi
  ok "venv 已创建（$($PY -V 2>&1)）"
else
  ok "venv 已存在，复用"
fi
# shellcheck disable=SC1091
. venv/bin/activate
python -m pip install -U pip -i "$PIP_MIRROR" -q || warn "升级 pip 失败，继续"
pip install -r requirements.txt -i "$PIP_MIRROR" --no-cache-dir || die "后端依赖安装失败，请把上面的报错发我"
ok "后端依赖安装完成"

# ------------------------------------------------------------
# 6. 前端：依赖 + 生产构建
# ------------------------------------------------------------
if [ "${SKIP_BUILD:-0}" = "1" ] && [ -d frontend/.next ]; then
  log "6. 跳过前端构建（start 模式且已有 .next）"
else
  log "6. 前端依赖与构建（2G 内存约 3~8 分钟，请耐心等）"
  cd frontend
  if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund || npm install --no-audit --no-fund || die "前端依赖安装失败"
  else
    npm install --no-audit --no-fund || die "前端依赖安装失败"
  fi
  ok "前端依赖安装完成"
  NODE_OPTIONS="--max-old-space-size=1536" npm run build || die "前端构建失败，请把上面的报错发我"
  ok "前端构建完成"
  cd "$ROOT"
fi

# ------------------------------------------------------------
# 7. 启动（后台常驻）
# ------------------------------------------------------------
log "7. 启动服务"
do_stop >/dev/null 2>&1 || true

cd "$ROOT"
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
  nohup venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port "$BACK_PORT" \
  > /tmp/baomi-back.log 2>&1 &
echo $! > /tmp/baomi-back.pid
ok "后端已启动（pid $(cat /tmp/baomi-back.pid)）"

cd "$ROOT/frontend"
PORT="$FRONT_PORT" nohup npm run start > /tmp/baomi-front.log 2>&1 &
echo $! > /tmp/baomi-front.pid
cd "$ROOT"
ok "前端已启动（pid $(cat /tmp/baomi-front.pid)）"

# ------------------------------------------------------------
# 8. 本机防火墙（安全组之外的第二道门，很多人卡在这）
# ------------------------------------------------------------
log "8. 本机防火墙放行"
if command -v firewall-cmd >/dev/null 2>&1 && $SUDO firewall-cmd --state >/dev/null 2>&1; then
  $SUDO firewall-cmd --permanent --add-port=${FRONT_PORT}/tcp >/dev/null 2>&1 || true
  $SUDO firewall-cmd --permanent --add-port=${BACK_PORT}/tcp  >/dev/null 2>&1 || true
  $SUDO firewall-cmd --reload >/dev/null 2>&1 || true
  ok "firewalld 已放行 ${FRONT_PORT} / ${BACK_PORT}"
elif command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
  $SUDO ufw allow ${FRONT_PORT}/tcp >/dev/null 2>&1 || true
  $SUDO ufw allow ${BACK_PORT}/tcp  >/dev/null 2>&1 || true
  ok "ufw 已放行 ${FRONT_PORT} / ${BACK_PORT}"
else
  ok "本机没有启用 firewalld/ufw，无需处理"
fi

# ------------------------------------------------------------
# 9. 自检
# ------------------------------------------------------------
log "9. 启动自检（等 6 秒）"
sleep 6
IP="$(curl -s -m 3 ifconfig.me 2>/dev/null || echo '<你的公网IP>')"
BC=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${BACK_PORT}/health" 2>/dev/null || echo 000)
FC=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${FRONT_PORT}/"      2>/dev/null || echo 000)
printf '  后端 /health  -> HTTP %s\n' "$BC"
printf '  前端 /        -> HTTP %s\n' "$FC"

echo
if [ "$BC" = "200" ] && [ "$FC" != "000" ]; then
  printf '%s部署成功%s\n\n' "$GREEN" "$NC"
else
  warn "有一个服务没起来，先看日志： bash deploy.sh logs"
fi

cat <<EOF

============================================================
 访问地址
   前端： http://${IP}:${FRONT_PORT}
   后端： http://${IP}:${BACK_PORT}/health

 常用命令
   bash deploy.sh status    看状态
   bash deploy.sh logs      看日志
   bash deploy.sh restart   重启
   bash deploy.sh stop      停止

 还打不开？按顺序查这三件事
   1) 阿里云控制台 -> 安全组 -> 入方向，放行 ${FRONT_PORT} 和 ${BACK_PORT}（授权对象 0.0.0.0/0）
   2) 确认实例有公网带宽 / 已绑定弹性公网 IP
   3) 看日志：tail -50 /tmp/baomi-front.log
============================================================
EOF
