# 卷八 · 调度层：独立 worker 与时间纪律

> 本卷解决的问题：**让系统在没有人操作的时候也能做对事，并且在它没做、做错、做重的时候，你能知道。**
> 这一层的特殊性在于：**没有用户在场**。所有在交互式系统里"用户会报错"的兜底机制，在这里全部失效。

---

## 8.0 为什么"定时任务"是一个独立的架构问题

在交互式系统里，出错有一个隐形的兜底：**用户会重试、会抱怨、会发现**。

定时任务没有这个兜底：

| 交互式 | 定时 |
|---|---|
| 用户点了按钮，没反应 → 再点一次 | 到点没执行 → 没人知道 |
| 报错弹窗 → 用户反馈 | 报错写日志 → 没人看 |
| 重复执行 → 用户看到两条，会问 | 重复执行 → 用户第二天收到两条总结，可能以为是 bug，也可能以为是特性 |
| 花光配额 → 用户看到 429 | 花光配额 → 静默，直到账单 |

所以定时任务的每一个设计点，都必须回答："如果这一步错了，我怎么知道？"

本项目的答案是三件套：**幂等键、心跳、独立配额**。后面会逐个展开。

---

## 8.1 APScheduler vs Celery vs cron vs 同步执行

### 候选面

| 方案 | 依赖 | 能力 | 在本项目 |
|---|---|---|---|
| **APScheduler 3.x（BlockingScheduler）** | 1 个包，纯 Python | cron/interval/date 触发器、JobStore、时区 | ✅ 选 |
| **Celery** | broker（Redis/RabbitMQ）+ worker 进程 + result backend | 分布式任务、重试、路由、限流 | ✗ broker 要 50~100MB 常驻 |
| **系统 cron** | 无（OS 自带） | 按时执行命令 | ✗ 无法感知业务状态，无法做幂等判断 |
| **web 进程内的后台任务** | 无 | `asyncio.create_task` 或 `BackgroundTasks` | ✗ web 重启丢任务；长回答会拖住调度 |
| **自己写 while + sleep** | 无 | 完全可控 | 需要自己实现 cron 解析、时区、错过补偿 |

### 逐项否决理由

**否决 Celery：内存。**

Celery 的最小部署是 `broker + worker`。用 Redis 做 broker 是 50~100MB，用 RabbitMQ 是 100MB+。在 2G 机器上，这是 5% 的内存换"分布式任务能力"，而本项目只有 1 台机器、6 个任务。

**分布式任务的价值在"多机、高吞吐、任务异构"**——本项目一个都不满足。

**否决系统 cron：无法感知业务状态。**

系统 cron 只能做"到点执行一条命令"。它不知道：

- 这个任务今天是否已经执行过（幂等）；
- 上次失败是否需要重试（重试）；
- 当前是否配额触顶（降级）；
- 执行结果是什么（可观测）。

如果用 cron，这些全部要在脚本里实现，等于自己写一个调度器。而 `python -m worker.scheduler` 已经是"一个进程 + 一个调度器"，比 cron 调用脚本更可控。

**否决"web 进程内后台任务"：两个致命问题。**

1. **重启丢任务**：`asyncio.create_task` 的任务在进程退出时消失。web 进程会因为部署、OOM、`--reload` 而重启。
2. **长回答会拖住调度**：如果调度和 web 在同一个事件循环里，一次 Agent 回答（十几秒）期间，定时任务的触发会被延迟。

第二条在 2 核机器上尤其明显——`asyncio` 不是真并行，CPU 密集的操作（图编译、JSON 解析大响应）会阻塞事件循环。

**注释里有一处印证**：

```python
# 真正的执行在独立 worker 进程（python -m worker.scheduler），web 不 import apscheduler
```

`api/schedule.py` 的注释明确说"web 不 import apscheduler"——这是一个**物理隔离**的声明：web 端连调度库的 import 都不做，从根上排除"不小心在 web 里跑了调度"。

### 决策

APScheduler 3.x + `BlockingScheduler` + `SQLAlchemyJobStore(data/scheduler.db)`，独立进程。

### 代价

