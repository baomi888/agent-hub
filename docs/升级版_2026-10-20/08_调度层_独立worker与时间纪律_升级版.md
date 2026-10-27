# 08_调度层_独立worker与时间纪律_升级版（2026-10-20）

> 对应研讨卷：`docs/技术选型研讨_2026-10-20/08_调度层_独立worker与时间纪律.md`
> 本卷回答的问题：从校赛到省赛，调度层要改什么、为什么现在必须改、改完怎么验。

---

## 0. 一句话结论

**机制已经齐全（幂等键、心跳三态、错过补偿、重试分级、cron 存表），缺的是三件"能拿出来给人看"的东西：单任务的超时上限、错过执行的留痕、连续失败的告警接线。**

校赛口径是"到点能跑、不会重复发"——`UNIQUE(idem_key)` 加 `misfire_grace_time=300` 已经做到。
省赛口径是"它跑没跑、跑砸了几次、我什么时候知道的"——而这三条现在**都只在 journald 的日志里**，没有一处能一眼看到。无人值守链路的价值恰恰在于"出事时有人知道"，这一层的升级就是把"知道"这件事产品化。

---

## 1. 现状盘点

| 组件 | 现状 | 出处（文件:行） |
|---|---|---|
| 进程形态 | 独立进程 `python -m worker.scheduler`，不监听端口、不接受外部请求 | `worker/scheduler.py:1-8` |
| 调度内核 | APScheduler 3.x `BlockingScheduler` + `SQLAlchemyJobStore(data/scheduler.db)` | `worker/scheduler.py:383-419` |
| 状态自管 | `core/scheduler.py` **不 import apscheduler**，幂等/重试/心跳全部自实现（可单测） | `core/scheduler.py:1-17` |
| 任务数 | **7 个**：`delegate.dispatch`/`delegate.retry`/`report.daily`/`report.backfill`/`housekeeping`/`profile.aggregate`/`prompt.evolve` | `core/scheduler.py:80-125` |
| 自进化任务 | `prompt.evolve` 默认 cron `20 */6 * * *`（每天 4 轮），周期可改表或改 `PROMPT_EVOLVE_CRON` | `core/scheduler.py:118-121` |
| cron 存表 | `schedule_jobs` 表；代码只给默认值，运维改表不会被重启打回 | `core/scheduler.py:128-135`、`238-242` |
| 幂等 | `idem_key = job_key\|subject_id\|planned_at` + `UNIQUE` + `ON CONFLICT DO NOTHING` + 判 rowcount | `core/scheduler.py:154`、`259-265`、`327-342` |
| 重试分级 | `retry`/`fatal`/`quota` 三类；退避 1/5/15 分钟，quota 延后 86400s（次日同刻） | `core/scheduler.py:63-67`、`463-517` |
| 失败分类 | 异常类型 > HTTP 状态码 > 文案关键词；判不出来按**不可重试**处理 | `core/scheduler.py:401-460` |
| 心跳 | 独立 job 每分钟写一次（`CronTrigger(minute="*")`）；三态 up/stalled/down（300/600s） | `worker/scheduler.py:409-418`；`core/scheduler.py:57-61`、`751-792` |
| 心跳内容 | `ts` + `pid` + `version`（`WORKER_VERSION="0.1.0"`） | `core/scheduler.py:717-732`；`worker/scheduler.py:73` |
| 错过补偿 | `misfire_grace_time=300` + `coalesce=True` + `max_instances=1` | `worker/scheduler.py:396-408` |
| **单任务超时** | **无**。只有"同一 job 不并发"，没有"这一跑最多多久" | `worker/scheduler.py:396-408`（无 timeout 参数） |
| 结构化日志 | `_log_json` 每轮 tick 输出一条 JSON：`due/claimed/skipped/succeeded/failed/elapsed_ms` | `worker/scheduler.py:141-147`、`339-358` |
| 时区 | 唯一入口 `core/timeutil.py`（`TZ`/`now_ts`/`now_local`/`day_str`/`day_bounds`/`hour_of`/`shift_day`/`recent_days`）；`TZ_STRICT=0` 紧急回退 | `core/timeutil.py:39-44`、`68-124` |
| 时区纪律 | worker 禁止裸 `datetime.now()`；显式给 `CronTrigger` 传 `ZoneInfo` | `worker/scheduler.py:19-25`、`389-399` |
| 手动触发 | `POST /api/schedule/run-now`（写 pending 行，worker 下一轮领） | `api/schedule.py:101-133` |
| 手动重排 | `POST /api/schedule/retry`（`force=true` 清 attempts，突破 3 次上限） | `api/schedule.py:136-165`；`core/scheduler.py:555-590` |
| 查询接口 | `/health`（心跳三态 + 各 job 最近一次）、`/jobs`（cron/开关/下次触发）、`/runs`（游标分页） | `api/schedule.py:64-98` |
| 连续失败 | `job_stats().consecutive_failures` **已算出**，但只出现在 `/health` 的 JSON 里，**未接任何告警** | `core/scheduler.py:683-711`、`770-783` |
| 进程守护 | systemd `Restart=always` / `RestartSec=10` / `TimeoutStopSec=20` | `systemd/agent-hub-worker.service:11-13` |
| 清理 | `housekeeping` 清 >90 天运行记录，**默认保留 failed** | `worker/scheduler.py:313-328`；`core/scheduler.py:667-680` |
| 业务与调度解耦 | 手动与定时走同一条 `service/daily.py` 路径，不产生第二套代码 | `main.py:94-97`（卷八 §8.10） |

