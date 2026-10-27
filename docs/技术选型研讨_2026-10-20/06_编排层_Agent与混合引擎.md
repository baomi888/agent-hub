# 卷六 · 编排层：Agent 与混合引擎

> 本卷解决的问题：**把"模型自主决定调用什么工具"这件事，从"靠提示词祈祷"变成"有声明、有预算、有兜底的工程结构"。**
> 这一层的核心矛盾是：自主性越强，可控性越差；而一个无人值守的系统，可控性比自主性重要。

---

## 6.0 编排层要解决的真实问题

"Agent"这个词被用得很泛。在本项目里，它具体指四件事：

1. **选择**：给定问题，模型决定调不调工具、调哪个；
2. **循环**：调完工具后，模型看结果，决定再调还是回答；
3. **状态**：多轮之间记住说过什么；
4. **边界**：不能无限循环、不能无界花钱、不能越权。

前三件是"能力"，第四件是"约束"。

**大多数 Agent 项目的失败不是能力不够，是约束不够。** 典型症状：

- 模型反复调同一个失败的工具（因为提示词说"可以用 web_search"，而 kb_search 返回了"不可用"）；
- 一次问答调了 30 次工具，账单 5 块钱；
- 模型把 `kb_id` 参数改成别的值，越权检索；
- 上下文里堆了 20 轮工具返回值，模型开始"忘记"原始问题。

本项目的所有编排层设计，都是针对这四个症状。

---

## 6.1 `create_agent` 而非 `AgentExecutor`

### 两代 API

| API | 时代 | 机制 | 状态 |
|---|---|---|---|
| `AgentExecutor`（`initialize_agent`） | LangChain 0.x | 自己实现 ReAct 循环，用 `Agent` 类决定下一步 | 已被标记为 legacy |
| `create_agent` | LangChain v1（底层 LangGraph） | 编译成状态图，节点/边由框架生成 | 当前推荐 |

### 为什么迁到 `create_agent`（`agent/builder.py:5-12`）

> 核心：`create_agent(model, tools, system_prompt)`
> - 底层 LangGraph，自动处理 ReAct 循环 / 工具路由 / 流式输出
> - **不注入 checkpointer**：会话记忆由 SQLite 会话库承载
> - 比旧版 `AgentExecutor` 更稳定，流式事件更丰富

"流式事件更丰富"是实际的迁移动机。`AgentExecutor` 的 `astream` 只给最终输出或中间步骤的粗粒度事件，而 LangGraph 的 `astream(stream_mode=["updates", "messages"])` 能同时给：

- `messages` 模式：逐 token 的 `(chunk, metadata)`；
- `updates` 模式：每个节点的输出（包含 `AIMessage.tool_calls` 和 `ToolMessage`）。

这正是前端需要区分"工具调用发起/返回"和"token 增量"的基础（见 `api/chat.py:915-955`）。

### 第三个选择：自研 ReAct 循环

为什么不自己写？

一个最小的 ReAct 循环大约 50 行：

```python
while steps < max_steps:
    resp = llm.invoke(messages, tools=schemas)
    if not resp.tool_calls:
        return resp.content
    for tc in resp.tool_calls:
        result = execute(tc)
        messages.append(ToolMessage(result))
```

看起来很简单。但 `create_agent` 多做的事：

1. **工具调用的并行执行**（多个 tool_call 同时跑）；
2. **错误处理**（工具抛异常时生成 `ToolMessage` 而不是崩掉整个图）；
3. **状态管理**（`messages` 的追加、裁剪、序列化）；
4. **流式的正确分帧**（哪些 chunk 是 token、哪些是节点输出）；
5. **递归限制**（`recursion_limit`）。

自己写这五项，大约 500 行，且每一项都有边界情况。**这是一个"抽象值得用"的例子**——因为抽象层做的事和需求完全重合。

### 代价

- **版本耦合**：`create_agent` 是 v1 的新 API，它的行为可能随小版本变化（比如 `stream_mode` 的返回结构）。
- **调试困难**：出问题时错误栈在 LangGraph 内部，不容易定位。
- **`SummarizationMiddleware` 不存在**（注释里明确写了"在 langgraph 1.2.x 不存在"），所以长对话压缩必须自己做（见卷九）。

第三条是一个典型的"框架能力缺口导致自研"的例子。卷九的上下文压缩模块，本质上就是补这个缺口。

---

## 6.2 checkpointer 故意为 None：本项目最值得学的决策

### 表面上的矛盾

`requirements.txt` 里明确列了：

```
# ---------- LangGraph 检查点（Agent 多轮记忆落盘，必须装！）----------
# 不装的话，builder.py 会静默退化为 MemorySaver，重启服务记忆全丢。
langgraph-checkpoint-sqlite>=3.1
```

但 `agent/builder.py::_make_checkpointer()` **返回 None**。

```
依赖装了，代码不用 —— 这不是遗漏，是决策。
```

`README.md` 甚至专门用一个引用块解释这件事。说明这个决策**反复被质疑过**。

### 三条实测依据（`builder.py:115-137`）

**依据 1：等于记两份。**

> 会话历史本来就存在 SQLite 会话库（`core/session.py`），`api/chat.py` 每轮都会把完整历史重建成 messages 一起喂给 agent。再挂一个 checkpointer 等于**记两份**，而且这份还只在进程内存里。

展开：`api/chat.py` 的 Agent 分支每轮做这件事：

```python
history = store.get_messages(uid, req.sid, include_system=False)[:-1]
messages = []
for m in ctxcompress.assemble_history(uid, req.sid, history):
    messages.append(HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(...))
messages.append(HumanMessage(content=prompt_question))
async for event in agent.astream({"messages": messages}, config=config_, ...):
```

也就是说：**每轮都把完整历史重新构造进 `messages`**。如果同时挂了 checkpointer，LangGraph 会把这次 `messages` 追加到它自己维护的状态里。

于是有了依据 2。

**依据 2：重复累积（已实测）。**

> 第 2 轮状态里第 1 轮的消息出现 2 次、第 3 轮出现 3 次，3 轮后状态 12 条（本应 6 条）。上下文平方级膨胀，白烧 token，还会让模型看到重复提问。

算一下：

| 轮次 | 本应条数 | 实际条数 | 说明 |
|---|---|---|---|
| 1 | 2 | 2 | user + assistant |
| 2 | 4 | 6 | 第 1 轮 ×2 + 第 2 轮 |
| 3 | 6 | 12 | 第 1 轮 ×3 + 第 2 轮 ×2 + 第 3 轮 |

