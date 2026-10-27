# 07_存储层_SQLite与向量库_升级版（2026-10-20）

> 对应研讨卷：`docs/技术选型研讨_2026-10-20/07_存储层_SQLite与向量库.md`
> 本卷回答的问题：从校赛到省赛，存储层要改什么、为什么现在必须改、改完怎么验。

---

## 0. 一句话结论

**不换数据库，补三样东西：能恢复的备份、带版本号的迁移、关系库与向量库的一致性对账。**

校赛口径是"能存住、重启不丢"——这一点已经做到了（WAL + `UNIQUE(idem_key)` 幂等 + 归属进 JOIN）。
省赛口径是"能**证明**存得住、丢得起、恢复得回来"，而评分标准"功能效果 20%"明确要"系统测试数据充分""已投入实用"：
演示现场最致命的不是功能没跑通，是**演示中途数据没了或者恢复不回来**。所以这一层的升级全部指向"可证明性"，而不是"换更强的引擎"。

---

## 1. 现状盘点

| 组件 | 现状 | 出处（文件:行） |
|---|---|---|
| 主库文件 | `data/sessions.db`，单文件，线程本地连接复用（不做连接池） | `core/session.py:41-48`、`core/session.py:59-80` |
| PRAGMA 三件套 | `journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000` | `core/session.py:75-77` |
| 主库表数 | **16 张**（session 10 / quota 2 / daily 2 / account 1 / checkin 1），全部共用 `session._get_conn` | `core/session.py:106-227`；`core/quota.py:102,266`；`service/daily.py:81,94`；`core/account.py:84`；`service/checkin.py:32` |
| 调度库 | `data/scheduler.db`（`schedule_jobs`/`job_runs`/`worker_heartbeat` + APScheduler 的 `apscheduler_jobs`），独立连接、同一套 PRAGMA | `core/scheduler.py:178-186`、`core/scheduler.py:127-165` |
| 工作流库 | `data/hybrid.db`（`workflow_defs`/`workflow_runs`/`switch_rules`/`exec_logs`），第三份 `threading.local()` 连接 | `hybrid/store.py:72-105`、`hybrid/store.py:165-206` |
| 迁移机制 | `ALTER TABLE ... ADD COLUMN` + `try/except: pass` 幂等补列；**全仓库无 `user_version` / schema 版本号** | `core/session.py:230-269`；`grep user_version` 无命中 |
| 备份方式（文档口径） | `cp data/sessions.db ...` + `tar czf chroma_db.bak...` | `规范/04_环境与部署.md:198-203` |
| WAL 实际状态 | 开发机实测：`sessions.db` 180 KB，`sessions.db-wal` **631.7 KB**（未 checkpoint） | 本机 `data/` 实测（2026-10-20） |
| 向量库 | Chroma `PersistentClient` 进程级单例，目录 `chroma_db/`（开发机 0.2 MB / 1 文件） | `kb/rag_core.py:41-57`；`core/config.py:38` |
| 知识库归属 | `kb_id` 内含 owner 哈希 + `data/kb_owners.json` 归属表，无记录即默认拒绝 | `kb/pipelines.py:13-25`、`kb/pipelines.py:76-78` |
| 删库一致性 | `delete_kb` 同事务内删 Chroma collection + 3 个 JSON + 源目录 | `kb/pipelines.py:347-371`；`kb/rag_core.py:1242-1248` |
| 删单文件一致性 | `delete_by_source`（Chroma） + `remove_file_record`（JSON）两条独立调用 | `kb/rag_core.py:1119-1134`；`kb/pipelines.py:519-533` |
| JSON 原子写 | 临时文件 + `fsync` + 带退避的 `os.replace`（Windows 句柄占用重试 6 次） | `kb/pipelines.py:107-130` |
| 租户隔离落库 | `owner` 是第一个必填参数（漏改即 TypeError）；读把归属并进 JOIN，写再走一次 `_owned()` | `core/session.py:14-24`、`276-282`、`395-396`、`540-543` |
| 幂等 | `UNIQUE(idem_key)` + `INSERT ... ON CONFLICT DO NOTHING` + 判 rowcount | `core/scheduler.py:154`、`core/scheduler.py:327-342` |
| 容量控制 | `messages` 无上限、无 TTL、无归档；`job_runs` 有清理（>90 天，保留 failed） | `core/session.py:124-133`；`core/scheduler.py:667-680` |

一句话：**卷七写的时候是 8 张表，现在是 23 张（16+3+4），而迁移机制还是"ALTER + try/except"这一套。**表翻了三倍，管理手段没变——这是本卷最主要的债务来源。

