# Prompt 自进化（GEPA 式）落地报告

> 2026-10-20 · 对应 issue #12 · 方案原文 `docs/规划/Prompt自进化GEPA方案_2026-10-17.md`
> 决策口径（用户 2026-10-20 拍板）：**回放集 200~300 例** · **M1–M4 的 95% 置信区间下界 ≥ -5pp 才允许晋升**

---

## 一、先说结论

方案里的 P0 / P1 / P2 三个阶段全部落地，闭环可跑：

```
feedbacks ──► collector ──► 回放集(200~300, 版本化) ──► mutator(结构化 diff)
                                                              │
                      prompt_store ◄── selector ◄── evaluator ◄┘
                    (candidate→shadow→active)
```

- **提示词已经是"数据"**：`agent/builder.py` 与 `kb/rag_core.py` 都从 `core/prompt_store` 取 active 版本，没有 active 时退回代码常量（version=0）。
- **候选默认不上线**：只到 `shadow`；`PROMPT_EVO_AUTO_PROMOTE` 默认关，promote 走人工 + 管理员。
- **统计判定而不是拍脑袋**：四个质量指标各自做成对 bootstrap，95% CI 下界 ≥ -5pp 才准进前沿；成本 / p95 延迟另设放大倍数上限。

本期**不含** hybrid 路径（方案 §待讨论已明确），也不做真实流量灰度（P3）。

## 二、拍板参数怎么落进代码

| 决策 | 落地位置 | 取值 |
|---|---|---|
| 回放集 200~300 例 | `PROMPT_EVO_REPLAY_MIN` / `PROMPT_EVO_REPLAY_MAX` | 200 / 300 |
| 不足就判定"样本不够" | `prompt_stats.gate_decision` | `< 200` → `insufficient_sample` |
| CI 下界 ≥ -5pp | `PROMPT_EVO_MAX_DROP` + `paired_bootstrap_diff` | `0.05`，α=0.05，2000 次重采样 |
| 成本 / 延迟不劣化 | `PROMPT_EVO_MAX_COST_RATIO` / `PROMPT_EVO_MAX_LATENCY_RATIO` | 1.15 / 1.20 |
| 只混入 up 样本防退化 | `PROMPT_EVO_REPLAY_UP_RATIO` | 0.3 |
| 改动面可控（KV 前缀） | `PROMPT_EVO_MAX_DIFF_LINES` + `prompt_guard` | 6 行 |

### 为什么是"下界"而不是"点估计"

点估计有一半概率落在真实值之上——等于有五成把握放行一个真实劣化 5pp 的候选。
取下界是要求"劣化到 5pp 这件事连统计误差都兜不住了才算过线"，
等价于「宁可少晋升，不放劣化上线」。这也是为什么 200 例是硬下限：
配对 bootstrap 在这个量级才能把 5pp 和 0 区分开（见 §五 的实测）。

### 为什么是"成对"bootstrap

基线与候选跑的是**同一批 case**。配对之后，"题目难度"这个最大的噪声源被消掉，
同样的样本量能测出更小的真实差异。两独立样本的做法在 200 例下几乎测不出 5pp。

## 三、代码结构

| 层 | 文件 | 职责 |
|---|---|---|
| ⑤ 存储 | `core/prompt_store.py`（新） | 版本快照 / active 解析（带 TTL 缓存）/ promote / rollback / 评估留档 |
| ② 护栏 | `core/prompt_guard.py`（新） | 结构化 diff 落地 + 四道闸门（黑名单、越权词、≤6 行、长度不塌） |
| ③ 统计 | `core/prompt_stats.py`（新） | 成对 bootstrap、M1–M4 闸门、M5/M6 倍数护栏、summary |
| ①②③④ | `service/prompt_evo.py`（新） | 采集 / 失败模式打标 / 回放集 / 评估 / 变异 / 帕累托择优 / 一轮闭环 |
| ⑥ 调度 | `core/scheduler.py` + `worker/scheduler.py` | 注册 `prompt.evolve`（默认 `20 */6 * * *`，即每日 4 次） |
| API | `api/prompt_evo.py`（新） | status / variants / replay-sets / run / promote / rollback / config |
| 接线 | `agent/builder.py`、`kb/rag_core.py`、`core/session.py`、`main.py` | 读 active + 缓存键含版本 + 两张新表 + 路由挂载 |
| 测试 | `tests/test_prompt_evo.py`（新） | 31 条，见 §五 |

### 三个必须记住的设计决定

**1. version=0 = 内置基线，不落库。**
数据库清空、换环境、迁移时行为自动退回代码常量，而不是"没有提示词可用"。
副作用是所有 `prompt_evals` 都允许 `variant_id=0`（基线评估也留档，
否则"相对谁下降"事后不可查）。

**2. `_AGENT_CACHE` 的键必须含 prompt version。**
编译好的 LangGraph 图把 system prompt 固化了。键里不带版本，
promote 之后照样命中旧图——库里版本变了、线上一个字没变，重启才恢复。
`tests/test_prompt_evo.py::test_builder_cache_key_includes_prompt_version` 专门盯这条。