- **多一个进程**：`deploy.sh` 要管三个（backend / frontend / worker）。README 诚实地写了这一点："诚实的代价是：`deploy.sh` 要多管一个进程。"
- **多一份内存**：worker 进程本身约 60~100MB（Python + APScheduler + SQLAlchemy + LLM 客户端）。
- **版本锁**：`apscheduler>=3.10,<4`，因为"4.x 的 API 与 SQLAlchemyJobStore 的用法都变了"。

关于版本锁的注释值得一读：

```
# 版本卡在 3.x：4.x 的 API 与 SQLAlchemyJobStore 的用法都变了，升级要单独验一遍。
apscheduler>=3.10,<4
# sqlalchemy 是 SQLAlchemyJobStore 的硬依赖。本机恰好被其它包装上了，
# 但不显式声明的话，下一次「照 requirements 装一台干净机器」时 worker 会 import 失败。
sqlalchemy>=2.0
```

第二段是一个很实际的教训：**"本机碰巧能跑"不等于"依赖声明完整"**。这是"干净机器测试"的价值——只有从零安装才能发现缺失的声明。

卷一会把这归入"形态 1 版本锁"，但这里还多一层：**隐式依赖**（SQLAlchemy 被别的包带进来了，但没显式声明）。

---

## 8.2 状态自管：只让 APScheduler 管"什么时候"

### 核心设计（`core/scheduler.py:3-17`）

> APScheduler 负责「什么时候跑」，本模块负责「跑没跑过、失败算谁的、worker 还活着吗」。
> 两者刻意分开，三个理由：
>
> 1. **可测**：本文件只依赖标准库 + SQLite，**不 import apscheduler**。幂等、重试、心跳三件事全部由单元测试覆盖，不需要起调度器、不需要等真实 cron 到点。
> 2. **共享**：web 进程要读心跳判健康、要写「立即执行」的一行；worker 进程要执行并回写状态。两个进程必须看到同一份事实。
> 3. **幂等靠约束而不是靠查**：`job_runs.idem_key` 上建 UNIQUE 索引……

### 第一个理由值得展开：可测性

如果状态管理交给 APScheduler 的 JobStore：

- 测试一个幂等逻辑，需要起一个 APScheduler、注册一个 job、等到 cron 时间、检查结果。或者用 `job.modify(next_run_time=now)` 强制触发——但这是**框架内部 API**，不稳定。
- 测试一个重试逻辑，需要模拟"job 抛异常"，然后验证"下次执行时间被推迟"——这要读 JobStore 的内部结构。

而现在的设计：

```python
# 测试可以直接调
run_id, created = scheduler.register_run(job_key, subject_id, planned_at)
assert created is True
run_id2, created2 = scheduler.register_run(job_key, subject_id, planned_at)
assert created2 is False        # 第二次不创建
```

**纯函数 + SQLite，几十毫秒跑完，不需要任何 mock。**

**这是"可测性驱动架构"的范例**：因为想测试，所以把状态管理从框架里剥离出来。

### 第二个理由：双进程共享事实

web 和 worker 需要看到同一份状态：

| 操作 | 发起方 | 用途 |
|---|---|---|
| 写心跳 | worker | web 读它判健康 |
| 读心跳 | web | `GET /api/schedule/health` |
| 写"立即执行" | web | worker 扫描时执行 |
| 更新状态 | worker | web 展示执行历史 |

如果状态在 APScheduler 的内存 JobStore 里，web 进程根本看不到。所以必须落库，且**必须两个进程都能访问**（同一个文件）。

这与卷七的"两个 db 文件"是配套的：`scheduler.db` 是两个进程的共享面。

### 第三个理由：约束优于查询

见卷七 §7.8，不再重复。这里补一个细节：

```python
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
```

五个状态。注意没有 `"retrying"`——因为重试不是一个独立状态，而是 `failed` + `next_attempt_at`。

**这是一个"用字段组合表达状态"而非"用状态枚举表达一切"的设计。**

好处：查询简单（`WHERE status='failed' AND next_attempt_at <= now`）。
坏处：状态的语义分散在两个字段里，读代码时要同时看。

---

## 8.3 cron 存表：改周期不用改代码

### 设计

```sql
CREATE TABLE schedule_jobs (
    job_key     TEXT PRIMARY KEY,
    cron        TEXT NOT NULL,
    tz          TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    enabled     INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    next_run_at INTEGER
);
```