**这是 O(n²) 的 token 消耗。** 第 10 轮时，状态里有约 110 条消息，而本应 20 条。

这个数字（"3 轮 12 条 vs 6 条"）是整个论证的核心，因为它**可复现**。任何人只要挂上 checkpointer 跑三轮就能看到。

**依据 3：两个"直觉改法"都是错的。**

> 原实现 `SqliteSaver.from_conn_string(path)` 是**错的**：它是 `@contextmanager`，返回上下文管理器而不是 saver 实例，于是每次对话直接抛 `TypeError: Invalid checkpointer provided ... Received _GeneratorContextManager`。
> 注意改成 `SqliteSaver(sqlite3.connect(path))` 也不行 —— 同步 saver 不支持 `astream`（会换成 `NotImplementedError`），必须用 `AsyncSqliteSaver`，而它要在 lifespan 里 async 打开。

这是**三级失败路径**的记录，极其宝贵：

| 尝试 | 结果 | 学到了什么 |
|---|---|---|
| `SqliteSaver.from_conn_string(path)` | `TypeError: Received _GeneratorContextManager` | 它是 `@contextmanager`，要 `with` 或 `__enter__` |
| `with SqliteSaver.from_conn_string(path) as saver:` | 会拿到 saver，但 `with` 块一退出就关连接 | 生命周期与 app 不匹配 |
| `SqliteSaver(sqlite3.connect(path))` | `NotImplementedError`（astream 不支持） | 同步 saver 不能用于 async 图 |
| `AsyncSqliteSaver` + lifespan 打开 | 可以，但要处理启动/关闭时序 | 复杂度陡增 |

**收益 vs 成本**：收益是"多一份状态"（但那份状态本来就是重复的），成本是"改 4 个地方 + 处理时序 + 学一个异步 API"。

判断：**不划算**。而且依据 2 说明，即使挂上了，也必须同时改 `api/chat.py` 不再重放历史——那就变成了"用 checkpointer 替代会话库"，是一次架构级迁移，不是加个参数。

### 为什么这个决策值得学

因为它展示了**"依赖装了但不用"这种状态如何正确处理**：

1. **不删依赖**：`langgraph-checkpoint-sqlite` 保留，因为将来可能要用；删了会让"想用的人"要重新装。
2. **写清为什么不用**：三条例依据 + 两条"别照着直觉改回去"。
3. **留接口**：`get_checkpointer()` 保留，将来换落盘版不用改调用方。
4. **写测试锁死**：`tests/test_agent_checkpointer.py` 断言"checkpointer 必须是 None"。

第 4 点最反直觉：**为"不做某事"写测试**。

大多数测试是"验证做了某事"。而这里的风险是"后人顺手做了某事"——因为 LangGraph 的文档、教程、示例都在教你"要挂 checkpointer"。

只有一条会红的测试能阻止这件事。这是**用测试固化否定性决策**的典范。

### 反转条件

如果要挂 checkpointer，必须同时做两件事（`builder.py:252-256`）：

> ① 同步的 `SqliteSaver` 不支持 astream，必须用 `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`，并在 lifespan 里 async 打开；
> ② 必须同时让 `api/chat.py` 只发当前这一条消息，别再重放历史，否则消息会重复累积。

也就是说：这是一个**成对变更**，不能只改一半。

---

## 6.3 图缓存：为什么键里不能有定位信息

### 问题

`create_agent` 每次调用都会编译一遍 LangGraph 状态图（节点/边/路由）。这是纯 CPU 开销，在 2 核机器上约 10~50ms。

一次问答只编译一次不算什么。但如果每轮都编译，加上"首屏 3 个请求并发"，就会变成明显的延迟。

### 缓存设计

```python
_AGENT_CACHE: OrderedDict[tuple[bool, str, str], object] = OrderedDict()
_AGENT_CACHE_MAX = 32
key = (bool(has_kb), scene, frozenset(activated))
```

三个维度：

- `has_kb`：有没有绑定知识库（决定 system prompt 的一个分支 + 是否绑定 kb_search 的行为）；
- `scene`：场景预设（考研/笔记/论文，拼在 prompt 末尾）；
- `activated`：已激活的延迟工具集（决定 `bind_tools` 的集合）。

**为什么图可以跨请求复用**：因为 `checkpointer=None`，图本身不带会话状态。状态是每次 `astream({"messages": ...})` 传入的。

**这是一个精妙的耦合**：checkpointer 为 None 这个决策，直接使图缓存成为可能。如果挂了 checkpointer，图可能持有状态引用，跨请求复用就危险了。

### 定位信息曾经在键里（P0-1 修复）

原实现把 `location_note(location)` 拼进 system prompt，并且把 `note` 放进缓存键：

```python
key = (bool(has_kb), note, scene)
```

问题有两个（`builder.py:179-183`）：

> ① 城市一多变 LRU 抖动、反复重编译图（原本 2×#城市×#场景×#激活集）；
> ② 更贵的是 system prompt 前缀随城市变化，KV 缓存直接失效（实测命中率 90%→0%）。

**第一个问题**：如果用户在北京，key 是 `(True, "北京...", "kaoyan")`；换到上海，key 变成 `(True, "上海...", "kaoyan")` → miss。32 个 LRU 槽位很快被"城市 × 场景"填满。

**第二个问题更致命**：KV 缓存。

### KV 缓存是什么，为什么前缀一变就失效

LLM 的推理过程（简化）：

```
对每个 token: 计算 attention(Q, K, V)，其中 K/V 来自所有前序 token
```

如果前缀完全相同，K/V 可以复用（不需要重算）。这就是"前缀缓存"（prompt caching / KV cache）。

各家都实现了这个优化（DeepSeek 有 context caching，OpenAI 有 prompt caching），命中时**输入 token 的价格大幅降低**（通常 1/10）。

关键在于：**它只对"完全相同的前缀"生效。**

如果 system prompt 是：

```
你是一个智能问答助手...
...
【用户当前位置】北京（经纬度 39.9, 116.4）
```

换到上海后：

```
你是一个智能问答助手...
...
【用户当前位置】上海（经纬度 31.2, 121.5）
```

**前缀在第一行之后就不同了**（因为 system 是一个整体字符串，从"【用户当前位置】"开始就变）。所以整个 system 的 KV 缓存全部失效。

实测"90%→0%"——这个数字的含义是：原本 90% 的输入 token 可以命中缓存（因为 system + 历史是稳定的），现在 0%。

