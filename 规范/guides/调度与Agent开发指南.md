# 调度与 Agent 开发指南（C 线 · 刘佳林）

> **读者**：负责 **Agent 编排 / 每日委托 / 每日总结 / 定时任务** 的开发者。
> **归属人**：刘佳林（GitHub 账号 `huashuotianxuan` —— 已于 2026-10-13 确认）。
>
> **本文定位**：`规范/01`～`04` 是团队级规范；本文是你这条线的实操指南：模块边界、新增结构、健康判定口径、必须补的技术缺口。
>
> ⚠️ **先把住的事实**：队友成果目前**只在 `origin/dev` 与 `origin/feat/ai-module-20261013`，尚未合入 `main`**。
> 2026-10-13 实测，本地**不存在** `service/`、`worker/`、`core/scheduler.py`、`core/tool_registry.py`、
> `api/agent.py`、`api/schedule.py`、`api/report.py`、`api/delegate.py`。
> 本文涉及这些文件的事实**一律以 `docs/团队分支分析_20261013.md` 的分支实况为依据**，并逐条标注「已在队友分支实现、待合入」。
> **别去本地找这些文件 —— 找不到是正常的，不是环境坏了。**

---

## 1. 模块边界（你负责什么）

（`01_架构与模块职责.md` §2 为唯一权威）

| 层 | 路径 | 状态 | 说明 |
| --- | --- | --- | --- |
| Agent 编排 | `agent/**` | **本仓已有** | `builder.py` 编排与图缓存（197 行）、`tools.py` 工具集（524 行） |
| 业务纯函数 | `service/**`，核心 `service/daily.py` | 队友分支（待合入） | 约 650 行：每日总结生成 + 溯源过滤 + 每日委托执行 |
| 独立调度进程 | `worker/**`，核心 `worker/scheduler.py` | 队友分支（待合入） | 约 451 行：**独立 APScheduler 进程**，五 job |
| 调度库层 | `core/scheduler.py` | 队友分支（待合入） | +853 行：三张表 + 幂等键 + 重试分级 + 心跳 |
| 工具注册表 | `core/tool_registry.py` | 队友分支（待合入） | +299 行：`TOOL_META` 七字段 + 延迟发现状态机 |
| 接口层 | `api/agent.py`、`api/schedule.py`、`api/report.py`、`api/delegate.py` | 队友分支（待合入） | 四个新路由，共 17 个端点 |

**不在你范围内**（改了要走 `01` §6 跨模块流程）：`kb/**`、`core/serper.py` → 林宏亮（B 线）；
`frontend/**`、`core/**`（除上述两个文件）、`main.py`、`deploy.sh`、`.github/**` → 申请人（A 线）。

**交叉区规则**：

| 文件 | 规则 |
| --- | --- |
| `api/*.py` | **文件名即归属**，`api/report.py` 归你 |
| `main.py` | 新增挂载点必须改这里，但归申请人 —— **改前先通知** |
| `core/config.py` | 新增配置项（如 `DAILY_AUTO_LIMIT`）**必须先通知申请人** |
| `core/session.py` | 改表结构必须同步 `02_核心数据结构定义.md` |

> 调度三张表在**独立的 `data/scheduler.db`**，所以改调度结构**不需要**动 `core/session.py` —— 别合并。

---

## 2. 新增的系统结构（两个新包 + 三块 core）

### 2.1 `service/daily.py`（约 650 行，待合入）

**定位**：业务纯函数核心 —— 每日总结生成 + 溯源过滤 + 每日委托执行。

**设计要点：LLM 可注入，便于测试。**

```python
def generate_daily_report(..., llm=None):
    llm = llm or get_llm(...)   # 测试注入 fake，不烧 token、不连网
```

> **这条要守住**：`03_流程规范.md` §4.4 明确**禁止在 CI 里跑需要真实 API Key 的用例**。
> 纯函数 + 可注入 LLM 是让这条链路能进离线测试的**唯一前提**。写死 `get_llm()` 就永远进不了 CI。

### 2.2 `worker/scheduler.py`（约 451 行，独立进程，待合入）

**本模块最需要建立的认知：`worker` 是独立进程，不是后端进程里的定时器。**

```bash
python -m worker.scheduler        # 常驻进程，APScheduler BlockingScheduler
```

| 属性 | 值 |
| --- | --- |
| 进程形态 | 独立常驻进程，**不监听端口、不接受外部请求** |
| 时区 | `APSCHEDULER_TIMEZONE=Asia/Shanghai` |
| jobstore | `WORKER_JOBSTORE=sqlite`（`data/scheduler.db`，与 web 共用 `data/` 目录） |
| 与 web | **彻底隔离** —— 总结生成耗时 20～40 秒，绝不能拖慢在线问答 |

**五个 job**（cron 全按 Asia/Shanghai 解释）：

| `job_key` | cron | 含义 |
| --- | --- | --- |
| `delegate.dispatch` | `*/5 * * * *` | 每 5 分钟扫一次到点委托 |
| `delegate.retry` | `7,22,37,52 * * * *` | 每 15 分钟重试失败委托 |
| `report.daily` | `30 23 * * *` | 每日 23:30 生成当日总结 |
| `report.backfill` | `0 4 * * *` | 每日 04:00 补生成过去 7 天 `failed`/`empty` 的总结 |
| `housekeeping` | `15 4 * * *` | 清理过期运行记录（>90 天）与历史上传件 |

