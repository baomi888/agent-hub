# 卷二 · 接入层：Web 框架与流式传输

> 本卷解决的问题：**一个 HTTP 请求进入系统时，第一层要替后面所有层挡掉什么。**
> 接入层的选型错误有个特点：它不会让你写不出功能，只会让你在某个深夜发现"所有接口都 404"。

---

## 2.1 FastAPI：为什么是它，以及"异步"在这个项目里的真实含义

### 问题域

这个后端要同时做四件事：

1. 提供 34+ 个 REST 端点（会话 CRUD、知识库管理、反馈、用量、调度、委托、总结）；
2. 提供 3 条 SSE 长连接流（RAG / Agent / hybrid）；
3. 上传并校验文件（多文件、20MB 上限、流式写盘）；
4. 在同一个进程里做重 CPU 与重 I/O 的混合工作（向量检索、图编译）。

### 候选面

| 框架 | 优势 | 在本项目的问题 |
|---|---|---|
| **Flask** | 最简、生态最广 | 同步模型。SSE 需要靠生成器 + `stream_with_context`，且一个长连接会占住一个线程；要跑并发必须上 gunicorn 多 worker 或 gevent |
| **Django + DRF** | 自带 ORM/Admin/迁移/权限 | 同步（ASGI 支持是后加的）；ORM 与"直接 sqlite3"冲突；Admin 对本项目无价值但带来大量启动开销；学习成本对三人团队过高 |
| **Sanic / Litestar** | 原生 async、性能好 | 生态与文档远小于 FastAPI；LangChain/Chroma 的集成示例几乎都以 FastAPI 为默认 |
| **FastAPI** | 原生 async + Pydantic 校验 + 自动 OpenAPI + `Depends` 依赖注入 + `StreamingResponse` | 需要自己处理同步库的线程化 |

### 判据

在这个项目里，"异步"这个卖点的真实价值被高估了，需要澄清：

**异步带来的不是吞吐，而是"长连接不占线程"。**

本项目的并发基线是"只读接口 8 并发 P50 33.7ms、SSE 问答 3 并发 P50 1.64s"。这个量级下，任何框架都能扛住。真正的问题在别处：

一次 SSE 问答持续 10~30 秒。如果是同步模型（每请求一线程），3 个并发问答就占 3 个线程，加上 gunicorn 的 worker 数限制（2 核常配 4~8），很快耗尽。而 async 模型下，30 秒的流只是一个挂起的协程，不占线程。

**所以 FastAPI 的选择理由不是"快"，而是"长连接便宜"。** 这是一个完全不同的论证，也是为什么在卷一会强调"性能上限在本项目权重很低"——如果按性能选，FastAPI 未必赢；按"长连接成本"选，它赢得很干净。

### 第二个理由：Pydantic 校验是防线，不是装饰

`api/chat.py:296-324` 的 `ChatRequest` 是这个理由的最好例子：

```python
top_k: int = Field(default=config.TOP_K_DEFAULT, gt=0, le=config.TOP_K_MAX)
mode: str = Field(default="auto", ...)
```

`gt=0, le=TOP_K_MAX` 这一行有两个作用：

1. **挡住"请求塞超大 top_k 拉爆检索与费用"**（`core/config.py:45-46` 的原话）；
2. 更重要的是，它把校验声明在**类型上**，而不是散落在 handler 里的 `if` 判断。

对比一下 Django REST Framework 的 serializer：能力类似，但 DRF 的 serializer 与 ORM 强耦合，而本项目不用 ORM，会变成"为了校验引入一个 ORM 生态"。FastAPI 的 Pydantic 模型是独立的，可以给非 ORM 的数据用——这是契合点，不是巧合。

### 第三个理由：`Depends` 让"默认安全"成为可能

这是本项目最值得抄的一条设计（`main.py:76-101`）：

```python
# 认证路由放最前面：它是唯一不带全局鉴权的（登录页总得能打开）。
# 其余四个业务 router 都在各自文件里挂了 dependencies=[Depends(get_current_user)]，
# 做成"默认要登录"——新增端点时忘了写鉴权，默认也是安全的那一侧。
```

