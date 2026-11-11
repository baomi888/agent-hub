# 提醒中心 AI 整理 v13 · WorkBuddy 交接单

> 交接日期：2026-11-10
> 设计状态：已定稿（v13 工具型；v12 只保留为历史）
> 代码状态：业务代码尚未接入，本目录只含设计稿与规格
> 目标：在 `/reminders` 落地“工具型 AI 整理”，用户说/输一段话，系统拆成待办与提醒，用户确认后才写库。

**当前视觉基线是 v13，不是 v12。**
凡是“聊天式、AI 头像、问候气泡、第一人称旁白”的旧描述，都以
`去AI味与引导性验收.md` 和 `prototype.html`（v13）为准。

---

## 0. 直接发给 WorkBuddy 的启动话

把下面这段整段发给 WorkBuddy：

```text
先读 docs/design/提醒中心_AI整理_v12/README.md 全文，再按 §1 的顺序读代码。

任务：在 /reminders 落地工具型 AI 整理 v13，不是聊天机器人。
流程：底部一句话输入 → AI 只生成 proposal → 用户逐条确认、改时间 →
确认后才写 tasks / reminders。AI 不能直接写库。

必须遵守：
1. 先 git status --short；不要覆盖 reminders/page.tsx、console.css 里未提交的改动。
2. 先读 AGENTS.md、docs/知穗_UI设计规范_v0.1.md、docs/待办与提醒_Phase1实施单_2026-11-10.md。
3. UI 以 docs/提醒中心UI对标与优化方案_2026-11-10.md §19 为准；
   docs/design/提醒中心_AI整理_v12/prototype.html 只做视觉参考，不直接复制它的静态数据逻辑。
4. 不新建 ai_tasks / ai_reminders；只写现有 tasks / reminders。
5. due_at 与 remind_at 分开；只设 due_at 不会响，UI 必须写出来。
6. 待办 ○ 可完成；联动提醒 ◇ 不可完成，只能推迟/暂停/关来源。
7. 不删课表、番茄钟、委托、打卡、四六级、单词、DDL、系统这些联动来源。
8. 不做假撤销；后端没有 restore 就不做“撤销”。
9. 不假装语音识别；后端没有 STT 前，语音入口只能禁用/标注“即将支持”，不能 setTimeout 假装转写。
10. 改完跑 npx tsc --noEmit；给出实际输出；截图 390/1440；负向断言逐条给结果。
11. 先读 docs/design/提醒中心_AI整理_v12/去AI味与引导性验收.md；
    P0 未过，不允许进入 Phase C（确认写库）。

分阶段做：
Phase A：前端壳 + 本地 fixture（先看效果，不接网）
Phase B：后端 POST /api/capture/parse（LLM 只返回 proposal）
Phase C：确认写库（复用 api.tasks.create / api.reminders.create）+ 失败处理
Phase D：语音（只有确定 STT 供应商后才做）

每阶段结束后停下来汇报：改了哪些文件、命令输出、截图路径、还差什么。
```

---

## 1. 先读什么（按顺序）

| 顺序 | 文件 | 读哪几节 | 为什么 |
|---|---|---|---|
| 1 | `AGENTS.md` | 全部，重点 §2 / §3 / §9 | 本机环境、前端约定、验证方法论 |
| 2 | `docs/知穗_UI设计规范_v0.1.md` | 全部 | 暖米黄 + 深棕墨 + 玉米金；禁止项 |
| 3 | `docs/提醒中心UI对标与优化方案_2026-11-10.md` | §13、§16、§17、§19 | 产品边界、v9 可信感、v10 历史、v13 工具型整理、去 AI 味与引导性 |
| 4 | `docs/待办与提醒_Phase1实施单_2026-11-10.md` | §2、§3、§5、§6、§8、§10、§11 | 两类对象、表结构、API、前端接入、测试、完成定义 |
| 5 | `docs/design/提醒中心_AI整理_v12/视觉基线_v13.md` | 全部 | 当前实现依据：结构、状态、文案、实测 |
| 6 | `docs/design/提醒中心_AI整理_v12/prototype.html` | 交互与样式 | 当前是 v13；只做视觉参考，静态样例数据不要照搬 |
| 7 | `docs/design/提醒中心_AI整理_v12/去AI味与引导性验收.md` | 全部 | 去人格化、状态清晰、注意力路由、错误出口、评分表 |

看图顺序：

