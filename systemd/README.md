# systemd 服务单元（nohup → systemd）

把三个常驻进程交给 systemd 托管，获得**自动重启**、**开机自启**和**结构化日志（journald）**。

| 文件 | 托管进程 | 监听 |
| --- | --- | --- |
| `agent-hub-backend.service` | `uvicorn main:app` | `127.0.0.1:8000` |
| `agent-hub-frontend.service` | `next start` | `0.0.0.0:3000` |
| `agent-hub-worker.service` | `python -m worker.scheduler` | 无（调度 worker，静默故障重灾区） |

## 安装

```bash
cd /root
bash systemd/install-systemd.sh          # 默认 APP_DIR=/root
# 或
bash systemd/install-systemd.sh /root/baomiagent
```

脚本会：校验目录 → 停掉现有 nohup 进程 → 渲染并安装 unit → `enable --now` → 自检。

## 为什么 unit 里不配 EnvironmentFile

后端 `core/config.py` 自己 `load_dotenv(.env)`，systemd 再解析一遍 `.env`
容易因中文/引号/空值报错导致**服务直接起不来**。所以只给 `PYTHONUTF8=1`，
业务配置全部交给应用自己读 `.env`。

## ⚠️ 与 deploy.sh 的冲突（必读）

`deploy.sh` 的 `start/stop/restart` 仍然是 **nohup + pid 文件**那一套。
迁移到 systemd 后如果再用 `bash deploy.sh restart`，会出现**两套进程抢端口**
（新进程起不来 / 旧进程被杀）。约定：

- **首次部署 / 重新构建**：用 `bash deploy.sh deploy`（它会构建并在末尾启动）
- **日常重启 / 看日志**：用 `systemctl` / `journalctl`
- 若已完全迁移，建议把 `deploy.sh` 的第 7 段启动改成 `systemctl restart ...`（后续可选改造）

## 回滚

```bash
bash systemd/install-systemd.sh --rollback
bash deploy.sh start
```

## 常用命令

```bash
systemctl status  agent-hub-backend agent-hub-frontend agent-hub-worker
systemctl restart agent-hub-backend
journalctl -u agent-hub-backend -f
journalctl -u agent-hub-worker -n 100
```
