# 06_编排层_Agent与混合引擎_升级版（2026-10-20）

> 对应研讨卷：`docs/技术选型研讨_2026-10-20/06_编排层_Agent与混合引擎.md`
> 本卷回答的问题：从校赛到省赛，编排这一层要改什么、为什么现在必须改、改完怎么验。
> 写作口径：现状在 dev 分支实读核对（含前端消费侧），引用 `文件:行`；未实证处标「（待核对）」。
> **一处对研讨卷的更正**：研讨卷 §6.6 记的「`TOOL_PLAYBOOK` 是空字典」已不成立——现在五个工具的说明已全部补齐并强校验（见 §1）。

---

## 0. 一句话结论

> **这一层的机制是全项目最厚的一层（元数据表、守卫、三重预算、三层防抖、表达式沙箱都齐了），但它目前的完成度是"对机器成立"，还不是"对用户成立"。**
> 三件最该改的：**① Agent 每一步的工具调用轨迹必须对前端可见且刷新不丢；② 三条执行路径的上限口径必须统一，且超限是"收编"不是"崩"；③ 工具失败要有结构化的回退路由，而不是靠文案特征猜它是不是失败了。**
> 这三条做完，"看着它一步步查资料再回答"就是评委能亲眼看到的东西——这是功能效果 20% 里最直观的一分，也是 Agent 类作品最容易被质疑"是不是编的"的地方。

---

## 1. 现状盘点

### 1.1 Agent 主体

| 组件 | 现状 | 出处（文件:行） |
|---|---|---|
| 构建方式 | LangChain v1 `create_agent`（LangGraph），checkpointer **故意为 None**，记忆交给 SQLite 会话库 | `agent/builder.py:5-12,115-137,141` |
| 图缓存 | LRU 32，键 = `(has_kb, scene, activated, prompt_version)`；提示词版本必须进键 | `agent/builder.py:149-164,200-201,259-262` |
| 提示词来源 | `prompt_store.resolve("agent", ...)`，支持 GEPA 自进化 | `agent/builder.py:200` |
| 动态信息 | 定位不进 system prompt，合并到最后一条 HumanMessage（保 KV 缓存前缀） | `agent/builder.py:228-234`、`api/chat.py:1046-1051` |
| 步数上限 | `recursion_limit=10`（LangGraph 超步），超限抛异常 → **整轮失败** | `api/chat.py:1058-1059,1145-1148` |
| 单次长度 | `AGENT_LLM_MAX_TOKENS` 默认 **0**（＝不封顶） | `core/config.py:221`、`agent/builder.py:214` |
| 身份注入 | `owner_id` / `kb_id` 走 `RunnableConfig.configurable`，模型在参数里编造 `kb_id` 无效 | `api/chat.py:1053-1065` |
| 工具清单 | 5 个：`kb_search` / `web_search` / `calculator` / `get_weather` / `download_file` | `agent/tools.py:604` |
| 工具元数据 | `ToolMeta`（category / defer / retryable / needs_approval / max_output_chars / cost_hint）齐全 | `core/tool_registry.py:66-143` |
| **工具说明书** | **`TOOL_PLAYBOOK` 已填满五个工具**，必备四小节（何时用/何时不用/参数/失败怎么办），导入即强校验 | `core/tool_registry.py:161-186,237-273,416-418` |
| 工具守卫 | 输出截断 + 不可重试封禁 + 危险动作审批（确认串＝`(工具名,参数)` 的 hash），沿用原 schema 重建 | `core/tool_guard.py:96,113,134-210,234-265` |
| 激活集隔离 | 按 `owner_id` 分桶，请求开始时取定、整轮不变 | `core/tool_registry.py:196-202` |
| ⚠️ 未接线 | `ToolMeta.category`（read/write/command）**声明了，但没有任何调度逻辑消费它**——工具实际仍是模型串行调用 | `core/tool_registry.py:50-57` 与 `core/tool_guard.py` 全文对照 |

### 1.2 混合引擎 `hybrid/`

