"use client";

// 用户反馈卡片（设置页用）。形态对齐 DeepSeek Web：**几个分类按钮 + 一段话**，
// 不搞冗长的表单 —— 用户提意见的耐心只有十几秒，字段越多越没人填。
//
// 三个刻意的决定：
//
// 1. **分类按钮的选项来自后端**（`/api/user-feedback/dict`），不写死在前端。
//    前端硬编码一份、后端校验另一份，加一个分类要改两处，
//    而这类不一致的表现是"按钮能点、提交 400"。
//
// 2. **提交后立刻显示"我提过的"**，带处理状态。
//    用户看不到回音就不会提第二次；这也是"反馈有没有人管"的唯一证据。
//
// 3. **不把内容塞进 URL / 不打日志**：反馈可能含个人信息，只走 POST body。
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { FeedbackDict, UserFeedbackItem } from "@/lib/types";
import { Seg } from "./console/ui";

const STATUS_CLASS: Record<string, string> = {
  open: "warn",
  doing: "info",
  done: "ok",
  closed: "",
};

/**
 * 用户侧的状态说法。
 *
 * 「待处理」是**管理员**的词（我还没处理它），对提意见的人是反的 ——
 * 他刚点完提交，最想知道的是"你们收到了吗"。所以这一个状态在用户侧
 * 显示为「已收到」，其余照旧。字典仍随后端（不硬编码选项，只换措辞）。
 */
const USER_STATUS_TEXT: Record<string, string> = {
  open: "已收到",
};