- **`delegate.retry` 为什么错开到 7/22/37/52 分**：SQLite 在 WAL 模式下写锁是**单写者**，dispatcher 与 retry 同一分钟跑会互相等锁。错开即可，**不需要消息队列**。
- **`report.daily` 为什么是次日 23:30**：当天最后一刻用户还在写；次日生成时昨天的会话已全部落盘。用户当晚 23:40 才聊完，可手动 `POST /api/report/generate` 补生成。
- **`dispatch` 5 分钟粒度的代价**：投递时间有 **±5 分钟抖动**，**必须写进产品文档** —— 不能让用户以为 07:00 就是 07:00:00。

**⚠️ 部署影响（`deploy.sh` 必须同步改）**

`deploy.sh` 目前只起**两个**进程（`uvicorn` + `npm run start`），合入后要起**第三个**：`python -m worker.scheduler`。
随之而来三件事，缺一件就是静默故障：

1. **多一条日志要轮转**（生产 `/tmp/baomi-*.log`、staging `/tmp/baomi-staging-*.log` 各再多一条）
2. **多一处进程存活检查**（现有 `pgrep -af` 要加上 worker）
3. **多一个「worker 挂了但 web 正常」的故障要监控** —— 最危险的一种：接口全 200，用户只是「今天没收到提醒」，**没人知道出了事**。判定手段见 §4。

> ⚠️ `deploy.sh` 归申请人（A 线）。你负责**提出需求并写清后果**，不要直接改。

---

## 3. 调度库层 `core/scheduler.py`（+853 行，待合入）

**职责**：三张表 + 幂等键 + 重试分级 + 心跳。

### 3.1 三张表

| 表 | 用途 | 对应端点 |
| --- | --- | --- |
| `schedule_jobs`（任务定义表） | job 定义（`job_key`/`cron`/`enabled`/`next_run_at`） | `GET /api/schedule/jobs` |
| `job_runs`（执行历史表） | 每次执行记录（游标分页） | `GET /api/schedule/runs` |
| `worker_heartbeat`（心跳表） | worker 存活心跳 | `GET /api/schedule/health` |

> **周期落库而不写死在代码里**：改周期不用改代码、不用重新发版，且「下次什么时候跑」对运维可见。

### 3.2 ⚠️ `job_runs` 在 `data/scheduler.db`，**不在** `data/sessions.db`

**刻意的隔离，别合并**（`02` §1 原文）。

| 存储 | 位置 | 归属 |
| --- | --- | --- |
| 会话 / 消息 / 反馈 / 用户 | `data/sessions.db` | 申请人 |
| **调度三张表** | **`data/scheduler.db`** | **刘佳林** |

**为什么单独强调**：备份脚本里两库是分开的（`04` §3.4 的 `cp data/scheduler.db ... 2>/dev/null || true`）。
把 `job_runs` 并进 `sessions.db` 会同时打坏三件事：备份策略、`_db_path` 的测试隔离、两库各自的 WAL 写入模型。

### 3.3 幂等键：`(job_key, subject_id, planned_at)` 三元组

```sql
CREATE TABLE job_runs (
  run_id      TEXT PRIMARY KEY,
  job_key     TEXT NOT NULL,        -- delegate.dispatch / report.daily ...
  subject_id  TEXT NOT NULL,        -- delegation_id 或 date
  planned_at  INTEGER NOT NULL,     -- 计划触发时刻（UTC 秒）
  idem_key    TEXT NOT NULL,        -- job_key|subject_id|planned_at
  status      TEXT NOT NULL,        -- pending/running/succeeded/failed/skipped
  attempts    INTEGER NOT NULL DEFAULT 0,
  started_at  INTEGER, finished_at INTEGER,
  error       TEXT,
  tokens_out  INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX ux_job_runs_idem ON job_runs(idem_key);
```

**`planned_at` 为什么必须在键里**：同一委托「今天 07:00」与「明天 07:00」是两次**不同**投递，都要发；
但「今天 07:00 这一次」无论被触发几次（worker 重启、重复扫描、手动重跑）**只能发一次**。

**执行方式**：先 `INSERT ... ON CONFLICT(idem_key) DO NOTHING`，影响行数为 0 就跳过。

> **用数据库唯一约束做幂等，而不是「查一下有没有再插」** —— 后者并发下有竞态。
> 与 `02` §5 反馈表的写法同源（那里也明确禁止「查有没有再决定插入还是更新」）。

### 3.4 重试分级（**四类，不能一刀切**）

| 失败类型 | 判定 | 重试策略 |
| --- | --- | --- |
| 大模型超时 / 5xx / 429 | 可重试 | 最多 **3 次**，间隔 **1/5/15 分钟**（指数退避），`attempts` 落库 |
| 大模型 **401/402**（额度耗尽） | **不可重试** | 直接 `failed` + 告警（继续重试只会继续烧钱） |
| 会话已删除 / 知识库不存在 | **不可重试** | `failed` + 委托 `enabled` 置 **false**（依赖已失效） |
| 配额已达上限 | 可重试 | **延后到次日同刻**（不是 15 分钟后 —— 今天已没额度） |

- **「超限时不扣」的沿用**：`core/quota.py` 的 `consume()` 触顶时不记账，本模块沿用 —— 否则重试会把计数越推越高，用户第二天零点也解不开。
- **投递幂等**：写回会话时用 `job_runs.run_id` 作消息外部标记；重试成功两次，会话里也**只出现一条**。

### 3.5 心跳

worker 每轮把心跳写入 `worker_heartbeat` 表。**这是健康判定的唯一依据**，见下节。

---

## 4. ⚠️ 健康判定口径（重要）：看心跳年龄，不看进程表

### 4.1 判定表

| 心跳年龄 `heartbeat_age_seconds` | 判定 |
| --- | --- |
| ≤ 300s | `up` |
| **> 300s** | **`stalled`** |
| **> 600s** | **`down`** |

### 4.2 为什么必须用心跳而不是 `pgrep`