| 组件 | 现状 | 出处 |
|---|---|---|
| 自研工作流 DSL | JSON 定义落 SQLite，运行期可改（这是自研而非用 LangGraph 的主因） | `hybrid/workflow.py:51-84`、`hybrid/store.py:276-288` |
| 节点类型 | `llm / tool / agent / condition / human` | `hybrid/workflow.py:51-62` |
| 内置流程 | `builtin_plan_execute`（规划 → Agent 执行），**已随 `_init_db` seed 入库** | `hybrid/store.py:37-68,246-263` |
| 切换规则 | `SwitchRule{signal, condition, action, priority, cooldown_seconds}`；**系统默认规则已 seed** | `hybrid/switcher.py:33-50`、`hybrid/store.py:220-244` |
| 规则生效逻辑 | 用户行同名覆盖系统行，按 priority 升序 | `hybrid/store.py:410-430` |
| 三层防抖 | 规则级冷却 / 方向级冷却 / 硬上限 `HYBRID_MAX_SWITCHES=5` | `hybrid/switcher.py:9-16`、`core/config.py:200-202` |
| Agent 三重预算 | 步数 10 / 时长 300s / token 8000（chars//2 折算），**全部走"收编"** | `hybrid/agent_engine.py:137,161-174`、`core/config.py:204-206` |
| 步数超限 | `GraphRecursionError` 被翻译成 `budget_exhausted` 收编信号（**hybrid 路径已做，api/chat 路径没做**） | `hybrid/agent_engine.py:249-257` |
| 工具连败 | 连续 3 次返回含失败提示 → `agent_stuck` 终止 | `hybrid/agent_engine.py:34,36,219-241` |
| 表达式安全 | 自研 AST 求值器，白名单算子；模板渲染用正则只认 `{question}` 与 `{shared.x}`（避开 `eval` / `str.format`） | `hybrid/expr.py:196,218`、`hybrid/workflow.py:113-116` |
| 人工节点 | `_Suspend` 异常穿透 retry 包装；`/runs/{id}/resume` 端点恢复并重开 SSE | `hybrid/workflow.py:35-46`、`api/hybrid.py:189-202,235-258` |
| 执行日志 | `exec_logs` 落库 + `/runs/{run_id}/logs` 回放 | `hybrid/store.py:499-519,521-534`、`api/hybrid.py:153-156` |

### 1.3 对前端的可见性（消费侧）

| 项 | 现状 | 出处 |
|---|---|---|
| 工具调用帧 | SSE `tool` 帧：`{name, input[:200]}` / `{name, output[:300]}` | `api/chat.py:1117-1132`（agent 路径）、`:964-973`（hybrid 路径） |
| 前端消费 | `case "tool"` 把帧 append 进该条消息的 `toolCalls` | `frontend/src/app/page.tsx:375-385` |
| 混合轨迹 | 独立时间轴组件，处理 `node_enter / node_exit / switch`，支持从 logs 重放 | `frontend/src/components/HybridTrace.tsx`、`frontend/src/lib/useHybridTrace.ts:117-131,161-205` |
| ⚠️ **刷新后** | hybrid 路径靠 `run_id` + `/logs` 可重放；**普通 agent 路径的 `toolCalls` 不落库，刷新即丢** | `core/session.py:124-132`（messages 表无 trace 列） |
| 缺少的字段 | 工具帧无 **耗时 / 成功失败 / 返回字符数 / 是否命中守卫（审批·封禁·截断）/ 步序号** | `api/chat.py:1117-1132` |

---

## 2. 校赛够用 vs 省赛不够

