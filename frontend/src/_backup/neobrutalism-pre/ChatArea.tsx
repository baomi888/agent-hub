"use client";

// 中央对话区：居中限宽消息流 + 底部模式切换 / 参数设置 / 大圆角输入框
import { useEffect, useRef, useState } from "react";
import type { ChatMode, ToolEvent } from "@/lib/types";

// 本地展示用的消息模型（含流式/工具调用状态）
export interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolEvent[];
  refs?: string;
  error?: string;
  streaming?: boolean;
}

const MODE_LABELS: { value: ChatMode; label: string; hint: string }[] = [
  { value: "auto", label: "自动", hint: "绑定知识库后自动优先检索" },
  { value: "rag", label: "检索", hint: "强制从知识库检索回答" },
  { value: "agent", label: "智能体", hint: "可调用工具：联网搜索 / 计算 / 天气" },
  { value: "llm", label: "纯对话", hint: "纯大模型对话，不检索" },
];

// 欢迎页 2×2 能力卡片
const ABILITIES: { title: string; desc: string; q: string; icon: React.ReactNode }[] = [
  {
    title: "查资料",
    desc: "从知识库检索内容",
    q: "帮我从资料库检索相关资料",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6" />
        <path d="M8 13h8M8 17h5" />
      </svg>
    ),
  },
  {
    title: "联网搜索",
    desc: "获取最新资讯",
    q: "联网搜索一下今天的热点新闻",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20" />
        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
    ),
  },
  {
    title: "数学计算",
    desc: "快速准确算数",
    q: "帮我计算 (12 + 34) × 5",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
        <rect x="4" y="2" width="16" height="20" rx="2" />
        <path d="M8 6h8" />
        <path d="M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h.01M12 19h.01M16 19h.01" />
      </svg>
    ),
  },
  {
    title: "查天气",
    desc: "全国城市天气",
    q: "查一下北京今天的天气",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
        <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
      </svg>
    ),
  },
];

// 四角星 logo（欢迎页头像 / 助手消息头像共用）
function Sparkle({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
      <path d="M12 2l2.3 7.7L22 12l-7.7 2.3L12 22l-2.3-7.7L2 12l7.7-2.3z" />
    </svg>
  );
}

// 轻量富文本：处理 **加粗** 与换行（不引入完整 markdown 依赖）
function renderContent(text: string): React.ReactNode {
  return text.split("**").map((part, i) => {
    const lines = part.split("\n").map((line, j, arr) => (
      <span key={j}>
        {line}
        {j < arr.length - 1 && <br />}
      </span>
    ));
    return i % 2 === 1 ? <strong key={i}>{lines}</strong> : <span key={i}>{lines}</span>;
  });
}