1. `shots/01-input-mobile.png`：输入态，输入固定在底部；
2. `shots/02-processing.png`：发送后出现“你说的”引用卡 + `正在整理…`；
3. `shots/03-result-mobile.png`：结果态，原话回看 + 逐条确认 + 汇总条；
4. `shots/04-input-360.png`：360px 窄屏无横向溢出；
5. `shots/05-input-desktop.png`：桌面 520px 抽屉。

---

## 2. 现在代码在哪

| 层 | 文件 | 现状 | 这次要做什么 |
|---|---|---|---|
| 页面 | `frontend/src/app/(console)/reminders/page.tsx` | 已有提醒/待办列表、快速添加、TaskDrawer、ReminderDrawer、时间轴、倒计时 | 加 AI 整理入口和抽屉挂载；不要重写整页 |
| 确定性解析 | `frontend/src/components/reminders/quickParse.ts` | 已有“一句话快速添加”，不接模型 | 保留；AI 整理不要替换它 |
| 前端类型 | `frontend/src/lib/types.ts` | `Task` / `TaskDraft` / `ReminderItem` / `ReminderDraft` 已存在 | 增加 CaptureProposal 类型 |
| 前端 API | `frontend/src/lib/api.ts` | `api.tasks` / `api.reminders` 已存在 | 增加 `api.capture.parse` |
| 待办后端 | `api/tasks.py`、`core/task_core.py` | CRUD + linked reminder 已上线 | 不改核心逻辑；确认时复用 |
| 提醒后端 | `api/reminders.py`、`core/reminder_core.py` | 通用提醒、重复规则、fire_at 已存在 | 补 `fire_at` 入参（见 §3.3） |
| LLM | `core/llm.py` | `get_llm()` 已封装 DeepSeek | 解析接口用它 |
| 并发闸 | `core/llm_gate.py` | `slot()` / `Busy` 已存在 | 解析接口必须过闸 |
| 样式 | `frontend/src/app/styles/tokens.css`、`console.css`、`a11y.css` | 提醒页 `rem-*` 样式从 console.css 约 1673 行开始 | 新增 `rem-cap-*`，只用 var(--...) |

关键现状：

- 当前页面已经有“快速添加”输入框，提交后直接 `api.tasks.create`。它是确定性解析，不是 AI。
- 当前没有 AI 解析端点，也没有 `ai_tasks` / `ai_reminders` 表。
- `Task` 的 `recurrence` 在 Phase 1 只允许 `"none"`；重复待办还没开放。
- `Reminder` 支持 `once / daily / weekdays / weekly / interval`，但没有完成语义。

---

## 3. 产品边界（先看，避免做错对象）

### 3.1 只允许两类对象

| 类型 | 数据 | UI | 能否完成 | 适合什么 |
|---|---|---|---|---|
| 待办 task | `tasks` | `○` | 能 | 作业、论文、报名、买东西 |
| 提醒 reminder | `reminders` | `◇` | 不能 | 开会、上课、每天背单词、每天复盘 |

AI 返回的每一条 proposal 必须明确 `kind: "task" | "reminder"`。前端不能把两类混成一张卡。

### 3.2 不要照搬效果图里的“每天 + 勾选”

v12 效果图里“背单词 · Unit 7 / 每天 / ○”是为了展示视觉，**Phase 1 后端不支持重复待办**：

- `Task.recurrence` 只允许 `"none"`；
- 想要“每天 20:00 背单词”，应生成 `kind: "reminder"`、`freq: "daily"`、`at: "20:00"`，UI 用 `◇`，没有 checkbox；
- 想要“今晚 20:00 背单词”，生成 `kind: "task"`、`remind_at` 为今晚 20:00，UI 用 `○`；
- 二选一，不要生成“可勾选 + 每天”的组合。

### 3.3 一次性事件需要 `fire_at`

`reminders` 表已有 `fire_at`，`reminder_core` 也支持 `once + fire_at`，但 `api/reminders.py` 的 `ReminderIn` / `ReminderPatch` 还没暴露这个字段。

AI 整理要支持“下周五 18:00 开会”这种**指定日期的一次性提醒**，必须先补：

- `api/reminders.py`：`ReminderIn` 和 `ReminderPatch` 增加 `fire_at: int | None`；
- `frontend/src/lib/types.ts`：`ReminderDraft` / `ReminderPatchBody` 增加 `fire_at?: number | null`；
- 校验：`fire_at` 只允许和 `freq="once"` 一起用；过去时间拒绝；非 once 传了就 400。