**3. 没有 active 时必须原样直通（不能用窗口截断替代）。**
`resolve()` 在无 active / 开关关闭 / DB 异常三种情况下都返回 `(default, 0)`，
所以进化关掉时行为与改造前**逐字一致**，`PROMPT_EVO_ENABLED=0` 就是一键回滚。

## 四、安全与防退化红线怎么落地

| 方案要求 | 实现 |
|---|---|
| 变异护栏结构校验，越界直接拒 | `prompt_guard.apply_diff`：黑名单条目在候选中必须原样存在；`kb_id` / 「忽略上文」出现在新增文本一律拒；add 只能落在「行为准则：」之后（角色首句 + 工具定义不可动） |
| 改动行数 ≤6 | `changed_lines = add + remove + modify` 行数之和，超限拒绝（同时是择优 tie-break 的输入） |
| 回归闸门 | 脚本式自测 + pytest 全绿才提交；promote 接口拒绝 `rejected` 候选（409） |
| 默认人工 promote | `PROMPT_EVO_AUTO_PROMOTE=0`；写操作全部要管理员 |
| 回滚 | `retired` 保留全量快照；`POST /rollback` 支持回上一版 / 指定版本 / 内置基线 |
| 隐私 | 用户 comment **不进任何提示词**，只用来打失败模式标签；问答文本过 `_redact()`（邮箱 / 手机号 / 身份证 / URL query 打码）+ 800 字截断 |
| KV 缓存纪律 | 最小 diff + 动态内容不进 system 前缀（定位仍在末尾追加，本次没动） |

## 五、验证

```
python -m pytest tests/test_prompt_evo.py -q     → 31 passed
python -m pytest -q                              → 335 passed, 1 failed（非本次改动，见 §七）
```

31 条覆盖九组：版本化（4）、缓存键（2）、结构护栏（8）、统计闸门（6）、
择优（3）、采集与回放集（3）、闭环端到端（2）、worker 注册（1）、
外加"模板锚点必须命中真实基线"1 条——后者是关键：模板写了不存在的锚点，
线上就是永远 `no_candidate`，指标全绿但功能从不生效。

**闭环端到端**用 stub runner/judge 跑真实链路（采集 → 回放集 → 基线评估 → 变异 →
候选评估 → 闸门 → shadow），并断言"默认不改变 `resolve()` 的结果"——
防止某次重构把 auto_promote 的默认值改成 True。

## 六、运维手册

```bash
# 手工跑一轮（不依赖 worker）
python -m worker.scheduler --once prompt.evolve

# 看候选与指标
curl -H "X-Auth-Token: <token>" http://127.0.0.1:8000/api/prompt-evo/status
curl -H "X-Auth-Token: <token>" "http://127.0.0.1:8000/api/prompt-evo/variants?path=agent&status=shadow"

# 晋升 / 回滚（管理员）
curl -X POST -H "X-Auth-Token: <admin>" http://127.0.0.1:8000/api/prompt-evo/variants/7/promote
curl -X POST -H "X-Auth-Token: <admin>" -H "Content-Type: application/json" \
     -d '{"path":"agent"}' http://127.0.0.1:8000/api/prompt-evo/rollback

# 一键关掉自进化（行为立刻退回硬编码提示词）
PROMPT_EVO_ENABLED=0
```

**成本**：一轮 = 回放集条数 ×（基线 + 候选）次生成 + 判分。默认
`PROMPT_EVO_RUN_MAX_CASES=200`、`PROMPT_EVO_CANDIDATES=2`，因此单轮上限约
`200 × 3 = 600` 次调用；基线指标对同一回放集版本**复用缓存**，稳定后回落到
`200 × 2 = 400`。`due_subjects` 一次只返回一条 path（按"最久没评估"轮转），
避免一轮把三条 path × 200 例全跑一遍。

## 七、遗留与已知问题

1. **P3 未做**：真实流量灰度、judge 与人工 downvote 的一致性校准、自动 promote 策略。
   当前 `PROMPT_EVO_JUDGE` 默认 `heuristic`（确定性、零成本），生产建议切 `llm`，
   但要先做一致性校准——否则"判分器和用户"两套标准，进化方向会漂。
2. **M3（工具路由）默认不参与统计**：默认执行器不含工具循环，`expect_tools` 为空的
   样本一律记 `None`。要真正评 M3 必须注入带工具的 runner（本期留了注入点）。
3. **hybrid 路径不在进化范围内**（方案已定）。
4. **工作区并行改动**：本轮提交时另有一路会话在改 `api/chat.py`、`core/scheduler.py`、
   `kb/rag_core.py`、`main.py`（审计 A1–A7：截断检测 / 限流 / 时区 / 指标 / SSE 断连），
   全量 pytest 的 1 条失败来自那边的 `done` 帧新增 `truncated` 字段
   （`tests/test_baseline_chat_stream.py` 的字段集断言）。与本次改动无因果关系。