export default function FeedbackCard({
  page,
  chrome = true,
}: {
  page: string;
  /** false = 不带外层卡片与标题，供弹窗内嵌用。
   *  弹窗自己已经有标题栏，再套一层「告诉我们哪里不对」就成了标题套标题。 */
  chrome?: boolean;
}) {
  const [dict, setDict] = useState<FeedbackDict | null>(null);
  const [category, setCategory] = useState("bug");
  const [content, setContent] = useState("");
  const [contact, setContact] = useState("");
  const [showContact, setShowContact] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [mine, setMine] = useState<UserFeedbackItem[]>([]);
  // ★ 2026-11-20：追问（在某条下面补一句）搬去 /my-feedback 了。
  //   弹窗只负责"提交一条"，追问需要看到上下文（他回了什么、我补什么），
  //   那是会话页该做的事。原来这里的 askFid / askText / asking / ask()
  //   一并删掉 —— 留着不用会被 lint 拦，也会让下个人以为这里还能追问。

  const loadMine = useCallback(() => {
    // 取 5 条而不是 3 条：开发者回复可能落在稍早的那一条上，
    // 列表太短会让"他回我了"这件事用户根本看不到，整个回复功能就白做了。
    api
      .myFeedback(5, 0)
      .then((r) => setMine(r.items))
      .catch(() => {
        /* 拉不到历史不影响提交 */
      });
  }, []);


  useEffect(() => {
    // 字典拿不到就退回一个最小可用集合：至少不能让用户连按钮都看不到。
    // （后端 4xx/5xx 时这里会走 catch，而不是把整张卡片留白）
    api
      .feedbackDict()
      .then((d) => setDict(d))
      .catch(() => {
        setDict({
          categories: [
            { value: "bug", label: "出错了" },
            { value: "answer", label: "回答不满意" },
            { value: "feature", label: "功能建议" },
            { value: "other", label: "其他" },
          ],
          statuses: [],
          transitions: {},
          limits: {
            min_content: 5,
            max_content: 2000,
            max_contact: 120,
            max_page: 120,
            min_reply: 1,
            max_reply: 1000,
          },
        });
      });
    loadMine();
  }, [loadMine]);

  const min = dict?.limits.min_content ?? 5;
  const max = dict?.limits.max_content ?? 2000;
  const tooShort = content.trim().length > 0 && content.trim().length < min;

  const submit = useCallback(async () => {
    const text = content.trim();
    if (text.length < min) {
      setMsg({ kind: "err", text: `内容至少 ${min} 个字` });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.submitFeedback({
        category,
        content: text,
        contact: contact.trim(),
        page,
      });
      setContent("");
      setContact("");
      setMsg({ kind: "ok", text: "已收到，谢谢你。处理进度和回复会出现在「我的反馈」里，也会在首页提醒你。" });
      loadMine();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "提交失败，请稍后再试" });
    } finally {
      setBusy(false);
    }
  }, [category, contact, content, loadMine, min, page]);

  const options = (dict?.categories ?? []).map((c) => ({ value: c.value, label: c.label }));

  const inner = (
    <>
      <div style={{ marginTop: 10 }}>
        <Seg
          ariaLabel="反馈分类"
          options={options.length ? options : [{ value: "bug", label: "出错了" }]}
          value={category}
          onChange={setCategory}
          stretch
        />
      </div>

      <div className="field" style={{ marginTop: 12 }}>
        <span className="f">具体说明</span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value.slice(0, max))}
          rows={4}
          placeholder="尽量说清「你在哪一步、点了什么、期望什么、实际发生了什么」——越具体越好定位。"
          style={{ width: "100%", resize: "vertical" }}
        />
        <p className="hint" style={{ marginTop: 4 }}>
          {content.trim().length} / {max}
          {tooShort ? `（至少 ${min} 个字）` : ""}
        </p>
      </div>

      {showContact ? (
        <div className="field">
          <span className="f">联系方式（可选）</span>
          <input
            value={contact}
            onChange={(e) => setContact(e.target.value.slice(0, dict?.limits.max_contact ?? 120))}
            placeholder="邮箱 / 微信，只有开发者能看到"
          />
        </div>
      ) : (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          style={{ marginTop: 2 }}
          onClick={() => setShowContact(true)}
        >
          想被回访？留个联系方式
        </button>
      )}

      {msg ? (
        <p
          className="hint"
          style={{ marginTop: 10, color: msg.kind === "err" ? "var(--red-ink)" : "var(--success-ink)" }}
        >
          {msg.text}
          {/* 提交成功后立刻给一个"去看处理进度"的口子（2026-11-20）：
              用户刚说完一件事，此刻正是他心里最想知道"有人管吗"的时候，
              错过这一下，他下次想起来就是"这站不理我"。
              用原生 <a> 而不是 next/link：这里可能出现在浮层里，
              整页跳转比在浮层内导航更符合"我现在要离开去看另一页"的意图。 */}
          {msg.kind === "ok" ? (
            <>
              {" "}
              <a href="/my-feedback" className="fb-goto">
                去看处理进度 →
              </a>
            </>
          ) : null}
        </p>
      ) : null}

      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={busy || content.trim().length < min}
          onClick={() => void submit()}
        >
          {busy ? "提交中…" : "提交"}
        </button>
      </div>

      {/* 「我提过的」只留摘要 + 去会话中心看（2026-11-20 第三次改版）。
          ★ 原来这里是一长串列表，每条带状态、回复链、补充框。
            用户反馈"这三个地方各长一张脸"（主页弹窗 / 设置页 / 反馈中心），
            同一个东西三种样子，观感是站不完整。
            现在分工明确：**提交**在这个弹窗，**看对话**去 /my-feedback
            （那边是左会话 + 右对话的微信形态，能看不同管理员的回复）。
          ★ 弹窗里不做左右两栏：560 宽塞两栏会把每栏压到 250 以下，
            中文断行到没法读。它的任务是"快速提一条"。 */}
      {mine.length > 0 ? (
        <>
          <div className="divide" />
          <div className="fb-mine-head">
            <p className="sec-label" style={{ margin: 0 }}>
              我提过的 · {mine.length} 条
            </p>
            <a href="/my-feedback" className="fb-mine-all">
              打开我的反馈 →
            </a>
          </div>
          {/* 只显示最近一条的摘要，让用户确认"我刚才那句在不在" */}
          <div className="fb-mine-last">
            <span className="fb-mine-text">{mine[0].content}</span>
            <span className={`ctag ${STATUS_CLASS[mine[0].status] ?? ""}`}>
              {USER_STATUS_TEXT[mine[0].status] ?? mine[0].status_label}
            </span>
          </div>
          {mine.length > 1 ? (
            <p className="meta fb-mine-more">另有 {mine.length - 1} 条，在「我的反馈」里</p>
          ) : null}
        </>
      ) : null}
    </>
  );

  // 弹窗里不套卡片：卡片边距 + 标题栏会和弹窗自己的外壳重复
  if (!chrome) return <div className="fb-bare">{inner}</div>;

  return (
    <div className="card card-pad">
      <div className="card-head">
        <span className="card-title">告诉我们哪里不对</span>
      </div>
      {inner}
    </div>
  );
}