| 维度 | 校赛口径 | 省赛口径 | 差距 | 证据 |
|---|---|---|---|---|
| **过程可见** | 回答里带来源编号就行 | **每一步工具调用了什么、多久、成功没成功，用户/评委全程可见** | 有 tool 帧但不完整（无耗时/成败/守卫标记），且 agent 路径刷新即丢 | `api/chat.py:1117-1132`、`core/session.py:124-132` |
| **超限行为** | 到顶就停 | 到顶**优雅收编**：用已有信息作答 + 明确告知 | 三条路三种口径：`recursion_limit=10` 硬失败 / hybrid 三重收编 / 单次长度默认不封顶 | `api/chat.py:1058` vs `hybrid/agent_engine.py:161-174` vs `core/config.py:221` |
| **失败回退** | 工具错了模型换个工具试试 | **有声明的回退路由**：确定性失败 → 换替代工具；临时失败 → 有限重试 | 依赖 `_FAIL_HINTS` 文案关键词判断失败（"不可用/失败/无法…"），脆弱且不可控 | `hybrid/agent_engine.py:36,219` |
| **错误可读** | 报错能看懂就行 | 不能出现英文异常栈 | `GraphRecursionError` 走 `describe_api_error` 兜底，原样吐给用户 | `kb/rag_core.py:71-88`、`api/chat.py:1145-1148` |
| **成本可控** | 有日配额就行 | **单轮 token 预算**，达预算优雅收尾 | `/api/chat` 的 agent 路径只有步数，**没有 token 预算**，且 `max_tokens=0` | `core/config.py:221`、`api/chat.py:1058` |
| **混合模式可演示** | 能切换就行 | 有"开关 → 降级 → 健康可见"的完整故事 | 开关与默认规则都有（`store.py:220-244`），但没有降级计数与健康指标 | `core/config.py:198-212`、`hybrid/switcher.py` |
| **人机协同** | 有人工节点 | 委托/计划这类跨天任务也能"暂停—追问—继续" | human 挂起只在 hybrid 工作流内可用，委托走的是 worker 独立进程 | `hybrid/workflow.py:35`、`worker/scheduler.py:268-313` |
| **工具生态** | 5 个够演示 | 扩展不需要改代码发版 | 工具硬编码在 `AGENT_TOOLS`，加工具要动代码 | `agent/tools.py:604` |

---

## 3. 升级项

### P0-1　工具调用轨迹：完整、统一、可回放

- **现状**：SSE 已经在发 `tool` 帧（`api/chat.py:1117-1132`），前端已经在收（`page.tsx:375-385`），hybrid 甚至有完整时间轴。缺的是三件事：**字段不全**（无耗时/成败/字符数/是否命中守卫/步序号）、**两路口径不一致**（agent 路径 vs hybrid 路径的帧结构不同）、**agent 路径不落库**（`messages` 表没有 trace 列，刷新后 `toolCalls` 丢失，而 hybrid 靠 `run_id` + `/logs` 能重放）。
- **改什么**：
  1. 定义统一 trace 帧：`tool_step {seq, name, args_digest, ok, ms, out_chars, guard}`（`guard` ∈ `null | approval | banned | truncated`，直接复用 `core/tool_guard.py:45-55` 已有的 `_GuardState` 统计）；
  2. agent 路径补上 dur/**成败**/字符数，并把 hybrid 的 `ExecEvent`（`hybrid/context.py:110`）与它对齐——**两条事件流同构**，前端一套组件渲染；
  3. `messages` 表按现有迁移风格加一列 `trace TEXT`（照抄 `core/session.py:241-244` 的 `ALTER TABLE ... try/except` 写法），`done` 帧不变，取历史时由前端从该列重建时间轴；
  4. 摘要必须过 `core/netsafe.py` 与长度上限（工具返回值里可能有 URL/密钥片段）。
- **为什么省赛必须改**：Agent 类作品最大的质疑是"它到底调用工具了没有"。一条**可回放的、带耗时与成败的工具时间线**，是把"它有自主能力"从自述变成证据的唯一办法。这一项直接吃功能效果 20% 里的"系统测试数据充分"。
- **代价/风险**：SSE 帧数变多（一次问答多 5~15 帧，可接受）；`trace` 列会让 messages 行变大，务必只存摘要（单条 ≤300 字符，整轮 ≤20 条）；老数据显示为空要优雅降级；`core/netsafe.py` 具体做了哪些脱敏需核对后再定要不要二次处理（**待核对**）。
- **反转条件**：若前端排期不足，先只做"后端补 `ok` / `ms` / `out_chars` 三个字段 + 落库"，UI 下个迭代再做时间线。即使不上 UI，落库本身也让"事后复盘"成为可能。
- **验收口径**：一条包含 ≥3 次工具调用的问答 → 时间线显示每次的序号/工具名/耗时/成败/字符数；刷新页面后时间线仍在（来自 `trace` 列）；工具返回被 `max_output_chars` 截断时该步标 `guard=truncated`；`download_file` 首次调用标 `guard=approval`。

