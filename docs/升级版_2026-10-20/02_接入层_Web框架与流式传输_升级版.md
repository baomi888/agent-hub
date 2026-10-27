# 02_接入层_Web框架与流式传输_升级版（2026-10-20）

> 对应研讨卷：`docs/技术选型研讨_2026-10-20/02_接入层_Web框架与流式传输.md`
> 本卷回答的问题：从校赛到省赛，这一层要改什么、为什么现在必须改、改完怎么验。
> 本卷范围：SSE 稳定性（断连/心跳/超时）、多 worker 下的流与配额、429 与 Retry-After 的对外契约、API 版本化与 OpenAPI、限流配额的对外可见性、8000 只绑回环与 nginx 反代的收口。

---

## 0. 一句话结论

接入层的业务逻辑不需要改，需要改的是**对外契约的完备性**：补 SSE 心跳与客户端超时兜底、给两类 429 都带 `Retry-After`、用 `X-RateLimit-*` 暴露限流状态、把 OpenAPI 与版本号做成可展示的文档面、HTTPS 上线时把公网入口从 3000 收口到 nginx 443。**这五件都不涉及重做，但每一件都直接对应"评审能否自己验到"**。

---

## 1. 现状盘点

### 1.1 应用与中间件

| 组件 | 现状 | 出处（文件:行） |
|---|---|---|
| FastAPI 实例 | `redirect_slashes=False`，`version="0.3.0 (W3)"` | `main.py:53-62` |
| CORS | 白名单只含 localhost；`allow_credentials=True` | `main.py:65-76` |
| 路由挂载 | 15 个 router；除 `auth_router` 外均挂 `dependencies=[Depends(get_current_user)]` | `main.py:82-110`；`api/kb.py:39` |
| 尾斜杠归一中间件 | 反射 `_IncludedRouter.effective_route_contexts()`，结果缓存到 `app.state` | `main.py:168-188`、`main.py:124-152` |
| 429（限流） | 全局处理器；body `{detail, retry_after}`；头 `Retry-After: int+1` | `main.py:194-200` |
| 429（日配额） | body `{detail, kind, limit}`，**无 `Retry-After`** | `main.py:203-208` |
| `/health` | `{status, version, llm_gate}`；注释要求"必须 async" | `main.py:228-236` |
| 开发启动 | `uvicorn.run(host="127.0.0.1", port=8000, reload=True)` | `main.py:246` |
| 生产启动 | `--host 127.0.0.1 --port 8000 --workers $WORKERS` | `deploy.sh:340-345` |
| worker 探测 | CPU 封 4；内存 <3G 封 2；`WORKERS=1` 可回退 | `deploy.sh:46-56`、`deploy.sh:29` |

### 1.2 SSE 与流式

| 组件 | 现状 | 出处（文件:行） |
|---|---|---|
| SSE 响应头 | `no-cache` / `X-Accel-Buffering: no` / `keep-alive`；**无心跳、无 `retry:`** | `api/chat.py:291-295` |
| 帧构造 | `event: X\ndata: {json}\n\n`，`ensure_ascii=False` | `api/chat.py:332-333` |
| 事件类型 | status/tool/sources/grounding/token/reasoning/title/done/error/truncation + hybrid 三个 | `api/chat.py`（协议注释）；`:549-557` |
| 断连检测（LLM 流） | 每 16 帧查一次，首帧必查 | `api/chat.py:634` |
| 断连检测（Agent/hybrid） | 每个事件都查一次 | `api/chat.py:956`、`api/chat.py:1080` |
| 确定性收尾 | `aclosing` 包上游；`background` 关上游 + 还并发名额 | `api/chat.py:631`、`api/chat.py:1301-1307` |
| 截断检测 | `_TRUNCATED_REASONS` 四值集合；取不到时**当作未截断** | `api/chat.py:525`、`:535-536`、`:560-567` |
| `done` 帧 | 带 `answer/refs/title/mode/message_id/run_id/truncated/plan_topic` | `api/chat.py:1288-1297` |
| LLM 闸位置 | 抢在 SSE 之前，抢不到 429 + `Retry-After` | `api/chat.py:673-685` |
| 前端解析 | `fetch` + `ReadableStream` 按 `\n\n` 切帧；`finally` 里 `reader.cancel()` | `frontend/src/lib/sse.ts:51-104` |
| 前端重连 | **无**（无 `Last-Event-ID`、无退避、无心跳超时检测） | `frontend/src/lib/sse.ts:26-104` |
| Next 代理 | `/api/:path*` → `http://localhost:8000/api/:path*` | `frontend/next.config.ts:10-17` |
| nginx SSE location | `location = /api/chat/stream`：HTTP/1.1、`proxy_buffering off`、`gzip off`、3600s | `https/agent-hub.conf.template:68-83` |
| nginx `/api/` location | 同样关缓冲，3600s | `https/agent-hub.conf.template:102-117` |
| nginx 前端 location | `proxy_read_timeout 60s`（**与 SSE 段不一致**） | `https/agent-hub.conf.template:86-98` |
| 上传上限 / CSP | `client_max_body_size 50m`；CSP **故意不加**（Next 内联脚本会白屏） | `https/agent-hub.conf.template:51`、`:34-35` |

