// 与后端 api/schemas.py 对齐的前端类型定义

export interface Conversation {
  id: string;
  title: string;
  kb_id: string | null;
  created_at: number;
  updated_at: number;
}

// 知识库内的单个文件（后端 kb_files.json 落盘，重启不丢）
export interface KbFile {
  name: string;
  chunks: number | null;
  added_at: number | null;
  /** 这条资料怎么进库的：upload=用户上传，web=联网抓的 */
  origin?: "upload" | "web";
}

export interface KbInfo {
  kb_id: string;
  name: string;
  chunks: number;
  files: KbFile[];
}

export interface Message {
  id?: number;
  role: "user" | "assistant" | "system";
  content: string;
  refs?: string;
  created_at?: number;
}

// 聊天模式：auto 自动 / rag 强制检索 / agent 强制工具 / llm 纯对话 / hybrid 混合编排
//
// hybrid（10-14 加）：工作流与自主 Agent 在**一次任务内**动态换手。
// 它是唯一需要额外 SSE 事件（hybrid_start / workflow_node / mode_switch）才能正确
// 渲染的模式 —— 其余四种只靠 token + done 就够了。
//
// ⚠️ 后端开关 HYBRID_MODE_ENABLED=0 时它会**静默回退 agent**（不报错）：
// 表现为「收不到 hybrid_start」且「done.mode === 'agent'」。
// 所以前端不能假设选了 hybrid 就一定走混合，得看事件有没有来（见 useHybridTrace）。
export type ChatMode = "auto" | "rag" | "agent" | "llm" | "hybrid";

// 场景预设类型（ScenarioId）已随「场景选择撤出主界面」一并删除（10-14 晚）：
// 场景化入口统一走模板广场（/templates，完整启动包：建库 + 会话 + 提示词）。
// 后端 /api/chat 的 scenario 字段与 core/scenarios.py 机制保留（旧前端兼容 + 未来
// 模板联动可复用），前端不再传。

export interface SearchResult {
  title: string;
  body: string;
  href: string;
}

export interface Defaults {
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  supported_extensions: string[];
}

// 引用溯源条目（SSE sources 事件）：点角标可展开原文
export interface RefSource {
  id: number;
  source: string;
  page: number | null;
  // 余弦相似度（越大越相关）。后端换算不了时给 null，此时前端不显示这一项
  score: number | null;
  // 依据强度分级，由后端按 RETRIEVE_LOW_SIM 判定。
  // 只给数字的话用户看不出 0.31 算不算数，所以要和百分比一起显示。
  strength?: "high" | "medium" | "low" | null;
  preview: string;
  text: string;
}

// 依据强度（SSE grounding 事件）：weak=true 时回答上方挂「依据较弱」提示条
export interface Grounding {
  strength: "high" | "medium" | "low" | "none";
  /** top1 余弦相似度，换算不出来时为 null */
  best: number | null;
  count: number;
  /** 检索到了，但全部不够格当依据 */
  weak: boolean;
}

// 库内文件正文预览：origin=archive 读原始留档，chunks 表示老库从向量片段拼回
export interface FilePreview {
  name: string;
  origin: "archive" | "chunks";
  truncated: boolean;
  content: string;
}

// 工具调用（SSE tool 事件）
export interface ToolEvent {
  name: string;
  input?: string;
  output?: string;
}

// ---------- 登录 ----------
export interface QuotaInfo {
  used: number;
  /** 0 表示不限 */
  limit: number;
  /** 管理员标记：为 true 时该维度无配额约束 */
  unlimited?: boolean;
}

// 与后端 api/auth.py 的 UserOut / MeOut 对齐
export interface UserOut {
  id: string;
  username: string;
}

export interface MeOut {
  user: UserOut;
  quota: Record<string, QuotaInfo>;
}

// ---------- 用量与反馈看板（P2）----------
export interface UsageDay {
  /** YYYY-MM-DD */
  day: string;
  ask: number;
  embed: number;
}

export interface FeedbackItem {
  rating: "up" | "down";
  comment: string;
  title: string;
  sid: string;
  created_at: number;
}

