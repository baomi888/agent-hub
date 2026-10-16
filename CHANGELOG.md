# 更新日志（CHANGELOG）

本文件记录 **知穗agent** 的所有重要变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
日期采用 `YYYY-MM-DD`。

> 说明：项目当前未打语义化版本 tag，故以「日期」作为发布单元；
> 每条右侧标注对应提交哈希，便于 `git show <hash>` 追溯。
> 当前活跃开发分支为 **`dev`**，`main` 保持为已发布的稳定线。

---

## [2026-10-16]

### 合入（混合模式跨轮记忆 + 计划主题识别）

- 合入 `e6075889`：hybrid 模式注入对话历史，修复「混合模式每轮失忆」——新增
  `HYBRID_HISTORY_MAX_MESSAGES`（默认 12，0=关闭），`ctx.shared["history"]` 由
  Agent / Workflow 引擎在拼 prompt 时前置。新增计划主题识别
  （`api/chat.py::detect_plan_topic` / `_resolve_plan_topic`），SSE `done` 新增
  `plan_topic` 字段。两处冲突（`core/config.py`、`hybrid/workflow.py`）均按「保留双方意图」
  解决，并同步更新 `tests/test_baseline_chat_stream.py` 的 done 字段断言。`38c75f2`

### 重构（剩余 AI/RAG/知识库模块：死代码清理 + 缺陷修复）

范围：上一轮 RAG/AI 核心之外尚未治理的模块（`kb/pipelines.py`、`agent/builder.py`、
`hybrid/*`、`core/llm.py`、`core/config.py`、`core/session.py`、`core/tool_registry.py`、
`api/hybrid.py`、`api/delegate.py`、`service/daily.py`）。原则同上一轮：可观测行为不变，
先钉行为再改。详见 `docs/剩余模块屎山治理与重构报告_2026-10-16.md`。

- **缺陷修复**
  - `hybrid/expr.py::eval_condition` 此前只兜 `ExprError`，与其 docstring「任何异常都按 False
    处理」不符：含 NUL 的表达式（`ast.parse` 抛 `ValueError`）与超深嵌套（`RecursionError`）
    都会漏进编排主循环。现统一收敛为「条件不成立」——只影响此前本就报错的输入。`d66499a`
  - `api/hybrid.py::upsert_rule` 条件语法校验补兜 `ValueError` → 400（此前含 NUL 的条件会 500）。`d66499a`
- **性能**
  - `hybrid/store.py`、`core/session.py` 的 `_db_path()` 改为纯计算，目录创建挪到建连前——
    去掉每条 SQL 前的一次 `os.makedirs` 系统调用。`d66499a`
- **清理（死代码，均经全仓库引用核查）**
  - 删除 8 处零引用符号（均经「全仓库引用核查」确认）：`hybrid/workflow.py::NodeError`、
    `core/tool_registry.py::get_meta/is_activated`、`service/daily.py::mark_read`。`d66499a`
  - 同批第二批（首轮提交时漏 stage，已在合并期间补提交）：`core/auth.py::NotAuthenticated`、
    `core/account.py::count_users/list_users`、`core/scheduler.py::today_local`。`da1adf8`
  - `api/delegate.py`：死函数 `_enabled_count` 收敛为共享纯函数 `_count_enabled`；
    `import json` 提到模块级；去掉 `model_dump()` 后不可达的 `isinstance` 分支。`d66499a`
- **注释**
  - 说明工作流 / Agent 节点产出为何直接写 `ctx.shared` 而不走 `shared_set`
    （落盘指针会破坏下游模板渲染）。`d66499a`
- **测试**
  - 新增 `tests/test_baseline_refactor_edges.py`（11 条，离线）：expr 正常语义不变 +
    NUL / 超深嵌套 / 白名单拒绝；delegate 返回体契约与启用计数口径。全量 189 → 200 passed。`d66499a`

### 重构（RAG / AI 核心：等价重构 + 性能优化）

范围：`kb/rag_core.py`、`kb/importer.py`、`api/kb.py`、`api/chat.py`、`agent/tools.py`。
原则：可观测行为（检索结果与排序、分片边界、prompt 字面量、SSE 事件序列、接口响应与状态码、
错误文案）一律不变；先补 30 条行为基线断言，再逐条改、逐条验证。详见
`docs/RAG与AI核心重构_回归测试报告_2026-10-16.md`。

