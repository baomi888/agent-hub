# 卷七 · 存储层：SQLite、向量库与文件系统

> 本卷解决的问题：**在一台 2G 内存的机器上，用最少的组件，把"谁说了什么、什么时候、算不算数"记住，并且记住的内容在崩溃后仍然可信。**
> 这一层最容易被低估，因为它在功能演示时完全隐形——所有的坑都要等到"并发写""重启""跨库查"才暴露。

---

## 7.1 SQLite：一个被"轻量"标签误导的选择

### 候选面

| 方案 | 常驻内存 | 运维成本 | 并发能力 | 在本项目 |
|---|---|---|---|---|
| **SQLite** | ~0（库级） | 零 | 单写多读（WAL 后可并发读） | ✅ 选 |
| PostgreSQL | 150~300MB | 中（调参、vacuum、备份） | 高 | ✗ 内存不足 |
| MySQL | 200~400MB | 中 | 高 | ✗ 同上 |
| MongoDB | 200MB+ | 中 | 高 | ✗ 同上 + 无事务保证（旧版本） |
| Redis | 50~100MB | 低 | 极高 | ✗ 只做缓存，不做持久主存（默认 RDB/AOF 有丢数据风险） |
| DuckDB | 低 | 零 | 单写 | ✗ OLAP 导向，不适合高频小事务 |

### 真正的判据不是"轻量"，是"运维面为零"

很多人选 SQLite 的理由是"简单"。但"简单"不是一个可评估的维度——**运维面为零**才是。

具体地：

- **无进程**：不需要 `systemctl start postgresql`，不需要 `pg_ctl`，不需要"数据库没起来导致应用报错"的排查。
- **无连接串**：不需要 host/port/user/password/database/pool_size 六个参数。路径就是配置。
- **无用户/权限体系**：文件权限就是权限。
- **无备份工具**：`cp data/sessions.db` 就是备份（WAL 模式下要用 `sqlite3 .backup`，见 §7.13）。
- **无版本升级**：SQLite 是文件格式，升级客户端不需要迁移服务端。

这五条加起来，等于**"数据库"这个组件在运维上不存在**。

对比一下如果选 PostgreSQL，需要额外做的事：

```
1. 装 PG（apt install postgresql，约 50MB 包）
2. 初始化集群（initdb）
3. 建用户、建库、授权
4. 调 shared_buffers（2G 机器建议 256MB）、effective_cache_size、work_mem
5. 配 pg_hba.conf（本地 socket 还是 TCP）
6. 备份策略（pg_dump + 定时任务 + 保留策略）
7. 监控（连接数、慢查询、磁盘）
8. 连接池（因为 PG 的连接是进程级，每次连接开销大）→ 又需要 pgbouncer 或应用层池
```

其中第 4 步是致命的：`shared_buffers=256MB` + 应用本身 300MB + Node 两个进程 400MB + Chroma 100MB = 已经超过 1G，加上系统和其他，2G 机器会频繁 swap。

**所以这不是"SQLite 够用"，是"PostgreSQL 用不起"。** 这个区别很重要，因为它决定了反转条件（见 §7.15）。

### 一个反直觉的事实：SQLite 的写性能比 PG 好

在单机、低并发、小事务的场景下，SQLite 的写入**比 PostgreSQL 更快**：

- 无网络往返（进程内函数调用 vs TCP）；
- 无 WAL 刷盘协调（PG 的 WAL 要 fsync，SQLite 的 `synchronous=NORMAL` 在 WAL 下不每次 fsync）；
- 无连接开销（SQLite 的连接是一 个文件句柄）。

本项目的写模式是"高频小事务"（每轮问答 2~4 次 INSERT/UPDATE），这正是 SQLite 的强项。

**结论：在这个工作负载下，SQLite 不是"降级方案"，是"更优方案"。**

---

## 7.2 三个 PRAGMA：缺一不可

```python
conn.execute("PRAGMA journal_mode=WAL")        # 写前日志
conn.execute("PRAGMA synchronous=NORMAL")      # WAL 下不必每次 fsync
conn.execute("PRAGMA busy_timeout=5000")       # 撞上并发写时等待
```

### `journal_mode=WAL`

**默认是 `DELETE`**（回滚日志）。WAL（Write-Ahead Logging）的区别：

| 模式 | 读 | 写 | 读写并发 |
|---|---|---|---|
| DELETE | 读时加共享锁 | 写时加排他锁 | **读阻塞写，写阻塞读** |
| WAL | 读快照 | 追加到 WAL 文件 | **读写可以并发** |

对 Web 应用，第二条是关键：用户在读会话列表时，另一个用户的问答不应该被阻塞。

**WAL 的代价**：
- 多两个文件（`-wal` 和 `-shm`）；
- WAL 文件会增长，需要 checkpoint（SQLite 自动做，但大量写之后可能膨胀）；
- **不能放在网络文件系统上**（NFS 不支持共享内存）。

最后一条是一个隐藏的部署约束：如果哪天要把 `data/` 挂到 NFS 或 SMB 上，WAL 会坏。

