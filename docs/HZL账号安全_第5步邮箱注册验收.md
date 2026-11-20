# HZL 账号安全：第 5 步注册必绑邮箱验收

日期：2026-11-19
状态：第 5 步完成；第 6 步暂停，等待用户明确指令。

## 功能与范围

注册必须提供用户名、密码、邮箱和邮箱验证证明。登录仍只需用户名和密码。
缺少证明、证明失效或邮件服务未启用，均不允许退回旧的无邮箱注册方式。
已有未绑定邮箱账号仍然可以登录；设置补绑功能保持可用。
保留用户名、弱密码、管理员名保留、注册关闭和注册防滥用限流规则。
没有开发找回密码、小眼睛或其他第 6 步功能。

## 接口设计

- POST /api/auth/register/email/send：公开的注册专用发码。
- POST /api/auth/register/email/verify：公开的注册专用验证码验证。
- POST /api/auth/register/：严格注册模型，新增必填 email、email_grant。
- GET /api/auth/config/：返回 allow_signup、require_email=true、email_ready。

采用注册专用路径，避免把第 4 步已登录邮箱设置接口改成公开接口；
原 /api/auth/email/code/send 仍仅供登录账号绑定和换绑，不开放找回用途。
这细化了原计划接口草案，不增加业务范围。

服务端生成随机注册 nonce，并通过 HttpOnly、SameSite=Lax、限定 /api/auth 的 Cookie 保存，
在 HTTPS 请求中设置 Secure。验证证明与当前浏览器、邮箱及注册用途关联。
成功注册清除临时 Cookie，并按原逻辑签发登录会话。证明不写 localStorage。
用户行、安全邮箱行和证明消费共用一个事务；唯一性冲突时全部回滚，
不会留下只有用户或只有邮箱的半成品。失败证明仍可在有效期内用于修正输入后提交。
默认服务仍要求显式启用并准备好专用表，不自动迁移现有数据库。

## 变更位置

- api/auth.py：独立注册模型、注册依赖、配置能力及原子注册调用。
- api/registration.py：注册专用公开邮箱验证和浏览器证明绑定。
- core/security_email.py：注册发送、验证码验证、用户和邮箱原子创建。
- main.py：挂载注册邮箱路由；只是 HZL 工作区里的入口文件，不是 main 分支。
- frontend/src/components/RegistrationEmailFields.tsx：注册邮箱表单、验证码、倒计时和反馈。
- frontend/src/components/AuthGate.tsx：注册前验证要求、切换模式清除证明。
- frontend/src/lib/api.ts、authStore.ts、hooks/useAuth.ts、types.ts：请求及类型贯通。
- 所有 AuthGate 调用方沿用同一组件和回调类型，无平行旧注册入口。
- tests/test_email_registration.py：13 项新增注册测试。
- tests/registration_support.py：旧回归测试准备新账号时，真实走测试邮箱验证接口。
- tests/test_account_profile.py、test_baseline_environment.py：更新账号准备方式，保留原业务断言。
- tests/test_security_email_settings.py：用显式合成历史账号准备未绑定状态，然后正常登录。
- tools/test_email_registration_ui.cjs：多入口注册交互测试。

测试辅助只允许独立临时库，没有生产 bypass 参数、无邮箱注册开关或删除旧测试。
本步没有修改其他未执行测试的预期；全项目测试及其历史夹具检查仍在第 8 步进行。

## 测试证据

开发前：测试因缺少 signup_service 失败，确认功能尚未实现。
实现后首批 10 项注册测试通过；随后增加错误验证码、证明重放、发送失败、
默认依赖到本机 FakeSMTP 的完整注册链路，共 13 项。
发现前端缺少 RegistrationEmail 类型导入，修复后类型检查及构建通过。
Git 差异检查发现旧测试文件末尾多余空行，已清除，没有改变行为或断言。

最终相关后端命令：

    cd D:\zhisui_agent
    .venv\Scripts\python.exe tools/run_isolated_tests.py tests/test_email_registration.py tests/test_security_email_settings.py tests/test_verification.py tests/test_baseline_environment.py tests/test_security_mail.py tests/test_account_profile.py tests/test_jwt_login_flow.py tests/test_auth_cookie_secure.py tests/test_mail.py -q

结果：190 passed，0 failed，0 skipped，29.84 秒。
第 5 步新增 13 项，加前 4 步 177 项相关回归。
两个原有警告保留：LangChain community 弃用、头像尺寸边界 Pillow 警告。
新增/变更 Python 文件 ruff check 通过，前端变更文件 ESLint 通过。
前端 TypeScript 与 Next.js 生产构建通过。
完整前端 lint：原有 36 errors、32 warnings，逐条对比差异为 0。
Git diff --check 在清除末尾空行后通过。

后端覆盖：
- 缺少邮箱或证明直接拒绝；服务关闭不恢复无邮箱注册。
- 成功创建账号、绑定邮箱、自动登录、退出后可重新登录。
- 错误验证码、过期证明、伪造证明、跨浏览器证明、修改邮箱和证明重放拒绝。
- 重名或邮箱唯一性冲突不留下半成品，证明回滚可重用。
- 两个浏览器并发注册同一邮箱，最多一个账号成功。
- 关闭注册后，发码、验证及提交注册均被拒绝。
- 旧用户名和密码规则仍执行。
- 发送失败不激活请求，也不创建账号。
- 默认依赖及真实本机 SMTP 协议到邮件内容、验证、注册、会话的链路通过。

## 浏览器及视觉验收

运行：

    node tools/test_email_registration_ui.cjs

需先构建，空闲本机端口 43129；使用独立无头 Edge 和本机附带 Playwright，
不读取用户浏览器资料，不启动后端。所有 API 由合成响应接管。

通过 3 个场景：
- 1440px /login；
- 390px /settings 登录门；
- 390px /library 登录门。

每个场景验证：
必填邮箱、未验证不能提交、错误验证码提示、修改邮箱清除证明、
切换模式清除状态、请求携带邮箱和证明、重名保留输入、
服务不可用阻止发码、注册成功广播登录状态并离开注册表单。
无未捕获页面异常。
桌面及窄屏截图已查看，保存在同目录第5步桌面.png、第5步窄屏.png。
浏览器使用模拟 API，后端使用本机 FakeSMTP；不冒充真实用户环境的浏览器到收件箱验收。

## 严格约束与停止点

所有数据库、文件和账号均为隔离测试数据。没有读取、复制或修改既有用户数据，
没有读取真实 .env，没有保存或使用真实 SMTP 授权码，也没有真实邮件发送。
功能仍未部署或永久启用；账号登录保持原契约，新注册必须具备邮箱服务。
工作始终在 HZL，main 与 HZL 提交指针不变：
00e7389f0d331cdb12c4c386587aaee9b38b00c7。
未提交、推送、切换分支、合并或部署。
本步运行的是相关回归，不是整个项目全量测试；全量检查仍在第 8 步执行。
第 6 步未开始，等待用户指令。
