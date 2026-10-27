# 知穗 Agent · 团队研发规范手册

**版本**：v1.0
**日期**：2026-10-13
**适用仓库**：https://github.com/baomi888/agent-hub
**维护人**：申请人（总负责）

---

## 手册说明

本手册是「知穗 Agent」团队协作与研发规范的**汇编版**，用于汇报、评审、答辩与新人培训。

**与在线文档的关系**：手册内容与仓库 `规范/` 目录保持同步。**日常开发请以仓库文档为准**——
仓库文档随代码演进，本手册是某一时刻的快照。

| 手册章节 | 对应仓库文档 |
| --- | --- |
| 第一章 项目概览与团队分工 | `规范/README.md`、`01_架构与模块职责.md` |
| 第二章 系统架构与模块职责 | `规范/01_架构与模块职责.md` |
| 第三章 核心数据结构定义 | `规范/02_核心数据结构定义.md` |
| 第四章 流程规范 | `规范/03_流程规范.md` |
| 第五章 环境与部署 | `规范/04_环境与部署.md` |
| 第六章 分角色开发指南 | `规范/guides/*.md` |

---

# 第一章 项目概览与团队分工

## 1.1 项目基本信息

| 项目 | 内容 |
| --- | --- |
| 产品名称 | 知穗 —— 面向大学生个性化 AI 学习助手 |
| 定位 | 面向大学生的 Agent 个性化学习助手，覆盖备考、求职两大场景 |
| 核心 | 个人知识库 + 标签钩子 + Agent 编排，解决通用大模型上下文压缩、无长期记忆问题 |
| 技术栈 | FastAPI + LangChain v1 + LangGraph + Chroma + SQLite；前端 Next.js 16 + React 19 + Tailwind v4 |
| 关键节点 | 2026-10-20 前提交申报书至邮箱（无需盖章） |
| 产品路线 | 申报期（接口+功能+Mock）→ 院赛期（真实编码+联调+答辩）→ 推广反馈 → 校赛 → 立项 → 省赛 |

## 1.2 团队与职责

| 角色 | 姓名 | 负责范围 |
| --- | --- | --- |
| 申请人（总负责） | 申请人 | 总负责 + 全部 UI 设计 + 接口对接 + 后端脚手架 + 数据模型 + 申报书统稿 + Demo 组装 + 路演答辩 |
| Agent 与调度 | 刘佳林 | Agent 编排、每日委托、每日总结（功能层 + 接口层 + 定时任务） |
| AI 与检索 | 林宏亮 | AI 板块（知识库、大模型调用、联网搜索、蒸馏检索、标签钩子） |

> ✅ **账号归属已于 2026-10-13 由申请人确认**：`huashuotianxuan` = **刘佳林**，`sakasa-code` = **林宏亮**。
> `CODEOWNERS` 已按此生效，审核会自动点名到正确的人。

## 1.3 三条并行开发线

| 线 | 负责人 | 独占目录 | 交付内容 |
| --- | --- | --- | --- |
| A · 前端与产品 | 申请人 | `frontend/**`、`docs/**` | UI、接口对接、文档、申报书、Demo |
| B · AI 与检索 | 林宏亮 | `kb/**`、`core/serper.py` | 知识库、检索、搜索、蒸馏、标签钩子 |
| C · Agent 与调度 | 刘佳林 | `agent/**`、`service/**`、`worker/**`、`core/scheduler.py`、`core/tool_registry.py` | Agent 编排、每日委托、每日总结、定时任务 |

**交叉区（需协商）**：`api/*.py`、`main.py`、`core/session.py`、`core/config.py`

---

# 第二章 系统架构与模块职责

