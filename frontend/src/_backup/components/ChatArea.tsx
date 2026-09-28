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

function Bubble({ m }: { m: UiMessage }) {
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
          <details className="mt-3 rounded-lg bg-subtle px-3 py-2 text-sm text-muted">
            <summary className="cursor-pointer font-semibold">参考资料</summary>
            <div className="mt-1 whitespace-pre-wrap">{m.refs}</div>
          </details>
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
    <label className="flex min-w-[7rem] flex-col">
      <span className="text-sm font-semibold text-text">{label}</span>
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
        className="mt-1 w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-text outline-none focus:border-accent"
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
}: Props) {
  const [input, setInput] = useState("");
  const [showParams, setShowParams] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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

  return (
    <section className="flex h-full min-w-0 flex-1 flex-col">
      {/* 消息区（居中限宽） */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 pb-4 pt-6">
          {messages.length === 0 ? (
            // 欢迎页：居中 logo + 标题 + 2×2 能力卡片
            <div className="flex flex-1 flex-col items-center justify-center py-10 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent text-white shadow-sm">
                <Sparkle className="h-7 w-7" />
              </div>
              <h1 className="mt-5 text-2xl font-bold tracking-tight text-text">
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
                    className="group flex flex-col items-start gap-2 rounded-2xl border border-line bg-surface p-4 text-left transition hover:border-accent/40 hover:shadow-sm"
                  >
                    <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent">
                      {a.icon}
                    </span>
                    <span className="text-sm font-semibold text-text">{a.title}</span>
                    <span className="text-xs text-muted">{a.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m) => (
                <Bubble key={m.id} m={m} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 参数面板（点「参数」展开） */}
      {showParams && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-2">
          <div className="fade-up flex flex-wrap items-end gap-5 rounded-2xl border border-line bg-surface p-4">
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

      {/* 底部：模式切换 + 大圆角输入框（居中限宽） */}
      <div className="mx-auto w-full max-w-3xl px-4 pb-4">
        <div className="mb-2 flex items-center gap-1">
          {MODE_LABELS.map((m) => (
            <button
              key={m.value}
              title={m.hint}
              onClick={() => onModeChange(m.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                mode === m.value
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-subtle"
              }`}
            >
              {m.label}
            </button>
          ))}
          <button
            onClick={() => setShowParams((s) => !s)}
            className={`ml-1 rounded-full px-3 py-1 text-xs font-medium transition ${
              showParams ? "bg-accent-soft text-accent" : "text-muted hover:bg-subtle"
            }`}
          >
            参数
          </button>
        </div>

        <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-sm">
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