export interface FeedbackStats {
  up: number;
  down: number;
  total: number;
  recent: FeedbackItem[];
}

export interface UsageSummary {
  quota: Record<string, QuotaInfo>;
  history: UsageDay[];
  /** 0 表示未启用每分钟限流 */
  rate_per_minute: number;
  feedback: FeedbackStats;
}

// ---------- 模板广场 ----------
export interface Template {
  id: string;
  name: string;
  category: string;
  /** 卡片图标语义 key（CornIcon 组件按知穗吉祥物风格渲染；历史 emoji 值回退 mini 知穗） */
  icon: string;
  tagline: string;
  description: string;
  tags: string[];
  suggested_kb_name?: string | null;
  session_title?: string;
  starter_prompt?: string | null;
  /** 点「设为每日委托」时预填的委托指令（持续型表述、不带频率词——频率由用户在表单里选）。
   *  目录里没有这个字段时前端回退到 tagline，所以后端没升级也不会空着。 */
  delegate_instruction?: string | null;
  recommended_mode?: string;
  philosophy?: string;
}

export interface ApplyTemplateResult {
  session_id: string;
  kb_id: string | null;
  title: string;
  starter_prompt?: string | null;
  recommended_mode?: string;
}

/* ============================================================
   以下四组类型对应「队友分支 feat/ai-module-20261013」新增的端点。
   ────────────────────────────────────────────────────────────
   ⚠️ 契约来源是 `docs/前端页面与接口映射_v0.2.md`（按分支代码反推的字段清单），
   不是联调结果。用 tryRequest 拉取，端点不存在时页面走「接口待上线」态，
   所以字段对不上也不会让页面崩掉；联调时逐项核对即可。
   ============================================================ */

// ---------- P4 每日委托 /api/delegate ----------

export type DelegateFreq = "daily" | "weekdays" | "weekly" | "interval";
export type DelegateKind = "study_plan" | "review" | "encourage" | "custom";

/**
 * 触发计划。注意**不是 cron**：后端用的是结构化字段，
 * 前端把它渲染成「每天 21:00」这种人话，而不是把 crontab 摊给用户看。
 */
export interface DelegateSchedule {
  freq: DelegateFreq;
  /** 触发时刻 HH:MM；interval 频率下可为空 */
  at?: string | null;
  /** weekly 用：0=周一 … 6=周日（与后端 ScheduleIn.weekday 对齐） */
  weekday?: number | null;
  /** interval 用：每多少分钟 */
  every_minutes?: number | null;
  tz: string;
}

export interface Delegate {
  id: string;
  /** 3–32 字，建后不可改 */
  name: string;
  kind: DelegateKind;
  schedule: DelegateSchedule;
  /** 投递目标会话；null = 默认会话 */
  session_id: string | null;
  session_title?: string | null;
  kb_id: string | null;
  kb_name?: string | null;
  /** 给模型看的生成指令 */
  instruction: string;
  tone: string;
  max_chars: number;
  /** 后端没有 status，只有这个布尔 */
  enabled: boolean;
  /** 暂停时为 null —— 页面上要能看出「它不会再来了」 */
  next_run_at: number | null;
  created_at?: number | null;
}

export type DelegateRunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface DelegateRun {
  run_id: string;
  job_key: string;
  subject_id?: string | null;
  status: DelegateRunStatus;
  planned_at: number | null;
  started_at?: number | null;
  finished_at?: number | null;
  error?: string | null;
  tokens_out?: number | null;
  /** 排查「今天为什么没收到」先看这一列 */
  trigger: "schedule" | "manual";
}

// ---------- P5 每日总结 /api/report ----------

export interface ReportStats {
  date: string;
  conversation_count: number;
  message_count: number;
  user_msg_count: number;
  assistant_msg_count: number;
  /** { 小时: 条数 }，确定性统计，不走模型 */
  active_hours: Record<string, number>;
  tool_call_count: number;
}

/** 一条结论 + 它必须指得回的真实消息 id（后端强制溯源，指不到的直接丢） */
export interface ReportConclusion {
  text: string;
  source_refs: number[];
}

export type ReportStatus = "pending" | "generating" | "ready" | "empty";

