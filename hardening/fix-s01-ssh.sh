#!/usr/bin/env bash
# ============================================================
#  S-01 一键修复：SSH 暴露面 + root 直登 + 爆破防护
#
#  背景：2026-10-29 安全评估发现 22 端口对公网开放、root 可登录、
#        fail2ban 未启用。本脚本把这条"一失全失"的路径一次关掉。
#
#  用法（在服务器上以 root 执行）：
#      # 第一次：装公钥 + 建 deploy + fail2ban + 禁止 root 密码登录
#      bash hardening/fix-s01-ssh.sh --pubkey "ssh-ed25519 AAAA... you@host"
#
#      # 只看要做什么，不改任何文件
#      bash hardening/fix-s01-ssh.sh --pubkey "..." --dry-run
#
#      # 另开终端验证 `ssh deploy@IP` 能登录、能 sudo 之后，再执行：
#      bash hardening/fix-s01-ssh.sh --i-verified --disable-password --disable-root
#
#      # 查看当前生效值
#      bash hardening/fix-s01-ssh.sh --status
#
#      # 回滚（恢复发行版默认的密码登录）
#      bash hardening/fix-s01-ssh.sh --rollback
#
#  ⚠️ 三条硬守卫：
#    1) --disable-password / --disable-root 必须显式带 --i-verified；
#       没有它，脚本只做到"root 不能用密码、但密码通道还在"，不会把你锁死。
#    2) 改 sshd 前先备份，改完 `sshd -t` 校验；不过自动移除本次 drop-in。
#    3) drop-in 用 00- 前缀：Ubuntu 云镜像的 50-cloud-init.conf 常写
#       `PasswordAuthentication yes`，sshd 是"先读到的值生效"，99- 会被它压住。
# ============================================================
set -uo pipefail

CYAN=$'\033[1;36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[1;31m'; NC=$'\033[0m'
log()  { printf '\n%s===== %s =====%s\n' "$CYAN" "$*" "$NC"; }
ok()   { printf '  %s[OK]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$YELLOW" "$NC" "$*"; }
die()  { printf '\n%s[中止]%s %s\n\n' "$RED" "$NC" "$*"; exit 1; }

DROPIN="/etc/ssh/sshd_config.d/00-agent-hub-hardening.conf"
SSHD_CFG="/etc/ssh/sshd_config"

PUBKEY=""
PUBKEY_FILE=""
USER_NAME="deploy"
DRY_RUN=0
DO_FAIL2BAN=1
DISABLE_PASSWORD=0
DISABLE_ROOT=0
I_VERIFIED=0
DO_STATUS=0
DO_ROLLBACK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --pubkey)            PUBKEY="${2:-}"; shift 2 ;;
    --pubkey-file)       PUBKEY_FILE="${2:-}"; shift 2 ;;
    --user)              USER_NAME="${2:-}"; shift 2 ;;
    --dry-run)           DRY_RUN=1; shift ;;
    --no-fail2ban)       DO_FAIL2BAN=0; shift ;;
    --disable-password)  DISABLE_PASSWORD=1; shift ;;
    --disable-root)      DISABLE_ROOT=1; shift ;;
    --i-verified)        I_VERIFIED=1; shift ;;
    --status)            DO_STATUS=1; shift ;;
    --rollback)          DO_ROLLBACK=1; shift ;;
    -h|--help)           sed -n '2,31p' "$0"; exit 0 ;;
    *) die "未知参数：$1（--help 看用法）" ;;
  esac
done

[ "$(id -u)" = "0" ] || die "请用 root 运行： sudo bash $0 ..."
command -v sshd >/dev/null 2>&1 || die "找不到 sshd，本机可能不是 OpenSSH 服务端"

