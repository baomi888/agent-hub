"use client";

/**
 * 首页的"你的反馈有回音了"提示条（2026-11-20）
 *
 * ★ 为什么要有它
 *   反馈面板、回复链、状态这些功能早就有了，用户就是不知道。
 *   提了意见得不到任何回音，比不提更伤 —— 他不会提第二次。
 *   这个组件是整个用户端反馈闭环里**最关键的一环**：主动送达。
 *
 * ★ 为什么是一条不挡内容的横条，不是弹窗
 *   弹窗会打断"我正在问问题"这件事，而且必须点掉才能继续，
 *   一个一年提两次意见的用户被弹窗拦下来的观感是"这站在烦我"。
 *   横条放在内容顶部、可关闭、点进详情即消失，成本最低。
 *
 * ★ 文案随状态变（这是用户明确要求的那点）
 *   · 有管理员回复 → 「X 回复了你 N 天前提的「摘要」」
 *   · 只是状态变了 → 「你提的「摘要」正在处理中 / 已解决」
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { FeedbackUnreadItem } from "@/lib/types";

/** 本次会话里别再弹出来的记忆（关掉一次 = 这次别烦我了） */
const DISMISS_KEY = "baomi:feedback-notice-dismissed";

function ago(ts: number): string {
  if (!ts) return "";
  const d = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (d < 3600) return `${Math.max(1, Math.floor(d / 60))} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  if (d < 86400 * 30) return `${Math.floor(d / 86400)} 天前`;
  const dt = new Date(ts * 1000);
  return `${dt.getMonth() + 1} 月 ${dt.getDate()} 日`;
}

export default function FeedbackNotice({ authedId }: { authedId: string | null }) {
  const [items, setItems] = useState<FeedbackUnreadItem[]>([]);
  const [latest, setLatest] = useState(0);
  // dismissed 存"被关掉的那个最新时间戳"而不是 true：
  // 关掉之后**又来了新的回音**，应该重新冒出来 —— 存布尔值就永远不会再提醒了。
  //
  // ★ 初值用**惰性初始化函数**直接读 localStorage，而不是先 null 再在 effect 里 set：
  //   后者被 React 19 的 set-state-in-effect 规则拦（同步 setState 会多一次级联渲染），
  //   而且首帧会闪一下"没关过"。惰性初始化是同步的、没有这两问题。
  //   服务端渲染时没有 localStorage，所以用 typeof window 守卫。
  const [dismissedAt, setDismissedAt] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(DISMISS_KEY);
      return raw ? Number(raw) || null : null;
    } catch {
      return null; // 隐私模式下 localStorage 会抛
    }
  });

  useEffect(() => {
    if (!authedId) return;
    let alive = true;
    void api
      .feedbackUnread()
      .then((res) => {
        if (!alive) return;
        setItems(res.items ?? []);
        setLatest(res.latest ?? 0);
      })
      .catch(() => {
        // 提醒失败不该让首页报错：它是加分项，不是主流程
      });
    return () => {
      alive = false;
    };
  }, [authedId]);

  const dismiss = useCallback(() => {
    setDismissedAt(latest);
    try {
      window.localStorage.setItem(DISMISS_KEY, String(latest));
    } catch {
      // 同上
    }
  }, [latest]);

  if (!authedId || items.length === 0) return null;
  // 关掉之后没有新回音 → 这次别再出现
  if (dismissedAt !== null && latest <= dismissedAt) return null;

  const first = items[0];
  const more = items.length - 1;
  const head = first.replied
    ? `「${first.excerpt}」有回复了`
    : `「${first.excerpt}」${first.user_status_label}`;

  return (
    <div className="fbn" role="status">
      <span className="fbn-dot" aria-hidden="true" />
      <p className="fbn-text">
        {head}
        <span className="fbn-meta">
          {" "}
          · {ago(first.changed_at)}
          {more > 0 ? ` · 另外还有 ${more} 条` : ""}
        </span>
      </p>
      <Link className="fbn-go" href="/my-feedback">
        去看看
      </Link>
      <button type="button" className="fbn-x" onClick={dismiss} aria-label="关闭提醒">
        ×
      </button>
    </div>
  );
}