如果不补这个字段，就只能做“每天/每周固定时刻”，做不了“下周五 18:00 只响一次”。不要在 AI 层伪造日期。

### 3.4 统一写现有表

- 待办：`POST /api/tasks`，字段 `title / note / list_key / priority / due_at / remind_at`。
- 提醒：`POST /api/reminders`，字段 `title / freq / at / weekday / every_minutes / fire_at / source:"manual"`。
- 不新建 `ai_tasks`、`ai_reminders`、`capture_items` 等表。
- 不直接写 `reminders` 里 `source="task"` 的 linked reminder；待办提醒只能通过 `/api/tasks` 改。

### 3.5 联动来源一个都不能删

课表、番茄钟、委托、打卡、四六级、单词、DDL、系统每日回顾都保留。

- 联动提醒没有 checkbox；
- 只能推迟 / 暂停 / 关来源；
- “新建提醒”仍然是次入口，不和“新建待办”抢主按钮。

### 3.6 AI 只能 propose

- `/api/capture/parse` 只返回 proposal，不写库；
- 用户勾选并点“加入 N 条”后才写；
- 用户全不选时按钮 disabled；
- 不做“AI 自动加入”。

---

## 4. 前端怎么做

### 4.1 入口

在 `reminders/page.tsx` 现有 `.rem-quick` 快速添加区域加一个**可见文字**的次入口：

- 桌面：输入框 + `添加` + `AI 整理`（带 spark，但不要只做图标）；
- 手机：`AI 整理` 单独一行或与 `添加` 并排，触控高度 ≥44；
- 不要替换原来的快速添加；
- 入口文案用 `一句话添加`：说清用户要做什么，不用“AI 整理”“智能助手”这类把能力当卖点的词。

### 4.2 组件拆分

建议：

```text
frontend/src/components/reminders/AiCaptureDrawer.tsx   新抽屉
frontend/src/components/reminders/captureTypes.ts       前端 proposal 类型（或并入 lib/types.ts）
frontend/src/components/reminders/useDialog.ts          可选：把 page.tsx 里的 useDialog 抽出来复用
```

如果不想动 `useDialog`，可以在新组件里复制一份焦点陷阱，但要在交付说明里写“后续合并”。不要为了复用去大改已经稳定的 TaskDrawer / ReminderDrawer。

### 4.3 结构

严格按 v13 三层（`prototype.html` 就是 v13；不要回退到 v12 的聊天语气）：

```text
抽屉头：整理成待办和提醒 / 结果态副标题 / 关闭（不再挂三步步骤条）
整理区：说明 + 具体示例 + 你说的引用卡 + 正在整理
底部 dock：示例句 + 信任说明 + 麦克风 + 输入框 + 发送 + 按住提示
```

结果态：

```text
抽屉头：确认这 N 条 / 2 条待办、2 个日程 / 2 条需要补充信息
结果体：你说的（原话高亮 + 编号）+ 汇总条 + 先处理 N 条需确认 + 逐条 proposal
底栏：已选 N 条 · M 条会提醒你 / 全不选 / 加入 N 条，并设 M 条提醒
```

### 4.4 状态机

```text
closed
  → open(input)
  → submitting(parse)
  → result(proposals)
  → committing
  → closed + toast + reload

input:
  - 文字 → Enter / 发送 → submitting
  - 示例句 → 只填入，不提交
  - 麦克风 → 只有真实 STT 才进入录音；否则禁用

result:
  - 逐条勾选 / 全选 / 全不选
  - 改时间 / 改清单 / 改优先级
  - 追问选项
  - 加入 N 条 → committing
  - 重新说 → input（清空）
  - 改文字 → input（回填原话）
```

### 4.5 输入与结果必须真实对应

效果图里“输入一段、结果固定 4 条”只是演示。真实实现：

- `parse` 返回几条，结果就渲染几条；
- 每条 proposal 带 `source_text` / `source_start` / `source_end`；
- 点编号时，只能高亮原文里对应的那一段；
- 如果后端找不到 offset，就不显示编号高亮，但条目仍保留；
- 不允许前端自己编造“第 2 条对应原话”。

### 4.6 确认写库

MVP 不做新的批量写接口，前端按 proposal 逐条调用现有 API：

- `kind === "task"` → `api.tasks.create(...)`
- `kind === "reminder"` → `api.reminders.create(...)`

要求：

