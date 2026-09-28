"use client";

// 中央对话区：书本打开效果 + 欢迎页 + 底部输入框
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
//
// 2026-09 重构：消息类型/常量/正文渲染/计划卡片/Bubble/Field 拆到 ./chat/*，
// 本文件只保留主组件（顶栏 + 欢迎页 + 消息区 + 输入区）。
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ChatMode, RefSource } from "@/lib/types";
import { ABILITIES, EXAMPLES, MODE_LABELS } from "./chat/constants";
import { buildPlanPrompt, detectPlanTopic, isPlanRequest, stripPlanHint } from "./chat/plan";
import Bubble from "./chat/Bubble";
import BubbleErrorBoundary from "./chat/BubbleErrorBoundary";
import Field from "./chat/Field";
import type { ChatAttachment, UiMessage } from "./chat/types";
import FilePreviewModal from "./FilePreviewModal";

// 兼容导出：page.tsx 仍从 "@/components/ChatArea" 引入这些符号
export type { UiMessage, ChatAttachment } from "./chat/types";
export { stripPlanHint } from "./chat/plan";

const aid = () => Math.random().toString(36).slice(2, 10);

interface Props {
  messages: UiMessage[];
  mode: ChatMode;
  sending: boolean;
  onModeChange: (m: ChatMode) => void;
  onSend: (question: string, attachments?: ChatAttachment[], display?: string) => void;
  onStop: () => void;
  /** 重新生成：传入目标助手消息 id 与原始问题，覆盖原答案而不是追加 */
  onRetry?: (assistantId: string, question: string) => void;
  /** 轻量反馈出口：复制失败、赞踩等，交给页面出 toast */
  onNotice?: (type: "success" | "error" | "info", msg: string) => void;
  topK: number;
  chunkSize: number;
  chunkOverlap: number;
  onParamChange: (patch: {
    topK?: number;
    chunkSize?: number;
    chunkOverlap?: number;
  }) => void;
  sessionTitle?: string | null;
  boundKb?: string | null;
  kbNameMap?: Record<string, string>;
  onUnbindKb?: () => void;
  kbPanelOpen?: boolean;
  onToggleKbPanel?: () => void;
  /** 窄屏下打开会话列表抽屉 */
  onOpenSidebar?: () => void;
  /** 外部把建议问题预填进输入框（如联网建库后），不自动发送 */
  prefill?: { text: string; ts: number };
  /** 删除某条消息及其所在的一轮对话（user + 后续 assistant） */
  onDeleteTurn?: (msgDbId: number) => void;
}