### P0-2　上限口径统一：超限是"收编"，不是"崩"

- **现状**：
  | 路径 | 步数上限 | 时长上限 | token 上限 | 超限行为 |
  |---|---|---|---|---|
  | `/api/chat` agent 路径 | `recursion_limit=10` | 无 | 无（`AGENT_LLM_MAX_TOKENS=0`） | 抛 `GraphRecursionError` → 进 `except Exception` → 用户看到英文异常 |
  | `hybrid` Agent 引擎 | `HYBRID_AGENT_MAX_STEPS=10` | 300s | 8000（chars//2） | **收编**（`status="switched"` + 信号 + 已有文本照常返回） |
  | 单次生成 | —— | —— | `max_tokens=2048`（主回答）/ `0`（Agent 主循环） | 截断，已有 `truncation` 帧提示 |
  同一个"步数到了"的概念，在两条路上一种是失败、一种是优雅降级——这不是设计，是历史。
- **改什么**：
  1. **agent 路径改收编**：用 `st["steps"]` 在 `updates` 事件里自己计数（hybrid 的做法，`agent_engine.py:205`），到阈值不再依赖 LangGraph 抛异常，而是最后追加一条"请用已有信息作答"的系统消息让模型收尾；同时发一个 SSE `limit` 帧；
  2. **`AGENT_LLM_MAX_TOKENS` 默认值改 2048**（`core/config.py:221` 的注释自己已经写了"生产建议按 `HYBRID_AGENT_TOKEN_BUDGET` 同量级设置"）。这是已知的、被兼容性理由保留下来的敞口，新部署应当带上；
  3. **补翻译**：`describe_api_error`（`kb/rag_core.py:71`）增加 `GraphRecursionError` 分支："这个任务需要的步骤太多，已中止。可以试着把问题拆小一些。"——这条错误万一漏出来，用户看到的应是可执行建议；
  4. 给三条路径各补一个指标：`agent_limit{reason=steps|seconds|tokens|recursion}`。
- **为什么省赛必须改**：这是"无人值守系统"的及格线。省赛评委自助试用一定会撞到复杂问题，届时"崩"和"优雅收尾"是两种完全不同的印象分；而三重预算（步数/时长/token）统一这件事本身，就是技术方案分里能讲三分钟的干货。
- **代价/风险**：改默认 `max_tokens` 会让现有长回答变短（这正是当初留 0 的原因）→ 用 env 开关 + 渐进放量，并观察 `chat_truncated` 计数（`api/chat.py:566`）有没有异常上升；自行计数可能与 LangGraph 的实际步数略有偏差，取"先到的为准"即可。
- **反转条件**：若自计数与异常路径长期不一致（即仍有大量 `GraphRecursionError` 漏出）→ 保留异常兜底，但把它翻译成中文（第 3 条无论如何都做）。
- **验收口径**：构造一个必然超限的任务 → 三条路径都在预算内给出**非空且可读**的回答 + 一个 `limit` 帧；界面上不出现任何英文异常；`agent_limit_total` 计数正确。

### P0-3　工具失败的结构化回退矩阵

- **现状**：`ToolMeta.retryable` 已经有了（`core/tool_registry.py:66-79`），`tool_guard` 也据此封禁（`tool_guard.py:154-159,189-196`）。但三处仍然靠**自然语言**：
  1. `hybrid/agent_engine.py:36` 的 `_FAIL_HINTS` 用文案关键词判断"这步是不是失败了"——工具改一句文案就判定失败，反之亦然；
  2. 没有声明"这个工具失败后应该改用什么"（`kb_search` 不可用 → `web_search` 是人人都懂的回退，但现在只写在各不相同的地方）；
  3. `tool_guard.py:215-228` 的 `_looks_like_deterministic_failure` 同样是文案特征匹配。
