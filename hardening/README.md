# 服务器安全加固（分步执行）

> ⚠️ 这一组动作**风险不对称**：做对了收益很大，做错了会把自己锁在服务器外面。
> 所以脚本设计成「一次只做一件事 + 带锁死守卫」，请**严格按顺序**执行，每步都另开终端验证。

## 一条命令修复 S-01（2026-10-29 新增，推荐）

`fix-s01-ssh.sh` 把「装公钥 → 建 deploy → NOPASSWD sudo → fail2ban → 禁 root 密码登录」
串成两条命令，且带 `--dry-run` / `--status` / `--rollback`。

```bash
# 第一次（不会关密码通道，不会锁死）
bash hardening/fix-s01-ssh.sh --pubkey "ssh-ed25519 AAAA... you@host"

# 另开终端验证：ssh deploy@<公网IP> 能登录、sudo -v 能过
# 验证通过后再执行（彻底关密码 / 禁 root）
bash hardening/fix-s01-ssh.sh --i-verified --disable-password --disable-root
```

要点：

- `--disable-password` / `--disable-root` **必须**带 `--i-verified`，否则脚本拒绝执行；
- drop-in 用 `00-` 前缀，避免 Ubuntu 云镜像的 `50-cloud-init.conf` 把 `PasswordAuthentication yes` 压回来；
- 改完用 `sshd -T` 校验**生效值**，不一致自动回滚；
- `--status` 看状态，`--rollback` 恢复发行版默认。

## 推荐顺序（分步版，仍然可用）

| 步骤 | 命令 | 为什么这个顺序 |
| --- | --- | --- |
| 1 | `bash hardening/harden-server.sh --ssh-key ~/id_ed25519.pub` | 先确保密钥能登，后面才敢关密码 |
| 2 | `bash hardening/harden-server.sh --create-sudo-user deploy` | 禁 root 前必须先有可用的 sudo 用户 |
| 3 | **另开终端** `ssh deploy@<公网IP>` + `sudo -v` | 验证不通就**停在这里**，别继续 |
| 4 | `bash hardening/harden-server.sh --fail2ban` | 防爆破，不影响现有登录 |
| 5 | `bash hardening/harden-server.sh --firewall` | 只开 22/80/443；**此时不动 3000** |
| 6 | HTTPS 上线后：`bash hardening/harden-server.sh --firewall --after-https` | 关 3000/8000 的前提是 443 已通 |
| 7 | `bash hardening/harden-server.sh --lock-root` | 改 `prohibit-password`（root 仍可用密钥） |
| 8（可选） | `CONFIRM_DISABLE_ROOT=yes bash hardening/harden-server.sh --disable-root` | 彻底禁 root，风险最高，最后做 |

## 三条硬性守卫（脚本内置）

1. **`--after-https` 会先检查 443 是否在监听**，没监听直接拒绝关 3000/8000。
   —— 因为应用现在还跑在 `3000`，提前关掉等于**立刻断服**。
2. **`--lock-root` / `--disable-root` 前**，脚本会检查是否存在
   「非 root + sudo/wheel 组 + 已装 `authorized_keys`」的用户，没有就拒绝执行。
3. 改 `sshd_config` 前先备份，改完 `sshd -t` 自检，**不过自动回滚**。

## 为什么 `--lock-root` 默认是 `prohibit-password` 而不是 `no`

`prohibit-password` = 禁止 root 用密码登录、**仍允许 root 用密钥**。
这是性价比最高的档位：关掉了爆破面，又保留了一条应急通道。
`--disable-root`（彻底 `no`）才需要显式再加一个参数，且应放在最后做。

## 与阿里云安全组的关系

本机 `ufw/firewalld` 只是**第二道门**。**阿里云控制台的安全组**才是第一道，
两边要一致：

| 阶段 | 本机防火墙 | 安全组 |
| --- | --- | --- |
| HTTPS 上线前 | 22 / 80 / 3000 | 22 / 3000（80 可选） |
| HTTPS 上线后 | 22 / 80 / 443 | 22 / 80 / 443（**删掉 3000**） |

## 回滚

- fail2ban：`systemctl disable --now fail2ban`
- 防火墙：`ufw disable` / `firewall-cmd --set-default-zone=public`
- sshd：`rm /etc/ssh/sshd_config.d/99-agent-hub-hardening.conf && systemctl reload ssh`
  （或从 `/etc/ssh/sshd_config.bak.<时间戳>` 还原）
