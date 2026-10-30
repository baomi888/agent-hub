"use client";

// 建库建议卡片 —— SSE `kb_proposal` 事件的落点（板块 D 定的契约）。
//
// 两个场景共用这一张卡，说法不同但决定是同一个"要不要存"：
//   · `no_kb_retrieval`：勾了「检索」但会话还没绑库 → 把这轮对话存成一个库
//   · `web_search`     ：勾了「智能联网」答完了   → 把收集到的外部信息存下来
//
// 四个刻意的决定：
//
// 1. **不自动建库、不自动消失**。这是"把东西存下来"的决定：建库会占配额，
//    知识库页会多一个条目，而删库比建库麻烦得多。卡片自己消失 = 用户永远
//    不知道自己错过了什么，所以它必须留在那儿等一句话。
//
// 2. **409 / 410 不给「重试」**。409 = 这条建议已经处理过（另一个标签页点过、
//    或刷新后重复点），410 = 已过期。这两种再点一万次也不会成功，
//    摆一个重试按钮就是骗用户点。只有网络抖动 / 5xx 才给重试。
//
// 3. **成功后要说清"东西去哪了"**：入库几条、有没有绑到当前会话。
//    只显示"成功"是不够的 —— 用户真正想知道的是"下次提问它会不会用上"。
//
// 4. **`bind` 显式传 true**，而不是依赖后端默认值。卡片上既然写了
//    "会绑到当前会话"，参数就必须是同一个值，否则是前端替后端撒谎。
//
// ⚠️ 当前状态：**组件已写好但尚未挂载**（page.tsx 还没处理 `kb_proposal` 事件）。
//    等后端 SSE 事件上线，在 done 之前的 case 里渲染它即可，组件本身不用改。
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ApiStatusError } from "@/lib/api";
import type { KbProposal, KbProposalConfirmResult } from "@/lib/types";

type Phase =
  | { kind: "pending" }
  | { kind: "confirming" }
  | { kind: "dismissing" }
  | { kind: "confirmed"; result: KbProposalConfirmResult }
  | { kind: "dismissed" }
  | { kind: "expired" }
  | { kind: "failed"; message: string; retryable: boolean; intent: "confirm" | "dismiss" };

/**
 * 后端全站时间戳都是 Unix 秒。万一是毫秒（这种混用在这个仓库里发生过），
 * 直接比会晚 1000 倍 —— 卡片永远显示"还能点"，用户点下去才吃 410。
 * 1e11 秒 ≈ 公元 5138 年，超过它的只可能是毫秒。
 */
function expiresAtSec(v: number): number {
  return v > 1e11 ? Math.floor(v / 1000) : v;
}

const SOURCE_TITLE: Record<KbProposal["source"], string> = {
  no_kb_retrieval: "把这轮聊的存成资料库？",
  web_search: "把这轮联网找到的存成资料库？",
};