### `synchronous=NORMAL`

**默认是 `FULL`**：每次事务提交都 fsync。这保证了"提交即落盘"，但 fsync 是慢操作（机械盘 5~10ms，SSD 0.1~1ms）。

`NORMAL` 在 WAL 模式下的语义是：**WAL 文件写入但不 fsync，checkpoint 时才 fsync**。

**这意味着什么**：
- 操作系统崩溃（断电）→ 最近几个事务可能丢；
- **应用崩溃（进程被杀）→ 数据不丢**（因为数据已经写进内核的 page cache，WAL 的重放机制保证一致性）。

对于一个知识助手，"丢最近 1~2 秒的会话写入"是可接受的（用户可以重发）。而"每次写入慢 5ms"在 2 核机器上是明显的（一 次问答 4 次写 = 20ms）。

**这是一个显式的"持久性 vs 性能"交易。** 交易的前提是"数据可重建/可重发"——如果这是银行账本，必须 `FULL`。

### `busy_timeout=5000`

SQLite 的锁是**文件级**的（WAL 下是 WAL 文件的写锁）。当两个连接同时写：

- 默认行为：立即返回 `SQLITE_BUSY`（错误）；
- 设置 `busy_timeout`：等待最多 5000ms 再返回错误。

**没有这个设置，并发写会大量报错。** 而报错的地方可能是一个 `INSERT`（加消息），用户看到的是"发消息失败"。

5000ms 的选择：SQLite 的写锁持有时间取决于事务大小。本项目的写事务都很小（单条 INSERT/UPDATE），通常 <10ms。5000ms 是 500 倍的余量，足以覆盖"checkpoint 正在执行"这类长操作。

**代价**：如果真有一个长事务（比如批量插入 1000 条片段），其他写会等 5 秒然后失败。项目的批量写是走 Chroma 的（不走 SQLite），所以不会触发。

### 三个 PRAGMA 的配套关系

**它们不是独立选项，是一组。**

- 只开 WAL 不开 NORMAL：每次提交仍 fsync，WAL 的写性能优势被吃掉一半；
- 只开 NORMAL 不开 WAL：`synchronous=NORMAL` 在 DELETE 模式下语义不同（可能丢事务），且读写仍互斥；
- 都不开 busy_timeout：并发写直接报错。

**这是一个"配置组合"而非"配置项"的例子。** 很多项目只抄了 WAL，忘了另外两个，然后遇到"偶发的 database is locked"。

---

## 7.3 连接管理：为什么是线程本地而不是连接池

### 两种方案

| 方案 | 机制 | 适用 |
|---|---|---|
| **连接池** | 维护 N 个连接，借用/归还 | 网络数据库（连接开销大） |
| **线程本地复用** | `threading.local()`，每线程一个连接 | 嵌入式数据库（连接开销小，但 pragma 设置有成本） |

### 本项目的选择（`core/session.py:51-80`）

```python
# 每次操作都新建 sqlite 连接，等于每次重开文件 + 重设 WAL；一次问答会开 6~7 个，
# 累计几毫秒、还都在请求线程里。改成按线程复用：连接只建一次、pragma 只设一次。
# 路径参与判断是因为测试会 monkeypatch _db_path 到临时目录——路径一变必须换连接。
_LOCAL = threading.local()

def _conn() -> sqlite3.Connection:
    path = _db_path()
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "path", None) == path:
        return conn
    ...
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    ...
```

### 三个关键设计

**① 为什么"每次新建"是问题**

`sqlite3.connect(path)` 本身很快（约 0.1ms）。但紧接着三条 PRAGMA：

- `journal_mode=WAL` 需要读/写数据库头（如果已经是 WAL 则快速返回，但仍有一次 I/O）；
- `synchronous=NORMAL` 是连接级设置；
- `busy_timeout` 是连接级设置。

一次问答开 6~7 个连接 = 6~7 次 pragma 设置。累积"几毫秒"看起来不多，但：

- 这些都在**事件循环所在的线程**里（除非包了 `to_thread`）；
- 一次问答的预算里，几毫秒是可感知的（用户等待时间已经是 1.6s）。

**② 路径参与判断**

```python
if conn is not None and getattr(_LOCAL, "path", None) == path:
    return conn
```

为什么？因为**测试会 monkeypatch `_db_path` 到临时目录**。

```python
# tests 里大概这么写
monkeypatch.setattr(core.session, "_db_path", lambda: str(tmpdir / "test.db"))
```

如果缓存连接时不检查路径，测试会继续读写上一个库（开发者的真实 `data/sessions.db`）——**测试污染生产数据**。

这是一个非常隐蔽的风险，也是一个"测试友好性影响生产代码"的例子。

**③ CPython 线程与 async 的关系**

`threading.local()` 在 async 里的行为：**同一个线程上的所有协程共享同一个 `threading.local`**。

也就是说，如果两个协程在同一个线程里并发执行（FastAPI 的 async 端点就是这样），它们会**共享同一个 sqlite 连接**。

这是安全的吗？

