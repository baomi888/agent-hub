"use client";

/**
 * 话题详情（/forum/[postId]）· 贴吧三栏版的外壳。
 *
 * 楼层结构直接复用留言板那套（`kb-post` / `kb-post-h` / `kb-replies` 等类名，
 * 它们在 library.css 里都是顶层选择器）。另写一套的话，以后调留言板的
 * 「我」标记或回复按钮尺寸，论坛这里会悄悄不跟着变 —— 两处长得一样、
 * 修一处坏一处，是这类页面最常见的债。
 *
 * 和留言板只有三处不同：
 *   1. 楼主那一层是**正文**（2000 字），不截断，要能读完。
 *   2. 顶栏与列表页同一套（tb-*），否则点进来像换了个站。
 *   3. **下架之后这一页仍然打得开**：链接可能已经被转发到班级群，
 *      点进来是 404 比"已下架"伤害更大。作者能看到原因并申诉，别人只看到下架。
 *
 * ★ 没有「已解决」这套：论坛只用来讨论和分享，不是答疑系统。真做了这个标记，
 *   一半帖子会永远挂在"未解决"上，另一半会被误标成"解决了"因而没人再回。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import CornIcon from "@/components/CornIcon";
import UserAvatar from "@/components/settings/UserAvatar";
import { api, forumImgUrl } from "@/lib/api";
import { fmtWhen } from "@/lib/time";
import type { BoardPost, ForumDetail } from "@/lib/types";

interface ReplyTarget {
  id: number;
  name: string;
}

/**
 * 图片网格的列数。
 *
 * 1 张给整宽（一张竖构图放两列里会窄得看不清）；2 张并排；3 张以上九宫格。
 * 不做"1 大 2 小"那种混合布局：图片数量一变版式就跳，同一屏里两种节奏会显乱。
 */
function gridCls(n: number): string {
  if (n <= 1) return "one";
  if (n === 2) return "two";
  return "many";
}