### 修复：把动态信息挪到末尾

```python
# P0-1：定位不再拼进 system prompt。
# 理由：system prompt 是**前缀**，动态内容拼进去 = 每换一个城市前缀全变，KV 缓存全废。
# 定位改由调用方在**最后一条用户消息**里追加，模型仍在回答前看得到，只是位置从"头部"挪到"尾部"。
```

实现（`api/chat.py:874-883`）：

```python
if location_ctx:
    _loc_note = location_note(location_ctx)
    if _loc_note:
        messages[-1] = HumanMessage(content=prompt_question + "\n\n" + _loc_note.strip())
```

**注意这里是"合并进最后一条用户消息"，不是"再追加一条 HumanMessage"**：

> 合并进最后一条用户消息而不是再追加一条 HumanMessage：部分 chat template 对"连续两条 human"敏感，合并是最稳的形态。

这是一个很实际的考虑。很多 chat template（尤其是开源模型的）假设 user/assistant 严格交替，连续两条 human 会被合并或报错。合并成一条是最兼容的写法。

### 这个修复的一般化形式

> **system prompt 必须只包含"对所有请求都相同"的内容。任何逐请求变化的信息，都应该追加到消息序列的末尾。**

这条规则适用于：
- 用户位置；
- 当前时间；
- 用户偏好（如果逐请求变）；
- 检索到的资料（本来就在末尾）。

不适用于：
- 场景预设（按场景稳定，且场景种类少）；
- 工具说明（固定）。

**代价**：模型对"末尾信息"的注意力可能不如"开头信息"（lost in the middle 现象）。但实际上"用户问题"本来就在末尾，定位信息和问题在一起，反而更自然。

---

## 6.4 五个工具：为什么恰好是这些

### 清单

| 工具 | 类别 | 可重试 | 需审批 | 输出上限 | 成本 |
|---|---|---|---|---|---|
| `kb_search` | read | **否** | 否 | 4000 | normal |
| `web_search` | read | 是 | 否 | 4000 | normal |
| `calculator` | read | **否** | 否 | 1000 | cheap |
| `get_weather` | read | 是 | 否 | 2000 | cheap |
| `download_file` | **write** | 是 | **是** | 1000 | expensive |

### 选择逻辑

这五个工具覆盖了"知识助手"的四类需求：

1. **内部知识**：`kb_search`（检索自己的库）
2. **外部知识**：`web_search`（联网）
3. **精确计算**：`calculator`（模型算术不可靠）
4. **实时数据**：`get_weather`（模型不知道今天天气）
5. **持久化**：`download_file`（把外部内容变内部知识）

**为什么没有更多**？

因为**每个工具都会消耗每轮请求的 schema token**。5 个工具的 schema 大约 800~1200 token。如果加到 20 个，就是 4000 token/轮——对于 2048 输出的回答，输入成本翻倍。

这就是 `defer`（延迟发现）机制存在的原因。

### `calculator` 为什么必须有

```python
# 3. 数学计算必须用 calculator 工具，禁止口算。
```

LLM 的算术能力不可靠。对于"1234 × 5678"这类，模型可能给出错误答案但语气笃定。

`calculator` 是一个**确定性工具**：同样的输入永远同样的输出。这在"知识助手"场景里很重要——学生用它算题，算错一次就失去信任。

**注意提示词用的是"禁止口算"而不是"建议使用"**，这是一个强指令（对应 `TOOL_META` 里 `retryable=False`：算错了重试也没用，是表达式本身的问题）。

### `download_file` 为什么是唯一的 write + needs_approval

```python
# ⚠️ 唯一会改外部状态的工具：拉任意 URL 落盘并可能并入知识库。
# 所以是 write（必须串行）+ needs_approval（执行前要用户点头）。
```

**改外部状态**意味着：
- 磁盘上多一个文件；
- 可能往知识库里加数据（如果传了 kb_id）；
- 可能覆盖同名文件。

**需要审批**的机制（`TOOL_APPROVAL_ENABLED=1`）：

> 危险动作审批开关（默认开）。`needs_approval=True` 的工具首次调用只返回确认串，带同一确认串再调才执行。

也就是说：

```
模型: download_file(url="http://...")
系统: "需要确认：即将下载 xxx 到知识库。确认请再次调用并带上 approval_token=abc123"
模型: download_file(url="http://...", approval_token="abc123")
系统: 执行
```

这是**两阶段提交**在工具层的应用。它防止的是"模型自作主张下载了一堆东西"。

**代价**：多一轮 LLM 往返（模型要理解"需要确认"并重试）。这增加了延迟和 token 消耗。

**判断**：对于"唯一一个会改状态的工具"，这个代价是值得的。如果所有工具都要审批，体验会崩。

### `get_weather` 的双数据源

README 提到"高德 + Open-Meteo 双源"。这是**外部依赖降级**的典型：

- 高德：中文城市名直接查，但有 key，有配额；
- Open-Meteo：免费、无需 key、支持经纬度，但需要坐标。

降级链：`城市名 → 高德`，`坐标 → Open-Meteo`，`都没有 → 让用户提供城市`。

这与 §3 的定位三级降级（浏览器定位 → IP 定位 → 手动输入）是同构的。

---

## 6.5 `TOOL_META`：把"能不能重试"从提示词里救出来

### 问题的起源（`core/tool_registry.py:8-14`）

> 本项目的现状是：约束散落在循环与文案里。最典型的一处是 `agent/tools.py:104-106`，知识库不可用时返回 "请改用 web_search，不要重复调用本工具"——靠**自然语言**劝模型别重试。
> 问题是文案会被上游吞掉/改写（10-06 的报告记过：被吞成"暂时不可用"后模型连调 2~3 次），于是"能不能重试"这个**机器策略**就建立在了模型的阅读理解上。

这段话描述的是一个非常典型的错误：**用自然语言实现机器策略**。

具体的失效链：

1. 工具返回 `"知识库不存在，请改用 web_search，不要重复调用本工具"`；
2. LangChain 把这个字符串包装成 `ToolMessage`；
3. 某些中间层（或者模型自己）把它压缩成 `"暂时不可用"`；
4. 模型看到"暂时不可用" → 理解为"稍后可用" → 重试；
5. 重试还是失败 → 再试。

**根因**：把"确定性失败"（库不存在，重试一万次也一样）表达成了"临时性失败"（暂时不可用）。

### 解法：把语义变成字段

