"use client";

// 左侧会话列表栏：小头像 + 标题 + 纯文字会话行 + 深色模式开关
import { useEffect, useState } from "react";
import type { Conversation } from "@/lib/types";

interface Props {
  sessions: Conversation[];
  activeSid: string | null;
  onSelect: (sid: string) => void;
  onCreate: () => void;
  onRename: (sid: string, title: string) => void;
  onDelete: (sid: string) => void;
}

export default function Sidebar({
  sessions,
  activeSid,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dark, setDark] = useState(false);

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

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-line bg-bg px-3 py-4">
      {/* 头部：小圆头像 + 标题 + 副标题 */}
      <div className="flex items-center gap-2.5 px-1">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-white">
          <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5">
            <path d="M12 2l2.3 7.7L22 12l-7.7 2.3L12 22l-2.3-7.7L2 12l7.7-2.3z" />
          </svg>
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold text-text">集合式 Agent</h1>
          <p className="truncate text-xs text-muted">多知识库 · 多对话 · 智能体</p>
        </div>
      </div>

      {/* 新建对话（主按钮，accent） */}
      <button
        onClick={onCreate}
        className="mt-4 flex items-center justify-center gap-1 rounded-full bg-accent py-2 text-sm font-semibold text-white transition hover:opacity-90"
      >
        <span className="text-base leading-none">＋</span> 新建对话
      </button>

      {/* 会话列表：纯文字行，选中浅粉紫底 */}
      <nav className="mt-3 flex flex-1 flex-col gap-0.5 overflow-y-auto pr-1">
        {sessions.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-muted">还没有会话，点上方新建</p>
        )}
        {sessions.map((c) => {
          const active = c.id === activeSid;
          return (
            <div
              key={c.id}
              onClick={() => onSelect(c.id)}
              className={`group relative cursor-pointer rounded-lg px-3 py-2 transition ${
                active ? "bg-accent-soft" : "hover:bg-subtle"
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
                        active ? "font-semibold text-accent" : "text-text"
                      }`}
                    >
                      {c.title}
                    </span>
                    <span className="flex shrink-0 gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                      <button
                        title="重命名"
                        onClick={(e) => {
                          e.stopPropagation();
                          startEdit(c);
                        }}
                        className="rounded px-1 text-xs text-faint hover:text-text"
                      >
                        改
                      </button>
                      <button
                        title="删除"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(c.id);
                        }}
                        className="rounded px-1 text-xs text-faint hover:text-rose-500"
                      >
                        删
                      </button>
                    </span>
                  </div>
                  {c.kb_id && (
                    <span className="mt-0.5 block truncate text-xs text-faint">
                      资料库 · {c.kb_id}
                    </span>
                  )}
                </>
              )}
            </div>
          );
        })}
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
        <div className="flex items-center gap-1.5 px-1 text-xs text-muted">
          <span className="h-2 w-2 rounded-full bg-green-500" />
          deepseek-chat
        </div>
      </div>
    </aside>
  );
}