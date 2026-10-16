# -*- coding: utf-8 -*-
"""定时任务 worker 进程入口（独立进程，不监听端口、不接受外部请求）。

启动方式：
  python -m worker.scheduler                # 常驻：APScheduler BlockingScheduler
  python -m worker.scheduler --once         # 把 5 个任务各跑一轮就退出（手工验证用）
  python -m worker.scheduler --once report.daily
  python -m worker.scheduler --list-jobs    # 打印任务定义表（cron / 开关 / 下次触发）
  python -m worker.scheduler --init-db      # 只建表 + 补齐任务定义，然后退出

为什么是独立进程而不是在 web 进程里起调度器
------------------------------------------
1. 总结生成一次 20~40 秒，跑在 web 的事件循环里会拖慢在线问答；
2. web 会重启、将来可能多副本，而「谁来跑」应该只有一个进程
   （job_runs 的幂等键能兜住重复投递，但那属于兜底，不是设计）；
3. worker 崩了 web 不受影响——代价是「静默故障」：接口全 200、用户只觉得
   「今天没收到提醒」。所以 worker 每轮心跳写 worker_heartbeat 表，
   由 GET /api/schedule/health 按**心跳年龄**判活（>600s 判 down，不看进程表）。

时区
----
`APSCHEDULER_TIMEZONE` 与 `core/config.SCHEDULER_TZ` 都取 Asia/Shanghai。
注意 APScheduler 3.11 已不再读这个名字的环境变量（那是 3.x 早期/2.x 的行为），
所以这里显式给 scheduler 与每个 CronTrigger 传 `timezone=ZoneInfo(...)`——
环境变量仍然设置，是为了让「按上海时间解释」这件事在不看代码时也一眼可见。
本文件禁止裸 `datetime.now()`；取当前时刻一律用 `core.scheduler.now_local()`。

业务动作的 hook 位置（本工单只留占位，由工单 05 的 `service/daily.py` 实现）
--------------------------------------------------------------------------
worker 负责幂等 / 重试分级 / 心跳 / 结构化日志，业务只实现下面这些函数即可，
**不要自己写 job_runs、不要自己判重**（否则两套幂等一定会漂移）：

  推荐（按对象粒度，幂等由 worker 保证）::

      due_subjects(job_key, planned_at) -> list[str]   # 本轮到期的对象 id；没有就 []
      run_one(job_key, subject_id, planned_at) -> dict # 干一次活，可返回 {"tokens_out": int}
      on_failure(job_key, subject_id, fatal: bool)     # 可选：不可重试失败时收尾

  可选（整任务入口，适合没有「对象」的活，如 housekeeping 的历史上传件清理）::

      run_job(job_key, planned_at) -> dict

  各 job 内部建议调用的纯函数（工单 05 的函数名）：
      report.daily / report.backfill  → service.daily.summarize_day(uid, date, stats, llm=...)
      delegate.dispatch               → service.daily.plan_today(delegation, context, llm=...)

对方模块还没写完时，worker 照常启动、照常写心跳，只是那一轮什么都不干
（`_load_handlers()` 用 try/except 兜住 import 失败，日志里说明原因）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo
from typing import Any, Callable

from core import config

# 时区必须在 apscheduler 之前定好：下面显式传 timezone，这里设的环境变量是给人看的说明
os.environ["APSCHEDULER_TIMEZONE"] = config.SCHEDULER_TZ

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # noqa: E402
from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: E402
from apscheduler.triggers.cron import CronTrigger  # noqa: E402

from core import scheduler as sched  # noqa: E402

WORKER_VERSION = "0.1.0"

# 心跳任务不在 schedule_jobs 表里：它不是「业务任务」，不该出现在 /api/schedule/jobs。
# 有它才能保证心跳年龄 ≤ 60s（只靠 5 分钟的 dispatch 写心跳，判定 down 会慢很多）。
HEARTBEAT_JOB_ID = "_worker_heartbeat"

# 失败重试扫描挂在哪个 job 上（cron 已与 dispatch 错开分钟，避免同一分钟抢 SQLite 写锁）
RETRY_JOB_KEY = "delegate.retry"

# 运行记录保留天数（housekeeping 清理）
RUN_KEEP_DAYS = 90

_log = logging.getLogger("worker.scheduler")

# job_key -> 业务模块（通常是 service.daily）。用注入的方式挂，见 register_handler。
_handlers: dict[str, Any] = {}
_handlers_loaded = False
_scheduler: BlockingScheduler | None = None


# ---------- 业务 hook 装载 ----------

def register_handler(job_key: str, module: Any) -> None:
    """手动挂一个业务模块（测试与函数注入用）。

    worker 只要求它提供 due_subjects + run_one（或 run_job），别的一概不假设。
    """
    _handlers[job_key] = module


def _load_handlers(force: bool = False) -> tuple[bool, str]:
    """按函数名把 service.daily 挂到 5 个 job 上；挂不上只记日志，不抛。

    返回 (是否挂上, 原因文案)。对方工单没写完时这里是 False，
    worker 仍然要能起来、能写心跳——「业务没实现」不该变成「调度不可用」。
    """
    global _handlers_loaded
    if _handlers_loaded and not force:
        return bool(_handlers), ""
    _handlers_loaded = True
    try:
        from service import daily  # type: ignore
    except ImportError as e:
        return False, "service.daily 尚未实现（%s）" % e
    except Exception as e:  # 对方模块里有语法错误/依赖缺失时也別把 worker 拖下水
        return False, "service.daily 导入失败：%s" % e
    for job_key in sched.JOB_DEFS:
        register_handler(job_key, daily)
    return True, ""


def _hook(module: Any, name: str) -> Callable[..., Any] | None:
    fn = getattr(module, name, None)
    return fn if callable(fn) else None


# ---------- 结构化日志（第 5 问的第 2 层告警手段） ----------

def _log_json(event: str, **fields: Any) -> None:
    payload = {"event": event, "worker_version": WORKER_VERSION, "ts": sched.now_ts()}
    payload.update(fields)
    try:
        _log.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # 日志永远不该把调度打断
        _log.info("event=%s %s", event, fields)


# ---------- 单个对象的执行（幂等 + 重试分级都在这条路径上） ----------

def _execute_one(job_key: str, subject_id: str, planned_at: int, run_id: str, module: Any) -> str:
    """执行一个对象并把结果落到 job_runs。返回 "succeeded" / "failed"。

    幂等：进这里之前已经 claim_job / claim_retry 成功（run_id 已置 running），
    所以同一个 (job_key, subject_id, planned_at) 不可能被执行两次。
    """
    runner = _hook(module, "run_one")
    if runner is None:
        sched.finish_job(run_id, ok=False, error="service.daily 未实现 run_one",
                         error_class=sched.CLASS_FATAL)
        return "failed"
    started = time.time()
    try:
        res = runner(job_key, subject_id, planned_at) or {}
        tokens = int(res.get("tokens_out") or 0) if isinstance(res, dict) else 0
        sched.finish_job(run_id, ok=True, tokens_out=tokens)
        _log_json("run", job_key=job_key, run_id=run_id, subject_id=subject_id,
                  status="succeeded", tokens_out=tokens,
                  elapsed_ms=int((time.time() - started) * 1000))
        return "succeeded"
    except Exception as e:  # noqa: BLE001 —— 业务异常一律转成「一次失败」，不能掀翻 worker
        cls = sched.classify_error(exc=e)
        sched.finish_job(run_id, ok=False, error="%s: %s" % (type(e).__name__, e), error_class=cls)
        _log_json("run", job_key=job_key, run_id=run_id, subject_id=subject_id,
                  status="failed", error_class=cls, error=str(e)[:300],
                  elapsed_ms=int((time.time() - started) * 1000))
        if cls == sched.CLASS_FATAL:
            # 依赖已失效（会话被删 / 库不存在 / 额度耗尽）：重试没有意义，
            # 交给业务收尾（例如把委托 enabled 置 false）
            closer = _hook(module, "on_failure")
            if closer is not None:
                try:
                    closer(job_key, subject_id, True)
                except Exception as e2:  # noqa: BLE001
                    _log_json("on_failure_error", job_key=job_key, subject_id=subject_id,
                              error=str(e2)[:300])
        return "failed"


def _run_job(job_key: str, planned_at: int) -> dict:
    """按 job 定义跑一轮：枚举到期对象 → 逐个 claim → 执行。"""
    stats = {"due": 0, "claimed": 0, "skipped": 0, "succeeded": 0, "failed": 0, "handler": False}
    module = _handlers.get(job_key)
    if module is None:
        return stats
    stats["handler"] = True

    due = _hook(module, "due_subjects")
    if due is not None:
        subjects = [str(s) for s in (due(job_key, planned_at) or [])]
        stats["due"] = len(subjects)
        for subject_id in subjects:
            run_id = sched.new_run_id()
            # 先登记再干活：INSERT ... ON CONFLICT DO NOTHING，插不进去说明这份活已经有人领了
            if not sched.claim_job(job_key, subject_id, planned_at, run_id=run_id):
                stats["skipped"] += 1
                _log_json("skip_duplicate", job_key=job_key, subject_id=subject_id,
                          planned_at=planned_at)
                continue
            stats["claimed"] += 1
            stats[_execute_one(job_key, subject_id, planned_at, run_id, module)] += 1
        return stats

    # 整任务入口：没有「对象」的活（housekeeping 之类）走这里，幂等键用 "*"
    whole = _hook(module, "run_job")
    if whole is not None:
        subject_id = "*"
        run_id = sched.new_run_id()
        if not sched.claim_job(job_key, subject_id, planned_at, run_id=run_id):
            stats["skipped"] += 1
            return stats
        stats["due"] = 1
        stats["claimed"] = 1
        try:
            whole(job_key, planned_at)
            sched.finish_job(run_id, ok=True)
            stats["succeeded"] += 1
            _log_json("run", job_key=job_key, run_id=run_id, subject_id=subject_id,
                      status="succeeded")
        except Exception as e:  # noqa: BLE001
            cls = sched.classify_error(exc=e)
            sched.finish_job(run_id, ok=False, error="%s: %s" % (type(e).__name__, e), error_class=cls)
            stats["failed"] += 1
            _log_json("run", job_key=job_key, run_id=run_id, subject_id=subject_id,
                      status="failed", error_class=cls, error=str(e)[:300])
    return stats


def run_pending(limit: int = 50) -> dict:
    """把手动触发（run-now 写的 pending 行）跑掉。

    只领「有业务实现」的 job：对方模块还没挂上时，pending 行留在表里等实现，
    不会被吞成一条假的 failed（手动触发的那一次不该凭空消失）。
    """
    stats = {"pending": 0, "succeeded": 0, "failed": 0}
    if not _handlers:
        return stats
    for row in sched.claim_pending(limit=limit, job_keys=sorted(_handlers)):
        module = _handlers.get(row["job_key"])
        if module is None:
            continue
        stats["pending"] += 1
        stats[_execute_one(row["job_key"], row["subject_id"], int(row["planned_at"]),
                           row["run_id"], module)] += 1
    return stats


def run_retries(limit: int = 50) -> dict:
    """把到点的重试跑掉（含配额触顶延后到次日同刻的那批）。

    重试**不新建行**：subject_id / planned_at 都取自原来那一行，
    幂等键不变——所以「重试成功两次」在会话里也只会落一条消息。
    """
    stats = {"due": 0, "claimed": 0, "succeeded": 0, "failed": 0}
    for row in sched.due_retries(limit=limit):
        module = _handlers.get(row["job_key"])
        if module is None or _hook(module, "run_one") is None:
            continue
        stats["due"] += 1
        if not sched.claim_retry(row["run_id"]):
            continue  # 另一个 worker 已经领走了，或 attempts 已到上限
        stats["claimed"] += 1
        stats[_execute_one(row["job_key"], row["subject_id"], int(row["planned_at"]),
                           row["run_id"], module)] += 1
    return stats


def run_housekeeping(planned_at: int | None = None) -> dict:
    """清理过期运行记录（>90 天），再调业务侧的历史上传件清理（有就调）。

    运行记录是本模块自己的表，自己清；上传件属于业务，留给 hook。
    """
    stat: dict[str, Any] = {"purged": 0, "uploads": None}
    cutoff = (sched.now_ts() if planned_at is None else int(planned_at)) - RUN_KEEP_DAYS * 86400
    stat["purged"] = sched.purge_runs(cutoff)
    module = _handlers.get("housekeeping")
    cleanup = _hook(module, "housekeeping_uploads") if module is not None else None
    if cleanup is not None:
        try:
            stat["uploads"] = cleanup(cutoff)
        except Exception as e:  # noqa: BLE001
            _log_json("housekeeping_uploads_error", error=str(e)[:300])
    return stat


# ---------- 每轮调度 ----------

def _beat() -> None:
    """心跳任务：只写一行心跳，不干活。"""
    sched.heartbeat(pid=os.getpid(), version=WORKER_VERSION)


def _tick(job_key: str) -> None:
    """一个 job 的一轮：写心跳 → 跑手动触发 → 跑本轮 → 回写 next_run_at。

    心跳放在最前面：即使这一轮的业务跑挂了，健康端点也不该判 worker 死了
    （那是「业务失败」，不是「调度进程故障」，两者告警口径不同）。
    """
    started = time.time()
    sched.heartbeat(pid=os.getpid(), version=WORKER_VERSION)
    planned_at = sched.now_ts()
    stats: dict[str, Any] = {"job_key": job_key, "planned_at": planned_at}
    stats.update(run_pending())
    if job_key == RETRY_JOB_KEY:
        stats.update(run_retries())
    else:
        stats.update(_run_job(job_key, planned_at))
    if job_key == "housekeeping":
        stats.update(run_housekeeping(planned_at))
    _sync_next_run()
    stats["elapsed_ms"] = int((time.time() - started) * 1000)
    _log_json("tick", **stats)


def _sync_next_run() -> None:
    """把「下次什么时候跑」写回 schedule_jobs，让 /api/schedule/jobs 与 /health 有得显示。

    有调度器时以 APScheduler 的 next_run_time 为准（它是权威）；
    `--once` 或 worker 没起时退化为按 cron 估算——worker 挂了的时候，
    运维恰恰最需要看到「下次该跑的时间」。
    """
    for j in sched.list_jobs():
        job = _scheduler.get_job(j["job_key"]) if _scheduler is not None else None
        # ⚠️ start() 之前作业是 pending：next_run_time 这个 slot 尚未赋值，
        # 直接访问会 AttributeError（worker 首次常驻时必炸）。getattr 安全取值。
        nrt = getattr(job, "next_run_time", None) if job is not None else None
        if nrt is not None:
            next_ts: int | None = int(nrt.timestamp())
        else:
            next_ts = sched.estimate_next_run(j["cron"], config.SCHEDULER_TZ)
        sched.update_next_run(j["job_key"], next_ts)


# ---------- 进程入口 ----------

def build_scheduler() -> BlockingScheduler:
    """按 schedule_jobs 表建 BlockingScheduler（SQLAlchemyJobStore → data/scheduler.db）。

    周期一律从表里读，不写死在代码里：改周期只改表，不用改代码、不用发版。
    """
    sched.ensure_tables()
    tz = ZoneInfo(config.SCHEDULER_TZ)
    store = SQLAlchemyJobStore(url=jobstore_url())
    sch = BlockingScheduler(jobstores={"default": store}, timezone=tz)
    for j in sched.list_jobs():
        if not j["enabled"]:
            _log_json("job_disabled", job_key=j["job_key"], cron=j["cron"])
            continue
        sch.add_job(
            _tick,
            CronTrigger.from_crontab(j["cron"], timezone=tz),
            args=[j["job_key"]],
            id=j["job_key"],
            name=j["job_key"],
            replace_existing=True,
            # 错过触发（比如 worker 停了一晚）在 5 分钟内补跑，再久就跳过——
            # 补跑一次「今天的总结」事小，第二天早上补跑十次事大
            misfire_grace_time=300,
            coalesce=True,      # 堆积的多次触发合并成一次
            max_instances=1,    # 同一个 job 不并发，SQLite 是单写者
        )
    sch.add_job(
        _beat,
        CronTrigger(minute="*", timezone=tz),
        id=HEARTBEAT_JOB_ID,
        name="worker 心跳",
        replace_existing=True,
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )
    return sch


def jobstore_url() -> str:
    """SQLAlchemy 连接串（Windows 路径要正斜杠）。"""
    return "sqlite:///" + sched._db_path().replace("\\", "/")


def _serve() -> int:
    """常驻：起调度器，阻塞到 Ctrl-C / SIGTERM。"""
    global _scheduler
    ok, why = _load_handlers(force=True)
    if not ok:
        _log_json("handlers_missing", reason=why)

    sch = build_scheduler()
    _scheduler = sch
    # 起来先写一次心跳：否则从启动到第一个整分钟之间，健康端点会显示 down
    sched.heartbeat(pid=os.getpid(), version=WORKER_VERSION)
    _sync_next_run()
    jobs = [j["job_key"] for j in sched.list_jobs() if j["enabled"]]
    _log_json("worker_start", pid=os.getpid(), tz=config.SCHEDULER_TZ, version=WORKER_VERSION,
              jobstore=jobstore_url(), jobs=jobs)
    try:
        sch.start()  # 阻塞；内部处理 KeyboardInterrupt / SystemExit
    finally:
        _scheduler = None
        _log_json("worker_stop", pid=os.getpid())
    return 0


def _run_once(target: str | None) -> int:
    """把指定 job（或全部）各跑一轮，跑完退出。给手工验收用。"""
    keys = list(sched.JOB_DEFS) if (not target or target == "*") else [target]
    unknown = [k for k in keys if k not in sched.JOB_DEFS]
    if unknown:
        print("未知的 task key: %s（可选：%s）" % (", ".join(unknown), ", ".join(sched.JOB_DEFS)))
        return 2
    ok, why = _load_handlers(force=True)
    if not ok:
        print("[提示] %s —— 本轮只写心跳，不执行业务动作" % why)
    for k in keys:
        _tick(k)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m worker.scheduler",
        description="定时任务 worker：独立进程 + APScheduler + SQLite JobStore（不监听端口）",
    )
    parser.add_argument("--once", nargs="?", const="*", default=None, metavar="JOB_KEY",
                        help="只跑一轮就退出；不给 JOB_KEY 表示 5 个任务各跑一轮")
    parser.add_argument("--list-jobs", action="store_true", help="打印任务定义表后退出")
    parser.add_argument("--init-db", action="store_true", help="建表并补齐任务定义后退出")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.init_db:
        sched.ensure_tables()
        print("已建表并补齐任务定义：%s" % sched._db_path())
        return 0

    if args.list_jobs:
        sched.ensure_tables()
        for j in sched.list_jobs():
            nxt = sched.estimate_next_run(j["cron"], config.SCHEDULER_TZ)
            print("%-18s %-16s enabled=%-5s tz=%s next_run_at=%s  %s" % (
                j["job_key"], j["cron"], j["enabled"], j["tz"], nxt, j["description"]))
        return 0

    if args.once is not None:
        return _run_once(args.once)
    return _serve()


if __name__ == "__main__":
    sys.exit(main())
