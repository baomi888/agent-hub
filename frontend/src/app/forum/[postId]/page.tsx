"use client";

/**
 * 话题详情（/forum/[postId]）。
 *
 * 楼层结构直接复用留言板那套（`kb-post` / `kb-post-h` / `kb-replies` 等类名，
 * 它们在 library.css 里都是顶层选择器）。另写一套的话，以后调留言板的
 * 「我」标记或回复按钮尺寸，论坛这里会悄悄不跟着变 —— 两处长得一样、
 * 修一处坏一处，是这类页面最常见的债。
 *
 * 和留言板只有一处不同：楼主那一层是**正文**（2000 字），不截断，要能读完。
 *
 * ★ 没有「已解决」这套：论坛只用来讨论和分享，不是答疑系统。真做了这个标记，
 *   一半帖子会永远挂在"未解决"上，另一半会被误标成"解决了"因而没人再回。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import AuthGate from "@/components/AuthGate";
import CornIcon from "@/components/CornIcon";
import MascotLogo from "@/components/chat/MascotLogo";
import UserAvatar from "@/components/settings/UserAvatar";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { fmtWhen } from "@/lib/time";
import type { BoardPost, ForumDetail } from "@/lib/types";

interface ReplyTarget {
  id: number;
  name: string;
}

export default function ForumTopicPage() {
  const auth = useAuth();
  const router = useRouter();
  const params = useParams<{ postId: string }>();
  const postId = Number(params?.postId ?? 0);

  const [data, setData] = useState<ForumDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [tip, setTip] = useState("");
  const [replyTo, setReplyTo] = useState<ReplyTarget | null>(null);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const formRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    if (!postId || Number.isNaN(postId)) {
      setErr("话题地址不对");
      setLoading(false);
      return;
    }
    setLoading(true);
    setErr("");
    try {
      setData(await api.getForumTopic(postId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "话题加载失败");
    } finally {
      setLoading(false);
    }
  }, [postId]);

  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [load]);

  const startReply = (p: BoardPost) => {
    // 自己的楼层也能回复：发完想起漏了一句、隔天来补结果，都是真实需求。
    // 留言板那边曾经因为一句 `if (p.mine) return;` 让按钮点了没反应，
    // 这里从一开始就不设那道拦截。
    setReplyTo({ id: p.id, name: p.author.name });
    setTip("");
    formRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    window.requestAnimationFrame(() => taRef.current?.focus());
  };

  const send = async () => {
    const body = text.trim();
    if (!body || sending || !data) return;
    setSending(true);
    setTip("");
    try {
      await api.replyForumTopic(postId, body, replyTo?.id);
      setText("");
      setEmojiOpen(false);
      setReplyTo(null);
      await load();
      window.requestAnimationFrame(() => {
        const list = document.querySelectorAll(".kb-post");
        const last = list[list.length - 1];
        last?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    } catch (e) {
      setTip(e instanceof Error ? e.message : "发送失败，请稍后重试");
    } finally {
      setSending(false);
    }
  };

  const remove = async (p: BoardPost, isRoot: boolean) => {
    const ask = isRoot
      ? "删除这个话题？它下面的回复会一起消失。"
      : "删除这条回复？删了就看不到了。";
    if (!window.confirm(ask)) return;
    try {
      await api.deleteForumTopic(p.id);
      // 删的是楼主 → 整栋楼没了，回列表；删的是一条回复 → 原地重拉这栋楼
      if (isRoot) router.push("/forum");
      else await load();
    } catch (e) {
      setTip(e instanceof Error ? e.message : "删除失败，请稍后重试");
    }
  };

  const insertSticker = (s: string) => {
    const el = taRef.current;
    const max = data?.max_reply ?? 300;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    const next = (text.slice(0, start) + s + text.slice(end)).slice(0, max);
    setText(next);
    window.requestAnimationFrame(() => {
      el?.focus();
      const pos = Math.min(start + s.length, next.length);
      el?.setSelectionRange(pos, pos);
    });
  };

  /** 一层楼。`isRoot` 是楼主那层：不缩进、正文不截断 */
  const renderFloor = (p: BoardPost, isRoot: boolean) => (
    <li
      className={
        "kb-post" + (p.mine ? " is-mine" : "") + (replyTo?.id === p.id ? " is-target" : "")
      }
      key={p.id}
      id={`forum-post-${p.id}`}
    >
      <UserAvatar
        avatar={p.author.avatar}
        name={p.author.name}
        className={isRoot ? "kb-post-ava" : "kb-post-ava is-reply"}
      />
      <div className="kb-post-main">
        <div className="kb-post-h">
          {p.author.admin && <span className="kb-role">管理员</span>}
          <b>{p.author.name}</b>
          {/* 楼主那层只标「楼主」，不再叠一个「我」——
              两个金标并排是重复信息，而且一眼看不出哪个是身份、哪个是位置。
              自己发的**回复**才标「我」，用来在别人帖子里认出自己那条。 */}
          {isRoot ? (
            <span className="kb-post-me">楼主</span>
          ) : (
            p.mine && <span className="kb-post-me">我</span>
          )}
          {p.reply_to && <span className="kb-post-to">回复 @{p.reply_to.name}</span>}
          <time dateTime={new Date(p.at * 1000).toISOString()}>{fmtWhen(p.at)}</time>
          <span className="kb-post-acts">
            {!isRoot && (
              <button
                type="button"
                className="kb-post-act"
                onClick={() => startReply(p)}
                aria-label={`回复 ${p.author.name}`}
              >
                <CornIcon name="reply" className="kb-post-act-ico" />
                回复
              </button>
            )}
            {p.can_delete && (
              <button
                type="button"
                className="kb-post-act kb-post-del"
                onClick={() => void remove(p, isRoot)}
                aria-label="删除"
              >
                删除
              </button>
            )}
          </span>
        </div>
        <p className="kb-post-b">{p.body}</p>
        {!isRoot && (p.replies?.length ?? 0) > 0 && (
          <ul className="kb-replies">
            {p.replies!.map((r) => renderFloor(r, false))}
          </ul>
        )}
      </div>
    </li>
  );

  if (auth.loading) {
    return (
      <div className="fm-boot">
        <p>正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    return (
      <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />
    );
  }

  const topic = data?.topic;
  const floor = data?.posts?.[0];
  const replies = data?.posts?.slice(1) ?? [];
  const max = data?.max_reply ?? 300;

  return (
    <div className="fm-root">
      <header className="fm-top">
        <div className="fm-top-in">
          <Link href="/forum" aria-label="返回论坛列表">
            <MascotLogo className="fm-logo" />
          </Link>
          <div className="fm-head-text">
            <h1>论坛中心</h1>
          </div>
          <div className="fm-crumb">
            <i>/</i>
            <Link href="/forum">论坛</Link>
            {topic && (
              <>
                <i>·</i>
                <span>{topic.cat_label}</span>
              </>
            )}
          </div>
          <Link href="/forum" className="fm-new" style={{ marginLeft: "auto" }}>
            <CornIcon name="back" className="h-4 w-4" />
            返回列表
          </Link>
        </div>
      </header>

      <main className="fm-detail">
        {loading && <p className="fm-empty">正在加载话题…</p>}
        {!loading && err && (
          <p className="fm-empty">
            {err}{" "}
            <button type="button" className="lib-ghost" onClick={() => void load()}>
              重试
            </button>
          </p>
        )}

        {!loading && !err && topic && (
          <>
            <div className="fm-dhead">
              <h1>{topic.title}</h1>
              <div className="fm-dtags">
                <span className="fm-tag">{topic.cat_label}</span>
                {topic.kb && (
                  <Link className="fm-src" href={`/library/${encodeURIComponent(topic.kb.id)}`}>
                    <CornIcon name="book" className="h-3 w-3" />
                    <span>{topic.kb.name}</span>
                  </Link>
                )}
                <span>
                  {topic.author.name} · {fmtWhen(topic.at)} · {topic.reply_n} 条回复
                </span>
              </div>
            </div>

            <ul className="fm-floors">
              {floor && renderFloor(floor, true)}
              {replies.map((p) => renderFloor(p, false))}
            </ul>

            <div className="fm-reply" ref={formRef}>
              <p className="fm-reply-h">回复</p>
              <div className="kb-board-form">
                {replyTo && (
                  <div className="kb-board-replying">
                    <span>
                      回复 <b>@{replyTo.name}</b>
                    </span>
                    <button type="button" onClick={() => setReplyTo(null)} aria-label="取消回复">
                      取消
                    </button>
                  </div>
                )}
                <textarea
                  ref={taRef}
                  className="kb-board-input"
                  value={text}
                  maxLength={max}
                  rows={3}
                  placeholder={replyTo ? `回复 @${replyTo.name}…` : "说点什么…"}
                  aria-label={replyTo ? `回复 ${replyTo.name}` : "回复内容"}
                  onChange={(e) => {
                    setText(e.target.value);
                    if (tip) setTip("");
                  }}
                  onKeyDown={(e) => {
                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                      e.preventDefault();
                      void send();
                    }
                    if (e.key === "Escape" && replyTo) setReplyTo(null);
                  }}
                />
                {emojiOpen && (
                  <div className="kb-board-emoji" role="group" aria-label="官方表情">
                    {(data?.stickers ?? []).map((s) => (
                      <button
                        key={s}
                        type="button"
                        className="kb-board-sticker"
                        onClick={() => insertSticker(s)}
                        aria-label={`插入表情 ${s}`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
                <div className="kb-board-bar">
                  <button
                    type="button"
                    className={"kb-board-emoji-btn" + (emojiOpen ? " on" : "")}
                    aria-expanded={emojiOpen}
                    onClick={() => setEmojiOpen((v) => !v)}
                  >
                    <CornIcon name="smile" className="h-4 w-4" />
                    表情
                  </button>
                  <span className="kb-board-count">
                    {text.length}/{max}
                  </span>
                  <button
                    type="button"
                    className="lib-share kb-board-send"
                    disabled={sending || !text.trim()}
                    onClick={() => void send()}
                  >
                    {sending ? "发送中…" : replyTo ? "回复" : "发送"}
                  </button>
                </div>
                {tip && <p className="kb-board-tip">{tip}</p>}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
