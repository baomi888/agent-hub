"use client";

// 对话里的围栏代码块（提示词最常见）：豆包式卡片 —— 内容自动换行完整显示，
// 右上角复制 / 下载 / 展开。旧实现是 <pre white-space:pre + overflow-x:auto>，
// 长提示词被横向截断，用户只能看到半截英文（截图反馈「不能正常显示」）。
//
// 展开用 portal 挂到 body：右栏抽屉有 transform，fixed 不 portal 会被裁进抽屉里
// （与 FilePreviewModal 同理）。expanded 只由用户点击置 true，SSR/hydration 阶段
// 恒为 false，不会出现 portal 的水合不一致。
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useCopy } from "@/lib/useCopy";

/** 复制图标 */
function CopyIcon({ done }: { done: boolean }) {
  return done ? (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v12m0 0l-4-4m4 4l4-4" />
      <path d="M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3m13 5h3a2 2 0 0 0 2-2v-3" />
    </svg>
  );
}

interface ActionsProps {
  label: string;
  copied: boolean;
  onCopy: () => void;
  onDownload: () => void;
  onExpand: () => void;
}

/** 卡片头：左边语言标签，右边复制/下载/展开三枚按钮 */
function Actions({ label, copied, onCopy, onDownload, onExpand }: ActionsProps) {
  return (
    <div className="md-code-head">
      <span className="md-code-lang">{label}</span>
      <div className="md-code-actions">
        <button type="button" className="md-code-btn" onClick={onCopy} title={copied ? "已复制" : "复制提示词"} aria-label="复制">
          <CopyIcon done={copied} />
        </button>
        <button type="button" className="md-code-btn" onClick={onDownload} title="下载为 txt" aria-label="下载">
          <DownloadIcon />
        </button>
        <button type="button" className="md-code-btn" onClick={onExpand} title="展开查看" aria-label="展开">
          <ExpandIcon />
        </button>
      </div>
    </div>
  );
}

export default function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const { copied, copy } = useCopy();
  const [expanded, setExpanded] = useState(false);
  // 语言标签：豆包卡片左上角显示 "text"；没有 lang 时默认 text
  const label = (lang || "text").slice(0, 24);

  const handleCopy = () => { void copy(code); };

  const handleDownload = () => {
    try {
      const blob = new Blob([code], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      // 文件名只留安全字符，避免 lang 里的反斜杠 / 路径分隔符污染下载名
      const safe = label.replace(/[^\w\u4e00-\u9fa5-]/g, "") || "text";
      a.download = `prompt-${safe}.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      // 下载能力受限（个别内置 WebView）时静默失败，复制仍可用
    }
  };

  // 展开层：Esc 关闭；遮罩点击关闭。复用全局 pv-mask/pv-card 骨架。
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [expanded]);

  return (
    <>
      <div className="md-code-card">
        <Actions label={label} copied={copied} onCopy={handleCopy} onDownload={handleDownload} onExpand={() => setExpanded(true)} />
        <pre className="md-code-body"><code>{code}</code></pre>
      </div>
      {expanded && createPortal(
        <div className="pv-mask" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setExpanded(false); }}>
          <div className="pv-card md-code-full" role="dialog" aria-modal="true" aria-label="提示词全文">
            <header className="pv-head">
              <div className="min-w-0">
                <h2 className="pv-title">提示词全文</h2>
                <p className="pv-meta">可复制 · 可下载 · Esc 关闭</p>
              </div>
              <button autoFocus className="pv-close" onClick={() => setExpanded(false)} title="关闭（Esc）" aria-label="关闭">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
            </header>
            <div className="md-code-full-bar">
              <Actions label={label} copied={copied} onCopy={handleCopy} onDownload={handleDownload} onExpand={() => setExpanded(false)} />
            </div>
            <pre className="md-code-body md-code-body-full"><code>{code}</code></pre>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
