"use client";

// 中央对话区：书本打开效果 + 欢迎页 + 底部输入框
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
//
// 2026-09 重构：消息类型/常量/正文渲染/计划卡片/Bubble/Field 拆到 ./chat/*，
// 本文件只保留主组件（顶栏 + 欢迎页 + 消息区 + 输入区）。
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import MascotLogo from "./chat/MascotLogo";
import CornIcon from "./CornIcon";
import { api } from "@/lib/api";
import type { ChatMode, RefSource, QuotaInfo } from "@/lib/types";
import { ABILITIES, EXAMPLES, MODE_LABELS, SCENARIOS, INTRO_ENTRIES, TOOLS_URL, SCENARIO_STARTERS } from "./chat/constants";
import { buildPlanPrompt, detectPlanTopic, isPlanRequest, stripPlanHint } from "./chat/plan";
import Bubble from "./chat/Bubble";
import BubbleErrorBoundary from "./chat/BubbleErrorBoundary";
import type { ChatAttachment, UiMessage } from "./chat/types";
import FilePreviewModal from "./FilePreviewModal";

// 兼容导出：page.tsx 仍从 "@/components/ChatArea" 引入这些符号
export type { UiMessage, ChatAttachment } from "./chat/types";
export { stripPlanHint } from "./chat/plan";

const aid = () => Math.random().toString(36).slice(2, 10);

/**
 * 检索条数也换成白话档位。
 * 「topK = 5」没人看得懂，「参考 5 段资料」一看就明白，
 * 顺带把「多参考 = 更全但更慢」的取舍讲清楚。
 */
const TOP_K_MODES = [
  { value: 3, hint: "更快" },
  { value: 5, hint: "均衡" },
  { value: 8, hint: "更全" },
] as const;

