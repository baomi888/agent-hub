// 对话区的共享类型：消息与附件
// 从 ChatArea.tsx 拆出（2026-09 重构），page.tsx 经由 components/ChatArea 再导出引用
import type { Grounding, RefSource, ToolEvent, TraceItem } from "@/lib/types";

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
  /** 依据强度：来自 SSE grounding 事件，weak=true 时回答上方挂提示条 */
  grounding?: Grounding;
  /** 后端 messages 表自增 id，用于删除整轮对话 */
  dbId?: number;
  /**
   * 本轮计划卡片的主题名（如「学习计划」），来自 SSE done 帧（仅 plan 请求有值）。
   *
   * 为什么由后端给：主题可能是从**历史**推断出来的——用户问「计划再细一点」，
   * 前端的 `detectPlanTopic(这一句)` 必然命不中，卡片就会显示成通用的「结构化计划」。
   * 后端看得到前文，能把它续成「学习计划」。前端仍保留关键词兜底（老消息没有这个字段）。
   */
  planTopic?: string;
  /**
   * 混合架构执行记录 id：来自 SSE done 事件（仅 mode=hybrid 有值）。
   *
   * ★ 它存在的唯一理由是**轨迹不随聊天消息持久化**（后端已知边界）：
   * 刷新页面后聊天记录能恢复，但执行轨迹恢复不了 —— 必须靠这个 run_id
   * 去 GET /api/hybrid/runs/{id}/logs 重建时间轴。
   * 存进消息而不是组件 state，是因为消息本身会被持久化，run_id 得跟着一起活过刷新。
   */
  hybridRunId?: string;
  /**
   * 本轮实际的混合执行模式。
   * ⚠️ 选了 hybrid 不等于真走了混合：后端开关关掉时会**静默回退 agent**，
   * 届时这里是 agent 而 UI 上用户选的是 hybrid —— 必须让用户看见这个差异，
   * 否则「我明明选了混合怎么没有轨迹」。
   */
  hybridMode?: "workflow" | "agent" | null;
  /** 该轮的执行轨迹（流式过程中逐步长出来；刷新后由 /logs 重建） */
  hybridTrace?: TraceItem[];
}

export interface ChatAttachment {
  id: string;
  name: string;
  type: string;
  size: number;
  url: string;
  file?: File;
}