---

## 2. 校赛够用 vs 省赛不够

| 维度 | 校赛口径 | 省赛口径 | 差距 | 证据 |
|---|---|---|---|---|
| 备份 | "有备份命令" | 有**定时任务** + 有**恢复演练记录** | 全仓库无定时备份脚本，无演练记录（服务器上是否已挂 crontab **待核对**） | `规范/04:198-203` 只有命令 |
| 备份正确性 | 认为 `cp` 等于备份 | WAL 模式下 `cp` 只拷主文件，丢 `-wal` 里的新数据 | 实测 `-wal`(631.7 KB) 是主库(180 KB) 的 3.5 倍——**按现行规范备份，恢复出来的是 78% 空白的数据** | 本机 `data/` 实测 |
| Schema 演进 | 补列成功就行 | 有版本号、有失败检测、可回滚 | 无版本号；`except Exception: pass` 让迁移失败**静默** | `core/session.py:230-269` |
| 并发写 | "4 worker 下排队但不报错" | 能量化"排队多久、什么时候开始 500" | `busy_timeout=5000` 是兜底参数，不是指标；无任何锁等待埋点 | `docs/并发承载测试报告_2026-10-20.md:94` |
| 容量 | "40G 盘还有 100 倍空间" | 有容量监控与归档策略 | 无监控、无 TTL、无归档任务 | 卷七 §7.12 |
| 跨库一致性 | 删库代码正确（两边都删） | 能**对账**：JSON 记录 vs Chroma 实际片段数 | 只有"读不到时用 Chroma 兜底"，无主动对账 | `kb/pipelines.py:404`；`kb/rag_core.py:1196` |
| 演示用数据 | 演示时能用 | "系统测试数据充分"——能出示数据量、恢复时长、一致性对账结果 | 三项均无 | 评分标准 功能效果 20% |
| 租户隔离 | 代码层做了 | 能出示**落库证据**（一行 SQL 证明 A 查不到 B 的数据） | 代码正确，但无自动化断言覆盖全部表（部分表如 `usage`/`daily_reports` 靠调用方传 owner） | `core/session.py:301-313` |

---

## 3. 升级项

### P0-1 备份：从"有命令"到"有任务 + 有演练"

- **现状**：`规范/04:198-203` 写的是 `cp data/sessions.db ...`，WAL 模式下会丢 `-wal`；没有定时任务，没有恢复演练。
- **改什么**：
  1. 用 `sqlite3 .backup`（或 Python `conn.backup()`）替代 `cp`，两个 db 都做，备份前先 `PRAGMA wal_checkpoint(TRUNCATE)`；
  2. 加一个 `tools/backup.py`（或 shell），由 `housekeeping` 之后的系统 cron 每天跑一次，保留 7 份；
  3. `chroma_db/` 用 `tar czf`（Chroma 自身是 SQLite + 二进制索引，同样不能热拷）；
  4. 备份产物写一份 `manifest.json`（时间、文件大小、表行数、Chroma collection 数）。
- **为什么省赛必须改**：演示现场唯一的不可恢复故障是"数据没了"。而现在的备份方式**看起来是好的**（文件能打开、能查询），恢复时才发现是旧的——这是卷七 §7.13 点名的"看起来没问题的陷阱"。省赛要求"已投入实用"，一次可演示的恢复演练比十个功能点更有说服力。
- **代价/风险**：每天多一次全量写（几百 MB 级，2G 机器上 I/O 有抖动）；备份进程与主进程争锁，必须放在低峰（建议跟 `housekeeping` 错开，`report.daily` 是 23:30，`housekeeping` 是 04:15）。
- **反转条件**：若备份耗时 > 5 分钟或 I/O 明显拖慢在线问答，降级为"每 3 天一次 + 演示前手动跑一次"。
- **验收口径**：在 staging 上执行"备份 → 删库 → 恢复 → `GET /api/sessions` 与 `GET /api/kb` 数据与备份前一致"，全程 ≤ 3 分钟，截图进作品材料。

### P0-2 Schema 版本化

