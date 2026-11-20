# 邮箱验证与找回密码：远程分支适配说明

## 本次范围

在 `feat/email-verify-code` 原有提交 `50a4aeaf45f9fa60b3682882d67c19fa8fc09a1e` 后新增功能提交，保留远程历史。功能来源为本地 HZL 的 `b60f4f8`，只迁入邮箱功能及必要测试；不合入整个 HZL 历史，不合并 main。

- 新注册用户必须验证邮箱，一个邮箱只能绑定一个账号；原有账号仍使用登录名、密码登录。
- 设置页允许历史账号首次绑定，以及通过原邮箱、新邮箱验证码换绑；不能清除唯一的安全邮箱。
- 找回密码通过已绑定邮箱验证码验证，输入两遍一致的新密码；重置后旧 Cookie/Bearer 会话失效，回登录页重新登录。
- 登录、注册、重置密码表单提供密码显示开关。
- 不包含手机号、短信、提醒邮件业务、生产部署或既有用户数据迁移。

## 适配旧分支的必要差异

保留旧版 `useAuth` 与授权事件广播，未引入 HZL 的全局登录状态模块；保留 `/` 原有登录门，并增加 `/login` 承接重置密码返回流程。复用原有 API 请求封装。

旧分支没有 SMTP 传输层，补充标准库传输模块与环境配置。旧分支没有账号软删除墓碑表，账号有效性检查使用其已有 `users` 表的存在性、状态及鉴权版本；删除、禁用、未绑定账号均不能找回密码。

旧分支没有提醒邮件接口，对该地址的请求仍返回 404，不新增该业务；安全邮箱独立存储并拒绝删除。

原有测试的合成账号准备改为完成邮箱验证再注册，原有业务断言保留。未携带 HZL 中无关的历史任务、提醒、ASR 或检索词测试修复。

## 运行配置与显式准备

默认 `SECURITY_EMAIL_ENABLED=0`。本次仅提交代码，不启用生产开关，不读取真实 `.env`，不访问、修改已有用户数据。

部署人员需另行完成环境配置与数据库备份，授权显式建表后才能启用。示例文件只包含空凭证：

```dotenv
SECURITY_EMAIL_ENABLED=0
MAIL_ENABLED=1
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_FROM_NAME=知穗 Agent
SMTP_TIMEOUT=12
```

`SMTP_PASSWORD` 为邮箱服务授权码，由部署环境注入，不能写入 Git。SMTP 是否接受邮件不等于用户实际收到，真实收件需另行验收。

验证码表不会通过 HTTP 请求或服务启动偷偷创建。经部署人员另行授权后，可在正确的部署环境中显式调用下面的建表过程（本次未在任何真实用户库执行）：

```python
import hashlib
from core import account, session
from core.auth import _secret
from core.verification import VerificationStore

key = hashlib.sha256(b"security-email-verification:" + _secret()).digest()
VerificationStore(session._db_path(), key).migrate()
```

该过程创建 `security_emails`、`verification_requests`、`verification_slots`、`verification_grants`、`verification_rate_limits`、`verification_cooldowns` 六张表，不自动补绑历史账号。准备完成后由部署人员设置 `SECURITY_EMAIL_ENABLED=1` 并重启服务。关闭该开关可停止安全邮箱功能，原有账号登录继续可用。

验证码有效期 10 分钟，验证证明有效期 5 分钟；60 秒重发间隔、5 次错误次数限制、邮箱/IP/用户/全局发送限制由后端执行。验证码和验证证明只以加密摘要持久化，注册/找回证明与浏览器 HttpOnly Cookie 绑定，原、新邮箱换绑证明必须属于同一流程。

## 隔离测试方式

先安装项目依赖。所有 Python 测试通过以下入口，在导入业务模块前生成临时数据路径，禁用 dotenv，阻止外网、原数据目录、越界 SQLite 与已有 8000 端口服务访问：

```powershell
$env:PYTHONUTF8="1"
python tools/run_isolated_tests.py tests/test_security_mail.py tests/test_verification.py tests/test_security_email_settings.py tests/test_email_registration.py tests/test_password_reset.py tests/test_account_journey.py tests/test_baseline_environment.py tests/test_isolation_tools.py -q
python tools/run_isolated_tests.py tests -q --tb=short
python tools/run_isolated_tests.py --script tests/test_auth_gate.py
python tools/run_isolated_tests.py --script tests/test_owner_isolation.py
python tools/run_isolated_tests.py --script tests/test_kb_isolation.py
python tools/run_isolated_tests.py --script tests/test_slash_no_redirect.py
python tools/run_isolated_tests.py --script tests/test_geo_locate.py
```

前端在 `frontend` 目录运行 `npx tsc --noEmit --incremental false`、`npx next build --webpack`。构建后从仓库根运行：

```powershell
node tools/test_security_email_ui.cjs
node tools/test_email_registration_ui.cjs
node tools/test_password_reset_ui.cjs
node tools/test_account_journey_ui.cjs
```

浏览器脚本需要 Playwright 与 Edge。可设置 `PLAYWRIGHT_MODULE` 指定 Playwright 包路径，`ZHISUI_BROWSER_CHANNEL` 指定浏览器；联调脚本支持 `ZHISUI_TEST_PYTHON` 指定 Python 路径，默认 Windows `.venv/Scripts/python.exe`。

前三组浏览器测试模拟 API，覆盖错误反馈、键盘、密码可见性和页面流程；联调测试使用真实回环 API、临时 SQLite 和本机 SMTP，并通过浏览器路由转发请求，不连接生产端口或真实邮箱。桌面 1440px 浅色与 390px 深色/PWA 状态均验收；不将此声称为真实手机或真实邮件投递验收。

## 2026-11-20 适配验收记录

- 适配前 pytest：914 passed、8 failed、1 skipped、1 deselected、1 xfailed，109 subtests passed。
- 邮箱专项：122 passed。
- 适配后 pytest：1036 passed、8 failed、1 skipped、1 deselected、1 xfailed，109 subtests passed；失败集合与基线完全一致，无新增失败。
- 8 项原有失败涉及 `test_query_api.py::test_shipped_vocab_still_loads` 与 `test_query_enhance.py` 的 7 项资料读取测试，因隔离规则拒绝访问既有 `data` 内容而失败。未使用用户数据、伪造缺失资料或降低断言来消除它们。pytest 原有跳过/排除/预期失败配置保持不变，不宣称全量全绿。
- 账号认证脚本 36/36、所有者隔离 21/21、知识库隔离 29/29、斜杠路由与定位脚本全部通过。
- TypeScript 与前端生产构建通过；Ruff 全仓通过。
- 前端 lint 修改前后均为 22 errors、29 warnings，诊断文件、规则、级别、信息逐项一致；这是目标旧分支的历史基线，并非 HZL 的 36 项历史错误，未擅自修复。
- 设置浏览器测试 2 场景、注册 3 场景、找回密码 2 场景、真实隔离 API+SMTP 完整旅程 2 场景，全部通过。
- 本次没有真实 SMTP 发信；本次代码验证只使用本机 SMTP。此前 HZL 的真实邮件验收属于历史证据，不冒充此次重新投递结果。
