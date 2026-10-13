# 知穗 Agent · 团队协作与研发规范

> 本目录（仓库根 `规范/`）是**团队协作的唯一入口**。每个人的职责边界、接口契约、开发环境、提交与发布流程，都以这里的文档为准。
>
> **产生冲突时**：以本目录文档为准；文档与代码不一致时，先改文档再改代码（或同时改）。
>
> **目录约定**：规范文档一律放 `规范/`，业务文档放 `docs/`。不要把规范混进 `docs/`。

---

## 一、这套文档解决什么问题

之前团队的痛点是「规则散落在各人的脑子里和聊天记录里」：分支怎么开、提交怎么写、谁能合 main、
staging 怎么部署、测试库连哪，全靠问。结果是：

- 三个人三条线，成果都停在 `dev` / `feat/*`，**`main` 落后 8～11 个提交**；
- 接口前缀一边写 `/api`、一边写 `/api/v1`，到联调才发现；
- 同一个「搜索」有两套实现（`agent/tools.py` 与 `core/serper.py`），改一处漏一处。

本套文档把这四类问题一次性定死。

---

## 二、文档地图：我该看哪一份

| 文档 | 解决什么 | 谁必须读 |
| --- | --- | --- |
| [01_架构与模块职责.md](./01_架构与模块职责.md) | 系统有哪些层、每个模块归谁、谁能改谁的文件、跨模块怎么改 | **全员** |
| [02_核心数据结构定义.md](./02_核心数据结构定义.md) | 用户 / 会话 / 消息 / 知识库 / 文档 / 检索结果 / 总结 / 委托 的字段契约 | **全员**（后端必读） |
| [03_流程规范.md](./03_流程规范.md) | 分支策略、提交规范、代码审核、CI/CD 管线要求 | **全员** |
| [04_环境与部署.md](./04_环境与部署.md) | 开发环境配置、测试数据库、API 命名管理、**Staging 部署** | **全员** |
| [guides/前端开发指南.md](./guides/前端开发指南.md) | 前端（Next.js 16）怎么起、怎么接接口、UI 规范 | 前端 |
| [guides/AI检索开发指南.md](./guides/AI检索开发指南.md) | 知识库 / 检索 / 搜索 / 蒸馏 / 标签钩子 | 林宏亮 |
| [guides/调度与Agent开发指南.md](./guides/调度与Agent开发指南.md) | Agent 编排 / 每日委托 / 每日总结 / worker | 刘佳林 |
| [guides/后端基础设施开发指南.md](./guides/后端基础设施开发指南.md) | auth / quota / session / 数据模型 / 部署脚本 | 申请人 |
| [团队研发规范_Word手册.md](./团队研发规范_Word手册.md) | 以上内容的**汇编版**（用于汇报 / 打印 / 答辩） | 全员（参考） |
| [决策记录.md](./决策记录.md) | **已拍板的技术决策与理由**（含每日总结形状冲突的裁决） | **全员** |

> **只看一份的话**：先读 `01` 和 `03`，这两份覆盖了 80% 的日常协作问题。

---

## 三、60 秒上手（新队友第一天）

```bash
# 1) 克隆并切到你自己的分支（分支命名见 03_流程规范.md）
git clone git@github.com:baomi888/agent-hub.git
cd agent-hub
git checkout dev
git pull
git checkout -b feat/你的模块-日期        # 例：feat/kb-hybrid-search-20261020

# 2) 后端环境（必须 Python 3.12，chromadb==0.5.23 装不上 3.14）
python -m venv venv
venv\Scripts\activate            # Windows；Linux/macOS 用 source venv/bin/activate
pip install -r requirements.txt
pip install pytest ruff

# 3) 配置（严禁提交 .env）
copy .env.example .env           # 然后填 DEEPSEEK_API_KEY / EMBEDDING_API_KEY

# 4) 起后端（只监听回环 127.0.0.1:8000）
python main.py

# 5) 起前端（另开一个终端）
cd frontend
npm install
npm run dev                      # http://localhost:3000

# 6) 提交前自检（这三条必须全绿）
ruff check .
pytest -q
cd frontend && npx tsc --noEmit
```

**验证后端就绪**：`curl http://127.0.0.1:8000/health` → 期望 `{"status":"ok",...}`

---

## 四、不可违反的红线（TL;DR）

1. **`.env` 永不入库**。仓库里只有 `.env.example` / `.env.staging.example` 模板。
2. **不直推 `main`**。`main` 受保护，必须走 PR + 至少 1 人审核。
3. **不提交真实密钥、真实服务器 IP、真实个人信息**。测试占位用 RFC 5737 的 `203.0.113.x`。
4. **不跨模块改动他人文件而不通知**（归属见 `01_架构与模块职责.md`）。
5. **契约破坏性变更必须升级版本并同步前端**（见 `04_环境与部署.md` 的 API 命名管理）。
6. **`async def` 里禁止同步阻塞调用**，一律 `await asyncio.to_thread(...)`。
7. **新增认证 / 配额 / 限流等横切逻辑时，必须补齐配套缺口**（前端解析、错误文案、回归测试）。

---

## 五、当前状态快照（2026-10-13）

| 项目 | 现状 |
| --- | --- |
| 主干 | `main` = `96e0dda`（接口文档版 + UI 统一改造） |
| 落后 | `main` 比 `dev` 少 8 个提交，比 `feat/ai-module-20261013` 少 11 个 |
| 队友成果位置 | `origin/dev`、`origin/feat/ai-module-20261013`（**尚未合入 main**） |
| 测试 | pytest 28 → 72 passed + 18 subtests（在后端线上） |
| 环境约束 | **必须 Python 3.12**（`chromadb==0.5.23` 不支持 3.14） |
| 前端 | Next.js 16.3.5 + React 19.2.8 + Tailwind v4，`tsc --noEmit` Exit 0 |
| 部署 | 阿里云 ECS（2 核 2G），`deploy.sh` 裸机 venv 部署，前端 :3000 / 后端 :8000；staging 分端口 3001/8001 |
| 账号归属 | ✅ 已确认：`huashuotianxuan`=刘佳林，`sakasa-code`=林宏亮 |
| 待拍板 | API 前缀 `/api` vs `/api/v1`（**已按你的要求暂缓，见 `04` 第 4 节**） |

---

## 六、本文档的维护

- 归属：申请人（总负责）维护，任何队友可提 PR 修改。
- 触发更新：模块归属变化、接口契约变化、环境/部署方式变化、CI 管线变化。
- 每次更新在文档末尾「变更记录」追加一行，不要直接覆盖历史结论。
- **已拍板的决策写进 [决策记录.md](./决策记录.md)**，不要只留在聊天记录里。
