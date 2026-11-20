"use client";

/**
 * 我的反馈（用户端，2026-11-20）
 *
 * ★ 为什么要有这个页面
 *   原来用户提交完反馈，只能在**设置页最底下**那一块看到后续，
 *   而没有任何东西告诉他"有人回你了"。提了意见得不到任何回音，
 *   比不提更伤 —— 所以他不会提第二次。
 *
 * ★ 为什么不跟管理员那样做成一级导航
 *   管理员上班就开着，一天看几十次；用户一年提 1–3 条。
 *   为一个一年用两次的功能在侧栏占一格，是拿高频界面的空间换低频功能。
 *   所以入口放在头像菜单 / 设置页，有回音时才在首页冒一条提示（见 FeedbackNotice）。
 *
 * ★ 为什么这里**没有图表**
 *   管理员那三张图是给他分诊用的（"手上还欠多少、大家在抱怨什么"）。
 *   用户不关心全站出错了 6 条，只关心"我这条怎么样了"。
 *   同一份数据，两边的第一眼问题不同，界面就该不同。
 *
 * ★ 多条回复怎么放
 *   一条反馈 = 一张卡 + 卡内一条竖向时间线（上旧下新），
 *   读起来是"我提的 → 你回我 → 我再补 → 你再回"。
 *   默认只摊开最新 2 条，更早的折成一行可点开 —— 用户一年提一条，
 *   但一条可能来回五六次，全摊开会很长。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import type { FeedbackReply, UserFeedbackItem } from "@/lib/types";
import { SkeletonLines } from "@/components/Skeleton";

/** 用户侧状态词。后端也会下发 user_status_label，
 *  这里留一份兜底（老数据 / 接口降级时不至于显示成 open）。 */
const ST_LABEL: Record<string, string> = {
  open: "已收到",
  doing: "正在处理",
  done: "已解决",
  closed: "已关闭",
};

/** 状态色：只用来点一下"现在到哪一步了"，不抢文字的位置 */
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

/** 相对时间。用户看的是"多久之前"，不是"11-17 15:22"这种精确到分的时间戳。 */
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