`APIRouter(dependencies=[...])` 的语义是：这个 router 下的所有端点都自动带上该依赖。于是"忘记加鉴权"这个失误的后果从"数据泄露"变成"接口 401"——**失误的方向被翻转了**。

这个模式在 Flask/Django 里也能实现（蓝图级 before_request / 中间件），但在 FastAPI 里它是**类型系统的一部分**，更难绕过。而且 `Depends` 的返回值直接注入 handler 签名：

```python
async def chat_stream(req: ChatRequest, request: Request, uid: str = Depends(get_current_user))
```

`uid` 是一个普通的 `str` 参数，handler 里不可能"忘记取当前用户"——因为它就在签名里。这比"从 `g.user` 或 `request.user` 里读"要难出错得多。

### 决策与代价

**决策**：FastAPI + uvicorn，单进程，`StreamingResponse` 手写 SSE。

**代价**：

1. **同步库必须手工线程化**。这是全项目最大的一条纪律负担，见 §2.8。
2. **没有 Admin、没有迁移工具**。表结构靠 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN` 的 try/except。这在 8 张表时可行，在 50 张表时会失控。
3. **没有版本化 API**。ADR D-005 把 `/api/v1` 挂起了，理由是"合分支是从 `/api` 迁 `/api/v1` 成本最低的时机"。这是一个**有明确窗口期的债**。

### 反转条件

当出现以下任一情况，应重新评估：

- 端点数量超过 80 个，且开始出现 v1/v2 并存的真实需求；
- 单进程内存接近上限（约 1.4G），必须多副本——此时 FastAPI 本身不是问题，但进程内状态是（见卷一 §1.6）；
- 需要 GraphQL 或 gRPC 作为一等接口。

---

## 2.2 为什么不用 SQLAlchemy ORM

这是一个容易被误读的决策。它不是"反 ORM"，而是"在这个规模下 ORM 的收益<成本"。

### ORM 提供什么

1. 模型定义与迁移；
2. 关系导航（`user.conversations[0].messages`）；
3. 查询构造器（避免手拼 SQL）；
4. 跨数据库方言。

### 本项目需要什么

看实际的数据访问模式（`core/session.py`）：

- `get_messages`：一条带 JOIN 的窗口截断查询，`SELECT * FROM (SELECT ... ORDER BY m.id DESC LIMIT ?) ORDER BY id ASC`；
- `find_turn_ids`：先拉全表 id+role，再在 Python 里按角色分段；
- `feedback_stats`：两次聚合查询（GROUP BY rating + 最近 N 条）；
- `bump_usage`：一条 `INSERT ... ON CONFLICT DO UPDATE SET count = count + excluded.count`。

这四条里，有两条是 ORM 表达起来比 SQL 更别扭的（窗口子查询、原子 upsert），另外两条是简单到 ORM 没有价值。

而 ORM 的收益项：

- 关系导航：本项目**没有复杂关系**。`messages` 只有 `conversation_id` 一个外键，`feedbacks` 只有 `conversation_id` + `message_id`。没有多对多，没有继承，没有聚合根。
- 迁移：本项目的迁移是"给老库补列"，用 `try: ALTER TABLE ... except: pass` 解决了。Alembic 的收益在"多人并行改 schema 且需要回滚"时才显现，本项目三个人里只有一个人碰 `session.py`（交叉区需协商）。
- 方言：只用 SQLite。

### 真正的理由：SQL 是审计面

这一点没写在注释里，但可以从代码风格看出来。

`core/session.py` 的每一条 SQL 都带一段注释解释"为什么这么写"：

```python
# 只取尾部：长会话全量载入既慢又占内存，而窗口截断只要最近
# limit_turns 轮。多留 10 行缓冲，避免 Agent 一轮产出多段 assistant
# 时把窗口起点切掉；倒序取再翻转，等价于"全量后截断"。
```

如果这段逻辑藏在 ORM 的 `slice(0, N)` 里，注释就无处可写，而且"倒序取+翻转"这个关键的实现细节会被抽象掉——后人只会看到"取最近 30 轮"，看不到"为什么要 +10 缓冲"。

**判据**：当一个系统只有 8 张表、单一数据库、两人维护时，手写 SQL 让**每一条查询都是可审计的文本**。ORM 的价值在"SQL 太多写不过来"时才开始为正。

### 代价与反转条件

**代价**：
- 参数化必须手工保证（`?` 占位符）。这是安全的，但需要纪律。
- 没有类型提示，`dict(row)` 之后字段名拼错只能运行时发现。项目用 `row_factory = sqlite3.Row` 缓解（返回 dict-like 对象，字段名拼错会 KeyError 而非静默 None）。
- 表结构变更没有版本记录，只能靠 `_init_db()` 里的迁移块累加。

**反转条件**：表数超过 25 张，或需要跨用户/跨租户的复杂查询（如运营后台的多维筛选），或需要同时支持 SQLite 与 PostgreSQL（比如客户私有化部署要求 PG）。

---

## 2.3 单进程 uvicorn：一个被迫但正确的约束

### 问题

uvicorn 可以 `--workers 4`。为什么不用？

### 答案：8 项进程内状态会语义错

卷一 §1.6 列了 8 项进程内状态。挑三个最致命的展开：

**① 限流会变成 N 倍额度。** `core/quota.py::_HITS` 是 `dict[uid, list[timestamp]]`。4 个 worker 就是 4 份独立计数，用户在 4 个 worker 间轮转，实际额度是 4×10=40 次/分钟。

**② 检索缓存会碎片化。** `_RETRIEVE_CACHE` 是 200 条的 LRU。4 个 worker 各自维护 200 条，命中率降到约 1/4，embedding 费用直接上升。这不是理论问题——embedding 是**按 token 真实计费**的。

**③ Agent 图缓存会重复编译。** `_AGENT_CACHE` 最多 32 个编译图。4 个 worker 各编译一遍，冷启动时的 CPU 峰值 ×4，在 2 核机器上会直接把首屏拖到十几秒。

还有一个更隐蔽的问题：**`threading.Lock` 只在进程内有效**。`_BM25_COND`、`_REGISTRY_LOCK`、`_AGENT_LOCK` 全部假设单进程。多 worker 下这些锁形同虚设，BM25 single-flight 会退化成 N 个并发全库读取。

### 所以这是一个"被约束决定的正确"

单进程本身是缺点（不能利用多核、一个 worker 卡住全站卡住）。但在这个项目里，它把"分布式一致性"这个巨大的复杂度问题，**彻底消除**了。

对比一下如果要支持多 worker 需要做什么：

| 状态 | 单进程方案 | 多副本方案 |
|---|---|---|
| 限流窗口 | 内存 dict | Redis + 原子操作（或 Lua 脚本） |
| 检索缓存 | LRU | Redis + 序列化 Document 对象 |
| BM25 索引 | 进程内 | 共享内存 / 独立索引服务 |
| Agent 图 | 进程内 | 每 worker 编译（可接受） |
| 激活集 | 进程内 | Redis Hash |
| Chroma 客户端 | 单例 | 每 worker 一个 client（sqlite 多写风险！） |

最后一行是杀手：**Chroma 的 PersistentClient 底层是 sqlite，多进程写入会锁竞争**。这不是加 Redis 能解决的，得换 Chroma Server 模式或换向量库。

**结论**：单进程不是省事，是保命。这个决策的有效期，与"向量库是否换掉"绑定。

---

## 2.4 SSE 手写 vs 框架封装 vs WebSocket

### 三种主流方案

| 方案 | 说明 | 在本项目的问题 |
|---|---|---|
| **WebSocket** | 全双工 | 需要额外的连接管理、心跳、重连；而本项目是**单向**推送（客户端发一次请求，服务端推 N 帧）。用全双工做单向是过度设计，且 WS 在某些代理/防火墙下会被拦 |
| **sse-starlette** | 封装好的 `EventSourceResponse` | 提供 ping、自动重连、事件格式化的便利。但本项目的 9 种事件类型 + 帧级断连检测 + 自定义 headers，用框架反而要绕 |
| **手写 `StreamingResponse`** | 自己拼 `event: xxx\ndata: {...}\n\n` | 全部控制权在自己手里；代价是格式、缓冲、断连都要自己管 |

### 本项目实际需要什么

看 `api/chat.py` 头部的事件协议注释，一共 12 种事件：

```
status / tool / sources / grounding / token / reasoning / title / done / error
hybrid_start / workflow_node / mode_switch   ← 仅 hybrid 模式
```

其中几个是**领域特有**的：

- `grounding { strength, best, weak }` —— 依据强度，前端要在正文流出来**之前**挂提示条。这是一个强时序要求：它必须排在第一个 `token` 之前，否则用户已经开始读正文了才看到"依据较弱"。
- `workflow_node { phase: "enter|exit", node_id }` —— 工作流节点进出，用于前端画执行轨迹时间轴。
- `mode_switch { rule, reason, from, to }` —— 混合模式的切换记录。

`sse-starlette` 的 API 是按"消息"抽象的，要表达"这一帧必须赶在 token 之前"只能靠调用顺序，框架不会帮你保证。而手写实现里，这就是**代码顺序**——`yield _sse("grounding", ...)` 写在 `yield _sse("token", ...)` 前面，读代码就能看到时序。

### 三个关键的手写细节

**① `_sse()` 只有三行，但它决定了协议形状**

```python
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