`job_defs()` 只提供默认值：

```python
def job_defs() -> dict[str, dict]:
    """任务定义表的默认值（job_key -> cron / 说明）。

    **只给默认值**：schedule_jobs 里已有的行不会被覆盖，
    所以运维改过 cron 之后重启 worker 不会被打回原样
    （「周期改这里，不改代码」是这张表存在的唯一理由）。
    """
```

### 这个设计的价值

想象一个场景：用户说"每日总结别在 23:30 生成，改到 22:00"。

- **代码里写死**：改 `config.py` → 提交 → 发版 → 重启 worker。10 分钟。
- **存表**：`UPDATE schedule_jobs SET cron='0 22 * * *' WHERE job_key='report.daily'`。1 秒。

更重要的是：**改配置不需要走代码评审**。运维可以自己改。

### "只给默认值"的语义

```python
# 插入时用 INSERT OR IGNORE（或 ON CONFLICT DO NOTHING）
# 已存在的行不动
```

这保证了：
- 首次部署 → 按默认值建表；
- 运维改过 → 重启不覆盖；
- 新增任务 → 加一行默认值，不影响已有的。

**这是一个"配置的版本管理"问题**：代码是"默认配置的来源"，数据库是"实际配置"。两者可能不一致，且**数据库优先**。

代价：如果代码里改了默认 cron（比如从 23:30 改成 22:30），已有部署不会生效——需要手动 UPDATE。这是一个**容易忘的运维步骤**。

更好的做法是：在代码里加一个"配置版本号"，worker 启动时比对，如果代码的默认值变了且用户没手动改过，就更新。但那增加复杂度。

**当前设计的判据：默认值很少变，所以"手动同步"的成本低于"自动合并"的复杂度。**

### 六个任务的 cron 设计

| job_key | cron | 为什么 |
|---|---|---|
| `delegate.dispatch` | `*/5 * * * *` | 每 5 分钟扫描一次到点的委托 |
| `delegate.retry` | `7,22,37,52 * * * *` | **错开 dispatch 的分钟**，避免抢 SQLite 写锁 |
| `report.daily` | `30 23 * * *` | 默认 23:30，留出当晚补记时间 |
| `report.backfill` | `0 4 * * *` | 凌晨 4 点补生成过去 7 天失败的总结 |
| `housekeeping` | `15 4 * * *` | 清理过期记录与上传件 |
| `profile.aggregate` | `30 2 * * *` | 凌晨 2:30 聚合用户画像 |

**第二行的设计值得注意**：

> "重试失败的委托投递（错开 dispatch 的分钟，避免抢 SQLite 写锁）"

如果 retry 也是 `*/5`，它会和 dispatch 在同一分钟触发。两个任务同时写 `job_runs`，会争 SQLite 的写锁（虽然有 `busy_timeout=5000`，但排队会让两个任务都变慢）。

用 `7,22,37,52`（偏移 7 分钟）让两者错开。

**这是一个"用调度时刻解决资源竞争"的技巧**——比加锁更简单，因为它在时间维度上避免了并发，而不是在数据维度上串行化。

**代价**：如果 dispatch 一次执行超过 7 分钟，仍会重叠。当前 dispatch 是"扫描 + 投递"，几十秒级，不会。

### `report.daily` 的 23:30 选择

```python
# 每晚 23:30：留出当晚补记的时间，次日跑到点时昨天的会话已经全部落盘。
```

这句话有点绕。准确理解：

- 总结的是"当天"（00:00~23:59）的对话；
- 在 23:30 生成，意味着最后 30 分钟的对话不会进总结；
- 为什么不留到 23:59？因为可能正好有用户在说话，且 23:59 到 00:00 只有 1 分钟，写入可能还没落盘。

而 `report.backfill`（凌晨 4 点）会补生成"failed / empty"的总结——如果 23:30 生成时数据不全，4 点会重试吗？大概不会（状态不是 failed），这是一个**语义缺口**。

**更严谨的设计**应该是：总结生成后，如果当天在 23:30 之后还有新消息，标记 `stale`，次日 backfill 重新生成。当前没有这个机制。

---

## 8.4 时区纪律：禁止裸 `datetime.now()`

### 规则（`core/scheduler.py:19-24`）

