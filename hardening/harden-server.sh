#!/usr/bin/env bash
# ============================================================
#  服务器安全加固（分步、可回滚、带锁死守卫）
#
#  用法（root）：
#      bash hardening/harden-server.sh --ssh-key /path/to/id_ed25519.pub
#      bash hardening/harden-server.sh --create-sudo-user deploy
#      bash hardening/harden-server.sh --fail2ban
#      bash hardening/harden-server.sh --firewall
#      bash hardening/harden-server.sh --firewall --after-https   # HTTPS 上线后才可关 3000
#      bash hardening/harden-server.sh --lock-root                # 改 prohibit-password（推荐）
#      bash hardening/harden-server.sh --disable-root             # 彻底禁 root（需 CONFIRM_DISABLE_ROOT=yes）
#
#  ⚠️ 安全约定（脚本会强制）：
#    - --after-https 只有在 443 已在监听时才允许关 3000/8000，否则直接拒绝。
#    - --lock-root / --disable-root 前，必须存在一个「非 root + sudo/wheel 组 +
#      已装 authorized_keys」的用户，否则拒绝（防止把所有人锁在门外）。
#    - 改 sshd 前先备份 + sshd -t 自检，不过自动回滚。
# ============================================================
set -uo pipefail

BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[1;31m'; NC=$'\033[0m'
log()  { printf '\n%s===== %s =====%s\n' "$CYAN" "$*" "$NC"; }
ok()   { printf '  %s[OK]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[拒绝/中止]%s %s\n\n' "$RED" "$NC" "$*"; exit 1; }

[ "$(id -u)" = "0" ] || die "请用 root 运行： sudo bash $0 ..."

SUDO_USER_NAME="${SUDO_USER:-}"
SSH_KEY=""
NEW_USER=""
DO_FAIL2BAN=0
DO_FIREWALL=0
AFTER_HTTPS=0
LOCK_ROOT=0
DISABLE_ROOT=0
DISABLE_PASSWORD_AUTH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --ssh-key)   SSH_KEY="${2:-}"; shift 2 ;;
    --create-sudo-user) NEW_USER="${2:-}"; shift 2 ;;
    --fail2ban)  DO_FAIL2BAN=1; shift ;;
    --firewall)  DO_FIREWALL=1; shift ;;
    --after-https) AFTER_HTTPS=1; shift ;;
    --lock-root) LOCK_ROOT=1; shift ;;
    --disable-root) DISABLE_ROOT=1; shift ;;
    --disable-password-auth) DISABLE_PASSWORD_AUTH=1; shift ;;
    -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

has_nonroot_sudo_key_user() {
  for g in sudo wheel; do
    getent group "$g" >/dev/null 2>&1 || continue
    for u in $(getent group "$g" | cut -d: -f4 | tr ',' ' '); do
      [ "$u" = "root" ] && continue
      home="$(getent passwd "$u" | cut -d: -f6)"
      [ -n "$home" ] && [ -s "$home/.ssh/authorized_keys" ] && return 0
    done
  done
  return 1
}

# ---------------- 1. 装 SSH 公钥 ----------------
if [ -n "$SSH_KEY" ]; then
  log "1. 安装 SSH 公钥"
  [ -f "$SSH_KEY" ] || die "公钥文件不存在：$SSH_KEY"
  target_user="${SUDO_USER_NAME:-root}"
  home="$(getent passwd "$target_user" | cut -d: -f6)"
  [ -n "$home" ] || die "用户不存在：$target_user"
  install -d -m 700 -o "$target_user" -g "$target_user" "$home/.ssh"
  touch "$home/.ssh/authorized_keys"
  grep -qxF "$(cat "$SSH_KEY")" "$home/.ssh/authorized_keys" 2>/dev/null || cat "$SSH_KEY" >> "$home/.ssh/authorized_keys"
  chmod 600 "$home/.ssh/authorized_keys"
  chown "$target_user:$target_user" "$home/.ssh/authorized_keys"
  ok "公钥已写入 $home/.ssh/authorized_keys（用户 $target_user）"
fi

# ---------------- 2. 建 sudo 用户 ----------------
if [ -n "$NEW_USER" ]; then
  log "2. 创建 sudo 用户：$NEW_USER"
  if id "$NEW_USER" >/dev/null 2>&1; then
    warn "用户已存在，跳过创建"
  else
    useradd -m -s /bin/bash "$NEW_USER" || die "useradd 失败"
    ok "用户已创建"
  fi
  if getent group sudo >/dev/null 2>&1; then usermod -aG sudo "$NEW_USER"; else usermod -aG wheel "$NEW_USER"; fi
  ok "已加入 sudo/wheel 组"
  # 复制 root 的 authorized_keys，保证新用户立刻能登录
  if [ -s /root/.ssh/authorized_keys ]; then
    nh="$(getent passwd "$NEW_USER" | cut -d: -f6)"
    install -d -m 700 -o "$NEW_USER" -g "$NEW_USER" "$nh/.ssh"
    cp /root/.ssh/authorized_keys "$nh/.ssh/authorized_keys"
    chown "$NEW_USER:$NEW_USER" "$nh/.ssh/authorized_keys"
    chmod 600 "$nh/.ssh/authorized_keys"
    ok "已继承 root 的 authorized_keys"
  else
    warn "root 没有 authorized_keys，记得给 $NEW_USER 单独装公钥"
  fi
  warn "下一步务必**另开一个终端**验证： ssh $NEW_USER@<公网IP> 能登录、且能 sudo，再继续禁 root"
