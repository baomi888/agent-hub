"use client";

/**
 * 公开知识库留言板（论坛式留言）。
 *
 * 覆盖两类库：官方课程库、用户自己公开的库。所以它只认 `kbId`，
 * 不知道也不关心这个库是官方还是同学分享的。
 *
 * 只用一处实现的原因：官方库详情页要把它铺在页面底部，广场上的同学库要把它
 * 塞进弹层。两处各写一份列表 + 输入框，最后必然漂移成"详情页能删、弹层不能删"
 * 这种毛病 —— 差异只在容器的 CSS，`compact` 一个开关就够了。
 *
 * 三条取舍：
 *   1. **表情由后端下发**（`stickers`）。前端硬编码一份的话，以后加一个表情
 *      要改两处、还得确保两端顺序一致，不如多传几十字节。
 *   2. 表情直接拼进正文，不单独存字段。它是普通字符，存成一个独立的
 *      `sticker` 列只会多出"只有表情没有文字"这条边界，没有别的好处。
 *   3. 分页用游标（`before=最后一条的 id`）而不是页码：留言板会一边看一边有人发，
 *      offset 分页在这种情况下会重复或漏条。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import CornIcon from "@/components/CornIcon";
import UserAvatar from "@/components/settings/UserAvatar";
import { api } from "@/lib/api";
import { fmtWhen } from "@/lib/time";
import type { BoardData, BoardPost } from "@/lib/types";

/** 与后端 kb_board.MAX_LEN 一致。超了后端也会拒，这里只是不让用户白打一遍 */
const MAX_LEN = 300;

interface Props {
  kbId: string;
  /** 弹层里用：标题小一号、去掉重复的引导语 */
  compact?: boolean;
  /** 条数变化时通知外面（广场卡片上的「N 条留言」要跟着变） */
  onCount?: (n: number) => void;
}