export default function MyFeedbackPage() {
  // useAuth 的返回形状是 { me, loading, ... }，不是 { user }
  const { me, loading: authLoading } = useAuth();
  const [items, setItems] = useState<UserFeedbackItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 哪些卡片展开了全部历史（默认只显示最新 2 条）
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [sending, setSending] = useState<number | null>(null);
  // 已读只在"首次看到这页"时标一次：每渲染一次就 POST 是浪费，也会让
  // "刚回来又变已读"——用户还没看清红点就消失了
  const markedRef = useRef(false);

  // 主键是 id（后端叫 uid），MeOut = { user: UserOut }
  const authedId = me?.user?.id ?? null;

  const load = useCallback(async () => {
    if (!authedId) return;
    try {
      const res = await api.myFeedback(50, 0);
      setItems(res.items ?? []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败");
      setItems([]);
    }
  }, [authedId]);

  useEffect(() => {
    if (authLoading || !authedId) return;
    // ★ 用 void（异步）而不是直接 await/调用：load() 第一行就可能 setState，
    //   React 19 的 set-state-in-effect 规则不允许在 effect 体里**同步**改状态。
    //   包一层 Promise 让它落到微任务里，首帧不会被额外的级联渲染打断。
    void Promise.resolve().then(load);
  }, [authLoading, authedId, load]);

  // 列表到手 → 标记全部已读（之后退出再进，红点就不该还亮着）
  useEffect(() => {
    if (!authedId || !items || markedRef.current) return;
    markedRef.current = true;
    void api.markFeedbackRead(0).catch(() => {
      // 标已读失败不该影响阅读：下次进页面会再标一次
    });
  }, [authedId, items]);

  const total = items?.length ?? 0;
  const openN = useMemo(
    () => (items ?? []).filter((it) => it.status === "open" || it.status === "doing").length,
    [items],
  );
  const doneN = useMemo(
    () => (items ?? []).filter((it) => it.status === "done").length,
    [items],
  );

  async function send(fid: number) {
    const text = (draft[fid] ?? "").trim();
    if (!text || sending) return;
    setSending(fid);
    try {
      const res = await api.replyMyFeedback(fid, text);
      // 回读一条：后端回的是"补完之后"的整条（含全部回复），
      // 不在前端自己往数组里拼 —— 拼出来的顺序和 id 容易和后端不一致
      setItems((prev) =>
        (prev ?? []).map((it) => (it.id === fid ? res.item : it)),
      );
      setDraft((d) => ({ ...d, [fid]: "" }));
      // 自己补充完后把这张卡展开，让用户看到刚说的话落在了哪里
      setExpanded((e) => ({ ...e, [fid]: true }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "补充失败");
    } finally {
      setSending(null);
    }
  }

  if (authLoading) {
    return (
      <div className="mf-wrap">
        <div className="card card-pad">
          <SkeletonLines widths={["40%", "90%", "70%"]} />
        </div>
      </div>
    );
  }

  return (
    <div className="mf-wrap">
      <header className="mf-head">
        <div>
          <h1>我的反馈</h1>
          <p className="meta">你提的问题、我们的回复，都在这。</p>
        </div>
        <Link className="btn btn-ghost" href="/chat">
          回到对话
        </Link>
      </header>

      {/* 状态条：只有三个数，没有图。用户要确认的只是"有几条还在办" */}
      {total > 0 && (
        <div className="mf-stats card card-pad">
          <div className="mf-stat">
            <b>{total}</b>
            <span>一共提过</span>
          </div>
          <div className="mf-stat">
            <b>{openN}</b>
            <span>还在办</span>
          </div>
          <div className="mf-stat">
            <b>{doneN}</b>
            <span>已解决</span>
          </div>
        </div>
      )}

      {err && <div className="mf-err">{err}</div>}

      {items === null ? (
        <div className="card card-pad">
          <SkeletonLines widths={["90%", "60%"]} />
        </div>
      ) : items.length === 0 ? (
        <div className="card card-pad mf-empty">
          <b>还没提过反馈</b>
          <p className="meta">
            遇到出错、答得不好、想要的功能，都可以在对话页右下角的「反馈」里告诉我们。
            提过的每一条都会出现在这里，包括我们的回复。
          </p>
          <Link className="btn btn-primary" href="/chat">
            去提第一条
          </Link>
        </div>
      ) : (
        <div className="mf-list">
          {items.map((it) => (
            <FeedbackTicket
              key={it.id}
              item={it}
              expanded={!!expanded[it.id]}
              onToggle={() =>
                setExpanded((e) => ({ ...e, [it.id]: !e[it.id] }))
              }
              draft={draft[it.id] ?? ""}
              onDraft={(v) => setDraft((d) => ({ ...d, [it.id]: v }))}
              onSend={() => void send(it.id)}
              sending={sending === it.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** 一条反馈 = 一张卡。卡内是一条时间线，不是左右聊天气泡。 */
function FeedbackTicket({
  item,
  expanded,
  onToggle,
  draft,
  onDraft,
  onSend,
  sending,
}: {
  item: UserFeedbackItem;
  expanded: boolean;
  onToggle: () => void;
  draft: string;
  onDraft: (v: string) => void;
  onSend: () => void;
  sending: boolean;
}) {
  const replies = item.replies ?? [];
  // 折叠：只留最新 2 条。用户提的那句正文永远在卡头上，不占这 2 个名额。
  const HIDE = 2;
  const hiddenCount = Math.max(0, replies.length - HIDE);
  const shown = expanded || hiddenCount === 0 ? replies : replies.slice(-HIDE);
  const stLabel = item.user_status_label || ST_LABEL[item.status] || item.status;
  const tone = ST_TONE[item.status] ?? "st-open";
  const catTone = CAT_TONE[item.category] ?? "cat-other";

  return (
    <article className={`mf-card card${expanded ? " is-open" : ""}`}>
      {/* 卡头：分类色斜杠 + 分类名 + 状态 + 时间 */}
      <header className="mf-card-head">
        <span className={`mf-cat ${catTone}`}>
          <i aria-hidden="true" />
          {item.category_label}
        </span>
        <span className={`mf-st ${tone}`}>{stLabel}</span>
        <time className="meta mf-time" dateTime={new Date(item.created_at * 1000).toISOString()}>
          {ago(item.created_at)}提的
        </time>
      </header>

      <p className="mf-body">{item.content}</p>
      {item.page && <p className="meta mf-page">来自 {item.page}</p>}

      {replies.length > 0 ? (
        <div className="mf-thread">
          {hiddenCount > 0 && (
            <button type="button" className="mf-more" onClick={onToggle}>
              {expanded ? "收起更早的回复" : `展开更早的 ${hiddenCount} 条`}
            </button>
          )}
          {shown.map((r) => (
            <ReplyRow key={r.id} r={r} />
          ))}
        </div>
      ) : (
        <p className="meta mf-wait">
          我们收到了，还没回复。处理完会出现在这里，也会在首页提醒你。
        </p>
      )}

      {/* 补充说明：让用户能落到**这一条**上说话，
          否则"我指的是设置页那个按钮"这种补充没地方放 */}
      <div className="mf-compose">
        <textarea
          value={draft}
          onChange={(e) => onDraft(e.target.value)}
          placeholder="还有要补充的吗？"
          rows={1}
          maxLength={1000}
        />
        <button
          type="button"
          className="btn btn-ghost mf-send"
          onClick={onSend}
          disabled={!draft.trim() || sending}
        >
          {sending ? "发送中" : "补充"}
        </button>
      </div>
    </article>
  );
}

function ReplyRow({ r }: { r: FeedbackReply }) {
  const mine = r.role === "user";
  return (
    <div className={`mf-reply${mine ? " mine" : ""}`}>
      <div className="mf-reply-who">
        <b>{mine ? "我补充" : r.admin_name || "管理员"}</b>
        <time>{ago(r.created_at)}</time>
      </div>
      <p>{r.content}</p>
    </div>
  );
}