### 1.3 限流、配额与并发

| 组件 | 现状 | 出处（文件:行） |
|---|---|---|
| 每分钟限流 | 进程内滑窗 `_HITS` + SQLite 秒桶兜底；全局读带 200ms 缓存 | `core/quota.py:53`、`:96-112`、`:115-151` |
| 多 worker 收敛度 | 明示为"**护栏不是闸门**"，收敛到约 N+ε，非严格 N | `core/quota.py:31-33` |
| 读失败降级 | 全局读不到 → 退回单层，记 `rate_shared_unavailable` | `core/quota.py:234-239` |
| 日配额 | 三种 kind：`ask`/`embed`/`auto`；原子 upsert；"超限不扣" | `core/quota.py:320-326`、`:375-391` |
| 配额上限 | 10 次/分；60 次/日问答；1000 片/日向量化；120 次/日自动任务 | `core/config.py:157,161,159,171` |
| LLM 并发闸 | `LLM_MAX_CONCURRENCY=16`、`LLM_QUEUE_TIMEOUT=20` | `core/config.py:67,70,72` |
| 闸作用域 | **进程内**；全局上限 = N × `LLM_MAX_CONCURRENCY` | `core/llm_gate.py:25-26` |
| 指标端点 | `/api/metrics`（Prometheus）、`/api/metrics/json`；"管理员 或 `X-Metrics-Token`" | `api/metrics.py:75-86`、`:59-72` |

### 1.4 研讨卷中已过期或待修正的判据

- "单进程 uvicorn，不扩多 worker"（`00_总纲:54`）——**已反转**（`deploy.sh:340-345` 在跑 `--workers`）。
- "限流在多副本下变成 N 倍额度"（`02:161`）——**已部分修复**（收敛到 N+ε，`core/quota.py:11-33`）。
- "ADR D-005 把 `/api/v1` 挂起"（`02:84`）——**仍悬挂**，原定迁移窗口（合分支）已过，见 P1-1。
- "后端只听回环"三条理由（`02:412-428`）——**基本有效**，上线后多一层 nginx，见 P0-4。

---

## 2. 校赛够用 vs 省赛不够