- `sqlite3.Connection` 默认 `check_same_thread=True`，**不允许跨线程使用**；
- 但同一线程内多协程交替使用同一个连接，是可以的（不是并发，是交替）；
- 前提是**没有 `await` 跨越一个未提交的事务**。

检查代码：所有 `_get_conn()` 都是 `with` 块，块内没有 `await`（都是同步 SQL）。所以安全。

**但这是一个脆弱的保证**：如果有人写了

```python
with _get_conn() as conn:
    conn.execute("INSERT ...")
    await something()          # ← 危险！
    conn.execute("UPDATE ...")
```

在 `await` 期间，另一个协程可能用同一个连接执行了别的 SQL，导致事务边界混乱。

**这是一个需要通过代码评审守住的约束**，当前没有机制强制。理想做法是在 `_get_conn` 里加断言（检查是否在 async 上下文），但那需要运行时检测，成本较高。

### 与 `asyncio.to_thread` 的交互

`api/chat.py` 大量使用 `await asyncio.to_thread(store.get_messages, ...)`。`to_thread` 会把调用扔到**线程池**，于是在那个线程里创建/复用一个连接。

线程池默认 `min(32, cpu_count + 4)` 个线程。在 2 核机器上是 6 个线程。所以最多 6 个 SQLite 连接同时存在。

6 个连接 + WAL + busy_timeout 5s → 并发写时会有等待，但不会失败。这是可以接受的。

**注意**：连接永不关闭。`_LOCAL.conn` 在线程结束时才被 GC。线程池的线程是长驻的，所以连接也是长驻的。6 个文件句柄，可以接受。

---

## 7.4 两个数据库文件：一次跨库 JOIN 事故

### 事实

```
data/sessions.db    ← 会话 / 消息 / 反馈 / 用量 / 用户 / 画像 / 日报 / 委托 / 令牌黑名单
data/scheduler.db   ← schedule_jobs / job_runs / worker_heartbeat + APScheduler 的 apscheduler_jobs
```

**为什么分开？**

`core/scheduler.py:10-12`：

> 两个进程必须看到同一份事实，所以状态落 `data/scheduler.db`（与 sessions.db 同目录、不同文件——**同文件会让聊天写入和调度写入互相抢锁**）。

展开：SQLite 的写锁是**数据库级**的（WAL 下是 WAL 文件级）。如果会话和调度在同一个文件：

- 用户发一条消息（写 messages）→ 持有写锁；
- 同时 worker 要更新心跳（写 worker_heartbeat）→ 等锁；
- 如果用户消息写入慢（大数据量、checkpoint），worker 的心跳会延迟。

心跳延迟会触发一个更糟的后果：**心跳年龄超过 600s 判 down** → 告警。但其实 worker 活着，只是被锁住了。

所以分文件是为了**故障隔离**：聊天的负载不影响调度的心跳。

### 代价：跨库不能 JOIN

`README.md:169`：

> 调度库与会话库是**两个独立的 SQLite 文件** —— 跨库查表是本项目踩过的坑（`GET /api/delegate/{id}/runs` 曾因此 500）。

### 事故机制

`GET /api/delegate/{id}/runs` 要返回"某个委托的执行历史"。数据在两边：

- `delegations` 表在 `sessions.db`（委托定义）；
- `job_runs` 表在 `scheduler.db`（执行记录）。

一个自然的写法是：

```sql
SELECT r.*, d.name
FROM job_runs r
JOIN delegations d ON d.delegation_id = r.subject_id
WHERE r.subject_id = ?
```

SQLite **不支持跨数据库文件 JOIN**（`ATTACH` 可以，但那是另一个连接的事，且跨库事务不可靠）。

如果代码在 `scheduler.db` 的连接上执行这个 SQL，会得到 `no such table: delegations` → 500。

### 修复方式

必须**两次查询 + 应用层合并**：

```python
# 1. 从 scheduler.db 取 runs
runs = scheduler.list_runs(job_key, subject_id)
# 2. 从 sessions.db 取委托名
d = store.get_delegation(owner, delegation_id)
# 3. 合并
```

这比 JOIN 多一次往返（约 0.2ms），但正确。

**代价**：应用层合并意味着"分页"变复杂。如果 runs 有 10000 条，要在应用层做"取第 3 页的 20 条"。

当前实现大概是"先在 scheduler.db 做 LIMIT/OFFSET，再批量查委托名"。因为委托名只有一个（同一个 subject_id），所以不是 N+1 问题。

### 这个事故的教训

**"物理分开"是一个正确的决策，但它产生了一个"不能想当然"的约束。**

如果有人不熟悉这个约束，写了一个 JOIN，会在测试环境通过（如果测试用的是同一个库文件）而在生产 500。

**这是"环境差异导致测试失效"的例子**：测试可能 monkeypatch 到一个临时目录，两个 db 都在那里，但仍然是两个文件——所以测试应该能发现。除非测试只测了"空数据"路径（空数据时 JOIN 也会返回空，不报错？不，缺表一定报错）。

实际上缺表是**立即报错**的（`no such table`），所以测试只要跑到这个端点就会红。说明当时没有测试覆盖这个端点，或者测试用的 mock 绕过了 SQL。

