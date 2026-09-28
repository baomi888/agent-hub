// 对话区的共享类型：消息与附件
// 从 ChatArea.tsx 拆出（2026-09 重构），page.tsx 经由 components/ChatArea 再导出引用
import type { RefSource, ToolEvent } from "@/lib/types";

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolEvent[];
  refs?: string;
  error?: string;
  streaming?: boolean;
  /** 用户主动中断了这次生成 */
  stopped?: boolean;
  attachments?: ChatAttachment[];
  /** 引用溯源条目：来自 SSE sources 事件，点角标可展开原文 */
  sources?: RefSource[];
  /** 后端 messages 表自增 id，用于删除整轮对话 */
  dbId?: number;
}

export interface ChatAttachment {
  id: string;
  name: string;
  type: string;
  size: number;
  url: string;
  file?: File;
}
