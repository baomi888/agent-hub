# HZL 账号安全开发：第 1 步基线（2026-11-19）

状态：基线验收已完成（2026-11-19 用户确认接受历史 lint 问题暂不修复），第 2 步及之后暂停。业务代码未修改，未提交或推送。完整前端 lint 仍未通过，不计为全绿。
起始 HZL / main 提交均为 00e7389f0d331cdb12c4c386587aaee9b38b00c7。

## 环境

- Python 3.12.14；独立 .venv，按 requirements.txt 安装，另装 pytest 和 ruff==0.17.3。
- Node.js v24.22.3；通过临时 npm 11 按 package-lock.json 执行 npm ci。
- npm 未在 PATH 中；使用 Codex 附带 pnpm 临时运行 npm，没有全局安装。
- pip check 通过；依赖和锁文件没有修改。
- 未读取项目 .env 或既有数据库。

## 安全测试入口

从项目根运行：

    .venv\Scripts\python.exe tools/run_isolated_tests.py tests/test_baseline_environment.py tests/test_account_profile.py tests/test_jwt_login_flow.py tests/test_auth_cookie_secure.py tests/test_mail.py -q

每次运行创建独立临时目录并保留以供排查。入口在导入业务模块前设置数据目录和
AGENT_HUB_DB，禁用 .env 加载；审计钩子阻止打开仓库 data / chroma_db / downloads /
data-staging 和 .env，SQLite 只允许使用本次临时目录，网络只允许本机回环。
这是 Python 测试进程的防误访问措施，不是操作系统级安全沙箱。
运行时不要直接启动未隔离的 main.py，也不要直接对用户数据库运行 pytest。

新增 4 项测试覆盖路径隔离、拒绝访问真实数据及配置、阻止外网连接、现有账号流程。
账号流程通过真实应用 HTTP 接口验证注册、登录、退出、修改密码和旧会话失效。

## 结果

- 账号、登录、Cookie、邮件及新增测试：103 passed，0 failed，0 skipped，29.26 秒。
- 两条警告：LangChain community 弃用提示；头像尺寸边界测试的 Pillow 警告。
- 新增 Python 文件 ruff check：通过。
- pip check：通过。
- 前端 TypeScript（tsc --noEmit --incremental false）：通过。
- Next.js 生产构建：通过，生成 23 个静态页面。
- 前端 HTTP 检查：/login、/settings 均为 200，且包含应用内容。
  使用 node tools/frontend_baseline.cjs 复现；须先完成前端构建，
  需要空闲的本机端口 43127，测试结束会关闭自己启动的进程。
  这不是浏览器交互或完整页面业务流程测试。
- 前端完整 ESLint：失败，36 errors、32 warnings。没有忽略目录、关闭规则或删除测试。

## 已定位的历史前端失败

原有前端文件无任何改动。完整机器可读错误报告：HZL账号安全_历史前端lint.json（原始报告副本；保留 36 errors、32 warnings）。

类别：react-hooks/set-state-in-effect、react-hooks/refs、react-hooks/globals、
react/no-unescaped-entities。涉及 _backup、cet、dashboard、delegate、knowledge、path、
reminders、forum、ChatArea、PageTransition、UsagePanel、部分提醒组件和 usePrefs 等。

这些问题在本次业务功能开发前已存在，修复将触及多个与账号功能无关的模块。
2026-11-19 用户明确要求：保留这些历史问题，不纳入本次修复。第 1 步按建立基线的目标验收完成；这不改变 lint 失败结果。
不得通过修改 ESLint 规则、忽略文件或跳过检查消除失败。

## 本步改动

- tools/run_isolated_tests.py：独立测试入口。
- tests/test_baseline_environment.py：4 项基线测试。
- tools/frontend_baseline.cjs：前端 HTTP 启动检查。
- 本文：基线结果和复现说明。

第 2 步（邮件服务）尚未开始；没有真实发送短信、邮件或调用 AI 服务。需求已改为仅邮箱，详细顺序见 HZL账号安全_仅邮箱开发计划.md。
全部功能完成后的全量测试尚未运行，本步只执行相关基线检查。