| 维度 | 校赛口径 | 省赛口径 | 差距 | 证据 |
|---|---|---|---|---|
| SSE 心跳 | 无心跳帧 | 长流需心跳保活，否则中间代理/移动端切后台会静默断流 | 无 | `api/chat.py:291-295` 只有三个头，无 `retry:` 无 ping |
| SSE 客户端超时 | 无超时检测 | 抖动后需明确提示"连接中断"或自动恢复 | 无 | `frontend/src/lib/sse.ts:82-94` 只有 `reader.read()` 循环，无超时 |
| SSE 断连粒度 | LLM 流每 16 帧、Agent/hybrid 每事件 | 粒度不一是可接受的，但缺端到端验证 | 有 `tests/test_sse_disconnect.py`（内容待核对） | `api/chat.py:634` vs `:956` |
| 429 契约 | `RateLimited` 带 `Retry-After`；`QuotaExceeded` 不带 | 两类都应带，否则前端无法"到点再试" | 缺 | `main.py:194-200` vs `main.py:203-208` |
| 限流状态可见性 | 只在触发时告知 | 客户端应能预知剩余次数/重置时刻 | 无 `X-RateLimit-*` | 全仓仅见 `Retry-After`（`main.py:199`、`api/chat.py:685`） |
| API 版本 | `/api`，无版本段 | 需要版本声明（可文档级而非路径级） | 缺 | `main.py:82-110` |
| OpenAPI 可展示性 | 默认 `/docs`、`/openapi.json`（**待核对公网可达性**） | 需要可展示的接口文档 | description 仅一行 | `main.py:58-59` |
| 多 worker 下的流 | 每进程独立闸，全局 = N×16 | 上限需可观测、可解释 | `/health` 每进程各报各的 | `main.py:232-233`、`core/llm_gate.py:223-233` |
| 公网入口收口 | 3000 直出公网 | HTTPS 上线后只留 443 | 3000 是唯一公网口 | `docs/运维强化落地报告_2026-10-20.md:24-31` |
| nginx 超时一致性 | SSE 3600s / 前端 60s / `/api/` 3600s | 三段策略需可解释 | 前端段与其它两段不一致 | `https/agent-hub.conf.template:96-97` vs `:81-82` |
| 请求追踪 | 无 request id | 排障与演示需一条请求可追踪 | 无 | 全仓无 `X-Request-ID`（grep 无命中） |

---

## 3. 升级项

### P0

#### P0-1 · SSE 心跳帧 + 客户端超时兜底

- **现状**：服务端只在有数据时发帧（`api/chat.py:654`），空闲期（等待上游首字、Agent 工具执行）可能长达数秒到数十秒不发任何字节；前端 `while(true) await reader.read()`（`frontend/src/lib/sse.ts:82-94`）没有超时判断。
- **改什么**：两处小改。① 服务端：在事件生成器里起一个每 15 秒发一帧 `event: ping` 的伴随任务（或在 `_sse` 旁加 `_ping()` 产出 `: ping\n\n` 注释帧），流结束时取消；② 前端：在 `streamChat` 里加一个"45 秒未收到任何字节即抛超时错误"的看门狗，并在 UI 层给出"连接似乎中断，可点重试"。
- **为什么省赛必须改**：演示环境从校内变成公网（可能走 nginx、可能手机热点）。空闲期不发字节的 SSE 流，遇到中间代理或 NAT 超时（常见 30~60 秒）会被**静默掐断**，表现为"转圈然后卡死"；而服务端 `is_disconnected()` 每 16 帧才查一次（`api/chat.py:634`），空闲期根本查不到。这是**演示现场最可能翻车的点**。
- **代价/风险**：心跳流量可忽略；看门狗阈值要覆盖"Agent 工具执行 30 秒"这类合法长静默，故取 45 秒。风险是阈值太小误报中断，把正常长任务打断。
- **反转条件**：若首字延迟与工具执行从不超过 20 秒（当前 `LLM_QUEUE_TIMEOUT=20`，`core/config.py:70`）且只在校内演示，可降为 P1；只要走公网 + nginx，就是 P0。
- **验收口径**：① `curl -N` 打 `/api/chat/stream`，未出字阶段能观察到 ≥1 帧 `ping`，间隔 ≤20s；② 浏览器切 offline 30s 再恢复，前端 45s 内给出提示而非无限转圈；③ 正常 60s 长任务不被误判中断。

#### P0-2 · 429 契约统一：两类都带 `Retry-After`，并区分 `kind`