一句话：**幂等、重试、心跳这条主线是完整的，而且比很多同类项目做得更细。缺的不是机制，是"机制跑起来了没有"的观测面。**

---

## 2. 校赛够用 vs 省赛不够

| 维度 | 校赛口径 | 省赛口径 | 差距 | 证据 |
|---|---|---|---|---|
| worker 崩了 | systemd 会自动拉起 | 能出示"kill 之后 N 秒自愈 + 该补的任务补跑了" | 有 `Restart=always`，**无自愈演练、无告警通知人** | `systemd/agent-hub-worker.service:12`；`monitoring/README.md:44-46` 三条告警里没有"worker 心跳"独立告警 |
| 任务卡死 | 心跳会变黄（stalled） | 卡死的任务能被**中断**而不是一直占着 | **没有单任务超时**：一次挂住的 LLM 调用会让该 job 永久停在 running | `worker/scheduler.py:396-408` 只有 `max_instances=1` |
| 重复执行 | 单 worker + 幂等键 | 能演示"两个 worker 同时跑也只发一条" | 单实例下从未验证过多实例路径（卷八自己也写了"job_runs 的幂等键能兜住重复投递，但那属于兜底"） | `worker/scheduler.py:13-14` |
| 错过执行 | `misfire_grace_time=300` + `coalesce` | 能回答"昨晚 22:00 停到今早 9:00，补了几次、跳了几次" | 有机制、**不留痕**：补跑与静默跳过的次数都不记 | `worker/scheduler.py:403-405` |
| 执行记录 | `job_runs` 有 status/attempts/error/tokens_out/elapsed | 有"成功率 / 耗时 p50 / 失败分类"的聚合视图与界面 | 有原始行、有 /runs 接口，**无聚合、无界面**（前端是否有页面**待核对**） | `core/scheduler.py:137-151`；`api/schedule.py:81-98` |
| 失败告警 | 日志里有 | 连续失败能主动通知到人 | `consecutive_failures` 已算、未接告警 | `core/scheduler.py:683-711` |
| 时区 | `core/timeutil.py` 唯一入口 | 跨时区/夏令时有测试守着 | `TZ_STRICT=0` 的回退路径存在但未见测试；`day_bounds` 已用 `+timedelta(days=1)` 防夏令时 | `core/timeutil.py:42-44`、`94-105` |
| cron 表达力 | 7 个任务全是"天天跑" | 支持"每月 1 号""仅工作日" | `estimate_next_run` 只支持日/月/周为 `*`，其它一律返回 None（宁可显示未知） | `core/scheduler.py:845-857` |
| 时间语义 | `report.daily` 23:30 生成当日总结 | 23:30 之后的新消息要有补生成机制 | **无 stale 标记**：卷八 §8.3 已点名这是语义缺口 | `core/scheduler.py:99-106`；卷八 §8.3 |

---

## 3. 升级项

### P0-1 单任务超时与"卡死可中断"

