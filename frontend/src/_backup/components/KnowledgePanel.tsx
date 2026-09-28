"use client";

// 右侧资料库面板：窄栏、库列表一行式（库名 + N 片段 + 绑定/删除）+ 导入资料（折叠）
import { useRef, useState } from "react";
import type { KbInfo, SearchResult } from "@/lib/types";

interface Props {
  kbs: KbInfo[];
  boundKb: string | null;
  onUpload: (kbId: string, files: File[]) => Promise<void>;
  onSearch: (keyword: string) => Promise<SearchResult[]>;
  onImport: (kbId: string, urls: string[]) => Promise<void>;
  onDeleteKb: (kbId: string) => void;
  onBind: (kbId: string | null) => void;
  chunkSize: number;
  chunkOverlap: number;
}

export default function KnowledgePanel({
  kbs,
  boundKb,
  onUpload,
  onSearch,
  onImport,
  onDeleteKb,
  onBind,
  chunkSize,
  chunkOverlap,
}: Props) {
  const [showImport, setShowImport] = useState(false);
  const [uploadKb, setUploadKb] = useState("");
  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importKb, setImportKb] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const toggle = (href: string) => {
    const next = new Set(selected);
    if (next.has(href)) next.delete(href);
    else next.add(href);
    setSelected(next);
  };

  const doSearch = async () => {
    if (!keyword.trim()) return;
    setBusy(true);
    try {
      const r = await onSearch(keyword.trim());
      setResults(r);
      setSelected(new Set());
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    if (!importKb.trim() || selected.size === 0) return;
    setBusy(true);
    try {
      await onImport(importKb.trim(), Array.from(selected));
      setResults([]);
      setSelected(new Set());
      setImportKb("");
    } finally {
      setBusy(false);
    }
  };

  const doUpload = async (files: FileList | null) => {
    if (!files || files.length === 0 || !uploadKb.trim()) return;
    setBusy(true);
    try {
      await onUpload(uploadKb.trim(), Array.from(files));
      if (fileRef.current) fileRef.current.value = "";
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-l border-line bg-bg px-3 py-4">
      {/* 头部 */}
      <div className="flex items-center justify-between px-1">
        <h2 className="text-sm font-semibold text-text">资料库</h2>
        <button
          className="text-sm font-medium text-accent transition hover:opacity-80"
          onClick={() => setShowImport((s) => !s)}
        >
          ＋ 导入资料
        </button>
      </div>

      {/* 导入面板（默认折叠） */}
      {showImport && (
        <div className="fade-up mt-3 flex flex-col gap-2.5">
          {/* 上传文档 */}
          <div className="rounded-2xl border border-line bg-surface p-3.5">
            <h3 className="text-xs font-semibold text-muted">上传文档（Word / PDF / 记事本）</h3>
            <div className="mt-2 flex flex-col gap-2">
              <input
                value={uploadKb}
                onChange={(e) => setUploadKb(e.target.value)}
                placeholder="资料库名称（英文，如 nba）"
                className="rounded-lg border border-line bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
              />
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".txt,.md,.pdf"
                onChange={(e) => doUpload(e.target.files)}
                className="text-xs text-muted"
              />
              <p className="text-xs text-faint">
                分段 {chunkSize} 字 / 重叠 {chunkOverlap} 字
              </p>
            </div>
          </div>

          {/* 联网导入 */}
          <div className="rounded-2xl border border-line bg-surface p-3.5">
            <h3 className="text-xs font-semibold text-muted">联网搜索导入</h3>
            <div className="mt-2 flex gap-2">
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doSearch()}
                placeholder="搜索关键词"
                className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
              />
              <button
                className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40"
                onClick={doSearch}
                disabled={busy}
              >
                搜索
              </button>
            </div>

            {results.length > 0 && (
              <>
                <ul className="mt-3 max-h-56 space-y-1.5 overflow-y-auto pr-1">
                  {results.map((r) => (
                    <li key={r.href}>
                      <label className="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-subtle">
                        <input
                          type="checkbox"
                          checked={selected.has(r.href)}
                          onChange={() => toggle(r.href)}
                          className="mt-0.5"
                        />
                        <div className="min-w-0">
                          <div className="truncate font-semibold text-text">{r.title}</div>
                          <div className="line-clamp-2 text-xs text-muted">{r.body}</div>
                        </div>
                      </label>
                    </li>
                  ))}
                </ul>
                <div className="mt-2 flex gap-2">
                  <input
                    value={importKb}
                    onChange={(e) => setImportKb(e.target.value)}
                    placeholder="导入到资料库名称"
                    className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
                  />
                  <button
                    className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40"
                    onClick={doImport}
                    disabled={busy || selected.size === 0 || !importKb.trim()}
                  >
                    导入 {selected.size} 条
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* 资料列表：一行式 */}
      <div className="mt-3 flex flex-1 flex-col gap-0.5 overflow-y-auto pr-1">
        {kbs.length === 0 ? (
          <div className="px-2 py-6 text-center">
            <p className="text-sm text-muted">还没有资料库</p>
            <p className="mt-1 text-xs text-faint">点上方「＋ 导入资料」上传或联网搜索</p>
          </div>
        ) : (
          kbs.map((k) => {
            const bound = boundKb === k.kb_id;
            return (
              <div
                key={k.kb_id}
                className="flex items-center justify-between rounded-lg px-2 py-2 transition hover:bg-subtle"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-text">{k.kb_id}</div>
                  <div className="text-xs text-faint">{k.chunks} 片段</div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                      bound
                        ? "bg-accent-soft text-accent"
                        : "text-accent hover:bg-accent-soft"
                    }`}
                    onClick={() => onBind(bound ? null : k.kb_id)}
                  >
                    {bound ? "已绑定" : "绑定"}
                  </button>
                  <button
                    className="rounded-md px-2 py-1 text-xs text-faint transition hover:text-rose-500"
                    onClick={() => onDeleteKb(k.kb_id)}
                  >
                    删
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 解绑当前 */}
      {boundKb && (
        <button
          className="mt-3 w-full rounded-full px-3 py-2 text-xs text-muted transition hover:bg-subtle"
          onClick={() => onBind(null)}
        >
          解绑当前资料库
        </button>
      )}
    </aside>
  );
}