- **现状**：`RateLimited` 的处理器给 `Retry-After: int(exc.retry_after)+1`（`main.py:199`，`+1` 是为了向上取整，见研讨卷 `02:468-476`）；`QuotaExceeded` 的处理器（`main.py:203-208`）**只回 body 不回头**；LLM 闸的 429 在端点里给 `Retry-After: str(e.retry_after)`（`api/chat.py:685`，不加 1）。三处口径不一。
- **改什么**：① `QuotaExceeded` 处理器补 `Retry-After`，值 = 距业务时区次日零点的秒数（用已有的 `core/timeutil.py:94-105` 的 `day_bounds`，别自己减时间戳）；② 统一三处取整方向为"向上取整"，并把 `kind` 也加进 `RateLimited` 的 body，让前端用同一字段区分"限流/日配额/上游忙"三态；③ 在前端错误映射里登记这三种 `kind`（`api/schemas.py` 内容**待核对**）。
- **为什么省赛必须改**：研讨卷自己说过"HTTP 状态码是产品语义，不只是技术语义"（`02:460-466`）。有真实用户后，撞到 429 必须知道何时能再试。日配额（每日 60 次问答，`core/config.py:161`）是普通用户最可能撞到的一种，恰恰是现在唯一没有 `Retry-After` 的那种——**缺口正好在最常走的路上**。
- **代价/风险**：改全局处理器是低风险的（新增头，不改语义）。风险是前端若已按"有没有 `Retry-After`"分支，行为会变，需前后端一起改一起验。
- **反转条件**：若确认前端不消费 `Retry-After`（需核对 `frontend/src/lib/api.ts` 与 `errors.ts`，**待核对**），则降为 P1（仅作接口规范性补全）。
- **验收口径**：三种 429 场景（连点限流 / 日配额触顶 / LLM 闸满）用 curl 逐一触发，均返回 429 且带合法 `Retry-After`（正整数），body 的 `kind` 可区分三态。

#### P0-3 · 限流状态对外可见（`X-RateLimit-*`）

- **现状**：客户端只有在被拒时才知道被限流；没有"还剩几次"的预知能力。`quota.hit()` 已经返回"本窗口内已用次数"（`core/quota.py:257`），`quota.snapshot()` 能给出三种 kind 的 `used/limit`（`core/quota.py:422-436`），但这两处数据都没走到响应头。
- **改什么**：加一个 HTTP 中间件（或改造现有 `normalize_api_slash` 旁挂一个），对所有 `/api/` 且已鉴权的响应补三个头：`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`。取值直接来自 `quota`，不新增状态。
- **为什么省赛必须改**：这是"限流从内部控制手段升级为对外服务契约"的标志动作，成本极低而可展示性极高。评审问"怎么防滥用"，答"有双层配额"是标准答案；答"有双层配额，且客户端能在响应头里实时看到剩余额度与重置时刻"是可验证答案。
- **代价/风险**：每个响应多算一次剩余额度会多一次 SQLite 读。需复用 200ms 缓存思路（`core/quota.py:72-73`）或**只加在 `/api/auth/me/` 与 `/api/chat/stream` 两个端点**。做成全局会让热路径变慢，与"限流器不该自己变成故障源"（`core/quota.py:28-29`）直接冲突。
- **反转条件**：若实测 P95 上升超过 5%，只保留 `/api/auth/me/` 一个端点（它本就返回 `quota.snapshot()`，见 `api/auth.py:169`，零额外成本）。
- **验收口径**：连续请求同一端点，`Remaining` 单调递减、`Reset` 为合法 Unix 秒；撞到 429 时 `Remaining` 为 0。

#### P0-4 · nginx 收口：HTTPS 上线后公网只留 443

