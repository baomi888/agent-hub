"use client";

/* eslint-disable @next/next/no-img-element -- 图片全是用户 blob / 抽取出的 data URL，next/image 优化没有意义 */
// 上传附件的点开预览（豆包/DeepSeek 式）：图片直接放大本地 blob，
// PDF 用浏览器自带查看器，Office / 文本走 /api/capture/preview 临时抽正文。
// 复用资料库预览弹层（.pv-*）的视觉，只是换内容区；portal 到 body 避开
// ≤1279px 时带 transform 的抽屉，否则弹层会被裁进 300px 的侧栏里。
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { AttachmentPreviewData } from "@/lib/types";
import type { ChatAttachment } from "./types";

interface Props {
  attachment: ChatAttachment;
  onClose: () => void;
}

const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp)$/i;

function kindOf(a: ChatAttachment): "image" | "pdf" | "doc" {
  const name = a.name || "";
  if (a.type.startsWith("image/") || IMAGE_RE.test(name)) return "image";
  if (a.type === "application/pdf" || /\.pdf$/i.test(name)) return "pdf";
  return "doc";
}

function formatSize(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "";
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

interface PreviewState {
  key: string;
  data: AttachmentPreviewData | null;
  error: string | null;
}

const staleFileHint = "原文件已不在当前页面，重新上传后就能点开预览";

export default function AttachmentPreviewModal({ attachment, onClose }: Props) {
  const reqKey = `${attachment.id}::${attachment.name}`;
  // 结果跟「正在预览哪个附件」绑在一起：换附件时在 render 阶段重置，
  // 不在 effect 里同步 setError（react-hooks/set-state-in-effect 会报错）。
  const initial = (key: string): PreviewState => ({
    key,
    data: null,
    error: kindOf(attachment) === "doc" && !attachment.file ? staleFileHint : null,
  });
  const [state, setState] = useState<PreviewState>(() => initial(reqKey));
  if (state.key !== reqKey) setState(initial(reqKey));
  const { data, error } = state;
  const [zoomed, setZoomed] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  // 焦点要在弹层关闭时还回去；在 render 阶段惰性抓，等 effect 就已经被 autoFocus 抢走了
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );
  const kind = kindOf(attachment);
  const title = attachment.name || "附件";

  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus?.();
    },
    [opener]
  );

  // 图片/PDF 用 blob URL 本地渲染，不必上传；文档才请求后端临时解析。
  // 刷新页面后历史消息的 file 不在内存里，给出可行动的提示，不假装能读。
  useEffect(() => {
    if (kind !== "doc" || !attachment.file) return;
    let alive = true;
    api.capture.previewAttachment(attachment.file).then(
      (res) => alive && setState((s) => (s.key === reqKey ? { key: reqKey, data: res, error: null } : s)),
      (e: unknown) =>
        alive &&
        setState((s) =>
          s.key === reqKey
            ? { key: reqKey, data: null, error: e instanceof Error ? e.message : "预览失败，请下载后查看" }
            : s
        )
    );
    return () => {
      alive = false;
    };
  }, [attachment, kind, reqKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !cardRef.current) return;
      const nodes = cardRef.current.querySelectorAll<HTMLElement>(
        'button, [href], [tabindex]:not([tabindex="-1"])'
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  if (!mounted) return null;

  const badge = kind === "image" ? "图片" : kind === "pdf" ? "PDF" : "文档正文";
  const showImages = data?.images?.length ? data.images : [];

  return createPortal(
    <div
      className="pv-mask apv-mask"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={cardRef}
        className={`pv-card apv-card${kind === "image" ? " is-image" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={`预览附件：${title}`}
      >
        <header className="pv-head">
          <div className="min-w-0">
            <h2 className="pv-title" title={title}>
              {title}
            </h2>
            <p className="pv-meta">
              <span className="pv-badge">{badge}</span>
              {formatSize(attachment.size) && <span>{formatSize(attachment.size)}</span>}
              {data && <span>{data.text.length.toLocaleString()} 字</span>}
            </p>
          </div>
          <a className="pv-btn" href={attachment.url} download={title} title="下载原文件">
            下载
          </a>
          <button autoFocus onClick={onClose} title="关闭预览（Esc）" aria-label="关闭预览" className="pv-close">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        {kind === "image" ? (
          <div className="apv-image-body">
            {/* 点一下在「适应窗口 / 原始尺寸」之间切换，豆包式看大图 */}
            <img
              className={`apv-img${zoomed ? " is-zoomed" : ""}`}
              src={attachment.url}
              alt={title}
              onClick={() => setZoomed((z) => !z)}
              title={zoomed ? "点一下适应窗口" : "点一下放大"}
            />
          </div>
        ) : kind === "pdf" ? (
          <div className="apv-pdf-body">
            <iframe className="apv-frame" src={attachment.url} title={`PDF 预览：${title}`} />
            <p className="pv-note">
              内嵌预览看不到内容时，
              <a href={attachment.url} target="_blank" rel="noreferrer">
                在新窗口打开
              </a>
            </p>
          </div>
        ) : error ? (
          <div className="pv-body">
            <p className="pv-error">{error}</p>
          </div>
        ) : !data ? (
          <div className="pv-body" aria-busy="true">
            <span className="skel-line" />
            <span className="skel-line" />
            <span className="skel-line short" />
          </div>
        ) : (
          <div className="pv-body">
            {data.text ? (
              <pre className="pv-text">{data.text}</pre>
            ) : (
              <p className="pv-empty">这个文件里没有可抽出的文字内容</p>
            )}
            {showImages.map((src, i) => (
              <img key={i} className="apv-extracted-img" src={src} alt={`文档内图片 ${i + 1}`} />
            ))}
            {data.truncated && <p className="pv-note">内容过长，预览只展示前面一部分</p>}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