**卷一的形态 6（假绿）的又一个变体：不是测试没跑，是测试没覆盖这条路径。**

---

## 7.5 表设计：8 张表的取舍

### 完整清单

| 表 | 库 | 行数量级 | 增长 | 关键索引 |
|---|---|---|---|---|
| `conversations` | sessions | 万 | 线性 | `(owner_id, updated_at DESC)` |
| `messages` | sessions | 十万 | 线性 | `(conversation_id, id)` |
| `feedbacks` | sessions | 千 | 线性 | 部分唯一 `(conversation_id, message_id)` |
| `usage` | sessions | 千 | 线性（按天） | 主键 `(user_id, day, kind)` |
| `user_tags` | sessions | 千 | 收敛（词表有限） | 主键 `(owner_id, tag)` |
| `user_profiles` | sessions | 百 | 收敛（每用户一行） | 主键 `owner_id` |
| `revoked_tokens` | sessions | 十 | **有上界**（exp 过期即删） | `exp` |
| `daily_reports` | sessions | 千 | 线性（按天） | `(owner_id, date)` |
| `delegations` | sessions | 十 | 收敛 | `(owner_id)` |
| `schedule_jobs` | scheduler | 6 | **固定** | 主键 `job_key` |
| `job_runs` | scheduler | 万 | 线性 | `UNIQUE(idem_key)` + 3 个辅助索引 |
| `worker_heartbeat` | scheduler | 1 | **恒定** | 主键 `id CHECK(id=1)` |

（严格说是 12 张表，但核心是前 8 张。）

### 索引设计的三个模式

**模式 1：复合索引按"过滤 + 排序"排列**

```sql
CREATE INDEX idx_conv_owner ON conversations(owner_id, updated_at DESC);
```

`owner_id` 在前（等值过滤），`updated_at DESC` 在后（排序列）。

这是因为 SQLite 的索引使用规则：**等值条件列必须在范围/排序列之前**。如果写成 `(updated_at DESC, owner_id)`，那么"按 owner 过滤 + 按时间排序"就无法用索引排序，需要额外排序。

**模式 2：主键当唯一约束用**

```sql
CREATE TABLE user_tags (
    owner_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    ...
    PRIMARY KEY (owner_id, tag)
);
```

复合主键 = 唯一约束 + 聚簇索引。这里不需要自增 id，因为 (owner_id, tag) 天然唯一。

`ON CONFLICT(owner_id, tag) DO UPDATE SET weight = weight + ?` 依赖这个主键。

**模式 3：部分唯一索引**

```sql
CREATE UNIQUE INDEX idx_fb_msg
    ON feedbacks(conversation_id, message_id)
    WHERE message_id IS NOT NULL;
```

`WHERE` 子句让唯一性只作用于"message_id 非空"的行。

**为什么需要**：`message_id` 可以是 NULL（前端在流式未结束时点了赞，还没有 id）。SQLite 的唯一索引对 NULL 的处理是"每个 NULL 都不同"，所以 NULL 行本来就不会冲突——那为什么要 `WHERE`？

因为**语义清晰**：`WHERE` 明确表达"只对已落库的消息做唯一性约束"。而且如果 `message_id` 从 NULL 变成非 NULL，唯一性才生效。

注释：

> 一条消息只保留最后一次反馈（点赞再点踩就是改，不是追加）

而实现（`add_feedback`）：

```python
if msg_id is not None:
    conn.execute("DELETE FROM feedbacks WHERE conversation_id = ? AND message_id = ?", ...)
conn.execute("INSERT INTO feedbacks ...")
```

**注意：实现用的是"先删再插"，不是 `ON CONFLICT DO UPDATE`。**

注释解释：

> SQLITE 的 upsert 对部分唯一索引（message_id IS NOT NULL）支持不好，两步写最稳

这是一个真实的 SQLite 限制：`ON CONFLICT` 目标必须匹配一个唯一索引，而部分索引的冲突目标语法支持有限（SQLite 的 `ON CONFLICT(columns) WHERE ...` 语法在旧版本不支持）。

**代价**：两步写不是原子的（如果进程在 DELETE 和 INSERT 之间崩溃，反馈会丢）。但在同一个 `with _get_conn()` 块里，两者在同一事务中（`_get_conn` 的 contextmanager 会 commit/rollback），所以是原子的。

**这是一个"看似不安全实则安全"的实现**——正确性依赖 `_get_conn` 的事务语义，而这个语义在另一个文件里。**隐含耦合**。

---

## 7.6 窗口截断：为什么在 SQL 层做

### 实现

```python
cap = limit_turns * 2 + 10
rows = conn.execute(
    f"SELECT * FROM (SELECT {cols} {join} ORDER BY m.id DESC LIMIT ?) ORDER BY id ASC",
    (sid, owner, cap),
).fetchall()
```

### 三个细节

**① 内层倒序 + LIMIT，外层正序**

这是"取最后 N 条"的标准写法。如果直接 `ORDER BY id ASC LIMIT N`，会取到**最早**的 N 条。

