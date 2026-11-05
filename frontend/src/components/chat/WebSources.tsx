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
import { useEffect, useMemo, useRef, useState } from "react";
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

export default function WebSources({
  items,
  label,
}: {
  items: WebSource[];
  /** 平铺列表的小抬头。只在「同一屏里还有资料库来源」时传 —— 两组来源上下相邻，
   *  不给抬头的话读者分不清上面这三条和下面那张卡片是什么关系。 */
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  // 浮层方向：默认往下（不遮住用户刚读完的回答）。但来源区常贴在页面底部，
  // 下方空间不够时会被输入框压住 —— 这时改成往上弹，宁可盖正文也要看得全。
  const [dir, setDir] = useState<"down" | "up">("down");
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

  // 默认平铺的 3 条：按域名去重，避免前三格全是同一个站点下的不同页面
  // （问「XX 官网」「XX 政策」时，搜索结果前五条经常同域）。去重只看展示，
  // 展开后的完整列表**不合并** —— 用户主动点开就是想看全的，那一步再合并
  // 等于藏信息。cites 那边也仍是全量，正文里的 [Wn] 一条都不会失效。
  const flat = useMemo(() => {
    const seen = new Set<string>();
    const out: WebSource[] = [];
    for (const s of items) {
      const host = (s.domain || "").replace(/^www\./, "") || s.url;
      if (seen.has(host)) continue;
      seen.add(host);
      out.push(s);
      if (out.length >= 3) break;
    }
    return out;
  }, [items]);

  const needMore = items.length > flat.length;

  // 展开前先量一下下方还剩多少空间（浮层最高 340 + 边距，留 20 的余量）
  const toggle = () => {
    if (!open && boxRef.current) {
      const r = boxRef.current.getBoundingClientRect();
      setDir(window.innerHeight - r.bottom < 360 ? "up" : "down");
    }
    setOpen(!open);
  };

  if (items.length === 0) return null;

  return (
    <div className="ws-block" ref={boxRef}>
      {/* 默认可见的前 3 条：横评里「要点一次才看得到来源」这一档，点击可得指数
          只有 61（豆包）甚至 44（DeepSeek），而默认展开的那一档是 91~96。
          来源摆出来，用户才可能去点 —— 藏在胶囊里等于没给。 */}
      {label && <div className="ws-flat-head">{label}</div>}

      {flat.length > 0 && (
        <ul className="ws-flat" aria-label="联网来源">
          {flat.map((s) => {
            const href = safeUrl(s.url);
            const inner = (
              <>
                <span className="ws-badge" aria-hidden="true">
                  {badgeOf(s)}
                </span>
                <span className="ws-flat-body">
                  <span className="ws-title">{s.title || s.domain || s.url}</span>
                  <span className="ws-meta">
                    {[s.domain, s.date].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </>
            );
            return (
              <li key={`f-${s.id}-${s.url}`}>
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
      )}

      {needMore && (
        <div className="ws-more">
          <button
            type="button"
            className="ws-pill"
            aria-expanded={open}
            onClick={toggle}
            title={open ? "收起其余来源" : `展开其余 ${items.length - flat.length} 条`}
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
            {open ? "收起" : `展开全部 ${items.length} 条`}
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
              <div
                className={`ws-pop${dir === "up" ? " is-up" : ""}`}
                role="dialog"
                aria-label="联网来源"
              >
                <div className="ws-pop-head">
                  <span>全部 {items.length} 个来源</span>
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
                          <span className="ws-host">
                            {[s.domain, s.date].filter(Boolean).join(" · ") || href || ""}
                          </span>
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
      )}
    </div>
  );
}
