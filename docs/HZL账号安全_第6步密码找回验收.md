# HZL 账号安全：第 6 步邮箱找回密码与密码显示验收

日期：2026-11-20
状态：第 6 步完成；第 7 步暂停，等待用户明确指令。

## 本步实现

- api/password_reset.py：公开的邮箱找回发码、验证和密码重置接口。
- core/security_email.py：当前安全邮箱查找、用途和浏览器绑定证明、事务内重置与失效控制。
- main.py：挂载找回路由，仅修改 HZL 工作区中的应用入口。
- frontend/src/components/PasswordInput.tsx：共用密码输入和可访问的小眼睛。
- frontend/src/components/AuthGate.tsx：登录密码行右下方“找回密码”，登录/注册密码显示开关。
- frontend/src/app/reset-password/page.tsx：邮箱验证码验证、两次新密码、错误反馈、成功返回登录。
- frontend/src/lib/api.ts：找回接口及重置成功后清除前端缓存登录状态。
- tests/test_password_reset.py：17 项新增后端测试。
- tools/test_password_reset_ui.cjs：桌面和窄屏找回/密码显示交互测试。

## 用户流程

登录页点击“找回密码” → 输入已绑定的安全邮箱 → 发送验证码 →
验证码输入和“验证邮箱”按钮出现 → 验证通过后显示两次新密码输入 →
两次一致且符合既有密码规则 → 重置成功，清空临时状态并返回登录页。
重置不自动登录，用户使用新密码重新登录。

登录、注册、新密码、再次输入新密码都默认隐藏。
每个密码输入独立切换，保留输入内容；键盘可操作，显示按钮不会提交表单。
登录/注册模式切换重新默认隐藏。请求处理中密码输入和显示按钮禁用。
修改密码输入后清除过时的错误提示。
设置中的旧密码修改功能未改变。

## 接口与安全边界

- POST /api/auth/password/reset/email/send
- POST /api/auth/password/reset/email/verify
- POST /api/auth/password/reset

接口只使用安全邮箱，不使用提醒邮箱，不存在手机号找回。
注册关闭不影响找回功能；邮件服务关闭或数据库未准备时拒绝操作，不自动迁移。
找回 Cookie 为随机 nonce、HttpOnly、SameSite=Lax，限定 /api/auth，
HTTPS 请求设置 Secure；证明同时绑定浏览器、用途、账号、邮箱和版本。

发码对已绑定账号、未注册邮箱、停用/删除/未绑定账号及 SMTP 发送故障返回相同字段与条件文案：
“如果该邮箱已绑定可用账号，验证码将发送到该邮箱，请检查收件箱或稍后重试。”
这不是“发送成功”或“邮件已到达”的承诺。
所有有效格式邮箱请求仍受接收方、IP、账号/未知接收方和全局限流约束。
不存在的账号不发送邮件；失败请求不能验证。
这里避免明确的响应文案/字段存在性泄露，不声称消除所有网络时间侧信道。

验证码错误上限、期限和一次性凭证规则复用第 3 步。
消费证明时在同一写事务内重新确认账号可用、未删除、邮箱仍绑定且版本一致。
密码散列更新、鉴权代数加一、失败登录计数/锁定清除、证明消费原子提交。
旧 Cookie 和 Bearer 令牌立即失效，其他旧验证码和找回证明因代数不匹配失效。
换绑后旧邮箱取得的找回证明同样不能使用。
服务端和前端都检查两次新密码一致，服务端复用既有密码规则并检查不能与登录名相同。
成功后删除当前会话 Cookie 和找回 Cookie；前端广播收回登录状态并跳转 /login。

## 测试结果

最终后端命令：

    cd D:\zhisui_agent
    .venv\Scripts\python.exe tools/run_isolated_tests.py tests/test_password_reset.py tests/test_email_registration.py tests/test_security_email_settings.py tests/test_verification.py tests/test_baseline_environment.py tests/test_security_mail.py tests/test_account_profile.py tests/test_jwt_login_flow.py tests/test_auth_cookie_secure.py tests/test_mail.py -q

结果：207 passed，0 failed，0 skipped，59.09 秒。
第 6 步新增 17 项，前 5 步相关回归 190 项。
两个已有警告保留：LangChain community 弃用提示、头像尺寸边界 Pillow 警告。

覆盖：
- 旧密码失败，新密码可重新登录；旧 Cookie 与 Bearer 被拒绝。
- 两次不一致、弱密码、与用户名相同均拒绝，失败不提前消耗有效证明。
- 已知/未知邮箱发码字段和文案一致；未知邮箱不发信。
- 错误验证码及错误次数上限、验证码过期、证明精确到期、重复使用和跨浏览器使用。
- 邮箱换绑后旧证明拒绝。
- 并发重置最多一个成功，其他尚未使用的找回证明失效。
- 注册关闭仍能找回；邮件功能关闭拒绝操作。
- 停用、删除和未绑定账号不能找回。
- SMTP 发送失败不激活请求，也不宣称已投递。
- 默认服务依赖到真实本机 FakeSMTP 协议、验证码验证及密码重置的链路通过。

Python 变更文件 ruff 检查通过。
前端变更文件 ESLint、类型检查及 Next.js 生产构建通过（含 /reset-password）。
完整前端 lint 保留历史 36 errors、32 warnings，逐条对比差异为 0。
Git diff --check 通过。
本步未运行整个项目全部测试，全量仍留在第 8 步。

## 浏览器与视觉验证

    node tools/test_password_reset_ui.cjs
    node tools/test_email_registration_ui.cjs

需先构建前端，并提供空闲本机端口 43130（找回测试）/43129（注册回归）。
使用独立无头 Edge 和本机附带 Playwright，不读取已有浏览器资料。
全部 API 被测试接管，不启动真实后端或发送外部邮件。

找回测试 1440px、390px 两个场景通过：
- 登录/注册默认隐藏、切换显示保留内容、显示按钮不提交。
- 两个新密码框独立切换，键盘 Enter 可操作。
- 找回入口、错误验证码、两次不一致不发起重置请求、后端弱密码错误反馈。
- 只有验证通过才显示重置表单，重置成功返回登录，密码为空且未自动登录。
- 重新访问找回页面不保留邮箱、证明或密码，无未捕获页面异常。

已有注册浏览器回归 3 个场景通过：
1440px /login、390px /settings 登录门、390px /library 登录门。
桌面及窄屏截图已查看，无明显裁切，最终截图保存在同目录第6步桌面.png、第6步窄屏.png。
这些截图是验证码验证通过、可以填写两次新密码的状态，不是发码前的页面。

## 失败定位与修复

1. 初始 10 项测试全部在缺失找回接口处收到 404，确认待实现能力。
2. Ruff 自动修复把跨模块 pytest 夹具导入认作未使用而删除，导致夹具缺失；
   改为明确再导出，保留所有用例与断言，未跳过测试。
3. 新增长行通过正常格式化修复，未放宽检查规则。
4. 截图发现修正密码后仍显示过时错误提示，编辑密码时清除，并重新构建和重跑两场景。

## 执行约束及停止点

全程 HZL；main 分支未改动，未提交、推送、合并、切换分支或部署。
HZL 和 main 提交指针保持 00e7389f0d331cdb12c4c386587aaee9b38b00c7。
全部数据为临时库与合成账号，没有读取、复制或修改已有用户数据，没有读取真实 .env。
未保存或使用真实 SMTP 授权码，未发送真实邮件，未永久启用功能或自动迁移用户库。
第 7 步全流程联调和真实邮件验收尚未开始。