**因为 `worker` 是独立进程，进程存在 ≠ 在正常跑。** 三种 `pgrep` 看不出来的故障：

1. **卡死**：进程还在，但 job 因数据库锁 / 网络挂起卡住，实际一个 job 都没跑。
2. **jobstore 失效**：进程活着，但调度器没读到 `schedule_jobs`，静默地什么都不做。
3. **静默失败**：每个 job 都在跑但每次都失败，接口全 200，用户「今天没收到提醒」。

`pgrep` 对这三种情况**全部返回「正常」**。而心跳是一个「多久没动静」的**绝对量**，天然覆盖「进程在但卡死」。

> 该口径已进验收清单：`04` §5.5 要求 staging 部署后检查「`/api/schedule/health` 心跳年龄正常（< 300s）」；
> `04` §7 故障排查表专门列了「调度状态误报正常 → 只看进程表，没看心跳年龄」。

### 4.3 `/api/schedule/health` 返回形状

```jsonc
{
  "worker": { "status": "up", "heartbeat_age_seconds": 43, "pid": 21488, "version": "0.1.0" },
  "jobs": [
    { "job_key": "delegate.dispatch", "cron": "*/5 * * * *", "enabled": true,
      "last_status": "succeeded", "last_run_at": 1760310900,
      "consecutive_failures": 0, "next_run_at": 1760311200 }
  ]
}
```

`consecutive_failures >= 3` 是**外发告警**阈值。告警分三层，第四层留待院赛：

| 层 | 手段 | 20 号前 |
| --- | --- | --- |
| 1. 落库可见 | `job_runs.status/error`，委托与总结列表显示「上次投递失败 + 原因」 | 是 |
| 2. 结构化日志 | 每轮写一行 JSON 日志（`job_key`/`run_id`/耗时/结果/错误） | 是 |
| 3. 健康端点 | `GET /api/schedule/health` | 是 |
| 4. 外发通知 | 连续 3 次失败 → 邮件 / Webhook | 院赛 |

**用户侧与运维侧告警分开**：用户只关心「我的委托没送到」，委托列表直接展示 `last_run.status` 与错误摘要；**不要把内部错误码抛给用户**。

### 4.4 时区红线：**禁止裸 `datetime.now()`**

```python
datetime.now()                            # ❌ 禁止
datetime.now(ZoneInfo("Asia/Shanghai"))   # ✅ 必须
```

**理由不是洁癖**：服务器 `TZ` 一旦是 UTC（容器镜像默认常常就是 UTC），所有「早上七点」会整体偏移 8 小时，**而且不会报错，只会安静地错**。

- **存储一律 UTC（Unix 秒），解释一律 Asia/Shanghai，展示一律带时区标注。**
- 中国大陆自 1991 年起无夏令时，Asia/Shanghai 全年 UTC+8，无 DST 跳变；但仍必须用 `ZoneInfo`，**不要手工 `+8`**。
- **跨日边界**：`date=2026-10-12` 覆盖 `[2026-10-12 00:00, 2026-10-13 00:00)`（上海时间）。
- ⚠️ **已知口径差**：`core/quota.py` 的 `day_key()` 用**服务器本地时区**，总结 `date` 用上海日历日。服务器 `TZ=Asia/Shanghai` 时一致；**部署要求必须写死 `TZ=Asia/Shanghai`**。

---

## 5. 工具注册表 `core/tool_registry.py`（+299 行，待合入）

### 5.1 `TOOL_META` 七字段

| 字段 | 含义 |
| --- | --- |
| `name` | 工具名 |
| `category` | 分类 |
| `defer` | 是否延迟发现 |
| `retryable` | 是否可重试 |
| `needs_approval` | 是否需要人工审批 |
| `max_output_chars` | 输出截断上限 |
| `cost_hint` | 成本提示 |

### 5.2 ⚠️ 导入即校验，漏配直接起不来

**设计意图**：漏配元数据在**导入期**报错，而不是运行时才发现 —— 把错误提前到「起不来」。

> **这是要主动维护的约束，不是一次性检查**。每次新增工具（`agent/tools.py` 的 `AGENT_TOOLS`，当前 5 个：
> `kb_search`/`web_search`/`calculator`/`get_weather`/`download_file`），**必须同时补 `TOOL_META` 七字段**。
> **好处**：本地开发时立刻发现，而不是「上线后某个工具被调用时才 500」。
> **代价**：本地起不来时先查 `TOOL_META` 是不是漏配了新工具 —— 这是**预期行为**，不是 bug。

### 5.3 延迟发现状态机

`defer=true` 的工具不一开始就暴露给模型，需经 `POST /api/agent/tools/discover` 激活。
**动机**：工具越多，模型选错概率越高；延迟加载能收窄候选空间。

⚠️ **与 Agent 图缓存的关系（需核实）**：工具集参与 `create_agent` 图编译，而 `agent/builder.py` 当前缓存键
是 `(has_kb, note, scene)`，**不含工具集状态**。若延迟发现状态在**编译期**改变实际注入的工具列表，
**缓存键必须同步带上该状态**，否则会出现「同键不同工具集」—— 与 §9.5 的「同键不同提示词」同类。
**待合入后核实代码，不要凭猜测定论。**

---

## 6. 四个新路由的端点契约（共 17 个，全部挂 `Depends(get_current_user)`）

> 来源：`docs/团队分支分析_20261013.md` §3.3。以下端点**均已在队友分支实现、待合入**。