- **现状**：`deploy.sh:348` 起 `next start` 绑 `:3000`（默认 0.0.0.0）对外；uvicorn 绑 `127.0.0.1:8000`。HTTPS 模板已把 `/` 反代到 3000、`/api/` 与 SSE 反代到 8000（`https/agent-hub.conf.template:68-117`）。但**上线后 3000 仍在公网监听**，形成"直连 3000 可绕过 nginx"的双入口。
- **改什么**：HTTPS 上线脚本收尾补两件事——① 把 `next start` 改绑 `127.0.0.1:3000`（需**核对 Next 16 是否接受 `HOSTNAME` 环境变量**）；② 防火墙摘掉 3000 的公网放行，且必须放在 HTTPS 成功之后（研讨卷已记录"现在关 3000 = 立刻断服"；`docs/运维强化落地报告_2026-10-20.md:69` 已有 `--after-https` 守卫）。
- **为什么省赛必须改**：双入口意味着**两套 TLS 行为、两套 Cookie 域、两套限流观测**。研讨卷已论证"8000 直连会绕过 Next 的 Cookie 域设置"（`02:424`），同样论证对 3000 绕过 nginx 成立。有真实用户后，绕过 TLS 的入口直接带来明文传输凭证的风险。
- **代价/风险**：改 `next start` 的绑定地址若参数名不对会导致前端起不来，**必须先在一台机器上试通再写进脚本**。防火墙收敛是破坏性动作，必须带"443 已监听"守卫。
- **反转条件**：若 Next 16 不支持回环绑定，退一步只做防火墙收敛（效果等价，本机仍监听）。
- **验收口径**：① 公网 `curl http://域名:3000` 超时或拒绝；② `https://域名` 正常；③ 安全组/防火墙里 3000 与 8000 均不在公网放行列表；④ 跑一次完整问答与一次 SSE 流，验证未走错入口。

#### P0-5 · 确认 `/docs` 与 `/openapi.json` 的公网暴露策略

- **现状**：FastAPI 默认开启 `/docs`、`/redoc`、`/openapi.json`（`main.py:53-62` 未关闭）。当前 3000 是唯一公网口，而 `/docs` 不经过 Next 的 `/api/*` rewrite，所以**公网打不到**它——这是"没配转发"的偶然状态，不是决策。
- **改什么**：明确决策并写进部署文档。建议：nginx 加一条 `location = /docs`（及 `/openapi.json`）反代到 8000，并加一层与 `X-Metrics-Token` 同款的静态 token 保护。
- **为什么省赛必须改**："文档规范"是作品呈现 20% 的明文项，**可交互的 OpenAPI 文档是它最直接的物证**。但它也是攻击面（暴露端点结构）。所以需要的是**显式决策后打开**，而不是当前的偶然状态——偶然状态在改任意一处 nginx 配置时就可能翻转。
- **代价/风险**：已有 router 级鉴权（`main.py:82-110`），暴露清单本身不是漏洞，但会方便探测，故至少加一层静态 token。
- **反转条件**：若赛前来不及加保护，保持不转发，并在文档写清"接口文档随源码提供，公网不暴露"——这本身也是可解释的决策。
- **验收口径**：决策明确且写进部署文档；若选择公开，则从公网能打开 `/docs` 且正常渲染；若选择不公开，则公网访问返回 404 且文档里有说明。

### P1

#### P1-1 · API 版本声明（文档级优先于路径级）

- **现状**：全部端点为 `/api/xxx`；`规范/决策记录.md:177-187` 的 D-005 记录：规划文档要求 `/api/v1` 但 35 个端点已实现为 `/api`，裁决为"前缀只在 `main.py` 定义一次"，并把"合分支"定为成本最低的迁移窗口。
- **改什么**：**不做路径迁移**（风险远大于收益），改为**文档级版本声明**：① 应用 `version` 从 `"0.3.0 (W3)"` 改为语义化版本 + 一个 `/api/version` 端点返回 `{api_version, app_version, build_date}`；② 在 OpenAPI 的 `description` 里写明版本号与兼容性约定；③ 写进部署文档"当前为 v1 前的过渡态，路径不含版本段，破坏性变更将体现在 `/api/version` 的 `api_version` 上"。
- **为什么省赛必须改**：版本化的本质是**让调用方知道自己签的是什么约**。`规范/决策记录.md:187` 定的迁移窗口（合分支）已过去，原判据失效；重定一个低成本等价方案，好过继续挂一条无法执行的债。
- **代价/风险**：`/api/version` 建议挂到 `main.py`、与 `/api/config/defaults` 并列且不带鉴权（便于探活）；只返回常量，无风险。
- **反转条件**：若赛事明确要求接口含版本段（**待核对**），则必须做路径迁移，此时可利用"前缀只在 `main.py` 定义一次"这个已铺好的条件（`规范/决策记录.md:181`）+ 前端基址统一改一处。
- **验收口径**：`curl /api/version` 返回三字段；与应用 `version` 一致；文档写有版本策略。