> * 落库一律 Unix 秒（UTC），与 sessions / messages 表一致；
> * 解释一律 `SCHEDULER_TZ`（默认 Asia/Shanghai），**禁止裸 `datetime.now()`**——带时区的 `datetime.now(TZ)` 是零成本的，而手工 +8 小时在跨时区迁移时没有任何防御力。

### 为什么这条纪律重要

Python 的 `datetime.now()` 返回**本地时间**（naive，无时区信息）。`datetime.now(ZoneInfo("Asia/Shanghai"))` 返回**带时区的时间**（aware）。

两者看起来一样（如果机器时区是对的），但：

| 场景 | naive | aware |
|---|---|---|
| 机器 TZ=UTC | 得到 UTC 时间，但代码以为是北京时间 → **错 8 小时** | 正确 |
| 夏令时切换 | 可能重复/跳过一小时 | 正确处理 |
| 存入数据库 | 无法区分时区 | 可以转换 |
| 跨机器迁移 | 依赖机器配置 | 不依赖 |

**"容器镜像的 TZ 常常是 UTC"** —— 这是注释里点的名。Docker 默认 `TZ=UTC`，很多"本地跑得好，部署就错 8 小时"的 bug 都是这个原因。

### 本项目的做法

1. **落库用 Unix 秒**（`int(time.time())`）。Unix 秒是绝对时间，与时区无关。
2. **解释用 `ZoneInfo`**：

```python
TZ_NAME = getattr(config, "SCHEDULER_TZ", "Asia/Shanghai") or "Asia/Shanghai"
TZ = ZoneInfo(TZ_NAME)
```

3. **cron 的 tz 字段**存在 `schedule_jobs.tz`，APScheduler 按它解释 cron。

### 一个容易漏的地方：`quota.day_key`

```python
def day_key(ts: int | None = None) -> str:
    """当天日期键，形如 2026-10-06（本地时区）。

    这里用本地时间而非 UTC：限额是给人看的，按服务器当地日子解释最好讲清楚。
    """
    return time.strftime("%Y-%m-%d", time.localtime(ts if ts is not None else time.time()))
```

`time.localtime()` 用的是**系统时区**，不是 `SCHEDULER_TZ`。

也就是说：如果服务器 TZ 是 UTC，`day_key` 会在 UTC 00:00 切换，对应北京时间 08:00。用户会发现"早上 8 点额度重置了"——这不符合"按自然日"的直觉。

**这与 §8.4 的纪律冲突**：调度用 `ZoneInfo`，配额用 `localtime`。

**评估**：这是一个真实的不一致。如果部署时设了 `TZ=Asia/Shanghai`（`deploy.sh` 或 systemd 里可能设了），两者一致。否则不一致。

**建议**：`day_key` 也应该用 `TZ`（`datetime.now(TZ).strftime(...)`）。这是一个 3 行的修改，但会影响"额度重置时刻"，需要产品确认。

### `_day_bounds`（总结用）

```python
def _day_bounds(date: str) -> tuple[int, int]:
    """本地时区的当天 [start, end) unix 秒。与 core.quota.day_key 的「本地日子」口径一致。"""
    t = time.strptime(date, "%Y-%m-%d")
    start = int(time.mktime(t))
    return start, start + 86400
```

`time.mktime` 也是系统时区。所以总结的"当天"与配额的"当天"一致（都用 `localtime`），但与调度的"时区"可能不一致。

**三个地方，两种时区语义**：调度（`ZoneInfo`）、配额（`localtime`）、总结（`localtime`）。

这是一个**口径分裂**（卷一的形态 5）。当前不出问题，是因为部署时大概设了 `TZ`。但它是一个"依赖环境配置才能正确"的设计。

---

## 8.5 重试分级：三种失败，三种处理

### 三个类别

```python
CLASS_RETRY = "retry"   # 可重试：超时 / 5xx / 429
CLASS_FATAL = "fatal"   # 不可重试：401 / 402、会话已删、库不存在
CLASS_QUOTA = "quota"   # 配额触顶：延后到次日同刻
```

### 三种策略

| 类别 | 退避 | 最大次数 | 理由 |
|---|---|---|---|
| `retry` | 1 / 5 / 15 分钟（指数） | 3 | 临时故障，等一会儿会好 |
| `fatal` | 不重试 | 0 | 重试一万次也一样 |
| `quota` | **86400 秒（次日同刻）** | —— | 按分钟退避毫无意义 |