- **现状**：`_init_db()` 用 `ALTER TABLE ADD COLUMN` + `try/except: pass` 补列（`core/session.py:230-269`），23 张表、三份连接管理，没有一处记录"当前是哪个版本"。
- **改什么**：给三个库各加 `PRAGMA user_version = N`，把现有补列逻辑登记为 `MIGRATIONS = [(1, fn), (2, fn), ...]`，启动时按当前版本依次执行；`ALTER` 失败的 `except` 不再 `pass`，改为写一条结构化日志 + 让启动自检接口能看见。
- **为什么省赛必须改**：现在"加一个字段"这件事没有版本号，意味着**无法回滚、无法判断某台机器处于哪个结构状态**。三人并行开发、staging 与生产同机（不同目录），结构漂移是迟早的事——而漂移的表现是"某张表少了列，某条 SQL 500"，排查成本极高。
- **代价/风险**：一次性梳理（约半天）；`user_version` 是库级单值，三个库要各自维护；老库版本号是 0，需要一次"基线登记"。
- **反转条件**：若表数量回落到 10 张以内且不再新增，可维持现状。
- **验收口径**：`python -c "import core.session; print(session.schema_version())"` 输出非 0；在一个"故意缺列"的库上启动，能打出一条 `schema_migration_failed` 日志而不是静默通过。

### P0-3 迁移失败不再静默

- **现状**：`core/session.py:231-234`、`241-244`、`247-250`、`266-269` 四处 `try/except: pass`。
- **改什么**：把 `pass` 换成 `_log.warning("migration_skip", ...)` 并累加到"启动自检"结果里；`/api/metrics` 或 `/health` 暴露 `schema_degraded` 标志。
- **为什么省赛必须改**：静默失败是评审最容易被追问的点——"你怎么知道迁移成功了？"现在答案是"不知道"。
- **代价/风险**：几乎为零；风险是把"预期内的已存在"也报成警告，需要区分 `duplicate column name` 与其它异常。
- **反转条件**：无。这一项没有不做的理由。
- **验收口径**：在已建好的库上重启，日志中**不出现** `migration_skip`；手工 `DROP COLUMN` 模拟一次冲突，日志出现且 `schema_degraded=true`。

### P1-1 锁竞争与写入可观测

- **现状**：`busy_timeout=5000` 已配（`core/session.py:77`），但没有任何计数——无法回答"现在有多少写在排队"。
- **改什么**：在 `core/metrics.py` 预声明 `sqlite_write_seconds`（直方图）与 `sqlite_busy_total`（计数器）；在 `_get_conn()` 的 yield 前后打点（包一层即可，不改调用方）。
- **为什么省赛必须改**：压测报告把"SQLite 多进程写竞争"列为第 6 号瓶颈（`docs/并发承载测试报告_2026-10-20.md:94`），给的结论是"用户量大时应把写集中到 worker 进程"——但**没有数据就无法判断什么时候该做这个迁移**。
- **代价/风险**：每条 SQL 多两次 `time.perf_counter()`（约 0.1 μs），可忽略。
- **反转条件**：无。
- **验收口径**：200 并发压测下 `sqlite_busy_total` = 0 且写入 p95 < 20ms；压测报告更新一节"存储层实测"。

### P1-2 关系库与向量库的一致性对账

- **现状**：`kb_files.json` 的 `chunks` 数与 Chroma 实际片段数是同一事实的两份记录；正常路径读 JSON，异常路径用 `list_sources()` 兜底（`kb/pipelines.py:404`）；删单文件要调两个函数（`delete_by_source` + `remove_file_record`），**中途失败就会不一致**。
- **改什么**：加一个对账函数 `reconcile(kb_id) -> {missing_in_chroma, missing_in_json, count_diff}`，挂到 `housekeeping`（每天 04:15）只记日志不自动修；同时在删文件/删库路径上把"Chroma 删除成功但 JSON 写入失败"显式记一条 `kb_consistency_error`。
- **为什么省赛必须改**："删了一个文档，列表里还在 / 列表没了但还能检索到"是评委随手就能试出来的问题；有对账日志才能证明"我们知道它会不一致，并且我们在盯"。
- **代价/风险**：对账要遍历整库元数据（大库慢），必须限流（每库每秒最多一次）且放在低峰。
- **反转条件**：若知识库总量 < 50 个且单库 < 1000 片，可只做日志不做界面。
- **验收口径**：人工制造不一致（直接删 Chroma collection 不删 JSON），对账日志能在下一轮 housekeeping 抓到并给出 diff。

### P1-3 容量监控与归档

- **现状**：`messages` 无上限，`job_runs` 有清理（>90 天、保留 failed，`core/scheduler.py:667-680`）。
- **改什么**：给 `messages` 定一个软上限（建议按会话：单会话 > 5000 条时前端提示"建议开新会话"），并把 `data/` 与 `chroma_db/` 的磁盘占用写进 `/health` 或 `/api/metrics`（现在只有 node_exporter 的整机磁盘告警，分不出是谁占的）。
- **为什么省赛必须改**：40G 盘够用不等于"永远够用"，而"磁盘 < 10%"那条告警（`monitoring/README.md:46`）触发时已经晚了——SQLite 此时已经开始写失败。
- **代价/风险**：归档（把老会话导出成文件再删）会破坏"完整历史"，属于产品决策，需要确认；**本项只做监控，不做自动删除**。
- **反转条件**：若演示期用户量 < 100 且消息总量 < 10 万条，可推迟到省赛后。
- **验收口径**：`/api/metrics` 能读到 `data_dir_bytes` / `chroma_dir_bytes`；压测灌 10 万条消息后仍能正常读写。