/** 顶栏胶囊文案：管理员显示「无限额」，否则「问答 x/y · 向量化 a/b」 */
function quotaLine(q: Record<string, QuotaInfo>): string {
  if (Object.values(q).some((x) => x.unlimited)) return "无限额 · 管理员";
  const fmt = (n: number) => (n === 0 ? "∞" : String(n));
  const parts: string[] = [];
  if (q["ask"]) parts.push(`问答 ${q["ask"].used}/${fmt(q["ask"].limit)}`);
  if (q["embed"]) parts.push(`向量化 ${q["embed"].used}/${fmt(q["embed"].limit)}`);
  return parts.join(" · ") || "用量";
}

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
  /** 检索条数变化。资料怎么切片是「建库时」的事，在右侧资料库面板按库设置 */
  onTopKChange: (v: number) => void;
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
  /** 地理定位开关状态（开启后发送消息自动带坐标） */
  geoEnabled?: boolean;
  /** 当前定位城市（角标展示用，可能为 null） */
  geoCity?: string | null;
  /** 当前会话选中的场景预设（null 表示未选）；轻量版仅前端保存 */
  scenario?: string | null;
  /** 切换场景预设，传 null 表示取消选择 */
  onScenarioChange?: (id: string | null) => void;
  /** 定位请求进行中：等授权 + IP 兜底最长要 18 秒，期间按钮禁用并转圈 */
  geoPending?: boolean;
  /** 切换定位开关（请求权限 / 关闭），由页面层处理权限与缓存 */
  onToggleGeo?: () => void;
  /** 当前会话 id：赞踩要按「会话 + 消息 id」落库，否则后端无从归档 */
  sessionId?: string | null;
  /** 当前账号配额概览：顶栏常驻胶囊用它显示今日用量 */
  quota?: Record<string, QuotaInfo>;
  /** 打开「用量与反馈」看板；不传则不显示顶栏胶囊 */
  onOpenUsage?: () => void;
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
  onTopKChange,
  sessionTitle,
  boundKb,
  kbNameMap,
  onUnbindKb,
  kbPanelOpen,
  onToggleKbPanel,
  onOpenSidebar,
  prefill,
  onDeleteTurn,
  geoEnabled = false,
  geoCity = null,
  geoPending = false,
  onToggleGeo,
  sessionId = null,
  scenario = null,
  onScenarioChange,
  quota,
  onOpenUsage,
}: Props) {
  const [input, setInput] = useState("");
  const [showParams, setShowParams] = useState(false);
  // 「自己填」展开：想直接敲数字的少数人才会点
  const [advancedTopK, setAdvancedTopK] = useState(false);
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

  // 首屏场景快选：选中场景预设 + 预填一句开场白（轻量版场景仅前端保存）
  const startScenario = (id: string) => {
    onScenarioChange?.(id);
    const starter = SCENARIO_STARTERS[id] ?? "";
    setInput(starter);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  // 首屏功能入口：模板广场 / 校园工具集 / 上传建库 / 让它研究
  const handleIntroEntry = (key: string) => {
    switch (key) {
      case "template":
        window.location.href = "/templates";
        break;
      case "tools":
        window.open(TOOLS_URL, "_blank", "noopener,noreferrer");
        break;
      case "upload":
        openUpload();
        break;
      case "research":
        startResearch();
        break;
    }
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
  // 赞踩落库：失败不影响界面（本地高亮已经点了），只提示一声让用户知道没存上
  const handleFeedback = useCallback(
    async (msgDbId: number, rating: "up" | "down", comment?: string) => {
      if (!sessionId) return;
      try {
        await api.sendFeedback(sessionId, msgDbId, rating, comment);
        onNotice?.(
          "success",
          rating === "up"
            ? "已记录：这条回答有帮助"
            : comment
              ? "已记录：谢谢你的补充，我会用来看板里复盘"
              : "已记录：这条回答不太行"
        );
      } catch {
        onNotice?.("error", "反馈没能保存，稍后可再点一次");
      }
    },
    [sessionId, onNotice]
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
        {/* 顶栏用量入口：常驻显示今日额度，点开看板。
            侧栏那个入口太折叠，用户找不到——这里给一个一眼可见的。 */}
        {onOpenUsage && (
          <button
            type="button"
            onClick={onOpenUsage}
            className="usage-chip"
            title="查看用量与反馈"
            aria-label="查看用量与反馈"
          >
            <CornIcon name="target" className="h-3.5 w-3.5 shrink-0" title="用量" />
            <span className="usage-chip-text">{quota ? quotaLine(quota) : "用量"}</span>
          </button>
        )}
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
            // 欢迎页：主路径是「直接问」，但首屏先把当前主打能力摆出来——
            // ① 场景快选（考研/笔记/论文）② 功能入口（模板/工具集/建库/研究）③ 三句示例
            <div className="welcome">
              <MascotLogo
                className="hero-logo mx-auto mb-2 h-[52px] w-[52px] object-contain"
                mood={messages.some((m) => m.streaming) ? "thinking" : "idle"}
                tiltKey={mode}
              />
              <div className="eyebrow mb-1.5">苞米地 · 智能体工作台</div>
              <h1 className="hero-title font-serif font-bold leading-snug text-ink">
                {greeting}，我是<span className="hero-name">知穗</span>
              </h1>
              <p className="mt-1.5 max-w-[30rem] text-msg leading-relaxed text-muted">
                一句话就行。选个场景，或直接问我——我会自己决定怎么帮你。
              </p>

              {/* ① 场景快选：当前主打能力，点一下进对应场景并预填开场白 */}
              <div className="mt-4 w-full">
                <div className="welcome-section-head">
                  <span className="eyebrow">选个场景开始</span>
                  <span className="rule" />
                </div>
                <div className="mt-2.5 flex w-full flex-wrap justify-center gap-2">
                  {SCENARIOS.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => startScenario(s.id)}
                      className="scenario-pick"
                      title={s.desc}
                    >
                      <CornIcon name={s.id === "kaoyan" ? "book" : s.id === "notes" ? "notes" : "pen"} className="h-5 w-5" title={s.label} />
                      <span className="sp-label">{s.label}</span>
                      <span className="sp-desc">{s.desc}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* ② 功能入口：替代旧四能力卡，反映当前真实功能 */}
              <div className="mt-4 w-full grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {INTRO_ENTRIES.map((e) => (
                  <button
                    key={e.key}
                    type="button"
                    onClick={() => handleIntroEntry(e.key)}
                    className="intro-entry"
                    aria-label={`${e.label}：${e.hint}`}
                  >
                    <span className="ie-icon">
                      <CornIcon name={e.icon} className="h-6 w-6" title={e.label} />
                    </span>
                    <span className="ie-text">
                      <span className="ie-label">{e.label}</span>
                      <span className="ie-hint">{e.hint}</span>
                    </span>
                  </button>
                ))}
              </div>

              {/* ③ 次级示例：三句能立刻点的话，三列等宽便签；min-w-0 让长句在卡内截断不溢出 */}
              <div className="mt-3 grid w-full grid-cols-1 gap-2 sm:grid-cols-3">
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

      {/* 检索设置：只留「参考几段资料」。
          切片参数是建库时的事，已经移到右侧资料库面板按库单独设置 */}
      {showParams && (
        <div className="chat-measure mx-auto px-6 pb-2">
          <div className="fade-up rounded-xl border border-line-strong bg-panel p-4">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-medium text-ink">每次回答参考几段资料？</span>
              <button
                type="button"
                onClick={() => setAdvancedTopK((a) => !a)}
                aria-expanded={advancedTopK}
                className="shrink-0 text-xs text-faint transition hover:text-ink"
              >
                {advancedTopK ? "收起" : "自己填"}
              </button>
            </div>

            <div className="mt-2 grid grid-cols-3 gap-2" role="group" aria-label="参考资料段数">
              {TOP_K_MODES.map((m) => {
                const on = topK === m.value;
                return (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => onTopKChange(m.value)}
                    aria-pressed={on}
                    className={`flex flex-col items-center gap-px rounded-lg border py-2 transition ${
                      on
                        ? "border-accent bg-accent-soft"
                        : "border-line bg-bg hover:border-line-strong hover:bg-fill-2"
                    }`}
                  >
                    <span className="text-sm font-medium text-ink">{m.value} 段</span>
                    <span className="text-fs-badge text-faint">{m.hint}</span>
                  </button>
                );
              })}
            </div>

            <p className="mt-2 text-xs text-faint">
              参考得多，答得全但更慢；参考得少，答得快，但可能漏掉资料里的细节。
            </p>

            {advancedTopK && (
              <label className="mt-2 flex items-center gap-2 rounded-lg border border-line bg-fill-3 px-2 py-1.5">
                <span className="text-fs-badge text-muted">精确段数</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={topK}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (!Number.isNaN(v)) onTopKChange(v);
                  }}
                  className="underline-input w-16 py-0.5 text-xs"
                />
              </label>
            )}

            <p className="mt-2 border-t border-line pt-2 text-xs text-faint">
              资料怎么分段、切多细，在右侧「资料库」里按每个库单独设置。
            </p>
          </div>
        </div>
      )}

      {/* 底部：场景预设 + 模式切换 + 输入框 */}
      <div className="chat-measure mx-auto px-6 pb-5">
        {/* 场景预设：考研复习 / 课程笔记 / 论文写作（轻量版仅前端保存，发送时带入请求） */}
        <div className="mb-2 flex items-center gap-2 flex-wrap">
          <span className="scenario-label">场景</span>
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onScenarioChange?.(scenario === s.id ? null : s.id)}
              aria-pressed={scenario === s.id}
              title={s.desc}
              className={`scenario ${scenario === s.id ? "active" : ""}`}
            >
              {s.label}
            </button>
          ))}
          {/* 校园工具集：从侧栏搬来（10-14）。
              它是「发消息之前顺手用一下」的入口，挂在对话栏这一行才够得着使用场景；
              放侧栏等于让它跟会话列表抢同一条视线。靠右、与 .scenario 同一族的胶囊外形，
              所以看得见但不跟场景抢主次 —— 场景是当前会话的属性（会高亮），它只是个外链。 */}
          <a
            href={TOOLS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="tools-link"
            title="校园工具集：GPA 计算 · 番茄钟 · 课表……（免登录，独立页）"
          >
            <CornIcon name="tools" className="h-[15px] w-[15px]" />
            校园工具集
          </a>
        </div>
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

        {geoEnabled && (
          <div className="geo-badge" role="status" aria-live="polite">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 21s7-6.4 7-11a7 7 0 1 0-14 0c0 4.6 7 11 7 11z" />
              <circle cx="12" cy="10" r="2.6" />
            </svg>
            <span>
              {geoCity ? `定位已开启 · 当前 ${geoCity}` : "定位已开启"}
            </span>
          </div>
        )}

        <div className="input-row">
          <button
            onClick={onToggleGeo}
            disabled={geoPending}
            title={
              geoPending
                ? "正在获取定位…"
                : geoEnabled
                  ? "定位已开启：发送时附带你的位置"
                  : "开启定位：问天气无需手输城市"
            }
            aria-label={geoPending ? "正在获取定位" : geoEnabled ? "关闭定位" : "开启定位"}
            aria-pressed={geoEnabled}
            aria-busy={geoPending}
            className={`geo-btn ${geoEnabled ? "on" : ""} ${geoPending ? "busy" : ""}`}
          >
            {/* 定位针：开启时填充实心，关闭时空心，与「+ 我的文件」同一套线条语言 */}
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 21s7-6.4 7-11a7 7 0 1 0-14 0c0 4.6 7 11 7 11z" />
              <circle cx="12" cy="10" r="2.6" />
            </svg>
          </button>
          <button
            onClick={() => attachRef.current?.click()}
            title="上传附件（图片会由视觉模型分析）"
            aria-label="上传附件，图片会由视觉模型分析"
            className="attach"
          >
            {/* 加号：与「+ 新建对话 / + 我的文件」同一套进料语义，替代原回形针 */}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
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
