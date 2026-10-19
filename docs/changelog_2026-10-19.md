# 苞米agent 修复记录 · 2026-10-19

> 本文件随代码提交到 GitHub，供团队/部署复核。
> 详细"还能改什么"分析见本地 `docs/前后端可更新点_2026-10-19.md`（该文件按约定不入库）。

## 背景
当天基于代码现状做了一轮"已有功能 vs 真实缺口"审计，产出 `前后端可更新点_2026-10-19.md`。
本日把其中确认的、可低风险落地的项全部修完（分两批：上午批次 + 本次后7/前5）。

## 一、全天修复总览

### 后端
| 项 | 内容 | 状态 |
|---|---|---|
| 后1 | 编辑/切换委托开关时不再把 `next_run_at` 重置为 NULL（此前会立刻多派发一次、白烧一次 LLM，并刷掉 `created_at`） | ✅ 上午批 |
| 后2 | 窗口放宽（30 轮 / 混合 60 条）后主链路 prompt 越过 DeepSeek 64K → 输入侧加 `CHAT_HISTORY_MAX_CHARS` 预算并丢最旧；混合 agent 节点单条 `[:500]` 对齐 | ✅ 上午批 |
| 后3 | 打卡/报告共 6 个同步 LLM 端点全部包 `asyncio.to_thread`（与委托链路同款，避免阻塞事件循环） | ✅ 上午批 |
| 后4 | 每周/间隔原本是"假功能"（一律 +86400 退化成每天一次）→ 新增 `compute_next_run()` 按 `freq` 真算下次时间，PATCH 改频次时按新节奏重算 `next_run_at` | ✅ 上午批 |
| 后6 | 手动执行"顺延到次日"的行为与 docstring/注释对齐 | ✅ 上午批 |
| **后7** | **自动停用时无告知 → 委托表新增 `last_error` 列，worker 致命失败时写入原因并透传给前端展示** | ✅ **本次新增** |
| 后5 | `agent/tools.py` 计算器仍用裸 `eval` | 刻意保留：现有 `eval` 已是 `{"__builtins__":{}}` 沙箱 + 多层守卫；而 `hybrid/expr.py::safe_eval` 不支持 `**`，替换反而破功能，收益不抵风险 |

### 前端
| 项 | 内容 | 状态 |
|---|---|---|
| 前1 | 改 name 被静默丢弃还提示"已保存" | 经核对 `delegate/page.tsx` 已是 `disabled={editing}`，误报，无需改 |
| 前2 | 删除确认文案与后端相反（后端保留历史）→ 改为"执行记录会保留" | ✅ 上午批 |
| 前3 | 首页 SSE error 帧改用 `detailToMessage`，不再出 `[object Object]` | ✅ 上午批 |
| 前4 | `lib/api.ts` 默认 60s 超时 + `AbortController`，打卡/总结/委托执行给 120s，不再无限转圈 | ✅ 上午批 |
| **前5** | **`/templates` 模板广场此前是唯一无登录门禁的页面 → 接入 `AuthGate`（与四屏同一套），未登录整页挡住** | ✅ **本次新增** |

## 二、本次提交（后7 + 前5）具体改动文件

**后端**
- `service/daily.py`
  - `delegations` 表新增 `last_error TEXT` 列（建表 DDL + 旧库 `ALTER` 幂等补列）
  - `set_enabled()`：重新启用时清 `last_error`
  - `save_delegation()`：`INSERT OR REPLACE` 保留 `last_error`（避免顺手改名字误清告警），仅重新启用时清空
  - `on_failure()`：签名加 `error` 参数，致命失败时写 `last_error`
- `worker/scheduler.py`：`on_failure` 调用处把运行异常摘要传给业务层；同步更新契约 docstring
- `api/delegate.py`：`_out()` 透出 `last_error`
- `tests/test_delegate_flow.py`：新增 `test_auto_disable_records_last_error` / `test_reenable_clears_last_error`；并在 `setUp` 复位 `quota._HITS` 修复"同用户名跨用例限流累积"的测试隔离 flake

**前端**
- `frontend/src/app/templates/page.tsx`：接入 `useAuth` + `AuthGate`，未登录整页挡住；`useEffect` 依赖 `auth.me` 登录后自动拉目录
- `frontend/src/app/delegate/page.tsx`：委托卡片在"已停用且带 `last_error`"时显示红色原因而非泛泛"已暂停"
- `frontend/src/lib/types.ts`：`Delegate` 接口加 `last_error?`

## 三、数据迁移
`delegations` 表新增 `last_error` 列：**旧库无需手动迁移**，`_ensure_daily_tables()` 首次启动会对已存在的表执行 `ALTER TABLE ... ADD COLUMN`（被 try/except 包住，列已存在则忽略）。新库直接带该列。

## 四、验证
- 后端 `pytest tests`：全绿（新增 2 条回归钉；`test_delegate_flow.py` 13 passed）
- 前端 `tsc --noEmit`：Exit 0
- 注：`next build` 在沙箱会因 safe-delete 守卫失败（与代码无关），按约定未用其验证。

## 五、部署注意
- 线上 `data/sessions.db` 旧库启动即自动补 `last_error` 列，无需停机迁移。
- 前端仅模板页加门禁、委托卡片多一处告警展示，**无新增依赖、无构建参数变化**。
- 灰度建议：先拉 `main` 部署，验证委托"每天/每周/间隔"三种频次 `next_run_at` 按预期推进、以及一个被自动停用的委托能在卡片上看到原因。