```python
@dataclass(frozen=True)
class ToolMeta:
    category: str              # read / write / command
    defer: bool = False        # 是否延迟发现
    retryable: bool = True     # False = 确定性失败，别重试
    needs_approval: bool = False
    max_output_chars: int = 4000
    cost_hint: str = COST_NORMAL
```

七个字段（含 `category` 的语义），每一个都对应一个**引擎需要做的决定**：

| 字段 | 引擎用它做什么 |
|---|---|
| `category` | `read` 可以并发；`write`/`command` 必须串行 |
| `defer` | 是否默认下发给模型 |
| `retryable` | 失败后是否进重试队列 |
| `needs_approval` | 执行前是否要求确认 |
| `max_output_chars` | 返回值超过就截断/落盘 |
| `cost_hint` | 编排级预算熔断的参考 |

**关键洞察：这不是"文档"，是"控制流输入"。** 注释说得很清楚：

> 这一层把它变成声明：`retryable` 是字段，引擎读它、确定性执行。文案仍然保留（它负责解释），但不再是唯一防线。

### 为什么"不替换 `@tool` 装饰器"

```python
# 不替换 @tool 装饰器。AGENT_TOOLS 与 bind_tools 是现成的 function calling 链路，
# 推翻它等于自建协议层。这里只另建一张元数据表：引擎读表，LLM 只看到 schema。
```

这是一个重要的边界判断：**元数据是给引擎看的，不是给模型看的。**

模型看到的仍然是 `@tool` 生成的 JSON schema（函数名、描述、参数）。`TOOL_META` 不在 schema 里，模型不知道它的存在。

**为什么要这样分离**：
- 如果放进 schema，模型会试图"利用"它（比如看到 `needs_approval=True` 就规避这个工具）；
- 元数据是控制面，不应该进数据面。

代价是"一处人工同步"：

> 代价是一处人工同步——所以 `assert_meta_complete()` 在导入时就做全量校验，宁可起不来，也不要跑起来之后才发现某个工具没有 category。

### 强制校验：让服务起不来

```python
def assert_meta_complete() -> None:
    names = set(tool_names())
    meta = _meta_keys()
    missing = sorted(names - meta)
    extra = sorted(meta - names)
    if missing or extra:
        raise RuntimeError(...)

# 导入即校验
assert_meta_complete()
assert_playbook_complete()
```

```python
# 刻意做成"起不来"而不是"打个日志"：漏配元数据的后果是引擎在运行时按默认值
# 做决定（比如把本该串行的写工具当成 read 并发出去），那种 bug 极难定位。
```

**这是一个"故障前移"的决策**：

| 方案 | 故障时机 | 定位成本 |
|---|---|---|
| 打 warning，用默认值 | 运行时（某个并发场景） | 极高（数据竞争，不可复现） |
| 抛异常，服务起不来 | 启动时 | 零（错误信息直接说缺哪个） |

**默认值的危险在于它不是"空"，而是"看起来合理"。** `category` 默认成 `read`，意味着一个新的写工具会被并发调用——数据可能损坏，但不会报错。

### 与"死代码"的区别

有人可能问：`defer`、`needs_approval`、`cost_hint` 这些字段，当前 5 个工具里只有 `download_file` 用了 `needs_approval=True`，其余都是默认值。这算不算过度设计？

注释里明确回答了：

> 本项目的 5 个工具**全部是 `defer=False`**——它们是"直接工具"，每轮全量下发。延迟机制是为**将来定时生成的工具**（打卡 / 导图 / 定时投递类）预留的，不是用来优化现在这 5 个的。

这是"为已知的未来留接口"，不是"为想象中的未来抽象"。区别在于：**已知的未来有具体的形态**（定时类工具），而想象中的未来没有。

---

## 6.6 三层渐进式加载

### 结构

```
第一层 = ToolMeta（路由/执行元数据，引擎与前端读，模型看不到）
第二层 = TOOL_PLAYBOOK（SKILL.md 式使用说明，延迟工具激活时随 schema 返回给模型）
第三层 = TOOL_RESOURCES（捆绑的参考文档/脚本，按需深入）
```

### 为什么需要第二层

```python
# 光有函数签名模型不会用。
```

这是对"function calling 就够了"这个想法的反驳。

一个函数的 schema 是：

```json
{
  "name": "daily_compose",
  "description": "生成每日总结",
  "parameters": {"type": "object", "properties": {"date": {"type": "string"}}}
}
```

模型知道"有个工具叫 daily_compose，接受 date 参数"。但它不知道：

- 什么时候该调（用户说"总结今天"时？还是每天定时？）；
- 调之前要不要先拉数据（有没有 `daily_pull`？）；
- 调完怎么用结果（直接给用户看？还是存起来？）。

这些是**工作流知识**，不是函数签名。所以需要 playbook：

```python
"daily_report": (
    "何时调：用户说"每日总结 / 日报 / 回顾今天"时。\n"
    "怎么用：先 daily_pull 拉当日事件，再 daily_compose 生成；"
    "不要凭空编造未发生的事件。"
)
```

**这个设计与 Anthropic 的 Skills 结构同构**（三级渐进加载：元数据 → SKILL.md → 资源）。项目的注释也明确提到了这个对应。

### 校验

```python
def assert_playbook_complete() -> None:
    missing = [n for n, m in TOOL_META.items() if m.defer and not (TOOL_PLAYBOOK.get(n) or "").strip()]
    if missing:
        raise RuntimeError(...)
```

**只对 `defer=True` 的工具强制要求 playbook**。因为直接工具常驻下发，靠 docstring 就够——它们的描述在每轮请求里都在。

这是一个"按成本分级"的校验策略：延迟工具的使用说明不进每轮请求，所以必须显式提供；直接工具的说明就在 schema 里，重复提供是浪费。

### 当前状态

`TOOL_PLAYBOOK` 是空字典（只有一个注释掉的示例）。`TOOL_RESOURCES` 也是空的。

所以**第二层和第三层目前是"结构存在但内容为空"**。这比"结构不存在"好（将来接定时工具时不用改架构），但比"有内容"差（无法验证设计是否真的可用）。

**这是一个典型的"预留但未验证"的设计**。它的风险是：真正接入时才发现 playbook 的格式/注入方式不对。

---

## 6.7 `tool_guard`：把元数据变成行为

### 三个守卫

**① 输出截断**

```python
max_output_chars: int = 4000    # 单次返回上限
```

当工具返回超过上限：

```
超过则落盘 + 只回预览标签，避免一次调用吃满上下文窗口
```