**② `cap = limit_turns * 2 + 10` 的 +10**

注释：

> 多留 10 行缓冲，避免 Agent 一轮产出多段 assistant 时把窗口起点切掉

展开：一轮对话通常是 1 user + 1 assistant = 2 条。但 Agent 模式下，一轮可能产出：

```
user
assistant (带 tool_calls)
tool
assistant (最终回答)
```

4 条。如果只取 `limit_turns * 2`，可能在中间切断。

+10 是 buffer，保证"取回的片段一定包含完整的轮次边界"。

**③ 截断在 Python 里再做一次**

```python
user_indices = [i for i, m in enumerate(msgs) if m["role"] == "user"]
if len(user_indices) > limit_turns:
    start = user_indices[-limit_turns]
    msgs = msgs[start:]
```

为什么 SQL 已经 LIMIT 了还要再截？

因为 SQL 的 LIMIT 是"按消息条数"，而这里要"按轮数"。`cap` 条消息可能包含 31 轮，需要精确截到 30 轮，且**必须从 user 消息开始**（不能从 assistant 中间开始，否则对话不完整）。

**这是一个"两层截断"的设计**：SQL 层限制数据量（内存），Python 层限制语义（轮次完整性）。

### 为什么不"全量载入再截断"

如果会话有 10000 条消息：

- 全量载入：约 10000 × 500 字 = 5MB 字符串 + 10000 个 dict 对象（约 10MB）；
- 在 2G 机器上，3 个并发请求就是 30MB。

而 `LIMIT 70` 只载入约 35KB。

**注释说得很对**："长会话全量载入既慢又占内存，而窗口截断只要最近 limit_turns 轮。"

### 代价

- 两层截断，逻辑分散在两处（SQL + Python），后人容易只看到一处。
- `cap` 的 +10 是一个魔数，没有配置化。

---

## 7.7 归属校验并进 JOIN

### 实现

```python
join = (
    "FROM messages m JOIN conversations c ON m.conversation_id = c.id "
    "WHERE m.conversation_id = ? AND c.owner_id = ?"
)
```

```python
# 归属校验并进查询：JOIN conversations 后按 owner 过滤，一次查询同时完成
# "验归属 + 取消息"，省掉一次独立的 EXISTS。
```

### 为什么不先 `_owned()` 再查

`_owned()` 是一次 `SELECT 1 FROM conversations WHERE id=? AND owner_id=?`，约 0.1ms。然后查消息又是一次。

合并成 JOIN 后：
- 少一次查询往返；
- **原子性更好**（如果两次查询之间会话被删，第二次会返回空，但不一致）。

### 但 `add_message` 仍然用 `_owned()`

```python
def add_message(owner, sid, role, content, refs=""):
    if not _owned(owner, sid):
        raise NotOwned(sid)
    ...
```

注释：

> 正常流程下 API 层已经用 get_conversation(owner, sid) 验过一次，这里再验一遍是"纵深防御"：消息写入比读取更危险，宁可多跑一次带索引的 EXISTS（约 0.1ms），也不要把写权限寄托在调用方记得校验。

**这是一个精彩的区分：读操作可以合并（性能优先），写操作要重复校验（安全优先）。**

为什么写入更危险？因为：
- 读到别人的数据是信息泄露；
- 写到别人的会话是**数据污染**（用户 A 的消息出现在用户 B 的对话里，两人都会看到错误内容）。

而且写入的错误更难修复（要找到污染的数据并清理）。

**"纵深防御"在这里不是口号，是一个明确的不对称决策。**

---

## 7.8 幂等：UNIQUE 约束而不是"先查再插"

### 问题

定时任务可能被重复触发：

- worker 重启 → 重新扫描"到点的任务"；
- web 的 `run-now` 与 worker 的扫描同时到；
- 重试机制再次执行。

如果每次都发一条消息，用户会收到重复的每日总结。

### 错误做法

```python
row = conn.execute("SELECT 1 FROM job_runs WHERE idem_key = ?", (key,)).fetchone()
if row is None:
    conn.execute("INSERT INTO job_runs ...")
```

**并发下必漏**：

```
T1: SELECT → 无结果
T2: SELECT → 无结果        ← 两个都以为没有
T1: INSERT → 成功
T2: INSERT → 成功          ← 重复
```

即使 SQLite 的写是串行的，两个 SELECT 可以在写之前都完成。

### 正确做法

```sql
CREATE UNIQUE INDEX ux_job_runs_idem ON job_runs(idem_key);

INSERT INTO job_runs (...) VALUES (...) ON CONFLICT(idem_key) DO NOTHING;
-- 判 rowcount：1 = 本次插入成功（首次执行），0 = 已存在（跳过）
```

`core/scheduler.py:13-17`：

> **幂等靠约束而不是靠查**：`job_runs.idem_key` 上建 UNIQUE 索引，登记一律 `INSERT ... ON CONFLICT(idem_key) DO NOTHING` + 判 rowcount。
> 「先 SELECT 看有没有、没有再 INSERT」在并发下必然漏（web 的 run-now 与 worker 的扫描可能同时到，两个都查到「没有」就都插），这与 `core/quota.py` 的 upsert 记账是同一套思路。

