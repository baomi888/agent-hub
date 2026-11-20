# HZL 账号安全：邮箱配置、接口与后续迁移说明

日期：2026-11-20。适用于本次 HZL 工作区的仅邮箱版本。
本文是交付说明；本次没有启用正式服务、迁移正式数据库、提交、推送或部署。

## 1. 已实现的使用方式

- 注册：用户名、密码、必填邮箱 → 发送验证码 → 输入验证码并验证 → 注册并登录。
- 登录：仍使用用户名和密码；密码右侧小眼睛可显示或隐藏内容。
- 历史账号：仍可登录；设置中验证邮箱后首次绑定，不会自动导入提醒邮箱。
- 换绑：验证原邮箱和新邮箱，然后一次确认完成；失败保留原邮箱。
- 找回：输入绑定邮箱 → 验证邮件验证码 → 输入两次相同的新密码 → 返回登录页面重新登录。
- 重置后旧密码、旧 Cookie/Bearer 会话及过时找回凭证不可继续使用。
- 一个安全邮箱只能属于一个账号；已绑定账号没有单独清空邮箱的入口。
- 不提供手机号、短信、邮箱验证码免密登录或无法访问旧邮箱时的绕过换绑。

## 2. 邮件配置

后端复用现有 SMTP 通道。以下是配置项说明，不包含真实授权凭证。
真实值应由获授权的部署环境安全注入，不写源码、测试快照或报告。

| 配置项 | 取值或说明 |
| --- | --- |
| SECURITY_EMAIL_ENABLED | 默认 0；正式开通前保持关闭；完成环境验证后才改为 1 |
| MAIL_ENABLED | 发信通道需为 1；同时影响原有提醒邮件通道 |
| SMTP_HOST | QQ 邮箱使用 smtp.qq.com |
| SMTP_PORT | 465 |
| SMTP_SECURITY | ssl；也支持正确配置的 starttls，公网不允许明文模式 |
| SMTP_USER | 发件账号完整邮箱 |
| SMTP_PASSWORD | 邮箱服务授权码；不是登录密码；本说明不记录其内容 |
| SMTP_FROM | 发件账号邮箱；未显式配置时底层默认使用 SMTP_USER |
| SMTP_FROM_NAME | 发件显示名称，可设为知穗 Agent |
| SMTP_TIMEOUT | 默认 12 秒，必须大于 0 |
| ALLOW_SIGNUP | 仍控制是否开放注册，关闭后不能通过发码接口绕过 |
| AUTH_SECRET | 保持部署原有稳定密钥；不要为本次功能擅自更换 |
| AGENT_HUB_DB | 明确的目标 SQLite 路径；测试必须指向新建临时库 |

邮件能力需要开关、发件配置和安全传输全部就绪。
数据库六张专用表也必须已经显式准备完成，否则接口返回 503。
请求和服务导入不会自动补绑现有用户，也不会为新功能偷偷执行迁移。
SMTP 接受邮件仅代表接受投递，不等于用户收件箱已收到。
找回发码使用统一提示，不向访问者确认邮箱是否有账号。

第 2 步及第 7 步的真实 QQ 邮件已由用户确认收到；本步未再次向外发送邮件。
真实测试工具不读取项目 .env，凭证只通过隐藏输入进入短期测试进程。

## 3. 数据结构和迁移边界

新增六张表：

| 表 | 作用 |
| --- | --- |
| security_emails | 每个账号的唯一已验证安全邮箱及绑定版本 |
| verification_requests | 用途、流程、验证码摘要、期限、错误次数和状态 |
| verification_slots | 当前发码流程与有效验证码选择 |
| verification_grants | 短期一次性证明的摘要及消费记录 |
| verification_rate_limits | 持久化发送预算 |
| verification_cooldowns | 持久化发送冷却 |

迁移入口为 core.verification.VerificationStore.migrate()，构造对象本身不读写数据库。
迁移幂等，只增加本功能专用表和索引；不会把旧提醒邮箱当成安全邮箱。
账号创建/邮箱绑定和证明消费共用事务；重置密码和鉴权代数更新共用事务。

**本次不执行真实数据库迁移。** 后续执行必须另有明确授权：

1. 在独立环境用合成旧库运行 tests/test_verification.py 的迁移与并发测试。
2. 明确目标路径、维护窗口、备份及恢复负责人；保留现有用户表和登录密钥。
3. 获得真实数据操作授权后，由维护者完成一致性备份，先在获准的测试副本演练。
4. 功能保持关闭；在服务停止写入的窗口，针对已核对的目标库显式运行迁移。
5. 核对六张新表、原有账号记录和原有提醒配置；禁止自动补绑。
6. 配置 SMTP；以受控测试账号验证注册、换绑、找回、旧会话失效和历史账号登录。
7. 确认后启用功能，并留意限流与发信错误；不在公开日志中记录验证码、密码或证明。

未来获准环境中的调用示例（仅说明 API，不在本次执行）：