`ensure_ascii=False` 很关键：默认 `True` 会把中文转成 `\u4e2d\u6587`，传输量翻 6 倍且调试时不可读。

**② headers 里有两个非直觉项**

```python
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # ← 关键
    "Connection": "keep-alive",
}
```

`X-Accel-Buffering: no` 是给 nginx 看的。nginx 默认会缓冲上游响应，SSE 流会被攒成一大块一次性发出——表现为"等待 10 秒，然后全部内容瞬间出现"。这个头是关闭 nginx 缓冲的标准方式。**没有它，整个流式体验为零**。

这条在 `https/agent-hub.conf.template` 里还有第二道保险（SSE 独立 location 关缓冲），说明这是一个被实际踩过的坑。

**③ 为什么不用 `EventSource`，而用 fetch + 手动解析**

浏览器的 `EventSource` API 有两个硬限制：只能 GET、不能带自定义头。本项目要 POST（带 question/mode/attachments）、要支持 `X-Auth-Token`（移动端场景）。所以前端用 `fetch` + `ReadableStream` 手动解析 SSE 帧（`frontend/src/lib/sse.ts`）。

这是一个典型的"标准协议 + 非标准客户端"组合：服务端发的是合法 SSE，客户端不用 `EventSource`。很多人会误以为这需要服务端改协议，其实不需要。

