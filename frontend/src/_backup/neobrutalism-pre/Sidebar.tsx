"use client";

// 左侧会话列表栏：品牌区 + 新建对话 + 搜索框 + 按时间分组的会话 + 深色模式开关
import { useEffect, useMemo, useState } from "react";
import type { Conversation } from "@/lib/types";

interface Props {
  sessions: Conversation[];
  activeSid: string | null;
  onSelect: (sid: string) => void;
  onCreate: () => void;
  onRename: (sid: string, title: string) => void;
  onDelete: (sid: string) => void;
  kbNameMap?: Record<string, string>;
}

// 后端时间戳可能是秒或毫秒，统一转毫秒
const toMs = (ts: number) => (ts < 1e12 ? ts * 1000 : ts);

// 按更新时间分桶：今天 / 昨天 / 近 7 天 / 更早
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
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dark, setDark] = useState(false);
  const [query, setQuery] = useState("");

  // 深色模式：读取本地偏好，写入 <html> 的 .dark 类
  useEffect(() => {
    const saved = localStorage.getItem("theme");
    const isDark = saved
      ? saved === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDark(isDark);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  const startEdit = (c: Conversation) => {
    setEditingId(c.id);
    setDraft(c.title);
  };

  const commitEdit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  // 搜索过滤 + 分组
  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? sessions.filter((c) => c.title.toLowerCase().includes(q))
      : sessions;
    return groupSessions(filtered);
  }, [sessions, query]);

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-line bg-bg px-3 py-4">
      {/* 头部：logo + 标题 + 副标题 */}
      <div className="flex items-center gap-2.5 px-1">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white">
          <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5">
            <path d="M12 2l2.3 7.7L22 12l-7.7 2.3L12 22l-2.3-7.7L2 12l7.7-2.3z" />
          </svg>
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold text-text">集合式 Agent</h1>
          <p className="truncate text-xs text-muted">多知识库 · 多对话 · 智能体</p>
        </div>
      </div>

      {/* 新建对话（幽灵主按钮，降低视觉权重） */}
      <button
        onClick={onCreate}
        className="mt-4 flex items-center justify-center gap-1.5 rounded-xl bg-subtle py-2.5 text-sm font-medium text-text transition hover:bg-accent-soft hover:text-accent"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="h-4 w-4">
          <path d="M12 5v14M5 12h14" />
        </svg>
        新建对话
      </button>

      {/* 搜索框 */}
      <div className="relative mt-3">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索对话…"
          className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-2 text-sm text-text outline-none placeholder:text-faint focus:border-accent"
        />
      </div>

      {/* 会话列表：按时间分组 */}
      <nav className="mt-2 flex flex-1 flex-col gap-0.5 overflow-y-auto pr-1">
        {sessions.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-muted">还没有会话，点上方新建</p>
        )}
        {sessions.length > 0 && grouped.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-muted">没有匹配「{query}」的会话</p>
        )}
        {grouped.map((g) => (
          <div key={g.label}>
            <div className="px-3 pb-1 pt-2 text-[11px] font-medium text-faint">{g.label}</div>
            {g.items.map((c) => {
              const active = c.id === activeSid;
              return (
                <div
                  key={c.id}
                  onClick={() => onSelect(c.id)}
                  className={`group relative cursor-pointer rounded-lg py-2 transition ${
                    active
                      ? "border-l-2 border-accent pl-2.5 pr-3"
                      : "border-l-2 border-transparent px-3 hover:bg-subtle"
                  }`}
                >
                  {editingId === c.id ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={commitEdit}
                      onKeyDown={(e) => e.key === "Enter" && commitEdit()}
                      className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm text-text outline-none"
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <>
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={`truncate text-sm ${
                            active ? "font-title text-accent" : "text-text"
                          }`}
                        >
                          {c.title}
                        </span>
                        <span className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                          <button
                            title="重命名"
                            onClick={(e) => {
                              e.stopPropagation();
                              startEdit(c);
                            }}
                            className="rounded p-1 text-faint hover:bg-subtle hover:text-text"
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                              <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                            </svg>
                          </button>
                          <button
                            title="删除"
                            onClick={(e) => {
                              e.stopPropagation();
                              onDelete(c.id);
                            }}
                            className="rounded p-1 text-faint hover:bg-subtle hover:text-rose-500"
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                            </svg>
                          </button>
                        </span>
                      </div>
                      {c.kb_id && (
                        <span className="mt-0.5 block truncate text-xs text-faint">
                          资料库 · {kbNameMap?.[c.kb_id] ?? c.kb_id}
                        </span>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 底部：深色模式开关 + 当前模型 */}
      <div className="mt-3 space-y-3 border-t border-line pt-3">
        <button
          onClick={() => setDark((d) => !d)}
          className="flex w-full items-center justify-between px-1"
        >
          <span className="text-sm text-text">深色模式</span>
          <span
            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${
              dark ? "bg-accent" : "bg-subtle"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${
                dark ? "translate-x-4" : "translate-x-0.5"
              }`}
            />
          </span>
        </button>
        <div className="flex items-center gap-1.5 rounded-lg bg-surface px-2.5 py-1.5 text-xs text-muted">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-500 opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
          </span>
          <span className="truncate">在线 · deepseek-chat</span>
        </div>
      </div>
    </aside>
  );
}