- **现状**：`add_job` 只设了 `misfire_grace_time=300`、`coalesce=True`、`max_instances=1`（`worker/scheduler.py:396-408`）。`max_instances=1` 只保证"同一 job 不并发"，**不保证"这一跑最多多久"**。一次挂住的 LLM 调用会让该 job 永久停在 running，后续 tick 全部被 `max_instances` 挡掉——表现是"心跳正常、这个任务再也不跑了"。
- **改什么**：
  1. `_execute_one` 里给 `runner()` 加超时（线程池 `concurrent.futures` 的 `wait(timeout=JOB_TIMEOUT)`，或 `signal.alarm` 兜底），超时按 `CLASS_RETRY` 分级落库；
  2. `core/scheduler.py` 加一个 `stale_running(cutoff)` 查询：`status='running' AND started_at < now - N` 的行；
  3. `/health` 里暴露 `stale_running_count`，并把这些行在下一轮 tick 时强制置 failed（带 `error="timeout_guard"`）。
- **为什么省赛必须改**：这是**唯一一个"心跳全绿但业务已经死了"的故障模式**。省赛演示期间如果 `report.daily` 卡在某次 LLM 调用上，用户看到的是"今天没收到总结"，而 `/api/schedule/health` 显示 up——这正是卷八开篇说的"没有用户在场，所有兜底机制失效"。
- **代价/风险**：强制超时可能打断一次真实的长总结（20~40s），阈值必须 > 单次业务上限（建议 300s，是正常耗时的 7 倍以上）；线程池中断不了"卡在 socket 读"的调用，只能标记 + 放弃，不能真杀——因此第 3 步（强制置 failed）比第 1 步更重要。
- **反转条件**：若所有 job 的上游调用都已有超时（`core/llm.py` 的字节间隔超时已覆盖流式，非流式路径**待核对**），可只做第 3 步的 stale 扫描。
- **验收口径**：注入一个 `run_one` 里 `time.sleep(600)` 的假 handler，`JOB_TIMEOUT` 后该 run 被置 failed 并进入重试链；`/health` 的 `stale_running_count` 从 1 变 0。

### P0-2 错过执行的留痕

- **现状**：`misfire_grace_time=300` + `coalesce=True` 决定了"停一晚上之后只补一次、再久就跳过"，但**补了几次、跳了几次完全没有记录**。
- **改什么**：在 `_tick` / `_sync_next_run` 里比对 APScheduler 的 `next_run_time` 与 `schedule_jobs.next_run_at`，把"本轮实际触发时刻 - 上一次预定时刻 > 一个周期"识别为 gap，写一条 `_log_json("missed_window", job_key=..., gap_seconds=..., planned_skipped=N)`；同时在 `schedule_jobs` 加两列 `last_missed_at` / `missed_count`（或直接落 `job_runs` 一条 `status=skipped` 的行 —— 五种状态里 `skipped` 已定义但**只在重复登记时用**，语义可复用）。
- **为什么省赛必须改**：省赛要"系统测试数据充分"。现在是"系统悄悄跳过了 8 次，没人知道"；改完是"系统记录了 8 次跳过，并告诉你它按策略补了 1 次"。后者才是可答辩的。
- **代价/风险**：跳过补跑的判定依赖 APScheduler 内部状态，取不到时（worker 刚起）要退化成按 cron 估算——`estimate_next_run` 已经为这个场景写好了（`core/scheduler.py:833-872`），直接复用。
- **反转条件**：无。
- **验收口径**：停 worker 2 小时再起，日志出现一条 `missed_window`，`GET /api/schedule/jobs` 能看到 `missed_count` 增量。

### P0-3 连续失败告警接线

- **现状**：`job_stats()` 已经算出 `consecutive_failures`（`core/scheduler.py:683-711`），`/health` 也返回它（`core/scheduler.py:770-783`），但**没有任何东西在读它**。`monitoring/README.md:44-46` 的三条告警是"组件挂 / 主机失联 / 磁盘不足"，不含"任务连续失败"。
- **改什么**：
  1. 在 `_tick` 末尾对每个 job 调一次 `job_stats`，`consecutive_failures >= 3` 时打一条 `_log_json("alert", level="critical", ...)`；
  2. `probe-health.sh` 里加一行：读 `/api/schedule/health` 的 `consecutive_failures` 最大值，写成 `agent_hub_job_consecutive_failures` 指标；
  3. 在 `alerts.yml` 加第 4 条规则：`max(agent_hub_job_consecutive_failures) >= 3` 持续 10m。
