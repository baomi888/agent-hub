"use client";

// 回答末尾的链接收口：把「一大片 URL」折成一枚胶囊，点开才是卡片列表。
//
// 为什么需要它：联网搜完之后，模型常把整排检索结果直接贴进正文，
// 用户看到的是十几个 https://… 挤在段落里，结论反而被挤没了。
// 这里不改内容、只改呈现 —— 默认折起来，点一下全都还在。
//
// 摘取逻辑在 content.tsx 的 splitTrailingLinks（纯函数，可单测）。
import { useState } from "react";

export interface LinkItem {
  url: string;
  label: string;
}

/** 域名当兜底标题：map.qq.com 比一整条 https://map.qq.com/m/detail/… 好读 */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** 没有标题时拿路径最后一段当标题：/travel-guide/maoming → "maoming" */
function pathName(url: string): string {
  try {
    const segs = new URL(url).pathname.split("/").filter(Boolean);
    const last = segs[segs.length - 1] ?? "";
    const clean = decodeURIComponent(last)
      .replace(/[-_]+/g, " ")
      .replace(/\.[a-z0-9]{1,5}$/i, "")
      .trim();
    return clean.length >= 2 ? clean : "";
  } catch {
    return "";
  }
}

export default function LinkCluster({ items }: { items: LinkItem[] }) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;

  return (
    <div className="link-cluster">
      <button
        type="button"
        className="lc-pill"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起链接" : "展开这 N 条链接"}
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
          <path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5" />
          <path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5" />
        </svg>
        {items.length} 个链接
        <svg
          className="lc-chev"
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
        <ul className="lc-list">
          {items.map((it, i) => {
            const host = hostOf(it.url);
            // 没有标题时，左右两边都写域名会变成「example.com — example.com」，
            // 先拿路径末段当标题，实在没有才退回域名（那时右边就不再重复）
            const label = it.label || pathName(it.url) || host;
            return (
              <li key={`${it.url}-${i}`}>
                <a href={it.url} target="_blank" rel="noopener noreferrer" title={it.url}>
                  {/* 首字徽标：和联网来源卡（WebSources）用同一套视觉，
                      用户在两处看到的是同一种"来源"符号，不用重新认识一遍 */}
                  <span className="ws-badge" aria-hidden="true">
                    {(label || host).trim().charAt(0).toUpperCase()}
                  </span>
                  <span className="lc-label">{label}</span>
                  {label !== host && <span className="lc-host">{host}</span>}
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
