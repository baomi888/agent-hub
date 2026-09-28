"use client";

// 右侧资料库面板：标题 + 导入按钮 + 知识库列表
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
import { useRef, useState, type RefObject } from "react";
import type { KbInfo, SearchResult } from "@/lib/types";
import FilePreviewModal, { shortName } from "./FilePreviewModal";

const MAX_UPLOAD_MB = 100;

interface Props {
  kbs: KbInfo[];
  boundKb: string | null;
  onUpload: (kbId: string, files: File[]) => Promise<void>;
  onSearch: (keyword: string) => Promise<SearchResult[]>;
  /** 联网建库：逐条抓并回调进度，完成后由页面层自动绑定 + 预填问题 */
  onWebImport: (
    kbName: string,
    urls: string[],
    onProgress: (i: number, total: number, title: string) => void
  ) => Promise<string>;
  onDeleteKb: (kbId: string) => void;
  /** 删除库内单个文件（只清它的片段） */
  onDeleteFile?: (kbId: string, name: string) => void;
  /** 按当前切片参数重建整库 */
  onRebuildKb?: (kbId: string) => void;
  onBind: (kbId: string | null) => void;
  chunkSize: number;
  chunkOverlap: number;
  onCollapse?: () => void;
  /** 中屏及以下作为浮层抽屉渲染 */
  overlay?: boolean;
  /** 抽屉是否展开（仅 overlay 时有意义） */
  open?: boolean;
  /** 抽屉容器引用，用于打开时接管焦点 */
  containerRef?: RefObject<HTMLElement | null>;
  /** 首屏还在拉资料库列表 */
  loading?: boolean;
}

