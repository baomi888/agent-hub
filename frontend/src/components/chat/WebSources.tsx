"use client";

// 联网来源：正文后的一枚「N 个来源」胶囊，点开是卡片列表（点卡片开新窗口）。
//
// 形态对标 DeepSeek / 豆包的「参考来源」：用户扫一眼就知道这段话是从哪几个
// 网页来的，想深挖再点进去。和资料库引用（RefBlock）刻意分开：
//   - 资料库：本地文件，能看「原文第 3 页」，没有网址；
//   - 联网：网页，能开原页面，没有原文片段。
// 混成一个列表，最后一定有一处把本地文件渲染成可点外链。
//
// 为什么是浮层而不是像 RefBlock 那样平铺：来源条目 5~10 条很常见，平铺会把
// 回答整体推得很长，读者滚到一半就找不到自己在看哪句；浮层不占正文高度。
import { useEffect, useRef, useState } from "react";
import type { WebSource } from "@/lib/types";

/** 只放行 http(s)：后端已经过滤过一次，这里再兜一道，防止旧接口/手工数据漏进来 */
function safeUrl(url: string): string | null {
  return /^https?:\/\//i.test(url.trim()) ? url.trim() : null;
}

/**
 * 字母徽标：中文标题取首字（「茂名旅游攻略」→ 茂），英文取域名首字母。
 *
 * 不用站点真实 favicon，是有意的取舍：favicon 只能由前端直连第三方站点取，
 * 一来部分站点直接 403 / 根本没配，会得到一片破图；二来每条来源都把用户 IP
 * 暴露给那个站点；三来境外站点在国内网络下经常转半天。DeepSeek / 豆包能做到
 * 是因为它们有自家服务代理并缓存图标。字母徽标零请求、永不破图，视觉上够用。
 */
function badgeOf(s: WebSource): string {
  const first = (s.title || "").trim().charAt(0);
  if (/[\u4e00-\u9fa5]/.test(first)) return first;
  const host = (s.domain || "").replace(/^www\./, "");
  return (host.charAt(0) || "?").toUpperCase();
}

export default function WebSources({ items }: { items: WebSource[] }) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // 关闭途径两条：Esc、点面板外面。不做焦点陷阱 —— 这里只是浮层不是模态框，
  // 项目里弹窗互抢焦点爆过栈（见 guide/feedback 那几处），能少一层就少一层。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  if (items.length === 0) return null;

  return (
    <div className="ws-block" ref={boxRef}>
      <button
        type="button"
        className="ws-pill"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起来源" : `查看这 ${items.length} 个来源`}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" />
        </svg>
        {items.length} 个来源
        <svg
          className="ws-chev"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <>
          {/* 手机端遮罩：抽屉从底部升起，没有遮罩的话点空白处不会关，像卡住了 */}
          <div className="ws-scrim" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="ws-pop" role="dialog" aria-label="联网来源">
            <div className="ws-pop-head">
              <span>来源</span>
              <button
                type="button"
                className="ws-close"
                onClick={() => setOpen(false)}
                aria-label="关闭"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <ul className="ws-list">
              {items.map((s) => {
                const href = safeUrl(s.url);
                const inner = (
                  <>
                    <span className="ws-badge" aria-hidden="true">
                      {badgeOf(s)}
                    </span>
                    <span className="ws-body">
                      <span className="ws-title">{s.title || s.domain || s.url}</span>
                      <span className="ws-host">{s.domain || href || ""}</span>
                      {s.snippet && <span className="ws-snippet">{s.snippet}</span>}
                    </span>
                  </>
                );
                return (
                  <li key={`${s.id}-${s.url}`}>
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`打开 ${s.domain || href}`}
                      >
                        {inner}
                      </a>
                    ) : (
                      <span className="ws-row-static">{inner}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