- **改什么**：让失败成为一个**结构化返回值**而不是一串字：
  1. 工具返回一个内部信封 `{ok: bool, kind: "deterministic"|"transient", code: str, message: str}`，`guarded_run`（`tool_guard.py:134`）负责把它拆成"给模型看的中文"与"给引擎看的字段"；
  2. `ToolMeta` 增加 `fallback_to: tuple[str, ...]`（如 `kb_search → web_search`）与 `fallback_hint`；引擎看到 `kind=deterministic` 时把这个 hint 注入下一条消息，而不是让模型自己想；
  3. `_FAIL_HINTS`（`agent_engine.py:36`）降级为**兜底**而非主要判据，逐步删掉。
- **为什么省赛必须改**：这一层研讨卷自己总结过主线——"把策略从提示词移到元数据"（研讨卷 §6.16 主线 1）。`retryable` 已经完成迁移，**失败分类与回退还没有**。而"5 个工具里 4 个可串行替代"这件事，是 Agent 编排正当性的核心论据。
- **代价/风险**：改返回值形态会牵动 `truncate_output`（`tool_guard.py:113`）之后的全部逻辑，回归面不小；必须保证**模型看到的仍是纯字符串**（外观不变），只有引擎读结构化部分。
- **反转条件**：若改造期过半还没跑通，退回"只给 `download_file` 之外的确定性工具补 `fallback_to` 声明"，其余维持现文案判据。
- **验收口径**：故意让 kb 不存在（换一个未绑定的 kb_id）→ `kb_search` 返回结构化 `deterministic` 失败 → 模型一轮内改用 `web_search`，且不再重复调用 `kb_search` ≥2 次；`tool_guard` 的 `banned` 集合被正确置位（现有 `tests/test_tool_guard.py` 应扩展这条）。

### P1-1　混合引擎的开关、降级与健康可见

- **现状**：`HYBRID_MODE_ENABLED`（`core/config.py:198`）+ 默认规则 seed（`store.py:220-244`）+ 内置工作流（`store.py:37-68`）——**研讨卷 §6.16 记的"规则表可能为空、功能可能未接线"这一条已经不成立**，这里如实更正。缺的是降级链路：混合引擎内部异常时，用户看到的是"混合执行失败：……"（`api/chat.py:1001,1011`），而不是"已自动改用普通问答继续为你服务"。
- **改什么**：三条——① 混合引擎初始化/执行失败时**自动降级到 rag 或 agent 路径**（`done.mode` 已经有最终模式的语义，`api/chat.py:400-415` 前端已据此识别静默回退，只需后端真的会退）；② 加健康与降级计数指标（`hybrid_switch_total{rule}`、`hybrid_degraded_total{reason}`）；③ 把降级开关做成 env（`HYBRID_AUTO_FALLBACK`）。
- **为什么省赛必须改**：混合执行是本项目最能体现"技术方案"的部分，但如果它在演示现场失败一次且不可降级，反而会成为污点。"有自动降级"这件事本身就是答辩素材。
- **代价/风险**：自动降级会掩盖问题（用户以为正常，实际走了弱路径）→ 必须在 SSE 发 `status` 帧显式说明，并落到 `done.mode`。
- **反转条件**：若降级逻辑与切换规则互相打架（例如规则把流量又切回来）→ 只保留"初始化失败才降级"，运行期失败仍报错。
- **验收口径**：手动让 `builtin_plan_execute` 定义非法 → 请求不 500，自动落到 rag/agent，UI 显示"已回退"，`hybrid_degraded_total` +1。

### P1-2　多轮计划 / 委托的状态机

