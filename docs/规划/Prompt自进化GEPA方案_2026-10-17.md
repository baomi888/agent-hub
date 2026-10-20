# Prompt 自进化 Loop（GEPA 式）落地方案

> 2026-10-17 · 评估对象：把「数据驱动的 Reflective Mutation + Badcase 归因 + 帕累托前沿多目标择优」引入 agent-hub  
> 结论先行：**可以加入，且本项目基础设施比典型 RAG 项目更接近可落地状态。唯一系统性缺口是「提示词还不是可版本化、可离线测量的对象」，补上这一课约 1 个迭代即可跑通最小闭环。**



---

## 1. GEPA 四要素与本项目现状的映射

| GEPA 要素                   | agent-hub 现状（已核实）                                                                                                    | 缺口                                            |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| Badcase 数据驱动归因            | `feedbacks` 表（up/down + comment，一条消息只留最后一次反馈）、`messages` 全文 + `refs`、`user_tags` 用户兴趣                                | 无「差样本集」导出 / 去重 / 按失败模式聚类                      |
| Reflective Mutation（反思变异） | `agent/builder.py::SYSTEM_PROMPT` 已按槽位组织（行为准则 9 条 / 绑库片段 / 场景片段）；`kb/rag_core.py::grounding_note` 已按检索强度分级注入【必须遵守】约束 | 无变异器（mutator）：输入 badcase + 当前提示词 → 输出结构化 diff |
| 帕累托前沿多目标择优                | 无                                                                                                                    | 需建：固定回放集 + 多指标判分 + 前沿选择                       |
| 小时级调优周期                   | `worker/scheduler.py` 已有完整 cron 框架（JOB_DEFS / claim 幂等 / 心跳 / 重试分级）                                                  | 加一个 `prompt.evolve` job 即可，调度侧零成本             |

另有三项已建资产可直接复用，是可行性成立的关键：

1. **双路径提示词天然是两个变异靶点**：Agent 路径（`agent/builder.py`，5 工具集合式 Agent）与 RAG 路径（`kb/rag_core.py`，纯检索问答），两者已解耦，可分别进化、独立回滚。
2. **已有提示词级干预先例**：P0 修复（2026-10-17，`8cfea31`）让 `agent/tools.py::kb_search` 复用 RAG 路径的 `grounding_note`，证明「按 badcase 改提示词约束」这条路在本项目已被验证过一次——GEPA 只是把「人工一次性改」变成「闭环批量改」。
3. **KV 缓存纪律已建立**（kv-cache 实验报告）：system prompt 前缀恒定、动态内容追加末尾。这给自进化加了一条硬约束（见 §5 最小 diff），反而比无约束项目更稳。

## 2. 指标集改造：从语音 Agent 指标到本项目指标

GEPA 原场景是语音 Agent（ASR 字准、对话延迟等联合优化）。本项目的帕累托目标集应换成：

| 编号 | 指标          | 方向 | 判分方式                                                      |
| -- | ----------- | -- | --------------------------------------------------------- |
| M1 | 引用忠实度       | ↑  | judge 检查 answer 论断是否被 `refs` 片段真正支撑（无依据即扣分）               |
| M2 | 拒答正确率       | ↑  | 检索 LOW 档时必须拒答的场景集（`grounding_note` 已有分级语义可直接圈样本）          |
| M3 | 工具路由正确率     | ↑  | 该 web_search 没调 / 不该 calculator 却调了 / 失败工具重复调用（准则第 6 条违例） |
| M4 | 回答质量        | ↑  | LLM-as-judge 打分，并用历史 downvote 做校准锚                        |
| M5 | 单轮 token 成本 | ↓  | 已有 quota / 用量统计口径                                         |
| M6 | 端到端延迟 p95   | ↓  | 在线埋点回放                                                    |

**帕累托择优规则**：M1–M4 越大越好，M5/M6 越小越好。候选进入非支配前沿后，按以下 tie-break 排序：

1. **最小 diff 优先**——与当前 active 提示词改动行数最少者优先。这是本项目的特殊约束：diff 越大 KV 前缀缓存失效越狠、引入回归面越大（直接衔接 P0-1 定位片段不进前缀的结论）。
2. 前沿内拥挤度（避免扎堆在同一维度）。
3. 不接受的候选：任何「单项大涨、其余全跌」的候选（成本调整后仍 dominated 的一律拒），这正是「避免单点优化引发指标对抗」在本项目落地口径。

## 3. 架构：六层闭环

```
① 采集 collector ──► ② 变异 mutator ──► ③ 评估 evaluator ──► ④ 择优 selector ──► ⑤ 存储 prompt_store
        ▲                                                                      │
        └──────────────── ⑥ worker prompt.evolve job（cron 小时级） ◄──────────┘
                                                        候选 shadow → 人工 promote → active
```

**① 采集层（Badcase 集构建）**  
从 `feedbacks JOIN messages JOIN conversations` 拉取 rating='down' 的样本，附上：问题、answer、refs、当时工具路径、场景 id。失败模式自动打标（检索弱硬凑 / 工具选错 / 该拒答没拒 / 常识错误 / 格式违规），标签即 mutator 的归因输入。按模式分层抽样，每轮进化用固定 seed 保证回放集可比。**必须混入 up 样本**：只学拒答会让模型变成「什么都不答」的对抗解。