### `quota` 的推理链最值得学

```python
# 配额触顶不按分钟退避（今天已经没额度了，15 分钟后再试还是超限），延后到次日同刻
QUOTA_DEFER_SECONDS = 86400
```

如果配额触顶也用 1/5/15 分钟退避：

- 第 1 次失败 → 1 分钟后重试 → 还是超限 → 第 2 次失败；
- 5 分钟后 → 还是超限 → 第 3 次失败；
- 15 分钟后 → 还是超限 → 放弃。

**结果：3 次无用功 + 21 分钟的延迟 + 3 条失败日志。** 而正确答案是"明天再说"。

所以 `CLASS_QUOTA` 直接把 `next_attempt_at` 设成"次日同一时刻"。

**这是一个"失败语义决定退避策略"的例子**：不是所有失败都适合指数退避。指数退避假设"时间会解决问题"，而配额触顶是"时间不会解决问题，除非跨过日界"。

### 与 HTTP 层重试的关系（呼应卷四）

`core/llm.py` 的 `max_retries=2` 是 HTTP 层。这里是业务层。两层叠加最坏是 3 × 3 = 9 次。

但 `fatal` 和 `quota` 类别会在业务层直接终止，不会再触发 HTTP 重试（因为根本不发起请求）。

**分层重试的正确设计**：内层（HTTP）处理"这一次请求的网络抖动"，外层（业务）处理"这个任务整体的失败"。两层的判据不同，不该合并。

---

## 8.6 心跳三态：为什么不用进程表

### 实现

```sql
CREATE TABLE worker_heartbeat (
    id      INTEGER PRIMARY KEY CHECK (id = 1),   -- 只有一行，恒等于 1
    ts      INTEGER NOT NULL,
    pid     INTEGER,
    version TEXT
);
```

`CHECK (id = 1)` 保证只有一行。这是一种"用约束保证单例"的技巧——比"每次 UPDATE WHERE id=1"更可靠（不可能出现两行）。

### 三态判定

```python
HEARTBEAT_DOWN_SECONDS = 600     # > 10 分钟判 down
HEARTBEAT_STALL_SECONDS = 300    # > 5 分钟判 stalled
```

| 心跳年龄 | 状态 | 含义 |
|---|---|---|
| < 300s | `up` | 正常 |
| 300~600s | `stalled` | 黄灯：可能卡住了 |
| > 600s | `down` | 红灯：worker 死了或卡死 |

### 为什么不用 `ps aux | grep worker`

注释（`core/scheduler.py:50-52`）：

> 600s = 两个 dispatch 周期：进程在但卡死（比如被一次 LLM 调用挂住）同样会被抓到，**这就是不走进程表的原因**。

展开：进程存在 ≠ 系统在工作。

`ps` 能看到进程，但看不到：
- 它是否在正常循环（还是卡在某个 `await` 上）；
- 它的调度队列是否在推进；
- 它是否因为异常进入了死循环。

而心跳是**主动写入**的：worker 每个 dispatch 周期（5 分钟）更新一次 `ts`。如果它卡住了，`ts` 不再更新，年龄增长 → 报警。

**"主动心跳"比"被动检查"更能反映健康状态**，因为心跳是"我从内部报告我还活着"，而进程表是"外部看到有个进程"。

### 三态而非二态

为什么不直接 `up/down`？

因为运维需要**提前量**：
- `stalled`（5 分钟无心跳）：可能是"这次 LLM 调用特别慢"，也可能"要卡死了"；
- `down`（10 分钟）：基本确定有问题。

黄灯让运维有机会在红灯之前看一眼。

**这是一个"告警分级"的设计**，在监控系统里是标准做法，但自己实现心跳时容易只做二态。

### 一个细节：`version` 字段

```python
WORKER_VERSION = "0.1.0"
```

心跳里带版本号。用途：确认"跑的是哪个版本的 worker"。

在部署后，如果心跳里的 version 还是旧的，说明新 worker 没起来（或者旧 worker 还在跑）。

**这是一个 3 个字段的成本，换来"部署是否生效"的可观测性。**

---

## 8.7 `BlockingScheduler` 的选型细节

