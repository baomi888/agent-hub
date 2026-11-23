# 后台生成实施计划

用户已确认 A 方案与回放衔接、终态一致、缓冲交接三项保证，授权直接执行。

目标为浏览器关闭后生成继续，刷新可恢复，停止显式取消。第一阶段使用单 API 进程，进程退出时中断。

- [x] 在 core/chat_runs.py 管理后台任务、逐帧回放、订阅、硬超时与终态保留。先写隔离测试，覆盖断连不取消、回放接续、停止与超时、缓冲溢出。
- [x] api/chat.py 由独立任务消费现有各模式生成器，HTTP 仅订阅。提供 GET /api/chat/stream/{sid} 与 POST /api/chat/{sid}/stop，meta 带 message_id，现有事件保持兼容。
- [x] 任务持有上游关闭和并发名额释放职责。修正草稿持久化成功标记，防止失败后被当成已提交。
- [x] 会话删除、消息删除、退出和应用退出取消并等待任务收尾。按用户限制一个运行任务，在扣费和插入消息前预留。
- [x] 前端恢复 streaming 消息、重连、显式停止、生成中状态。复用现有事件展示与播放器，回放历史直接呈现。
- [x] 部署脚本明确单 worker 约束。补充现有方案中的实现说明与 B 阶段边界。
- [x] 使用 tools/run_isolated_tests.py 执行相关后端测试，运行前端类型检查和 lint，审查最终差异。

内存限制的实现选择需同时保留事件元数据与未落库尾部。优先使用有大小上限的内存回放日志，超过上限溢出临时文件，数据库继续承担消息持久化。临时日志随任务过期关闭，避免全量文本与事件双重常驻内存。该选择保障数据库写入暂时失败时仍可重连，进程重启仍按 A 方案中断。


## 验证记录

在独立 Python 3.12 环境安装 requirements.txt，测试经 tools/run_isolated_tests.py 运行，所有数据库和文件落在临时目录，外部模型使用替身。

```powershell
.scratch/background-chat-py312/Scripts/python tools/run_isolated_tests.py tests/test_chat_runs.py tests/test_sse_disconnect.py tests/test_background_chat_api.py tests/test_stream_draft.py tests/test_truncation_detect.py tests/test_baseline_chat_stream.py tests/test_jwt_login_flow.py tests/test_query_enhance_in_chat.py -q
```

前端检查使用 `npx tsc --noEmit`、针对修改文件的 ESLint 和 `node --test tests/background-stream.test.cjs`。部署脚本通过 `bash -n`。浏览器验证记录在本次任务最终结果中。

后端相关回归最终结果为 91 项通过，存在一项 langchain-community 依赖弃用提示。前端传输回归 6 项通过，类型检查、修改文件 ESLint、后端 Ruff 和差异空白检查均通过。

浏览器使用临时 Next 服务与模拟 API，5 个场景通过，均无页面错误。场景包括刷新恢复、准备阶段轮询、准备阶段停止、首次自动建会话发送、完成后停止保持 done。隔离浏览器预先标记引导与当前版本公告已读，等待启动画面结束后使用正常按钮操作。临时服务已清理。尚未部署到服务器，真实模型链路未进行付费调用验证。