export default function ForumTopicPage() {
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
  /** 申诉已提交（本地即时反馈，避免要刷新才看见） */
  const [appealed, setAppealed] = useState(false);
  const [appealing, setAppealing] = useState(false);
  /** 正在放大看的那张图的 hash，空串 = 没开灯箱 */
  const [big, setBig] = useState("");
  // ---- 举报 ----
  // 面板只记"在举报哪一层的谁"，不记整条帖：楼层数据可能被 load() 刷新替换，
  // 存 id + 名字就够，提交时只需要 id。
  const [report, setReport] = useState<{ id: number; name: string } | null>(null);
  const [reportReason, setReportReason] = useState("");
  const [reportBusy, setReportBusy] = useState(false);
  // 本页举报过的楼层 id。只活在这一个页面实例里 —— 刷新后会变成"又能举报"，
  // 但后端 UNIQUE(post_id, uid) 兜住了，重复只会返回 dup 而不是多算一次。
  const [reported, setReported] = useState<number[]>([]);
  const [reasons, setReasons] = useState<{ key: string; label: string }[]>([]);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const formRef = useRef<HTMLDivElement | null>(null);

  /* 灯箱或举报面板开着的时候：ESC 关掉，并把页面滚动锁住。
     不锁的话背景会跟着手指滑，看起来像两张图在互相顶 */
  useEffect(() => {
    if (!big && !report) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (report) setReport(null);
      else setBig("");
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [big, report]);

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

  const openReport = async (p: BoardPost) => {
    setTip("");
    setReport({ id: p.id, name: p.author.name });
    setReportReason("");
    if (reasons.length) return;
    // 举报原因从服务端拿。这里**懒加载**而不是页面进来就拉：绝大多数人不会
    // 举报，为了一次可能不发生的操作先占一个请求不值得。
    try {
      const c = await api.getForumCats();
      setReasons(c.report_reasons ?? []);
    } catch {
      // 拿不到就让"提交"一直灰着，用户能看出是选项没加载出来，
      // 比给一个空列表然后点提交报 400 要好
    }
  };

  const sendReport = async () => {
    if (!report || !reportReason || reportBusy) return;
    setReportBusy(true);
    try {
      await api.reportForumTopic(report.id, reportReason);
      // 重复举报后端也返回成功（dup），前端不区分：对用户来说
      // "已经记下了" 就是这个操作唯一该给出的反馈
      setReported((prev) => (prev.includes(report.id) ? prev : [...prev, report.id]));
      setReport(null);
      setReportReason("");
      setTip("已收到举报，管理员会看。");
    } catch (e) {
      setTip(e instanceof Error ? e.message : "举报没提交上，稍后再试");
    } finally {
      setReportBusy(false);
    }
  };

  const appeal = async () => {
    if (appealing) return;
    setAppealing(true);
    setTip("");    try {
      await api.appealForumTopic(postId);
      setAppealed(true);
    } catch (e) {
      setTip(e instanceof Error ? e.message : "申请失败，请稍后重试");
    } finally {
      setAppealing(false);
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
            {/* 自己的楼层不给举报入口 —— 等于举报自己，点了后端也会拒。
                举报过的楼层换成静态「已举报」，不给第二次点击的机会：
                留着按钮，用户会以为没生效。 */}
            {!p.mine &&
              (reported.includes(p.id) ? (
                <span className="kb-post-act is-done">已举报</span>
              ) : (
                <button
                  type="button"
                  className="kb-post-act"
                  onClick={() => void openReport(p)}
                  aria-label={`举报 ${p.author.name} 的这条`}
                >
                  <CornIcon name="warn" className="kb-post-act-ico" />
                  举报
                </button>
              ))}
          </span>
        </div>
        <p className="kb-post-b">{p.body}</p>
        {/* 配图跟着楼主正文走。放在标题下面会让"正文"和"图"被标题隔开，
            读起来像两个不相干的东西；放在楼层里则和这条帖是一个整体 */}
        {(() => {
          const imgs = isRoot ? data?.topic?.images ?? [] : [];
          if (!imgs.length) return null;
          return (
            <div className={"tb-dimgs " + gridCls(imgs.length)}>
              {imgs.map((h, i) => (
                <button
                  type="button"
                  className="tb-dimg"
                  key={h}
                  onClick={() => setBig(h)}
                  aria-label={`查看第 ${i + 1} 张图`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={forumImgUrl(h)}
                    alt=""
                    loading={i > 1 ? "lazy" : undefined}
                  />
                </button>
              ))}
            </div>
          );
        })()}
        {!isRoot && (p.replies?.length ?? 0) > 0 && (
          <ul className="kb-replies">{p.replies!.map((r) => renderFloor(r, false))}</ul>
        )}
      </div>
    </li>
  );

  /* 论坛不再要求登录（2026-11-14）。这一页更要放开 —— 帖子链接会被转发到
     群里，点进来先看到登录框是最劝退的一种；也不需要先等 auth 就绪，
     否则首屏会闪一句「正在确认登录状态」。 */

  const topic = data?.topic;
  const floor = data?.posts?.[0];
  const replies = data?.posts?.slice(1) ?? [];
  const max = data?.max_reply ?? 300;
  const gone = Boolean(data?.removed);

  return (
    <div className="tb-root">
      <header className="tb-top">
        <div className="tb-top-in">
          <Link href="/forum" className="tb-logo" aria-label="返回论坛">
            <span className="tb-logo-mark">
              <CornIcon name="task" className="h-4 w-4" />
            </span>
            <span className="tb-logo-t">知穗论坛</span>
          </Link>
          <div className="tb-crumb">
            <Link href="/">工作台</Link>
            <i>/</i>
            <Link href="/forum">论坛</Link>
            {topic && (
              <>
                <i>/</i>
                <span>{topic.cat_label}</span>
              </>
            )}
          </div>
          <div className="tb-top-acts">
            <Link href="/forum" className="tb-new">
              <CornIcon name="back" className="h-4 w-4" />
              返回列表
            </Link>
          </div>
        </div>
      </header>

      <main className="tb-detail">
        {loading && (
          <p className="tb-loading" role="status">
            正在加载话题
          </p>
        )}

        {!loading && err && (
          <div className="tb-blank">
            <p className="tb-blank-t">{err}</p>
            <p className="tb-blank-p">可能被作者删了，或者链接被复制坏了。</p>
            <button type="button" className="tb-ghost" onClick={() => void load()}>
              重试
            </button>
          </div>
        )}

        {!loading && !err && topic && (
          <>
            <div className="tb-dhead">
              <h1>{topic.title}</h1>
              <div className="tb-dtags">
                {/* 板块做成纯标签，不做"点一下回列表筛这个板块" ——
                    列表页不读 URL 上的 cat，点了会跳回全部话题，
                    那比不能点更让人困惑。要做就得让列表页接 searchParams，
                    属于另一件事，不在这一批里塞 */}
                <span className="tb-tag">{topic.cat_label}</span>
                {topic.kb && (
                  <Link
                    className="tb-bar-inline"
                    href={`/library/${encodeURIComponent(topic.kb.id)}`}
                  >
                    {topic.kb.name}
                  </Link>
                )}
                {/* 作者和时间只写在楼层头那一处。原来标题下面也写一遍
                    「苞米呀 · 今天 10:15 · 0 条回复」，紧接着楼主的头里又是
                    「管理员 苞米呀 楼主 今天 10:15」—— 同一句话上下隔 20px 说两遍。
                    0 条回复时整个计数都不出现：列表里已经这么处理。
                    下架时也不出现：楼层整块被藏了，写「1 条回复」却在下面
                    找不到任何一条，读起来像加载失败 */}
                {topic.reply_n > 0 && !gone && <span>{topic.reply_n} 条回复</span>}
                {gone && (
                  <span className="tb-dbadge">
                    <CornIcon name="warn" className="h-3.5 w-3.5" />
                    已下架
                  </span>
                )}
              </div>
            </div>

            {gone ? (
              /* 下架之后这一页仍然能打开。别人看到的是"已下架"，作者多一行
                 原因和一个申诉入口 —— 原因不公开：公布违规细节等于二次传播，
                 也让作者在同学面前被点名 */
              <div className="tb-dgone">
                <h2>这条因违反社区规范被下架</h2>
                {data?.reason ? (
                  <p>
                    原因：<b>{data.reason}</b>
                  </p>
                ) : (
                  <p>具体原因只对作者本人显示。</p>
                )}
                <p className="tb-gone-hint">
                  原链接保留这个说明，不会变成"页面不存在"。
                  {data?.reason ? "确认是误判可以申请复核。" : ""}
                </p>
                {data?.can_appeal &&
                  (appealed ? (
                    <p className="tb-gone-hint">已提交复核，管理员会再看一遍。</p>
                  ) : (
                    <button
                      type="button"
                      className="tb-appeal"
                      disabled={appealing}
                      onClick={() => void appeal()}
                    >
                      {appealing ? "提交中…" : "申请复核"}
                    </button>
                  ))}
                {tip && <p className="tb-comp-tip">{tip}</p>}
              </div>
            ) : (
              <>
                <ul className="tb-floors">
                  {floor && renderFloor(floor, true)}
                  {replies.map((p) => renderFloor(p, false))}
                </ul>

                <div className="tb-reply" ref={formRef}>
                  <p className="tb-reply-h">回复</p>
                  <div className="kb-board-form">
                    {replyTo && (
                      <div className="kb-board-replying">
                        <span>
                          回复 <b>@{replyTo.name}</b>
                        </span>
                        <button
                          type="button"
                          onClick={() => setReplyTo(null)}
                          aria-label="取消回复"
                        >
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
          </>
        )}
      </main>

      {/* 灯箱。点背景或按 ESC 关闭 —— 手机上必须留一个大面积可点区域，
          只给一个 22px 的关闭按钮，手指会点不中 */}
      {big && (
        <div
          className="tb-viewer"
          role="dialog"
          aria-modal="true"
          aria-label="查看大图"
          onClick={() => setBig("")}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={forumImgUrl(big)} alt="" />
          <button
            type="button"
            className="tb-viewer-x"
            aria-label="关闭"
            onClick={() => setBig("")}
          >
            <CornIcon name="close" className="h-5 w-5" />
          </button>
        </div>
      )}

      {/* 举报面板。**不写"举报成功，我们会尽快处理"** —— 这个站没有客服，
          能兑现的承诺只有一句"管理员会看"。写了做不到的话，用户下次就不报了。 */}
      {report && (
        <div
          className="tb-report"
          role="dialog"
          aria-modal="true"
          aria-label="举报"
          onClick={() => setReport(null)}
        >
          <div className="tb-report-in" onClick={(e) => e.stopPropagation()}>
            <div className="tb-report-h">
              <b>举报 {report.name} 的这条</b>
              <button
                type="button"
                className="tb-report-x"
                aria-label="关闭"
                onClick={() => setReport(null)}
              >
                <CornIcon name="close" className="h-5 w-5" />
              </button>
            </div>
            <div className="tb-report-opts" role="radiogroup" aria-label="举报原因">
              {reasons.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  role="radio"
                  aria-checked={reportReason === r.key}
                  className={"tb-report-opt" + (reportReason === r.key ? " on" : "")}
                  onClick={() => setReportReason(r.key)}
                >
                  {r.label}
                </button>
              ))}
              {!reasons.length && (
                <p className="tb-report-wait">原因加载中…如果一直不出来，关掉重进这页。</p>
              )}
            </div>
            <p className="tb-report-note">
              举报只有管理员能看到，对方不会收到通知。同一条帖每台设备只能报一次。
            </p>
            <div className="tb-report-acts">
              <button
                type="button"
                className="tb-report-btn"
                onClick={() => setReport(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="tb-report-btn primary"
                disabled={!reportReason || reportBusy}
                onClick={() => void sendReport()}
              >
                {reportBusy ? "提交中…" : "提交举报"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
