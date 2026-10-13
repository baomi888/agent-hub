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

// 聊天模式：auto 自动 / rag 强制检索 / agent 强制工具 / llm 纯对话
export type ChatMode = "auto" | "rag" | "agent" | "llm";

// 场景预设：kaoyan 考研复习 / notes 课程笔记 / paper 论文写作（轻量版只做提示词 + 选择器）
export type ScenarioId = "kaoyan" | "notes" | "paper";

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
export type DelegateKind = "study" | "review" | "encourage" | "custom";

/**
 * 触发计划。注意**不是 cron**：后端用的是结构化字段，
 * 前端把它渲染成「每天 21:00」这种人话，而不是把 crontab 摊给用户看。
 */
export interface DelegateSchedule {
  freq: DelegateFreq;
  /** 触发时刻 HH:MM；interval 频率下可为空 */
  at?: string | null;
  /** weekly 用：0=周日 … 6=周六 */
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