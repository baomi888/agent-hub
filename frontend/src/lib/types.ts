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