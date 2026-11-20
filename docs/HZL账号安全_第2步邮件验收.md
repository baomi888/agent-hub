# HZL 账号安全开发：第 2 步邮件服务验收

日期：2026-11-19
状态：第 2 步完成；第 3 步未开始，等待用户指令。

## 本步实现

- core/security_mail.py：安全验证码邮件模板和发送适配层，不访问用户数据库。
- 支持注册、首次绑定、旧邮箱验证、新邮箱验证、找回密码五种用途。
- 仅接收单个合法邮箱和六位 ASCII 数字；用途不合法不会发信。
- 有效期文案统一引用 CODE_TTL_SECONDS=600，后续验证码存储应复用同一常量。
- 只返回 ready 能力状态，不暴露 SMTP 账号和凭证。
- 对外发送只允许 SSL / STARTTLS；明文 SMTP 仅供本机回环测试。
- 安全邮件独立开关 SECURITY_EMAIL_ENABLED，默认关闭；不改变原提醒邮件开关和业务。
- 复用原 core/mailer.py 发送与错误分类；适配层返回固定中文说明，不转交原始服务器错误。
- .env.example 只增加说明和非秘密开关，没有真实凭证。
- tools/check_security_mail_live.py：受控 QQ SMTP 自发自收验收；隐藏输入，仅在进程内使用凭证。
- tests/test_security_mail.py：25 项新增测试。

## 测试与修复证据

1. 开发前新增测试失败：安全邮件模块尚不存在，确认是待实现能力缺失。
2. 实现后初次新增 23 项测试通过。
3. 真实验收工具首次非交互运行未取得标准输入，退出且没有发送邮件。
   定位为执行环境关闭了标准输入；增加隐藏交互输入和缺少输入时安全退出。
   新增回归证明空输入返回 2、不发送邮件、不抛出 traceback。
4. 补充外部传输错误响应不暴露敏感值的测试。
5. 最终新增测试与相关回归：128 passed，0 failed，0 skipped，37.16 秒。
   其中第 1 步相关回归 103 项，第 2 步新增 25 项。
6. 改动 Python 文件 ruff check 通过；pip check 通过；Git diff --check 通过。
7. 两条既有测试警告保留：LangChain community 弃用、头像尺寸边界 Pillow 警告。

复现：

    cd D:\zhisui_agent
    .venv\Scripts\python.exe tools/run_isolated_tests.py tests/test_baseline_environment.py tests/test_security_mail.py tests/test_account_profile.py tests/test_jwt_login_flow.py tests/test_auth_cookie_secure.py tests/test_mail.py -q
    .venv\Scripts\python.exe -m ruff check core/security_mail.py core/config.py tests/test_security_mail.py tools/check_security_mail_live.py
    .venv\Scripts\python.exe -m pip check

全部自动化测试仍使用临时目录和本机 FakeSMTP。
本步没有前端改动，没有重复构建；历史 lint 36 errors、32 warnings 仍记录，不宣称通过。
本步不是全项目全量测试，最终全量检查仍在第 8 步执行。

## 真实收发验收

- 服务：smtp.qq.com，465，SSL，使用默认可信证书验证。
- 收件范围：用户提供的发件邮箱自身，仅发送一封测试邮件；账号在本文中不完整记录。
- 验收主题：知穗 Agent · 找回密码验证码。
- SMTP 返回：smtp_accepted=true，error=null。
- 用户明确确认：“已收到，内容正常”。
- 未向其他收件人发信，没有查询邮箱中的其他邮件。

“SMTP 接受”和“用户确认收件”分开记录，两者在本步均完成。
验证码仅用于本次邮件模板验收，没有创建验证码数据库、绑定邮箱或修改密码。

## 配置和保密边界

- 没有读取或修改项目真实 .env，也没有把授权码写入代码、文档或配置文件。
- 提供的授权凭证通过隐藏输入进入独立测试进程；没有回显到日志，进程结束后不保留凭证。
- 实际业务运行配置仍未永久启用，后续启动测试环境时需要通过环境变量安全注入 SMTP 配置。
- SECURITY_EMAIL_ENABLED=1、MAIL_ENABLED=1 才启用；未配置时 status.ready=false，不报告发送成功。
- 不把本步视为生产部署；不修改当前运行服务或全局环境配置。
- 真邮件工具在联网前建立只允许指定 SMTP IP/465 的连接限制，拒绝任何 SQLite 连接和已有数据目录访问。
- Python 审计钩子为防误访问措施，不替代操作系统安全沙箱。

## 分支和停止点

- 全部改动在 HZL 工作区，未提交、推送、切换分支或合并。
- HZL 和 main 指针仍为 00e7389f0d331cdb12c4c386587aaee9b38b00c7。
- 本步没有访问或修改现有用户数据，没有修改提醒邮箱绑定关系。
- 第 3 步验证码持久化、数据结构和一次性凭证尚未开始。
- 等待用户明确指令后再执行下一步。
