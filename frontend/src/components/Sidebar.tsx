"use client";

// 左侧会话列表栏：品牌区 + 新建对话 + 搜索框 + 按时间分组的会话 + 深色模式开关
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
import { useMemo, useState, type RefObject } from "react";
import MascotLogo from "./chat/MascotLogo";
import type { Conversation } from "@/lib/types";
import type { FontScale } from "@/lib/hooks/usePrefs";

interface Props {
  sessions: Conversation[];
  activeSid: string | null;
  onSelect: (sid: string) => void;
  onCreate: () => void;
  onRename: (sid: string, title: string) => void;
  onDelete: (sid: string) => void;
  kbNameMap?: Record<string, string>;
  dark?: boolean;
  onToggleDark?: () => void;
  /** 聊天正文字号档位（小/标准/大），持久化在 prefs 里 */
  fontScale?: FontScale;
  onFontScaleChange?: (f: FontScale) => void;
  /** 窄屏下作为浮层抽屉渲染 */
  overlay?: boolean;
  /** 抽屉是否展开（仅 overlay 时有意义） */
  open?: boolean;
  /** 抽屉容器引用，用于打开时接管焦点 */
  containerRef?: RefObject<HTMLElement | null>;
  /** 首屏还在拉会话列表：渲染骨架，避免先闪一下空态 */
  loading?: boolean;
  /** 拉取失败时的提示文案 */
  loadError?: string | null;
  /** 失败后手动重试 */
  onRetryLoad?: () => void;
}

const toMs = (ts: number) => (ts < 1e12 ? ts * 1000 : ts);

function groupSessions(list: Conversation[]) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 3600 * 1000;
  const buckets: { label: string; items: Conversation[] }[] = [
    { label: "今天", items: [] },
    { label: "昨天", items: [] },
    { label: "近 7 天", items: [] },
    { label: "更早", items: [] },
  ];
  for (const c of list) {
    const t = toMs(c.updated_at || c.created_at || 0);
    if (t >= startOfToday) buckets[0].items.push(c);
    else if (t >= startOfToday - day) buckets[1].items.push(c);
    else if (t >= startOfToday - 7 * day) buckets[2].items.push(c);
    else buckets[3].items.push(c);
  }
  return buckets.filter((b) => b.items.length > 0);
}