function Bubble({
  m,
  userQuestion,
  onCopy,
  onForward,
  onRetry,
}: {
  m: UiMessage;
  userQuestion?: string;
  onCopy?: (text: string) => void;
  onForward?: (text: string) => void;
  onRetry?: (question: string) => void;
}) {
  const [feedback, setFeedback] = useState<"like" | "dislike" | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(m.content);
      setCopied(true);
      onCopy?.(m.content);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      onCopy?.(m.content);
    }
  };

  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="glass max-w-[75%] bg-accent-soft px-4 py-2.5 text-[15px] leading-relaxed text-text">
          <div className="whitespace-pre-wrap">{m.content}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-white">
        <Sparkle className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1 pt-0.5">
        {/* 工具调用过程：默认折叠为灰色横条，点击才展开 */}
        {m.toolCalls && m.toolCalls.length > 0 && (
          <details className="mb-2 rounded-lg bg-subtle px-3 py-1.5 text-xs text-muted">
            <summary className="cursor-pointer">
              <span>
                ▾ 思考过程：调用了{" "}
                {Array.from(new Set(m.toolCalls.map((t) => t.name))).join("、")} 等{" "}
                {m.toolCalls.length} 个工具
              </span>
            </summary>
            <div className="mt-1.5 space-y-1.5">
              {m.toolCalls.map((t, i) => (
                <div key={i}>
                  <div className="font-semibold">调用工具 · {t.name}</div>
                  {t.input && <div className="truncate opacity-80">入参：{t.input}</div>}
                  {t.output && <div className="line-clamp-3 opacity-80">结果：{t.output}</div>}
                </div>
              ))}
            </div>
          </details>
        )}

        <div className="whitespace-pre-wrap text-text">
          {m.error ? (
            <div className="warn rounded-lg px-3 py-2 text-sm">
              <div className="font-semibold">⚠ 暂时无法完成请求</div>
              <details className="mt-1">
                <summary className="cursor-pointer text-xs opacity-80">查看详情</summary>
                <div className="mt-1 whitespace-pre-wrap text-xs">{m.error}</div>
              </details>
            </div>
          ) : (
            renderContent(m.content)
          )}
          {m.streaming && <span className="cursor" />}
        </div>

        {/* 引用资料 */}
        {m.refs && (
          <div className="mt-3 rounded-xl border border-line bg-bg px-3 py-2 text-sm">
            <div className="mb-1 flex items-center gap-1.5 font-title text-text">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 text-accent">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
              参考资料
            </div>
            <div className="whitespace-pre-wrap text-xs leading-relaxed text-muted">{m.refs}</div>
          </div>
        )}

        {/* 操作栏：复制 / 转发 / 重试 / 赞 / 踩（流式或出错时不显示） */}
        {!m.streaming && !m.error && (
          <div className="mt-2 flex items-center gap-1 text-faint">
            {/* 复制 */}
            <button
              onClick={handleCopy}
              title={copied ? "已复制" : "复制"}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition hover:bg-subtle hover:text-text"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              <span>{copied ? "已复制" : "复制"}</span>
            </button>

            {/* 转发：填入输入框 */}
            <button
              onClick={() => onForward?.(m.content)}
              title="转发到输入框"
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition hover:bg-subtle hover:text-text"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                <polyline points="16 6 12 2 8 6" />
                <line x1="12" y1="2" x2="12" y2="15" />
              </svg>
              <span>转发</span>
            </button>

            {/* 重试：重发上一条用户消息 */}
            {onRetry && userQuestion && (
              <button
                onClick={() => onRetry(userQuestion)}
                title="重新生成"
                className="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition hover:bg-subtle hover:text-text"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                  <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
                  <path d="M21 3v5h-5" />
                </svg>
                <span>重试</span>
              </button>
            )}

            <div className="mx-1 h-3 w-px bg-line" />

            {/* 赞 */}
            <button
              onClick={() => setFeedback(feedback === "like" ? null : "like")}
              title="赞"
              className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs transition hover:bg-subtle ${
                feedback === "like" ? "text-accent" : "hover:text-text"
              }`}
            >
              <svg viewBox="0 0 24 24" fill={feedback === "like" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M7 10v12" />
                <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
              </svg>
            </button>

            {/* 踩 */}
            <button
              onClick={() => setFeedback(feedback === "dislike" ? null : "dislike")}
              title="踩"
              className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs transition hover:bg-subtle ${
                feedback === "dislike" ? "text-rose-500" : "hover:text-text"
              }`}
            >
              <svg viewBox="0 0 24 24" fill={feedback === "dislike" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M17 14V2" />
                <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
              </svg>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// 数字参数字段：中文名 + 通俗说明
function Field({
  label,
  hint,
  value,
  min,
  max,
  onCommit,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  onCommit: (v: number) => void;
}) {
  return (
    <label className="flex flex-col">
      <span className="text-sm font-title text-text">{label}</span>
      <span className="text-xs text-muted">{hint}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) onCommit(v);
        }}
        className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm text-text outline-none focus:border-accent"
      />
    </label>
  );
}