### `idem_key` 的构造

```sql
idem_key TEXT NOT NULL,   -- job_key|subject_id|planned_at
```

三段的含义：

- `job_key`：哪个任务（`report.daily` / `delegate.dispatch`）；
- `subject_id`：对谁（`delegation_id` 或 `date`）；
- `planned_at`：**计划触发时刻**（Unix 秒）。

第三段是最关键的。为什么用"计划触发时刻"而不是"执行时刻"？

因为"执行时刻"每次重试都不同，会导致 idem_key 不同 → 每次都插入 → 不幂等。

而 `planned_at` 是"这个任务本来应该在什么时候跑"，是稳定的。所以：

- 第一次执行：`planned_at = 2026-10-20 23:30:00` → key 唯一；
- 重试：同一个 `planned_at` → key 相同 → 被约束挡住。

**这是一个"用业务时间而非物理时间做幂等键"的设计**，非常正确。

### 一个隐含的问题

如果任务因为某种原因**在该时刻没有执行**，第二天补跑时，`planned_at` 应该是什么？

- 如果是"昨天的 23:30"，那就是补生成，idem_key 与昨天的一致（如果昨天没跑过，则能插入）；
- 如果是"今天补跑的时刻"，那就是新的 key，可能重复生成。

项目有 `report.backfill` 任务（"补生成过去 7 天内 failed / empty 的总结"），它的 `subject_id` 是日期，`planned_at` 大概是该日期的计划时刻。所以补生成是幂等的（同一日期的 key 固定）。

**这说明 idem_key 的设计是整个幂等机制的核心**，比约束本身更重要。约束保证"不重复插入"，而 key 的设计保证"什么是重复"。

---

## 7.9 乐观锁：压缩块写入

### 并发场景

上下文压缩是**回答结束后异步执行**的（`_schedule_compress`）。而下一轮请求可能立刻到达。

```
T1（异步压缩）: 读 compressed_upto=100，压缩 100~120 的消息
T2（下一轮问答）: 也在读历史，可能触发压缩
```

如果两个都写 `compressed_summary` / `compressed_upto`，会互相覆盖。

### 解法（`core/context_compress.py:241`）

> 为什么用乐观锁：压缩是回答结束后的异步动作，下一轮请求可能先到。

具体实现（`commit_summary`）：

```python
# 伪代码
UPDATE conversations
SET compressed_summary = ?, compressed_summary_prev = ?, compressed_upto = ?
WHERE id = ? AND owner_id = ?
  AND (compressed_upto IS NULL OR compressed_upto = ?)   -- ← 比较旧值
```

如果 `rowcount == 0`，说明 `compressed_upto` 被别人改过 → 放弃本轮压缩。

### 为什么乐观锁而不是悲观锁

- **压缩不是关键路径**：失败就下次再试，用户无感知。
- **悲观锁会阻塞**：`BEGIN IMMEDIATE` 会持有写锁，阻塞其他写入（包括用户的下一轮消息）。
- **冲突概率低**：压缩事件之间隔很多轮，两轮同时压缩的情况很少。

**"低冲突 + 失败可重试"是乐观锁的经典适用条件。**

### 代价

失败时放弃，可能导致"上下文一直没被压缩"（如果每轮都有并发）。但这不会导致错误，只是失去了压缩的收益。

**这是一个"宁可少压缩，也不能写坏"的决策**，与卷五的"宁可少滤几个，也不能误杀"同构。

---

## 7.10 JSON 文件 vs 关联表：三个 JSON 的取舍

### 三个文件

```
data/kb_names.json     { safe_kb_id: 中文名 }
data/kb_files.json     { kb_id: [{name, chunks, added_at}, ...] }
data/kb_owners.json    { kb_id: owner_id }
```

### 为什么不用表

| 维度 | JSON 文件 | SQLite 表 |
|---|---|---|
| 读写方式 | 整体读/整体写 | 单行增删改 |
| 查询 | 无（全在内存） | 任意 SQL |
| 并发 | 需要文件锁 + 原子写 | 数据库锁 |
| 崩溃恢复 | 依赖原子写（tmp+rename） | WAL |
| 内存 | 全量驻留 | 按需读 |

**本项目的场景**：

- `kb_owners`：查询模式是"给定 kb_id 查 owner"，本质是内存 dict 查找。kb 数量在百级。
- `kb_files`：查询模式是"列出某库的文件"，需要整个列表。
- `kb_names`：查询模式是"显示名"，全量载入。

**没有一个是"需要按条件筛选大量行"的场景。** 所以 JSON 更自然。

**关键理由**：这三个文件都是"注册表的影子"——`pipelines.py` 里的 `_KB_OWNERS` / `_KB_FILES` / `_KB_NAMES` 三个 dict 才是真正的读写入口，JSON 只是它们的持久化镜像。

如果用 SQLite，每次读还要"查表 → 转 dict"，而 dict 已经在内存里了。多一层转换，没有收益。