export interface DailyReport {
  report_id: string;
  date: string;
  status: ReportStatus;
  stats: ReportStats;
  content: ReportConclusion[];
  model?: string | null;
  generated_at?: number | null;
  read_at?: number | null;
}

// ---------- P6 打卡 /api/checkin ----------
//
// ⚠️ 后端打卡语义 = 「那天有没有生成每日总结（status='ready'）」，所以打卡数据
// 全部从 daily_reports 派生，不另造数据。下面两个类型对应 api/checkin.py。

/** GET /api/checkin/streak：连续天数 / 最长连续 / 已打卡日期集合 */
export interface CheckinStreak {
  /** 所有打卡日（升序） */
  dates: string[];
  /** 当前连续天数 */
  streak: number;
  /** 近 30 天内最长连续 */
  longest: number;
}

/** GET /api/checkin/history 单条：{date, points, shared} */
export interface CheckinHistoryItem {
  /** YYYY-MM-DD */
  date: string;
  /** 当天总结的结论条数 */
  points: number;
  /** 是否已分享（POST /api/checkin/share 标记） */
  shared: boolean;
}

// ---------- P7 设置：Agent 工具元数据 /api/agent/tools ----------

export interface AgentTool {
  name: string;
  /** read = 只读；write = 会改状态，页面上要用暖色区分 */
  category: "read" | "write" | string;
  /** 延迟下发（false = 每轮全量带给模型） */
  defer: boolean;
  retryable: boolean;
  needs_approval: boolean;
  max_output_chars: number;
  cost_hint: string;
}

// ---------- P7 设置：调度进程 /api/schedule/* ----------

/** 只看心跳年龄：>300s stalled、>600s down */
export type WorkerHealth = "up" | "stalled" | "down";

export interface ScheduleHealth {
  status: WorkerHealth;
  last_heartbeat: number | null;
  pid?: number | null;
  age_seconds?: number | null;
}

export interface ScheduleJob {
  job_key: string;
  cron?: string | null;
  enabled: boolean;
  last_run_at?: number | null;
  last_status?: string | null;
}
// ============ P6 混合架构（工作流 × 自主 Agent）============
// 契约来源：docs/前端对接接口文档_混合架构_2026-10-14.md（林宏亮 v1）
// ⚠️ 后端代码截至 10-14 尚未推到任何远端分支（hybrid/ 与 api/hybrid.py 均为 0 文件），
//    所以这一段是**照文档实现**，等后端上线后要按真实响应回归一次。

// ---------- SSE 事件 ----------

/** hybrid_start：起跑即发，mode 是**初始**模式（workflow / agent），不是最终模式 */
export interface HybridStartEvent {
  run_id: string;
  mode: HybridRuntimeMode;
}

/** 工作流节点类型（NodeDef.type） */
export type HybridNodeType = "llm" | "tool" | "agent" | "condition" | "human";

export interface WorkflowNodeEnter {
  phase: "enter";
  node_id: string;
  type: HybridNodeType;
  /** 展示名；后端保证空则回填为 node_id */
  name: string;
}

export interface WorkflowNodeExit {
  phase: "exit";
  node_id: string;
  status: "ok" | "failed";
  /** 失败跳转目标；null 才会进入切换规则判定 */
  on_fail?: string | null;
  error?: string | null;
}

export type WorkflowNodeEvent = WorkflowNodeEnter | WorkflowNodeExit;

/** 模式切换；rule="manual" 是人工干预，"__max_switches__" 是切换次数达上限被强制收工 */
export interface ModeSwitchEvent {
  rule: string;
  /** 人类可读决策依据，可直接展示在轨迹里 */
  reason: string;
  from: HybridRuntimeMode;
  to: HybridRuntimeMode;
}

export type HybridRuntimeMode = "workflow" | "agent";

// ---------- 执行轨迹（消息上持久化，刷新后靠 /logs 重建）----------