**② 变异层（Reflective Mutation）**  
输入 = 失败模式标签 + 典型样本 N 条 + 当前提示词 + 槽位定义；输出 = 结构化 diff（JSON：`{slot, action: add|remove|modify, target_section, new_text, rationale}`）。  
硬护栏（结构校验，不信任 LLM 自觉）：

- 只允许变异「行为准则」区域与场景片段；系统角色首句、工具定义、`kb_id` 服务端注入相关表述**不可增删**——防提示词被进化到越权入口上（呼应 build_agent 里 kb_id 不进提示词的安全设计）。
- diff 行数上限（建议 ≤6 行），保证 KV 前缀稳定与回归面可控。
- 「工具返回错误时如实解释，绝不编造」这类安全条目列入变异黑名单。

**③ 评估层（回放 + 多指标判分）**  
固定回放集 = 历史 badcase + 分层采样的 up case（版本化存库，逐轮只增不任意改，改则升集版本号）。每条候选在回放集上重跑两条路径（Agent / RAG），judge 用独立 LLM（temperature=0，与被评提示词不同 prompt 模板，防「优化判分器」）。

**④ 择优层**：见 §2 帕累托规则。

**⑤ 存储层（新表，`core/session.py` 照现有 ALTER 补列模式建）**

```sql
prompt_variants(
  id, path TEXT,          -- 'agent' | 'rag'
  version INTEGER, parent_id INTEGER,
  diff_json TEXT,         -- 变异 diff（可读可审计）
  prompt_text TEXT,       -- 全量提示词快照
  metrics_json TEXT,      -- {M1..M6, pareto: bool, dominated_by: id?}
  status TEXT,            -- candidate | shadow | active | retired
  created_by TEXT,        -- 'evolve:<run_id>' | 'manual:<uid>'
  created_at INTEGER
)
prompt_evals(
  id, variant_id, replay_set_version,
  per_case_json TEXT,     -- 每条 case 的判分明细（可复盘）
  summary_json TEXT, elapsed_ms, created_at
)
```

**⑥ 调度层**：`core/scheduler.py::JOB_DEFS` 加 `prompt.evolve`（建议 `*/30 * * * *` 或每日 4 次，先手动 `--once prompt.evolve` 验证），复用现成 claim 幂等 / 重试分级 / 结构化日志，不自己写第二套。

## 4. 与现有代码的接线点

| 文件                                          | 改动                                                                                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `agent/builder.py`                          | `SYSTEM_PROMPT` 改为 `prompt_store.active('agent') or SYSTEM_PROMPT`；**`_AGENT_CACHE` 键必须加 prompt version**，否则改了提示词缓存不失效——这是最易踩的坑 |
| `kb/rag_core.py`                            | `grounding_note` 模板同样版本化（P0 修复刚把它变成双路径同源，进化时保持同源）                                                                               |
| `core/session.py`                           | 两张新表（迁移照 compressed_summary 三列的补列模式）                                                                                            |
| `worker/scheduler.py` + `core/scheduler.py` | 注册 `prompt.evolve` job；业务逻辑放 `service/prompt_evo.py`，遵循「不自己写 job_runs」约定                                                        |
| `api/prompt_evo.py`（新）                      | 候选列表 / 指标对比 / promote / rollback / 手动触发一轮；默认要登录，promote 建议加管理员判定                                                                |
| `tests/`                                    | 新增 `test_prompt_evo.py`：护栏校验（黑名单条目删不掉）、缓存键含 version、promote 后 build_agent 生效、回滚生效                                               |

## 5. 安全与防退化红线（本项目特有）

1. **变异护栏用结构校验落地**：解析提示词分节，越界 diff 直接拒，不指望模型自觉。
2. **回归闸门**：229 个 pytest + 脚本式自测全绿才允许 promote；`tests/test_agent_grounding.py` 是范式——prompt 类变更也要有测试兜底。
3. **默认人工 promote**：候选只进 shadow；自动 promote 做成配置开关且默认关（与 CODEOWNERS / PR 文化一致）。
4. **回滚**：retired 版本保留全量快照，一键回退；上线后指标劣化可自动回退（劣化阈值写进 promote 记录）。
5. **隐私**：回放集只存脱敏问答对，comment 原文不进提示词变异上下文（防把用户个人信息泄进 system prompt）。

## 6. 分期与工作量

| 阶段 | 内容                                                                       | 周期    |
| -- | ------------------------------------------------------------------------ | ----- |
| P0 | prompt 版本化（表 + builder 读取 + 缓存键 + fallback + 测试）。不接进化，先把提示词变成「数据」        | 1–2 天 |
| P1 | collector + evaluator：badcase 导出与聚类、回放集、judge 多指标。先「能测量」                 | 3–5 天 |
| P2 | mutator + selector + `prompt.evolve` job + shadow/promote API。闭环成形，达成小时级 | 3–5 天 |
| P3 | 真实流量灰度、judge 与人工 downvote 的一致性校准、自动 promote 策略                           | 持续    |

## 7. 一句话回答「能不能加入」

能。本项目已有 badcase 数据（feedbacks）、小时级调度（worker）、双路径变异靶点（Agent/RAG 提示词）三块关键积木；缺的是「提示词版本化 + 离线评估」这一课，补上即可。GEPA 原场景的语音指标需替换为 §2 的 M1–M6，并用「最小 diff + 帕累托前沿」双约束把自进化嵌进已建立的 KV 缓存纪律里。
