"use client";

/**
 * 我的反馈（用户端）
 *
 * 骨架按数量切换：
 *   0 条    → 引导版式
 *   1–3 条  → 单列卡片，卡片自带完整对话
 *   ≥4 条   → 左侧会话列表 + 右侧对话
 *
 * 对话统一上下式：提问在上，回复依次在下。不做左右气泡。
 *
 * 引导靠图标与版式承担，不靠成段说明文字。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import type { MyFeedbackItem, MyFeedbackMessage } from "@/lib/types";
import { SkeletonLines } from "@/components/Skeleton";

/** ≥ 这个数量才启用左列表。 */
const COUNT_FOR_LIST = 4;

const ST_LABEL: Record<string, string> = {
  open: "已收到",
  doing: "正在处理",
  done: "已解决",
  closed: "已关闭",
};
const ST_TONE: Record<string, string> = {
  open: "st-open",
  doing: "st-doing",
  done: "st-done",
  closed: "st-closed",
};
const CAT_TONE: Record<string, string> = {
  bug: "cat-bug",
  answer: "cat-answer",
  feature: "cat-feature",
  ui: "cat-ui",
  other: "cat-other",
};

/** 分类图标。用同一套线性风格，与知穗的图标语言一致。 */
const CAT_ICON: Record<string, string> = {
  bug: "M4 8h4M4 12h4M4 16h4M8 8l4-3v14l-4-3H6a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2zM16 8l3-2M16 12h4M16 16l3 2",
  answer: "M12 4a8 8 0 1 0 3.5 15.2L20 20l-.8-4.5A8 8 0 0 0 12 4z",
  feature: "M12 4v3M12 17v3M4 12h3M17 12h3M6.5 6.5l2 2M15.5 15.5l2 2M17.5 6.5l-2 2M8.5 15.5l-2 2",
  ui: "M5 5h14v14H5zM5 9h14M9 9v10",
  other: "M6 12h.01M12 12h.01M18 12h.01",
};

function ago(ts: number): string {
  if (!ts) return "";
  const d = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  if (d < 86400 * 30) return `${Math.floor(d / 86400)} 天前`;
  const dt = new Date(ts * 1000);
  return `${dt.getMonth() + 1} 月 ${dt.getDate()} 日`;
}

function lastAt(it: MyFeedbackItem): number {
  return it.last_at || it.at;
}

function CatIcon({ cat }: { cat: string }) {
  return (
    <svg
      className="mfx-ico"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={CAT_ICON[cat] ?? CAT_ICON.other} />
    </svg>
  );
}

