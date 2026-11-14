# security/ — 线上安全审计留档

这里放**已经发生过的**安全排查与处置记录，以及可复现的脚本。
不是规范文档，也不是待办清单（待办在 `docs/` 与 `.workbuddy/memory/`）。

## 目录

| 文件 | 内容 |
|---|---|
| `2026-11-14_账号异常与反代审计.md` | 三项任务：异常账号调用 / 反代排查 / 同 IP 归并与禁用。含判据、证据、处置、回滚 |
| `scripts/audit_accounts.py` | 只读审计：调用量、每分钟峰值、用量 vs 上限、孤儿身份、并发登录 |
| `scripts/disable_accounts.py` | 禁用/封堵工具，**默认预演**，`--apply` 才写库 |

## 两条硬规则

1. **审计只读**。所有统计都在库的副本上跑（`sqlite3` 的 backup API 在线取一份），
   不改线上库、不改线上配置。唯一允许的写操作是"禁用"，且必须走
   `scripts/disable_accounts.py`（它带预演、幂等、审计留痕、管理员豁免）。

2. **不在公开内容里带真实身份**。本仓库是 public，往报告里写证据时：
   IP 只留前两段（`112.96.*.*`），用户名不出现，uid 只留前 8 位。
   完整明细写在 `.workbuddy/security/`（已 gitignore），不进仓库。

## "禁用"在这个项目里有三种实现，别用错

| 对象 | 做法 | 为什么 |
|---|---|---|
| 账号还在 | `status=0` **且** `token_valid_after += 1` | `status` 只在登录最后一步看，挡不住已发出的 7 天 cookie；代数 +1 才能让旧令牌立刻 401 |
| 账号已删（幽灵身份） | 往 `deleted_users` 写墓碑 | 没有 `users` 行可改；`current_user_id` 只在查不到人时查墓碑 |
| 单枚令牌 | `revoked_tokens` 写 jti | 用于退出登录 / 精确踢某台设备 |

只改 `status` 是最常见的错：用户重新打开页面照样能进，因为 cookie 还在。

## 用法

```bash
# 只读审计
python3 security/scripts/audit_accounts.py --db data/sessions.db

# 预演（默认不写库）
python3 security/scripts/disable_accounts.py --db data/sessions.db --orphans

# 执行
python3 security/scripts/disable_accounts.py --db data/sessions.db --orphans --apply

# 回滚（这些账号本身已不存在，通常不需要）
sqlite3 data/sessions.db "delete from deleted_users where reason='security_audit_20261114';"
```

## 2026-11-14 那次的结论（详见报告）

- 没有任何账号把单日额度用光（ask 最高 27/60），所以**没有禁用任何真实用户**；
- 真正的异常是 75 个"删号后令牌仍有效"的幽灵身份 → 全部写墓碑封堵，并做了正/负双向验证；
- 没有第三方反代；但 XFF 第一跳可伪造、本机直连 8000 绕开 nginx 审计，两条待修。