**`/api/agent`（2 个）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/agent/tools` | 列出工具 + 执行元数据 |
| POST | `/api/agent/tools/discover` | 激活被延迟的工具 |

**`/api/schedule`（5 个）**

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/api/schedule/health` | worker 心跳 + 各 job 最近执行 | 登录 |
| GET | `/api/schedule/jobs` | 任务定义表 | 登录 |
| GET | `/api/schedule/runs` | 执行历史（**游标分页**） | 登录 |
| POST | `/api/schedule/run-now` | 手动触发 | **管理员** |
| POST | `/api/schedule/retry` | 重排失败执行 | **管理员** |

- **管理员判定**：沿用 `core/config.py` 的 `ADMIN_USERNAMES`（按用户名比对），不在名单 → **403**。
- **`run-now` 的关键设计**：手动触发**不另开执行路径** —— 只写一行 `status=pending, planned_at=now` 的
  `job_runs`，worker 下一轮扫到即执行。手动与自动走**同一条执行路径**，不产生第二条代码分支。

**`/api/report`（3 个）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/report/today` | 今日总结（**无则 404**） |
| GET | `/api/report/{date}` | 指定日期总结 |
| POST | `/api/report/generate` | 触发生成（**同步返回**） |

⚠️ `/today` 与契约不一致 —— 见 §9.2。

**`/api/delegate`（7 个）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/api/delegate/` | 列表 / 新建 |
| GET / PATCH / DELETE | `/api/delegate/{id}/` | 取 / 改 / 删 |
| POST | `/api/delegate/{id}/run` | 立即执行（**支持 `dry_run`**） |
| GET | `/api/delegate/{id}/runs` | 执行历史 |

**契约零破坏（硬承诺）**：现有 **35 个端点的路径、请求字段、响应形状一律未改** —— 这是队友自己写的验收项
（测试文档 M 组专验契约零破坏）。遵守 `04` §4.2 命名规则：全小写 + 连字符分词、资源用名词复数、
动作型用动词子路径、前缀**只在 `main.py` 写一次**。**尾斜杠**：新模块端点按「不带尾斜杠」注册
（`/api/agent/tools`、`/api/schedule/health`…），但 `/api/delegate/` 系列**带尾斜杠** —— 沿用其既有约定。

---

## 7. 每日总结的数据契约

**真实响应形状**（**以 `02_核心数据结构定义.md` §8 为契约**）：

```jsonc
{
  "report_id": "...",
  "date": "2026-10-13",
  "status": "...",
  "stats": {
    "conversation_count": 0, "message_count": 0,
    "user_msg_count": 0, "assistant_msg_count": 0,
    "active_hours": 0, "tool_call_count": 0
  },
  "content": [
    { "text": "结论文本", "source_refs": [123, 456] }   // ← 溯源消息 id 列表
  ],
  "model": "...", "generated_at": "...", "read_at": null
}
```

### ⚠️ `content` 不是思维导图树

**`content` 是「结论 + 溯源消息 id」的列表**，形如 `[{text, source_refs: [消息id]}]`。
**后端做了强制溯源**（`_filter_by_traceability`）：**`source_refs` 指不到真实消息的结论，会被直接丢弃。**

**后果**：**UI 里每条结论必须有可点回原文的角标**。这既是产品方向文档的硬要求
（「导图每个节点必须能点回原文」），也正好是后端已在执行的口径。

### ⚠️ 设计稿与实现的形状差异（合入前必须对齐）

接口设计稿（`docs/接口文档/Agent_编排_每日委托_每日总结 三块模块的功能需求.html`）写的是：

| 字段 | 设计稿 | 实现（`02` §8 契约） |
| --- | --- | --- |
| `content` | **object**：`{topics, questions, highlights, todo, suggestions}` | **list**：`[{text, source_refs}]` |
| `sources` | **独立字段**：`[{type, ref_id, title, ts}]` | **无独立字段**，溯源内嵌 `content[].source_refs` |
| `stats` | `{sessions, messages, active_minutes}` | `{conversation_count, message_count, user_msg_count, assistant_msg_count, active_hours, tool_call_count}` |

> **两者不一致，而 `02` 是契约**。合入前必须拍板：改实现回归设计稿，还是按「实现已跑通、契约以 `02` 为准」改设计稿。
> **拍板前前端与文档一律按 `02` §8 的 list 形状走** —— 它才是被 `_filter_by_traceability` 实际产出和消费的形状。

**其他硬约束**：

- **统计与正文分开**：统计口径确定（会话数、消息数、活跃时段）由 **SQL 直接算**；只有正文交给模型。
- **不动用户数据**：总结**只读**当天消息，不写会话、不改标题、不删任何东西。
- **长上下文要压缩**：先按会话分块摘要（**map**）再合并（**reduce**），**不能把全天原文一次性塞进 prompt**。
- **不做知识库写入**（避免把未验证的推断固化成知识）；**不做跨天趋势 / 周报月报**（院赛后）。
- `report_id` = `md5(owner_uid + '|' + date)` 前 12 位，**每天每人一条**；`status` ∈ `pending`/`generating`/`ready`/`failed`/`empty`。

---

## 8. 每日委托的业务规则

### 8.1 硬约束（逐条不可协商）

| 规则 | 说明 | 违反后果 |
| --- | --- | --- |
| `kind` 取值 | `study_plan` / `review` / `encourage` / `custom` | — |
| **启用上限** | 最多 **5 条**启用中 | 定时任务烧 token 且无人值守，必须有硬上限 |
| **同名** | 返回 **409** | `name` 是用户内唯一标识 |
| `name` | **建后不可改** | 它是唯一标识，`delegation_id` 由它派生 |
| `dry_run` | `POST .../run?dry_run=true` **只生成、不投递** | 见 §8.3 |

**`delegation_id`**：12 位十六进制，`md5(owner_uid + '|' + name)` 前 12 位 —— 与 `kb_id`、`report_id` 同一套规则。