### 三种 Scheduler

| 类 | 用途 | 何时用 |
|---|---|---|
| `BackgroundScheduler` | 在已有事件循环/线程里后台跑 | web 进程内 |
| `BlockingScheduler` | 主线程阻塞，专用于调度 | **独立进程** |
| `AsyncIOScheduler` | 在 asyncio 事件循环里跑 | async 应用内 |

本项目用 `BlockingScheduler`，因为 worker 是**独立进程**，它的唯一职责就是调度。

**如果放在 web 里，应该用 `AsyncIOScheduler`**（否则 `BlockingScheduler` 会阻塞 uvicorn）。

### JobStore 的选择

```python
SQLAlchemyJobStore(url="sqlite:///data/scheduler.db")
```

为什么用 SQLAlchemyJobStore 而不是 `MemoryJobStore`？

因为**任务定义要持久化**：worker 重启后，APScheduler 要能恢复"有哪些任务、上次跑到哪"。

如果用 `MemoryJobStore`，重启后所有任务丢失，需要重新 `add_job`。虽然 `job_defs()` 可以在启动时重新注册，但**动态添加的任务**（如果将来支持）会丢。

**但注意**：本项目实际是"启动时按 `job_defs()` + `schedule_jobs` 表重新注册所有任务"，所以 JobStore 的主要作用是存 `next_run_at`（上次执行到哪），而不是存任务定义。

这是一个**双重持久化**：

- `schedule_jobs` 表（本项目自己的）：cron 定义 + 启用状态；
- `apscheduler_jobs` 表（APScheduler 的）：任务的下次执行时间。

两者可能不一致（比如手动 UPDATE 了 cron，但 APScheduler 的 next_run_at 还是按旧 cron 算的）。

**这是一个潜在的坑**：改 cron 后需要重载 worker（或调用 `reschedule`）。

---

## 8.8 fire-and-forget：`_BG_TASKS` 与强引用

### 场景

回答结束后要做三件后台事：

1. 生成标题（其实不是后台，是并行 task）；
2. 抽取用户画像标签；
3. 判定是否压缩上下文。

这三件都"不该阻塞 done 帧"。

### 实现

```python
def _schedule_compress(uid: str, sid: str) -> None:
    if not getattr(config, "COMPRESS_ENABLED", True):
        return
    try:
        _t = asyncio.create_task(_compress_after_turn(uid, sid))
    except RuntimeError:
        return          # 没有运行中的事件循环
    _BG_TASKS.add(_t)
    _t.add_done_callback(_BG_TASKS.discard)
```

### 为什么需要 `_BG_TASKS` 集合

```python
# 后台任务强引用：任务完成前被 GC 会报 "Task was destroyed but it is pending"。
# 完成后自动移除，避免集合无限增长。
_BG_TASKS: set = set()
```

**这是 asyncio 的一个经典陷阱**：`asyncio.create_task` 返回的 Task 对象如果没有强引用，可能被 GC 回收，导致任务永远不执行（并打印警告）。

事件循环只持有**弱引用**。所以必须自己保存。

**这不是"资源泄漏"的预防，是"任务被静默取消"的预防。** 而"静默取消"意味着：画像没抽、上下文没压缩，且没有任何错误——又一个静默失败。

### `add_done_callback(_BG_TASKS.discard)`

任务完成后从集合移除，避免集合无限增长（每个请求一个 task，一天 60 次 × 100 用户 = 6000 个引用）。

**这是一个"引用生命周期管理"的标准写法**：创建时加，完成时删。

### `except RuntimeError` 的兜底

```python
except RuntimeError:
    # 没有运行中的事件循环（同步调用场景，如单测直接调 handler）：压缩不是必需动作，直接跳过
    return
```

`asyncio.create_task` 在没有运行中的 loop 时会抛 `RuntimeError: no running event loop`。

这个兜底是为了**测试友好**：单测可以直接调 `chat_stream` 的 handler 而不起事件循环。

**这是一个"生产代码为测试让步"的例子**。代价是：如果真的在生产环境出现"没有 loop"（不可能，但假设），压缩会静默跳过。

**评估**：可接受。因为压缩是优化项，跳过不影响正确性。

---

## 8.9 `asyncio.to_thread` 纪律