### 反转条件

- 需要服务端主动推送（不是响应某次请求），比如"定时任务完成后通知前端"——那 WebSocket 或轮询更合适；
- 需要断线续传（网络抖动后从第 37 个 token 继续）——当前协议做不到，需要在 `done` 帧里靠 `answer` 全量兜底（这也是为什么 `done` 要带完整 answer，虽然平时用不上）。

---

## 2.5 为什么只发 delta

### 事故级的性能问题

早期实现每帧回传**累计全文**。假设回答 1000 个 token：

- 第 1 帧传 1 个 token，第 2 帧传 2 个……第 1000 帧传 1000 个；
- 总传输量 = 1+2+...+1000 = **500,500 个 token 量级的文本**；
- 是实际内容的 **500 倍**。

这在本地开发时不明显（localhost 带宽无限），一上线就变成"用户看着字一个一个蹦，但网卡在狂转"。

### 修复

```python
acc.text += delta            # 服务端自己累加（用于落库和 done 帧）
yield _sse("token", {"text": delta})   # 只发增量
```

前端对应地做累加。协议里明确写了这一点（`api/chat.py:19`）：

> `token` `{ text: "..." }` 增量 token，前端自己累加（`text` 不是全文）

**为什么注释要特别强调"不是全文"**？因为这是一个协议级的语义约定，一旦前端理解错了，表现是"每个字重复出现 N 次"——而不是报错。又是一个静默失败。

### 代价

前端必须维护状态（累加缓冲）。这在前端是自然的（React state），但如果未来要做"服务端渲染首屏 + 客户端接管流"，delta 协议会让切换点变复杂——因为服务端渲染时拿不到"半截流"。

`done` 帧里带完整 `answer` 就是为这个场景留的后路：**断线/刷新后可以靠 done 帧的 answer 恢复完整内容**。对于思考过程同理（`reasoning` 字段带完整思考文本）。