### P2-1 跨库 JOIN 的静态检查

- **现状**：`sessions.db` 与 `scheduler.db` 物理分开（正确决策），代价是不能跨库 JOIN——这个约束只存在于注释和事故记忆里（卷七 §7.4），`service/daily.py:642-644` 的注释还专门记了一次踩坑。
- **改什么**：加一条 ruff/lint 规则或单测，禁止在同一条 SQL 里出现分属两库的表名（白名单比对即可）。
- **为什么省赛必须改**：属于"讲出来加分、不讲也不会扣分"的工程严谨性项，优先级低于上面全部。
- **反转条件**：若两库合并（不建议），本项作废。
- **验收口径**：故意写一条跨库 JOIN 的单测，CI 报红。

### P2-2 换 Postgres 预案（只写文档，不实施）

- **现状**：换 PG 的五条判据（卷七 §7.14）**一条都没触发**：单机部署、写入远低于 100/s、单库远小于 10GB、无复杂分析查询、无合规要求。
- **改什么**：把五条判据抄进运维文档，附"触发后要改什么"清单（方言/连接池/迁移工具/隔离级别/备份监控）。
- **为什么省赛"必须"**：不是必须换，是必须能回答评委"你这个数据量再涨十倍怎么办"。有判据清单 = 有演进路径，这是"主题创意 + 技术方案"两栏都吃分的。
- **代价/风险**：零（只写文档）。
- **反转条件**：任一判据成立即启动评估。
- **验收口径**：文档存在，且判据可量化（有阈值、有当前值）。

---

## 4. 竞品对照

| 竞品 | 这一层怎么做 | 我们差在哪 | 跟不跟 |
|---|---|---|---|
| **Dify**（142k⭐，2核4G） | PostgreSQL + pgvector 为默认，存储与向量同一个库；自带 LLMOps 日志追踪与 A/B 测试，运行数据可回看 | 我们两个库 + 一个向量目录，且**没有跨库对账**；Dify 有完整的可观测面板，我们只有 `/api/metrics` 裸指标 | **不跟**（换 PG 在 2G 机器上不成立）；但**要跟它的"运行日志可回看"**——对应 P1-1/P1-2 的可观测补齐 |
| **FastGPT**（26.1k⭐，2核4G） | MongoDB + PostgreSQL/pgvector 混合；商业版提供运行日志看板 | 同上。它的"2核4G 起步"说明我们同档位，但它的存储栈组件更多，运维面更大 | 不跟栈，**跟它的日志看板思路**（我们的 P1-1 是它的最小子集） |
| **RAGFlow**（80.7k⭐，**4核16G**） | Elasticsearch/Infinity 双引擎 + 知识图谱 + MySQL | 它对硬件的要求是我们的 8 倍，**这条路线在 2核2G 上根本跑不起来**——这正是我们坚持 SQLite+Chroma 的对照证据 | 完全不跟；但它的"深度文档理解"是检索层（卷五）的事，不在本卷 |
| **Coze 扣子**（闭源 SaaS） | 无需自管存储，数据全在厂商侧 | 我们自托管，必须自己回答"数据在哪、怎么恢复" | 不跟（自托管是本项目的前提）；但**"备份与恢复"是 Coze 用户从不需要想、我们必须讲清楚的事**，这正是 P0-1 的由来 |
| **n8n**（188k⭐，1核1G 即可跑） | SQLite 为默认、PG 可选；工作流执行历史落库、可配置清理保留天数 | 我们的 `job_runs` 保留 90 天且保留 failed（`core/scheduler.py:667-680`），口径与 n8n 一致，**这一项我们是齐的** | 已对齐，无需额外动作 |

**读法**：三个开源竞品里有两个（Dify、FastGPT）与我们同档硬件但用更重的存储栈——说明"2核4G 跑 PG+pgvector"是可行的，**我们选 SQLite 不是因为跑不动 PG，而是为了零运维**（卷七 §7.1 的判据）。所以升级方向是"补齐可证明性"，不是"换重栈"。

---

## 5. 落地顺序与验收口径