- **性能**
  - 上传时文件记录由「每文件一次重读+重写 `kb_files.json`」改为一次性登记。`5eed439`
  - 入库前的"全文前 4000 字"预览改为边拼边截断，不再为整篇文档造 MB 级中间串。`27bd942`
  - 来源卡片与参考片段共用一份中间量（来源名/页码/相似度/强度），强度阈值不再写两遍。`06c2fb5`
  - `select_diverse` 的离群过滤把相似度换算从两遍减到一遍。`27bd942`
  - 流式回答的断连检测由逐帧改为首帧 + 每 16 帧一次。`b6a968f`
  - 切片器按 `(chunk_size, overlap)` 复用、embedding 线程池进程内复用。`d004b96`
  - 天气工具的 `CITY_ALIASES` / `WMO_CN` 等字面量表提到模块级。`6ac2129`
- **结构**
  - `import_url` 与 `download_and_index` 的后段重复流程抽成 `_index_into_kb()`（两处不同的报错文案刻意保留）。`62d24f4`
- **测试**
  - 新增 30 条行为基线用例（检索/RRF/偏置/分片/主题注入/来源格式化、SSE 序列、上传与导入接口、天气映射），
    全部离线可跑。全量 159 → 189 passed。`c36c8ff`

### 修复

- `POST /api/kb/{kb_id}/query` 此前必然 500：端点调用的是 2026-10-13 死代码清理中被删除的
  `RagPipeline.answer`，AttributeError 被端点兜成 500。已按删除前的实现原样恢复该方法。`c5b5dbd`
- `_bias_rerank` 遇 `distance=None`（混合检索里仅关键词通道召回的片段）抛 TypeError：
  「用户有画像标签 + 命中关键词独有片段」同时成立时线上是一条静默 500。现在这类片段不参与
  减距与重排（保持原位置），只让有向量距离的条目参与排序。`c5b5dbd`

---

## [2026-10-14]

### 新增（混合架构：工作流 × 自主 Agent）
- 新增第五种模式 `hybrid`（`POST /api/chat/stream` 的 `mode` 取值）：工作流跑确定性步骤，
  Agent 接管不确定环节，执行中按上下文条件无缝切换（降级接管 / 收编回归 /
  流程内嵌 agent 节点三种形态）。
- 引擎层 `hybrid/`：TaskContext 统一上下文（`shared` 唯一交换通道 + 切换即快照 + version 乐观锁）、
  WorkflowEngine（5 类节点 / 条件分支受限表达式 / 节点级超时重试 / on_fail 跳转）、
  AgentEngine（复用 `build_agent()`，步数/token/时长三维预算护栏）、
  SwitchEngine（规则链优先级 + 人工指令插队 + 防抖冷却）、HybridOrchestrator（引擎注册表，模式可插拔）。
- 持久化独立 `hybrid.db` 四表：workflow_defs / workflow_runs / switch_rules / exec_logs。
- 管理面 `api/hybrid.py` 12 端点：工作流 CRUD（内置只读）、规则覆盖模型
  （系统默认删不掉，用户同名覆盖、删覆盖恢复默认，条件表达式语法级校验）、
  执行记录与全链路日志查询、人工切换（running 才允许，否则 409）。
- SSE 新增 `hybrid_start / workflow_node / mode_switch` 三事件，`done` 带 `run_id`；
  协议说明见 `api/chat.py` 头注释。`HYBRID_MODE_ENABLED=0` 时静默回退 agent。
- 前端对接接口预留（后端契约）：请求体 `workflow_id`、SSE 三事件 + `done.run_id`、
  `/api/hybrid` 12 端点，详见 `docs/混合架构实现说明_2026-10-14.md`「前端对接接口」。
  前端 UI 实现按职责边界不在本次改动范围，`frontend/` 保持改动前状态。
- 内置资产：`builtin_plan_execute`（规划→执行）工作流 + 4 条系统默认切换规则。
- 测试：新增 25 项混合架构用例（context / expr / workflow / switcher / orchestrator），
  全量 64 项绿；前端回退后 `tsc --noEmit` 通过。
- 设计依据 `docs/混合架构系统研讨报告.md`，实现明细见 `docs/混合架构实现说明_2026-10-14.md`。

### 修复
- `orchestrator._switch_event` 补写 exec_logs：此前切换事件只发 SSE 不落库，
  监控链路缺切换记录。

---

## [2026-10-11]