**其他字段**：`name` 3~32 字符；`prompt` 0~1000，`kind=custom` **必填**；
`deliver_sid` **必须是本人的会话 id**（跨用户一律 404，不泄露存在性）；
`schedule` = `{freq: daily|weekdays|weekly|interval, at: 'HH:MM', weekday: 0-6, every_minutes: 60~1440, tz: 'Asia/Shanghai'}`。
**`next_run_at` 由服务端算好绝对值**，前端不做 cron 推算（避免两端算法不一致）。

### 8.2 到点投递行为

- **写回一个真实存在的会话**（`deliver_sid`），用户在**对话流**里看到它 —— **不是独立通知列表**。
- **同一天同一委托只送达一次**（幂等键见 §3.3）。
- 内容生成**要能引用用户自己的知识库与当天上下文**，否则只是模板套话。
- **推送只做站内 + 可选邮件**；短信 / 微信 / App 推送不做。
- **不做自然语言解析**（「每天早上七点」→ cron），20 号前用**结构化表单**；**不做跨用户委托**。

### 8.3 `dry_run` 的产品价值（**别当调试开关看**）

`dry_run=true` 只生成内容、不投递，用来实现：**「系统先生成好、给用户确认 / 修改」，而不是让用户每天填表。**
这是产品方向要求的核心交互，与 §9.3 的语义之争直接相关。

### 8.4 配额：新增 `kind='auto'`（默认 120）

`core/quota.py` 原有 `_LIMITS` **只有 `ask` 与 `embed` 两项**，而定时任务烧的 token **不计入任何一项** ——
`DAILY_ASK_LIMIT=60` 挡的是**用户主动提问**，自动调用完全在这个计数之外，**是个敞口**。

**新增 `auto`**：每次委托投递、每次总结生成记 1 次；触发时抛 `QuotaExceeded('auto', 120)`。

- 响应体形状与现有配额**完全一致**（`main.py` 的异常处理器已统一处理 `kind`/`limit`，**前端不用改解析**）。
- **手动执行同样消耗 `auto` 额度**：手动与定时**共用一个池子**，否则可以刷。
- **为什么按次数不按 token**：token 只能事后知道，而配额**第一职责是入口拦截**，次数可以先验判断。

> ⚠️ `core/quota.py` 与 `core/config.py` 归**申请人** —— 新增 `DAILY_AUTO_LIMIT` **必须先通知**。

---

## 9. ⚠️ 必须补的技术缺口（逐条给方案）

> 严重度与归属以 `01` §7「已知缺口」为准，以下均属 **C 线**。

### 9.1【高】`api/report.py` 两处同步调用未包 `to_thread`

**为什么是高**：`03` §3.3 把「`async def` 里有没有同步阻塞（必须 `await asyncio.to_thread(...)`）」列为
reviewer 的**正确性检查项**。这不是风格问题 —— `POST /api/report/generate` 是**同步返回**的，
生成一份总结要 **20~40 秒**；同步阻塞会把事件循环卡死，**期间所有在线用户的请求全部停摆**。

```python
# ❌ 现状
@router.post("/generate")
async def generate(...):
    report = generate_daily_report(...)      # 20~40s 同步阻塞

# ✅ 目标
@router.post("/generate")
async def generate(...):
    report = await asyncio.to_thread(generate_daily_report, ...)
```

**同时要做三件事**（否则只修一半）：

1. **两处都要改** —— 不要只改 `generate`。
2. **确认 `service/daily.py` 内部没有 `async` 假设** —— `to_thread` 里跑的是同步函数，内部 `await` 会直接崩。
3. **确认 SQLite 连接按线程复用**（`02` §1）—— `to_thread` 会换线程，而 `core/session.py` **按线程复用连接**、
   路径参与判断。读写时确认走同一套连接管理，**不要另开裸 `sqlite3.connect`**。

**提交信息**：`fix(api): report 端点同步调用包 to_thread，避免阻塞事件循环`（`03` §2.3 已列为**好提交范例**）。

### 9.2【中】`/api/report/today` 与文档契约 `/{date}` 不一致

实现是 `GET /api/report/today`，而 `04` §4.3 端点索引（与设计稿）写的是 `GET /api/report/{date}`。

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A. 两全（推荐）** | **保留 `/today`**，**同时补 `/{date}`** | `/{date}` 是契约必须存在；保留 `/today` 不破坏调用方 |
| B. 只留 `/{date}` | 删 `/today`，前端改传当天日期 | 破坏已有调用，且「今天」口径要在前端重算 |
| C. 只留 `/today` | 改契约文档 | **不可取**：`/{date}` 是查历史某天的唯一入口 |

**⚠️ 路由顺序陷阱**（选 A 必须注意）：`/{date}` 是**路径参数**，会把 `/today` 当成 `date="today"` 吃掉。
**`/today` 必须注册在 `/{date}` 之前**，或在 `/{date}` 内显式拒绝非 `YYYY-MM-DD` 格式（400「日期格式需为 YYYY-MM-DD」）。
顺手补齐：`date > 今天` 返回 **400「日期不能晚于今天」**（Asia/Shanghai 判定）。

### 9.3【需拍板】「每日委托」语义未对齐：补 `/submit` 还是改口径

状态：**待组会拍板**（`03` §5 待拍板事项 #2）；影响 C 线实现 + **申报书描述**。

| | 会议纪要口径 | 当前实现 |
| --- | --- | --- |
| 交互 | **学生填当日产出 → Agent 反馈** | **定时委托 CRUD + 定时投递到会话** |
| 需要 | 一个 **`POST /submit`** 端点接收「今天的产出」 | 7 个 CRUD + run 端点 |

**两条路，二选一（不要两个都做一半）**：