- **现状**：委托与每日总结跑在**独立 worker**（`worker/scheduler.py:268,290,313`），状态机在 `core/scheduler.py:64-77`（`pending/running/succeeded/failed/skipped` + `CLASS_RETRY/FATAL/QUOTA` + `MAX_ATTEMPTS=3` + `BACKOFF_MINUTES=(1,5,15)`）。这套 IO 级的状态机很完整，但**没有"人机协同"这一档**；而 hybrid 内部已经有 human 节点 + `_Suspend` + `resume` 端点（`api/hybrid.py:189-202`），两者没有接上。
- **改什么**：给委托加一层**业务状态**（草稿 → 待确认 → 执行中 → 已挂起 → 已完成/已取消），跨进程共享（worker 与 web 读写同一张 SQLite 表，照 `core/scheduler.py` 的连接写法）；挂起时复用 `_Suspend` 语义，恢复入口复用 `/runs/{id}/resume` 的模式。
- **为什么省赛必须改**："每天跟着你推进一个长期目标、卡住了会回来问你"是主题创意 30% 里最有画面感的场景；而它会反过来证明 state machine 的存在不是摆设。
- **代价/风险**：跨进程状态 + SSE 提示的组合是最容易出不可复现 bug 的地方；且 worker 与 web 各自 `_LOCAL` 连接缓存（`core/scheduler.py:171`）需要显式失效策略。
- **反转条件**：若跨进程的部分一周内压不稳 → 缩回来只做"Web 侧的委托暂停/继续"，worker 侧维持 IO 级状态机不变。
- **验收口径**：委托执行到需要确认的节点 → 前端弹出等待提示；等待 10 分钟后从另一台浏览器继续，任务从断点接着跑；期间 worker 重启不丢状态。

### P1-3　成本闸：单轮 token 预算

- **现状**：`HYBRID_AGENT_TOKEN_BUDGET=8000`（chars//2 折算，`hybrid/agent_engine.py:168`）只保护 hybrid 路径；`/api/chat` 的 agent 路径没有 token 预算，且 `AGENT_LLM_MAX_TOKENS=0`。
- **改什么**：在 `/api/chat` 的 agent 路径引入同一套累计（累加 `delta` 长度 + `ToolMessage` 长度，用现成的 `core/context_compress.py:385` `estimate_tokens`），达到预算就走 P0-2 的收编路径；本轮消耗与触发原因写入 04 卷 P0-2 的 `llm_calls` 明细（两卷联动）。
- **为什么省赛必须改**：这是 04 卷 P0-2（成本可观测）的自然延伸——**可观而不可控，等于只做了一半**。而且"单轮额度有闸"本身也是对"已投入实用"这一评分项的直接回应。
- **代价/风险**：chars//2 折算对中文偏粗（偏高估），必须写清它是估算；如果高估太多会导致正常使用被提前收编 → 先用 8000 → 观察 `agent_limit{reason=tokens}` 的分布 → 再调。
- **反转条件**：若一周内该原因触发率 >5% 且多为误伤 → 提高预算，或改成"先发一次收尾提示、再终止"的两阶段策略。
- **验收口径**：注入一个超预算任务 → 达到预算后 1 轮内收尾，实测 token 消耗 ≤ 预算的 110%（对照 04 卷落库的数）。

### P1-4　工具生态：插件化 → MCP

- **现状**：工具硬编码在 `AGENT_TOOLS`（`agent/tools.py:604`），加一个工具要同时改三个地方（`tools.py` 定义、`TOOL_META`、`TOOL_PLAYBOOK`），并且过不了启动校验就会直接起不来（`tool_registry.py:416-418`）。这个"宁可起不来"的设计是对的，但它让"用户在运行期装工具"不可能。三层渐进式加载（meta → playbook → resources）与延迟发现 `discover()` 都已经就位（`tool_registry.py:365-398`），只是 `TOOL_RESOURCES` 仍为空。
- **改什么**（两步，先易后难）：
  1. **插件化**：把工具注册改成数据驱动——一个 `tools/` 目录下的清单（名字/实现路径/meta/playbook）在启动时装载并做同样的三项校验；这让"加工具不改框架"成立，也让 `TOOL_RESOURCES` 有落地位置；
  2. **MCP**：接入后把外部 MCP 工具映射成 `StructuredTool` + `ToolMeta`。**三条硬约束**：① 必须过 `build_guarded_tools`（`core/tool_guard.py:234`），否则守卫形同虚设；② 外部来源工具默认 `defer=True` + `needs_approval=True`（它会改外部世界）；③ 工具集合入图缓存键（`agent/builder.py:164`），保证工具前缀稳定、KV 缓存不被打掉。
