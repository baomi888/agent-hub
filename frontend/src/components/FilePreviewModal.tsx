"use client";

// 库内文件正文预览弹层。
// 用 portal 挂到 body：≤1279px 时右栏是带 transform 的抽屉，
// transform 会成为 fixed 定位的包含块，弹层不 portal 就会被裁进 300px 的抽屉里。
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { FilePreview } from "@/lib/types";

interface Props {
  kbId: string;
  /** 后端记录里的原始文件名（老库可能是完整临时路径，展示时只取文件名） */
  name: string;
  /** 该文件在库里的片段数，展示用 */
  chunks?: number | null;
  onClose: () => void;
}

/** 老库入库时把临时绝对路径写进了 source，展示只取最后一段 */
export function shortName(name: string): string {
  const s = (name || "").replace(/\\/g, "/");
  const seg = s.split("/").filter(Boolean).pop();
  return seg || name;
}

type PreviewState =
  | { key: string; status: "loading" }
  | { key: string; status: "ok"; data: FilePreview }
  | { key: string; status: "err"; error: string };

// 只在客户端 portal：SSR 阶段 document 不存在，用 getServerSnapshot 给出 false 避免水合不一致
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

export default function FilePreviewModal({ kbId, name, chunks, onClose }: Props) {
  const reqKey = `${kbId}::${name}`;
  // 请求结果跟「请求哪个文件」绑在一起存：换文件时在 render 阶段直接重置，
  // 不用在 effect 里同步 setState（会触发 react-hooks/set-state-in-effect）
  const [state, setState] = useState<PreviewState>({ key: reqKey, status: "loading" });
  if (state.key !== reqKey) setState({ key: reqKey, status: "loading" });

  const [copied, setCopied] = useState(false);
  // 复制失败单独存，不覆盖正文：正文还在就照常显示
  const [copyError, setCopyError] = useState<string | null>(null);
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  const cardRef = useRef<HTMLDivElement>(null);
  // 用惰性初始值在首次 render 阶段抓触发元素：autoFocus 会在 commit 阶段抢走焦点，
  // 等到 effect 里再读 activeElement，拿到的已经是弹层自己的关闭按钮了
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );

  // 关闭时把焦点还回去，否则键盘用户会掉回页面开头
  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus?.();
    },
    [opener]
  );

  useEffect(() => {
    let alive = true;
    api.getFileContent(kbId, name).then(
      (data) => alive && setState({ key: reqKey, status: "ok", data }),
      (e) =>
        alive &&
        setState({
          key: reqKey,
          status: "err",
          error: e instanceof Error ? e.message : "读取失败",
        })
    );
    return () => {
      alive = false;
    };
  }, [kbId, name, reqKey]);

  // Esc 关闭 + Tab 锁在弹层内
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !cardRef.current) return;
      const nodes = cardRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, textarea, [tabindex]:not([tabindex="-1"])'
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

  const doCopy = useCallback(async () => {
    if (state.status !== "ok") return;
    try {
      await navigator.clipboard.writeText(state.data.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopyError("复制失败：浏览器拒绝了剪贴板权限，请手动选中文本复制");
    }
  }, [state]);

  if (!mounted) return null;

  const title = shortName(name);
  const isPdf = /\.pdf$/i.test(title);
  const data = state.status === "ok" ? state.data : null;
  const error = state.status === "err" ? state.error : null;

  return createPortal(
    <div
      className="pv-mask"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={cardRef}
        className="pv-card"
        role="dialog"
        aria-modal="true"
        aria-label={`预览文件：${title}`}
      >
        <header className="pv-head">
          <div className="min-w-0">
            <h2 className="pv-title" title={name}>
              {title}
            </h2>
            <p className="pv-meta">
              <span className="pv-badge">
                {data ? (data.origin === "archive" ? "原文" : "片段拼回") : "读取中"}
              </span>
              {chunks != null && <span>{chunks} 片段</span>}
              {data && <span>{data.content.length.toLocaleString()} 字</span>}
            </p>
          </div>
          <button
            autoFocus
            onClick={onClose}
            title="关闭预览（Esc）"
            aria-label="关闭预览"
            className="pv-close"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        {error ? (
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
            {copyError && <p className="pv-error">{copyError}</p>}
            {data.content.trim() ? (
              <>
                {data.origin === "chunks" && (
                  <p className="pv-note">
                    这个文件入库较早，原始文件没有留档，以下是它在库里的片段按顺序拼回的内容
                    {isPdf ? "（PDF 文字层）" : ""}。
                  </p>
                )}
                {data.origin === "archive" && isPdf && (
                  <p className="pv-note">PDF 预览显示的是文字层内容，不含版式与图片。</p>
                )}
                <pre className="pv-text">{data.content}</pre>
                {data.truncated && (
                  <p className="pv-note">
                    内容较长，预览只截取前 {data.content.length.toLocaleString()} 字。
                  </p>
                )}
              </>
            ) : (
              <p className="pv-empty">没能读到这个文件的正文（可能是扫描版 PDF 或空文件）。</p>
            )}
          </div>
        )}

        <footer className="pv-foot">
          <button
            onClick={doCopy}
            disabled={!data || !data.content}
            className="pv-btn"
            title="复制预览到的正文"
          >
            {copied ? "已复制" : "复制正文"}
          </button>
          <button onClick={onClose} className="pv-btn primary">
            关闭
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
}