### 原子写

JSON 写入必须原子，否则进程崩溃会留下半个文件（JSON 解析失败 → 所有知识库都读不出来）。

标准做法：

```python
# 写临时文件 → fsync → rename（rename 在同一文件系统上是原子的）
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, ensure_ascii=False)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, path)     # 原子替换
```

`README.md` 提到 `kb/pipelines.py` 有"原子写文件清单"。所以这个模式被用了。

**这是一个容易漏的细节**：直接 `json.dump(open(path, "w"))` 在崩溃时会截断文件。而 `ensure_ascii=False` 是另一个细节（否则中文变成 `\uXXXX`，文件不可读）。

### 代价

- **没有事务**：三个文件之间可能不一致（比如 kb_owners 写了但 kb_files 没写）。
- **没有并发控制**：多个进程写会互相覆盖（单进程下靠 `_REGISTRY_LOCK` 保证）。
- **无查询能力**：如果将来要"按 owner 列出所有库"，需要遍历 dict（内存操作，快）。

**反转条件**：知识库数量超过 1000，或需要多机共享。

---

## 7.11 向量库与关系库的边界

### 两个存储的实际分工

| 数据 | 存哪 | 为什么 |
|---|---|---|
| 片段正文 | Chroma | 检索需要 |
| 片段 metadata（source/page/chunk_index/topics） | Chroma | 过滤需要 |
| 文件清单（文件名/片段数） | JSON | 展示需要，不需要检索 |
| 会话/消息 | SQLite | 关系查询、事务 |
| 调度状态 | SQLite | 事务、幂等约束 |
| 上传原文 | 文件系统 | 大文件，不需要检索 |

### 一个隐含的重复

`kb_files.json` 里的 `chunks` 数量，与 Chroma 里该 source 的片段数，是**同一事实的两份记录**。

`list_sources()` 的存在就是为了"从 Chroma 重新统计"（兜底数据源）。

**这是一个典型的"denormalization + 兜底"设计**：

- 正常路径：读 JSON（快）；
- 异常路径：从 Chroma 重算（慢但准）。

注释：

> 统计库内每个来源文件的片段数（文件列表记录的兜底数据源）。

**问题**：什么时候用哪个？如果 JSON 与 Chroma 不一致（比如入库中途失败），前端会显示错误的数量。

`pipelines.py` 的 `add_file_records` 大概是"入库成功后写 JSON"，所以正常情况下一致。但"入库中途失败不回滚"是已知缺口（规范里列了第 5 条）。

**这是一个"最终一致但中间状态可见"的设计**，适合"错误可见但不致命"的场景。

---

## 7.12 数据增长与容量规划

### 粗算

假设 100 个用户，每人 50 个会话，每会话 20 轮：

```
messages: 100 × 50 × 20 × 2 = 200,000 条
每条平均 500 字（UTF-8 中文 3 字节）= 1.5KB
总大小 ≈ 300MB
```

加上索引（约 30%）、WAL（约 10%）、Chroma（如果每人 1 个库、每库 1000 片 × 500 字 × 1536 维 float32 = 6MB/库，100 个库 = 600MB）。

**总计约 1GB。** 40G 磁盘可以撑很久。

### 但增长不是线性的

`messages` 是**永久的**——用户删会话才删消息。所以：

- 用户越活跃，增长越快；
- 没有 TTL、没有归档。

`规范/01` 里列了缺口："会话无总量上限（`core/session.py`），严重度：低"。

**为什么是低**：因为 40G 磁盘 + 200,000 条 = 300MB，还有 100 倍空间。

**什么时候变成高**：当单用户消息数达到百万级（约 1.5GB），SQLite 的 `COUNT(*)` 和全表扫描会变慢，且备份时间变长。

### 已知的清理机制

`job_runs` 有清理（`housekeeping` 任务："清理过期运行记录（>90 天）与历史上传件"）。

`messages` 没有。

**这是一个有意的取舍**：运行记录是"可丢弃的日志"，消息是"用户资产"。

---

## 7.13 备份与迁移

### 三个必须一起备份的路径

```
data/           ← sessions.db, scheduler.db, *.json, kb_sources/, chat_uploads/
chroma_db/      ← 向量库
.env            ← 密钥（不在 git 里）
```

第四个容易漏：`data/.auth_secret`（如果 `AUTH_SECRET` 没配，密钥会生成在这个文件里）。**丢了它，所有用户的登录态失效。**

`core/config.py` 的注释：

> 好处是重启不丢登录态；坏处是迁移机器时要把这个文件一起带走，否则所有人掉线一次。

### WAL 模式下的备份陷阱

直接 `cp data/sessions.db backup.db` 在 WAL 模式下**可能得到不一致的快照**：

- 主文件里是旧数据；
- 新数据在 `-wal` 文件里；
- 复制主文件时，`-wal` 里的内容丢失。

正确做法：

```bash
sqlite3 data/sessions.db ".backup backup.db"
```

`.backup` 命令会正确处理 WAL。