## 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器                                                       │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP  :3000
┌───────────────────────────▼─────────────────────────────────┐
│  展示层  frontend/           Next.js 16.3.5 + React 19       │
│    src/app/        页面路由（page.tsx / templates）           │
│    src/components/ UI 组件（Sidebar / ChatArea / KBPanel…）   │
│    src/lib/        接口封装（api.ts / sse.ts / hooks）         │
│    ★ 前端不直连后端端口，靠 next.config.ts 的 rewrite 代理     │
└───────────────────────────┬─────────────────────────────────┘
                            │ /api/*  →  http://localhost:8000/api/*
┌───────────────────────────▼─────────────────────────────────┐
│  接口层  api/ + main.py      FastAPI（路由 / 鉴权 / 参数校验） │
│    auth chat kb sessions feedback geo templates usage        │
│    ★ 默认全鉴权：业务 router 挂 Depends(get_current_user)      │
└───────────────────────────┬─────────────────────────────────┘
                            │ 调用纯业务函数
┌───────────────────────────▼─────────────────────────────────┐
│  业务核心层  core/ + agent/ + kb/                             │
│    core/    账号 / 会话 / 配额 / 配置 / LLM / 网络安全          │
│    agent/   LangChain Agent 编排 + 工具集                     │
│    kb/      知识库流水线 + 向量检索 + 网页导入                  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  数据层                                                       │
│    SQLite   data/sessions.db   会话 / 消息 / 用户              │
│    SQLite   data/scheduler.db  调度三张表                      │
│    Chroma   chroma_db/         向量库（按 kb_id 分 collection） │
│    文件     data/kb_sources/ 等 上传原文 / 索引元数据           │
└─────────────────────────────────────────────────────────────┘

        外部依赖：DeepSeek Chat（对话）
                  阿里百炼 DashScope（embedding + 视觉）
                  Serper（联网搜索）/ 高德（天气）
```

## 2.2 关键架构约定

1. **后端只监听 `127.0.0.1:8000`，不对外。** 前端经 Next 的 `/api/*` rewrite 访问，公网只暴露 `:3000`。
2. **`redirect_slashes=False` + 尾斜杠归一中间件**：解决 Next（去尾斜杠）与 FastAPI（补尾斜杠）互相重定向的黑洞。
3. **新增端点默认要登录**：业务 router 统一挂 `Depends(get_current_user)`，忘写鉴权时默认落在安全侧。
4. **`async def` 里禁止同步阻塞**，一律 `await asyncio.to_thread(...)`。
5. **429 用全局异常处理器**，不在端点内 try/except —— 避免被顺手转成 500。

## 2.3 后端模块职责表

| 层 | 文件 | 行数 | 职责 | 归属 |
| --- | --- | --- | --- | --- |
| 入口 | `main.py` | 152 | 路由挂载 / 中间件 / 429 处理器 | 申请人 |
| 接口 | `api/chat.py` | 580 | 附件上传 / SSE 流式问答 | 申请人 |
| 接口 | `api/kb.py` | 474 | 知识库 CRUD / 上传 / 检索 / 导入 | 林宏亮 |
| 接口 | `api/auth.py` | 90 | 注册 / 登录 / 登出 / 当前用户 | 申请人 |
| 接口 | `api/sessions.py` | 68 | 会话 CRUD / 消息删除 | 申请人 |
| 接口 | `api/geo.py` | 98 | 按来源 IP 兜底定位 | 申请人 |
| 接口 | `api/templates.py` | 43 | 模板广场 | 申请人 |
| 接口 | `api/feedback.py` | 41 | 反馈读写 | 申请人 |
| 接口 | `api/usage.py` | 25 | 用量看板 | 申请人 |
| 核心 | `core/session.py` | 443 | 会话与消息持久化 | 申请人 |
| 核心 | `core/config.py` | 115 | 集中配置（唯一真相源） | 申请人 |
| 核心 | `core/quota.py` | 180 | 限流与配额 | 申请人 |
| 核心 | `core/auth.py` | 131 | 令牌签发与校验 | 申请人 |
| 核心 | `core/account.py` | 118 | 用户表与口令散列 | 申请人 |
| 核心 | `core/scheduler.py` | +853 | 调度库：三表 + 幂等键 + 重试分级 + 心跳 | 刘佳林 |
| 核心 | `core/tool_registry.py` | +299 | 工具元数据 + 延迟发现状态机 | 刘佳林 |
| 核心 | `core/serper.py` | +147 | 联网搜索唯一入口 | 林宏亮 |
| 知识库 | `kb/rag_core.py` | 718 | 向量 + 混合检索（BM25+RRF） | 林宏亮 |
| 知识库 | `kb/pipelines.py` | 427 | 写入流水线（并发 embedding） | 林宏亮 |
| 知识库 | `kb/importer.py` | 252 | 网页 / 文件导入 | 林宏亮 |
| Agent | `agent/tools.py` | 443 | 工具集 | 刘佳林 |
| Agent | `agent/builder.py` | 164 | Agent 编排与图缓存 | 刘佳林 |

## 2.4 跨模块改动流程

改动**别人归属**的文件时：

1. **先通知**：说清「改哪个文件、为什么、影响面」。
2. **建独立分支**：`feat/跨模块-简述-日期`，不混进自己的功能分支。
3. **小步提交**：一个提交只做一件事，便于 review 与回滚。
4. **拉对方做 reviewer**（`CODEOWNERS` 会自动点名）。
5. **合并后同步文档**：职责边界或契约变化时更新对应文档。

---

# 第三章 核心数据结构定义

## 3.1 存储总览

| 存储 | 位置 | 内容 |
| --- | --- | --- |
| SQLite | `data/sessions.db` | conversations / messages / feedbacks / users |
| SQLite | `data/scheduler.db` | 调度三张表 |
| Chroma | `chroma_db/` | 向量库，按 `kb_id` 分 collection |
| 文件 | `data/kb_sources/` 等 | 上传原文与索引元数据 |

**SQLite 连接约定**：连接按线程复用（不是每次新建）；路径参与判断（测试会 monkeypatch）；
统一 PRAGMA：`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=5000`。

> ⚠️ `job_runs` 在 `data/scheduler.db`，**不在** `data/sessions.db`。这是刻意的隔离。

## 3.2 用户 `users`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `username` | TEXT | PK | 登录名，唯一 |
| `password_hash` | TEXT | NOT NULL | PBKDF2-SHA256，默认 200000 轮 |
| `created_at` | INTEGER | NOT NULL | Unix 秒 |

- 重名抛 `sqlite3.IntegrityError`，API 层翻译成 **409**。
- `ADMIN_USERNAMES` 内的用户不受配额与限流约束。
- `LEGACY_OWNER = "legacy"`：历史数据锁在此名下，**默认拒绝**，需 `tools/migrate_owner.py` 指派。

## 3.3 会话 `conversations`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | TEXT | PK | `uuid4().hex[:12]` |
| `owner_id` | TEXT | 可空 | 归属人；**NULL = 老数据，一律判不可见** |
| `title` | TEXT | NOT NULL | 默认「新对话」 |
| `kb_id` | TEXT | 可空 | 绑定知识库 |
| `created_at` / `updated_at` | INTEGER | NOT NULL | Unix 秒 |

**多用户隔离铁律**：每个读写会话的入口都必须先过 `_owned(owner, sid)`；
`owner_id IS NULL` 永远返回 False，**不做「无主即公开」的推断**。

## 3.4 消息 `messages`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | INTEGER | PK AUTOINCREMENT |
| `conversation_id` | TEXT | NOT NULL, FK ON DELETE CASCADE |
| `role` | TEXT | NOT NULL, CHECK IN ('system','user','assistant') |
| `content` | TEXT | NOT NULL（Markdown） |
| `refs` | TEXT | 可空，**JSON 字符串**，前端需 `JSON.parse` |
| `created_at` | INTEGER | NOT NULL |

`MAX_HISTORY_TURNS = 30`：喂给模型时最多保留 30 轮。

## 3.5 反馈 `feedbacks`

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | INTEGER | PK AUTOINCREMENT |
| `conversation_id` | TEXT | NOT NULL |
| `message_id` | INTEGER | 可空 |
| `rating` | TEXT | NOT NULL, CHECK IN ('up','down') |
| `comment` | TEXT | 可空 |
| `created_at` | INTEGER | NOT NULL |

**唯一索引**：`idx_fb_msg(conversation_id, message_id) WHERE message_id IS NOT NULL`

> **语义**：一条消息只保留**最后一次**反馈（点赞后再点踩是**改**，不是追加）。
> 用部分唯一索引实现，不用「先查再决定插入或更新」（并发下会双写）。

## 3.6 知识库契约

| 概念 | 规则 |
| --- | --- |
| `kb_id` | `md5(owner \| name)` 派生 → **同名即同库** |
| 归属 | `data/kb_owners.json` |
| 名称 | `data/kb_names.json` |

**切片与检索参数**

| 参数 | 默认 | 语义 |
| --- | --- | --- |
| `CHUNK_SIZE` | 500 | 切片大小 |
| `CHUNK_OVERLAP` | 50 | 切片重叠 |
| `TOP_K` | 3 | 返回条数 |
| `EMBED_BATCH_SIZE` | 10 | 百炼单次批量上限 |
| `EMBED_CONCURRENCY` | 4 | 并发批次数（太高撞 429） |
| `RETRIEVE_FETCH_MULT` | 3 | 候选池倍数 |
| `RETRIEVE_MIN_FETCH` | 12 | 候选池下限 |
| `RETRIEVE_MMR_LAMBDA` | 0.65 | MMR 相关度权重 |
| `RETRIEVE_MIN_SIM` | 0.25 | **丢不丢** |
| `RETRIEVE_LOW_SIM` | 0.45 | **够不够格当依据** |

> ⚠️ `RETRIEVE_MIN_SIM` 与 `RETRIEVE_LOW_SIM` 是**两件事**，代码注释明确写了不许合并。
> 前者决定「这段资料丢不丢」，后者决定「留下来的够不够格当依据」。
> 依据：用户调研（56 份）中「答案不准还装自信」占 **83.9%**，是压倒性第一担忧。

## 3.7 调度数据与每日总结

**调度健康判定**：看**心跳年龄**，不看进程表 —— `> 300s` 为 `stalled`，`> 600s` 为 `down`。
（`worker` 是独立进程，存在不代表在正常跑。时区 `Asia/Shanghai`，五个 job。）

**每日总结响应形状**

```jsonc
{
  "report_id": "...", "date": "2026-10-13", "status": "...",
  "stats": { "conversation_count": 0, "message_count": 0, "user_msg_count": 0,
             "assistant_msg_count": 0, "active_hours": 0, "tool_call_count": 0 },
  "content": [ { "text": "结论文本", "source_refs": [123, 456] } ],
  "model": "...", "generated_at": "...", "read_at": null
}
```

> ⚠️ **`content` 不是思维导图树**，是「结论 + 溯源消息 id」列表。
> 后端做了**强制溯源**（`_filter_by_traceability`）：refs 指不到真实消息的结论**会被丢弃**。
> 因此 **UI 里每条结论必须有可点回原文的角标**。

## 3.8 每日委托业务规则

| 规则 | 说明 |
| --- | --- |
| `kind` | `study_plan` / `review` / `encourage` / `custom` |
| 启用上限 | **最多 5 条** |
| 同名 | **409** |
| `name` | **建后不可改**（唯一标识） |
| `dry_run` | 只生成内容、**不投递**（实现「系统先生成好、给用户确认/修改」） |

## 3.9 统一响应与错误约定

| 场景 | 状态码 | 响应体 |
| --- | --- | --- |
| 正常 | 200 / 201 | 业务对象 |
| 未登录 | 401 | `{"detail": "..."}` |
| 越权 / 不存在 | 404 | `{"detail": "..."}`（**不区分**，避免探测他人资源） |
| 重名冲突 | 409 | `{"detail": "..."}` |
| 限流 | 429 | `{"detail","retry_after"}` + `Retry-After` 头 |
| 配额超限 | 429 | `{"detail","kind","limit"}` |
| 系统错误 | 500 | `{"detail": "..."}` |

---

# 第四章 流程规范

## 4.1 Git 分支策略

```
main     ●────────────●────────────────●──────────►  受保护，只接受 PR 合并
          \          /                  \
           \        /                    \
dev  ●──────●──────●──────────────────────●───────►  集成分支，staging 部署来源
      \    /        \                    /
       \  /          \                  /
feat/*  ●             ●───────●        ●            短命分支，生命周期 ≤ 5 天
```

| 分支 | 角色 | 谁能推 | 规则 |
| --- | --- | --- | --- |
| `main` | 稳定主干，随时可发布 | **没人能直推** | PR + ≥1 approve + CI 全绿 |
| `dev` | 集成与 staging 来源 | 开发者合并自己的特性分支 | PR 合并，CI 必须绿 |
| `feat/*` `fix/*` | 短命特性分支 | 归属人 | 从 `dev` 切出，≤ 5 天 |
| `hotfix/*` | 生产紧急修复 | 归属人 | 从 `main` 切出，修完同时合回 main 与 dev |

**分支命名**：`<类型>/<模块>-<简述>-<日期>`
模块名取值：`frontend` / `kb` / `agent` / `api` / `core` / `deploy`

```
✅ feat/kb-hybrid-search-20261020
✅ fix/report-to-thread-20261021
❌ test、mybranch、临时、新分支2
```

**合并方向铁律**

- 特性分支 → `dev`：随时可合，CI 绿 + 1 人 approve。
- `dev` → `main`：按节奏合，需申请人 approve + 全量 CI 绿。
- `main` → `dev`：合并发布后**立刻回流**。

> ⚠️ **当前最大的流程问题**：`main` 比 `dev` 少 8 个提交、比 `feat/ai-module-20261013` 少 11 个。
> 队友成果全停在 `dev` / `feat` 上**没有回流 main**。
> **建议收口时间：10/17 组会之后、10/18 联调之前** —— 早合早解决命名冲突，别拖到 10/20 申报稳定期。

**冲突处理原则**：改名前先合（`dev`/`feat` 不含改名与 UI 统一提交，"苞米/知慧/知穗"会撞车）；
不用 `--ours`/`--theirs` 一把梭；契约冲突优先保契约；合完必须跑测试。

## 4.2 提交规范

**格式**：`<类型>: <简述>` 或 `<类型>(<作用域>): <简述>`

| 部分 | 要求 |
| --- | --- |
| 类型 | **必填**：`feat` `fix` `docs` `refactor` `perf` `test` `chore` `style` `revert` |
| 作用域 | 可选：`ui` `kb` `agent` `api` `core` `deploy` `docs` `test` |
| 简述 | **必填**，中文，说清「做了什么」，≤ 50 字，结尾不加句号 |
| 正文 | 可选，说清「**为什么**这么改」 |

```
✅ feat(kb): 混合检索加 BM25+RRF，召回率提升 18%
✅ fix(api): report 端点同步调用包 to_thread，避免阻塞事件循环
✅ docs: 新增团队研发规范手册与分角色开发指南

❌ 改了一下      ❌ fix bug      ❌ update      ❌ 提交
❌ feat: 新增功能、修了bug、改了样式、更新文档   （一件事混杂四类改动）
```

**提交粒度**：一个逻辑变更 = 一个提交；不要提交半成品；不要提交生成物
（`data/`、`chroma_db/`、`.next/`、`node_modules/`、`venv/`、`*.db` 已被 `.gitignore`）。

**提交前自检**

```bash
ruff check .                    # 必须无错
pytest -q                       # 必须全绿
cd frontend && npx tsc --noEmit # 必须 Exit 0
git status                      # 确认没有 .env / *.db / 密钥
git diff --cached               # 逐行看一眼暂存区
```

> ⚠️ **提交 `.env` 是最高级别的安全事故**。`.gitignore` 已忽略它，但 `git add -f` 能绕过 ——
> **永远不要用 `git add -f` 加环境文件**。

## 4.3 代码审核流程

**PR 模板必填项**：改动类型 / 做了什么 / 影响范围（前端、接口契约、数据库结构、部署、仅文档）/
验证方式（贴实际命令与结果）/ 关联任务 / 自查清单。

**审核责任与时限**

| 环节 | 责任人 | 时限 |
| --- | --- | --- |
| 自审 | 提 PR 的人 | 提 PR 前完成自检 |
| 代码审核 | `CODEOWNERS` 指定的人 | **24 小时内**响应 |
| 最终合并 | 申请人 | — |

**意见分级**：`[阻塞]` 必须改 / `[建议]` 可讨论 / `[提问]` 只想弄懂 / `[表扬]` 值得学习

**审核清单（reviewer 视角）**

- **正确性**：边界条件（空值、超长、并发、重复提交）；错误处理（异常有没有被吞、状态码对不对）；
  **越权检查**（动会话/知识库的入口是否过了 `_owned`）；**`async` 里有无同步阻塞**。
- **契约一致性**：字段变更是否同步 `02` 文档与前端 `lib/types.ts`；
  是否破坏现有 35 个端点的契约（**路径/请求字段/响应形状一律不改**）；横切逻辑是否补齐配套缺口。
- **工程性**：提交信息合规；测试只增不减；有没有改出边界；有没有混入无关改动/调试代码。
- **安全**：无硬编码密钥/真实 IP/个人信息；SSRF/鉴权/配额改动同步更新测试；错误信息不回显用户输入。

**不可合并（硬性拦截）**：CI 未全绿；无 approve 或 approve 人不是 CODEOWNERS 指定者；
含 `.env`/密钥/`*.db`；破坏契约未同步文档与前端；PR 描述为空。

## 4.4 CI/CD 管线要求

**现状**：`.github/workflows/ci.yml` 只触发 `main`，仅跑离线测试。
**两个不足**：① **漏了 `dev`** —— 而 `dev` 才是集成分支，PR 到 `dev` 完全没有 CI；
② **没有 lint、没有前端检查** —— `ruff` 与 `tsc` 全靠人自觉。

**目标管线**

```
PR 提交 / 推送
      │
      ├─► [job 1] lint          ruff check .
      ├─► [job 2] offline-tests pytest 离线子集（3.11/3.12/3.13 矩阵）
      └─► [job 3] frontend      npm ci + tsc --noEmit + npm run build
                 │
          全绿 + 1 人 approve
                 │
        合并到 dev  → 自动部署 staging
                 │
        合并到 main → 手动批准后部署生产
```

**分级测试要求**

| 级别 | 何时跑 | 要求 |
| --- | --- | --- |
| 离线单元测试 | **每个 PR** | 必须全绿，**数量只增不减** |
| 集成测试 | 合并到 `dev` 前手动跑 | 需真实后端 + `.env` + 登录态 |
| 真机冒烟 | 上 staging 前 | 参考标准：29 项全过 |

> 🔴 **两类离线测试必须分开跑（CI 已踩过的坑）**：`tests/` 下混着
> **pytest 风格**（有 `def test_*`）与**脚本式自测**（无 `test_*`，检查写在模块级、结尾 `sys.exit`）。
> 后者若被 `pytest` 收集：全过时报 `collected 0 items`（**假绿，零覆盖**），
> 失败时 `sys.exit(1)` 干掉整个 pytest 进程、看不出哪项挂。
> **脚本式必须用 `python tests/xxx.py` 跑**。判断：`grep -c '^def test_' tests/test_xxx.py`，输出 0 即脚本式。
> 详见 `04_环境与部署.md` §2.7。

> **不写测试的功能不算完成**。新增端点必须带真机冒烟记录（贴命令与输出）。

**CI 配置要求**：触发分支必须含 `main` **和** `dev`；依赖使用缓存；失败信息可定位；
禁止跑需要真实 API Key 的用例；禁止使用生产密钥；CI 时长控制在 **10 分钟内**。

**CD 要求**：staging 在合并 `dev` 后**自动**部署、无需人工批准；
生产在合并 `main` 后**手动**触发、需申请人批准。
部署后**必须**健康检查（`/health` 200 + 前端首页可访问）；失败**立即回滚**。

---

# 第五章 环境与部署

## 5.1 环境总览

| 环境 | 位置 | 前端端口 | 后端端口 | 数据目录 | 用途 |
| --- | --- | --- | --- | --- | --- |
| 本地开发 | 各人电脑 | 3000 | 8000 | 本地 `data/` | 日常开发 |
| **Staging** | 阿里云 ECS（**与生产同机**） | **3001** | **8001** | `data-staging/` | 联调、验收、演示预演 |
| 生产 | 阿里云 ECS | 3000 | 8000 | `data/` | 对外 |

**硬件约束（务必记住）**：2 核 2G；前端构建约需 3～8 分钟；已启用 swap；
构建参数 `NODE_OPTIONS=--max-old-space-size=1536`。
**不要在 ECS 上跑 `pytest` 全量集成测试**，会挤爆内存。

## 5.2 开发环境配置

**版本要求（硬约束）**

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| **Python** | **3.12**（3.11～3.13 可用） | ⚠️ **不能用 3.14**：`chromadb==0.5.23` 装不上 |
| Node.js | **≥ 20.9**（推荐 20.19.0） | Next.js 16 硬性要求 |

```bash
# 后端
python -m venv venv
venv\Scripts\activate                 # Windows；Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
pip install pytest ruff

# 配置
copy .env.example .env                # 填 DEEPSEEK_API_KEY / EMBEDDING_API_KEY / EMBEDDING_BASE_URL

# 启动
python main.py                        # 后端 http://127.0.0.1:8000
cd frontend && npm install && npm run dev   # 前端 http://localhost:3000
```

**必须填的三项**（与 `core/config.py` 的 `validate()` 一致）：
`DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`

> **为什么要单独配 embedding**：DeepSeek **不提供** embedding 接口，向量化必须由独立服务完成。

**配置读取规则**：所有 Key / 路径 / 阈值**只能**从 `core/config.py` 读，业务代码禁止硬编码。

## 5.3 测试数据存储与隔离

**重要**：本项目**没有** MySQL / PostgreSQL 之类的数据库服务，所以**没有「连接地址」这类配置**。
数据全部落在**进程本地**：`data/sessions.db`（SQLite）、`data/scheduler.db`（SQLite）、
`chroma_db/`（Chroma 持久化目录）。

> 也就是说，「测试数据库连接地址」在本项目的真实答案是：**它是文件路径，不是连接串**。
> 对应配置项是 `DATA_DIR` 与 `PERSIST_DIR`。

**测试如何隔离**：靠 `monkeypatch` 把路径改到临时目录。`core/session.py` 的连接管理刻意做了路径判断 ——
路径一变**必须**换连接，否则会继续读写上一个库。

**各环境隔离**

| 环境 | `DATA_DIR` | `PERSIST_DIR` |
| --- | --- | --- |
| 本地 | `data` | `chroma_db` |
| **Staging** | **`data-staging`** | **`chroma-staging`** |
| 生产 | `data` | `chroma_db` |

> ⚠️ **Staging 绝不能指向生产的 `data/`**。一旦共用，staging 的测试数据会污染生产用户，
> 且 `chroma_db` 并发写可能损坏索引。`deploy-staging.sh` 已内置硬拦截。

**备份（改结构前必做）**

```bash
cp data/sessions.db  data/sessions.db.bak.$(date +%Y%m%d)
cp data/scheduler.db data/scheduler.db.bak.$(date +%Y%m%d) 2>/dev/null || true
tar czf chroma_db.bak.$(date +%Y%m%d).tar.gz chroma_db/
```

**表结构变更流程**：改 `_init_db()` + 加幂等 `ALTER TABLE` 迁移 → 同步 `02` 文档 → 通知全员。
现有模式是「新建库直接带新列 + 老库靠 `ALTER` 补列」，两条路径得到同一套结构。

## 5.4 API 命名管理方式

**现状与待拍板**

| 口径 | 值 | 出处 |
| --- | --- | --- |
| 规划文档（T5 统稿） | `/api/v1` | `docs/挑战杯_最终分工与任务清单_v1.md` |
| **实际实现（35 个端点）** | **`/api`（无 v1）** | `main.py` 的 `include_router` |

> 🅿️ **本轮不拍板**，只把管理方式定下来。

**命名管理规则（现在就生效）**

1. **前缀只在 `main.py` 定义一次**，路由文件内部只写相对路径 → 将来迁到 `/api/v1` 只改一处。
2. **路径全小写 + 连字符分词**：`/api/kb/import-search` ✅，`/api/kb/importSearch` ❌
3. **尾斜杠由本项目自己归一**（`redirect_slashes=False` + 中间件），不要引入新的不一致。
4. **资源路径用名词复数**：`/api/sessions/` ✅，`/api/getSessions` ❌
5. **动作型端点用动词子路径**（仅限确实不是 CRUD 的操作）：`POST /api/report/generate`
6. **新增端点必须同步三处**：`main.py` 挂载点、本文档端点索引、前端 `lib/api.ts`
7. **鉴权默认开启**：业务 router 一律挂 `Depends(get_current_user)`；**只有 `auth` 路由例外**。
8. **契约零破坏**：现有 35 个端点的路径、请求字段、响应形状一律不改。

**端点索引（35 个）**

| 前缀 | 归属 | 端点数 |
| --- | --- | --- |
| `/api/auth` | 申请人 | 5 |
| `/api/chat` | 申请人 | 2 |
| `/api/kb` | 林宏亮 | 14 |
| `/api/sessions` | 申请人 | 6 |
| `/api/feedback` | 申请人 | 2 |
| `/api/geo` | 申请人 | 1 |
| `/api/templates` | 申请人 | 2 |
| `/api/usage` | 申请人 | 1 |
| `main.py`（`/health`、`/api/config/defaults`） | 申请人 | 2 |
| `/api/agent` `/api/schedule` `/api/report` `/api/delegate` | 刘佳林 | 14（**待合入**） |

## 5.5 Staging 环境部署

**设计目标**：在**同一台 ECS** 上跑一套与生产完全独立的实例，用于联调、验收、演示预演。

**三重隔离**

| 维度 | 生产 | Staging | 隔离手段 |
| --- | --- | --- | --- |
| 端口 | 3000 / 8000 | **3001 / 8001** | 不同监听端口 |
| 数据 | `data/`、`chroma_db/` | **`data-staging/`、`chroma-staging/`** | 不同目录，**绝不共用** |
| 配置 | `.env` | **`.env.staging`** | 独立密钥与配额 |
| 代码 | `/root/agent-hub` | **`/root/agent-hub-staging`** | 两个独立 git 工作区 |
| 日志 | `/tmp/baomi-*.log` | `/tmp/baomi-staging-*.log` | 不同文件 |
| PID | `/tmp/baomi-*.pid` | `/tmp/baomi-staging-*.pid` | 不同文件 |

> **为什么用两个工作区而不是同一目录切分支**：同一目录切分支会让生产代码在构建期间**处于半成品状态**，
> 前端 `.next` 也会被覆盖。两个工作区互不影响，代价只是多一份 `venv` 与 `node_modules`。

**首次部署**

```bash
# 0) 前置：确认生产在跑
bash deploy.sh status

# 1) 克隆第二个工作区（分支锁定 dev）
cd /root
git clone git@github.com:baomi888/agent-hub.git agent-hub-staging
cd agent-hub-staging && git checkout dev

# 2) 准备 staging 配置
cp .env.staging.example .env.staging
nano .env.staging
#    必填：DEEPSEEK_API_KEY / EMBEDDING_API_KEY / EMBEDDING_BASE_URL
#    必须确认：DATA_DIR=data-staging、PERSIST_DIR=chroma-staging、AUTH_SECRET 另设一个

# 3) 一键部署
bash deploy-staging.sh deploy
```

**日常更新**：`cd /root/agent-hub-staging && bash deploy-staging.sh deploy`

**`deploy-staging.sh` 子命令**

| 命令 | 作用 |
| --- | --- |
| `deploy` | 完整部署：拉取 → 依赖 → 构建 → 启动 |
| `start` | 只启动（跳过构建） |
| `stop` | 停止 |
| `restart` | 重启（不改代码） |
| `status` | 状态 + 健康检查 + **隔离自查** + 生产状态只读查看 |
| `logs` | 跟踪日志 |
| `env` | 只检查 `.env.staging` |
| `rollback` | 回滚到上一个可用提交并重启 |

**内置安全护栏**

1. **数据目录硬拦截**：若 `DATA_DIR=data` 或 `PERSIST_DIR=chroma_db`，**脚本拒绝继续**。
2. **只动 staging 端口**：停止服务时按端口精确匹配，不用宽泛的 `pkill -f "uvicorn main:app"`，避免误杀生产。
3. **工作区校验**：无 `.env.staging` 时直接中止，防止在生产目录误跑。
4. **回滚前 stash**：未提交改动先 stash，不丢失。

**CI/CD 自动部署**：合并到 `dev` → `.github/workflows/deploy-staging.yml` 自动 SSH 部署 + 健康检查。
所需 Secrets：`STAGING_HOST` / `STAGING_USER` / `STAGING_SSH_KEY` / `STAGING_URL`。

**部署后验收清单**

- [ ] `/health` 返回 200
- [ ] 前端首页可打开，**且登录页正常**
- [ ] 能完成一次完整问答（登录 → 新建会话 → 发消息 → 收到流式回答）
- [ ] 知识库列表加载正常（**不与生产串数据**）
- [ ] 若涉及调度模块：`/api/schedule/health` 心跳年龄正常（< 300s）
- [ ] 日志无异常堆栈

**端口与防火墙**：生产放行 `3000`，staging 放行 **`3001`**；
后端始终只监听 `127.0.0.1`（8000/8001 都**不需要**放行）。

**回滚**：`bash deploy-staging.sh rollback`
> **原则：出问题先回滚，再排查**。不要在 staging 上「边修边试」——
> 它存在的意义就是提供一个可信的验收基线，一个坏掉的 staging 比没有更糟。

---

# 第六章 分角色开发指南

四份指南的完整内容见仓库 `规范/guides/` 目录：

| 指南 | 面向 | 核心内容 |
| --- | --- | --- |
| `前端开发指南.md` | 申请人 | 技术栈、前后端通信、代码组织、设计系统、接口对接、已知缺口 |
| `AI检索开发指南.md` | 林宏亮 | 知识库标识、写入流水线、检索链路、阈值语义、搜索收口、安全 |
| `调度与Agent开发指南.md` | 刘佳林 | 独立 worker 进程、调度库、心跳判定、工具注册表、四个新路由契约 |
| `后端基础设施开发指南.md` | 申请人 | 认证、多用户隔离、配额限流、表结构演进、中间件机制、部署脚本 |

---

# 附录 A · 待拍板事项

| # | 事项 | 影响 | 状态 |
| --- | --- | --- | --- |
| 1 | **API 前缀 `/api` vs `/api/v1`** | 影响全部 35 个端点与前端调用 | **按指示暂缓** |
| 2 | 「每日委托」语义：补 `/submit` 还是改口径 | 影响 C 线实现与申报书描述 | 待定 |
| 3 | 文档口径与实现不一致（写了"只交付设计"但已编码） | 影响申报书表述 | 倾向承认已实现 |
| 4 | ~~GitHub 账号与人对应关系~~ | ~~影响 CODEOWNERS 是否拦对人~~ | ✅ **已确认**：`huashuotianxuan`=刘佳林，`sakasa-code`=林宏亮 |
| 5 | ~~每日总结 `content` 形状冲突~~ | ~~前端解析必须二选一~~ | ✅ **已裁决**（2026-10-13）：`content` 取设计稿 object 语义 + **补 `sources` 字段** + `stats` 合并为 9 字段；`source_refs` 用整数 ID。见 `02` §8.1 |
| 6 | **是否补 `/api/profile*` 端点** | 前端个人页对接 | 待定 |
| 7 | **标签钩子的存储与接口形状** | B 线接口未定义，仓库零命中 | 待定 |

# 附录 B · 已知缺口清单

| # | 缺口 | 位置 | 归属 | 严重度 |
| --- | --- | --- | --- | --- |
| 1 | `api/report.py` 两处同步调用未包 `to_thread` | `api/report.py` | C 线 | **高** |
| 2 | 端点 `/today` 与文档契约 `/{date}` 不一致 | `api/report.py` | C 线 | 中 |
| 3 | `/templates` 不在 `AuthGate` 内 | `frontend/src/app/templates/page.tsx` | A 线 | 中 |
| 4 | 搜索双轨未收口 | `agent/tools.py` vs `core/serper.py` | B/C 线 | 中 |
| 5 | 入库中途失败不回滚（无 `delete_by_source`） | `api/kb.py:127-148` | B 线 | 中 |
| 6 | 会话无总量上限 | `core/session.py` | A 线 | 低 |
| 7 | `top_k` / `chunk_size` 无边界校验 | `api/chat.py:68`、`api/kb.py` | B 线 | 中 |
| 8 | 反馈写入无长度/频率体检 | `api/feedback.py` | A 线 | 低 |
| 9 | Agent 图缓存键含原始经纬度导致缓存永不命中 | `agent/builder.py:67/80/145-149` | C 线 | 中 |
| 10 | 「标签钩子」仓库零命中，接口未定义 | — | B 线 | 中 |

# 附录 C · 不可违反的红线（TL;DR）

1. **`.env` / `.env.staging` 永不入库**，也**永远不要 `git add -f`** 加环境文件。
2. **不直推 `main`**。必须走 PR + 至少 1 人审核。
3. **不提交真实密钥、真实服务器 IP、真实个人信息**。测试占位用 RFC 5737 的 `203.0.113.x`。
4. **不跨模块改动他人文件而不通知**。
5. **契约破坏性变更必须升级版本并同步前端与文档**。
6. **`async def` 里禁止同步阻塞**，一律 `await asyncio.to_thread(...)`。
7. **新增横切逻辑必须补齐配套缺口**（前端解析、错误文案、回归测试）。

---

# 变更记录

| 日期 | 版本 | 变更 | 人 |
| --- | --- | --- | --- |
| 2026-10-13 | v1.0 | 首版：项目概览与分工、分层架构与模块职责、核心数据结构、流程规范、环境与 Staging 部署、分角色指南索引 | 申请人 |