interface Props {
  messages: UiMessage[];
  mode: ChatMode;
  sending: boolean;
  onModeChange: (m: ChatMode) => void;
  onSend: (question: string) => void;
  onStop: () => void;
  topK: number;
  chunkSize: number;
  chunkOverlap: number;
  onParamChange: (patch: {
    topK?: number;
    chunkSize?: number;
    chunkOverlap?: number;
  }) => void;
  // 当前会话标题与已绑定知识库（顶栏展示用）
  sessionTitle?: string | null;
  boundKb?: string | null;
  kbNameMap?: Record<string, string>;
  onUnbindKb?: () => void;
  // 聊天框附件上传（上传到已绑定知识库）
  onAttach?: (kbId: string, files: File[]) => void;
  // 右侧资料库面板折叠
  kbPanelOpen?: boolean;
  onToggleKbPanel?: () => void;
}

export default function ChatArea({
  messages,
  mode,
  sending,
  onModeChange,
  onSend,
  onStop,
  topK,
  chunkSize,
  chunkOverlap,
  onParamChange,
  sessionTitle,
  boundKb,
  kbNameMap,
  onUnbindKb,
  onAttach,
  kbPanelOpen,
  onToggleKbPanel,
}: Props) {
  const [input, setInput] = useState("");
  const [showParams, setShowParams] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const attachRef = useRef<HTMLInputElement>(null);

  const currentMode = MODE_LABELS.find((m) => m.value === mode);

  // 新消息时滚动到底部
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const submit = () => {
    const q = input.trim();
    if (!q || sending) return;
    setInput("");
    onSend(q);
  };

  const fillExample = (q: string) => {
    setInput(q);
    inputRef.current?.focus();
  };

  // 转发：把助手回答填入输入框
  const handleForward = (text: string) => {
    setInput(text);
    inputRef.current?.focus();
  };

  return (
    <section className="ambient-canvas flex h-full min-w-0 flex-1 flex-col">
      {/* 顶栏：当前会话标题 + 已绑定知识库 chip */}
      <div className="glass-header flex items-center gap-2 px-4 py-2.5">
        <span className="truncate text-sm font-title text-text">{sessionTitle ?? "新对话"}</span>
        {boundKb && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent">
            已绑定：{kbNameMap?.[boundKb] ?? boundKb}
            {onUnbindKb && (
              <button
                onClick={onUnbindKb}
                title="解绑知识库"
                className="ml-0.5 rounded-full transition hover:opacity-70"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="h-3 w-3">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            )}
          </span>
        )}
        <div className="flex-1" />
        {/* 资料库面板折叠/展开 */}
        {onToggleKbPanel && (
          <button
            onClick={onToggleKbPanel}
            title={kbPanelOpen ? "收起资料库" : "展开资料库"}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-subtle hover:text-text"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              {kbPanelOpen ? (
                <>
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </>
              ) : (
                <>
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                </>
              )}
            </svg>
          </button>
        )}
      </div>

      {/* 消息区（居中限宽） */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-4 pb-4 pt-6">
          {messages.length === 0 ? (
            // 欢迎页：居中 logo + 标题 + 2×2 能力卡片
            <div className="flex flex-1 flex-col items-center justify-center py-10 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent text-white shadow-[0_0_24px_rgba(139,92,246,0.35)]">
                <Sparkle className="h-7 w-7" />
              </div>
              <h1 className="mt-5 text-2xl font-title tracking-tight text-text">
                你好呀，我是集合式 Agent
              </h1>
              <p className="mt-2 text-[15px] text-muted">
                支持知识库检索、联网搜索、数学计算、天气查询
              </p>
              <div className="mt-8 grid w-full max-w-md grid-cols-2 gap-3">
                {ABILITIES.map((a) => (
                  <button
                    key={a.title}
                    onClick={() => fillExample(a.q)}
                    className="card-hover group flex flex-col items-start gap-2 rounded-2xl border border-transparent bg-surface p-4 text-left shadow-[var(--shadow-1)] hover:border-accent/30"
                  >
                    <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent transition group-hover:scale-105">
                      {a.icon}
                    </span>
                    <span className="text-sm font-title text-text">{a.title}</span>
                    <span className="text-xs text-muted">{a.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m, idx) => {
                // 找到该助手消息对应的上一条用户消息（用于"重试"）
                let userQ: string | undefined;
                if (m.role === "assistant") {
                  for (let i = idx - 1; i >= 0; i--) {
                    if (messages[i].role === "user") {
                      userQ = messages[i].content;
                      break;
                    }
                  }
                }
                return (
                  <Bubble
                    key={m.id}
                    m={m}
                    userQuestion={userQ}
                    onForward={handleForward}
                    onRetry={onSend}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* 参数面板（点「参数」展开） */}
      {showParams && (
        <div className="mx-auto w-full max-w-4xl px-4 pb-2">
          <div className="fade-up grid grid-cols-3 gap-4 rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-1)]">
            <Field
              label="检索条数"
              hint="返回几条最相关资料"
              value={topK}
              min={1}
              max={10}
              onCommit={(v) => onParamChange({ topK: v })}
            />
            <Field
              label="分段长度"
              hint="资料切多长的片段"
              value={chunkSize}
              min={100}
              max={2000}
              onCommit={(v) => onParamChange({ chunkSize: v })}
            />
            <Field
              label="重叠字数"
              hint="相邻片段重叠多少字"
              value={chunkOverlap}
              min={0}
              max={500}
              onCommit={(v) => onParamChange({ chunkOverlap: v })}
            />
          </div>
        </div>
      )}

      {/* 底部：模式切换（含说明，同一行）+ 大圆角输入框 */}
      <div className="mx-auto w-full max-w-4xl px-4 pb-4">
        <div className="mb-1.5 flex items-center gap-1">
          {MODE_LABELS.map((m) => (
            <button
              key={m.value}
              onClick={() => onModeChange(m.value)}
              title={m.hint}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                mode === m.value
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-subtle hover:text-text"
              }`}
            >
              {m.label}
            </button>
          ))}
          <span className="ml-1 truncate text-xs text-faint">{currentMode?.hint}</span>
          <button
            onClick={() => setShowParams((s) => !s)}
            title="检索参数设置"
            className={`ml-auto rounded-full p-1.5 transition ${
              showParams ? "bg-accent-soft text-accent" : "text-muted hover:bg-subtle hover:text-text"
            }`}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 1v6M12 17v6M4.2 4.2l4.3 4.3M15.5 15.5l4.3 4.3M1 12h6M17 12h6M4.2 19.8l4.3-4.3M15.5 8.5l4.3-4.3" />
            </svg>
          </button>
        </div>

        <div className="input-focus flex items-end gap-1.5 rounded-2xl border border-line bg-surface p-2 shadow-[var(--shadow-1)]">
          {/* 附件上传：上传到已绑定知识库 */}
          <button
            onClick={() => {
              if (!boundKb) {
                alert("请先在右侧资料库绑定一个知识库，再上传附件");
                return;
              }
              attachRef.current?.click();
            }}
            title="上传附件到已绑定知识库"
            className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-subtle hover:text-text"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <path d="M21.4 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <input
            ref={attachRef}
            type="file"
            multiple
            accept=".txt,.md,.pdf"
            className="hidden"
            onChange={(e) => {
              const fs = e.target.files;
              if (fs && fs.length > 0 && boundKb && onAttach) {
                onAttach(boundKb, Array.from(fs));
              }
              if (attachRef.current) attachRef.current.value = "";
            }}
          />
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2 text-[15px] leading-relaxed text-text outline-none placeholder:text-faint"
          />
          {sending ? (
            <button
              onClick={onStop}
              title="停止"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-rose-500 text-white transition hover:opacity-90"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!input.trim()}
              title="发送"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-white transition disabled:opacity-40 hover:opacity-90"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                <path d="M12 19V5" />
                <path d="M5 12l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}