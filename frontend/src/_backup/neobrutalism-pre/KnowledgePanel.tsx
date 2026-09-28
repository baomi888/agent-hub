"use client";

// 右侧资料库面板：标题 + 虚线导入按钮 + 卡片式知识库列表（勾选框表示绑定状态）
import { useRef, useState } from "react";
import type { KbInfo, SearchResult } from "@/lib/types";

const MAX_UPLOAD_MB = 100; // 与后端保持一致

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
  onCollapse?: () => void;
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
  onCollapse,
}: Props) {
  const [showImport, setShowImport] = useState(false);
  const [uploadKb, setUploadKb] = useState("");
  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importKb, setImportKb] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const fmtSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

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

  const onFileChange = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploadError(null);
    const arr = Array.from(files);
    // 客户端校验：单文件大小上限
    const oversized = arr.find((f) => f.size > MAX_UPLOAD_MB * 1024 * 1024);
    if (oversized) {
      setUploadError(
        `「${oversized.name}」超过 ${MAX_UPLOAD_MB}MB 限制（${fmtSize(oversized.size)}）`
      );
      return;
    }
    setSelectedFiles(arr);
    // 若已填写资料库名称，自动上传
    if (uploadKb.trim()) {
      doUpload(arr);
    }
  };

  const doUpload = async (files?: File[]) => {
    const list = files ?? selectedFiles;
    if (list.length === 0 || !uploadKb.trim()) return;
    setBusy(true);
    setUploadError(null);
    try {
      await onUpload(uploadKb.trim(), list);
      setSelectedFiles([]);
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      const msg = e instanceof Error ? e.message : "上传失败";
      setUploadError(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-l border-line bg-bg px-3 py-4">
      {/* 头部 */}
      <div className="flex items-center justify-between px-1">
        <h2 className="text-sm font-semibold text-text">资料库</h2>
        {onCollapse && (
          <button
            onClick={onCollapse}
            title="收起资料库"
            className="flex h-6 w-6 items-center justify-center rounded-md text-muted transition hover:bg-subtle hover:text-text"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
              <path d="m15 18-6-6 6-6" />
            </svg>
          </button>
        )}
      </div>

      {/* 导入资料：虚线大按钮 */}
      <div className="mt-3 px-1">
        <button
          onClick={() => setShowImport((s) => !s)}
          className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-line bg-surface py-2.5 text-sm font-medium text-muted transition hover:border-accent/60 hover:text-accent"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="h-4 w-4">
            <path d="M12 5v14M5 12h14" />
          </svg>
          导入资料
        </button>
        <p className="mt-1.5 text-center text-[11px] text-faint">
          支持 TXT / Markdown / PDF
        </p>
      </div>

      {/* 导入面板（默认折叠） */}
      {showImport && (
        <div className="fade-up mt-3 flex flex-col gap-2.5">
          {/* 上传文档 */}
          <div className="rounded-xl border border-line bg-surface p-3.5">
            <h3 className="text-xs font-semibold text-muted">上传文档</h3>
            <div className="mt-2 flex flex-col gap-2">
              <input
                value={uploadKb}
                onChange={(e) => setUploadKb(e.target.value)}
                placeholder="资料库名称（如 寻臻项目计划书）"
                className="rounded-lg border border-line bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
              />
              {/* 隐藏的原生 input，由下方按钮触发 */}
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".txt,.md,.pdf"
                onChange={(e) => onFileChange(e.target.files)}
                className="hidden"
              />
              {/* 可点击上传区：点击即打开资源管理器 */}
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="flex flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed border-line bg-bg py-5 text-center transition hover:border-accent/60 hover:bg-accent-soft/40 disabled:opacity-50"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-7 w-7 text-faint">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
                <span className="text-sm font-medium text-text">
                  {busy ? "上传中…" : "点击选择文件"}
                </span>
                <span className="text-[11px] text-faint">支持 TXT / Markdown / PDF，可多选</span>
              </button>

              {/* 已选文件列表 */}
              {selectedFiles.length > 0 && (
                <div className="flex flex-col gap-1">
                  {selectedFiles.map((f, i) => (
                    <div key={i} className="flex items-center gap-1.5 rounded-md bg-bg px-2 py-1 text-xs text-muted">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3 shrink-0 text-accent">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <path d="M14 2v6h6" />
                      </svg>
                      <span className="truncate flex-1">{f.name}</span>
                      <span className="text-faint">{fmtSize(f.size)}</span>
                    </div>
                  ))}
                  {/* 上传错误提示 */}
                  {uploadError && (
                    <p className="rounded-md bg-rose-50 px-2 py-1 text-[11px] text-rose-600">
                      {uploadError}
                    </p>
                  )}
                  {/* 名称未填时提示 */}
                  {!uploadKb.trim() && !uploadError && (
                    <p className="text-[11px] text-amber-600">请先填写资料库名称后自动上传</p>
                  )}
                  {uploadKb.trim() && selectedFiles.length > 0 && !busy && (
                    <button
                      onClick={() => doUpload()}
                      className="mt-1 rounded-lg bg-accent py-1.5 text-xs font-medium text-white transition hover:opacity-90"
                    >
                      上传到「{uploadKb}」
                    </button>
                  )}
                </div>
              )}

              <p className="text-xs text-faint">
                分段 {chunkSize} 字 / 重叠 {chunkOverlap} 字
              </p>
            </div>
          </div>

          {/* 联网导入 */}
          <div className="rounded-xl border border-line bg-surface p-3.5">
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

            {/* 目标资料库：始终可见 */}
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

            {results.length > 0 && (
              <ul className="mt-3 max-h-56 space-y-1.5 overflow-y-auto pr-1">
                {results.map((r) => (
                  <li
                    key={r.href}
                    className="flex items-start gap-1 rounded-lg px-2 py-1.5 text-sm hover:bg-subtle"
                  >
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={selected.has(r.href)}
                        onChange={() => toggle(r.href)}
                        className="mt-0.5"
                      />
                      <div className="min-w-0">
                        <div className="truncate font-medium text-text">{r.title}</div>
                        <div className="line-clamp-2 text-xs text-muted">{r.body}</div>
                      </div>
                    </label>
                    <a
                      href={r.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="跳转到官方网页"
                      className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-accent-soft hover:text-accent"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                        <polyline points="15 3 21 3 21 9" />
                        <line x1="10" y1="14" x2="21" y2="3" />
                      </svg>
                    </a>
                    <a
                      href={`/api/kb/download?url=${encodeURIComponent(r.href)}`}
                      download
                      title="下载到本机"
                      className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-accent-soft hover:text-accent"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* 知识库列表：行项式 */}
      <div className="mt-3 flex-1 overflow-y-auto pr-1">
        {kbs.length === 0 ? (
          <div className="px-2 py-6 text-center">
            <p className="text-sm text-muted">还没有资料库</p>
            <p className="mt-1 text-xs text-faint">点上方「导入资料」上传或联网搜索</p>
          </div>
        ) : (
          <div className="flex flex-col">
            {kbs.map((k) => {
              const bound = boundKb === k.kb_id;
              return (
                <div
                  key={k.kb_id}
                  className={`group flex items-start gap-2.5 border-b border-line px-1 py-3 transition ${
                    bound ? "bg-accent-soft/40" : "hover:bg-subtle"
                  }`}
                >
                  {/* 勾选框：唯一绑定入口 */}
                  <button
                    title={bound ? "取消绑定" : "绑定到当前对话"}
                    onClick={() => onBind(bound ? null : k.kb_id)}
                    className={`mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border transition ${
                      bound ? "border-accent bg-accent text-white" : "border-line-strong bg-surface hover:border-accent"
                    }`}
                  >
                    {bound && (
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
                        <path d="M20 6L9 17l-5-5" />
                      </svg>
                    )}
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className={`truncate text-sm ${bound ? "font-title text-accent" : "font-medium text-text"}`}>
                    {k.name || k.kb_id}
                  </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-faint">
                      <span className="rounded-full bg-subtle px-1.5 py-px">{k.files.length} 文件</span>
                      <span className="rounded-full bg-subtle px-1.5 py-px">{k.chunks} 片段</span>
                    </div>
                  </div>
                  {/* 删除：hover 才出现 */}
                  <button
                    title="删除知识库"
                    className="mt-0.5 rounded-md p-1 text-faint opacity-0 transition hover:bg-rose-50 hover:text-rose-500 group-hover:opacity-100 dark:hover:bg-rose-500/10"
                    onClick={() => onDeleteKb(k.kb_id)}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                    </svg>
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}