export default function MyFeedbackPage() {
  const { me, loading: authLoading } = useAuth();
  const authedId = me?.user?.id ?? null;

  const [items, setItems] = useState<MyFeedbackItem[] | null>(null);
  const [messages, setMessages] = useState<MyFeedbackMessage[]>([]);
  const [activeFid, setActiveFid] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [sending, setSending] = useState<number | null>(null);
  const [showThread, setShowThread] = useState(false);
  const msgsRef = useRef<HTMLDivElement | null>(null);
  const markedRef = useRef(false);

  const load = useCallback(async () => {
    if (!authedId) return;
    try {
      const res = await api.myFeedbackThread();
      const list = (res.items ?? [])
        .slice()
        .sort((a, b) => lastAt(b) - lastAt(a) || b.fid - a.fid);
      setItems(list);
      setMessages(res.messages ?? []);
      setActiveFid((prev) => prev ?? (list[0]?.fid ?? null));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败");
      setItems([]);
    }
  }, [authedId]);

  useEffect(() => {
    if (authLoading || !authedId) return;
    void Promise.resolve().then(load);
  }, [authLoading, authedId, load]);

  useEffect(() => {
    if (!authedId || items === null || markedRef.current) return;
    markedRef.current = true;
    void api.markFeedbackRead(0).catch(() => {});
  }, [authedId, items]);

  const count = items?.length ?? 0;
  const twoPane = count >= COUNT_FOR_LIST;

  const twoPaneMsgs = useMemo(
    () => (twoPane ? messages.filter((m) => m.fid === activeFid) : []),
    [twoPane, messages, activeFid],
  );

  const active = useMemo(
    () => items?.find((it) => it.fid === activeFid) ?? null,
    [items, activeFid],
  );

  useEffect(() => {
    if (!twoPane) return;
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [twoPane, activeFid, twoPaneMsgs.length]);

  async function send(fid: number) {
    const text = (draft[fid] ?? "").trim();
    if (!text || sending) return;
    setSending(fid);
    try {
      await api.replyMyFeedback(fid, text);
      setDraft((d) => ({ ...d, [fid]: "" }));
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSending(null);
    }
  }

  const stLabel = (it: MyFeedbackItem) =>
    it.user_status_label || ST_LABEL[it.status] || it.status;

  if (authLoading) {
    return (
      <div className="mfx">
        <div className="mfx-sheet">
          <SkeletonLines widths={["40%", "90%", "70%"]} />
        </div>
      </div>
    );
  }

  const openN = (items ?? []).filter(
    (i) => i.status === "open" || i.status === "doing",
  ).length;

  return (
    <div className={`mfx${twoPane ? " is-two" : ""}`}>
      <header className="mfx-head">
        <div className="mfx-head-l">
          <h1>我的反馈</h1>
          {count > 0 && (
            <p className="meta">
              一共 {count} 条{openN > 0 ? ` · ${openN} 条还在办` : ""}
            </p>
          )}
        </div>
        <Link href="/" className="mfx-back">
          回到对话
        </Link>
      </header>

      {err && <div className="mfx-err">{err}</div>}

      {items === null ? (
        <div className="mfx-sheet">
          <SkeletonLines widths={["90%", "60%", "40%"]} />
        </div>
      ) : count === 0 ? (
        /* 空态：图标 + 三段流向，不用成段文字解释 */
        <div className="mfx-invite">
          <span className="mfx-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 4a8 8 0 1 0 3.5 15.2L20 20l-.8-4.5A8 8 0 0 0 12 4z" />
            </svg>
          </span>
          <div className="mfx-flow">
            <div className="mfx-flow-step">
              <span className="mfx-flow-ico">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z" />
                  <path d="M14.5 6.5l3 3" />
                </svg>
              </span>
              <b>提一条</b>
              <i>对话页右下角</i>
            </div>
            <span className="mfx-flow-arrow" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </span>
            <div className="mfx-flow-step">
              <span className="mfx-flow-ico">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 7h16M4 12h10M4 17h6" />
                </svg>
              </span>
              <b>我们处理</b>
              <i>状态会在这里变</i>
            </div>
            <span className="mfx-flow-arrow" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </span>
            <div className="mfx-flow-step">
              <span className="mfx-flow-ico">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 6h16v11H9l-4 3v-3H4z" />
                </svg>
              </span>
              <b>看到回复</b>
              <i>每条都在这里</i>
            </div>
          </div>
          <Link className="btn btn-primary btn-sm" href="/">
            去对话页
          </Link>
        </div>
      ) : twoPane ? (
        <div className={`mfx-two${showThread ? " has-thread" : ""}`}>
          <aside className="mfx-list">
            <nav aria-label="我的反馈列表">
              {items.map((it) => {
                const on = it.fid === activeFid;
                return (
                  <button
                    key={it.fid}
                    type="button"
                    className={`mfx-li${on ? " on" : ""}`}
                    onClick={() => {
                      setActiveFid(it.fid);
                      setShowThread(true);
                    }}
                    aria-current={on ? "true" : undefined}
                  >
                    <span className={`mfx-li-ico ${CAT_TONE[it.category] ?? "cat-other"}`}>
                      <CatIcon cat={it.category} />
                    </span>
                    <span className="mfx-li-main">
                      <span className="mfx-li-title">{it.text}</span>
                      <span className="mfx-li-foot">
                        <span className={`mfx-st ${ST_TONE[it.status] ?? "st-open"}`}>
                          {stLabel(it)}
                        </span>
                        <time className="meta">{ago(lastAt(it))}</time>
                      </span>
                    </span>
                    {it.replies > 0 && (
                      <span className="mfx-li-count" aria-label={`${it.replies} 条回复`}>
                        {it.replies}
                      </span>
                    )}
                  </button>
                );
              })}
            </nav>
          </aside>

          <section className="mfx-thread">
            {active ? (
              <>
                <div className="mfx-thread-head">
                  <button
                    type="button"
                    className="mfx-thread-back"
                    onClick={() => setShowThread(false)}
                    aria-label="返回列表"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M15 6l-6 6 6 6" />
                    </svg>
                  </button>
                  <span className={`mfx-li-ico ${CAT_TONE[active.category] ?? "cat-other"}`}>
                    <CatIcon cat={active.category} />
                  </span>
                  <span className="mfx-thread-cat">{active.category_label}</span>
                  <span className={`mfx-st ${ST_TONE[active.status] ?? "st-open"}`}>
                    {stLabel(active)}
                  </span>
                  <time className="meta mfx-thread-time">{ago(active.at)}提的</time>
                </div>
                <div className="mfx-msgs" ref={msgsRef}>
                  <StackThread msgs={twoPaneMsgs} />
                </div>
                <Composer
                  value={draft[active.fid] ?? ""}
                  onChange={(v) => setDraft((d) => ({ ...d, [active.fid]: v }))}
                  onSend={() => void send(active.fid)}
                  busy={sending === active.fid}
                />
              </>
            ) : (
              <div className="mfx-placeholder">从左边选一条反馈。</div>
            )}
          </section>
        </div>
      ) : (
        <div className="mfx-cards">
          {items.map((it) => (
            <article key={it.fid} className="mfx-card">
              <div className="mfx-card-head">
                <span className={`mfx-li-ico ${CAT_TONE[it.category] ?? "cat-other"}`}>
                  <CatIcon cat={it.category} />
                </span>
                <span className="mfx-card-cat">{it.category_label}</span>
                <span className={`mfx-st ${ST_TONE[it.status] ?? "st-open"}`}>
                  {stLabel(it)}
                </span>
                <time className="meta mfx-card-time">{ago(it.at)}</time>
              </div>

              <p className="mfx-ask">{it.text}</p>

              <div className="mfx-thread-body">
                <StackThread msgs={messages.filter((m) => m.fid === it.fid)} />
              </div>

              <Composer
                value={draft[it.fid] ?? ""}
                onChange={(v) => setDraft((d) => ({ ...d, [it.fid]: v }))}
                onSend={() => void send(it.fid)}
                busy={sending === it.fid}
              />
            </article>
          ))}

          <Link className="mfx-more" href="/">
            <span className="mfx-more-ico" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </span>
            <span className="mfx-more-txt">
              <b>再提一条</b>
              <i>对话页右下角的「反馈」</i>
            </span>
            <span className="mfx-more-go" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </span>
          </Link>
        </div>
      )}
    </div>
  );
}

/** 上下式对话：回复依次排列，左侧金线连成一段。 */
function StackThread({ msgs }: { msgs: MyFeedbackMessage[] }) {
  const replies = msgs.filter((m) => m.kind === "admin" || m.kind === "user_reply");
  if (replies.length === 0) {
    return <p className="mfx-wait">我们收到了，还没回复。</p>;
  }
  return (
    <div className="mfx-replies">
      {replies.map((m) => (
        <div className="mfx-reply" key={`${m.fid}-${m.rid}`}>
          <span className="mfx-reply-who">
            <b>{m.kind === "admin" ? m.author || "开发者" : "我"}</b>
            <i>{ago(m.at)}</i>
          </span>
          <p className="mfx-reply-body">{m.text}</p>
        </div>
      ))}
    </div>
  );
}

function Composer({
  value,
  onChange,
  onSend,
  busy,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  busy: boolean;
}) {
  return (
    <div className="mfx-compose">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="补充说明…"
        rows={1}
        maxLength={1000}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
      />
      <button
        type="button"
        className="btn btn-primary btn-sm"
        onClick={onSend}
        disabled={!value.trim() || busy}
      >
        {busy ? "发送中" : "发送"}
      </button>
    </div>
  );
}