- **为什么省赛必须改**：这是"技术方案"分里最符合当下技术风向的一项，而且能直接对上 Dify / n8n 已经支持 MCP 的事实（见 §4）——**别人有的你没有，评委一定会问**。
- **代价/风险**：新的依赖与版本锁（项目已经吃过 `langchain-chroma==0.2.3 → chromadb → Python 版本` 的四层链）；安全模型是最大的隐性工作量（外部工具可执行代码级操作）；2G 内存下每多一个常驻进程/模型都有代价。
- **反转条件**：两周窗口 + 三人并行排不下时，**优先做第 1 步（插件化）**——它零新依赖、纯收益，且能把"工具生态"这件事至少讲到一半；MCP 降级为演示路演里的"下一步规划"。
- **验收口径**：新增一个工具**不改 `builder.py` / `rag_core.py` 任何一行**，只放清单文件 → 服务起得来、`all_meta()` 能查到、Agent 能调用；接一个只读 MCP 工具 → 它的守护卫生效（超长输出被截断、`exec_logs` 有记录）。

### P2　（本期只列不做）

- **并行工具执行**：`ToolMeta.category` 已经区分 read/write/command（`core/tool_registry.py:50-57`），但引擎从不并发。真要做需要改 `build_guarded_tools` 之上那一层，风险与收益不成比例，先留着作为"声明已就位"的证据。
- **多 Agent 委托 / 角色分工**：调 Worker A 去做调研、Worker B 去写稿——需要更细的路由与更贵的预算，不适合三人团队。
- **工作流可视化编排画布**：JSON 定义已经支持（`store.py:276-288`），但前端画布的投入与主题创意分无关，排到最后。

---

## 4. 竞品对照

| 产品 | 这一层怎么做 | 我们差在哪 | 要不要跟 |
|---|---|---|---|
| **Dify**（142k⭐，Workflow+RAG+Agent+LLMOps，2核4G，**已支持 MCP**） | 可视化工作流 + Agent 节点切换，执行轨迹逐步展示，节点级日志可回看 | 轨迹粒度我们不差（hybrid 还有可回放的 `exec_logs`）；差的是 **MCP 工具生态** 与"运行期装工具" | **跟**：P1-4 的 MCP 接入，是这一层缺口最小、收益最高的一条 |
| **FastGPT**（26.1k⭐，2核4G，中文问答优化、流式快、高并发） | 简单工作流编排；工具以"插件/系统工具"形式给出 | 我们多了元数据表、守卫、预算三重闸 | 不跟；编排深度我们已在它之上 |
| **RAGFlow**（80.7k⭐，需 4核16G） | Agent 能力服务于文档场景，不是通用编排 | 不适用 | 不跟 |
| **Coze 扣子**（闭源、插件生态丰富、一键分发、模型不可自定义） | **插件商店 + 一键分发**是它最大的卖点；但闭源、不可自托管 | 我们的工具是硬编码 5 个；但我们的工具可以本地扩展 | **跟一半**：插件清单化（P1-4 第 1 步）对标它的"插件"；不跟它的分发形态 |
| **n8n**（188k⭐，400+ 集成、代码节点、MCP） | 集成数量是碾压性的，失败走错误分支 | 无可比性：它是集成编排平台，不是 Agent 引擎 | **只跟一个思路**：把降级做成显式的控制分支（P1-1），而不是 try/except |