- **路径 1 · 补 `/submit`**：新增 `POST /api/delegate/{id}/submit`（或 `/api/report/submit`），接收当日产出文本 →
  触发 Agent 反馈 → 结果同样走「结论 + 溯源消息 id」契约。代价：多一个端点 + 一份契约 + 一组测试，**但叙事与纪要一致**。
- **路径 2 · 改口径**：把纪要与申报书表述改成「**定时委托 + 可选 `dry_run` 先预览再确认**」，
  承认 `dry_run` 就是「给用户确认 / 修改」的实现（§8.3）。代价：**零代码**，产品上仍满足「不让用户每天填表」。

> **唯一判据是 T10 申报书文本**：若已按「学生填产出」写，走**路径 1**；若尚未定稿，走**路径 2** 更省时。
> **不要按个人偏好选。** 无论选哪条，都要同步改 `02` §9（那里已标「语义未对齐」）。

### 9.4 四项明写「未做」

| # | 事项 | 归属 | 状态 |
| --- | --- | --- | --- |
| 1 | **「标签钩子」定义** | **B 线（林宏亮）** | 仓库**零命中**，接口未定义，队友要求**由申请人先定义** |
| 2 | **联网搜索协同设计** | B 线 | 未做 |
| 3 | **蒸馏检索实现** | B 线 | 未做 |
| 4 | **申报书 AI 文字** | A 线（申请人） | 未做 |

> **对 C 线的耦合面**：若「标签钩子」要在委托生成时注入标签上下文，或「联网搜索协同」要作为 Agent 工具，
> 都会改到 `agent/tools.py` 与 `TOOL_META`（你归属）。
> **行动项**：组会上先明确这两项的**接口形状**再决定是否需要你加工具；**定义清楚前不要自己发明一个形状** —— 会和 B 线撞车。

### 9.5【中】Agent 图缓存键含原始经纬度，导致缓存**永不命中**

位置：`agent/builder.py:67 / :80 / :145-149`（已核实代码）：

```python
# :67 —— city + 坐标分支，原始浮点直接进提示词
f"\n\n【用户当前位置】{city}（经纬度 {lat}, {lon}）。"
# :80 —— 只有坐标分支，同样
f"\n\n【用户当前位置】经纬度 {lat}, {lon}（前端地理定位得到，未反查出城市名）。"
# :145-149 —— 提示词片段直接当缓存键
note = location_note(location)
key = (bool(has_kb), note, scene)     # ← note 含 6 位小数的原始经纬度
```

**为什么永不命中**：浏览器定位返回的是 **6 位小数**（如 `31.230416, 121.473701`），
**每次定位几乎必然不同**（第 5~6 位就在抖）。于是 `note` 每次都不同 → `key` 每次都不同 →
**每次请求都 miss，都要重新编译一遍 LangGraph 状态图**。

> `builder.py:117-120` 的注释说「三者组合有限，命中率很高」，并配了 `_AGENT_CACHE_MAX = 32` 的 LRU。
> **开定位的用户把这个假设直接打破了**：键空间从「少量组合」变成「每人每次定位一个键」，
> LRU 被持续冲刷，其他用户的正常缓存也一起被挤掉。**这是缓存设计核心假设失效，不是小瑕疵。**

**✅ 修法：在 `location_note` 内部归一化坐标**

```python
lat = round(float(lat), 2)    # 约 1km 精度，对「查天气」完全够用
lon = round(float(lon), 2)
```

**精度取 2 位的理由**：这个坐标的**唯一用途**是 `get_weather(lat=..., lon=...)` 查天气，
天气是**城市级**信息，1km 精度绰绰有余；保留 6 位小数对天气查询**零收益**，却把缓存键打成了无限空间。

**⚠️ 绝对不能这样做（会造成「同键不同提示词」）**：

```python
# ❌ 错误修法：外面另做一个归一化键，但提示词里仍是原始坐标
note = location_note(location)                      # 含 6 位小数
key = (bool(has_kb), normalize(note), scene)        # 归一化后的键
```

这会让**两次不同坐标算出同一个 key**，但 `note`（进提示词的内容）**并不相同** ——
第二次会直接命中缓存，**拿到的 system prompt 里写的是第一个用户的坐标**。

> 这是**缓存键与内容不一致**的经典 bug：键相同 ⇒ 内容必须逐字节相同。
> 唯一安全做法是**让提示词本身就被归一化** —— 即**在 `location_note` 内部**改，
> 这样「键」与「提示词」天然同源，不可能不一致。

**顺带确认**：`location_note` 的 `has_xy` 用 `lat is not None and lon is not None`。
归一化时注意**不要**把「无坐标」误判成有坐标（`None` 不能进 `round()`），三种来源都要各测一次。

### 9.6【中】「打卡」在后端没有对应端点

后端**无 `/checkin/*` 端点**，`stats` 里**也没有 `streak`**。但 `挑战杯` T8 产出明写「**打卡**/总结/Agent 反馈样例」，
Demo 核心链路也含「总结打卡」。

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| **A. 从 `report` 派生（建议）** | 前端用「当天是否有 `status=ready` 的总结」当打卡 | **零后端改动**；语义略弱，跨天边界要按上海时区算 |
| B. 新增 `/api/checkin/*` | `POST /checkin`、`GET /checkin/streak` + 新表 | 新表 + 端点 + 契约 + 测试；**还要拍板进哪个 db** |
| C. `stats` 补 `streak` | 总结生成时顺带算连续天数 | 改动最小，但**只读、不能主动打卡**；`stats` 是契约字段 |

> **建议**：20 号前走 **A**（零改动、不阻塞前端），P1/P2 再认领 **B**（`挑战杯` 把「激励 / 留存后台」列为 P2 且归你）。
> ⚠️ **若选 C，`stats` 任何字段增删都必须同步 `02` §8**（契约修改规则：①改文档 ②改代码 ③通知受影响方）。