# ---------------- 状态 ----------------
if [ "$DO_STATUS" = "1" ]; then
  log "当前生效的 SSH 配置（sshd -T 解析后的值）"
  sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|maxauthtries|logingracetime)' | sed 's/^/  /'
  echo
  if id "$USER_NAME" >/dev/null 2>&1; then
    home="$(getent passwd "$USER_NAME" | cut -d: -f6)"
    ok "用户 $USER_NAME 存在（$home）"
    [ -s "$home/.ssh/authorized_keys" ] && ok "已装 authorized_keys（$(wc -l < "$home/.ssh/authorized_keys") 条）" || warn "没有 authorized_keys"
    if sudo -l -U "$USER_NAME" 2>/dev/null | grep -q 'NOPASSWD'; then ok "sudo: NOPASSWD 已配置"; else warn "sudo: 未看到 NOPASSWD"; fi
  else
    warn "用户 $USER_NAME 不存在"
  fi
  echo
  if command -v fail2ban-client >/dev/null 2>&1; then
    fail2ban-client status sshd 2>/dev/null | sed 's/^/  /' || warn "fail2ban 已装但 sshd jail 未运行"
  else
    warn "fail2ban 未安装"
  fi
  [ -f "$DROPIN" ] && ok "加固 drop-in 存在：$DROPIN" || warn "加固 drop-in 不存在"
  exit 0
fi

# ---------------- 回滚 ----------------
if [ "$DO_ROLLBACK" = "1" ]; then
  log "回滚 SSH 加固"
  if [ -f "$DROPIN" ]; then
    rm -f "$DROPIN"
    ok "已删除 $DROPIN"
  else
    warn "drop-in 不存在，无需删除"
  fi
  if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || systemctl restart sshd
    ok "sshd 已重载；PasswordAuthentication 回到发行版默认"
  else
    die "sshd -t 校验失败，请检查 $SSHD_CFG"
  fi
  warn "deploy 用户与 NOPASSWD sudo 仍在（如需移除：userdel -r $USER_NAME && rm -f /etc/sudoers.d/90-agent-hub-$USER_NAME）"
  exit 0
fi

# ---------------- 参数校验 ----------------
if [ -z "$PUBKEY" ] && [ -z "$PUBKEY_FILE" ]; then
  if [ "$DISABLE_PASSWORD" = "1" ] || [ "$DISABLE_ROOT" = "1" ]; then
    : # 二次加固可以不再带公钥
  else
    die "缺少公钥。传 --pubkey \"ssh-ed25519 AAAA...\" 或 --pubkey-file /path/id_ed25519.pub"
  fi
fi
if [ -n "$PUBKEY_FILE" ]; then
  [ -f "$PUBKEY_FILE" ] || die "公钥文件不存在：$PUBKEY_FILE"
  PUBKEY="$(cat "$PUBKEY_FILE")"
fi
if [ -n "$PUBKEY" ]; then
  # 只做形状校验：算法 + base64 + 可选 comment
  printf '%s' "$PUBKEY" | grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh.com) [A-Za-z0-9+/=]+( .*)?$' \
    || die "公钥格式不对。应为 \"ssh-ed25519 AAAA... comment\" 一行"
fi
if [ "$DISABLE_PASSWORD" = "1" ] || [ "$DISABLE_ROOT" = "1" ]; then
  [ "$I_VERIFIED" = "1" ] || die "拒绝执行：--disable-password / --disable-root 必须先另开终端验证
    ssh $USER_NAME@<公网IP>  能登录，且 sudo -v 能通过，
  然后带上 --i-verified 再执行。当前会话不受影响，但新会话可能登不进来。"
fi

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN：只打印计划，不改任何文件"
  printf '  用户：%s\n' "$USER_NAME"
  printf '  公钥：%s\n' "${PUBKEY:0:60}..."
  printf '  fail2ban：%s\n' "$([ "$DO_FAIL2BAN" = 1 ] && echo 安装/启用 || echo 跳过)"
  printf '  PermitRootLogin：%s\n' "$([ "$DISABLE_ROOT" = 1 ] && echo no || echo prohibit-password)"
  printf '  PasswordAuthentication：%s\n' "$([ "$DISABLE_PASSWORD" = 1 ] && echo no || echo 保持默认)"
  printf '  drop-in：%s\n' "$DROPIN"
  printf '  sshd 备份：%s.bak.<时间戳>\n' "$SSHD_CFG"
  exit 0