#### P1-2 · OpenAPI 描述补全

- **现状**：`main.py:58-59` 只有一行 description；各 router 有 `tags=`，但端点级 `summary/description/responses` 多数未写（**待核对逐个端点**）。
- **改什么**：给 `/api/chat/stream`、`/api/kb/*`、`/api/auth/*` 三条主链路端点补 docstring 首行（FastAPI 作为 `summary`）与 `responses` 声明（尤其是 401/404/429）。
- **为什么**：OpenAPI 是 P0-5 的载体，文档质量取决于这些字段；429 语义在文档里显式化，正好呼应 P0-2。
- **代价/风险**：纯文档工作，零功能风险；工作量在 15 个 router 的端点遍历。
- **反转条件**：若 P0-5 决策为"不公开 `/docs`"，该项降级——但**仍建议做**，`openapi.json` 已可用于契约核对（`tools/check_hybrid_contract.sh:170-174` 就在用 OpenAPI）。
- **验收口径**：三条主链路端点的 `summary` 非空；429/401/404 在 `responses` 里显式声明；`tools/check_hybrid_contract.sh` 仍通过。

#### P1-3 · 前端 SSE 重连与 `done` 帧兜底

- **现状**：无重连。`done` 帧已带完整 `answer`（`api/chat.py:1288-1297`），具备"断线后靠 done 恢复"的数据基础，但前端没用上。
- **改什么**：在 `frontend/src/lib/sse.ts` 里把已收到的 `token` 增量累积进 ref；流中断时用 `message_id` 回查历史消息补齐正文，而不是丢弃已渲染内容。
- **为什么**：配合 P0-1 的看门狗。光有"检测到中断"没有"中断后怎么办"，体验仍然是断的。
- **代价/风险**：需要"按 message_id 取单条消息"的查询路径（**待核对 `api/sessions.py` 是否已有**）；引入新端点有回归面。
- **反转条件**：若 `api/sessions.py` 的消息列表接口可复用，成本降到很低；若赛前来不及，降级为"中断后提示刷新"——刷新走历史消息本就能看到完整回答。
- **验收口径**：在流进行到一半时手动 abort，页面不丢失已渲染内容，并在 3 秒内给出"已恢复完整内容"或"刷新可查看"之一。

#### P1-4 · 多 worker 下 LLM 闸的跨进程可观测

- **现状**：`LLM_MAX_CONCURRENCY=16`（`core/config.py:67`），`deploy.sh` 实测本机会起 2 worker（`deploy.sh:53`），所以全局实际是 32。`/health` 每进程各报各的（`main.py:232-233`），没有聚合视图。
- **改什么**：两选一，取其轻：① **调参**：把 `LLM_MAX_CONCURRENCY` 按 worker 数整除（如 `WORKERS=2` 时设 8），并在 `deploy.sh` 里加一句提示"全局上限 = WORKERS × LLM_MAX_CONCURRENCY"；② **观测**：在 `/health` 的 `llm_gate` 快照里补一个 `workers_expected` 字段，或在文档里写清换算公式。
- **为什么**：闸的定位是"体面地排队"而非"精确地限流"（`core/llm_gate.py:9-10`），所以精确收敛不是目标；**目标是让运维一眼看出全局上限**。当前要"自己乘一下"，属未标定边界的典型。
- **代价/风险**：调小 `LLM_MAX_CONCURRENCY` 会增加排队。需先确认上游真实并发上限（**待核对 DeepSeek/百炼的并发与 TPM 限制**），否则是白白牺牲吞吐。
- **反转条件**：若上游并发上限本来就远高于 32，该项无意义，只做文档标注即可。
- **验收口径**：文档或 `/health` 里能直接读出全局上限；`deploy.sh status` 输出里能读到 worker 数；二者相乘与文档一致。