export default function KbBoard({ kbId, compact = false, onCount }: Props) {
  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  /** 输入框里的文字 */
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  /** 发送失败的提示，贴在输入框下面 */
  const [tip, setTip] = useState("");
  const [emojiOpen, setEmojiOpen] = useState(false);
  /** 正在翻更早的留言 */
  const [moreBusy, setMoreBusy] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // onCount 是从父组件传下来的，父组件每渲染一次就是新函数 —— 放进 load 的
  // 依赖里会让它在父组件随便一次 state 变化后重新拉一遍留言。用 ref 取最新的。
  const countRef = useRef(onCount);
  useEffect(() => {
    countRef.current = onCount;
  }, [onCount]);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await api.getKbBoard(kbId);
      setData(d);
      countRef.current?.(d.total);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "留言加载失败");
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    if (!kbId) return;
    // 放宏任务里：直接在 effect 体里 setLoading 会被 react-hooks/set-state-in-effect
    // 判成级联渲染（和 /library 两页同样的处理）
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [kbId, load]);

  const bump = (posts: BoardPost[], total: number) => {
    setData((d) => (d ? { ...d, posts, total } : d));
    countRef.current?.(total);
  };

  const send = async () => {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    setTip("");
    try {
      const r = await api.postKbBoard(kbId, body);
      const d = data;
      bump([r.post, ...(d?.posts ?? [])], (d?.total ?? 0) + 1);
      setText("");
      setEmojiOpen(false);
    } catch (e) {
      setTip(e instanceof Error ? e.message : "发送失败，请稍后重试");
    } finally {
      setSending(false);
    }
  };

  const remove = async (p: BoardPost) => {
    if (!window.confirm("删除这条留言？删了就看不到了。")) return;
    try {
      await api.deleteKbBoardPost(kbId, p.id);
      const d = data;
      bump(
        (d?.posts ?? []).filter((x) => x.id !== p.id),
        Math.max(0, (d?.total ?? 1) - 1)
      );
    } catch (e) {
      setTip(e instanceof Error ? e.message : "删除失败，请稍后重试");
    }
  };

  const loadMore = async () => {
    const posts = data?.posts ?? [];
    const last = posts[posts.length - 1];
    if (!last || moreBusy) return;
    setMoreBusy(true);
    setTip("");
    try {
      const d = await api.getKbBoard(kbId, last.id);
      setData((cur) =>
        cur ? { ...cur, posts: [...cur.posts, ...d.posts], has_more: d.has_more } : d
      );
    } catch (e) {
      setTip(e instanceof Error ? e.message : "加载失败，请稍后重试");
    } finally {
      setMoreBusy(false);
    }
  };

  /** 把表情插到光标处。用 selectionStart 而不是拼在末尾——用户可能想插在句子中间 */
  const insertSticker = (s: string) => {
    const el = taRef.current;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    const next = (text.slice(0, start) + s + text.slice(end)).slice(0, MAX_LEN);
    setText(next);
    window.requestAnimationFrame(() => {
      el?.focus();
      const pos = Math.min(start + s.length, next.length);
      el?.setSelectionRange(pos, pos);
    });
  };

  const total = data?.total ?? 0;
  const posts = data?.posts ?? [];

  return (
    <section className={"kb-board" + (compact ? " is-compact" : "")} aria-label="留言板">
      <div className="kb-board-h">
        <span className="eyebrow">留言板</span>
        <span className="rule" />
        <span className="lib-count">{total} 条</span>
      </div>

      {!compact && (
        <p className="kb-board-lead">
          对这个库有想法、有补充、有疑问，都可以写在这里。只能发文字和表情，不发文件。
        </p>
      )}

      <div className="kb-board-form">
        <textarea
          ref={taRef}
          className="kb-board-input"
          value={text}
          maxLength={MAX_LEN}
          rows={compact ? 2 : 3}
          placeholder={compact ? "说点什么…" : "写下你的问题或补充…"}
          aria-label="留言内容"
          onChange={(e) => {
            setText(e.target.value);
            if (tip) setTip("");
          }}
          onKeyDown={(e) => {
            // Ctrl/Cmd + Enter 发送：留言框里 Enter 应该是换行，不能直接发
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              void send();
            }
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
            {text.length}/{MAX_LEN}
          </span>
          <button
            type="button"
            className="lib-share kb-board-send"
            disabled={sending || !text.trim()}
            onClick={() => void send()}
          >
            {sending ? "发送中…" : "发送"}
          </button>
        </div>
        {tip && <p className="kb-board-tip">{tip}</p>}
      </div>

      {loading && <p className="kb-board-empty">正在加载留言…</p>}
      {!loading && err && (
        <p className="kb-board-empty">
          {err}{" "}
          <button type="button" className="lib-ghost" onClick={() => void load()}>
            重试
          </button>
        </p>
      )}
      {!loading && !err && posts.length === 0 && (
        <p className="kb-board-empty">还没有人留言，来做第一个。</p>
      )}

      {posts.length > 0 && (
        <ul className="kb-board-list">
          {posts.map((p) => (
            <li className={"kb-post" + (p.mine ? " is-mine" : "")} key={p.id}>
              <UserAvatar
                avatar={p.author.avatar}
                name={p.author.name}
                className="kb-post-ava"
              />
              <div className="kb-post-main">
                <div className="kb-post-h">
                  <b>{p.author.name}</b>
                  {p.mine && <span className="kb-post-me">我</span>}
                  <time dateTime={new Date(p.at * 1000).toISOString()}>
                    {fmtWhen(p.at)}
                  </time>
                  {p.can_delete && (
                    <button
                      type="button"
                      className="kb-post-del"
                      onClick={() => void remove(p)}
                      aria-label="删除这条留言"
                    >
                      删除
                    </button>
                  )}
                </div>
                <p className="kb-post-b">{p.body}</p>
              </div>
            </li>
          ))}
        </ul>
      )}

      {data?.has_more && (
        <button
          type="button"
          className="lib-ghost kb-board-more"
          disabled={moreBusy}
          onClick={() => void loadMore()}
        >
          {moreBusy ? "加载中…" : "加载更早的留言"}
        </button>
      )}
    </section>
  );
}
