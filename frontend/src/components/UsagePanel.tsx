"use client";

// 「用量与反馈」看板（P2 信任基础设施）。
// 触发点：侧栏底部当前账号那一块点开。数据全来自 GET /api/usage/summary，
// 一次拉取画满：当日额度两条进度 + 近 7 日迷你柱状 + 赞踩聚合 + 最近几条反馈。
// 弹层复用 FilePreviewModal 的 portal + pv-mask/pv-card，避免被抽屉 transform 裁切。
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { UsageSummary, UsageDay } from "@/lib/types";
import CornIcon from "./CornIcon";

interface Props {
  onClose: () => void;
}

type State =
  | { status: "loading" }
  | { status: "ok"; data: UsageSummary }
  | { status: "err"; error: string };

// 只在客户端 portal（SSR 无 document）
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

const fmtLimit = (n: number) => (n === 0 ? "∞" : String(n));

/** 把 "2026-10-11" 压成 "10/11"，窄屏也放得下 */
function shortDay(d: string): string {
  const p = d.split("-");
  return p.length === 3 ? `${p[1]}/${p[2]}` : d;
}

function ago(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - (ts > 1e12 ? Math.floor(ts / 1000) : ts));
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

export default function UsagePanel({ onClose }: Props) {
  const [state, setState] = useState<State>({ status: "loading" });
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  const cardRef = useRef<HTMLDivElement>(null);
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );

  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus?.();
    },
    [opener]
  );

  const load = useCallback(() => {
    setState({ status: "loading" });
    let alive = true;
    api.usageSummary().then(
      (data) => alive && setState({ status: "ok", data }),
      (e) =>
        alive &&
        setState({ status: "err", error: e instanceof Error ? e.message : "读取失败" })
    );
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => load(), [load]);

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

  if (!mounted) return null;

  const data = state.status === "ok" ? state.data : null;

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
        className="pv-card up-card"
        role="dialog"
        aria-modal="true"
        aria-label="用量与反馈"
      >
        <header className="pv-head">
          <div className="min-w-0">
            <h2 className="pv-title">用量与反馈</h2>
            <p className="pv-meta">
              <span className="pv-badge">只属于你</span>
              <span>数据每打开一次刷新</span>
            </p>
          </div>
          <button
            autoFocus
            onClick={onClose}
            title="关闭（Esc）"
            aria-label="关闭"
            className="pv-close"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        <div className="pv-body">
          {state.status === "loading" && (
            <p className="py-12 text-center text-fs-md text-muted">正在统计…</p>
          )}

          {state.status === "err" && (
            <div className="py-12 text-center">
              <p className="text-fs-md text-red-ink">{state.error}</p>
              <button onClick={() => load()} className="btn-import mt-3 px-4 py-1.5">
                重试
              </button>
            </div>
          )}

          {data && (
            <div className="flex flex-col gap-5">
              {/* 一、今日额度 */}
              <section>
                <div className="group-head">
                  <span className="eyebrow">今日额度</span>
                  <span className="rule" />
                </div>
                {Object.values(data.quota).some((q) => q.unlimited) ? (
                  <p className="mt-3 rounded-md border border-line bg-fill-3 px-3 py-2.5 text-fs-sm text-muted">
                    管理员账号：问答与向量化均不限额度。限流防爆破仍在生效。
                  </p>
                ) : (
                  <div className="mt-3 flex flex-col gap-3">
                    {(
                      [
                        ["ask", "今日问答", "每轮对话消耗 1 次"],
                        ["embed", "向量化分片", "上传/建库按片段计费"],
                      ] as const
                    ).map(([kind, label, hint]) => {
                      const q = data.quota[kind];
                      if (!q) return null;
                      const pct = q.limit ? Math.min(100, Math.round((q.used / q.limit) * 100)) : 0;
                      const danger = pct >= 90;
                      return (
                        <div key={kind}>
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="text-fs-sm text-ink">{label}</span>
                            <span className="text-fs-sm tabular-nums text-muted">
                              {q.used}
                              <span className="text-faint"> / </span>
                              {fmtLimit(q.limit)}
                            </span>
                          </div>
                          <div className="up-bar mt-1.5">
                            <span
                              className={danger ? "up-bar-fill danger" : "up-bar-fill"}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <p className="mt-1 text-fs-xs text-faint">{hint}</p>
                        </div>
                      );
                    })}
                    {data.rate_per_minute > 0 && (
                      <p className="text-fs-xs text-faint">
                        另有限流：每分钟最多 {data.rate_per_minute} 次请求。
                      </p>
                    )}
                  </div>
                )}
              </section>

              {/* 二、近 7 天趋势 */}
              <section>
                <div className="group-head">
                  <span className="eyebrow">近 7 天</span>
                  <span className="rule" />
                </div>
                <UsageBars history={data.history} />
              </section>

              {/* 三、赞踩反馈 */}
              <section>
                <div className="group-head">
                  <span className="eyebrow">答案反馈</span>
                  <span className="rule" />
                </div>
                {data.feedback.total === 0 ? (
                  <p className="mt-3 rounded-md border border-line bg-fill-3 px-3 py-2.5 text-fs-sm text-muted">
                    还没有反馈。每条回答下都能点赞或点踩——你的评价会帮我们找到不可靠的答案。
                  </p>
                ) : (
                  <>
                    <div className="mt-3 flex items-center gap-4">
                      <div className="flex items-center gap-1.5">
                        <CornIcon name="target" className="h-4 w-4" title="赞" />
                        <span className="text-fs-lg font-medium text-ink tabular-nums">
                          {data.feedback.up}
                        </span>
                        <span className="text-fs-xs text-muted">赞</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <CornIcon name="warn" className="h-4 w-4" title="踩" />
                        <span className="text-fs-lg font-medium text-ink tabular-nums">
                          {data.feedback.down}
                        </span>
                        <span className="text-fs-xs text-muted">踩</span>
                      </div>
                    </div>
                    {data.feedback.recent.length > 0 && (
                      <ul className="mt-3 flex flex-col gap-1.5">
                        {data.feedback.recent.map((f, i) => (
                          <li
                            key={i}
                            className="flex items-start gap-2 rounded-md border border-line bg-fill-3 px-2.5 py-2"
                          >
                            <span
                              className={`mt-0.5 shrink-0 rounded-xs px-1.5 py-0.5 text-fs-xs ${
                                f.rating === "up"
                                  ? "bg-accent-soft text-accent-ink"
                                  : "bg-fill-1 text-muted"
                              }`}
                            >
                              {f.rating === "up" ? "赞" : "踩"}
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-fs-sm text-ink" title={f.title}>
                                {f.title || "（未命名会话）"}
                              </p>
                              {f.comment && (
                                <p className="mt-0.5 text-fs-xs leading-relaxed text-faint">
                                  {f.comment}
                                </p>
                              )}
                            </div>
                            <span className="shrink-0 text-fs-xs text-faint">
                              {ago(f.created_at)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </section>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

/** 近 7 日两根柱（问答 / 向量化），高度按各自峰值归一。零数据也画空槽保持节律 */
function UsageBars({ history }: { history: UsageDay[] }) {
  const maxAsk = Math.max(1, ...history.map((d) => d.ask));
  const maxEmbed = Math.max(1, ...history.map((d) => d.embed));
  const allZero = history.every((d) => d.ask === 0 && d.embed === 0);
  return (
    <div className="mt-3">
      <div className="up-chart">
        {history.map((d) => (
          <div key={d.day} className="up-col" title={`${d.day} · 问答 ${d.ask} · 向量化 ${d.embed}`}>
            <div className="up-col-bars">
              <span
                className="up-bar-v ask"
                style={{ height: `${Math.round((d.ask / maxAsk) * 100)}%` }}
              />
              <span
                className="up-bar-v embed"
                style={{ height: `${Math.round((d.embed / maxEmbed) * 100)}%` }}
              />
            </div>
            <span className="up-col-label">{shortDay(d.day)}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-3 text-fs-xs text-faint">
        <span className="inline-flex items-center gap-1">
          <i className="up-dot ask" /> 问答
        </span>
        <span className="inline-flex items-center gap-1">
          <i className="up-dot embed" /> 向量化
        </span>
        {allZero && <span className="text-faint">· 近 7 天还没有使用记录</span>}
      </div>
    </div>
  );
}