### 规则（`core/quota.py:16-18`）

> 调用注意：本文件的函数全是同步的（SQLite 写约 2ms），在 async 端点里一律用 `asyncio.to_thread(...)` 包一层——这是本项目既定规则，**别因为它只有 2ms 就裸调**。

### 为什么 2ms 也要包

单次 2ms 看起来无害。但：

- 一次问答有 6~7 次 SQLite 操作 → 14ms；
- 3 个并发请求 → 42ms 的事件循环阻塞；
- 加上 embedding（250ms）、Chroma 检索（10~50ms）、图编译（10~50ms）→ 累计可能 300ms+。

**事件循环阻塞的后果不是"慢"，是"所有并发请求都慢"**——因为单进程只有一个事件循环。

在 2 核机器上，用户能明显感觉到"别人在用的时候我这边卡"。

### 执行的严格程度

检查 `api/chat.py`：

```python
conv = await asyncio.to_thread(store.get_conversation, uid, req.sid)     # ✅
await asyncio.to_thread(quota.consume, uid, "ask")                       # ✅
kb_exists = bool(kb_id and await asyncio.to_thread(pipelines.exists, uid, kb_id))   # ✅
await asyncio.to_thread(store.add_message, uid, req.sid, "user", req.question)      # ✅
```

但有一处例外：

```python
# RAG 分支
history = store.get_messages(uid, req.sid, include_system=False)[:-1]    # ❌ 裸调
```

这是一个**违反纪律的地方**。它在 `event_generator` 内部（已经是 async 生成器），裸调会阻塞事件循环。

**为什么没被发现**：因为 `get_messages` 在 30 轮窗口下只读 70 行，约 1~2ms。用户感知不到。

**但这正是一个"2ms 也要包"的规则被违反的例子。** 说明规则的执行依赖人工审查，没有自动化检查。

**改进方向**：可以用 lint 规则（禁止在 `async def` 里直接调用已知的同步模块函数），或者用 `asyncio.to_thread` 的统一包装层。

### `规范/01` 里记录的同类缺口

```
| 1 | `api/report.py` 两处同步调用未包 `to_thread` | `api/report.py` | C 线 | **高**（违反既定规则） |
```

这条被标为"高"，说明团队认识到这个问题。但**"违反规则"这件事本身需要靠人工发现**，说明规则没有被工具化。

**这是一个"纪律型架构"的固有脆弱性**：靠人记住，就会漏。

---

## 8.10 每日委托与总结：手动和定时走同一条代码路径

### 设计声明（`main.py:94-97`）

> 每日总结与每日委托：业务能力全在 `service/daily.py`，
> worker 定时触发走的是同一套 service 函数，**手动与定时不产生第二条代码路径**。

### 为什么这很重要

如果手动和定时是两条路径：

- 手动路径可能有 bug，定时路径没有（或反之）；
- 修一个 bug 要改两处；
- 测试要覆盖两遍。

而统一到 `service/daily.py` 后：

- 一条路径，测一次；
- 手动的结果就是定时的结果（用户可以先手动试，再设定时）。

**这是一个"消除双轨"的正确做法**，成本是 `service/daily.py` 必须是纯函数（不依赖 web 上下文）。

### 纯函数化

```python
"""每日委托与每日总结的纯函数核心。

1. **LLM 可注入**：所有会调模型的函数都把 `llm` 作为可选参数，默认走
   `core.llm.get_llm()`（DeepSeek）。测试注入假 LLM（`.invoke()` 返回带 `.content`
   的对象即可），不依赖真实 API Key。
2. **统计确定、不走模型**：`collect_stats` 只做 SQL 聚合，可被直接 SQL 交叉验证。
3. **溯源强制**：……每个 ref 都能指回当天该用户真实存在的 message id；
   指不回去的结论**直接丢弃并记日志**。
4. **总结只读**：不写会话、不改标题、不删任何东西。
5. **复用同一 SQLite**：新表走 core.session 的同一份连接，不另起连接管理。
"""
```

五条里有三条是"可测性/正确性"的设计：

- **LLM 注入** → 测试不需要 key；
- **统计确定** → 可以 SQL 验证；
- **总结只读** → 可以安全重跑。