---

## 2.6 断连检测：为什么是每 16 帧

```python
if (seen == 1 or seen % 16 == 0) and await request.is_disconnected():
    return
```

### 不检测会怎样

用户关掉页面，服务端还在生成。1000 token 的回答继续烧完，`store.add_message` 照常落库。用户下次进来看到一条自己没看过的完整回答，账单上多了一次完整调用。

### 为什么不是每帧

`request.is_disconnected()` 不是免费的——它要读 ASGI 的 receive channel，涉及一次 `await` 和状态检查。对一个 1000 帧的回答，逐帧调用就是 1000 次额外开销。

### 为什么是 16

注释里没写死理由，但从数量级可以推：16 帧大约对应"两百来个汉字"，在正常生成速度下是 100~300ms。也就是说"客户端走了"最多晚 300ms 被发现，代价是省下 984 次检查。

**这是一个典型的"把开销摊到可接受的延迟预算里"的决策**。它的隐含前提是：单帧成本 << 一次 `is_disconnected()` 的成本。如果未来换成极快的模型（每帧 1ms），16 帧 = 16ms，检测就会变成主要开销，此时应该改成按时间间隔（比如每 200ms）而不是按帧数。

### 一个容易忽略的点：首帧必须查

`seen == 1` 是特例。原因是：如果用户在请求发出后立刻关闭页面（比如误点），第一帧就应该发现，而不是等 16 帧。这是"冷启动路径要单独优化"的一般规律。

---

## 2.7 尾斜杠黑洞：一次完整的架构事故复盘

这是本项目最有教学价值的事故，因为它是**三个正确设计叠加出的错误**。

### 三个正确设计

1. **前端不直连后端，走 Next.js rewrite 代理**。正确——解决 CORS、Cookie 域、部署时的端口暴露。
2. **FastAPI 默认 `redirect_slashes=True`**。正确——`/api/sessions` 和 `/api/sessions/` 都能用，容错。
3. **Next.js 默认去尾斜杠（trailingSlash: false）**。正确——SEO 与 URL 规范化的标准做法。

### 叠加后的结果

```
浏览器: POST /api/sessions/          （前端代码写的是带斜杠，与后端路由一致）
   ↓
Next.js: 308 → /api/sessions          （去尾斜杠，这是它的默认行为）
   ↓
FastAPI: 307 → /api/sessions/         （补尾斜杠，Location: http://localhost:8000/api/sessions/）
   ↓
浏览器: 尝试连接 localhost:8000       ← 这是用户自己的电脑，必然失败
```

致命点在第三步：`Location` 头里的 host 是 **Next 代理改写后的 Host**（`localhost:8000`），而不是用户看到的域名。浏览器拿到这个 307，会去连自己机器的 8000 端口。

### 症状

`main.py:104-114` 的注释写得很准确：