fi

# ---------------- 1. 建用户 + sudo ----------------
log "1. 账号：$USER_NAME"
if id "$USER_NAME" >/dev/null 2>&1; then
  ok "用户已存在，复用"
else
  useradd -m -s /bin/bash "$USER_NAME" || die "useradd 失败"
  ok "用户已创建"
fi
if getent group sudo >/dev/null 2>&1; then usermod -aG sudo "$USER_NAME"; else usermod -aG wheel "$USER_NAME"; fi
ok "已加入 sudo/wheel 组"

# 锁掉密码（只允许密钥登录）。sudo 用 NOPASSWD，避免"没密码就不能 sudo"。
passwd -l "$USER_NAME" >/dev/null 2>&1 || true
SUDOERS="/etc/sudoers.d/90-agent-hub-$USER_NAME"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$USER_NAME" > "$SUDOERS"
chmod 440 "$SUDOERS"
if command -v visudo >/dev/null 2>&1 && ! visudo -cf "$SUDOERS" >/dev/null 2>&1; then
  rm -f "$SUDOERS"
  die "sudoers 校验失败，已删除 $SUDOERS"
fi
ok "已配置 NOPASSWD sudo（$SUDOERS）"

# ---------------- 2. 装公钥（deploy + root 双通道）----------------
if [ -n "$PUBKEY" ]; then
  log "2. 安装公钥"
  for u in "$USER_NAME" root; do
    home="$(getent passwd "$u" | cut -d: -f6)"
    [ -n "$home" ] || { warn "跳过不存在的用户 $u"; continue; }
    install -d -m 700 -o "$u" -g "$u" "$home/.ssh"
    chown "$u:$u" "$home/.ssh" 2>/dev/null || true
    chmod 700 "$home/.ssh"
    touch "$home/.ssh/authorized_keys"
    if grep -qxF "$PUBKEY" "$home/.ssh/authorized_keys" 2>/dev/null; then
      ok "$u：公钥已存在"
    else
      printf '%s\n' "$PUBKEY" >> "$home/.ssh/authorized_keys"
      ok "$u：公钥已写入"
    fi
    chmod 600 "$home/.ssh/authorized_keys"
    chown "$u:$u" "$home/.ssh/authorized_keys"
  done
fi

# ---------------- 3. fail2ban ----------------
if [ "$DO_FAIL2BAN" = "1" ]; then
  log "3. fail2ban"
  if ! command -v fail2ban-client >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -y >/dev/null 2>&1
      DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban >/dev/null 2>&1 || warn "fail2ban 安装失败（继续，sshd 加固不受影响）"
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y fail2ban >/dev/null 2>&1 || warn "fail2ban 安装失败（继续）"
    elif command -v yum >/dev/null 2>&1; then
      yum install -y fail2ban >/dev/null 2>&1 || warn "fail2ban 安装失败（继续）"
    else
      warn "没有包管理器，跳过 fail2ban"
    fi
  fi
  if command -v fail2ban-client >/dev/null 2>&1; then
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
    ok "fail2ban 已启用（10 分钟 5 次失败 → 封 1 小时）"
  fi
fi

# ---------------- 4. sshd 加固 ----------------
# 关密码前最后一道自检：目标用户必须真的有可用公钥
if [ "$DISABLE_PASSWORD" = "1" ] || [ "$DISABLE_ROOT" = "1" ]; then
  home="$(getent passwd "$USER_NAME" | cut -d: -f6)"
  if [ -z "$home" ] || [ ! -s "$home/.ssh/authorized_keys" ]; then
    die "拒绝执行：用户 $USER_NAME 没有可用的 authorized_keys。
  关闭密码后你将无法登录。先执行第一条（带 --pubkey），验证 ssh $USER_NAME@IP 能进，再来。"
  fi
  [ -f "/etc/sudoers.d/90-agent-hub-$USER_NAME" ] || warn "未看到 NOPASSWD sudo 配置，请确认 $USER_NAME 能执行 sudo"
