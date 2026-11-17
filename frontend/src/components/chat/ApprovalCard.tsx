"use client";

// 高风险工具确认卡 —— SSE `approval` 帧的落点（L4，2026-11-17）。
//
// 这张卡的唯一职责：把「用户真的点了一下」变成一个由服务端签发的 token。
// 卡上点一下 → POST /api/chat/approval → 拿到 token → 父级带 token + turn_id
// 重发本轮提问 → 工具这次才真的执行。
//
// 四个刻意的决定：
//
// 1. **参数要露出来**。用户点的不是"允许这个工具"，是"允许下载这个地址"。
//    只写工具名等于让人闭眼签字。
//
// 2. **倒计时是真的**（后端 TTL 默认 120 秒）。到点就把按钮换掉，
//    而不是让用户点下去吃一个 404——那样他会以为是自己点错了。
//
// 3. **404 不给"重试"**：过期 / 已处理 / 不是你的，三种结果在后端是同一个 404
//    （区分了就等于告诉调用方"这个 id 存在"）。三种情况再点都不会成功，
//    所以这里只请他重新提问。
//
// 4. **视觉上沿用建库建议卡**（.kbp-* 那套）：同一条消息流里，两种卡片都是
//    "需要你做决定的东西"，长得不一样反而要用户重新学一遍。
//    差别只在一个地方：这张卡的顶线是警示色，主按钮写的是动作本身。

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ApiStatusError } from "@/lib/api";
import type { IssuedApproval, ToolApprovalRequest } from "@/lib/types";

type Phase =
  | { kind: "waiting" }
  | { kind: "busy" }
  | { kind: "approved" }
  | { kind: "dismissed" }
  | { kind: "expired" }
  | { kind: "failed"; message: string };

/** 工具名 → 人话。未收录的原样显示——编一个更好听的名字比显示真名更危险。 */
const TOOL_LABEL: Record<string, string> = {
  download_file: "下载文件",
};

export default function ApprovalCard({
  approval,
  onApproved,
}: {
  approval: ToolApprovalRequest;
  /** 拿到 token 后由父级重发本轮提问（前端不在这里自己发） */
  onApproved: (issued: IssuedApproval, req: ToolApprovalRequest) => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "waiting" });
  const [left, setLeft] = useState(Math.max(0, Math.round(approval.expires_in)));

  // 倒计时：每秒减一，到 0 就把卡片切成"已过期"。
  // 只在 waiting 时跑 —— 已经点过/已拒绝的卡片不需要再跳秒（跳了反而像还在等）。
  useEffect(() => {
    if (phase.kind !== "waiting") return;
    if (left <= 0) {
      setPhase({ kind: "expired" });
      return;
    }
    const t = window.setTimeout(() => setLeft((n) => n - 1), 1000);
    return () => window.clearTimeout(t);
  }, [phase.kind, left]);

  const allow = useCallback(async () => {
    if (phase.kind !== "waiting") return;
    setPhase({ kind: "busy" });
    try {
      const issued = await api.issueApproval(approval.request_id);
      setPhase({ kind: "approved" });
      onApproved(issued, approval);
    } catch (e) {
      const status = (e as ApiStatusError).status;
      if (status === 404) {
        setPhase({ kind: "expired" });
        return;
      }
      setPhase({
        kind: "failed",
        message: e instanceof Error ? e.message : "确认失败，请稍后再试",
      });
    }
  }, [approval, onApproved, phase.kind]);

  const label = TOOL_LABEL[approval.tool_name] ?? approval.tool_name;

  if (phase.kind === "approved") {
    return (
      <p className="kbp-done-line" role="status">
        已允许，正在重新执行这一步…
      </p>
    );
  }
  if (phase.kind === "dismissed") {
    return (
      <p className="kbp-done-line" role="status">
        已忽略。这个工具不会执行，你可以随时重新提问。
      </p>
    );
  }
  if (phase.kind === "expired") {
    return (
      <p className="kbp-done-line" role="status">
        这次确认已经过期（等太久了）。重新问一次就好。
      </p>
    );
  }

  return (
    <div
      className="kbp-card apr-card"
      role="group"
      aria-labelledby={`apr-${approval.request_id}`}
    >
      <p className="kbp-title" id={`apr-${approval.request_id}`}>
        <svg className="kbp-mark" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z" />
          <path d="M12 9v4M12 16h.01" />
        </svg>
        需要你确认：{label}
      </p>
      <p className="kbp-reason">
        它会改动外部状态，只有你点了允许才会执行。
      </p>
      {/* 参数原文：用户签的是"这个地址/这个文件"，不是"这个工具"。
          内层 span 是必需的：截断（max-height + overflow）只能挂在**没有内边距**
          的元素上，否则被裁掉的下一行会露在 padding 里（见 approval.css 注释）。 */}
      <p className="apr-args" title={approval.input}>
        <span className="apr-args-in">{approval.input}</span>
      </p>
      <p className="kbp-meta">
        {left > 0 ? `还剩 ${left} 秒可以确认` : "正在失效…"}
      </p>
      {phase.kind === "failed" && (
        <p className="kbp-err" role="alert">
          {phase.message}
        </p>
      )}
      <div className="kbp-actions">
        <button
          type="button"
          className="kbp-act"
          onClick={() => void allow()}
          disabled={phase.kind === "busy"}
        >
          {phase.kind === "busy" ? "确认中…" : "允许执行"}
        </button>
        <button
          type="button"
          className="kbp-act-ghost"
          onClick={() => setPhase({ kind: "dismissed" })}
          disabled={phase.kind === "busy"}
        >
          不用了
        </button>
      </div>
    </div>
  );
}
