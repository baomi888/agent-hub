// 对话区的共享类型：消息与附件
// 从 ChatArea.tsx 拆出（2026-09 重构），page.tsx 经由 components/ChatArea 再导出引用
import type {
  Grounding,
  KbProposal,
  RefSource,
  ToolApprovalRequest,
  ToolEvent,
  TraceItem,
  WebSource,
} from "@/lib/types";

/**
 * 从回答正文里剥出来的资料块（对应 core/data_boundary.strip_blocks 的返回值）。
 *
 * `source` 是后端 SOURCE_KB / SOURCE_WEB / SOURCE_UPLOAD；
 * `text` 已被后端截到 4000 字（`truncated` 标出是否截断）——
 * 折叠区里放几万字没有意义，还会把页面拖慢。
 */
export interface RetrievedBlock {
  source: "kb" | "web" | "upload" | string;
  text: string;
  truncated?: boolean;
}

export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolEvent[];
  refs?: string;
  error?: string;
  streaming?: boolean;
  /**
   * 流式期间的进度说明（来自 SSE status 帧，已翻译成人话）：
   * "正在翻你的资料库…" "找到 3 段相关资料" "正在组织答案…"
   * 带资料库提问时首帧要等十几秒，这行字是那段时间里唯一的"它还活着"的证据。
   */
  status?: string;
  /**
   * 建库建议（板块 D，来自 SSE `kb_proposal` 帧，发生在 done 之前）。
   *
   * 挂在这一条回答上而不是放到页面级状态里：建议说的就是"这一轮的东西要不要存"，
   * 脱离了具体那条回答，"这些"指什么是模糊的。会话重新打开时不会再出现
   * （它是那一轮的交互，不是历史内容）。
   */
  proposal?: KbProposal;
  /**
   * 需要用户点头的高风险工具调用（SSE `approval` 帧）。
   *
   * 为什么是数组而不是单张：同一条回答里模型可能对不同参数调用两次同一个工具
   * （比如两个文件），后端会为每次调用各推一帧。存单张就必须丢一个，
   * 而"丢了的那一张"在界面上表现为工具静默不执行——最难查的那种。
   */
  approvals?: ToolApprovalRequest[];
  /** 用户主动中断了这次生成 */
  stopped?: boolean;
  attachments?: ChatAttachment[];
  /** 引用溯源条目：来自 SSE sources 事件，点角标可展开原文 */
  sources?: RefSource[];
  /**
   * 联网来源：来自 SSE `web_sources` 事件（web_search 真实返回的网页）。
   *
   * 与 `sources` 分开存，不合并：两者的生命周期和语义都不同 —— 资料库来源
   * 在正文之前就下发（要赶在用户读到第一句前挂上"依据较弱"），联网来源要等
   * 工具真的被调用；而且一个能看原文、一个能开网页，界面形态本来就不一样。
   * 合并会让后到的一批把先到的顶掉（旧前端就是整条覆盖）。
   */
  webSources?: WebSource[];
  /** 依据强度：来自 SSE grounding 事件，weak=true 时回答上方挂提示条 */
  grounding?: Grounding;
  /** 后端 messages 表自增 id，用于删除整轮对话 */
  dbId?: number;
  /**
   * 从回答正文里剥出来的资料块（SSE done 帧的 `blocks`，2026-11-23）。
   *
   * 后端 `core/data_boundary.strip_blocks` 把模型复述进正文的
   * 「【资料开始·用户上传】…【资料结束·用户上传】」整段摘出来，
   * 正文不再携带这些内部资料（含 score 这类实现细节）。
   * 摘出来的内容挂在这里，由 `BlocksTrace` 渲染成默认收起的折叠区 ——
   * 用户仍能展开看"这一轮到底检索到了什么"，但它不再把答案挤到几屏之外。
   */
  blocks?: RetrievedBlock[];
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