```python
import hashlib
import os
from pathlib import Path

# 所有环境值由维护者提前安全提供；不得用示例密钥替换已有 AUTH_SECRET。
path = Path(os.environ["AGENT_HUB_DB"]).resolve(strict=True)
if not path.is_file():
    raise RuntimeError("目标必须是已核对的现有数据库文件")
secret = os.environ["AUTH_SECRET"]
if len(secret) < 32:
    raise RuntimeError("必须使用部署已有的稳定密钥")
# 导入前还需明确 DATA_DIR/PERSIST_DIR/DOWNLOAD_DIR，避免默认目录混用。
from core.verification import VerificationStore

key = hashlib.sha256(b"security-email-verification:" + secret.encode("utf-8")).digest()
VerificationStore(path, key).migrate()
```

应在进程启动前设置 PYTHON_DOTENV_DISABLED=1，并注入完整、正确的环境配置，
上述示例不读取原 .env，不会自动判断部署使用了哪个密钥文件。
原部署若只使用 .auth_secret 文件，应先由获授权维护者规划密钥保持方式，不能运行时重新生成。

## 4. 回退方式

优先关闭 SECURITY_EMAIL_ENABLED，关闭新增发码/绑定/找回能力，保留账号数据库。
现有用户名密码登录继续可用；新注册因要求验证邮箱会暂停。
若需回退程序版本，必须先验证旧版本对新增表和已修改密码的兼容性。
不要直接覆盖旧数据库备份：那会丢失发布后的新账号、邮箱绑定及密码变更。
已经重置密码的账号应继续使用新密码，不能把旧密码恢复成可用状态。
关闭开关不会删除数据，也不会恢复已经失效的登录会话。
备份恢复和删除新表均不在本次操作范围内。

## 5. 实际接口

所有路径前缀均为 /api/auth，保留现有尾斜线兼容处理。
请求采用严格字段模型，多余字段拒绝。

| 方法/路径 | 请求字段 | 权限与结果 |
| --- | --- | --- |
| GET /config/ | 无 | 注册开关、require_email=true、email_ready |
| POST /register/email/send | address | 注册需开放；设置浏览器 HttpOnly 验证 Cookie |
| POST /register/email/verify | request_id, code | 同一浏览器验证，返回 grant |
| POST /register/ | username, password, email, email_grant | 有效邮箱证明；原子注册绑定，成功自动登录 |
| GET /security-email | 无 | 已登录；脱敏绑定状态 |
| POST /email/code/send | purpose, address（按用途）, flow（换绑流程） | 已登录；只接受绑定/换绑用途 |
| POST /email/code/verify | request_id, code | 已登录；验证当前用户流程 |
| POST /security-email/bind | grant | 已登录历史账号首次绑定 |
| POST /security-email/change | old_grant, new_grant | 同一账号、同一流程，两份证明原子消费 |
| POST /password/reset/email/send | address | 设置找回 Cookie；统一业务提示 |
| POST /password/reset/email/verify | request_id, code | 同一浏览器，返回短期 grant |
| POST /password/reset | grant, new_password, confirm_password | 两次一致且符合现有密码规则；清除会话，不自动登录 |

purpose 为 bind_email / change_email_old / change_email_new；
注册和找回各自使用独立公开端点，不接受用户指定目标 user_id。
旧邮箱地址从账号绑定记录取得，不由前端任意指定。

## 6. 验证规则

- 六位 ASCII 数字验证码，10 分钟有效；验证成功换取 5 分钟一次性证明。
- 60 秒发送冷却；最多五次错误尝试；成功重发后旧码失效。
- 邮箱每天最多 10 次，上海日期；IP 和账号每 10 分钟 20 次；全局每日 1000 次。
- 以上来自 core.verification.Limits；当前没有对应的任意 .env 限额覆盖项。
- 数据库保存 HMAC 摘要，不保存明文验证码或证明。
- 注册/找回 Cookie 为 HttpOnly、SameSite=Lax；HTTPS 请求带 Secure。
- 邮箱去首尾空格、域名转小写，保留本地部分大小写；不合并点号或加号别名。
- 前端改变邮箱后清除先前验证状态；后端独立核对地址、流程、版本和期限。
- 短期证明不提供登录权限。验证码验证成功后仍需点击对应操作的确认按钮。

## 7. 本地重验

从 D:\zhisui_agent 在 HZL 运行；不启动使用真实数据的 main.py。
先确认 Python/Node 环境已准备完成，父子进程统一 UTF-8：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
.venv\Scripts\python.exe tools/run_isolated_tests.py tests -q
.venv\Scripts\python.exe tools/run_isolated_tests.py --script tests/test_auth_gate.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pip check
```

完整脚本清单和实际结果见 HZL账号安全_第8步测试清单.md。
隔离入口禁止原 data/data-staging/chroma_db/downloads/.env、越界 SQLite、外网和 8000 业务端口。
脚本必须通过 --script 执行；不要通过裸 python 脚本绕开这些边界。

前端先在 frontend 运行已安装的 TypeScript、ESLint 和 next build；
构建后在项目根运行 tools/test_account_journey_ui.cjs 等浏览器工具。
当前机器 Node/npm 的路径约定见开发计划和第 1 步报告。
真实邮件工具仅在另获发信授权时人工运行，不能放入无人确认的常规回归。
