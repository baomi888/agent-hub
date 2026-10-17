"""定时任务 worker 包。

`python -m worker.scheduler` 启动常驻调度进程；本包**不进 api/ 的依赖链**——
web 进程只 import `core.scheduler`（读表、写待执行行），不会把 APScheduler 拉进来。
"""