- 点击后立刻 `busy`，防双击重复写；
- 用 `Promise.allSettled` 或顺序执行；
- 成功的条目从列表移除，失败的条目保留并标红；
- 完成后 `reload()`；
- toast 必须说清楚“加入了几条、几条没成功”；
- 不做假撤销；失败就让用户重试或手动删。

如果产品坚持“要么全成功、要么全不写”，再单独加 `POST /api/capture/commit`，不要在前端假装原子性。

### 4.7 样式

- 只读 `frontend/src/app/styles/tokens.css`，不新增颜色；
- 新样式统一 `rem-cap-*` 前缀，放在 `console.css` 提醒页区块后面；
- 主按钮深棕墨底，hover 变金；浅底金字用 `--accent-ink`；
- 禁止荧光黄、粗黑描边、直角、大面积深色、玻璃拟态、emoji、图标库；
- 手机输入框 `font-size: 16px`；
- `prefers-reduced-motion` 下关掉三点跳动和结果淡入；
- 抽屉 `role="dialog" aria-modal="true"`，Esc 关、焦点陷阱、关闭后焦点归还。

### 4.8 语音

当前后端没有 STT（全仓库没有 whisper / 语音转写接口）。所以：

- **Phase A/B/C 先做文字**；
- 麦克风按钮可以保留，但必须 `disabled` 或点后提示“语音输入即将支持”；
- 不能用 `setTimeout` 假装“正在听”然后直接出结果；
- Phase D 接入真实 STT 后，补：录音中、转写中、失败、取消、权限拒绝五种状态；
- 录音上传接口单独设计，不要把音频塞进 `/api/capture/parse`。

---

## 5. 后端怎么做

### 5.1 新文件

```text
api/capture.py             HTTP 端点
core/capture_parse.py      prompt、LLM 调用、JSON 校验、offset 计算
tests/test_capture_parse.py
```

`main.py` 注册：

```python
from api.capture import router as capture_router
app.include_router(capture_router, prefix="/api/capture", tags=["AI 整理"])
```

### 5.2 端点

```text
POST /api/capture/parse
```

无尾斜杠；挂 `Depends(get_current_user)`；`owner` 只从当前用户取，不从 body 取。

请求：

```json
{
  "text": "明天高数作业要交，周五社团开会，今晚还得背单词，下周那个课程论文也得弄",
  "now": 1762764000
}
```

`now` 可选；不传就用服务器当前时间。不要相信客户端传的“明天”。

响应：

```json
{
  "items": [
    {
      "id": "p1",
      "kind": "task",
      "title": "交高数作业",
      "note": null,
      "list_key": "homework",
      "priority": 0,
      "due_at": 1762852799,
      "remind_at": null,
      "recurrence": null,
      "source_text": "明天高数作业要交",
      "source_start": 0,
      "source_end": 8,
      "needs_confirm": false,
      "questions": []
    },
    {
      "id": "p2",
      "kind": "reminder",
      "title": "社团开会",
      "note": null,
      "list_key": null,
      "priority": 0,
      "due_at": null,
      "remind_at": null,
      "recurrence": {
        "freq": "once",
        "fire_at": 1763032800
      },
      "source_text": "周五社团开会",
      "source_start": 9,
      "source_end": 15,
      "needs_confirm": true,
      "questions": [
        {
          "id": "q1",
          "text": "是这周五，还是以后每周五？",
          "options": [
            { "label": "这周五", "value": "once" },
            { "label": "每周五", "value": "weekly" }
          ]
        }
      ]
    }
  ],
  "unresolved": [],
  "model": "deepseek-chat",
  "latency_ms": 1830
}
```

字段规则：

- `kind` 只能是 `task` / `reminder`；
- `title` ≤ 60 字（reminder 上限 60，task 上限 120；统一 60 最安全）；
- `list_key` 只在 task 上有值，枚举 `inbox/course/homework/club/life/exam`；
- `priority` 0/1/2；
- `due_at` / `remind_at` / `fire_at` 一律 Unix 秒；
- `recurrence` 对 task 必须为 null；对 reminder 是 `{freq, at?, weekday?, every_minutes?, fire_at?}`；
- `source_text` 必须是原文的连续子串；`source_start/end` 由服务端用 `text.find()` 算，不让模型直接给 offset；
- 找不到原文时 `source_start/source_end` 为 null，前端不高亮，但条目保留；
- `needs_confirm` 为 true 时必须给至少一个 question；
- 最多 12 条；超过就截断并在 `unresolved` 里说明。

### 5.3 Prompt 规则

