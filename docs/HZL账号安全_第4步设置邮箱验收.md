# HZL 账号安全：第 4 步设置邮箱绑定与换绑验收

日期：2026-11-19
状态：第 4 步完成；第 5 步暂停，等待用户明确指令。

## 改动与行为

- core/security_email.py：从登录身份和数据库读取账号状态、鉴权代数、当前安全邮箱和绑定版本。
- api/security_email.py：全部接口需登录，严格请求模型拒绝客户端传入 user_id 或版本字段。
- main.py：挂载账号安全路由；这是修改 HZL 工作区中的应用入口文件，不是修改 main 分支。
- frontend/src/components/settings/SecurityEmailSettings.tsx：设置中的安全邮箱卡片和绑定/换绑弹窗。
- frontend/src/lib/api.ts：对应接口调用。
- 设置页：在账号下方增加账号安全区域，按账号标识重建状态，避免沿用前一个账号的证明。
- tests/test_security_email_settings.py：18 项新增临时库接口测试。
- tools/test_security_email_ui.cjs：桌面和窄屏浏览器流程测试。

历史账号可继续正常登录；在设置中验证新邮箱后首次绑定。
邮箱显示脱敏地址，不返回原始完整邮箱。
已绑定账号只能换绑，不存在单独删除唯一安全邮箱的接口或按钮。
旧邮箱验证码只发送到数据库中该账号当前绑定的地址，前端指定地址不起作用。
换绑必须同时提供同一账号、同一流程的旧邮箱和新邮箱证明。
当前账号/绑定版本在事务中重新读取，业务写入与两个证明消费原子提交。
失败保留旧邮箱；并发绑定只能一个占用相同邮箱，并发换绑只允许一次生效。
原邮件提醒邮箱保持独立，删除提醒邮箱不影响安全邮箱。
本步没有修改用户名密码注册流程，不开放公开注册或找回用途。

## 数据与部署边界

功能默认仍关闭；没有永久启用 SMTP，也没有保存或使用之前提供的真实授权码。
默认依赖先要求登录，再检查开关和表是否存在；缺表返回 503，不自动迁移。
第 3 步的显式迁移只在测试临时库执行。
未来启用需另行安排测试/部署配置和显式迁移；本步不授权访问或改变既有用户数据库。
所有测试账号、SMTP 接收和绑定记录都是合成数据。
没有启动连接真实用户数据库的后端服务，没有发送真实邮件。

## 测试结果

后端最终命令：

    cd D:\zhisui_agent
    .venv\Scripts\python.exe tools/run_isolated_tests.py tests/test_security_email_settings.py tests/test_verification.py tests/test_baseline_environment.py tests/test_security_mail.py tests/test_account_profile.py tests/test_jwt_login_flow.py tests/test_auth_cookie_secure.py tests/test_mail.py -q

结果：177 passed，0 failed，0 skipped，44.78 秒。
第 4 步新增 18 项，前 3 步相关回归 159 项。
保留两个已有警告：LangChain community 弃用、头像尺寸边界 Pillow 警告。

Python 静态检查通过：

    .venv\Scripts\python.exe -m ruff check api/security_email.py core/security_email.py tests/test_security_email_settings.py main.py

后端覆盖：未登录拒绝、历史用户首次绑定、脱敏、错误码、跨账号证明、客户端身份字段拒绝、
旧收件人由服务端控制、两个证明必需、错误用途、错误流程、有效证明过期、
唯一邮箱并发绑定、并发换绑、旧登录代数失效、邮件故障保留绑定、提醒邮箱独立、
未开放注册/找回用途、关闭功能时鉴权、缺表不迁移。
默认依赖到 FakeSMTP 的完整发送/验证/绑定链路经过真实应用 HTTP 接口测试，
不仅依赖注入模拟发送器。

前端：
- 三个变更 TS/TSX 文件 lint 通过。
- tsc --noEmit --incremental false 通过。
- Next.js 生产构建通过（最终构建含 TypeScript 检查）。
- 完整前端 lint 仍为 36 errors、32 warnings，与历史报告逐条比较，差异为 0。
  未关闭规则、忽略目录或把历史失败改写为通过。
- Git diff --check 通过。

## 浏览器交互与视觉检查

从项目根运行：

    node tools/test_security_email_ui.cjs

需先构建前端、本机 43128 端口空闲。使用已安装 Edge 的独立无头进程，
不读取已有浏览器资料；Playwright 来自本机附带运行时，模块路径可用 PLAYWRIGHT_MODULE 覆盖，
浏览器渠道可用 ZHISUI_BROWSER_CHANNEL 覆盖。
脚本结束后关闭自己启动的前端和浏览器。

最终结果：
- 1440px 桌面：通过。
- 390px 窄屏：通过。
- 页面未捕获异常：0。
- 完整绑定、完整换绑、错误验证码、输入变化清除证明、关闭重开清空、
  两证明缺一不可、无解绑按钮、键盘焦点和 Esc、服务不可用提示均验证。
- 测试正常关闭产品首次引导，不使用强制点击。
- 所有 /api 请求被测试模拟接管，不连接后端；其他未接入业务按 FastAPI 标准 Not Found 契约模拟。
- 这是前端交互测试，与后端 FakeSMTP 集成测试分别执行，不冒充浏览器到真实邮件服务的全链路验收。
- 通过运行生成的桌面和窄屏截图已查看，无明显裁切或按钮不可见。

## 失败定位与修复

1. 开发前测试因接口模块缺失失败，确认待实现功能。
2. 状态接口直接调用服务而绕过依赖注入，导致状态不一致；统一为 FastAPI 依赖。
3. 新接口依赖声明触发 Ruff B008，改为 Annotated 声明，未关闭检查。
4. 浏览器没有专用 Chromium，使用已安装 Edge 的独立无头测试实例。
5. 浏览器模拟未匹配项目自动去掉尾斜杠的路径，按实际契约修复模拟。
6. 初次产品引导遮挡按钮，测试通过正常关闭入口完成前置流程。
7. 验证码标签将按钮文字包含进输入可访问名称，拆分标签与按钮；
   拆分过程出现的 JSX 闭合错误也在 lint 中发现并修复。
8. 未接入业务模拟返回非标准 404，触发页面探测逻辑异常；改为实际 Not Found 契约。
9. 所有相关断言和错误场景保留后，重新构建并完整重跑至通过。

## 停止点

当前仍在 HZL；main 分支和 HZL 提交指针未改变：
00e7389f0d331cdb12c4c386587aaee9b38b00c7。
所有代码保留在未提交工作区，未提交、推送、切换分支、合并或部署。
未读取或修改既有用户数据，未读取真实 .env。
本步仅运行相关回归，整个项目全量测试仍留到第 8 步。
第 5 步注册必绑邮箱尚未开始。