为什么需要？因为 `download_file` 可能返回一个 500KB 的网页正文。如果原样进 `ToolMessage`，下一次 LLM 调用的输入会爆炸（而且很可能超模型上下文）。

**"落盘 + 预览标签"是一个聪明的设计**：内容没丢（在文件里），模型知道"有更多内容"（标签里有路径），但上下文没爆。

**② 危险动作审批**

见 §6.4。

**③ 不可重试工具封禁**

`retryable=False` 的工具失败后，引擎直接终结该分支，不进重试队列。

这条针对的正是 §6.5 描述的症状："模型原地重试 2~3 次"。

### 实现方式：`StructuredTool.from_function` 重建

```python
# 为什么用 StructuredTool.from_function 重建，而不是原地包装
```

（注释没写完理由，但从代码位置可以推断。）

原地包装的做法是：

```python
@tool
def kb_search(query: str) -> str:
    return _original(query)

kb_search.description = original.description   # 保留描述
```

问题：`@tool` 装饰器会重新生成 schema，如果包装函数签名不同（比如加了 `config` 参数），schema 会变，模型看到的工具定义就变了。

`StructuredTool.from_function(func=guarded, name=original.name, description=original.description, args_schema=original.args_schema)` 的做法是**显式沿用原 schema**：

```python
# 它用 StructuredTool.from_function 重建，name / description / args_schema 原样沿用
# ——工具前缀与模型观感都不变。
```

**"工具前缀不变"是这里的关键**。因为工具 schema 会进每轮请求的 system/tools 部分，如果它变了，KV 缓存前缀就变了（同 §6.3）。

所以：**任何对工具的包装，都必须保证序列化后的 schema 逐字节不变。** 这是一个很容易忽略的约束。

---

## 6.8 `contextvars` 而不是 schema 字段

### 问题

工具需要知道"当前用户是谁、绑定哪个库"。怎么传？

### 三种方案

| 方案 | 做法 | 问题 |
|---|---|---|
| **schema 字段** | 工具签名加 `owner_id: str` 参数，由模型填 | 模型能看到、能改；且多一个参数就多一份 schema token |
| **全局变量** | `set_current_user(uid)` | 并发不安全（一个进程多个请求） |
| **contextvars** | `ContextVar` + 请求开始时设置 | 协程安全；模型看不到 |

### 本项目选 contextvars

```python
# 为什么用 contextvars 而不是往 schema 里加字段：
```

（同样，注释被截断了，但理由清楚。）

`contextvars` 的特性：
- 每个协程/task 有独立的值；
- `asyncio` 会自动传播（在 `create_task` 时复制上下文）；
- 线程池中**不会**自动传播（`asyncio.to_thread` 需要显式 `copy_context()`）。

最后一条是一个坑：如果工具在 `to_thread` 里执行，contextvars 可能拿不到。

所以工具的 kb_id 实际是通过 `RunnableConfig.configurable` 传的（`api/chat.py:892-896`），这是 LangChain 提供的机制，比 contextvars 更可靠（它会随图状态传递）。

`contextvars` 用在哪里？可能用于 `tool_guard` 的审批状态（当前轮的 approval token）。

**这两套机制并存**说明了一个现实：LangChain 的 config 机制覆盖了"工具参数注入"，但覆盖面之外的（如审批状态）需要自己的机制。

**风险**：两套上下文传递机制并存，容易出现"某一套没设值"的静默失败。当前代码里，如果 `configurable` 缺 `owner_id`，工具会拿不到用户——需要确认是否有明确的错误而不是静默降级。

### 与卷三的呼应

`kb_id` 服务端注入（卷三 §3.7）的技术实现就是这个。所以这不只是"方便"，是**安全边界的技术载体**。

如果改用 schema 字段，安全边界就破了。所以这个选择是**由安全需求倒推的技术选择**，不是偏好。

---

## 6.9 `recursion_limit=10`

### 配置

```python
config_ = {
    "recursion_limit": 10,
    "configurable": {...},
}
```

注释：

> `recursion_limit` 兜底：工具返回"不可用"时模型反复重试是纯烧钱，硬限制整轮的工具步数（LLM 往返 + 工具执行都占步数）。

### 10 是怎么算的

LangGraph 的 `recursion_limit` 计的是**超步（superstep）数**，每个节点执行算一步。一个"LLM 决策 → 工具执行"循环至少 2 步。

所以 10 步 ≈ 5 个工具调用轮次。

对于一个"知识助手"，5 轮工具调用足够：
1. `kb_search` → 不够 → 
2. `web_search` → 找到链接 →
3. `download_file` → 入库 →
4. `kb_search` → 再查 →
5. 回答。

再多就是异常情况了。

### 达到限制会怎样

LangGraph 会抛 `GraphRecursionError`。当前代码的 `except Exception` 会捕获它并返回：

```python
acc_text = f"Agent 执行失败：{describe_api_error(e)}"
yield _sse("error", {"detail": acc_text})
```

`describe_api_error` 不认识这个异常，会走最后的 `return f"{cls_name}: {e}"` → 用户看到 `GraphRecursionError: Recursion limit of 10 reached...`。

**这是一个体验缺口**：英文异常直接给用户。应该翻译成"这个任务需要的步骤太多，已中止。可以试着把问题拆小一些"。

### 与其他预算的关系

| 预算 | 位置 | 单位 | 触发后 |
|---|---|---|---|
| `recursion_limit=10` | LangChain config | 超步 | 抛异常，整轮失败 |
| `HYBRID_AGENT_MAX_STEPS=10` | hybrid 引擎 | 工具调用 | 收编（切换/终止） |
| `HYBRID_AGENT_MAX_SECONDS=300` | hybrid 引擎 | 秒 | 收编 |
| `HYBRID_AGENT_TOKEN_BUDGET=8000` | hybrid 引擎 | 字符/2 | 收编 |
| `AGENT_LLM_MAX_TOKENS` | 单次生成 | token | 输出截断 |
| `DAILY_ASK_LIMIT=60` | 配额 | 次/天 | 429 |

**注意 hybrid 的三个预算是"收编"而不是"失败"**——它们触发切换规则，可能从 Agent 切到工作流，或者直接终止但保留已有产出。而 `recursion_limit` 是硬失败。

这是一个设计不一致：同一个"步数超限"的概念，在两条路径上行为不同。原因可能是 `recursion_limit` 是 LangGraph 的能力，无法"优雅收编"。

---

## 6.10 提示词的集合式结构与 grounding 对齐