系统提示必须写死：

1. 只输出 JSON，不解释、不 markdown；
2. 按服务器给的当前时间、星期、时区算“明天/周五/下周”；
3. 每个条目必须引用原文连续片段 `source_text`，不能改写原话；
4. 不确定的时间不要猜：`due_at` / `remind_at` / `fire_at` 填 null，`needs_confirm` 为 true；
5. 只有用户明确说“提醒我 / 叫我 / 记得”时才填 `remind_at`；不要把 `due_at` 自动复制成 `remind_at`；
6. “每天/每周/工作日”这类周期放 `recurrence`，不要塞进 `due_at`；
7. 事件/会议/上课用 `reminder`；有完成动作的作业/论文/报名用 `task`；
8. 不要把一句话拆成多个同义条目；也不要合并两件不同的事；
9. 不做数据库操作，不输出 SQL，不输出用户 id；
10. 输出必须能被 Pydantic 校验；字段缺失按 null 处理，但 `kind/title/source_text` 必须有。

建议 `temperature=0`，`max_tokens=1200`，输入上限 2000 字。

### 5.4 LLM 调用

用 `core.llm.get_llm(temperature=0, max_tokens=1200)`；异步端点里用 `await llm.ainvoke(...)`，不要阻塞事件循环。

必须过 `core.llm_gate.slot()`：

```python
try:
    async with llm_gate.slot():
        resp = await llm.ainvoke([...])
except llm_gate.Busy as exc:
    raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)})
```

JSON 解析失败：

- 先做一次轻量修复（去掉 ```json 包裹、取第一个 `{` 到最后一个 `}`）；
- 仍失败就返回 502 `AI 没整理出来，请再说一次`；
- 不要静默返回空数组，也不要在失败时创建任何 task/reminder。

### 5.5 校验

用 Pydantic model 校验 LLM 输出，不要手写一堆 `if`。至少校验：

- `kind` 枚举；
- `title` 长度；
- `list_key` 枚举；
- `priority` 0–2；
- 时间戳是正整数且在合理范围；
- task 的 `recurrence` 必须 null；
- reminder 的 `freq` 枚举，`at` 是 `HH:MM`，`weekday` 0–6，`every_minutes` 60–1440；
- `fire_at` 只允许 `freq="once"`；
- `source_start/end` 由服务端计算，不信任模型；
- 单条失败不要让整个响应 500：把它放进 `unresolved`，其余条目照常返回。

### 5.6 不写库

`/api/capture/parse` 只读 + 调模型，不写 `tasks` / `reminders` / `reminder_fires`。
确认写库由前端调用现有 `POST /api/tasks` / `POST /api/reminders` 完成。

---

## 6. 类型与 API 客户端

### 6.1 `frontend/src/lib/types.ts`

新增：

```ts
export type CaptureKind = "task" | "reminder";

export interface CaptureQuestion {
  id: string;
  text: string;
  options: { label: string; value: string }[];
}