- **为什么省赛必须改**：省赛评审问"你的系统出问题你怎么知道"，现在的答案是"看日志"——而日志在 journald 里，需要 SSH。改成指标后是"手机上收到通知"。这一项成本极低（三个文件各几行），是性价比最高的一项。
- **代价/风险**：第 4 条告警会增加告警噪音；`quota` 类失败（延后 24h）不该计入告警，需要按 `error_class` 过滤——`job_runs` 已有 `error` 文本，分类字段**当前不落库**（`finish_job` 写了 `error` 但没写 `error_class`），需顺带补一列。
- **反转条件**：若告警噪音过大导致疲劳，把阈值从 3 提到 5。
- **验收口径**：手工把某个 job 的 handler 换成抛异常的实现，跑三轮后指标值 = 3，Prometheus 规则进入 pending。

### P1-1 多实例安全验证

- **现状**：`worker/scheduler.py:13-14` 的注释主动声明"job_runs 的幂等键能兜住重复投递，但那属于兜底，不是设计"——也就是说**多实例这条路径从来没被验证过**。
- **改什么**：不加分布式锁（那是 P2 且没必要），只做三件事：
  1. 写一个双进程测试（两个 `python -m worker.scheduler --once` 同时打同一个 `planned_at`），断言 `job_runs` 只多一行、业务只执行一次；
  2. 把"只允许一个 worker"写进运维文档（`systemd` 的 unit 是单例，扩副本前必读）；
  3. `run_pending` 的 `claim_pending` 已经是 `UPDATE ... WHERE status='pending'` 的 CAS（`core/scheduler.py:389-395`），补一个注释说明它为什么在多实例下也安全。
- **为什么省赛必须改**：评分标准"技术方案 30%"，能把"我们为什么不需要分布式锁"讲清楚，比"我们上了分布式锁"更得分——前提是你**验证过**。
- **代价/风险**：零（只加测试与文档）。
- **反转条件**：真的要上第二个 worker 实例时，才需要重新评估（那时应该先做卷七的"换 PG"判据）。
- **验收口径**：双进程测试进 `tests/`，CI 绿。

### P1-2 执行记录聚合视图与后台界面

- **现状**：`/api/schedule/runs` 返回原始行（游标分页，`api/schedule.py:81-98`），`/health` 返回每个 job 的 `last_status/last_run_at/consecutive_failures`。没有"最近 7 天成功率""按 job 分组的耗时 p50"。
- **改什么**：加一个 `sched.summary(days=7)`：`{job_key: {total, succeeded, failed, success_rate, p50_ms, last_error}}`；前端做一个 `/schedule` 页（若已有则接入）。
- **为什么省赛必须改**：演示时评委想看的是一张表，不是一串 JSON。这一项直接吃"作品呈现 20%"。
- **代价/风险**：聚合查询在 `job_runs` 上跑（有 `idx_job_runs_job` 索引，`core/scheduler.py:157`），7 天数据量下毫秒级；前端页面是**新增工作量**，需与前端同学排期。
- **反转条件**：若演示录制视频里用 `curl | jq` 已足够，可只做接口不做页面。
- **验收口径**：`GET /api/schedule/summary?days=7` 返回 7 个 job 的聚合；页面上能看到"今日委托投递成功率 100%"。

### P1-3 `report.daily` 的 stale 语义

- **现状**：`report.daily` 默认 23:30 跑（`core/scheduler.py:99-102`），23:30 之后的新消息不会进当天总结；`report.backfill`（04:00）只补"failed / empty"，**不补"生成后又产生了新消息"**。卷八 §8.3 已把这个标为语义缺口。
- **改什么**：`daily_reports` 加一列 `generated_upto`（生成时的最大 message id，或直接用 `source_updated_at`）；backfill 的 `due_subjects` 增加一条判据：当天总结存在且 `generated_upto < 该日最后一条消息 id` → 标记为 stale 并重生成。
- **为什么省赛必须改**："我的总结少算了晚上聊的半小时"是用户能直接感知的质量问题，而"每日总结"是本项目对外的主打功能之一（卷十一）。评委随便问一句"23:30 之后聊的算不算今天"，现在答不上来。
- **代价/风险**：重生成要再花一次 LLM 调用（成本）；且总结只读是"可安全重跑"的前提（`service/daily.py` 第 4 条），改动不能破坏这条。
- **反转条件**：若产品口径改成"总结只覆盖到 23:30，此后算次日"，则改为在前端明示这个口径，不做重生成。
- **验收口径**：23:30 后再发一条消息，次日 04:00 backfill 后该日总结包含这条内容，且 `daily_reports` 只有一行（幂等重生成，不追加）。