export default function KnowledgePanel({
  kbs,
  boundKb,
  onUpload,
  onSearch,
  onWebImport,
  onDeleteKb,
  onDeleteFile,
  onRebuildKb,
  onBind,
  chunkSize,
  chunkOverlap,
  onCollapse,
  overlay = false,
  open = false,
  containerRef,
  loading = false,
}: Props) {
  const [feed, setFeed] = useState<"upload" | "web" | null>(null);
  const [uploadKb, setUploadKb] = useState("");
  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importKb, setImportKb] = useState("");
  const [busy, setBusy] = useState(false);
  // 联网建库逐条抓取时的进度：「正在抓第 3/10 篇」
  const [importProgress, setImportProgress] = useState<{ i: number; total: number; title: string } | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [netError, setNetError] = useState<string | null>(null);
  // 删除资料库会连带丢掉它的全部片段，先确认再删
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);
  // 展开哪个库的文件清单
  const [expandedKb, setExpandedKb] = useState<string | null>(null);
  // 待确认删除的单个文件，格式 `${kb_id}::${name}`
  const [confirmFile, setConfirmFile] = useState<string | null>(null);
  // 正在预览的文件（kb_id + 原始文件名 + 片段数）
  const [preview, setPreview] = useState<{
    kbId: string;
    name: string;
    chunks: number | null;
  } | null>(null);
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
    setNetError(null);
    try {
      const r = await onSearch(keyword.trim());
      setResults(r);
      setSelected(new Set());
    } catch (e) {
      setNetError(e instanceof Error ? e.message : "搜索失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    if (!importKb.trim() || selected.size === 0) return;
    setBusy(true);
    setNetError(null);
    try {
      const urls = Array.from(selected);
      await onWebImport(importKb.trim(), urls, (i, total, title) => {
        setImportProgress({ i, total, title });
      });
      setImportProgress(null);
      setResults([]);
      setSelected(new Set());
      setImportKb("");
    } catch (e) {
      setNetError(e instanceof Error ? e.message : "导入失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  const onFileChange = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploadError(null);
    const arr = Array.from(files);
    const oversized = arr.find((f) => f.size > MAX_UPLOAD_MB * 1024 * 1024);
    if (oversized) {
      setUploadError(
        `「${oversized.name}」超过 ${MAX_UPLOAD_MB}MB 限制（${fmtSize(oversized.size)}）`
      );
      return;
    }
    setSelectedFiles(arr);
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
    <aside
      ref={containerRef}
      tabIndex={-1}
      className={`side-right flex h-full shrink-0 flex-col px-4 py-5 ${open ? "open" : ""}`}
      aria-label="资料库"
      aria-busy={loading}
      inert={!open}
    >
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <h2 className="flex items-baseline gap-2.5">
          <span className="font-serif text-fs-xl font-bold text-ink">资料库</span>
          <span className="h-px w-6 bg-line-strong" />
        </h2>
        {onCollapse && (
          <button
            onClick={onCollapse}
            title="收起资料库"
            aria-label="收起资料库"
            className="icon-btn"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m15 18-6-6 6-6" />
            </svg>
          </button>
        )}
      </div>

      {/* 两种进料入口：我上传 / 它去搜 —— 主路径「直接问」在左侧对话区 */}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <button
          onClick={() => setFeed(feed === "upload" ? null : "upload")}
          className={`feed-btn ${feed === "upload" ? "on" : ""}`}
          aria-expanded={feed === "upload"}
        >
          ＋ 我的文件
        </button>
        <button
          onClick={() => setFeed(feed === "web" ? null : "web")}
          className={`feed-btn ${feed === "web" ? "on" : ""}`}
          aria-expanded={feed === "web"}
        >
          ＋ 网上的资料
        </button>
      </div>

      {/* 进料①：上传文档 */}
      {feed === "upload" && (
        <div className="fade-up mt-3 flex flex-col gap-2.5">
          {/* 上传文档 */}
          <div className="rounded-xl border border-line-strong bg-panel p-4">
            <h3 className="eyebrow">我的文件</h3>
            <div className="mt-2 flex flex-col gap-2">
              <input
                value={uploadKb}
                onChange={(e) => setUploadKb(e.target.value)}
                placeholder="资料库名称（如 寻臻项目计划书）"
                aria-label="资料库名称"
                className="underline-input py-1.5 text-sm"
              />
              <input
                ref={fileRef}
                type="file"
                multiple
                accept=".txt,.md,.pdf"
                onChange={(e) => onFileChange(e.target.files)}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-line-strong bg-bg py-5 text-center transition hover:bg-fill-2 disabled:opacity-50"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-7 w-7 text-muted">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
                <span className="text-sm font-medium text-ink">
                  {busy ? "上传中…" : "点击选择文件"}
                </span>
                <span className="text-fs-xs text-faint">支持 TXT / Markdown / PDF，可多选</span>
              </button>

              {selectedFiles.length > 0 && (
                <div className="flex flex-col gap-1">
                  {selectedFiles.map((f, i) => (
                    <div key={i} className="flex items-center gap-1.5 rounded-lg border border-line bg-bg px-2 py-1.5 text-xs text-muted">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3 shrink-0 text-ink">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <path d="M14 2v6h6" />
                      </svg>
                      <span className="truncate flex-1">{f.name}</span>
                      <span className="text-faint">{fmtSize(f.size)}</span>
                    </div>
                  ))}
                  {uploadError && (
                    <p className="rounded-lg border border-line-strong bg-red/5 px-2 py-1 text-fs-xs text-red-ink">
                      {uploadError}
                    </p>
                  )}
                  {!uploadKb.trim() && !uploadError && (
                    <p className="text-fs-xs text-muted">请先填写资料库名称后自动上传</p>
                  )}
                  {uploadKb.trim() && selectedFiles.length > 0 && !busy && (
                    <button
                      onClick={() => doUpload()}
                      className="btn-import mt-1 w-full py-1.5 text-xs"
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
        </div>
      )}

        {/* 进料②：联网搜索导入（带逐条进度） */}
        {feed === "web" && (
          <div className="fade-up mt-3 flex flex-col gap-2.5">
            <div className="rounded-xl border border-line-strong bg-panel p-4">
              <h3 className="eyebrow">网上的资料</h3>
            <div className="mt-2 flex gap-2">
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doSearch()}
                placeholder="搜索关键词"
                aria-label="联网搜索关键词"
                className="underline-input min-w-0 flex-1 py-1.5 text-sm"
              />
              <button
                className="btn-import px-3 py-1 text-sm"
                onClick={doSearch}
                disabled={busy}
              >
                搜索
              </button>
            </div>

            <div className="mt-2 flex gap-2">
              <input
                value={importKb}
                onChange={(e) => setImportKb(e.target.value)}
                placeholder="导入到资料库名称"
                aria-label="导入到资料库名称"
                className="underline-input min-w-0 flex-1 py-1.5 text-sm"
              />
              <button
                className="btn-import px-3 py-1 text-sm"
                onClick={doImport}
                disabled={busy || selected.size === 0 || !importKb.trim()}
              >
                {importProgress ? `抓取中 ${importProgress.i}/${importProgress.total}` : `导入 ${selected.size} 条`}
              </button>
            </div>

            {/* 逐条抓取进度：等待时间也是一次低成本的二次交互窗口 */}
            {importProgress && (
              <div className="mt-3 rounded-lg border border-line-strong bg-bg p-2.5">
                <div className="import-progress-bar">
                  <span style={{ width: `${(importProgress.i / importProgress.total) * 100}%` }} />
                </div>
                <p className="mt-1.5 text-fs-xs text-muted">
                  正在抓第 {importProgress.i}/{importProgress.total} 篇
                  {importProgress.title ? `：${importProgress.title}` : ""}
                </p>
              </div>
            )}

            {netError && (
              <p className="mt-2 rounded-lg border border-line-strong bg-red/5 px-2 py-1 text-fs-xs text-red-ink">
                {netError}
              </p>
            )}

            {results.length > 0 && (
              <ul className="mt-3 max-h-56 space-y-1 overflow-y-auto rounded-lg border border-line-strong bg-bg p-1.5 pr-1">
                {results.map((r) => (
                  <li
                    key={r.href}
                    className="flex items-start gap-1 rounded-lg px-1.5 py-1.5 text-sm last:border-b-0 hover:bg-fill-2"
                  >
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={selected.has(r.href)}
                        onChange={() => toggle(r.href)}
                        className="mt-0.5 rounded accent-accent"
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
                      className="icon-btn mt-0.5 h-7 w-7 shrink-0 justify-center rounded-lg border border-line"
                    >
                      <svg viewBox="0 0 24 24">
                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                        <polyline points="15 3 21 3 21 9" />
                        <line x1="10" y1="14" x2="21" y2="3" />
                      </svg>
                    </a>
                    <a
                      href={`/api/kb/download?url=${encodeURIComponent(r.href)}`}
                      download
                      title="下载到本机"
                      className="icon-btn mt-0.5 h-7 w-7 shrink-0 justify-center rounded-lg border border-line"
                    >
                      <svg viewBox="0 0 24 24">
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

      {/* 知识库列表 */}
      <div className="mt-3 flex-1 overflow-y-auto pr-1">
        {loading ? (
          <div className="flex flex-col gap-2 px-1 pt-1" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skel-block">
                <span className="skel-line" />
                <span className="skel-line short" />
              </div>
            ))}
          </div>
        ) : kbs.length === 0 ? (
          <div className="px-3 py-8 text-center">
            {/* 玉米线描插画：呼应「苞米地笔记本」的暖金氛围 */}
            <div className="kb-empty-illus" aria-hidden="true">
              <svg viewBox="0 0 80 80">
                {/* 玉米棒 */}
                <path d="M40 14 C29 18 25 30 27 45 C29 56 34 62 40 62 C46 62 51 56 53 45 C55 30 51 18 40 14Z" />
                {/* 颗粒：横向弧线 */}
                <path d="M31 25 C36 23 44 23 49 25" />
                <path d="M29 34 C35 32 45 32 51 34" />
                <path d="M29 43 C35 41 45 41 51 43" />
                <path d="M31 52 C36 50 44 50 49 52" />
                {/* 颗粒：纵向线 */}
                <path d="M40 15 V61" />
                <path d="M34 17 V59" />
                <path d="M46 17 V59" />
                {/* 顶须 */}
                <path d="M40 14 C39 9 36 6 33 5" />
                <path d="M40 14 C41 8 44 5 47 4" />
                <path d="M40 14 C43 10 46 8 50 8" />
                {/* 底部叶片 */}
                <path d="M38 61 C32 64 28 70 28 75 C34 73 38 68 38 61Z" />
                <path d="M42 61 C48 64 52 70 52 75 C46 73 42 68 42 61Z" />
              </svg>
            </div>
            <p className="font-serif text-sm font-medium text-ink">还没有资料库</p>
            <div className="mt-4 space-y-2 text-left">
              <div className="flex items-start gap-2.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-fs-badge font-bold text-white">1</span>
                <p className="text-xs text-muted">点上方「我的文件」或「网上的资料」开始建库</p>
              </div>
              <div className="flex items-start gap-2.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-fs-badge font-bold text-white">2</span>
                <p className="text-xs text-muted">勾选资料库左侧的勾选框绑定到当前对话</p>
              </div>
              <div className="flex items-start gap-2.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-fs-badge font-bold text-white">3</span>
                <p className="text-xs text-muted">回到左侧对话区开始提问</p>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col">
            {kbs.map((k) => {
              const bound = boundKb === k.kb_id;
              const files = k.files ?? [];
              const expanded = expandedKb === k.kb_id;
              return (
                <div
                  key={k.kb_id}
                  className={`kb-row group ${bound ? "bound" : ""}`}
                >
                  <button
                    title={bound ? "取消绑定" : "绑定到当前对话"}
                    aria-label={bound ? `取消绑定资料库：${k.name || k.kb_id}` : `绑定资料库到当前对话：${k.name || k.kb_id}`}
                    aria-pressed={bound}
                    onClick={() => onBind(bound ? null : k.kb_id)}
                    className={`check ${bound ? "on" : ""}`}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M20 6L9 17l-5-5" />
                    </svg>
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="kb-name">{k.name || k.kb_id}</div>
                    <div className="kb-meta">
                      <span className="tag">{files.length} 文件</span>
                      <span className="tag">{k.chunks} 片段</span>
                      {files.length > 0 && (
                        <button
                          type="button"
                          className="kb-manage"
                          aria-expanded={expanded}
                          onClick={() => setExpandedKb(expanded ? null : k.kb_id)}
                        >
                          {expanded ? "收起" : "管理文件"}
                        </button>
                      )}
                    </div>

                    {expanded && files.length > 0 && (
                      <ul className="kb-files">
                        {files.map((f) => {
                          const key = `${k.kb_id}::${f.name}`;
                          return (
                            <li key={f.name} className="kb-file">
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                <path d="M14 2v6h6" />
                              </svg>
                              <button
                                type="button"
                                className="kb-file-name"
                                title={`预览全文：${shortName(f.name)}`}
                                onClick={() =>
                                  setPreview({ kbId: k.kb_id, name: f.name, chunks: f.chunks })
                                }
                              >
                                {shortName(f.name)}
                              </button>
                              <span className="kb-file-meta">{f.chunks ?? "—"} 片段</span>
                              {f.origin === "web" && <span className="tag web">网上</span>}
                              {f.origin === "upload" && <span className="tag up">上传</span>}
                              {confirmFile === key ? (
                                <span
                                  className="kb-file-confirm"
                                  onKeyDown={(e) => {
                                    if (e.key === "Escape") setConfirmFile(null);
                                  }}
                                >
                                  <button
                                    title="确认删除这个文件"
                                    aria-label={`确认删除文件：${f.name}`}
                                    className="kb-del confirm"
                                    onClick={() => {
                                      onDeleteFile?.(k.kb_id, f.name);
                                      setConfirmFile(null);
                                    }}
                                  >
                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                      <path d="M20 6L9 17l-5-5" />
                                    </svg>
                                  </button>
                                  <button
                                    autoFocus
                                    title="取消"
                                    aria-label="取消删除"
                                    className="kb-del"
                                    onClick={() => setConfirmFile(null)}
                                  >
                                    <svg viewBox="0 0 24 24" aria-hidden="true">
                                      <path d="M6 6l12 12M18 6L6 18" />
                                    </svg>
                                  </button>
                                </span>
                              ) : (
                                <button
                                  title="从库里删除这个文件"
                                  aria-label={`删除文件：${f.name}`}
                                  className="kb-del"
                                  onClick={() => setConfirmFile(key)}
                                >
                                  <svg viewBox="0 0 24 24" aria-hidden="true">
                                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                                  </svg>
                                </button>
                              )}
                            </li>
                          );
                        })}
                        {onRebuildKb && (
                          <li className="kb-file-foot">
                            <button
                              type="button"
                              className="kb-rebuild"
                              title="按当前切片参数重建整库（会对全部文件重新向量化）"
                              onClick={() => onRebuildKb(k.kb_id)}
                            >
                              按 {chunkSize}/{chunkOverlap} 重建整库
                            </button>
                          </li>
                        )}
                      </ul>
                    )}
                  </div>
                  {confirmDelId === k.kb_id ? (
                    <span
                      className="flex shrink-0 items-center gap-1"
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setConfirmDelId(null);
                      }}
                    >
                      <button
                        title="确认删除"
                        aria-label={`确认删除资料库：${k.name || k.kb_id}`}
                        className="kb-del confirm"
                        onClick={() => {
                          onDeleteKb(k.kb_id);
                          setConfirmDelId(null);
                        }}
                      >
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                      </button>
                      <button
                        autoFocus
                        title="取消"
                        aria-label="取消删除"
                        className="kb-del"
                        onClick={() => setConfirmDelId(null)}
                      >
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                          <path d="M6 6l12 12M18 6L6 18" />
                        </svg>
                      </button>
                    </span>
                  ) : (
                    <button
                      title="删除知识库"
                      aria-label={`删除资料库：${k.name || k.kb_id}`}
                      className="kb-del"
                      onClick={() => setConfirmDelId(k.kb_id)}
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                      </svg>
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {preview && (
        <FilePreviewModal
          kbId={preview.kbId}
          name={preview.name}
          chunks={preview.chunks}
          onClose={() => setPreview(null)}
        />
      )}
    </aside>
  );
}
