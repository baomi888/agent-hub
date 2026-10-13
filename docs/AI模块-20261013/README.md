# AI 模块交付（2026-10-13）

本目录是 **2026-10-13** 一天工作的全部交付物，围绕一件事展开：

> **让「每日总结」能被无人触发地定时生成**，并把它的三个前置底座一起铺好
> （工具执行元数据 / 独立调度 worker / 结论层蒸馏检索）。

对应团队后端岗（林宏亮）的五条职责中的第 1、2 条与部分第 5 条，
详见 [02-工作文档.md](02-工作文档.md) §3 的对照表。

---

## 阅读顺序

| 顺序 | 文件 | 读者 | 内容 |
| --- | --- | --- | --- |
| 1️⃣ | [02-工作文档.md](02-工作文档.md) | **所有人** | 为什么这么做（六个决定 + 否决理由）与如何做的（流程 / 分工 / 验证手段 / 踩坑） |
| 2️⃣ | [01-结构树.md](01-结构树.md) | 想快速看全局 | 分支拓扑图 + 今日提交树 + 代码结构树 + 交付文档结构树 |
| 3️⃣ | [03-接口文档.html](03-接口文档.html) | **前端 / 评审** | 34 个新端点契约 + Mock + 现有 35 端点全量索引。单文件自包含，**双击即可离线打开** |
| 4️⃣ | [04-测试文档.md](04-测试文档.md) | **要亲手验的人** | 13 组用例（A–M），每条给 curl + 界面两种路径，「实际结果」列留空给你填 |
| 5️⃣ | [05-验证记录.md](05-验证记录.md) | 想核对证据的人 | 8 条验证命令的真实输出与退出码（脚本采集，非手写） |

> **最省时间的路径**：先读 02 的第一节（六个决定），再打开 03 看接口，最后照 04 挑几条跑。

---

## 一句话结论

| 项 | 结果 |
| --- | --- |
| 测试 | **72 passed + 18 subtests**（改动前基线 28 —— 只增不减） |
| 新端点 | 34 条（工具 2 / 调度 5 / 总结 5 / 委托 7 / 蒸馏 5 / 编排 10） |
| 真机冒烟 | 29 项全过（起真实 uvicorn 打真实 HTTP） |
| 接口文档 | 静态 13 项 + headless Edge 25 项双校验通过 |
| 未完成 | 联网搜索协同设计、申报书 AI 板块文字、标签钩子定义、蒸馏检索实现 |

---

## 环境（要跑起来必须先做这一步）

```powershell
# ⚠️ 必须用 Python 3.12 —— requirements.txt 钉死的 chromadb==0.5.23 装不上 3.14
$V = 'C:\biancheng\.scratch\ifdoc\venv312\Scripts\python.exe'
$V --version                                              # 期望 Python 3.12.15
& $V -m pip install -r C:\biancheng\agent-hub\requirements.txt

cd C:\biancheng\agent-hub
& $V -m pytest -q                                          # 期望 72 passed
& $V -m uvicorn main:app --port 8000                       # 起服务
curl http://127.0.0.1:8000/health
```

一键基线（起服务 + 探端点 + 注册 + 带 Cookie 复探，自动收尾）：

```powershell
python C:\biancheng\.scratch\daily-scheduler\collect_baseline.py
python C:\biancheng\.scratch\daily-scheduler\smoke_new_endpoints.py    # 新端点 29 项冒烟
```

---

## 规格与工单

口径、范围、硬约束、验收标准都在 `.scratch/daily-scheduler/`：

```
spec.md                                  总规格（目标 / 用户已拍板口径 / 范围 / 硬约束 / 验收 / 风险）
issues/01-interface-doc.md               接口文档扩写
issues/02-test-doc.md                    测试文档
issues/03-tool-registry.md               工具注册表 + 延迟发现
issues/04-scheduler.md                   调度 worker + 可观测
issues/05-daily-summary.md               每日总结 + 每日委托
issues/06-distillation.md                蒸馏检索（结论层）
issues/07-linhongliang-deliverables.md   林宏亮五份交付汇总
```

---

## 两条硬约束（改动前务必看）

1. **现有 35 个端点的路径、请求字段、响应形状一律未改** —— 前端契约零破坏是本轮的验收项之一
   （测试文档 M 组就是验它）。
2. **`job_runs` 表在 `data/scheduler.db`，不在 `sessions.db`** —— 跨库查表是本轮踩过的坑
   （`GET /api/delegate/{id}/runs` 曾因此 500）。见 `core/scheduler.py::_db_path()`。