或者停服务后再 cp（但要同时复制 `-wal` 和 `-shm`）。

**这是一个"看起来没问题"的陷阱**：cp 出来的文件能打开、能查询，只是数据是旧的。恢复时才发现丢了最近的数据。

### 迁移

`tools/migrate_owner.py` 负责把遗留数据（`owner_id IS NULL`）指派给某个账号。

这个工具必须**显式运行**，不能自动。理由是卷三 §3.8 讲的"默认拒绝"。

---

## 7.14 什么时候必须换 PostgreSQL：判据清单

这是本卷最重要的一节，因为"SQLite 够用"这句话有明确的有效期。

### 五条判据（任一成立即需评估）

**判据 1：需要多机部署**

SQLite 是文件，不能跨机共享（NFS 上 WAL 会坏）。所以一旦需要"2 个应用实例"，就必须换。

**判据 2：写入并发超过单写锁的承受力**

SQLite 是单写者。如果写事务的平均持续时间 × 写入 QPS > 1，就会排队。

粗算：本项目的写事务约 1~2ms（单条 INSERT），所以理论上限是 500~1000 写/秒。实际受 `busy_timeout` 和 checkpoint 影响，能到 100~200 写/秒。

超过这个量级，`database is locked` 会开始出现。

**判据 3：单库文件超过 10GB**

SQLite 支持 TB 级，但：
- 备份时间线性增长；
- 索引重建（`VACUUM`）需要同等空闲空间；
- 故障恢复时间变长。

**判据 4：需要复杂的分析查询**

`GROUP BY` + 多表 JOIN + 窗口函数。SQLite 支持部分窗口函数（3.25+），但没有并行查询、没有列存、没有物化视图。

如果有"运营后台要看多维统计"，应该上 PG（或专门的 OLAP）。

**判据 5：合规要求**

等保、审计日志、行级权限、加密存储。SQLite 只有文件级加密（SQLCipher），没有行级。

### 迁移的成本

不是"改连接串"，而是：

1. **SQL 方言**：`ON CONFLICT` 语法兼容，但 `AUTOINCREMENT`、`PRAGMA`、`last_insert_rowid()` 都要改；
2. **连接管理**：从线程本地变成连接池（asyncpg 或 SQLAlchemy async）；
3. **迁移工具**：从 `ALTER TABLE ... try/except` 变成 Alembic（一次性把 12 张表的迁移历史补齐）；
4. **事务语义**：PG 的默认隔离级别是 READ COMMITTED，SQLite 是 SERIALIZABLE。乐观锁的实现要重新验证；
5. **备份/监控**：全套新增。

**估计 2~3 周**（含测试）。这是一个需要提前规划的手术，不是配置调整。

---

## 7.15 存储层的整体评价

### 一个"看起来原始"但实际正确的架构

外人看这个项目的存储层，会觉得"太土"：

- 手写 SQL；
- 没有 ORM；
- 没有迁移工具；
- 状态分散在 SQLite + JSON + 文件系统 + Chroma 四个地方。

但把每个选择的理由摊开，会发现它们服务于同一个目标：**让"数据在哪、是否一致、怎么恢复"这三个问题有唯一答案。**

| 数据 | 唯一答案 |
|---|---|
| 会话/消息 | `data/sessions.db`，WAL，`.backup` 备份 |
| 调度状态 | `data/scheduler.db`，UNIQUE 约束保证幂等 |
| 向量 | `chroma_db/`，每库一 collection |
| 注册表 | `data/*.json`，原子写 |
| 原文 | `data/kb_sources/`，按 source 命名 |

没有"缓存里也有一份"、没有"消息队列里有在途数据"、没有"Redis 里有临时状态"。所有状态都是**终态的、可枚举的**。

**这是单机系统最大的优势：你可以把整个系统的状态打包成一个 tar 包。**

### 三个真实的技术债

1. **没有 schema 版本号**。`_init_db()` 里的迁移是"累加式"的，靠 `try/except` 幂等。如果迁移逻辑出错（比如 ALTER 加了错类型的列），没有任何检测手段。
2. **跨库 JOIN 的约束只存在于注释和事故记忆里**。没有 lint 规则、没有测试防止"在新代码里写跨库 JOIN"。
3. **`messages` 无上限**。这是已知缺口，严重度被评估为低，但没有任何自动化的容量监控（`monitoring/` 里有磁盘告警，但不区分"是日志占的"还是"是数据库占的"）。

### 一条可迁移的经验

这一层最值得抄的是 §7.8 的幂等设计：

> **幂等靠约束，不靠查。**

这个原则适用于一切"至少执行一次"的系统：
- 消息去重（UNIQUE + ON CONFLICT）；
- 定时任务（idem_key）；
- 支付回调（订单号唯一索引）；
- 事件消费（offset + 唯一键）。

而它的前提是**先把"什么是重复"定义清楚**（idem_key 的三个字段）。定义不清，约束也救不了。

下一卷进入调度层。存储层解决"记住"，调度层解决"什么时候自动去做"——这是系统从"被动响应"变成"主动陪伴"的分界线。
