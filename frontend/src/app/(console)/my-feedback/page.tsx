"use client";

/**
 * 我的反馈 · 用户端会话中心（2026-11-20 第三版，微信式）
 *
 * ★ 为什么推翻了第二版的卡片流
 *   第二版做成"一条反馈一张卡、卡内时间线"。用户原话：
 *   「不是按照我们之前的管理员的那种，微信是有会话然后可以看到不同管理员的回复」。
 *   他说得对 —— 反馈的本质就是**来回对话**，对话就该长成会话的样子。
 *   卡片流适合彼此独立、读一眼就走的列表（已办事项、通知）；
 *   而"我提了一个问题 → 他回了 → 我又补一句"是有来有回的，不是几条并列记录。
 *
 * ★ 为什么用户端要做成流，管理员端反而是"一条一张卡"
 *   管理员要同时处理**很多人的很多条**，混成一条流会分不清谁在说什么；
 *   用户端只有**他自己**，会话流正是他的心智模型（和微信一致）。
 *   两边形态不同，是因为两边要回答的问题不同。
 *
 * ★ 数据来源
 *   `GET /api/user-feedback/thread`，直接复用 core 的 thread(uid)：
 *     · items[]    —— 左列表要的"一条反馈一行"（状态 + 最后一条预览）
 *     · messages[] —— 右对话要的"全局时间序"（kind: user / user_reply / admin）
 *   排序由后端做，前端**不许**按 fid 分组后重排（会把时序搞错，
 *   core/user_feedback.py 的 thread() 注释里记着这次事故）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import type { MyFeedbackItem, MyFeedbackMessage } from "@/lib/types";
import { SkeletonLines } from "@/components/Skeleton";

/** 用户侧四档状态词。后端也会下发 user_status_label，这里兜底老数据。 */
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

/** 这条会话最近一次动静：有回复就用最后回复时间，否则用提问时间 */
function lastAt(it: MyFeedbackItem): number {
  return it.last_at || it.at;
}

