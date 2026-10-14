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

// 聊天模式：auto 自动 / rag 强制检索 / agent 强制工具 / llm 纯对话 / hybrid 工作流×智能体混合
export type ChatMode = "auto" | "rag" | "agent" | "llm" | "hybrid";

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
  preview: string;
  text: string;
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

// ---------- 混合架构（hybrid）----------
// 与后端 hybrid/ 模块对齐：工作流定义 / 切换规则 / 执行记录

export type HybridMode = "workflow" | "agent";
export type NodeType = "llm" | "tool" | "agent" | "condition" | "human";

export interface WorkflowNode {
  id: string;
  type: NodeType;
  name?: string;
  config: Record<string, unknown>;
  timeout_seconds?: number | null;
  retry?: number;
  on_fail?: string | null;
  requires_agent?: boolean;
}

export interface WorkflowEdge {
  source: string;
  target: string;
  condition?: string | null;
}

export interface WorkflowDef {
  id: string;
  name: string;
  version?: number;
  entry: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  timeout_seconds?: number | null;
}

// workflow_defs 表行（GET /api/hybrid/workflows 返回）
export interface WorkflowRow {
  id: string;
  owner_id: string | null; // null = 系统内置（只读）
  name: string;
  description: string;
  definition: WorkflowDef;
  version: number;
  builtin: boolean;
  created_at: number;
  updated_at: number;
}

export type SwitchAction = "to_agent" | "to_workflow" | "terminate";

export interface SwitchRule {
  name: string;
  signal: string;
  condition: string;
  action: SwitchAction;
  reason: string;
  priority: number;
  enabled: boolean;
  cooldown_seconds: number;
  /** true = 系统默认规则（删不掉，只能同名覆盖） */
  system?: boolean;
}

// 气泡内执行轨迹条目：SSE hybrid_start / workflow_node / mode_switch 累积而来
export interface TraceItem {
  kind: "start" | "node_enter" | "node_exit" | "switch";
  mode?: HybridMode;
  nodeId?: string;
  nodeName?: string;
  nodeType?: NodeType;
  status?: string; // ok | failed
  error?: string;
  rule?: string;
  reason?: string;
  from?: HybridMode;
  to?: HybridMode;
}

// exec_logs 行（GET /api/hybrid/runs/{id}/logs）
export interface ExecLog {
  id: number;
  event_type: string;
  mode: string;
  node_id: string | null;
  decision: string | null;
  payload: string | null;
  ts: number;
}

export interface HybridRun {
  run_id: string;
  task_id: string;
  workflow_id: string | null;
  status: string;
  created_at: number;
  updated_at: number;
}

// ---------- 模板广场 ----------
export interface Template {
  id: string;
  name: string;
  category: string;
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