### P2-1 cron 表达力与夏令时测试

- **现状**：`estimate_next_run` 只支持日/月/周为 `*`，否则返回 None（`core/scheduler.py:845-857`）——这是"宁可显示未知也不猜"的正确取舍；`day_bounds` 已用 `+timedelta(days=1)` 而非 `+86400` 防夏令时（`core/timeutil.py:94-105`）。
- **改什么**：补两组测试 —— ① `TZ=America/New_York` 下 `day_bounds` 跨夏令时切换日仍得到正确区间；② `TZ_STRICT=0` 的回退路径有断言保护。cron 表达式解析器**不扩**（7 个任务全是天天跑）。
- **为什么省赛必须改**：属于"讲出来加分"的严谨性项；且 `TZ_STRICT=0` 是个生产开关，没有测试守着它，是最容易被误触的开关。
- **代价/风险**：零（只加测试）。
- **反转条件**：无。
- **验收口径**：两个测试进 CI。

### P2-2 手动管理的界面化

- **现状**：`run-now` / `retry` 都是 HTTP API（`api/schedule.py:101-165`），需要 curl 或管理员自己拼请求。
- **改什么**：后台页面上有"立即执行""重排失败""改 cron""启停任务"四个按钮，全部调既有 API。
- **为什么省赛必须改**：只在"作品呈现"维度有加分，且依赖前端排期。**优先级最低**。
- **代价/风险**：改 cron 是高权限动作，必须保留"管理员校验"（`api/schedule.py:53-61` 已复用 `quota._is_admin`，不要重写一份）。
- **反转条件**：若演示视频里用命令行演示"改 cron 只改表不改代码"已经足够，本项可省。
- **验收口径**：页面上改一次 cron，`GET /api/schedule/jobs` 立刻反映，且 worker 重启后不被打回（`core/scheduler.py:238-242` 的 `ON CONFLICT DO NOTHING` 保证）。

---

## 4. 竞品对照

| 竞品 | 这一层怎么做 | 我们差在哪 | 跟不跟 |
|---|---|---|---|
| **n8n**（188k⭐，1核1G 即可跑） | 调度是它的**主业**：可视化工作流 + cron 节点 + 执行历史落库（可配保留天数）+ 失败重试 + 错误工作流分支 | 我们的 `job_runs` 保留 90 天、保留 failed，口径一致；差的是**执行历史的界面与聚合**（P1-2）——n8n 首页就是执行列表 | **跟 P1-2**（界面化），不跟架构。n8n 的"错误工作流"是给编排层用的，我们只有 7 个固定任务，不需要 |
| **Dify**（142k⭐，2核4G） | 定时任务能力弱，主要靠外部触发；但有完整的 LLMOps 追踪与 A/B 测试 | 我们反而**更强**：cron 存表、幂等键、心跳三态、重试分级，Dify 社区版都没有 | 不跟；这是我们的差异化点，要在答辩里讲 |
| **FastGPT**（26.1k⭐，2核4G） | 定时任务以"应用级定时触发"形式存在，商业版提供运行日志看板 | 同 Dify，弱于我们；但它的"运行日志看板"是我们要补的（P1-2） | 只跟看板 |
| **RAGFlow**（80.7k⭐，4核16G） | 调度围绕文档解析 pipeline（任务队列 + 进度条），不是通用 cron | 场景不同，不可比 | 不跟 |
| **Coze 扣子**（闭源 SaaS） | 定时触发器是平台能力，用户看不到也管不了 | 我们把"cron 存表、可改、可查"做成了显式能力 | 不跟；我们的可控性更强 |

**读法**：这一层是本项目**少有的、明显强于同档开源竞品**的一层。所以升级方向不是"补能力"，而是"把已有能力变得可见、可演示、可答辩"——P0 三条全部是这个性质。

---

## 5. 落地顺序与验收口径