### 两套 SYSTEM_PROMPT

**Agent 路径**（`agent/builder.py`）：9 条行为准则，带 5 个工具的能力清单。

**RAG 路径**（`kb/rag_core.py`）：5 条规则，纯检索问答。

### 结构差异

**RAG 的 5 条**：

```
1. 仅依据【参考资料】回答，不得使用资料之外的任何知识；
2. 回答末尾注明依据的片段编号；
3. 如果参考资料不足，不要只甩一句冷冰冰的拒答。先说明...再给出下一步；
4. 用中文简洁作答，不要编造；
5. 如果用户输入的是词语或短语，则根据参考资料围绕该主题做简要介绍。
```

**Agent 的 9 条**：

```
1. 根据用户问题自主选择是否调用工具；
2. 需要知识库资料时用 kb_search；需要最新互联网信息时用 web_search；
3. 数学计算必须用 calculator，禁止口算；
4. 天气查询优先用 get_weather；
5. 当用户要求"搜索并下载论文"时，先 web_search 找到直链，再 download_file；
6. 若某工具明确返回失败，不要用相同参数重复调用；
7. 工具返回错误时如实解释，绝不编造；
8. 参考知识库资料回答时，末尾注明来源编号。只用真正支撑了结论的片段当依据；
   kb_search 在检索偏弱时会主动返回**必须遵守**的约束，请严格照它指示；
9. 闲聊、身份、常识等无需工具的问题直接回答。
```

**规律**：
- RAG 的准则是"知识边界"（只能用资料）；
- Agent 的准则是"工具选择"（什么时候用哪个）。

这是**集合式 Agent**（工具集合 + 选择准则）与**流程式 Agent**（step-by-step 流程）的区别。项目选择前者，理由是：工具少（5 个）、任务开放（学生可能问任何事），流程式会僵化。

### 第 8 条：grounding 对齐的修复

这一条是最值得注意的。修复前，Agent 路径的防编造只有软性的一句"绝不编造"。修复后：

```
参考知识库资料回答时，末尾注明来源编号（依据：[1][2]）。
只用真正支撑了结论的片段当依据；kb_search 在检索偏弱（相似度过低或仅沾边）
时会主动返回**必须遵守**的约束，请严格照它指示：如实告知用户知识库里没有
直接相关资料、并给出下一步，不要拿低分片段硬凑答案。
```

修复的技术部分在 `agent/tools.py::kb_search`：

> 现 `agent/tools.py::kb_search` 已改为复用 `kb/rag_core.py::grounding_note(assessment)`，与 RAG 路径同源。

**这是一个"消除双轨"的修复**（卷一的形态 3）：

- 修复前：RAG 路径有 `LOW_CONFIDENCE_NOTE` / `MARGINAL_CONFIDENCE_NOTE`，Agent 路径没有 → 同一个知识库，走 RAG 会示弱，走 Agent 会硬答。
- 修复后：`kb_search` 返回的内容里带上 `grounding_note`，Agent 看到后遵守。

**为什么这很重要**：同一个系统，两条路径对同一个问题给出不同的"诚实度"，用户会迷惑（"为什么早上问它说没有，下午问它就编了一个？"）。

现在 `tests/test_agent_grounding.py` 锁住这个对齐。

### 一个隐含的设计原则

> **同一份内容，如果会被两条路径使用，就必须由同一个函数产出。**

`grounding_note(assessment)` 成为唯一产出点。RAG 路径直接调用，Agent 路径通过 `kb_search` 间接调用。两处话术永远一致。

这与卷五 §5.4 的 `_source_meta` 是同一个模式（消除口径漂移）。

---

## 6.11 场景预设

### 机制

```python
system_prompt += scene    # scene = scenarios_mod.get_scenario_prompt(scenario)
```

三个场景：`kaoyan`（考研复习）/ `notes`（课程笔记）/ `paper`（论文写作）。

### 校验

```python
# 非白名单值按未选处理
```

这是一个安全设计：`scenario` 来自请求体，用户可以传任意字符串。如果不校验，就等于"用户可以通过 `scenario` 参数往 system prompt 里注入任意文本"——这是一个提示词注入入口。

**白名单是提示词注入的基本防线。** 同样的模式在：
- `mode`（`VALID_MODES`）
- 主题标签（受控词表）
- 用户画像标签（受控词表）

### 为什么场景放在 system 而不是末尾

`builder.py:216-217`：

> 场景预设保留在 system 里：它是按场景选定的稳定内容，不随请求变化。

这与"定位信息必须挪到末尾"是同一个原则的两面：**稳定的放前面，动态的放后面**。

场景种类少（3 个）、且会进入缓存键（`key = (has_kb, scene, activated)`），所以它对 KV 缓存的影响是"3 倍的前缀空间"，而不是"无限"。

---

## 6.12 混合引擎：为什么自研工作流 DSL

### 问题

`hybrid/` 包实现了一个"工作流 × 自主 Agent 动态切换"的引擎。为什么不用 LangGraph？

LangGraph 本身就是"图 + 节点 + 边 + 条件路由"，看起来正好是工作流引擎。

### 五个理由

**① 定义必须存表、运行期可改**

```python
wf_def = validate_definition(row["definition"])     # 从数据库读出来
```

LangGraph 的图是**代码**（`StateGraph` 的构建过程）。要支持"用户在前端编辑工作流"，必须能把图序列化成 JSON 存进 SQLite。

LangGraph 有 `draw_mermaid()` 可以导出可视化，但**没有"从 JSON 构建图"的官方路径**。要做，需要自己写"JSON → StateGraph"，那已经等于自研引擎了。

**② 节点类型是领域特有的**

```python
type: Literal["llm", "tool", "agent", "condition", "human"]
```

`human` 节点（人工确认/挂起）在 LangGraph 里需要用 `interrupt_before` 实现，语义不完全对应（LangGraph 的 interrupt 是"在图执行前/后暂停"，本项目的 human 节点是"流程中的一个等待点"）。

**③ 需要"模式切换"这个跨引擎概念**

LangGraph 的图是自洽的，一个图内不会有"切换到另一个执行范式"。而 hybrid 需要"工作流跑到一半，让 Agent 接管，然后再回来"。

这个"回来"需要保存断点（`ctx.workflow_state["next_node"]`），并在 Agent 结束后从该节点的**出边**继续。这是两个引擎之间的协议，LangGraph 不提供。

**④ 预算与超时需要细粒度控制**