export default function Sidebar({
  sessions,
  activeSid,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  kbNameMap,
  dark = false,
  onToggleDark,
  fontScale = "m",
  onFontScaleChange,
  overlay = false,
  open = false,
  containerRef,
  loading = false,
  loadError = null,
  onRetryLoad,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  // 删除是破坏性操作，点一下先进确认态，避免误删整段对话
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);

  const startEdit = (c: Conversation) => {
    setEditingId(c.id);
    setDraft(c.title);
  };

  const commitEdit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? sessions.filter((c) => c.title.toLowerCase().includes(q))
      : sessions;
    return groupSessions(filtered);
  }, [sessions, query]);

  return (
    <aside
      ref={containerRef}
      tabIndex={-1}
      className={`side-left flex h-full shrink-0 flex-col px-4 py-5 ${open ? "open" : ""}`}
      inert={overlay && !open}
    >
      {/* 品牌区：玉米穗 + 衬线标题 */}
      <div className="flex items-center gap-2.5 px-1">
        <MascotLogo className="h-9 w-9 shrink-0 object-contain" />
        <div className="min-w-0">
          <h1 className="truncate font-serif text-fs-xl font-bold leading-tight text-ink">
            苞米Agent
          </h1>
          <p className="brand-sub truncate">知识库 · 多对话 · 智能体</p>
        </div>
      </div>

      {/* 新建对话：深色圆角按钮 */}
      <button onClick={onCreate} className="btn-new mt-5 w-full">
        ＋ 新建对话
      </button>

      {/* 搜索框：大圆角 */}
      <div className="search mt-4">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索对话..."
          aria-label="搜索对话"
        />
      </div>

      {/* 会话列表 */}
      <nav className="mt-3 flex flex-1 flex-col overflow-y-auto pr-1" aria-label="会话列表" aria-busy={loading}>
        {loading && (
          <div className="flex flex-col gap-2 px-1 pt-1" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="skel-block">
                <span className="skel-line" />
                <span className="skel-line short" />
              </div>
            ))}
          </div>
        )}
        {!loading && loadError && (
          <div className="px-2 py-6 text-center">
            <p className="text-sm text-red-ink">{loadError}</p>
            {onRetryLoad && (
              <button onClick={onRetryLoad} className="btn-import mt-2 px-3 py-1 text-xs">
                重试
              </button>
            )}
          </div>
        )}
        {!loading && !loadError && sessions.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-muted">还没有会话，点上方新建</p>
        )}
        {!loading && !loadError && sessions.length > 0 && grouped.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-muted">没有匹配「{query}」的会话</p>
        )}
        {grouped.map((g) => (
          <div key={g.label}>
            <div className="group-head">
              <span className="eyebrow">{g.label}</span>
              <span className="rule" />
            </div>
            {g.items.map((c) => {
              const active = c.id === activeSid;
              return (
                <div
                  key={c.id}
                  role="button"
                  tabIndex={0}
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(c.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(c.id);
                    }
                  }}
                  className={`session group ${active ? "active" : ""}`}
                >
                  {editingId === c.id ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={commitEdit}
                      onKeyDown={(e) => e.key === "Enter" && commitEdit()}
                      className="w-full rounded-lg border border-line bg-panel px-2 py-1 text-sm text-ink outline-none focus:border-accent"
                      aria-label="会话标题"
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <>
                      <div className="flex items-center justify-between gap-2">
                        <span className="session-name">{c.title}</span>
                        <span className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                          <button
                            title="重命名"
                            aria-label={`重命名会话：${c.title}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              startEdit(c);
                            }}
                            className="rounded p-0.5 text-faint transition-colors hover:text-accent hover:bg-fill-2"
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5">
                              <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                            </svg>
                          </button>
                          <button
                            title="删除"
                            aria-label={`删除会话：${c.title}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDelId(c.id);
                            }}
                            className="rounded p-0.5 text-faint transition-colors hover:text-red hover:bg-red/10"
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5">
                              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                            </svg>
                          </button>
                        </span>
                      </div>
                      {c.kb_id && (
                        <div className="session-kb">
                          资料库 · {kbNameMap?.[c.kb_id] ?? c.kb_id}
                        </div>
                      )}
                      {confirmDelId === c.id && (
                        <div
                          className="mt-1.5 flex items-center gap-1.5"
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.stopPropagation();
                              setConfirmDelId(null);
                            }
                          }}
                        >
                          <span className="text-fs-xs text-red-ink">删除这条会话？</span>
                          <button
                            className="rounded border border-line px-1.5 py-0.5 text-fs-xs text-red-ink transition hover:border-red hover:bg-red hover:text-white"
                            onClick={() => {
                              onDelete(c.id);
                              setConfirmDelId(null);
                            }}
                          >
                            删除
                          </button>
                          <button
                            autoFocus
                            className="rounded border border-line px-1.5 py-0.5 text-fs-xs text-muted transition hover:bg-fill-2"
                            onClick={() => setConfirmDelId(null)}
                          >
                            取消
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 底部：字号调节 + 深色模式开关 + 在线状态 */}
      <div className="mt-3 border-t border-line pt-3">
        {onFontScaleChange && (
          <div className="fs-picker mb-2" role="group" aria-label="聊天字号">
            <span className="fs-picker-label">字号</span>
            <div className="fs-picker-opts">
              {(
                [
                  { v: "s", label: "小", title: "" },
                  { v: "m", label: "标准", title: "" },
                  { v: "l", label: "大", title: "" },
                  { v: "auto", label: "系统", title: "跟随浏览器默认字号设置" },
                ] as const
              ).map((o) => (
                <button
                  key={o.v}
                  onClick={() => onFontScaleChange(o.v)}
                  aria-pressed={fontScale === o.v}
                  title={o.title || undefined}
                  className={fontScale === o.v ? "active" : ""}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {onToggleDark && (
          <button
            onClick={onToggleDark}
            title={dark ? "切换到浅色模式" : "切换到深色模式"}
            aria-label={dark ? "切换到浅色模式" : "切换到深色模式"}
            aria-pressed={dark}
            className="theme-chip mb-2 w-full justify-center"
          >
            {dark ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
              </svg>
            )}
            <span>{dark ? "浅色" : "深色"}</span>
          </button>
        )}
        <div className="status">
          <span className="dot" />
          <span className="truncate">在线 · deepseek-chat</span>
        </div>
      </div>
    </aside>
  );
}