一句话：**编排层我们已经站在"比大多数校园项目深"的位置上，唯一会被追问的缺口是"工具能不能动态扩展 / 接 MCP"。** 其余資源都该花在"让已有的深度被看见"（P0-1）和"让已有的边界行为统一"（P0-2 / P0-3）。

---

## 5. 落地顺序与验收口径

| 顺序 | 事项 | 依赖 | 验收口径 |
|---|---|---|---|
| **1** | **P0-2 上限统一**（含 `AGENT_LLM_MAX_TOKENS=2048`、`GraphRecursionError` 中文化、三类指标） | 无 | 构造超限任务：三条路径都不崩、不留英文异常、指标正确 |
| **2** | **P0-1 trace 扩充 + 落库** | 1（先把"超限/守卫"这些状态定义好，才好记进 trace） | 见 P0-1 口径；刷新后轨迹仍在 |
| **3** | **P0-3 结构化失败 + fallback_to** | 1 | 见 P0-3 口径；`kb_search` 失败一轮内改道 |
| 4 | P1-1 混合降级与健康指标 | 1 | 见 P1-1 口径；降级可被观测且被显式告知 |
| 5 | P1-3 单轮 token 预算 | 1、以及 04 卷 P0-2 落库 | 见 P1-3 口径 |
| 6 | P1-4 第一步：工具清单化 | 3（先把 meta/playbook 的校验挪到装载时） | 加工具不改框架代码 |
| 7 | P1-4 第二步：MCP 只读工具试点 | 6 | 守卫生效、可被 Agent 调用 |
| 8 | P1-2 委托状态机 + 人机协同 | 4（复用 human/resume 语义） | 跨会话挂起与续跑 |
| — | P2-* | — | 不做 |

统一红线：
- 每一步都必须帶 env 开关（沿用 `core/config.py:198-231,340-366` 那一组"一行 env 回退"的既有惯例）；
- 每改一处 MCP/tools evidence，都必须同步更新 `core/tool_registry.py` 的导入即校验，绝不能出现"声明与实现分叉"——这正是研讨卷 §6.5 反复强调的那条。

---

## 6. 不做的事（及为什么）

| 不做 | 为什么 |
|---|---|
| **不用 LangGraph 重写 `hybrid/`** | 自研的五个理由里，最硬的一条是"工作流定义要存表、运行期可改"（研讨卷 §6.12 ①）。只要这条成立，迁回 LangGraph 就是净损失 2000 行换不来任何东西。**反转条件单一且明确**：放弃运行期可编辑。 |
| **不挂 checkpointer** | 三条实测依据 + 一条"assert 必须为 None"的否定性测试（`tests/test_agent_checkpointer.py`）已经把这条路焊死了。要挂就得同时改 `api/chat.py` 不再重放历史——那是成对变更，不是加个参数。 |
| **不做工具并行执行** | `category` 声明留着，但引擎不动。当前的串行代价是延迟，收益是控制流简单、副作用顺序可预测；在 2 核机器上，用并行度换可预测性是划算的 |
| **不做工作流可视化画布** | JSON 定义已支持，但画布是"作品呈现"里最容易被误会为"买来的"那部分。把同样的时间给 P0-1 的工具时间线，分数更高。 |
| **不为了"看起来智能"放宽任何一道护栏** | `MAX_ATTEMPTS=3`、`MAX_SWITCHES=5`、`retryable=False` 封禁、`needs_approval=True` 这些是"无人值守不出事"的保证。放宽它们能换来一次漂亮的演示，却会在评委自助试用时换成一次难堪。 |
| **不自研新的 Agent 协议** | `AGENT_TOOLS` + `bind_tools` 是现成的 function calling 链路，推翻它等于自建协议层（研讨卷 §6.5 已论证）。MCP 也要接在它上面，而不是另起炉灶。 |

---

> 三卷合起来的一句话：04 让调用**可信**，05 让依据**可靠**，06 让过程**可见**。省赛答辩的三个可演示现场分别是——**主模型挂了它照样答（04）、你说错它有依据地纠正（05）、它查了什么你看得见（06）**。