export interface CaptureProposal {
  id: string;
  kind: CaptureKind;
  title: string;
  note: string | null;
  list_key: TaskListKey | null;
  priority: TaskPriority;
  due_at: number | null;
  remind_at: number | null;
  recurrence: {
    freq: ReminderFreq;
    at?: string | null;
    weekday?: number | null;
    every_minutes?: number | null;
    fire_at?: number | null;
  } | null;
  source_text: string;
  source_start: number | null;
  source_end: number | null;
  needs_confirm: boolean;
  questions: CaptureQuestion[];
}
```

### 6.2 `frontend/src/lib/api.ts`

新增：

```ts
capture: {
  parse: (text: string) =>
    request<{ items: CaptureProposal[]; unresolved: string[] }>(
      "/api/capture/parse",
      { method: "POST", body: JSON.stringify({ text }) }
    ),
},
```

### 6.3 `ReminderIn` / `ReminderPatch`

补 `fire_at` 后，前端 `ReminderDraft` / `ReminderPatchBody` 也要同步。
这是 AI 一次性事件能落地的前置条件。

---

## 7. 实施顺序

### Phase A：前端壳（1–2 天）

- 新 `AiCaptureDrawer`，用本地 fixture 走通输入 → 整理中 → 结果 → 勾选 → 关闭；
- 入口挂到 `/reminders`；
- 不接后端；
- 先交 11 个状态的状态板 + 自评分表；P0 未过不进 Phase B；
- 截图 390/1440；
- 对照 `prototype.html` 检查底部 composer、空态、结果态。

### Phase B：后端 parse（1–2 天）

- 新 `api/capture.py` + `core/capture_parse.py`；
- Pydantic 校验 + source offset；
- mock LLM 的测试；
- 前端把 fixture 换成 `api.capture.parse`；
- 失败态：429 / 502 / 0 条 / 部分 unresolved。

### Phase C：确认写库（1 天）

- 逐条 `api.tasks.create` / `api.reminders.create`；
- 补 `fire_at` 入参；
- 成功/失败/toast/reload；
- 防双击；
- 重开抽屉复位。

### Phase D：语音（单独立项）

- 先确定 STT 供应商和密钥；
- 录音上传 → 转写 → 同一 parse；
- 没有 STT 就不做，不要假动画。

---

## 8. 验收

### 8.1 正向

- 打开 `/reminders`，能看到 `一句话添加` 入口；
- 点开后是底部 composer，不是中央大麦克风；抽屉里没有 AI 头像和问候气泡；
- 输入一句话，Enter/发送后先出现“你说的”引用卡，再出现“正在整理…”，最后出现结果；
- 结果条数与后端返回一致；
- 点编号能高亮原文对应片段；
- 取消勾选，计数和按钮文案同步变；
- 点“加入 N 条”，成功写库，列表刷新，toast 说清楚几条会提醒；
- 360/390 无横向滚动，输入框字号 ≥16px；
- 桌面 1440 抽屉 520px，composer 贴底。

### 8.2 负向断言

- 打开抽屉：结果态和结果底栏必须不存在/隐藏；
- 结果只能由真实 parse 响应触发；不得用 setTimeout 伪造进度；
- 示例句只填入，不自动提交；
- 输入为空时发送必须 disabled，不能拿示例句偷偷提交；
- parse 失败：不创建任何 task/reminder，保留原文，给重试；
- parse 返回 0 条：显示“没听出安排”，不创建空待办；
- 全不选：主按钮 disabled，点击不能关闭抽屉；
- 双击“加入”：只写一次；
- 只设 due_at：不创建 linked reminder；
- 改 due_at：不能改已有 remind_at；
- 重复待办 proposal：必须降级为 reminder 或标记需确认，不能直接写 recurrence；
- 联动提醒：没有 checkbox，不能出现“完成”；
- 删除/关闭：不能删掉课表、番茄钟、委托、打卡、四六级、单词、DDL、系统；
- 语音未接 STT：不能显示“正在听”然后返回假结果；
- 401：按现有 `baomi:unauthorized` 处理，不能把用户留在空白抽屉。

### 8.3 命令

```bash
cd frontend
npx tsc --noEmit
```

后端（项目 venv）：

```bash
venv\Scripts\python.exe -m pytest tests/test_capture_parse.py -q
```

注意：本机 pytest 退出码可能被 safe-delete 吞掉，读 junitxml 的 `testsuites` 求和；没有输出就不算通过。

### 8.4 去 AI 味与引导性

按 `去AI味与引导性验收.md` §9 评分：

- 平均 ≥ 2.5；
- 任何一项不能是 0 或 1；
- 状态矩阵 11 行逐行有截图；
- P0 全部满足。

---

## 9. 不要做

- 不要新开 `/capture` 页面；先做在提醒中心里，稳定后再独立。
- 不要新建 `ai_tasks` / `ai_reminders`。
- 不要删除或替换 `quickParse.ts`。
- 不要用 AI 直接写库；必须用户确认。
- 不要把 `due_at` 和 `remind_at` 合成一个字段。
- 不要把“每天背单词”做成可勾选待办；Phase 1 没这个能力。
- 不要在前端假装 offset、编号、原话高亮；必须来自后端。
- 不要用 emoji、图标库、玻璃拟态、大面积深色。
- 不要做假撤销。
- 不要假装语音识别。
- 不要覆盖未提交的页面/样式改动；先 `git status --short`。
- 不要 `git add -A`；只加明确路径。

---

## 10. 交付时让 WorkBuddy 回什么

每个阶段结束，要求它回：

1. 改了哪些文件（精确路径）；
2. `npx tsc --noEmit` 的实际输出；
3. 后端测试命令 + junitxml 统计；
4. 390 / 1440 截图路径；
5. 负向断言逐条结果；
6. `去AI味与引导性验收.md` 的自评分表 + 11 个状态截图；
7. 没做的部分和原因（尤其是语音、批量原子性）。

没有这些，不算完成。