fi

log "4. sshd 配置"
mkdir -p /etc/ssh/sshd_config.d
cp -a "$SSHD_CFG" "$SSHD_CFG.bak.$(date +%Y%m%d%H%M%S)" || die "备份 sshd_config 失败，未做任何改动"
ok "已备份 $SSHD_CFG"

ROOT_LOGIN="prohibit-password"
[ "$DISABLE_ROOT" = "1" ] && ROOT_LOGIN="no"
PASSWORD_AUTH_LINE="# PasswordAuthentication 保持发行版默认（首次加固不锁死）"
if [ "$DISABLE_PASSWORD" = "1" ]; then
  PASSWORD_AUTH_LINE="PasswordAuthentication no"
fi

cat > "$DROPIN" <<EOF
# 由 hardening/fix-s01-ssh.sh 生成（2026-10-29 S-01）
# 用 00- 前缀：确保先于 50-cloud-init.conf 被读取（sshd 是首个值生效）
PermitRootLogin $ROOT_LOGIN
PubkeyAuthentication yes
KbdInteractiveAuthentication no
$PASSWORD_AUTH_LINE
MaxAuthTries 3
LoginGraceTime 20
ClientAliveInterval 300
ClientAliveCountMax 2
X11Forwarding no
AllowAgentForwarding no
EOF

if ! sshd -t 2>/dev/null; then
  rm -f "$DROPIN"
  die "sshd -t 校验失败，已删除本次 drop-in（配置未生效）。请检查 $SSHD_CFG"
fi
ok "sshd -t 校验通过"

# 用 sshd -T 看**生效值**：确认 50-cloud-init 之类的文件没有压住我们
EFF_ROOT="$(sshd -T 2>/dev/null | awk '/^permitrootlogin /{print $2}')"
if [ "$EFF_ROOT" != "$ROOT_LOGIN" ]; then
  rm -f "$DROPIN"
  die "生效值与预期不符：permitrootlogin=$EFF_ROOT（期望 $ROOT_LOGIN）。
  多半是 /etc/ssh/sshd_config.d/ 里有更早的文件覆盖了它。已回滚本次 drop-in。"
fi
ok "生效值：PermitRootLogin=$EFF_ROOT"

if [ "$DISABLE_PASSWORD" = "1" ]; then
  EFF_PWD="$(sshd -T 2>/dev/null | awk '/^passwordauthentication /{print $2}')"
  [ "$EFF_PWD" = "no" ] || warn "PasswordAuthentication 生效值仍为 $EFF_PWD（检查 50-cloud-init.conf）"
fi

systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || systemctl restart sshd
ok "sshd 已重载"

# ---------------- 5. 收尾提示 ----------------
log "完成"
cat <<EOF

  当前会话：$([ -n "${SSH_CONNECTION:-}" ] && echo "$SSH_CONNECTION" || echo "本地/控制台")
  ${YELLOW}不要关闭当前会话${NC}。请另开一个终端验证：
      ssh $USER_NAME@<公网IP>        # 应能免密登录
      sudo -v                        # 应直接通过（NOPASSWD）

  验证通过后，再执行第二条（彻底关闭密码通道 / 禁止 root）：
      bash hardening/fix-s01-ssh.sh --i-verified --disable-password --disable-root

  看状态： bash hardening/fix-s01-ssh.sh --status
  要回滚： bash hardening/fix-s01-ssh.sh --rollback

  别忘了阿里云安全组：22 入方向限定为自己的出口 IP（这是第一道门）。
EOF
