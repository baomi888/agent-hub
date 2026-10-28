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

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function FeedbackCard({ page }: { page: string }) {
  const [dict, setDict] = useState<FeedbackDict | null>(null);
  const [category, setCategory] = useState("bug");
  const [content, setContent] = useState("");
  const [contact, setContact] = useState("");
  const [showContact, setShowContact] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [mine, setMine] = useState<UserFeedbackItem[]>([]);

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
      setMsg({ kind: "ok", text: "已收到，谢谢你。处理进度和开发者回复会显示在下面。" });
      loadMine();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "提交失败，请稍后再试" });
    } finally {
      setBusy(false);
    }
  }, [category, contact, content, loadMine, max, min, page]);

  const options = (dict?.categories ?? []).map((c) => ({ value: c.value, label: c.label }));

  return (
    <div className="card card-pad">
      <div className="card-head">
        <span className="card-title">告诉我们哪里不对</span>
        <span className="meta">开发者会看到</span>
      </div>

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
                <span className={`ctag ${STATUS_CLASS[it.status] ?? ""}`}>{it.status_label}</span>
              </div>
              {/* 开发者回复：这是用户判断"我提的有没有人管"的唯一凭据，
                  所以给它比"状态标签"更显眼的位置，而不是再塞进一行小字 */}
              {it.reply ? (
                <div className="fb-reply">
                  <div className="fb-reply-head">
                    开发者回复
                    {it.replied_at ? ` · ${fmtTime(it.replied_at)}` : ""}
                  </div>
                  <div className="fb-reply-body">{it.reply}</div>
                </div>
              ) : null}
            </div>
          ))}
        </>
      ) : null}
    </div>
  );
}