**第 4 条（只读）是整个设计的关键**：如果总结会修改会话数据，就无法"重跑"（第二次跑会基于被改过的数据）。只读意味着幂等。

### 与 idem_key 的关系

只读 + idem_key = **可以安全地补生成**。

`report.backfill` 会重跑过去 7 天失败的总结。如果总结有副作用，补生成会污染数据。

**所以"只读"不是一个附加约束，是"补生成"这个功能的前提。**

---

## 8.11 监控与探活

### 三层

| 层 | 手段 | 能发现什么 |
|---|---|---|
| **应用自证** | `GET /api/schedule/health` | worker 心跳三态 |
| **外部探针** | `monitoring/probe-health.sh` 写 textfile 指标 | 组件挂、主机失联、磁盘 |
| **指标存储** | Prometheus + node-exporter | 趋势、告警规则 |

### `probe-health.sh` 的设计意图

```python
# monitoring/：prometheus+node-exporter（mem_limit 256m）、3 条告警
# （组件挂/主机失联/磁盘<10%）、probe-health.sh 写 textfile 指标
# （能抓 worker 静默死亡）、外部 uptime 需用户账号。
```

**"能抓 worker 静默死亡"** —— 这是关键。

worker 死了，但：
- 它不监听端口，所以 HTTP 探针探不到；
- 它的进程可能被重新拉起（systemd `Restart=always`），但业务上可能没在工作。

所以探针要查**业务状态**：读 `worker_heartbeat.ts`，算年龄，写成一个 Prometheus 指标。

```
# TYPE agent_hub_worker_heartbeat_age_seconds gauge
agent_hub_worker_heartbeat_age_seconds 42
```

告警规则：`age > 600` → 报警。

**这是"业务指标"而不是"进程指标"的监控。** 进程活着但业务卡住，也能抓到。

### 三条告警的取舍

| 告警 | 阈值 | 理由 |
|---|---|---|
| 组件挂 | up == 0 持续 N 分钟 | 基本可用性 |
| 主机失联 | node-exporter 无数据 | 机器挂了 |
| 磁盘 < 10% | `node_filesystem_avail_bytes / size < 0.1` | 磁盘满了 SQLite 会写失败 |

**为什么只有三条**：因为告警太多会疲劳。三条覆盖了"服务不可用 / 机器不可用 / 将要不可用"。

**缺少的告警**：
- 错误率（5xx 比例）；
- LLM 调用失败率；
- 配额触顶比例（如果很多人触顶，说明额度设低了）。

这些需要应用层埋点（Prometheus client + 自定义指标），当前没有。是一个缺口。

---

## 8.12 调度层的整体评价

### 一条核心主线

这一层的所有设计都在回答一个问题：**"无人值守时，怎么知道它做对了？"**

答案是一个三角：

```
        幂等（做不重）
         /        \
        /          \
   心跳（活着）—— 配额（花得起）
```

三者缺一不可：

- 只有幂等，没有心跳：任务不重复，但可能三天没跑，没人知道；
- 只有心跳，没有幂等：worker 活着，但每次都发两条总结；
- 只有配额，没有幂等：钱没白花，但用户收到重复消息。

### 三个已知缺口

1. **`quota.day_key` 用 `localtime` 而非 `SCHEDULER_TZ`**（§8.4 的口径分裂）。
2. **cron 改表后不重载 worker，APScheduler 的 `next_run_at` 可能仍是旧值**。
3. **没有"总结生成后又有新消息"的补生成触发**（§8.3）。

### 一条可迁移的经验

`core/scheduler.py` 开头的三句话，是这一层最好的总结：

> APScheduler 负责「什么时候跑」，本模块负责「跑没跑过、失败算谁的、worker 还活着吗」。

**"框架负责什么、我负责什么"的边界划得越清楚，系统越可控。**

因为框架的黑盒部分越少，出问题时你能查的地方就越多。而 APScheduler 的黑盒（cron 解析、job 状态机）恰好是"不需要你管"的部分，本模块负责的（幂等、重试、心跳）恰好是"业务特有、框架管不了"的部分。

**这是一个正确的分层：把通用能力交给框架，把业务语义留给自己。**

下一卷进入上下文工程。调度层解决"什么时候做"，上下文层解决"做的时候还记得多少"——这是长对话里最容易被忽视、也最容易烧钱的地方。