| 序 | 项 | 依赖 | 产出 | 验收（可演示/可截图） |
|---|---|---|---|---|
| 1 | P0-3 连续失败告警接线 | 无（需顺带给 `job_runs` 补 `error_class` 列） | 3 个文件各几行 | 注入失败 3 次 → 指标 = 3，规则 pending |
| 2 | P0-1 单任务超时 + stale 扫描 | 无 | `JOB_TIMEOUT` 配置 + `stale_running()` + `/health` 字段 | 假 handler 睡 600s → 被置 failed；`stale_running_count` 归零 |
| 3 | P0-2 错过执行留痕 | 无 | `missed_window` 日志 + `missed_count` | 停 worker 2h → 日志与接口都能看到 |
| 4 | P1-1 多实例双进程测试 | 无 | 一个测试 + 一段文档 | CI 绿；文档说明"为什么不需要分布式锁" |
| 5 | P2-1 夏令时 / `TZ_STRICT` 测试 | 无 | 两个测试 | CI 绿 |
| 6 | P1-2 聚合接口 | 1 | `sched.summary()` + `GET /api/schedule/summary` | 7 个 job 的成功率表 |
| 7 | P1-3 总结 stale 重生成 | 无（要动 `service/daily.py`） | `generated_upto` 列 + backfill 判据 | 23:30 后发消息 → 次日补生成且只有一行 |
| 8 | P2-2 后台界面 | 6、前端排期 | 管理页 | 页面上改 cron 生效且重启不回退 |

**顺序理由**：P0 三条互不依赖、各自半天以内，按"性价比"排——P0-3 最便宜（几行配置）且直接回答"你怎么知道出问题"，所以第一。P1-3 要动 `service/daily.py`（与卷十一耦合），放后面。P2-2 依赖前端排期，最后。

**演示口径（省赛现场）**：三张图 = ① `/api/schedule/health` 心跳三态 + 7 个 job 的成功率；② 一次"kill worker → 自动拉起 → 补跑"的完整日志；③ 连续失败触发告警的 Prometheus 截图。

---

## 6. 不做的事（及为什么）

| 不做 | 为什么 |
|---|---|
| **上 Celery + Redis/RabbitMQ** | 卷八 §8.1 的否决理由今天仍然成立：broker 要 50~100MB 常驻，2G 机器上换"分布式任务能力"，而我们只有 1 台机器、7 个任务。且 Celery 的幂等还得自己实现，`job_runs` 那套会变成两套 |
| **分布式锁 / 选主（leader election）** | 只要保持单 worker（systemd unit 保证），就不需要。P1-1 的验证 + 文档足以回答"扩了怎么办"；真要扩副本，应该先过卷七的"换 PG"判据 |
| **迁到 K8s CronJob / 云函数定时触发器** | 裸机 + venv 是卷十 10.5 的决策（2G 内存），K8s 是本项目的反面 |
| **把调度塞回 web 进程（BackgroundScheduler）** | 卷八 §8.1 两个致命问题（重启丢任务、长回答拖住调度）今天一条都没变；`api/schedule.py:11-14` 明确写了"本文件不 import apscheduler"，这个边界要守住 |
| **支持完整 cron 语法（月/周/日历规则、`@monthly`、L/W/#）** | `estimate_next_run` 宁可返回 None 也不猜（`core/scheduler.py:839-840`），这是正确取舍；7 个任务全是"天天跑"，扩语法只增加解析器的出错面。真需要"每月 1 号"时再单独加 |
| **任务优先级队列 / 抢占** | 7 个任务已经用"cron 分钟错开"来避免抢锁（`delegate.retry` 用 `7,22,37,52` 错开 dispatch，`core/scheduler.py:95-98`），时间维度的调度比数据维度的排队更简单也更好讲 |
| **任务级权限（非管理员也能建定时任务）** | 定时任务是花钱的（卷三 3.10 的 `auto` 配额），开放给普通用户等于开放账单。保持管理员-only |
| **自动扩缩容 worker** | 只有一个进程、一台机器 |

---

## 附：本卷的"待核对"项

1. **`core/llm.py` 的非流式调用是否已有超时**——决定 P0-1 的第 1 步（真超时）是否必要，或只需做第 3 步（stale 扫描）。
2. **前端是否已有 `/schedule` 相关页面**——决定 P1-2 与 P2-2 的剩余工作量。
3. **生产机上 `systemd` 是否已启用**（`install-systemd.sh` 是否执行过）——`systemd/README.md:28-36` 提示 `deploy.sh` 与 systemd 会抢端口，若仍跑在 nohup 下，则 P0 三条的"自愈"前提不成立，需先迁 systemd。
4. **`job_runs.error_class` 未落库**——`finish_job` 只写 `error` 文本（`core/scheduler.py:512-516`），P0-3 的"按失败类别过滤告警"需要补一列，属于本卷新增的改动，需与卷七的 schema 版本化（P0-2）一起排。