export default function ChatArea({
  messages,
  mode,
  sending,
  onModeChange,
  onSend,
  onStop,
  onRetry,
  onNotice,
  topK,
  chunkSize,
  chunkOverlap,
  onParamChange,
  sessionTitle,
  boundKb,
  kbNameMap,
  onUnbindKb,
  kbPanelOpen,
  onToggleKbPanel,
  onOpenSidebar,
  prefill,
  onDeleteTurn,
}: Props) {
  const [input, setInput] = useState("");
  const [showParams, setShowParams] = useState(false);
  const [pending, setPending] = useState<ChatAttachment[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const attachRef = useRef<HTMLInputElement>(null);
  // 输入历史：↑/↓ 在历史里前后翻，Esc 清空输入框
  // 本地历史用惰性初始值读，不在 effect 里 setState（会触发 react-hooks/set-state-in-effect）
  const [history, setHistory] = useState<string[]>(() => {
    try {
      const raw = typeof localStorage === "undefined" ? null : localStorage.getItem("baomi.history");
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.filter((x) => typeof x === "string").slice(0, 50) : [];
    } catch {
      // 历史是锦上添花，读不到（隐私模式下 localStorage 不可用）不影响主流程
      return [];
    }
  });
  const histIdxRef = useRef(-1); // -1 = 正在编辑草稿
  const draftRef = useRef("");
  const stickRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  // 从引用角标打开的全文预览（文件名来自 RefSource，kb 用当前会话绑定的）
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  // 顶栏「能力」快捷弹层：对话进行中也能一键切到常用能力（欢迎页四宫格的随身版）
  const [abOpen, setAbOpen] = useState(false);
  // 联网建库后把建议问题预填进输入框：渲染期基于 prop 调整 state（用 state 去重，避免 ref 在渲染期被读写），
  // 不自动发送，让用户确认/补充后再发
  const [appliedPrefillTs, setAppliedPrefillTs] = useState<number>(-1);
  if (prefill && prefill.ts !== appliedPrefillTs) {
    setAppliedPrefillTs(prefill.ts);
    setInput(prefill.text);
  }

  // 高度自适应统一在这里做：历史回填时也会跟着变高
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [input]);

  // 进来就能打字：省掉"先点一下输入框"这一步
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // 预填建议问题后把焦点送进输入框（仅副作用，不在此 setState）
  useEffect(() => {
    if (!prefill) return;
    inputRef.current?.focus();
  }, [prefill]);

  // 按时段问候：SSR 与水合各算一次可能跨过整点，首帧用安全默认值，挂载后再校准
  const [greeting, setGreeting] = useState("你好呀");
  useEffect(() => {
    const h = new Date().getHours();
    setGreeting(
      h < 6 ? "夜深了" : h < 11 ? "早上好" : h < 13 ? "中午好" : h < 18 ? "下午好" : "晚上好"
    );
  }, []);

  // 能力弹层：Esc 关闭
  useEffect(() => {
    if (!abOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAbOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [abOpen]);

  const pendingImageCount = pending.filter((a) => a.type.startsWith("image/")).length;
  const pendingOtherCount = pending.length - pendingImageCount;

  const currentMode = MODE_LABELS.find((m) => m.value === mode);

  // Codex 式分段滑块：量出 active 按钮的位置，让 .mode-thumb 滑过去
  const modeTabsRef = useRef<HTMLDivElement | null>(null);
  const modeBtnRefs = useRef<Partial<Record<ChatMode, HTMLButtonElement | null>>>({});
  const [thumb, setThumb] = useState({ x: 0, w: 0, ready: false });
  useLayoutEffect(() => {
    const update = () => {
      const el = modeBtnRefs.current[mode];
      if (!el) return;
      setThumb({ x: el.offsetLeft, w: el.offsetWidth, ready: true });
    };
    update();
    // 字体加载、窗口缩放都会改变按钮宽度，跟着重算
    const ro = new ResizeObserver(update);
    if (modeTabsRef.current) ro.observe(modeTabsRef.current);
    window.addEventListener("resize", update);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [mode]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // 切换会话（首条消息换了）时强制回到底部，否则会停在上一会话的滚动位置
  const firstId = messages[0]?.id ?? "";
  const prevFirstRef = useRef(firstId);
  useEffect(() => {
    if (firstId === prevFirstRef.current) return;
    prevFirstRef.current = firstId;
    stickRef.current = true;
    setAtBottom(true);
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [firstId]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickRef.current = near;
    setAtBottom((prev) => (prev === near ? prev : near));
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = true;
    setAtBottom(true);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, []);

  const addFiles = (files: FileList | File[]) => {
    const arr = Array.from(files);
    if (arr.length === 0) return;
    setPending((prev) => {
      const next = [...prev];
      for (const f of arr) {
        if (next.some((a) => a.name === f.name && a.size === f.size)) continue;
        next.push({
          id: aid(),
          name: f.name,
          type: f.type || "application/octet-stream",
          size: f.size,
          url: URL.createObjectURL(f),
          file: f,
        });
      }
      return next;
    });
  };

  const removeAttachment = (id: string) => {
    setPending((prev) => {
      const t = prev.find((a) => a.id === id);
      if (t) URL.revokeObjectURL(t.url);
      return prev.filter((a) => a.id !== id);
    });
  };

  const pushHistory = (q: string) => {
    if (!q) return;
    setHistory((prev) => {
      const next = [q, ...prev.filter((x) => x !== q)].slice(0, 50);
      try {
        localStorage.setItem("baomi.history", JSON.stringify(next));
      } catch {
        /* 存不下就不持久化，本次会话内仍可用 */
      }
      return next;
    });
  };

  // dir = -1 往上翻（更早/更新的一条），dir = 1 往下翻回草稿
  const recallHistory = (dir: -1 | 1) => {
    if (history.length === 0) return;
    let idx = histIdxRef.current;
    if (dir === -1) {
      if (idx === -1) {
        draftRef.current = input; // 第一次上翻，先把草稿留住
        idx = 0;
      } else if (idx < history.length - 1) {
        idx += 1;
      } else {
        return;
      }
    } else {
      if (idx <= -1) return;
      idx -= 1; // 回到 -1 即草稿
    }
    histIdxRef.current = idx;
    setInput(idx === -1 ? draftRef.current : history[idx]);
    // 回填后把光标放到末尾，否则长文本会停在开头
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  const submit = () => {
    const q = input.trim();
    if ((!q && pending.length === 0) || sending) return;
    const atts = pending;
    setInput("");
    setPending([]);
    pushHistory(q);
    histIdxRef.current = -1;
    draftRef.current = "";
    // 自己发的内容一定要看到，重置为贴底跟随
    stickRef.current = true;
    setAtBottom(true);
    if (isPlanRequest(q)) {
      const topic = detectPlanTopic(q);
      onSend(buildPlanPrompt(q, topic), atts, q);
      return;
    }
    onSend(q, atts);
  };

  const fillExample = (q: string) => {
    setInput(q);
    inputRef.current?.focus();
  };

  // 首屏四宫格：直接切到对应模式并预填示例，省掉「先去顶部切模式」这一步
  const startAbility = (m: ChatMode, q: string) => {
    onModeChange(m);
    setInput(q);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  // 进料路径②：让它带着题目去联网研究——光标留在末尾，等用户补上题目
  const startResearch = () => {
    setInput("帮我研究一下：");
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  // 进料路径③：用户自己给资料——把资料库面板推到他面前
  const openUpload = () => {
    if (kbPanelOpen) return;
    onToggleKbPanel?.();
  };

  const handleForward = useCallback((text: string) => {
    setInput(text);
    inputRef.current?.focus();
  }, []);

  // 用 useCallback 固定引用，否则每次渲染都会让 Bubble 的 memo 失效
  const handleCopyFail = useCallback(
    () => onNotice?.("error", "复制失败，请手动选择文本"),
    [onNotice]
  );
  const handleFeedback = useCallback(
    () => onNotice?.("success", "已记录你的反馈"),
    [onNotice]
  );
  // 从引用角标跳到全文预览：引用块只给文件名，kb 用当前会话绑定的那个
  const openPreviewFile = useCallback((source: string) => setPreviewFile(source), []);

  return (
    <section className="book-spine flex h-full min-w-0 flex-1 flex-col">
      {/* 顶栏 */}
      <div className="flex items-center gap-2 border-b border-line px-5 py-3">
        {onOpenSidebar && (
          <button
            onClick={onOpenSidebar}
            title="打开会话列表"
            aria-label="打开会话列表"
            className="icon-btn hamburger -ml-1 shrink-0"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          </button>
        )}
        <span className="truncate font-serif text-sm font-medium text-ink">
          {sessionTitle ?? "新对话"}
        </span>
        {boundKb && (
          <span
            className="kb-chip min-w-0 max-w-[44vw]"
            title={kbNameMap?.[boundKb] ?? boundKb}
          >
            <span className="dot" />
            <span className="kb-chip-name min-w-0 truncate">
              已绑定：{kbNameMap?.[boundKb] ?? boundKb}
            </span>
            {onUnbindKb && (
              <button
                onClick={onUnbindKb}
                title="解绑知识库"
                aria-label="解绑知识库"
                className="kb-unbind ml-0.5 text-faint transition hover:text-red"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" className="h-3 w-3" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            )}
          </span>
        )}
        <div className="flex-1" />
        {/* 顶栏能力快捷入口：对话进行中不用滚回欢迎页，点一下就切模式 + 预填示例 */}
        <button
          type="button"
          onClick={() => setAbOpen((o) => !o)}
          title="常用能力"
          aria-label="常用能力"
          aria-haspopup="true"
          aria-expanded={abOpen}
          className="ability-trigger"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 2.5l1.7 5 5 1.7-5 1.7L12 15.9l-1.7-5-5-1.7 5-1.7L12 2.5z" />
            <path d="M18.5 14.5l.8 2.4 2.4.8-2.4.8-.8 2.4-.8-2.4-2.4-.8 2.4-.8.8-2.4z" />
          </svg>
          <span>能力</span>
        </button>
        {abOpen && (
          <>
            <div className="pop-backdrop" onClick={() => setAbOpen(false)} aria-hidden="true" />
            <div className="ability-pop" role="menu" aria-label="常用能力">
              {ABILITIES.map((a) => (
                <button
                  key={a.label}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    startAbility(a.mode, a.example);
                    setAbOpen(false);
                  }}
                  className="ability-pop-item"
                >
                  <span className="ab-pop-icon">{a.icon}</span>
                  <span className="ab-pop-text">
                    <span className="ab-pop-label">{a.label}</span>
                    <span className="ab-pop-hint">{a.hint}</span>
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
        {onToggleKbPanel && (
          <button
            onClick={onToggleKbPanel}
            title={kbPanelOpen ? "收起资料库" : "展开资料库"}
            aria-label={kbPanelOpen ? "收起资料库" : "展开资料库"}
            aria-expanded={!!kbPanelOpen}
            className="icon-btn"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
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

      {/* 消息区 */}
      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto">
          <div className="chat-measure mx-auto flex min-h-full flex-col px-6 pb-4 pt-6">
          {messages.length === 0 ? (
            // 欢迎页：主路径是「直接问」，进料（研究/上传）只做被弱化的第二行
            <div className="welcome">
              <img
                src="/corn-logo.webp"
                alt=""
                width={72}
                height={72}
                decoding="async"
                className="corn-logo hero-logo mx-auto mb-4 h-[72px] w-[72px] object-contain"
              />
              <div className="eyebrow mb-2">苞米地 · 智能体工作台</div>
              <h1 className="hero-title font-serif font-bold leading-snug text-ink">
                {greeting}，我是<span className="hero-name">苞米</span>
              </h1>
              <p className="mt-3 max-w-[30rem] text-msg leading-relaxed text-muted">
                一句话就行。我会自己决定——是查你的资料、上网搜，还是算一下。
              </p>

              {/* 四宫格能力卡片：呼应设计稿，点一下直接进对应模式并预填一句能立刻发的话 */}
              <div className="mt-6 grid w-full grid-cols-2 gap-2.5 sm:grid-cols-4">
                {ABILITIES.map((a) => (
                  <button
                    key={a.label}
                    type="button"
                    onClick={() => startAbility(a.mode, a.example)}
                    className="ability-card"
                    aria-label={`${a.label}：${a.hint}`}
                  >
                    <span className="ab-icon">{a.icon}</span>
                    <span className="ab-label">{a.label}</span>
                    <span className="ab-hint">{a.hint}</span>
                  </button>
                ))}
              </div>

              {/* 次级示例：三句能立刻点的话，三列等宽便签；min-w-0 让长句在卡内截断不溢出 */}
              <div className="mt-4 grid w-full grid-cols-1 gap-2 sm:grid-cols-3">
                {EXAMPLES.map((e) => (
                  <button
                    key={e.q}
                    onClick={() => fillExample(e.q)}
                    className="welcome-eg"
                    title={e.q}
                  >
                    <span className="welcome-eg-hint">{e.hint}</span>
                    <span className="q">{e.q}</span>
                  </button>
                ))}
              </div>

              <p className="mt-4 text-fs-md text-faint">
                或者：
                <button type="button" onClick={startResearch} className="welcome-link">
                  让它研究一个题目
                </button>
                <span className="welcome-sep">·</span>
                <button type="button" onClick={openUpload} className="welcome-link">
                  上传你的资料
                </button>
              </p>

              <p className="welcome-foot">问完的每一句，都能翻回原文。</p>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m, idx) => {
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
                  // 错误边界：一条坏消息渲染抛错时只降级这一条，不白屏整个会话
                  <BubbleErrorBoundary key={m.id}>
                    <Bubble
                      m={m}
                      userQuestion={userQ}
                      onForward={handleForward}
                      onCopyFail={handleCopyFail}
                      onFeedback={handleFeedback}
                      onRetry={onRetry}
                      onPreviewFile={boundKb ? openPreviewFile : undefined}
                      onDeleteTurn={onDeleteTurn}
                    />
                  </BubbleErrorBoundary>
                );
              })}
            </div>
          )}
          </div>
        </div>
        {!atBottom && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="scroll-down"
            aria-label="回到最新消息"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 5v14" />
              <path d="M19 12l-7 7-7-7" />
            </svg>
            <span>回到最新</span>
          </button>
        )}
      </div>

      {/* 参数面板 */}
      {showParams && (
        <div className="chat-measure mx-auto px-6 pb-2">
          <div className="fade-up grid grid-cols-3 gap-4 rounded-xl border border-line-strong bg-panel p-4">
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

      {/* 底部：模式切换 + 输入框 */}
      <div className="chat-measure mx-auto px-6 pb-5">
        <div className="mb-2 flex items-center gap-3">
          <div className="mode-tabs" role="group" aria-label="对话模式" ref={modeTabsRef}>
            <span
              className="mode-thumb"
              aria-hidden="true"
              style={{
                transform: `translateX(${thumb.x}px)`,
                width: thumb.w,
                opacity: thumb.ready ? 1 : 0,
              }}
            />
            {MODE_LABELS.map((m) => (
              <button
                key={m.value}
                ref={(el) => {
                  modeBtnRefs.current[m.value] = el;
                }}
                onClick={() => onModeChange(m.value)}
                title={m.hint}
                aria-pressed={mode === m.value}
                className={`mode ${mode === m.value ? "active" : ""}`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setShowParams((s) => !s)}
            title="检索参数设置"
            aria-label="检索参数设置"
            aria-expanded={showParams}
            className={`gear ml-auto ${showParams ? "text-accent" : ""}`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 1v6M12 17v6M4.2 4.2l4.3 4.3M15.5 15.5l4.3 4.3M1 12h6M17 12h6M4.2 19.8l4.3-4.3M15.5 8.5l4.3-4.3" />
            </svg>
          </button>
        </div>

        {pending.length > 0 && (
          <div className="attach-strip">
            {pending.map((a) => (
              <div key={a.id} className="attach-item">
                {a.type.startsWith("image/") ? (
                  <img src={a.url} alt={a.name} />
                ) : (
                  <span className="attach-file-badge">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                      <path d="M8 13h8M8 17h5" />
                    </svg>
                  </span>
                )}
                <span className="max-w-[150px] truncate">{a.name}</span>
                <button
                  onClick={() => removeAttachment(a.id)}
                  title="移除附件"
                  aria-label={`移除附件：${a.name}`}
                  className="attach-rm"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                    <path d="M6 6l12 12M18 6L6 18" />
                  </svg>
                </button>
              </div>
            ))}
            <p className="attach-note">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 16v-4M12 8h.01" />
              </svg>
              {pendingOtherCount > 0
                ? `仅图片会参与回答（${pendingImageCount} 张）；其余 ${pendingOtherCount} 个文件暂不支持解析`
                : `${pendingImageCount} 张图片将上传，由视觉模型结合问题一起分析`}
            </p>
          </div>
        )}

        <div className="input-row">
          <button
            onClick={() => attachRef.current?.click()}
            title="上传附件（图片会由视觉模型分析）"
            aria-label="上传附件，图片会由视觉模型分析"
            className="attach"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M21.4 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <input
            ref={attachRef}
            type="file"
            multiple
            // 后端只有视觉模型会消费附件，非图片格式上传了也不会参与回答，
            // 与其让用户选完才发现没用，不如在选择器层面就只放行图片
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const fs = e.target.files;
              if (fs && fs.length > 0) addFiles(fs);
              if (attachRef.current) attachRef.current.value = "";
            }}
          />
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => {
              const items = e.clipboardData?.items;
              if (!items) return;
              const files: File[] = [];
              for (const it of Array.from(items)) {
                if (it.kind === "file") {
                  const f = it.getAsFile();
                  if (f) files.push(f);
                }
              }
              if (files.length > 0) {
                e.preventDefault();
                addFiles(files);
              }
            }}
            onKeyDown={(e) => {
              // 输入法组合期间的按键是"选候选词"，一律不拦
              if ((e.nativeEvent as KeyboardEvent).isComposing) return;
              const el = e.currentTarget;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
                return;
              }
              if (e.key === "Escape") {
                if (input) {
                  e.preventDefault();
                  histIdxRef.current = -1;
                  setInput("");
                } else if (pending.length > 0) {
                  e.preventDefault();
                  setPending((prev) => {
                    prev.forEach((a) => URL.revokeObjectURL(a.url));
                    return [];
                  });
                }
                return;
              }
              if (e.key === "ArrowUp" && !e.shiftKey) {
                // 光标在开头（或输入框是空的）才取历史，否则交给 textarea 正常移动光标
                if (input === "" || (el.selectionStart === 0 && el.selectionEnd === 0)) {
                  e.preventDefault();
                  recallHistory(-1);
                }
                return;
              }
              if (e.key === "ArrowDown" && !e.shiftKey) {
                if (el.selectionStart === input.length && el.selectionEnd === input.length) {
                  e.preventDefault();
                  recallHistory(1);
                }
              }
            }}
            rows={1}
            placeholder="随便问点什么…"
            aria-label="输入问题"
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-2.5 text-msg leading-relaxed text-text outline-none placeholder:text-faint"
          />
          {sending ? (
            <button onClick={onStop} title="停止生成" aria-label="停止生成" className="send stop">
              <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!input.trim() && pending.length === 0}
              title="发送"
              aria-label="发送"
              className="send"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5" />
                <path d="M5 12l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>

        {/* 键盘提示从 placeholder 里挪出来：placeholder 要短，才像"随便问点什么" */}
        <div className="input-hint">
          <span className="truncate">{currentMode?.hint}</span>
          <span className="ml-1 shrink-0 whitespace-nowrap">Enter 发送 · ↑ 上一条 · Esc 清空</span>
        </div>
      </div>

      {previewFile && boundKb && (
        <FilePreviewModal
          kbId={boundKb}
          name={previewFile}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </section>
  );
}