export default function MyFeedbackPage() {
  const { me, loading: authLoading } = useAuth();
  const authedId = me?.user?.id ?? null;

  const [items, setItems] = useState<MyFeedbackItem[] | null>(null);
  const [messages, setMessages] = useState<MyFeedbackMessage[]>([]);
  const [activeFid, setActiveFid] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // 窄屏下列表与对话二选一：true = 正在看对话（列表收起）
  const [showThread, setShowThread] = useState(false);
  const msgsRef = useRef<HTMLDivElement | null>(null);
  const markedRef = useRef(false);

  const load = useCallback(async () => {
    if (!authedId) return;
    try {
      const res = await api.myFeedbackThread();
      // 列表按"最近动静"倒序：有新回复的顶上来（用户端不按状态分档，
      // 他只有几条，按状态分会显得很空）
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

  // 打开这页 = 全部已读（入口红点该灭了）
  useEffect(() => {
    if (!authedId || items === null || markedRef.current) return;
    markedRef.current = true;
    void api.markFeedbackRead(0).catch(() => {
      /* 标已读失败不该挡住阅读，下次进来再标 */
    });
  }, [authedId, items]);

  const activeMsgs = useMemo(
    () => messages.filter((m) => m.fid === activeFid),
    [messages, activeFid],
  );

  // 切会话 / 有新消息后滚到底（和微信一致：最新的话在最下面）
  useEffect(() => {
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeFid, activeMsgs.length]);

  const active = useMemo(
    () => items?.find((it) => it.fid === activeFid) ?? null,
    [items, activeFid],
  );

  async function send() {
    const text = draft.trim();
    if (!text || !activeFid || sending) return;
    setSending(true);
    try {
      await api.replyMyFeedback(activeFid, text);
      setDraft("");
      // 回读整个会话流，而不是自己往数组里拼一条 ——
      // 拼出来的 id / 顺序 / 作者容易和后端不一致，重拉一遍最省事也最准
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSending(false);
    }
  }

  const stLabel = (it: MyFeedbackItem) =>
    it.user_status_label || ST_LABEL[it.status] || it.status;

  if (authLoading) {
    return (
      <div className="mfc">
        <div className="card card-pad" style={{ margin: 16 }}>
          <SkeletonLines widths={["40%", "90%", "70%"]} />
        </div>
      </div>
    );
  }

  return (
    <div className={`mfc${showThread ? " has-thread" : ""}`}>
      {/* ---------- 左：会话列表 ---------- */}
      <aside className="mfc-list">
        <div className="mfc-list-head">
          <b>我的反馈</b>
          <Link href="/" className="meta mfc-back">
            回到对话
          </Link>
        </div>

        {err && <div className="mfc-err">{err}</div>}

        {items === null ? (
          <div className="mfc-skel" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="mfc-skel-row">
                <span className="skel-line" />
                <span className="skel-line short" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="mfc-empty">
            <b>还没提过反馈</b>
            <p className="meta">
              遇到出错、答得不好、想要的功能，都可以在对话页右下角的「反馈」里告诉我们。
            </p>
            <Link className="btn btn-primary btn-sm" href="/">
              去提第一条
            </Link>
          </div>
        ) : (
          <nav className="mfc-items" aria-label="我的反馈列表">
            {items.map((it) => {
              const on = it.fid === activeFid;
              return (
                <button
                  key={it.fid}
                  type="button"
                  className={`mfc-item${on ? " on" : ""}`}
                  onClick={() => {
                    setActiveFid(it.fid);
                    setShowThread(true);
                  }}
                  aria-current={on ? "true" : undefined}
                >
                  <span className="mfc-item-top">
                    <span className={`mfc-cat ${CAT_TONE[it.category] ?? "cat-other"}`}>
                      <i aria-hidden="true" />
                      {it.category_label}
                    </span>
                    <span className={`mfc-st ${ST_TONE[it.status] ?? "st-open"}`}>
                      {stLabel(it)}
                    </span>
                  </span>
                  <span className="mfc-item-title">{it.text}</span>
                  <span className="mfc-item-foot">
                    <time className="meta">{ago(lastAt(it))}</time>
                    {it.replies > 0 && (
                      <span className="mfc-count" aria-label={`${it.replies} 条回复`}>
                        {it.replies}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </nav>
        )}
      </aside>

      {/* ---------- 右：对话 ---------- */}
      <section className="mfc-main">
        {active ? (
          <>
            <header className="mfc-main-head">
              <button
                type="button"
                className="mfc-backbtn"
                onClick={() => setShowThread(false)}
                aria-label="返回列表"
              >
                ‹
              </button>
              <div className="mfc-main-title">
                <span className={`mfc-cat ${CAT_TONE[active.category] ?? "cat-other"}`}>
                  <i aria-hidden="true" />
                  {active.category_label}
                </span>
                <span className={`mfc-st ${ST_TONE[active.status] ?? "st-open"}`}>
                  {stLabel(active)}
                </span>
              </div>
              <time className="meta mfc-main-time">{ago(active.at)}提的</time>
            </header>

            <div className="mfc-msgs" ref={msgsRef}>
              {activeMsgs.map((m) => {
                const mine = m.kind !== "admin";
                return (
                  <div
                    key={`${m.fid}-${m.rid ?? "root"}`}
                    className={`mfc-row${mine ? " me" : ""}`}
                  >
                    {!mine && (
                      <span className="mfc-who">
                        {m.author || "管理员"}
                        <i>{ago(m.at)}</i>
                      </span>
                    )}
                    <div className="mfc-bubble">{m.text}</div>
                    {mine && <span className="mfc-time">{ago(m.at)}</span>}
                  </div>
                );
              })}
              {activeMsgs.length > 0 &&
                activeMsgs.every((m) => m.kind !== "admin") && (
                  <p className="mfc-wait">
                    我们收到了，还没回复。处理完会在这里出现，也会在首页提醒你。
                  </p>
                )}
            </div>

            {/* 补一句必须落在**这一条**里，否则读者不知道在说哪条 */}
            <div className="mfc-compose">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="补充说明…"
                rows={1}
                maxLength={1000}
                onKeyDown={(e) => {
                  // Enter 发送、Shift+Enter 换行 —— 与聊天一致
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => void send()}
                disabled={!draft.trim() || sending}
              >
                {sending ? "发送中" : "发送"}
              </button>
            </div>
          </>
        ) : (
          <div className="mfc-placeholder">
            <p className="meta">从左边选一条反馈，看看我们怎么回的。</p>
          </div>
        )}
      </section>
    </div>
  );
}