`HYBRID_NODE_TIMEOUT=120`、`HYBRID_WORKFLOW_TIMEOUT=600`、`HYBRID_AGENT_MAX_STEPS=10`……

LangGraph 有 `recursion_limit`，但没有"单节点超时"（要用 `asyncio.wait_for` 自己包）。

**⑤ 可测性**

```python
# 本文件只依赖标准库 + SQLite，不 import apscheduler
```

（这是 scheduler 的注释，但同一思路。）hybrid 引擎的核心（`workflow.py`、`switcher.py`、`expr.py`）都可以脱离 web 框架单测。

### 决策

自研，但**复用 Agent 的能力**：

```python
# agent 类型节点：流程内直接内嵌一次 Agent 执行（一等公民，不走切换规则）
```

也就是说：hybrid 不是"替代 LangGraph"，而是"在 LangGraph 之上编排"。`agent_engine.py` 内部仍然调 `build_agent`。

**这是一个正确的位置：不做重复的轮子，只做缺失的那一层。**

### 代价

- 需要自己实现：节点执行、边选择、超时、重试、断点、事件流。
- 代码量：`workflow.py` + `switcher.py` + `orchestrator.py` + `engine.py` + `context.py` + `expr.py` + `store.py` ≈ 2000 行。
- 需要自己保证正确性（没有 LangGraph 的测试覆盖）。

**判断**：这个成本是为了"运行期可编辑"这个产品需求付的。如果工作流是固定的，用 LangGraph 更好。

### 反转条件

如果放弃"用户可编辑工作流"，改用代码定义 → 应该迁回 LangGraph（少 2000 行，多一份框架维护）。

---

## 6.13 表达式求值器：为什么不用 `eval`

### 问题

切换规则和边条件需要表达式：

```python
condition: str = ""    # 对 {shared, signal, ctx} 的受限表达式
edges: [{"source": "a", "target": "b", "condition": "shared.score > 0.8"}]
```

最直接的做法是 `eval(condition, {"shared": ..., "ctx": ...})`。

### 为什么不能用

`eval` 是**任意代码执行**。攻击者（或者只是配错的人）可以写：

```python
__import__('os').system('rm -rf /')
```

或者：

```python
open('/root/.env').read()
```

而且这些表达式**存在数据库里、可以由前端编辑**（`switch_rules` 表和 `workflow_definitions`）。

**这是一个完整的 RCE 通道**：能编辑工作流的人 = 能在服务器上执行任意命令的人。

### 自研求值器（`hybrid/expr.py`）

```python
# 为什么不用 eval/exec：
```

实现方式（从 `switcher.py` 的调用可以推断）：

```python
scope = {
    "shared": ctx.shared,
    "signal": signal_payload or {},
    "ctx": {"goal": ctx.goal, "status": ctx.status, "current_mode": ctx.current_mode},
}
return eval_condition(condition, scope)
```

`eval_condition` 大概支持：
- 比较：`> < >= <= == !=`
- 布尔：`and or not`
- 取值：`shared.xxx`、`signal.xxx`、`ctx.xxx`
- 字面量：数字、字符串、true/false

**只支持这些，其他一律报错。**

### 代价

- 表达能力受限（没有函数调用、没有算术表达式？可能有一些）。
- 需要自己实现 tokenizer + parser（或者用 `ast.literal_eval` 的变体）。

**但这是必要的代价。** 任何"用户可编辑的表达式"场景，都必须避免 `eval`。

同样的原则适用于：
- 模板渲染：`hybrid/workflow.py::render_template` 用正则只认 `{question}` 和 `{shared.xxx}`：

> 不用 `str.format`：模板来自用户定义，format 遇到未知占位符直接 KeyError，而且 format spec 有属性访问注入面。

`str.format` 的属性访问注入是什么？

```python
"{0.__class__.__mro__[1].__subclasses__()}".format(obj)
```

可以通过 format 访问任意属性，进而拿到类对象，进而 RCE。这是一个知名的 Python 漏洞模式。

**注意：这个项目在"用户可控的字符串"上，两处都避开了标准库的危险 API（`eval`、`str.format`）。** 这说明作者对此有清醒认识，不是运气。

---

## 6.14 切换规则与三层防抖

### 规则结构

```python
class SwitchRule(BaseModel):
    name: str
    signal: str            # 引擎上报的事件名
    condition: str = ""    # 可选表达式
    action: Literal["to_agent", "to_workflow", "terminate"]
    reason: str = ""
    priority: int = 100
    cooldown_seconds: int = 30
```

### 三层防抖（`switcher.py:9-16`）

> 1. **规则级**：同名规则 `cooldown_seconds` 内不重复触发（显式 0 = 结构型规则豁免，如 `requires_agent`/`goal_satisfied` 是流程语义的一部分，不该被防抖吃掉）；
> 2. **全局级**：同方向（from→to）`HYBRID_SWITCH_COOLDOWN` 秒内只切一次，同样豁免显式 0 冷却的规则；
> 3. **硬上限**：`HYBRID_MAX_SWITCHES` 次切换后强制 terminate，宁可收工也不乒乓。

### 为什么需要三层

**"乒乓"问题**：两条规则互相触发。

```
规则 A: 工作流里节点失败 → 切 Agent
规则 B: Agent 预算耗尽 → 切回工作流
```

如果 A 的失败条件持续成立，B 又立刻触发，就会：

```
workflow → agent → workflow → agent → ...（无限）
```

每次切换都要重新初始化上下文、重新调 LLM。一次请求可能烧掉几十次调用。

**三层的分工**：

| 层 | 作用域 | 防什么 |
|---|---|---|
| 规则级 | 单条规则 | 同一条规则连续触发 |
| 方向级 | from→to 方向 | A→B→A→B 的乒乓 |
| 硬上限 | 整个请求 | 一切未预见的循环 |

**递进关系**：规则级最细（只挡重复），方向级中等（挡往返），硬上限兜底（无条件终止）。

### "结构型规则豁免"的精妙

```python
if rule.cooldown_seconds > 0:       # 0 = 豁免
    ...
```

为什么 `requires_agent` 要豁免？因为它不是"异常处理"，是**流程语义**：

```python
# requires_agent=True 的普通节点：引擎跑到这里主动让出
# （EngineResult.switched + signal="requires_agent"）
```

也就是说：工作流定义里明确写了"这个节点要 Agent 来做"。如果防抖把它挡掉，工作流就跑不下去了。

**这是一个重要的区分：异常触发的切换（需要防抖）vs 语义触发的切换（不应防抖）。**