fi

# ---------------- 3. fail2ban ----------------
if [ "$DO_FAIL2BAN" = "1" ]; then
  log "3. 安装/配置 fail2ban"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y fail2ban >/dev/null 2>&1 || die "fail2ban 安装失败"
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y fail2ban >/dev/null 2>&1 || die "fail2ban 安装失败"
  else
    yum install -y fail2ban >/dev/null 2>&1 || die "fail2ban 安装失败"
  fi
  mkdir -p /etc/fail2ban/jail.d
  cat > /etc/fail2ban/jail.d/sshd.local <<'JAIL'
[sshd]
enabled = true
port = ssh
maxretry = 5
findtime = 10m
bantime = 1h
JAIL
  systemctl enable --now fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban >/dev/null 2>&1 || true
  ok "fail2ban 已启用（sshd: 10 分钟内错 5 次封 1 小时）"
  fail2ban-client status sshd 2>/dev/null | sed 's/^/     /' || warn "fail2ban status 读取失败，稍后手动看"
fi

# ---------------- 4. 防火墙 ----------------
if [ "$DO_FIREWALL" = "1" ]; then
  log "4. 防火墙收敛"
  if [ "$AFTER_HTTPS" = "1" ]; then
    if ! ss -lnt 2>/dev/null | grep -q ':443 '; then
      die "443 未在监听，拒绝关闭 3000/8000。
  先把 HTTPS 跑起来（bash https/setup-https.sh <域名> <邮箱>），确认 443 可访问后再加 --after-https。"
    fi
    ok "443 已在监听，允许收敛"
  fi

  if command -v ufw >/dev/null 2>&1; then
    ufw allow 22/tcp  >/dev/null 2>&1 || true
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    if [ "$AFTER_HTTPS" = "1" ]; then
      ufw delete allow 3000/tcp >/dev/null 2>&1 || true
      ufw delete allow 8000/tcp >/dev/null 2>&1 || true
      ok "已删除 3000/8000 放行"
    fi
    ufw --force enable >/dev/null 2>&1 || true
    ok "ufw 已启用：22/80/443"
    ufw status numbered | sed 's/^/     /'
  elif command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=ssh  >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-service=http >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-service=https >/dev/null 2>&1 || true
    if [ "$AFTER_HTTPS" = "1" ]; then
      firewall-cmd --permanent --remove-port=3000/tcp >/dev/null 2>&1 || true
      firewall-cmd --permanent --remove-port=8000/tcp >/dev/null 2>&1 || true
      ok "已移除 3000/8000 放行"
    fi
    firewall-cmd --reload >/dev/null 2>&1 || true
    ok "firewalld 已启用：ssh/http/https"
  else
    die "既没有 ufw 也没有 firewalld，请先装一个（apt install ufw）"
  fi
  warn "别忘了：阿里云**安全组**是第二道门，也要同步只放行 22/80/443"
fi

# ---------------- 5. 锁 root / 禁 root ----------------
if [ "$LOCK_ROOT" = "1" ] || [ "$DISABLE_ROOT" = "1" ]; then
  log "5. SSH root 登录限制"
  has_nonroot_sudo_key_user || die "拒绝执行：没有找到「非 root + sudo/wheel + 有 authorized_keys」的用户。
  现在禁用 root 会让你永久失去 SSH 登录能力。
  先执行： bash hardening/harden-server.sh --create-sudo-user deploy --ssh-key <你的公钥>
  并用新用户成功登录验证后，再回来执行本步。"

  SSHD_CFG="/etc/ssh/sshd_config"
  EXTRA="/etc/ssh/sshd_config.d/99-agent-hub-hardening.conf"
  mkdir -p /etc/ssh/sshd_config.d
  cp -a "$SSHD_CFG" "$SSHD_CFG.bak.$(date +%Y%m%d%H%M%S)"

  {
    echo "# 由 hardening/harden-server.sh 生成"
    if [ "$DISABLE_ROOT" = "1" ]; then
      echo "PermitRootLogin no"
    else
      echo "PermitRootLogin prohibit-password"
    fi
    if [ "$DISABLE_PASSWORD_AUTH" = "1" ]; then
      echo "PasswordAuthentication no"
      echo "KbdInteractiveAuthentication no"
    fi
  } > "$EXTRA"

  if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || systemctl restart sshd
    ok "sshd 配置已生效：$(grep -h '^PermitRootLogin' "$EXTRA")"
    [ "$DISABLE_ROOT" = "1" ] && warn "root 已彻底禁止登录。请**保持当前会话不断开**，另开终端用普通用户验证能登录后再退出。"
  else
    rm -f "$EXTRA"
    die "sshd -t 校验失败，已回滚本次改动（未 reload）。请检查 $SSHD_CFG"
  fi
fi

if [ -z "$SSH_KEY" ] && [ -z "$NEW_USER" ] && [ "$DO_FAIL2BAN$DO_FIREWALL$LOCK_ROOT$DISABLE_ROOT" = "0000" ]; then
  warn "没有指定任何动作。看用法： bash $0 --help"
fi

echo
printf '%s完成。建议顺序：装公钥 → 建 sudo 用户 → 另开终端验证 → fail2ban → 防火墙 → 禁 root%s\n' "$GREEN" "$NC"
