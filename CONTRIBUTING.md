# 贡献指南（Contributing）

感谢你关注 **苞米 Agent**！这是一个基于 RAG 知识库 + LangChain Agent 的个人知识助手项目。
下面说明如何在本地把它跑起来、如何跑测试、以及提交代码的约定。

> 项目整体介绍、功能亮点、架构图见根目录 [README.md](./README.md)。

---

## 1. 环境要求

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11+（已于 3.11 / 3.12 / 3.13 验证） | 后端运行时 |
| Node.js | 22+ | 前端（Next.js 16） |
| DeepSeek API Key | — | 对话大模型（在 `.env` 配置） |
| Embedding 服务 Key | — | 向量化（如阿里百炼 `text-embedding-v3`） |

> 为什么 embedding 要单独配：DeepSeek 不提供 embedding 接口，向量化必须由独立的 embedding 服务完成。

---

## 2. 本地启动

### 2.1 后端（FastAPI）

```bash
# 1) 安装依赖（运行时依赖的唯一真相源是 requirements.txt，deploy.sh 也用它）
pip install -r requirements.txt

# 2) 准备配置：复制模板，填入你的真实 Key
cp .env.example .env
#    然后用编辑器打开 .env，填 DEEPSEEK_API_KEY / EMBEDDING_API_KEY

# 3) 启动（监听 http://127.0.0.1:8000）
python main.py
```

验证后端是否就绪：

```bash
curl http://127.0.0.1:8000/health
# 期望返回：{"status":"ok"}
```

### 2.2 前端（Next.js）

```bash
cd frontend
npm install
npm run dev          # 访问 http://localhost:3000
```

前端通过相对路径 `/api/*` 代理到后端，本地无需额外配置跨域。

---

## 3. 运行测试

测试分为两类，**CI 默认只跑离线子集**，保证绿色构建稳定：

### 3.1 离线单元测试（无需网络 / 外部服务 / 登录态）

```bash
pip install pytest
pytest tests/test_chroma_singleton.py \
       tests/test_upload_stream.py \
       tests/test_retrieve_select.py \
       tests/test_pipelines_concurrency.py \
       tests/test_agent_checkpointer.py \
       tests/test_agent_guard.py
```

这些用例不连真实 LLM / 向量库，embedding 用 `DeterministicFakeEmbedding` 打桩。

### 3.2 集成测试（需本地服务 / 登录态 / 外部 API）

以下用例 **CI 不跑**，本地手动验证时可整体 `pytest`（需先按 §2 启动后端并配置 `.env`）：

- 多用户隔离：`test_auth_gate` / `test_kb_isolation` / `test_owner_isolation`
- 公网能力：`test_geo_locate`（依赖外部 IP 定位服务）、`test_web_import_creates_index`
- 端到端：`test_w2` / `test_w3`（需真实启动后端并打 `localhost:8000`）

---

## 4. 代码风格

本项目使用 [ruff](https://docs.astral.sh/ruff/) 做静态检查与格式化（配置见 `pyproject.toml`）：

```bash
pip install ruff
ruff check .        # 静态检查
ruff format .       # 自动格式化
```

约定：

- **Python**：遵循 PEP 8；命名用 `snake_case`；公开函数尽量补全返回类型注解。
- **注释**：中文优先，讲清「为什么 / 设计权衡」，而不是复述「是什么」。
- **横切关注点**：新增认证 / 配额 / 限流等横切逻辑时，务必补齐「配套缺口」
  （如前端解析、错误文案、回归测试），避免「规则 A 处遵守、B 处漏了」类 bug。

---

## 5. 提交与 PR

1. Fork 本仓库，切出特性分支：`feat/xxx`（新功能）或 `fix/xxx`（修复）。
2. 确保 `ruff check .` 通过、离线测试全绿。
3. 提交信息用前缀区分类型：`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`。
4. 开 PR，CI 会自动跑 §3.1 的离线测试子集。

---

## 6. 安全须知（务必遵守）

- 🔒 **严禁提交 `.env`**（含 API Key），它已被 `.gitignore` 忽略；仓库里只有 `.env.example` 模板。
- 🚫 不要硬编码个人信息、服务器 IP、密钥。测试占位统一用 RFC 5737 文档地址（`203.0.113.x`）。
- 🛡️ 涉及 SSRF / 鉴权 / 配额的逻辑改动，务必同步更新对应测试用例。

---

## 7. 目录速览

```
main.py              FastAPI 入口（斜杠黑洞、回环监听、默认鉴权）
api/                 路由层（auth/chat/kb/sessions/geo/feedback）
core/                业务核心（session/account/auth/quota/llm/upload/netsafe）
kb/                  知识库（pipelines 写入并发 / rag_core 向量检索 / importer 导入）
agent/               LangChain Agent（builder 编排 / tools 工具）
tests/               离线单元测试 + 集成测试
frontend/            Next.js 前端（三栏：侧边栏 / 对话区 / 知识面板）
```