/** 轨迹里的一段：要么是某个节点的一次进出，要么是一次模式切换 */
export type TraceItem =
  | {
      kind: "node";
      node_id: string;
      type: HybridNodeType;
      name: string;
      /** 进行中 / 已完成 / 失败 */
      status: "running" | "ok" | "failed";
      on_fail?: string | null;
      error?: string | null;
    }
  | { kind: "switch"; rule: string; reason: string; from: HybridRuntimeMode; to: HybridRuntimeMode };

// ---------- 工作流定义（管理面 CRUD）----------

export interface WorkflowNodeDef {
  id: string;
  type: HybridNodeType;
  name?: string;
  config?: Record<string, unknown>;
  timeout_seconds?: number | null;
  /** 节点内重试次数（先重试，再谈失败跳转/切换） */
  retry?: number;
  /** 失败跳转目标；null 才进入切换规则判定 */
  on_fail?: string | null;
  /** 执行到本节点主动让出给 Agent 模式 */
  requires_agent?: boolean;
}

export interface WorkflowEdgeDef {
  source: string;
  target: string;
  /** 受限表达式，仅 shared.<key> 作用域；null = 默认兜底边 */
  condition?: string | null;
}

export interface WorkflowDef {
  /** ⚠️ 不参与落库主键，后端会自己生成 12 位 hex 作为行 id；建议留空 */
  id?: string;
  name?: string;
  version?: number;
  entry?: string | null;
  nodes: WorkflowNodeDef[];
  edges?: WorkflowEdgeDef[];
  /** 0 = 用服务端默认 */
  timeout_seconds?: number;
}

export interface WorkflowIn {
  name: string;
  description?: string;
  definition: WorkflowDef;
}

export interface WorkflowRow {
  id: string;
  owner_id: string;
  name: string;
  description: string;
  definition: WorkflowDef;
  version: number;
  created_at: number;
  updated_at: number;
  /** 内置只读：改/删一律 404 */
  builtin: boolean;
}

// ---------- 切换规则 ----------

export type RuleAction = "to_agent" | "to_workflow" | "terminate";

export interface RuleIn {
  /** ≤ 40 字符。⚠️ 引擎实际只会上报 4 个 signal，自定义的存得下但不会触发 */
  signal: string;
  /** ≤ 500 字符；空 = 命中信号即触发。仅做语法校验，语义运行期才判 */
  condition?: string;
  action: RuleAction;
  reason?: string;
  /** 0–9999，数字小者先求值 */
  priority?: number;
  enabled?: boolean;
  /** 0–86400，显式 0 = 结构型规则豁免防抖 */
  cooldown_seconds?: number;
}

export interface RuleRow {
  id: string;
  owner_id: string;
  name: string;
  signal: string;
  condition?: string | null;
  action: RuleAction;
  reason?: string | null;
  priority: number;
  enabled: boolean;
  cooldown_seconds: number;
  builtin: boolean;
}

/** 引擎只会上报这 4 个 signal；界面上自定义 signal 要提示「不会触发」 */
export const KNOWN_SIGNALS = [
  "requires_agent",
  "node_failed",
  "budget_exhausted",
  "goal_satisfied",
] as const;

// ---------- 执行记录与日志 ----------

export type HybridRunStatus = "running" | "completed" | "failed" | "timeout" | "stopped";

export interface HybridRunRow {
  run_id: string;
  task_id: string;
  workflow_id?: string | null;
  status: HybridRunStatus;
  created_at: number;
  updated_at: number;
}

export interface HybridRunDetail extends HybridRunRow {
  owner_id: string;
  /** TaskContext 快照：goal / current_mode / workflow_state / agent_state / shared / mode_trace */
  context: Record<string, unknown>;
}

export type HybridLogEventType =
  | "node_enter"
  | "node_exit"
  | "tool_call"
  | "token"
  | "switch"
  | "error"
  | "status"
  | "human_intervention";

export interface HybridLogRow {
  id: number;
  event_type: HybridLogEventType;
  mode?: HybridRuntimeMode | null;
  node_id?: string | null;
  /** 仅 switch 事件 */
  decision?: { rule: string; reason: string; from: HybridRuntimeMode; to: HybridRuntimeMode } | null;
  /** 摘要级数据，后端序列化后截断 2000 字符 */
  payload?: Record<string, unknown>;
  ts: number;
}
