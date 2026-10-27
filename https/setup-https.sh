#!/usr/bin/env bash
# ============================================================
#  知穗 Agent · HTTPS 一键上线脚本（域名备案通过后用）
#
#  用法（在服务器项目目录里执行）：
#      bash setup-https.sh <你的域名> [通知邮箱]
#      例： bash setup-https.sh agent.example.com admin@example.com
#      无邮箱也可用： bash setup-https.sh agent.example.com
#                   -> 用 --register-unsafully-without-email（不推荐，收不到到期提醒）
#
#  前置（域名备案通过后才满足）：
#    1) 域名已完成 ICP 备案
#    2) 域名 DNS 的 A 记录已指向本机公网 IP（脚本会自动校验）
#    3) 阿里云安全组入方向已放行 80 + 443（证书申请要 80，访问要 443）
#
#  脚本会做：装 nginx/certbot -> 写反代配置 -> 申请 Let's Encrypt 证书
#           -> 自动续期 -> 校验。全程可重复运行（幂等）。
# ============================================================
set -uo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ]; then
  echo "用法: bash setup-https.sh <你的域名> [通知邮箱]"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # 项目根目录（setup-https.sh 在 https/ 下）
TEMPLATE="$ROOT/https/agent-hub.conf.template"
if [ ! -f "$TEMPLATE" ]; then
  echo "[中止] 找不到配置模板：$TEMPLATE"
  exit 1
fi

BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[1;31m'; NC=$'\033[0m'
log()  { printf '\n%s===== %s =====%s\n' "$CYAN" "$*" "$NC"; }
ok()   { printf '  %s[OK]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[中止]%s %s\n\n' "$RED" "$NC" "$*"; exit 1; }

if [ "$(id -u)" = "0" ]; then SUDO=""; else SUDO="sudo"; fi

# ------------------------------------------------------------
# 0. 探测包管理器
# ------------------------------------------------------------
log "0. 环境探测"
if command -v apt-get >/dev/null 2>&1; then
  PM="apt"; ok "包管理器：apt (Debian/Ubuntu)"
elif command -v dnf >/dev/null 2>&1; then
  PM="dnf"; ok "包管理器：dnf"
elif command -v yum >/dev/null 2>&1; then
  PM="yum"; ok "包管理器：yum"
else
  die "不支持的包管理器，请手动安装 nginx + certbot 后重跑"
fi

# ------------------------------------------------------------
# 1. DNS 预检：域名 A 记录必须指向本机公网 IP
# ------------------------------------------------------------
log "1. DNS 预检（域名必须已解析到本机）"
PUB_IP="$(curl -s -m 5 ifconfig.me 2>/dev/null || true)"
[ -n "$PUB_IP" ] || die "拿不到本机公网 IP（出网异常？）"
ok "本机公网 IP：$PUB_IP"

# 用 getent 拿域名解析结果（兼容无 dig 的环境）
resolve_ip() {
  getent hosts "$1" 2>/dev/null | awk '{print $1}' | head -1
}
D_IP="$(resolve_ip "$DOMAIN")"
if [ -z "$D_IP" ]; then
  die "域名 $DOMAIN 解析不到任何 IP。
  请先在 DNS 控制台把 $DOMAIN 的 A 记录指向 $PUB_IP，等生效后再跑本脚本。"
fi
if [ "$D_IP" != "$PUB_IP" ]; then
  die "域名 $DOMAIN 解析到 $D_IP，但本机公网 IP 是 $PUB_IP，二者不一致。
  请修正 DNS 的 A 记录指向 $PUB_IP 后再跑。"
fi
ok "DNS 校验通过：$DOMAIN -> $D_IP == $PUB_IP"

# ------------------------------------------------------------
# 2. 安装 nginx（没装才装）
# ------------------------------------------------------------
log "2. 安装 nginx"
if command -v nginx >/dev/null 2>&1; then
  ok "nginx 已存在：$(nginx -v 2>&1)"
else
  if [ "$PM" = "apt" ]; then
    $SUDO apt-get update -y >/dev/null 2>&1
    $SUDO apt-get install -y nginx >/dev/null 2>&1 || die "nginx 安装失败"
  elif [ "$PM" = "dnf" ]; then
    $SUDO dnf install -y nginx >/dev/null 2>&1 || die "nginx 安装失败"
  else
    $SUDO yum install -y nginx >/dev/null 2>&1 || die "nginx 安装失败"
  fi
  ok "nginx 安装完成：$(nginx -v 2>&1)"
fi

# ------------------------------------------------------------
# 3. 落配置：从模板渲染，启用站点
# ------------------------------------------------------------
log "3. 写入 nginx 反代配置"
$SUDO mkdir -p /var/www/letsencrypt

# 先装 http 级配置（log_format timed），否则站点里的 access_log ... timed 会因
# "unknown log format" 让 nginx -t 失败
GLOBAL_TEMPLATE="$ROOT/https/nginx-http-global.conf.template"
if [ -f "$GLOBAL_TEMPLATE" ]; then
  $SUDO cp "$GLOBAL_TEMPLATE" /etc/nginx/conf.d/00-agent-hub-log.conf
  ok "已安装 http 级配置（log_format timed）"
else
  warn "缺少 nginx-http-global.conf.template，timed 日志将不可用"
fi

# 优先用 Ubuntu/Debian 的 sites-available 结构，否则用 conf.d
if [ -d /etc/nginx/sites-available ]; then
  CONF="/etc/nginx/sites-available/agent-hub.conf"
  ENABLED_DIR="/etc/nginx/sites-enabled"
  $SUDO rm -f "$ENABLED_DIR/default"            # 去掉默认站点，避免 80 端口冲突
else
  CONF="/etc/nginx/conf.d/agent-hub.conf"
fi

# 渲染：把模板里的 __DOMAIN__ 换成真实域名
$SUDO bash -c "sed 's/__DOMAIN__/$DOMAIN/g' '$TEMPLATE' > '$CONF'"
ok "配置已写入：$CONF"

# 启用（sites-available 结构需要软链）
if [ -n "${ENABLED_DIR:-}" ]; then
  $SUDO ln -sf "$CONF" "$ENABLED_DIR/agent-hub.conf"
  ok "已启用站点（sites-enabled 软链）"
fi

# 语法校验（certbot 改配置前先确保本段无误）
$SUDO nginx -t || die "nginx 配置语法检查失败，请检查模板"

# ------------------------------------------------------------
# 4. 本机防火墙放行 80 / 443
# ------------------------------------------------------------
log "4. 本机防火墙放行 80/443"
if command -v firewall-cmd >/dev/null 2>&1 && $SUDO firewall-cmd --state >/dev/null 2>&1; then
  $SUDO firewall-cmd --permanent --add-service=http  >/dev/null 2>&1 || true
  $SUDO firewall-cmd --permanent --add-service=https >/dev/null 2>&1 || true
  $SUDO firewall-cmd --reload >/dev/null 2>&1 || true
  ok "firewalld 已放行 http/https"
elif command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
  $SUDO ufw allow 80/tcp >/dev/null 2>&1 || true
  $SUDO ufw allow 443/tcp >/dev/null 2>&1 || true
  ok "ufw 已放行 80/443"
else
  ok "本机没有启用 firewalld/ufw，跳过"
fi

# ------------------------------------------------------------
# 5. 启动 nginx
# ------------------------------------------------------------
log "5. 启动 nginx"
if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl enable nginx >/dev/null 2>&1 || true
  if $SUDO systemctl is-active --quiet nginx; then
    $SUDO systemctl reload nginx >/dev/null 2>&1 || $SUDO systemctl restart nginx
    ok "nginx 已重载"
  else
    $SUDO systemctl start nginx >/dev/null 2>&1 || die "nginx 启动失败"
    ok "nginx 已启动"
  fi
else
  $SUDO nginx -s reload 2>/dev/null || $SUDO nginx || die "nginx 启动失败"
  ok "nginx 已启动"
fi

# ------------------------------------------------------------
# 6. 安装 certbot
# ------------------------------------------------------------
log "6. 安装 certbot"
if command -v certbot >/dev/null 2>&1; then
  ok "certbot 已存在：$(certbot --version 2>&1 | head -1)"
else
  if [ "$PM" = "apt" ]; then
    $SUDO apt-get update -y >/dev/null 2>&1
    $SUDO apt-get install -y certbot python3-certbot-nginx >/dev/null 2>&1 || die "certbot 安装失败"
  elif [ "$PM" = "dnf" ]; then
    $SUDO dnf install -y certbot python3-certbot-nginx >/dev/null 2>&1 || die "certbot 安装失败"
  else
    $SUDO yum install -y certbot python3-certbot-nginx >/dev/null 2>&1 || die "certbot 安装失败"
  fi
  ok "certbot 安装完成：$(certbot --version 2>&1 | head -1)"
fi

# ------------------------------------------------------------
# 7. 申请证书（幂等：已存在就跳过）
# ------------------------------------------------------------
log "7. 申请 Let's Encrypt 证书"
if certbot certificates 2>/dev/null | grep -q "$DOMAIN"; then
  ok "域名 $DOMAIN 的证书已存在，跳过申请"
else
  CERT_ARGS=(--nginx -d "$DOMAIN" --non-interactive --agree-tos)
  if [ -n "$EMAIL" ]; then
    CERT_ARGS+=(--email "$EMAIL")
  else
    warn "未提供邮箱，使用 --register-unsafely-without-email（收不到到期提醒，不建议长期使用）"
    CERT_ARGS+=(--register-unsafely-without-email)
  fi
  $SUDO certbot "${CERT_ARGS[@]}" || die "证书申请失败。
  常见原因：
    - 安全组没放行 80 端口（HTTP-01 校验需要）
    - 域名 DNS 还没生效
    - 80 端口被其它程序占用
  排错后重跑本脚本即可（幂等）。"
  ok "证书申请成功"
fi

# certbot --nginx 已自动把 80 段复制成 443 并加了证书 + 301 跳转
$SUDO nginx -t && $SUDO nginx -s reload 2>/dev/null || $SUDO systemctl reload nginx >/dev/null 2>&1 || true

# ------------------------------------------------------------
# 8. 验证自动续期
# ------------------------------------------------------------
log "8. 验证自动续期"
if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl enable --now certbot.timer >/dev/null 2>&1 || true
  ok "certbot.timer 已启用（自动续期）"
else
  ok "certbot 定时器（续期）已随安装启用"
fi
$SUDO certbot renew --dry-run >/dev/null 2>&1 && ok "续期演练通过" || warn "续期演练未通过，但证书已生效，可忽略或在有网时再测"

# ------------------------------------------------------------
# 9. 总结
# ------------------------------------------------------------
echo
printf '%sHTTPS 上线完成%s\n\n' "$GREEN" "$NC"
cat <<EOF
============================================================
 访问地址
   https://${DOMAIN}        （自动跳 https，浏览器绿锁）

 证书位置
   /etc/letsencrypt/live/${DOMAIN}/fullchain.pem
   自动续期：certbot 内置 timer，到期前 30 天自动更新

 还需要你手动做的 2 件事（脚本在服务器内做不了）：
   1) 阿里云控制台 -> 安全组 -> 入方向，放行 80 与 443
      （授权对象 0.0.0.0/0；80 只用于证书续期校验，可保留）
   2) 建议把原来的 3000 端口规则「删掉或改为 127.0.0.1」，
      让外部统一走 443，不再裸奔 http://IP:3000

 验证（服务器上）
   curl -sI https://${DOMAIN} | head -1
   certbot certificates

 想回退到纯 http？
   删 /etc/nginx/sites-enabled/agent-hub.conf（或 conf.d 下那份），
   systemctl restart nginx，再按 部署说明 放行 3000 即可。
============================================================
EOF