export default function KbProposalCard({
  proposal,
  onConfirmed,
  onDismissed,
}: {
  proposal: KbProposal;
  /** 建库成功：父级用它刷新资料库列表 / 绑定状态 */
  onConfirmed?: (r: KbProposalConfirmResult) => void;
  onDismissed?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "pending" });
  // 库名可改：这是要长期留在知识库列表里的名字，落成用户不认识的默认名，
  // 之后没有改名入口就很难受。默认折叠，不打扰主决定。
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(proposal.kb_hint);
  // 过期靠本地时间推，不靠网络 —— 但不能每秒 tick（没必要、也费电），
  // 30 秒粒度足够：卡片过期变灰，用户不会盯着秒看。
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));

  const deadline = expiresAtSec(proposal.expires_at);

  useEffect(() => {
    if (phase.kind !== "pending" && phase.kind !== "failed") return;
    const id = window.setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 30_000);
    return () => window.clearInterval(id);
  }, [phase.kind]);

  // 本地判定过期后统一走 expired，避免「按钮亮着但点了没用」
  const expiredLocally = nowSec >= deadline;
  const expired = phase.kind === "expired" || (phase.kind === "pending" && expiredLocally);
  const busy = phase.kind === "confirming" || phase.kind === "dismissing";

  const confirm = useCallback(async () => {
    if (busy) return;
    setPhase({ kind: "confirming" });
    try {
      const trimmed = name.trim();
      const result = await api.confirmKbProposal(proposal.proposal_id, {
        // 只在用户真改过名字时才传，让后端用它自己的默认值兜底
        ...(trimmed && trimmed !== proposal.kb_hint ? { name: trimmed } : {}),
        bind: true,
      });
      setPhase({ kind: "confirmed", result });
      onConfirmed?.(result);
    } catch (e) {
      const status = (e as ApiStatusError).status;
      const message = (e as Error)?.message || "建库没有完成";
      // 410 过期 / 409 已处理：都不是"再试一次"能解决的，转成确定态
      if (status === 410) {
        setPhase({ kind: "expired" });
        return;
      }
      if (status === 409) {
        setPhase({
          kind: "failed",
          message: "这条建议已经处理过了，刷新页面就能看到最新的资料库。",
          retryable: false,
          intent: "confirm",
        });
        return;
      }
      setPhase({
        kind: "failed",
        message,
        // 没有 status = 网络层失败（超时 / 断网）；5xx 也算可重试
        retryable: status === undefined || status >= 500,
        intent: "confirm",
      });
    }
  }, [busy, name, onConfirmed, proposal.kb_hint, proposal.proposal_id]);

  const dismiss = useCallback(async () => {
    if (busy) return;
    setPhase({ kind: "dismissing" });
    try {
      await api.dismissKbProposal(proposal.proposal_id);
      setPhase({ kind: "dismissed" });
      onDismissed?.();
    } catch (e) {
      const status = (e as ApiStatusError).status;
      if (status === 410) {
        setPhase({ kind: "expired" });
        return;
      }
      // 「忽略」失败不该占着用户注意力：本地直接收起即可。
      // 后端那条 pending 记录 10 分钟后自然不再重复发（见接口文档 §5.1）。
      if (status === 409 || status === 404) {
        setPhase({ kind: "dismissed" });
        onDismissed?.();
        return;
      }
      setPhase({
        kind: "failed",
        message: (e as Error)?.message || "暂时没能忽略，稍后再试",
        retryable: status === undefined || status >= 500,
        intent: "dismiss",
      });
    }
  }, [busy, onDismissed, proposal.proposal_id]);

  // 「收起」只在本地下结论，不调接口：用在"这条建议早就有结论了"的场景
  // （409 已处理 / 404 建议不存在）—— 后端那边是终态，再 dismiss 一次毫无意义。
  const closeLocal = useCallback(() => setPhase({ kind: "dismissed" }), []);

  // ---------- 终态：已建库 ----------
  // ★ 必须排在"过期"之前：确认成功那一刻如果恰好过了 expires_at，
  //   也不能把"已存好"改口成"已过期"。
  if (phase.kind === "confirmed") {
    const r = phase.result;
    const imported = r.imported ?? 0;
    const kbName = r.name || name.trim() || proposal.kb_hint;
    return (
      <div className="kbp-card kbp-card-ok" role="status" aria-live="polite">
        <p className="kbp-title">
          <span className="kbp-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M4 12.5l5 5L20 6.5" />
            </svg>
          </span>
          已存进「{kbName}」
        </p>
        <p className="kbp-meta">
          {imported > 0 ? `入库 ${imported} 条` : "建库完成，但这轮没抓到可入库的内容"}
          {r.chunks ? ` · ${r.chunks} 个片段` : ""}
          {r.bound ? " · 已绑到当前会话，下次提问会自动用它" : " · 还没绑到当前会话"}
        </p>
        {!r.bound && (
          <p className="kbp-hint">
            没绑上的话，在右侧「资料库」里选这个库绑一下，后面的提问才用得上。
          </p>
        )}
        <p className="kbp-links">
          <a href="/knowledge" className="kbp-link">
            去资料库看看
          </a>
        </p>
      </div>
    );
  }

  // ---------- 终态：已忽略 / 已过期 ----------
  // 都收成一行小字。卡片的目的已经结束，不该继续占着消息区的注意力。
  if (phase.kind === "dismissed") {
    return (
      <p className="kbp-done-line" role="status">
        已忽略，不会自动存进资料库。
      </p>
    );
  }
  if (expired) {
    return (
      <p className="kbp-done-line" role="status">
        这条建库建议已经过期了。想存的话，在右侧「资料库」里手动上传或沉淀就行。
      </p>
    );
  }

  // ---------- 待决定 / 提交中 / 失败可重试 ----------
  const failed = phase.kind === "failed" ? phase : null;
  const disabled = busy || expired;
  // 空值不占位：宁可少显示一段，也不要出现「未给主题」这种把内部状态漏给用户的话
  const metaParts = [
    proposal.candidates > 0 ? `可入库 ${proposal.candidates} 条` : "",
    proposal.topic ? `主题：${proposal.topic}` : "",
  ].filter(Boolean);

  return (
    <div className="kbp-card" role="group" aria-labelledby={`kbp-${proposal.proposal_id}`}>
      <p className="kbp-title" id={`kbp-${proposal.proposal_id}`}>
        <span className="kbp-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          </svg>
        </span>
        {SOURCE_TITLE[proposal.source] ?? "存一份到资料库？"}
      </p>

      {/* reason 由后端生成（≤40 字），前端不改写 —— 它知道这轮到底发生了什么，
          前端只知道自己收到了一个事件。 */}
      {proposal.reason && <p className="kbp-reason">{proposal.reason}</p>}

      {renaming ? (
        <label className="kbp-name-field">
          <span className="kbp-name-label">库名</span>
          <input
            className="kbp-name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={60}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                setRenaming(false);
              }
              if (e.key === "Escape") {
                setName(proposal.kb_hint);
                setRenaming(false);
              }
            }}
          />
        </label>
      ) : (
        <p className="kbp-kb">
          <span className="kbp-kb-name">{name.trim() || proposal.kb_hint}</span>
          <button
            type="button"
            className="kbp-rename"
            onClick={() => setRenaming(true)}
            disabled={disabled}
          >
            改名
          </button>
        </p>
      )}

      {/* 元信息：可入库条数 / 主题 / 绑定承诺。
          "会绑到当前会话" 单独一个 nowrap 的 span —— 窄屏（372px）下这行会折，
          不加约束时最后三个字会被拆成「会绑到当前会 / 话」这种单字落行。 */}
      <p className="kbp-meta">
        {metaParts.join(" · ")}
        {metaParts.length > 0 && " · "}
        <span className="kbp-nowrap">会绑到当前会话</span>
      </p>

      {/* 失败要能重试，但只对"重试有意义"的失败给按钮（见文件头第 2 条） */}
      {failed && (
        <p className="kbp-err" role="alert">
          {failed.message}
        </p>
      )}

      <div className="kbp-actions">
        {failed && !failed.retryable ? (
          // 不可重试（409 已处理 / 404 建议不存在）：这种情况下摆「再试一次」
          // 就是骗用户点 —— 探针真的抓到过这个 bug（按钮禁用了、文字还写着再试一次，
          // 自相矛盾）。只给一个「知道了」把卡片收起。
          <button type="button" className="kbp-act-ghost" onClick={closeLocal}>
            知道了
          </button>
        ) : (
          <>
            <button
              type="button"
              className="kbp-act"
              onClick={() => (failed?.intent === "dismiss" ? dismiss() : confirm())}
              disabled={disabled}
            >
              {phase.kind === "confirming" ? "正在建库…" : failed ? "再试一次" : "建库并存入"}
            </button>
            <button type="button" className="kbp-act-ghost" onClick={dismiss} disabled={disabled}>
              {phase.kind === "dismissing" ? "正在收起…" : "不用了"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