用 `cooldown_seconds=0` 表达这个区分，是一个简洁的设计（一个字段两用）。

### 硬上限的处理方式

```python
if len(ctx.mode_trace) >= config.HYBRID_MAX_SWITCHES:
    return SwitchDecision(
        action="terminate",
        rule="__max_switches__",
        reason=f"模式切换已达上限（{config.HYBRID_MAX_SWITCHES} 次），强制收工防乒乓",
    )
```

**"宁可收工也不乒乓"** —— 这句话体现了价值排序：**完成一半的任务 > 无限循环的任务**。

因为无限循环不仅花光预算，还给不出任何结果。而"收工"至少能返回已产出的部分。

### 代价

- 正常场景下切换被延迟（cooldown 30 秒）。如果一次请求里真的需要快速切换两次（A→B→A），会被挡。
- 30 秒的 cooldown 对一个总时长 300 秒的请求来说，最多允许 10 次切换——但硬上限是 5 次，所以 cooldown 实际很少生效。

**这说明参数之间没有严格协调**：`HYBRID_SWITCH_COOLDOWN=30` 与 `HYBRID_MAX_SWITCHES=5`，在 300 秒的超时下，5 次切换 × 30 秒 = 150 秒，还在预算内。所以硬上限先触发。

这是一个可以优化的点，但在当前使用强度下无关紧要。

---

## 6.15 人工节点的"异常穿透"设计

### 问题

工作流的 `human` 节点要"挂起"，等人工确认。但 `_run_with_retry` 会把节点抛的**任何异常**都转成 error 事件。

如果"挂起"用 `return` 表达，就没法从 async generator 内部"跳出"；如果用普通异常，会被当成失败。

### 解法（`workflow.py:35-46`）

```python
class _Suspend(Exception):
    """人工节点挂起信号：用异常穿透 _run_with_retry，避免被当成节点失败。

    为什么需要专门一条通道：_run_with_retry 把节点抛的异常统一转成 error 事件，
    而"挂起"不是失败——它是正常让出。用异常穿透后由 stream() 显式接住。
    """
```

**这是一个"用异常做控制流"的例子**，通常被视为反模式，但这里有明确理由：

1. 挂起需要**穿透多层**（节点 → retry 包装 → 主循环）；
2. `return` 无法穿透 async generator 的 `yield` 链；
3. 自定义异常类型可以被精确捕获（`except _Suspend`），不会误伤。

**关键在"精确捕获"**：`_Suspend` 不是 `Exception` 的宽泛捕获，而是一个专用类型。所以"挂起"和"真失败"在类型层面就分开了。

### SSE 连接的生命周期问题

`api/hybrid.py:202` 的注释：

> 为什么不在这里直接跑引擎：原 SSE 连接在挂起时已经结束，

也就是说：人工节点挂起时，SSE 流必须结束（否则连接会挂很久）。用户确认后，需要**重新发起一个请求**继续执行。

这要求：
- `run_id` 持久化（`ctx.run_id`）；
- 挂起时的状态持久化（`next_node`）；
- 恢复端点（用 `run_id` 找到断点）。

这是一个**"流式 + 人工介入"的固有矛盾**：流式假设"一次请求一次响应"，人工介入假设"跨越多次请求"。

项目的解法是"分成两次请求"，代价是前端要处理"这个任务挂起了，请确认"的状态。

---

## 6.16 编排层的整体评价

### 三条主线

把这一卷的 15 个决策归类，会发现三条主线：

**主线 1：把"策略"从"提示词"移到"代码/元数据"**

| 提示词做法 | 工程做法 |
|---|---|
| "不要重复调用" | `retryable=False` 字段 |
| "最多调 5 次" | `recursion_limit=10` |
| "下载前要确认" | `needs_approval=True` |
| "输出太长的要截断" | `max_output_chars` |

这条主线的依据是一个判断：**模型对自然语言指令的遵循是概率性的，而代码执行是确定性的。**

**主线 2：可复用的东西必须能复用，且复用时不能改变行为**

| 问题 | 解法 |
|---|---|
| 图编译是 CPU 开销 | LRU 缓存，键只含稳定维度 |
| 包装工具会改 schema | `StructuredTool.from_function` 显式沿用 |
| 动态信息破坏前缀缓存 | 挪到末尾，不进 system |

共同点：**复用不能以"改变行为"为代价**。缓存键多一个维度（城市），就会破坏 KV 缓存；包装工具时多一个参数，就会改变模型看到的 schema。

**主线 3：不确定的地方要有"最后一层"**

| 风险 | 最后一层 |
|---|---|
| 模型无限循环 | `recursion_limit` |
| 规则乒乓 | `MAX_SWITCHES` |
| 上下文爆炸 | `max_output_chars` |
| 生成无界 | `max_tokens` |
| 用户可控表达式 RCE | 自研求值器 |

每一条都是"在最坏情况下仍然不会灾难性失败"的保证。

### 三个已知缺口

1. **`TOOL_PLAYBOOK` / `TOOL_RESOURCES` 为空**（结构有，内容无）。
2. **`GraphRecursionError` 未翻译成中文**（用户看到英文异常）。
3. **hybrid 的切换规则表初始为空**（`list_rules(uid)` 返回什么取决于是否有默认规则，代码里没有明显的 seed 逻辑）。

第 3 点需要确认：如果规则表是空的，`SwitchEngine.decide` 永远返回 `continue`，那么"自动切换"这个功能实际不生效——只剩下人工切换和 `requires_agent` 的语义切换。

**这是一个"功能可能未接线"的风险**，属于卷一口径的"模块存在≠生效"。README 说"混合架构已实现"，但需要 grep 确认默认规则是否被 seed。

### 一条可迁移的经验

这一层最值得抄的不是任何具体实现，而是 `_make_checkpointer` 的**注释结构**：

```
① 表面看起来该做什么（挂 checkpointer）
② 为什么这里不做（三条实测依据）
③ 两个直觉改法为什么也错（三级失败路径）
④ 真要做的话必须同时做什么（成对变更）
⑤ 留了哪个接口给未来（get_checkpointer）
⑥ 哪条测试锁住这个决策（test_agent_checkpointer）
```

**六段式注释，把一个否定性决策讲成了不可推翻的论证。** 这是本报告见过的最好的"决策文档"形态——它不需要单独的 ADR 文件，因为它就在代码旁边，任何人改到这里都会看到。

下一卷进入存储层。编排层决定"做什么"，存储层决定"记住什么、记住多久、记错了怎么办"。
