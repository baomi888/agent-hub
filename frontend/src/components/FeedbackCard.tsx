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

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

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
  /**
   * 正在「追问」的那一条（0 = 全部收起）。
   *
   * 为什么需要它：一个人提三条互不相干的意见时，管理员的回复是分别落在
   * 各条下面的，用户想补一句"我指的是设置页那个按钮"却没有地方可说 ——
   * 只有一个全局输入框的话，补的话会被当成第四条新反馈。
   */
  const [askFid, setAskFid] = useState(0);
  const [askText, setAskText] = useState("");
  const [asking, setAsking] = useState(false);

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

  /** 在**我这一条**下面补一句话（补完回读列表，不自己拼状态）。 */
  const ask = useCallback(
    async (fid: number) => {
      const t = askText.trim();
      if (!t || asking) return;
      setAsking(true);
      try {
        await api.replyMyFeedback(fid, t);
        setAskText("");
        setAskFid(0);
        loadMine();
      } catch (e) {
        setMsg({ kind: "err", text: e instanceof Error ? e.message : "补充失败" });
      } finally {
        setAsking(false);
      }
    },
    [askText, asking, loadMine]
  );

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
      setMsg({ kind: "ok", text: "已收到，谢谢你。处理进度和开发者回复会显示在下面。" });
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

      {mine.length > 0 ? (
        <>
          <div className="divide" />
          <p className="sec-label" style={{ marginBottom: 6 }}>
            我提过的
          </p>
          {mine.map((it) => (
            <div key={it.id} style={{ padding: "5px 0" }}>
              <div className="run" style={{ gridTemplateColumns: "1fr auto auto" }}>
                <span
                  style={{
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {it.content}
                </span>
                <span className="meta">{fmtTime(it.created_at)}</span>
                <span className={`ctag ${STATUS_CLASS[it.status] ?? ""}`}>
                  {USER_STATUS_TEXT[it.status] ?? it.status_label}
                </span>
              </div>
              {/* 回复链：开发者回复是"我提的有没有人管"的唯一凭据，
                  所以给它比"状态标签"更显眼的位置，而不是再塞进一行小字。
                  多条 = 不同管理员先后跟进的，按时间排，每条署上回复人的名字
                  （用户明确要求看得到是谁回的）。
                  ★ role="user" 的是**用户自己**补充的话，必须标「我补充」：
                    不然会显示成「管理员 回复：<自己的话>」，语义直接反了。 */}
              {(it.replies ?? []).length > 0
                ? (it.replies ?? []).map((r) => {
                    const mineMsg = r.role === "user";
                    return (
                      <div className={`fb-reply${mineMsg ? " mine" : ""}`} key={r.id}>
                        <div className="fb-reply-head">
                          {mineMsg ? "我补充" : `${r.admin_name || "管理员"} 回复`}
                          {r.created_at ? ` · ${fmtTime(r.created_at)}` : ""}
                        </div>
                        <div className="fb-reply-body">{r.content}</div>
                      </div>
                    );
                  })
                : null}

              {/* 在这条下面追问。收起的形态是一个小按钮：列表有 5 条，
                  每条都摊开一个输入框会让「我提过的」变成一张表单。 */}
              {askFid === it.id ? (
                <div className="fb-ask">
                  <textarea
                    className="input"
                    rows={2}
                    autoFocus
                    value={askText}
                    maxLength={dict?.limits.max_reply ?? 1000}
                    placeholder="补充一句（只跟在这条下面，不会变成新的反馈）。"
                    disabled={asking}
                    onChange={(e) => setAskText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault();
                        void ask(it.id);
                      }
                    }}
                  />
                  <div className="fb-ask-foot">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={asking}
                      onClick={() => {
                        setAskFid(0);
                        setAskText("");
                      }}
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={asking || !askText.trim()}
                      onClick={() => void ask(it.id)}
                    >
                      {asking ? "发送中…" : "发送"}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  className="fb-ask-btn"
                  onClick={() => {
                    setAskFid(it.id);
                    setAskText("");
                  }}
                >
                  补充说明
                </button>
              )}
            </div>
          ))}
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
