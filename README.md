# 集合式 Agent

基于 **LangChain v1 + LangGraph + Chroma + FastAPI** 的集合式 Agent 产品，把 8 周课程 Demo（RAG 知识库、多轮对话、工具调用 Agent）整合为单体后端服务。

两大板块：

- **RAG 知识库板块**：文档加载（txt/md/pdf）→ 切片 → Embedding → Chroma 持久化 → top-k 检索 → 提示词组装 → LLM 生成；多知识库隔离；SSE 流式问答；参数可调（chunk_size / overlap / top_k）；联网资料导入（URL 抓取、搜索预览勾选）；引用溯源 `[1][2]` 角标；拒答约束。
- **多对话流 Agent 板块**：多会话（新建/切换/重命名/删除），每会话独立历史、可绑定知识库、流式输出、自动生成标题；底层 `create_agent` + 工具集（`kb_search` / `web_search` / 计算器 / 天气）。

## 技术栈

| 层 | 选型 |
|---|---|
| LLM | DeepSeek（chat）`ChatOpenAI` |
| Embedding | 阿里百炼 `text-embedding-v3`（`OpenAIEmbeddings`） |
| Agent 框架 | LangChain `create_agent` + LangGraph（MemorySaver checkpoint） |
| 向量库 | Chroma（`langchain-chroma`，多 collection 隔离） |
| 会话存储 | SQLite（WAL，参数化查询，10 轮窗口截断） |
| Web | FastAPI + uvicorn + sse-starlette |
| 联网搜索 | DuckDuckGo（duckduckgo-search） |
| 天气 | 高德地图 + Open-Meteo 双数据源 |

## 目录结构

```
苞米agent/
├── main.py              # FastAPI 入口（路由挂载 / CORS / 配置检查）
├── requirements.txt     # 依赖（版本锁定，见下）
├── .env.example         # 环境变量模板
├── core/
│   ├── config.py        # 集中配置（密钥 / 路径 / 默认参数）
│   ├── llm.py           # get_llm() / get_embeddings()
│   └── session.py       # SQLite 会话 / 消息存储
├── kb/
│   ├── rag_core.py      # RagPipeline（加载/切片/建库/检索/生成）
│   ├── pipelines.py     # 多知识库动态注册表
│   └── importer.py      # 联网导入（URL 抓取 / 搜索预览）
├── agent/
│   ├── builder.py       # build_agent()（create_agent + 4 工具）
│   └── tools.py         # kb_search / web_search / calculator / get_weather
├── api/
│   ├── chat.py          # SSE 流式问答（auto/rag/agent/llm 四模式）
│   ├── kb.py            # 知识库路由（上传/查询/联网导入/删除）
│   ├── sessions.py      # 会话 CRUD
│   └── schemas.py       # Pydantic 模型
└── tests/
    ├── test_w1.py       # RAG 全链路
    ├── test_w2.py       # 会话 + SSE 流式
    └── test_w3.py       # Agent 多工具 + 联网导入（HTTP E2E）
```

## 安装

```powershell
# 1. 依赖（版本已锁定，避免 pip 自动升级破坏 API 兼容）
pip install -r requirements.txt

# 2. 配置密钥：复制 .env.example 为 .env 并填入真实 key
#    DEEPSEEK_API_KEY      DeepSeek 对话密钥（必填）
#    EMBEDDING_API_KEY     向量化服务密钥（必填，阿里百炼等）
#    EMBEDDING_BASE_URL    向量化服务地址（必填）
#    AMAP_API_KEY          高德地图 Web 服务 key（可选，用于天气工具）
```

> 版本说明：`langchain-chroma 1.x` 需 `chromadb 1.x`（部分环境无法安装），
> 本项目固定用 `langchain-chroma==0.2.3 + chromadb==0.5.23` 组合，两者互相兼容。
> 若重新安装时 `create_agent` 报 ImportError，需强制重装：
> `pip install --force-reinstall --no-deps langchain==1.4.1 langchain-core==1.6.3 langchain-openai==1.6.2 langchain-community==0.4.2 langchain-text-splitters==1.1.2 langchain-chroma==0.2.3 langgraph==1.2.11`，并补装 `pip install langchain-protocol`。

## 运行

```powershell
# 启动（默认 0.0.0.0:8000）
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 或直接
python main.py
```

健康检查：`GET http://localhost:8000/health`

## API 一览

### 会话 `/api/sessions/`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/` | 新建会话（`{title, kb_id?}`） |
| GET | `/` | 列出所有会话（按更新时间倒序） |
| GET | `/{sid}/` | 会话详情 + 消息历史 |
| PATCH | `/{sid}/` | 重命名 / 绑定知识库（`kb_id` 传 `null` 解绑） |
| DELETE | `/{sid}/` | 删除会话 |

### 知识库 `/api/kb/`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/{kb_id}/upload` | 上传文件（txt/md/pdf，多文件）并重建索引 |
| POST | `/{kb_id}/query` | 同步问答（返回 `{answer, refs}`） |
| GET | `/` | 列出所有知识库 |
| GET | `/{kb_id}/` | 查看库状态（chunks / 参数 / 文件） |
| DELETE | `/{kb_id}/` | 删除知识库 |
| POST | `/import-search` | 联网搜索预览（返回候选 URL 列表） |
| POST | `/{kb_id}/import-url` | 抓取单个 URL 入库 |
| POST | `/{kb_id}/import-batch` | 批量抓 URL 入库 |

### 问答（SSE）`/api/chat/stream`

请求体：`{sid, question, top_k?, kb_id_override?, mode?}`

`mode` 四选一，默认 `auto`：

- `auto` — 绑定知识库且非空 → RAG；否则 → Agent
- `rag` — 强制 RAG（检索 + 组装 + LLM，不走工具）
- `agent` — 强制 Agent（create_agent + 4 工具 + LangGraph 多轮记忆）
- `llm` — 强制纯 LLM 对话

SSE 事件协议：

| event | data | 说明 |
|---|---|---|
| `status` | `{text}` | 状态提示 |
| `tool` | `{name, input?, output?}` | Agent 工具调用 / 返回 |
| `token` | `{text, refs}` | 增量 token（`text` 为累计全文） |
| `title` | `{title}` | 自动生成的会话标题（仅首轮） |
| `done` | `{answer, refs, title, mode}` | 轮次结束 |
| `error` | `{detail}` | 错误信息 |

### 其他

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config/defaults` | 前端默认参数（chunk_size / top_k 等） |
| GET | `/health` | 健康检查 |

## 测试

```powershell
python tests/test_w1.py   # W1 RAG 全链路
python tests/test_w2.py   # W2 会话 + SSE 流式
python tests/test_w3.py   # W3 Agent 多工具 + 联网导入（需先启动后端）
```

## 里程碑进度

- **W1**（迁移地基）：LangChain v1 迁移、RAG 全链路 ✅
- **W2**（会话层）：SQLite 多会话 + SSE 流式问答 ✅
- **W3**（Agent 集成）：create_agent + 4 工具 + 联网资料导入 ✅
- **W4**（Web 整合）：前端界面（进行中）
- **W5**（打磨交付）：拒答约束、错误处理、性能、最终文档