### P2

#### P2-1 · `X-Request-ID` 贯穿

- **现状**：无。排障只能靠时间窗与 uid 反查。
- **改什么**：中间件生成 `X-Request-ID`（取客户端传入值或 `uuid4`），挂 `request.state`，响应头回显，SSE 的 `error` 帧与日志带上它。
- **为什么**：有真实用户后，"用户报障 → 定位到那一次请求"是刚需。
- **代价/风险**：要改日志格式与若干错误出口，回归面偏大。
- **反转条件**：若用户量始终很小且日志可全文 grep（当前 `/tmp/baomi-back.log` 单文件），可不做。
- **验收口径**：一次请求的错误帧、服务端日志、响应头三者 ID 一致。

#### P2-2 · nginx 三段超时的策略说明

- **现状**：SSE 段与 `/api/` 段 3600s（`https/agent-hub.conf.template:81-82, 115-116`），前端段 60s（`:96-97`）。
- **改什么**：模板注释补一句"为什么三段不同"——SSE 是长流必须放长；`/api/` 含建库/导入这类长任务也放长；前端段是页面与静态资源，60s 足够，短超时反而能快速暴露挂死的 SSR。
- **为什么**：避免后人"顺手统一成 60s"导致建库接口 504。这正是研讨卷反复强调的"把决策的适用边界写下来"。
- **代价/风险**：零功能风险。**反转条件**：无。
- **验收口径**：模板有该注释，`nginx -t` 通过。

---

## 4. 竞品对照

在"接入层与流式传输"这一层的做法：

| 平台 | 做法 | 我们差在哪 | 要不要跟 |
|---|---|---|---|
| **Dify** | 版本化公开 API（`/v1/...`）+ 完整 OpenAPI + 固定且文档化的 SSE 事件类型 + 明确错误码表 | 我们事件类型更丰富（12 种，含 `grounding`/`workflow_node`/`mode_switch`），但**无版本段、无公开错误码表、事件协议只在源码注释里** | **跟**"错误码表 + 事件协议文档化"（低成本高展示）；**不跟**路径版本化（见 P1-1） |
| **FastGPT** | 商业版含日志看板；接口统一错误结构；中文错误信息 | 我们的 `describe_api_error` 已做中文错误翻译（`kb/rag_core.py` 导出，`api/chat.py:36` 引入），这点不差 | **基本不跟**。日志看板属商业版能力，不属本层 |
| **RAGFlow** | 重解析任务用异步任务 + 进度流；接口有 task 状态机 | 我们的建库是同步端点（**待核对 `api/kb.py` 是否流式**） | **部分跟**：状态机模式值得借鉴，但赛前改接口形态风险大，记入赛后候选 |
| **Coze 扣子** | 闭源 SaaS，不支持自定义模型端点，流式由平台托管 | 不适用——自建全栈，SSE 必须自己负责 | **不跟**。这恰是差异点："模型端点可控"是 Coze 给不了的 |
| **n8n** | Webhook 触发为主，非长流式产品 | 调度侧（`worker/scheduler.py`）有类似能力，接入层无可对照项 | **不跟** |

**总判断**：差距**不在能力而在契约的显式程度**。Dify 的 `/v1` 与错误码表是平台必须给的；我们只需把**已有的写清楚**——事件协议表、错误码表、限流契约。这是 1~2 天的文档工作，回报是"文档规范"这一栏的实质证据。

---

## 5. 落地顺序与验收口径