### 修复（top_k 上界 + 偏好读回）
- 服务端 `top_k` 补硬上界：新增 `TOP_K_MAX`（默认 20，可用环境变量覆盖），
  `ChatRequest` / `KBQueryRequest` / `query_kb` 三处入口统一加 `le` 约束，超限直接 422。
- 前端 `usePrefs` 补读回 `topK / chunkSize / chunkOverlap`：此前挂载只读回 mode/kbPanel/fontScale，
  检索参数停在默认值，`prefsReady` 后的落盘会把用户改过的值写回默认——刷新即被抹掉。
- 详见 `docs/修复说明_2026-10-11_top_k与偏好读回.md`。

### 新增（条件式查询改写 · 默认开启）
- 多轮追问（"那它呢""第二个呢"）单独看检索不到东西。新增**条件式**改写：仅当问题含指代词
  或长度 ≤ 8 字、且存在对话历史时，才用最近 4 条历史把它补全成可独立检索的问题；其余问题
  零额外开销——这是它跟"每题都改写"的关键区别。
- 新增配置：`QUERY_REWRITE_ENABLED`（默认 1，置 0 完全关闭）、`QUERY_REWRITE_MAX_LEN`(8)、
  `QUERY_REWRITE_HISTORY`(4)、`QUERY_REWRITE_MAX_TOKENS`(64，只输出一条问题，防跑偏也防费用)。
- 容错兜底：改写失败、结果不可信（空或过长）一律退回原问题，绝不阻塞检索；RAG 与图片理解
  两条检索路径均已接入，改写后的问题直接作为检索词，检索结果缓存键随之正确、不跨上下文串结果。
- 调用方一次取历史，改写与 `_build_messages` 共用，避免重复查 SQLite。
- 新增离线用例 `tests/test_query_rewrite.py`（9 例，打桩 LLM 不触网）。

### 新增（混合检索：向量 + BM25 · RRF）
- 检索从纯向量升级为**混合检索**：新增 BM25 关键词通道（自实现，仅标准库，无新依赖），
  两条通道用 RRF 融合。补上纯向量对专有名词 / 缩写 / 编号（"CUDA 12.4""第 3 条"）的召回短板。
- 新增配置：`HYBRID_ENABLED`（默认 1，置 0 退回纯向量）、`HYBRID_RRF_K`(60)、
  `HYBRID_KEYWORD_TOP`(20)、`HYBRID_MAX_DOCS`(20000，超限自动退回纯向量)。
- `select_diverse` 兼容 `(doc, distance)` 与 `(doc, distance, rank)` 两种候选；
  **关键词独有片段距离为 None，前端不显示相似度——绝不伪造分数**。现有调用方与回归用例零改动。
- 关键词索引按库缓存，随 `_cache_invalidate`（导入 / 删除 / 重建）失效并懒加载重建。

### 修复（超时与下载收口）
- LLM / 视觉 / Embedding 客户端补 `request_timeout` 与 `max_retries`（新增 `LLM_TIMEOUT`、
  `LLM_MAX_RETRIES`、`EMBED_TIMEOUT`、`EMBED_MAX_RETRIES`），消除单次请求无限挂起的隐患。
- `/api/kb/download` 由「整包读内存」改为**流式转发**（256KB 一块）+ 200MB 上限
  （优先按 `Content-Length` 拒收，缺失时流式兜底拦截），与 Agent 工具通道下载对齐。

### 性能（后端第二轮 · 第一/第二梯队）
- **第一梯队**：SQLite 连接按线程复用、归属校验并入 JOIN、历史查询尾部 LIMIT、
  LLM/Embedding 客户端记忆化、查询向量独立缓存 + 按向量检索 + `count` 合并。`0a2d72e`
- **第二梯队**：Agent 图按参数缓存、会话标题异步并行生成、
  入库 embedding 分窗并发（新增 `EMBED_CONCURRENCY`，默认 4，实测约 3.2×）、
  `list_sources` 分页 + 结果缓存 + 去重复扫。`0a2d72e`
- 均为纯优化，无 API 契约变化，前端零改动。详见 `docs/性能优化说明_2026-10-11.md`。

### 修复（安全与性能收口）
- 附件路径任意文件读取漏洞、计算器指数爆炸 OOM、同步 HTTP 阻塞事件循环；
  检索结果 LRU 缓存、图片压缩、下载流式分块 + 200MB 上限、导入去重、
  LLM `max_tokens` 兜底、Agent 步数硬限、配额先验后扣。`793207e`

