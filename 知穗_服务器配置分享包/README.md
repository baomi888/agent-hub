# 知穗 Agent · 服务器配置分享包

把「知穗 Agent」部署到 **Linux 服务器（Ubuntu ECS）** 所需的最小配置集。
**不含** `node_modules` / `.next` / 密钥 / 数据库，体积小、可直接分享。

> 注意：本包**不含** Windows 版 nginx（`nginx-1.30.6/` 是本地 Windows 副本，服务器用不上）。
> 目标机的 nginx 由 HTTPS 脚本现场用 `apt` 安装；纯 HTTP 阶段甚至不需要 nginx。

---

## 目录结构

```
知穗_服务器配置分享包/
├── deploy.sh                              # 生产一键部署（核心）
├── deploy-staging.sh                      # staging 一键部署（与生产并存）
├── .env.example                           # 生产环境变量模板
├── .env.staging.example                   # staging 环境变量模板
├── .github/
│   └── workflows/
│       └── deploy-staging.yml             # GitHub Actions：手动部署 staging
└── README.md                              # 本文件
```

---

## 各文件说明

| 文件 | 作用 | 放到哪里 |
| --- | --- | --- |
| `deploy.sh` | 生产部署：检测/安装 Node≥20、Python≥3.10，建 2G swap，检查 `.env`，建 venv 装依赖，前端 `npm run build`，后台起 **uvicorn(:8000 回环)+next(:3000)+worker**，放行本机防火墙，自检 | 服务器项目根目录（如 `/root`） |
| `deploy-staging.sh` | staging 部署：与生产同机不同端口（前端 3001 / 后端 8001） | staging 项目根目录（如 `/root/agent-hub-staging`） |
| `.env.example` | 生产环境变量模板（复制成 `.env` 后再填 Key） | 与 `deploy.sh` 同目录 |
| `.env.staging.example` | staging 环境变量模板（复制成 `.env`） | 与 `deploy-staging.sh` 同目录 |
| `.github/workflows/deploy-staging.yml` | GitHub Actions 工作流：**仅手动触发**（`workflow_dispatch`），通过 SSH 跑 `deploy-staging.sh deploy` | 复制到**仓库**的 `.github/workflows/` 下（GitHub 只认这里） |

> **workflow 说明**：本包内是「仅手动」版本（推 `dev` 不会自动部署，避免无 secrets 时报「没有运行作业」）。
> 如果你希望推 `dev` 自动部署 staging，把 `on:` 改成 `push: branches: [dev]` + `workflow_dispatch` 即可。

---

## 生产部署（纯 HTTP，最快跑通）

1. 把本包内容传到服务器项目根目录（如 `/root`），或在服务器上 `cd` 到该目录。
2. 先跑一次让脚本生成 `.env`：
   ```bash
   bash deploy.sh
   ```
3. 按提示编辑 `.env`，至少填好这 3 项（`core/config.py` 只强制校验这 3 个）：
   - `DEEPSEEK_API_KEY`
   - `EMBEDDING_API_KEY`
   - `EMBEDDING_BASE_URL`（例：`https://dashscope.aliyuncs.com/compatible-mode/v1`）
4. 填好后再跑一次：
   ```bash
   bash deploy.sh
   ```
5. 阿里云安全组入方向放行 **3000**（后端 8000 已只监听回环，无需放行）。
6. 浏览器打开 `http://<公网IP>:3000`。

日常运维：
```bash
bash deploy.sh status     # 看后端/前端/worker 三进程
bash deploy.sh logs       # 跟日志
bash deploy.sh restart    # 重启
bash deploy.sh stop       # 停止
```

---

## staging 部署（与生产并存）

```bash
# 在 staging 项目目录
cd /root/agent-hub-staging
cp .env.staging.example .env     # 首次
nano .env                        # 填 Key（可与生产不同）
bash deploy-staging.sh deploy
```
staging 用 **3001（前端）/ 8001（后端回环）**，安全组按需放行 3001。

---

## 注意事项

- **编码/换行**：包内所有文本统一 **UTF-8 无 BOM + LF**（Linux 目标）。请勿用记事本另存为 CRLF，否则 shell 脚本在 Linux 上可能出现 `$'\r'` 报错。
- **不含真实密钥**：`.env.example` 只有占位符；真实 `.env` 请勿提交、勿放进分享包。
- **HTTPS**：本包不含 HTTPS 配置。域名 ICP 备案通过后，用主仓库的 `https/setup-https.sh <域名> [邮箱]` 一键上线（nginx 443 反代 + Let's Encrypt 自动续期）。
- **Windows nginx 不要带**：`nginx-1.30.6/` 是 Windows 版，目标是 Linux，带过去也跑不起来，纯属增加体积。