> 症状：页面能打开，但「新建会话 / 发消息 / 所有 /api/* 调用」全部失败。

注意"页面能打开"——因为页面是 Next 渲染的，不走代理。只有 `/api/*` 走代理，所以只有 API 全挂。这导致排查方向极易跑偏：前后端都"活着"，`/health` 也是 200，但功能全废。

### 修复：双管齐下

**第一步：关掉 FastAPI 的自动重定向**

```python
app = FastAPI(redirect_slashes=False, ...)
```

**第二步：自己归一尾斜杠**（`main.py:159-179`）

```python
@app.middleware("http")
async def normalize_api_slash(request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/api/") and path != "/api/":
        has_slash = path.endswith("/")
        alt = path[:-1] if has_slash else path + "/"
        cache = getattr(request.app.state, "api_paths", None)
        if cache is None:
            cache = _collect_api_paths(request.app.routes)
            request.app.state.api_paths = cache
        ...
        # 当前形式命中不了、但翻一个尾斜杠就能命中时，归一到能命中的那一种
        if not matches_any(path) and matches_any(alt):
            request.scope["path"] = alt
```

这段代码有三个精妙之处：

**① 只归一"能命中"的路径**。如果两种形式都不命中，就原样放过（让 404 正常发生）。这避免了"把所有路径都强行改成某个形状"的粗暴做法。

**② 路由集合缓存在 `app.state`**。不在每次请求时重建——因为 `_collect_api_paths` 要遍历路由树，包括 `_IncludedRouter` 的 `effective_route_contexts()`。

**③ `raw_path` 也要改**

```python
if request.scope.get("raw_path") is not None:
    request.scope["raw_path"] = alt.encode()
```

这是极易漏的一行。ASGI 的 `scope` 里同时有 `path`（解码后）和 `raw_path`（原始字节），某些中间件/框架读后者。只改一个会导致"某些组件看到 A，某些看到 B"。

### 这段代码里藏着一个 FastAPI 内部细节

```python
def _collect_api_paths(routes: list) -> set[str]:
    """注意：include_router 进来的路由是 _IncludedRouter，其 .path 为 None，
    真实 APIRoute 收在 effective_route_contexts() 里、path 是相对前缀的，
    必须用 route_path 才能拿到带前缀的完整路径。
    """
    p = getattr(r, "route_path", None) or getattr(r, "path", None)
```

`_IncludedRouter`、`effective_route_contexts()`、`route_path` —— 这三个都不是 FastAPI 公开文档里的 API。这是靠读源码 + 调试找到的。

**这个细节说明了一个通用问题**：当你需要"反射"一个框架的内部状态时（路由表、依赖图、中间件栈），你必然会碰到私有 API，而私有 API 会在小版本升级时静默改变。所以这段代码的正确性依赖 `fastapi>=0.115` 这个宽松约束——**这是一个未被测试覆盖的版本风险**。

### 教训

1. **重定向是跨组件协议**，任何一层的重定向都可能把 host 改坏。在代理架构里，能不用重定向就不用。
2. **"三个正确设计"不等于"一个正确系统"**。需要在集成点做显式的冲突检查。
3. **症状与根因的距离**：症状是"新建会话失败"，根因是"三个框架的默认值叠加"。中间隔着 Next、nginx、FastAPI 三层。这也是为什么 `tests/test_slash_no_redirect.py` 存在——它不测"新建会话"，它直接测"不要 307"。

---

## 2.8 后端只听回环：一条被质疑最多的决策

```python
uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
```

### 三条理由（`main.py:222-226`）

1. 前端本来就靠 Next 的 `/api/*` rewrite 访问，后端不需要对公网开放；
2. 少一层暴露面；
3. **8000 直连会绕过 Next 的 Cookie 域设置，容易踩登录态丢失的坑**。

第三条是最实质的。展开说：Cookie 是按 host 存的。用户访问 `http://服务器IP:3000`，Next 在 `3000` 的响应里 `Set-Cookie`，Cookie 域是"服务器IP"（不带端口）。如果用户绕过 Next 直接访问 `http://服务器IP:8000`，Cookie 还是会带（同 host）；但如果后端自己也 `Set-Cookie`，就可能出现两份同名 cookie、路径不同、互相覆盖。

更麻烦的是 CORS：直连 8000 时，前端是 `:3000`、后端是 `:8000`，属于跨域，需要 `allow_credentials=True` + 精确 origin。而 `allow_origin_regex` 写的是 `^https?://(localhost|127\.0\.0\.1)(:\d+)?$` —— **只匹配 localhost，不匹配公网 IP**。所以公网直连 8000 时，CORS 会直接拒绝带凭证的请求。

也就是说：**这个系统在设计上就不支持"前端和后端分域名部署"**。如果将来要做（比如前端上 CDN、后端独立），需要改的东西比想象的多：CORS 白名单、Cookie 的 `SameSite=None; Secure`、`X-Forwarded-Proto` 信任链、CSRF 防护。

### 代价

- 无法做"后端独立扩容"；
- 无法把后端暴露给第三方（比如开放 API）；
- 压测必须走 3000 端口（多一跳 Next），测出来的延迟包含代理开销。项目里的压测脚本 `tools/stress_live.py` 就是打 3000 的。

### 反转条件

需要对外开放 API（第三方接入、移动端 App 直连）时。此时必须同时做：独立鉴权（Bearer token 而非 Cookie）、CORS 精确白名单、速率限制按 API key、以及 `--proxy-headers` 的正确配置。

---

## 2.9 429 用全局异常处理器：一个"错误分类"问题

### 代码

```python
@app.exception_handler(quota.RateLimited)
async def _on_rate_limited(request, exc) -> JSONResponse:
    return JSONResponse(status_code=429, content={...}, headers={"Retry-After": ...})

@app.exception_handler(quota.QuotaExceeded)
async def _on_quota_exceeded(request, exc) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": ..., "kind": exc.kind, "limit": exc.limit})
```

### 理由（`main.py:182-184`）

> 注册成全局异常处理器，而不是在每个端点里 try/except：端点自己 catch 容易顺手把它转成 500，前端就分不清"系统出错"和"你今天用超了"。

这句话点出了一个普遍问题：**HTTP 状态码是产品语义，不只是技术语义**。

- 429 + `Retry-After` → 前端显示"操作太频繁，请 12 秒后再试"，并禁用按钮 12 秒；
- 429 + `kind: "embed", limit: 1000` → 前端显示"今日向量化额度已用尽（1000），明天再来"；
- 500 → 前端显示"服务器出错了，请稍后重试"，并建议刷新。

三种情况对用户的含义完全不同，对前端的处理也完全不同。如果端点自己 catch 成 500，这三条路径就合并成一条——**信息被销毁了**。

### 一个细节：`Retry-After` 要 +1

```python
headers={"Retry-After": str(int(exc.retry_after) + 1)}
```

`retry_after` 是浮点秒数（比如 12.3）。HTTP 的 `Retry-After` 只接受整数秒。向下取整会得到 12，但 12 秒时窗口还没滑完（还差 0.3 秒），用户重试还是 429。所以向上取整——`int()+1` 对正数是向上取整的近似。

这是一个 3 个字符的 bug 修复，但它决定了"用户重试能不能成功"。

### 代价

全局处理器是"最后一道网"。如果某个端点自己 `except Exception` 把 `RateLimited` 吞了，全局处理器就不会触发。这需要纪律：**不要在端点里 catch 宽泛的 Exception**。

项目里有几处 `except Exception` 确实会吞掉配额异常的风险点（比如 `_hybrid_candidates` 的 try/except），但那些位置不在配额调用路径上，暂时安全。这是一个需要定期审查的模式。

---

## 2.10 上传：分块写盘与"整批先校验"

### 两个设计

**① 流式写盘，不整份读内存**

```python
# api/chat.py:399-401
# 分块写盘：以前是 await f.read() 整份读进内存再判大小，
# 1GB 的附件在 2G 内存的机器上等判出来时进程已经被 OOM killer 带走了
size = await upload.save_stream(f, path, MAX_CHAT_UPLOAD_MB * 1024 * 1024)
```

这是 §1.2 约束 1 的直接产物。`await f.read()` 的语义是"把整个文件读进内存"，20MB 的图片 ×9 张 = 180MB，在 2G 机器上是一次危险的峰值。

`save_stream` 的实现逻辑是：分块读 → 累计大小 → 超限立即中止 → 删除半截文件。关键在"超限立即中止"：不能先写完再判断，因为攻击者可以传 10GB。

**② 整批先校验再落盘**

```python
# 整批先校验再落盘：有一张不合格就整个拒绝，
# 否则前面几张写好了、后面报错，会留下一半的孤儿文件
rejected = [f.filename for f in files if not _is_supported_image(...)]
if rejected:
    raise HTTPException(400, ...)
```

这是"事务性"的手工实现。文件系统没有事务，所以用"先全验、再全写"的顺序来保证要么全成、要么全不成。

注意这里还有一层：`_is_supported_image` 同时检查扩展名和 content-type。为什么要两个？因为：
- 只看扩展名：改个后缀就能传任意文件；
- 只看 content-type：客户端可以随意伪造；
- 两个都看：提高了门槛，但仍不防"真的图片里藏 payload"——不过本项目不执行图片，只喂给视觉模型，所以风险可控。

### 而真正的防线在读取侧

```python
# api/chat.py:435-441
def _resolve_upload_path(path: str) -> str | None:
    """attachments[].path 完全由客户端提供，直接 open() 等于开放任意本地文件读取
    （甚至可以把 .env / sessions.db 喂给第三方视觉模型）。"""
    root = os.path.realpath(CHAT_UPLOAD_DIR)
    real = os.path.realpath(path)
    if real == root or real.startswith(root + os.sep):
        return real
```

这是全项目最重要的安全函数之一。`attachments` 是请求体里的 JSON，`path` 是客户端给的字符串。如果直接 `open(path)`，攻击者可以：

```
POST /api/chat/stream
{"attachments": [{"type": "image/png", "path": "/root/agent-hub/.env"}]}
```

然后 `.env` 里的 DeepSeek key 就进了第三方视觉模型的请求体。这是一个不返回给攻击者、但泄露给第三方的**侧信道泄露**——比直接返回文件内容更隐蔽。

修复用 `realpath` 前缀校验，正确处理了 `../`、软链接、符号链接。`os.sep` 的拼接细节也对（防止 `/data/chat_uploads_evil` 被误判为在 `/data/chat_uploads` 下）。

**这条经验值得单独记**：任何"客户端提供路径，服务端读文件"的设计，`realpath` 前缀校验是底线。更安全的做法是"客户端只给 id，服务端自己拼路径"——本项目在知识库删除那里就是这么做的（用 `source` 文件名而不是路径）。

---

## 2.11 中间件顺序：一个没写但必须成立的隐含约定

FastAPI 的中间件是**洋葱模型**，后加的在外层。本项目的注册顺序是：

```
1. CORSMiddleware          （add_middleware，在 app 创建后立即）
2. normalize_api_slash     （@app.middleware("http")，装饰器形式）
```

执行顺序（请求进入时）：`normalize_api_slash` → `CORS` → 路由。

这个顺序是否正确？

- **尾斜杠归一必须在 CORS 之前**吗？其实无所谓，因为 CORS 只看 origin/headers 不看 path。
- **CORS 必须在异常处理之前**吗？关键。如果 CORS 在异常处理器外面，那么 429/500 响应的 `Access-Control-Allow-Origin` 头会被加上。本项目同源部署，实际不走 CORS 预检，所以这个问题不会暴露——但如果哪天改成跨域部署，**429 响应缺少 CORS 头**会导致前端拿不到错误信息（fetch 直接抛 network error）。

这是一个典型的"当前配置下正确，换配置就错"的隐含假设。卷十二会把它归入"未标定边界的决策"。

---

## 2.12 接入层的整体评价

把这一卷的九个决策放在一起看，会浮现出一个模式：

| 决策 | 表面理由 | 真实理由 |
|---|---|---|
| FastAPI | 异步性能好 | 长连接不占线程 + Pydantic 校验 + Depends 默认安全 |
| 不用 ORM | 轻量 | SQL 可审计 + 无复杂关系 |
| 单进程 | 简单 | 8 项进程内状态的语义正确性 |
| 手写 SSE | 灵活 | 12 种领域事件的时序控制 |
| 只发 delta | 省带宽 | 避免 O(n²) |
| 16 帧检测 | 省开销 | 延迟预算换 CPU |
| 关 redirect_slashes | 修 bug | 重定向是跨组件协议，必须收敛到一处 |
| 只听回环 | 安全 | Cookie 域与 CORS 的一致性 |
| 全局 429 处理器 | 规范 | 错误分类是产品语义 |

**共同点：没有一条是"因为某个技术更快/更流行"。全部是"因为某个技术在某条具体路径上不会静默出错"。**

这就是卷一那条主线在接入层的体现。

最后一个观察：接入层是**唯一一层几乎没有技术债的层**。版本锁不在这一层（在 LLM/向量库），双轨不在这一层，口径漂移也不在这一层。原因是这一层最接近"物理事实"——HTTP 协议、ASGI 规范、浏览器的 Cookie 规则，这些都是稳定的，不会被框架版本改变。

**启示**：离协议越近的层，决策越稳定；离框架越近的层，决策越容易过期。所以架构上的力气应该花在"把业务逻辑推离框架"——这也是为什么本项目的业务核心（`service/daily.py`）是纯函数，不 import 任何 web 框架。

下一卷进入身份层。如果说接入层决定"请求能不能进来"，身份层决定"进来的请求能看到什么"——这是本项目从单用户 demo 变成多用户系统的分水岭。