| 序 | 事项 | 产出物 | 验收口径 | 依赖 |
|---|---|---|---|---|
| 1 | 429 契约统一（P0-2） | `main.py` + 前端错误映射 | 三种 429 均带 `Retry-After` 与 `kind` | 前后端联调 |
| 2 | SSE 心跳 + 看门狗（P0-1） | `api/chat.py` + `sse.ts` | 空闲期可见 ping；断网 45s 内有提示；60s 长任务不误判 | 无 |
| 3 | 限流响应头（P0-3） | 中间件（限两个端点） | Remaining 单调递减；P95 影响 <5% | 无 |
| 4 | `/docs` 暴露策略（P0-5） | 部署文档 + nginx | 决策明确且可验证 | 排在 P0-4 之后 |
| 5 | nginx 收口（P0-4） | `setup-https.sh` 收尾 + 防火墙 | 公网 3000 不可达；https 正常；链路通过 | **HTTPS 上线成功后** |
| 6 | API 版本声明（P1-1） | `/api/version` + description | 三字段返回；文档有版本策略 | 无 |
| 7 | OpenAPI 描述补全（P1-2） | 三条主链路端点 | summary 非空；429/401/404 显式；契约脚本仍过 | P0-5 决策 |
| 8 | 前端重连兜底（P1-3） | `sse.ts` | 中途 abort 不丢已渲染内容 | P0-1；取单条消息能力 |
| 9 | 多 worker 闸可观测（P1-4） | 文档或 `/health` 字段 | 全局上限可读且与 worker 数相乘一致 | 上游并发上限（待核对） |
| 10 | 请求 ID（P2-1） | 中间件 + 日志 | 三处 ID 一致 | 有余力 |
| 11 | nginx 超时注释（P2-2） | 模板注释 | `nginx -t` 通过 | 无 |

**关键路径**：2 → 5。心跳是演示防翻车的关键；nginx 收口必须排在 HTTPS 成功之后，且是唯一有"断服风险"的动作，需回滚预案（模板保留 80 段即可随时回退到 3000 直出）。

---

## 6. 不做的事（及为什么）

| 不做 | 为什么 |
|---|---|
| **不做 `/api` → `/api/v1` 的路径迁移** | `规范/决策记录.md:187` 定的低成本窗口（合分支）已过。现在迁移要同时改 15 个 router 前缀、前端基址、nginx 三处 location 与全部文档测试，收益仅是"看起来更规范"。文档级版本声明（P1-1）能达到同样效果，成本是它的十分之一。 |
| **不引入 sse-starlette 等 SSE 框架** | 本项目需要 12 种领域事件与"这一帧必须赶在 token 之前"的强时序控制，按消息抽象的框架表达不了（`02:214-215`）；引入要重写整条流式路径。 |
| **不把 SSE 改成 WebSocket** | 本项目是单向推送，用全双工做单向是过度设计，且 WS 在部分代理/防火墙下会被拦（`02:196`）。 |
| **不做断线续传（从第 37 个 token 继续）** | 需要服务端缓存半截流 + `Last-Event-ID` 回放，是完整状态机改造。而 `done` 帧已带完整 `answer`（`api/chat.py:1288-1297`），配合 P1-3 能达到九成体验，成本是前者的零头；真需要时研讨卷已留反转条件（`02:251`）。 |
| **不做 Redis 化的精确限流** | `core/quota.py:31-33` 如实标注这是"护栏不是闸门"，收敛到 N+ε。精确 N 需要 Redis 或每请求一次 DB 原子自增，"两者都比这个方案贵得多"。2 核 2G 下上 Redis 是拿内存换一个不存在的精度需求。 |
| **不加 CSP** | `https/agent-hub.conf.template:34-35` 已写明：Next 有内联脚本，硬加会白屏；上 CSP 需先做 nonce/hash 方案。赛前做 nonce 的收益（一个安全响应头）远低于风险（白屏 = 演示事故）。 |
| **不为限流响应头做全局中间件** | 见 P0-3。全局加会让热路径多一次 SQLite 读，与"限流器不该自己变成故障源"（`core/quota.py:28-29`）冲突。 |
| **不改尾斜杠归一中间件** | 它依赖 FastAPI 私有 API（`_IncludedRouter`、`effective_route_contexts()`、`route_path`），研讨卷已标注为"未被测试覆盖的版本风险"（`02:402`）。但它是修 bug 的产物且当前工作正常，赛前动它等于主动引入回归；正确动作是把它列入反转条件登记表的观测项（见 01 卷 P0-3）。 |