---

## 10. 合并注意

### 10.1 `api/delegate.py` 曾被 `git reset --hard` 清掉过一次

**队友文档自述**：`api/delegate.py` 曾被 `git reset --hard` 清掉，**悬空对象里也没找回**，现在的版本是**重写的**。

1. **重写版没有历史**，`git log --follow` 看不到演进 —— 不要试图考古。
2. **按「新代码」审查**，不要假设它已历过 review。
3. **重写容易漏细节** —— 特别核对 §8.1 的**四条硬约束**（5 条上限 / 同名 409 / `name` 不可改 / `dry_run` 语义）
   与 §9.3 的语义口径是否全部实现。
4. **`reset --hard` 意味着当时可能有未提交的工作丢失** —— 若某条文档承诺的字段/行为在代码里找不到，
   **先怀疑是这次事故的残留**，而不是「设计就是这样」。

### 10.2 其他合并风险（`团队分支分析_20261013.md` §5）

| # | 风险 | 对你（C 线）的影响 |
| --- | --- | --- |
| 1 | **`origin/main` 落后 8～11 个提交** | 你的成果**不在 main**，任何基于 main 的验收都看不到 |
| 2 | **命名残留**：`dev`/`feat` 不含改名提交（`204bb72` 知穗）与 UI 统一提交（`96e0dda`） | 「苞米/知慧/知穗」会撞车。**先合代码、后统一改名**，一次只做一件事 |
| 3 | 搜索双轨：`agent/tools.py` 直连搜索 vs `core/serper.py` | **收口方向已定**：一律走 `core/serper.py`，`agent/tools.py` 内直连实现**逐步删除**（`agent/**` 归你） |
| 4 | **合并时机** | 建议 **10/17 组会后、10/18 联调前**把 `feat/ai-module-20261013` 合进 `main`，别拖到 10/20 申报稳定期 |

**合并后必须跑测试**（`03` §1.4）—— 不能只看「无冲突标记」就认为合并成功。

---

## 11. 测试标准

**队友已举证的基线（只增不减）**：

| 项 | 数字 |
| --- | --- |
| pytest | **28 → 72 passed + 18 subtests**（**只增不减**） |
| 新端点**真机冒烟** | **29 项全过**（起真实 uvicorn 打真实 HTTP） |
| 接口文档校验 | 静态 **13 项** + headless Edge **25 项**双通过 |
| 环境 | **必须 Python 3.12**（`chromadb==0.5.23` 装不上 3.14） |

> `03` §4.3 已把「**29 项全过**」写成真机冒烟参考标准。你新增任何端点，**测试数量只能加不能减**，
> 且必须带真机冒烟记录（贴命令与输出）。

**本模块必须覆盖的用例**：

| 类别 | 用例要点 |
| --- | --- |
| **幂等** | 同一 `idem_key` 连插两次，第二次影响行数为 0、不重复投递 |
| **重试分级** | 四类失败**各测一条**：可重试 3 次 / 401-402 不重试 / 依赖失效置 `enabled=false` / 配额触顶延到次日 |
| **健康判定** | `heartbeat_age_seconds` = 100 / 400 / 700 → `up` / `stalled` / `down` 三档 |
| **溯源过滤** | `source_refs` 指向**不存在**的消息 id → 该条结论**被丢弃** |
| **委托业务规则** | 第 6 条启用 → 400；同名 → 409；`name` 改名 → 拒绝；`dry_run=true` → **不写会话** |
| **时区** | 总结 `date` 按上海日历日；禁止裸 `datetime.now()`（monkeypatch 改 `TZ` 复现 8 小时偏移） |
| **`to_thread`** | 生成过程中并发打一个轻量端点，确认**不被阻塞**（§9.1 的回归测试） |
| **缓存键归一化** | 同城两组 6 位小数坐标 → **命中同一缓存对象**（`is` 比较）；归一化后坐标变了但 key 不变 → **必须失败**（防 §9.5 错误修法） |

**测试隔离**：沿用 `monkeypatch` 改路径（`04` §3.2）。
⚠️ **脚本式自测必须加进 `pytest.ini` 的 `--ignore`**，否则收集阶段整段执行 → `no such table: users`（`04` §2.6）。

---

## 12. 提交前自检与分支命名

### 12.1 提交前自检（`03` §2.5）

```bash
ruff check .                    # 静态检查必须无错
pytest -q                       # 离线测试必须全绿（数量只增不减）
cd frontend && npx tsc --noEmit # 前端类型检查必须 Exit 0（如涉及前端）
git status                      # 确认没有 .env / *.db / 密钥被误加
git diff --cached               # 逐行看一眼暂存区
```

**C 线专属自查**（通用清单之外逐条过）：

- [ ] `async def` 里**没有**同步阻塞（§9.1 的 `to_thread`）—— reviewer 的正确性检查项
- [ ] 新增工具已补 `TOOL_META` **七字段**（否则导入期直接起不来）
- [ ] `job_runs` 写的是 **`data/scheduler.db`**，没混进 `sessions.db`
- [ ] 时间处理**无裸 `datetime.now()`**，一律 `ZoneInfo("Asia/Shanghai")`
- [ ] 委托/总结的读写入口**都过了归属校验**（跨用户一律 404，不泄露存在性）
- [ ] 若改了 `stats` / `content` 字段 → 已同步 `02_核心数据结构定义.md`
- [ ] 若新增配置项（如 `DAILY_AUTO_LIMIT`）→ **已通知申请人**
- [ ] 若动了 `main.py` 挂载点 → **已通知申请人**
- [ ] `worker` 部署变化（第三个进程、日志轮转、存活检查）**已告知申请人**