| 序 | 项 | 依赖 | 产出 | 验收（可演示/可截图） |
|---|---|---|---|---|
| 1 | P0-3 迁移失败不静默 | 无 | 日志 + `schema_degraded` | 模拟缺列 → 日志出现 |
| 2 | P0-2 `user_version` + 迁移清单 | 1 | `core/session.py` / `core/scheduler.py` / `hybrid/store.py` 三处版本号 | 打印版本号非 0 |
| 3 | P0-1 备份脚本 + 定时任务 | 2 | `tools/backup.py` + manifest | **staging 上完整恢复演练一次**，全程 ≤3 分钟 |
| 4 | P1-1 锁/写耗时埋点 | 无 | 2 个指标 | 压测报告补一节存储实测 |
| 5 | P1-2 一致性对账 | 无 | `reconcile()` + housekeeping 挂钩 | 人为制造不一致 → 日志抓到 |
| 6 | P1-3 容量指标 | 无 | 2 个指标 | 灌 10 万条消息后仍正常 |
| 7 | P2-1 跨库 JOIN lint | 2 | 一条 lint 规则 | 故意写错 → CI 红 |
| 8 | P2-2 换 PG 判据文档 | 无 | 文档 | 判据有阈值有当前值 |

**顺序理由**：1→2→3 是一条链——先让失败可见，才能给迁移登记版本，才能放心做"备份—改结构—恢复"的闭环。4~6 是纯增量，谁先谁后不影响。7~8 是"讲出来加分"，放最后。

**演示口径（省赛现场）**：三张截图 = 备份 manifest、恢复后的一致性对账结果、`/api/metrics` 里存储相关的四个数字。这比"我们用了 SQLite"这句话有力得多。

---

## 6. 不做的事（及为什么）

| 不做 | 为什么 |
|---|---|
| **换 PostgreSQL** | 五条判据一条都没触发（§3 P2-2）。换库是 2~3 周的手术（方言、连接池、Alembic、隔离级别、备份监控全套），用在省赛前是负收益——它会同时消耗掉卷五检索层与卷九上下文层的改造时间 |
| **上 Redis 做主存 / 共享限流** | 卷八/卷三已用"SQLite 秒桶"（`core/quota.py:57-73`）解决了多 worker 限流；Redis 只为"严格 N"这个精度付 50~100MB 常驻内存，2G 机器上不划算 |
| **引入 SQLAlchemy ORM / Alembic 全套** | 23 张表全是手写 SQL，ORM 的价值在跨库与复杂关系；Alembic 会带来一整套"自动生成迁移"的约定，与现行"建表即带新列 + 老库 ALTER"的双路径冲突。`user_version` + 手写迁移清单是同效果的最小实现 |
| **分库分表 / 按用户拆库** | 单库 180 KB 起步，拆了以后跨用户统计（画像聚合 `profile.aggregate`）立刻变成分布式查询 |
| **SQLCipher 全库加密** | 加密后 `.backup` 与 `cp` 都不能直接看，排障成本暴涨；且省赛不要求密态存储（卷三的隔离已经是软件层隔离） |
| **对象存储（OSS/S3）存上传原文** | 单机部署下 `data/kb_sources/` 就是对象存储；引入外部依赖会破坏"整机可打包成一个 tar"这个最大优势（卷七 §7.15） |
| **Chroma 换 Milvus / pgvector / Elasticsearch** | 卷五 5.1 的判据仍然成立：要"嵌进单进程 + 零运维 + metadata 过滤"，只有 Chroma 全占。RAGFlow 那套 ES 要 4核16G |
| **自动修复一致性（对账后自动删/自动补）** | 自动修会在"对账逻辑有 bug"时把正确数据删掉。只记日志、人工确认，是这一层唯一安全的默认 |

---

## 附：本卷的"待核对"项

1. **生产机上是否已挂定时备份的 crontab**——本卷按"没有"写，需 SSH 到 `8.163.62.25` 执行 `crontab -l` 确认（`monitoring/README.md:29` 只装了探针 cron）。
2. **生产 `data/` 与 `chroma_db/` 的实际大小**——本卷引用的是开发机实测（180 KB / 631.7 KB WAL / 0.2 MB Chroma），生产值待核对，用于 P1-3 的阈值设定。
3. **生产 uvicorn worker 数**——`systemd/agent-hub-backend.service:16` 的 `__WORKERS__` 由 `install-systemd.sh` 按 vCPU/内存探测替换，实际值待核对；它直接决定 P1-1 的写竞争实测基线。
4. **前端是否已有备份/容量相关界面**——本卷未发现相关页面，若已存在则 P1-3 的"监控"部分可简化为接入既有页面。
