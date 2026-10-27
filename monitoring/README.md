# 监控与告警（Prometheus + node_exporter）

目标：把「页面正常、但 worker 静默死亡 / 磁盘写满 / 主机失联」这类**静默故障**变成主动告警。

> ⚠️ 资源提醒：这台 ECS 是 2C2G。Prometheus 常驻约 **150–250MB**，
> 已用 `mem_limit: 256m` 封顶、`retention=15d` 限制磁盘。若内存吃紧，
> 可只保留 `node_exporter` + 外部 uptime，先不上 Prometheus。

## 组成

| 文件 | 作用 |
| --- | --- |
| `docker-compose.yml` | Prometheus（`127.0.0.1:9090`）+ node_exporter（`127.0.0.1:9100`），均只绑回环 |
| `prometheus.yml` | 抓取配置（15s） |
| `alerts.yml` | **3 条告警规则** |
| `probe-health.sh` | 每分钟探测后端 /health、前端 /、worker 进程，写 textfile 指标 |

## 部署步骤

```bash
# 1) 准备 textfile 目录（node_exporter 容器会挂它）
sudo mkdir -p /var/lib/node_exporter/textfile

# 2) 起监控栈
cd /root/monitoring
sudo docker compose up -d

# 3) 装探针 cron（每分钟）
chmod +x probe-health.sh
( crontab -l 2>/dev/null; echo "* * * * * /root/monitoring/probe-health.sh >/dev/null 2>&1" ) | crontab -

# 4) 验证指标已经写出来
cat /var/lib/node_exporter/textfile/agent_hub.prom
```

没有 Docker 的话，用 `apt install prometheus prometheus-node-exporter` 也能跑，
把 `prometheus.yml` / `alerts.yml` 放到 `/etc/prometheus/`，node_exporter 加
`--collector.textfile.directory=/var/lib/node_exporter/textfile` 即可。

## 三条告警规则

| 告警 | 触发条件 | 覆盖的故障 |
| --- | --- | --- |
| `AgentHubComponentDown` | `agent_hub_up == 0` 持续 2m | 后端/前端/worker 任一挂掉（**含静默的 worker 死亡**） |
| `InstanceDown` | `up{job="node"} == 0` 持续 2m | 主机宕机 / node_exporter 失联 |
| `DiskSpaceLow` | 磁盘可用 < 10% 持续 10m | 云盘写满导致 SQLite/Chroma 崩溃 |

## 告警怎么真正「响」到你手机/邮箱

Prometheus 只负责**触发**，不负责**投递**。三种方式任选：

1. **Alertmanager（自建，最完整）**：再加一个容器，配置 webhook/邮件/钉钉。
2. **Grafana Cloud / 阿里云 ARMS**：把 Prometheus 数据 remote_write 上去，用它的告警。
3. **外部 uptime（最省事，推荐先上这个）**：
   - **UptimeRobot**（免费 50 个监控）：监控 `https://你的域名` 和
     `http://8.163.62.25:3000/health`，异常邮件通知。
   - **healthchecks.io**（免费，心跳式）：在 `probe-health.sh` 末尾加一句
     `curl -fsS -m 5 https://hc-ping.com/<uuid>`，探针不跑也会告警。
   > 这两个都需要你自己的账号，我无法代注册；账号建好后把地址填进去即可。

## 查看

Prometheus 只监听回环，本地这样访问：

```bash
ssh -L 9090:127.0.0.1:9090 root@8.163.62.25
# 然后浏览器打开 http://localhost:9090 ，Alerts 页看规则状态
```

## 停止 / 回滚

```bash
cd /root/monitoring && sudo docker compose down
crontab -l | grep -v probe-health.sh | crontab -   # 移除探针 cron
```