### 文档
- 新增舍友本地启动指南（傻瓜式步骤 + 排错表）。`c639e35`
- 更新大学生智能体平台需求问卷（精简 7 题版）。`c0811a9`

### 新增
- 可行性分析更新 + 问卷星导入说明；新增校园工具集 / 平台本地预览原型。`25ac87b`

---

## [2026-10-10]

### 变更（仓库产品化）
- 清理历史方案 / 补丁脚本、补 MIT LICENSE、脱敏真实 IP、README 补架构图。`3eb0db9`
- 工程化与类型注解：新增 CI / `pyproject.toml` / `CONTRIBUTING`，补全核心模块返回类型注解。`1c7e53e`

### 新增
- 模板广场原型 + 大学生智能体平台需求问卷。`0af5a91`
- NBA 知识库扩展资料（历史 / 球队 / 赛制 / 奖项 / 纪录 / 国际化 / 战术）。`74b1a0c`

---

## [2026-10-06]

### 新增
- 多用户隔离 + 鉴权 + 配额 + SSRF 收口（P1–P3 落地）。`8f6c051`
- 管理员无配额 + 前端显示额度 / 管理员标识。`8c312c4`

### 变更
- 调低默认配额到保守档（问答 60 / 向量化 1000 / 分钟 10），适配亲友限定部署。`b390c88`

---

## [2026-10-05]

### 修复
- 联网建库空库写入失败 / Agent 相似度口径 / 后端只监听回环。`5c1408b`

---

## [2026-10-04]

### 修复
- 前端资料库勾选框在没有当前会话时静默失效。`d2c96e1`
- 更正 RAG 相似度 bug 的线上实测数值并补可复现验证说明。`52a6eaf`

### 工具
- 新增线上轻量压测脚本（低并发、可熔断、不留脏数据）。`553772d`
- 新增现场演示用压测脚本（零第三方依赖）。`c9a0d7b`

### 文档
- 同步 README 到当前代码现状 + 补 10-04 扫描报告。`4a7ef30`

---

## [2026-10-03]

### 修复
- `requirements.txt` 用三引号当注释，导致 pip 解析失败、部署卡在第 5 步。`45cdbf0`
- RAG 距离换算的守卫值域写错，导致离群过滤一直被旁路。`cde792c`

### 文档
- 报告补「附二：傍晚部署踩坑」——现象 / 根因 / 修法与副产品结论。`2280ba9`

### 变更
- 归档 09-28 以来累积改动：四个线上修复 + 三条性能 / 稳定性改动。`c787c6a`

---

## [2026-09-28]

### 新增（首个版本）
- 知穗agent：RAG 知识库问答 + 苞米地笔记本主题前端。`9e7cd31`
- 吉祥物 `MascotLogo` 组件（眨眼 / 漂浮 / 摇摆 / 腮红呼吸），替换四处 logo。`9d6e3f9`
- 吉祥物交互彩蛋：思考态（嘴变 ○）/ 完成眨眼 / 切模式歪头。`e926d9b`
- 模式切换改为 Codex 式滑块。`7a1de78`
- 上传图标改加号 + 浏览器地理定位自动附带（问天气免手输城市）。`d7916ed`
- 输入框上方定位角标（当前城市）。`b2ea697`

### 修复
- 链接显示解码百分号编码（乱码 URL 显示回中文），`href` 保持原值。`1c224b9`

### 变更
- 间距令牌化：定义 `--space-*` 刻度并替换 48 处硬编码 `gap`。`2808c7c`
- 聊天头像 28px → 44px，神态与交互彩蛋可见。`f93d58f`
- 输入栏图标放大至 20px。`b2ea697`

### 文档 / 清理
- 重写 README —— 苞米地笔记本产品化呈现。`20c65ac`
- logo 预览页新增交互彩蛋演示区（思考态 / 眨眼 / 歪头可点）。`aaf8aeb`
- 删除旧 `corn-logo.webp`，吉祥物 SVG 已全面接管 logo 与 favicon。`d93143f`

---

<!-- 维护约定：
  1. 每次发布 / 合并到 main 时，在文件顶部「当前日期」段落追加条目，按 新增/变更/修复/文档/性能 分组；
  2. 条目末尾附提交哈希，便于追溯；
  3. 详细的技术说明写入 docs/ 下的专题文档，此处只保留一行摘要 + 指向。 -->