> ⚠️ **提交 `.env` 是最高级别的安全事故**。**永远不要用 `git add -f` 加环境文件。**
> `data/`、`chroma_db/`、`*.db` 已在 `.gitignore`。

### 12.2 本模块分支命名（`03` §1.2：`<类型>/<模块>-<简述>-<日期>`）

| 场景 | 分支名 |
| --- | --- |
| Agent 编排 | `feat/agent-orchestration-20261020` |
| 调度 / worker | `feat/agent-scheduler-worker-20261021` |
| 委托 / 总结 | `feat/agent-delegate-report-20261021` |
| 修 `to_thread`（§9.1） | `fix/api-report-to-thread-20261021` |
| 收口 `/today`（§9.2） | `fix/api-report-date-endpoint-20261021` |
| 修缓存键（§9.5） | `fix/agent-location-cache-key-20261021` |

**模块名取值**：`frontend` / `kb` / `agent` / `api` / `core` / `deploy`。
**铁律**：**永远从 `dev` 切出，不是 `main`**；生命周期 **≤ 5 天**；合并目标 **`dev`**。
`❌` 反例：`test`、`mybranch`、`临时`、`新分支2`。

### 12.3 本模块提交信息（`03` §2.1–2.2：`<类型>(<作用域>): <简述>`，中文、≤50 字、结尾不加句号）

```
✅ feat(agent): 调度底座接入独立 worker 进程，五个 job 按 Asia/Shanghai 触发
✅ feat(agent): 每日委托补 dry_run 预览，只生成不投递便于用户确认
✅ fix(api): report 端点同步调用包 to_thread，避免阻塞事件循环
✅ fix(agent): 定位坐标在 location_note 内归一化，修复图缓存永不命中
✅ fix(core): 心跳年龄超阈值判 stalled/down，替代进程表判活
✅ test(agent): 补调度幂等与重试分级用例，pytest 72 → 96 passed
✅ docs: 新增 C 线调度与 Agent 开发指南
❌ 改了一下 / fix bug / update / 提交
❌ feat: 新增功能、修了bug、改了样式、更新文档（一件事混杂四类改动）
```

**一个提交只做一件事** —— 混杂提交无法 revert、无法 review、无法归因。

### 12.4 PR 必须回答（`03` §3.1）

- 后端接口：**是否破坏契约？** → C 线标准答案 **否**（现有 35 端点路径/请求字段/响应形状一律不改）
- 数据库结构：**是否变更表结构？** → **是**（调度三张表在 `data/scheduler.db`；改结构要同步 `02` §7）
- 部署方式：**是** —— `deploy.sh` 要多管一个 `python -m worker.scheduler` 进程
- 验证方式：**贴出实际执行的命令与结果**（例：`pytest -q` → 96 passed）

---

## 13. ⚠️ 待确认事项（**确认前不要当既成事实用**）

| # | 事项 | 现状 | 影响 |
| --- | --- | --- | --- |
| 1 | ~~GitHub 账号与人对应~~ | ✅ **已确认**（2026-10-13）：`huashuotianxuan` = **刘佳林**（你），`sakasa-code` = **林宏亮**。`CODEOWNERS` 已生效 | 已解除。遗留：`feat/ai-module-20261013` 交付文档自称对应「林宏亮五条职责」属**署名写错**，建议改口径 |
| 2 | 接口命名空间：`/api` 还是 `/api/v1` | 实现全用 `/api`（35 端点）；`挑战杯` T5 写的是 `/api/v1` | **按指示暂缓**（`04` §4.1）；本文所有路径按**现状 `/api`** 书写 |
| 3 | 「每日委托」语义：补 `/submit` 还是改口径 | 见 §9.3 | 影响 C 线实现 + 申报书描述 |
| 4 | 文档口径与实现不一致：设计稿写「只交付设计 + Mock」，实际已编码 | 队友倾向**改口径、承认已实现** | 影响申报书表述 |
| 5 | 每日总结 `content` 形状：设计稿 object vs 实现 list | 见 §7 | **合入前必须对齐**，否则前端按错形状解析 |
| 6 | 延迟发现状态是否进 Agent 图缓存键 | 见 §5.3 | **待合入后核实代码**，不要凭猜测定论 |

---

## 14. 落地顺序建议（前一项是后一项的前提）

1. **等合入** —— 先把 `feat/ai-module-20261013` 合进 `dev`（建议 10/17 组会后、10/18 联调前），
   否则本地的 `service/`、`worker/`、`core/scheduler.py`、四个路由**根本不存在**，什么都改不了。
2. **修 §9.1（`to_thread`，高优先级）** —— 独立分支，一个提交一件事。
3. **收口 §9.2（`/today` vs `/{date}`）** —— 顺带补 `/{date}` 的 400 校验与路由顺序。
4. **修 §9.5（缓存键归一化）** —— 改动小、收益明确（开定位用户的缓存从「永不命中」变回正常）。
5. **确认 §7 的 `content` 形状** —— 与前端 + 申请人一起定，改完同步 `02`。
6. **拍板 §9.3（委托语义）** —— 组会决定后，代码与申报书口径**同一次改**。
7. **补 §9.6（打卡）的派生方案** —— 20 号前走 A 方案（零后端改动）。
8. **`deploy.sh` 三进程改造** —— 提需求给申请人，附上「多一条日志、多一处存活检查、多一个静默故障」三条后果。
9. **`/submit` 与 P2 的激励/留存后台** —— 院赛阶段（`挑战杯` P1 #5、P2）。

---

## 变更记录

| 日期 | 变更 | 人 |
| --- | --- | --- |
| 2026-10-13 | 首版 | 申请人 |
