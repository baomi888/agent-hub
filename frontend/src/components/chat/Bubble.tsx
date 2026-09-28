"use client";

// 消息气泡：用户气泡 + AI 回答（工具调用条 / 计划卡 / 引用溯源 / 操作条）
// memo 优化：流式回答时每个 token 都会更新 messages 数组，
// 已完成的旧消息对象引用不变，跳过重渲染，避免长对话越答越卡
import { memo, useMemo, useState } from "react";
import type { UiMessage } from "./types";
import { TOOL_META, toolLabel } from "./constants";
import { parseRefs, renderContent } from "./content";
import PlanCard, { detectPlanTopic, isPlanRequest, parsePlan } from "./plan";
import { useCopy } from "@/lib/useCopy";
import HubMark from "./HubMark";

const Bubble = memo(function Bubble({
  m,
  userQuestion,
  onCopy,
  onForward,
  onRetry,
  onCopyFail,
  onFeedback,
  onPreviewFile,
  onDeleteTurn,
}: {
  m: UiMessage;
  userQuestion?: string;
  onCopy?: (text: string) => void;
  onForward?: (text: string) => void;
  onRetry?: (assistantId: string, question: string) => void;
  /** 剪贴板不可用等静默失败，交给上层出 toast */
  onCopyFail?: () => void;
  /** 赞/踩：后端还没有反馈接口，先在前端给一次可见回应 */
  onFeedback?: () => void;
  /** 打开该文件全文预览（未绑定资料库时不传，入口就不显示） */
  onPreviewFile?: (source: string) => void;
  /** 删除该消息及其所在的一轮对话 */
  onDeleteTurn?: (msgDbId: number) => void;
}) {
  const [feedback, setFeedback] = useState<"like" | "dislike" | null>(null);
  const { copied, copy } = useCopy();
  // 溯源展开状态：同一时刻只展开一条，避免长回答把页面撑得太长
  const [openSrc, setOpenSrc] = useState<number | null>(null);
  // 必须放在提前 return 之前，保证 hooks 调用顺序恒定
  const refItems = useMemo(() => parseRefs(m.refs), [m.refs]);
  const srcMap = useMemo(
    () => new Map((m.sources ?? []).map((s) => [s.id, s])),
    [m.sources]
  );

  const handleCopy = async () => {
    const ok = await copy(m.content);
    if (ok) {
      onCopy?.(m.content);
    } else {
      // 非 HTTPS / 无剪贴板权限时会走到这里，原来是完全静默的
      onCopyFail?.();
    }
  };

  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%]">
          {m.attachments && m.attachments.length > 0 && (
            <div className="msg-attachments">
              {m.attachments.map((a) =>
                a.type.startsWith("image/") ? (
                  <img key={a.id} src={a.url} alt={a.name} className="msg-img" />
                ) : (
                  <div key={a.id} className="msg-file">
                    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                      <path d="M8 13h8M8 17h5" />
                    </svg>
                    <span className="max-w-[180px] truncate">{a.name}</span>
                  </div>
                )
              )}
            </div>
          )}
          {m.content && (
            <div className="bubble-user mt-1.5 px-4 py-3 leading-relaxed text-solid-text">
              <div className="message-body">{renderContent(m.content)}</div>
            </div>
          )}
          {m.dbId && onDeleteTurn && !m.streaming && (
            <div className="mt-1.5 flex justify-end">
              <button
                type="button"
                onClick={() => onDeleteTurn(m.dbId!)}
                title="删除这轮对话"
                aria-label="删除这轮对话"
                className="msg-action-del"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                  <polyline points="3 6 5 6 21 6" />
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                </svg>
                <span>删除</span>
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  const refId = (idx: string) => `${m.id}-ref-${idx}`;
  const toolNames = m.toolCalls
    ? Array.from(new Set(m.toolCalls.map((t) => toolLabel(t.name))))
    : [];

  return (
    <div className="ai-msg flex gap-3">
      <HubMark className="h-7 w-7 flex-shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1 pt-0.5">
        {/* 工具调用条：进行中显形（转圈 + 已用工具），结束后收成可展开记录 */}
        {m.toolCalls && m.toolCalls.length > 0 && (
          m.streaming ? (
            <div className="tool-bar" role="status">
              <span className="tool-spin" aria-hidden="true" />
              <span>已调用 {toolNames.join("、")}</span>
            </div>
          ) : (
            <details className="tool-trace">
              <summary>
                调用记录：{toolNames.join("、")} · 共 {m.toolCalls.length} 次
              </summary>
              <div className="mt-2 flex flex-col gap-2">
                {m.toolCalls.map((t, i) => (
                  <div key={i} className="min-w-0">
                    <span className="tool-chip">
                      {TOOL_META[t.name]?.icon}
                      {toolLabel(t.name)}
                    </span>
                    {t.input && <div className="mt-1 truncate text-fs-xs opacity-80">入参：{t.input}</div>}
                    {t.output && <div className="mt-0.5 line-clamp-3 text-fs-xs opacity-80">结果：{t.output}</div>}
                  </div>
                ))}
              </div>
            </details>
          )
        )}

        <div className="bubble-ai whitespace-pre-wrap text-text">
          {m.error ? (
            <div className="warn px-3 py-2 text-sm">
              <div className="font-semibold">⚠ 暂时无法完成请求</div>
              <details className="mt-1">
                <summary className="cursor-pointer text-xs opacity-80">查看详情</summary>
                <div className="message-body text-xs">{renderContent(m.error)}</div>
              </details>
            </div>
          ) : !m.streaming && isPlanRequest(userQuestion) ? (
            (() => {
              const plan = parsePlan(m.content);
              if (!plan) return renderContent(m.content);
              const topic = detectPlanTopic(userQuestion);
              return <PlanCard data={plan} content={m.content} topic={topic} onCopy={handleCopy} />;
            })()
          ) : m.streaming && !m.content ? (
            <div className="thinking-loader">
              <div className="thinking-dots">
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
              </div>
              <span className="thinking-label">思考中...</span>
            </div>
          ) : (
            <>
              {renderContent(m.content)}
              {m.stopped && <span className="stopped-tag">已停止生成</span>}
              {m.streaming && <span className="cursor" />}
              {/* 引用角标：点一下跳到下面第 N 条参考片段 */}
              {!m.streaming && refItems.length > 0 && (
                <span className="ml-1 inline-flex flex-wrap items-center gap-1">
                  {refItems.map((r) => (
                    <a
                      key={r.idx}
                      href={`#${refId(r.idx)}`}
                      className="ref-note"
                      title={`跳到参考资料 ${r.idx}：${r.source}`}
                    >
                      {r.idx}
                    </a>
                  ))}
                </span>
              )}
            </>
          )}
        </div>

        {(m.refs && refItems.length > 0) || (m.sources?.length ?? 0) > 0 ? (
          <div className="ref-block">
            <div className="ref-head">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
              <span>参考资料 · {refItems.length || m.sources?.length} 条</span>
              <span className="ref-head-hint">
                {onPreviewFile ? "「查看原文」核对依据，「查看全文」读整篇" : "点「查看原文」核对模型到底依据了什么"}
              </span>
            </div>
            <div className="ref-list">
              {(refItems.length > 0 ? refItems : (m.sources ?? []).map((s) => ({
                idx: String(s.id),
                source: s.source + (s.page ? ` 第${s.page}页` : ""),
                score: s.score.toFixed(3),
                preview: s.preview,
              }))).map((r) => {
                const idxNum = Number(r.idx);
                const full = srcMap.get(idxNum)?.text;
                const open = openSrc === idxNum;
                // 取不带页码的纯文件名：老消息只有 refs 文本，里面的 source 带着「 第N页」
                const fileSource =
                  srcMap.get(idxNum)?.source ?? r.source.replace(/\s*第\d+页\s*$/, "");
                return (
                  <div key={r.idx} id={refId(r.idx)} className="ref-item">
                    <span className="ref-idx">{r.idx}</span>
                    <div className="ref-body">
                      <div className="ref-src">
                        {renderContent(r.source)}
                        {r.score && <span className="ref-meta">相似度 {r.score}</span>}
                      </div>
                      {r.preview && <div className="ref-preview">{renderContent(r.preview)}</div>}
                      {(full || (onPreviewFile && fileSource)) && (
                        <div className="ref-actions">
                          {full && (
                            <button
                              type="button"
                              className="ref-more"
                              aria-expanded={open}
                              aria-controls={`ref-full-${m.id}-${r.idx}`}
                              onClick={() => setOpenSrc(open ? null : idxNum)}
                            >
                              {open ? "收起原文" : "查看原文"}
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d={open ? "m18 15-6-6-6 6" : "m6 9 6 6 6-6"} />
                              </svg>
                            </button>
                          )}
                          {onPreviewFile && fileSource && (
                            <button
                              type="button"
                              className="ref-more"
                              title={`打开「${fileSource}」全文预览`}
                              onClick={() => onPreviewFile(fileSource)}
                            >
                              查看全文
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                                <polyline points="15 3 21 3 21 9" />
                                <line x1="10" y1="14" x2="21" y2="3" />
                              </svg>
                            </button>
                          )}
                        </div>
                      )}
                      {full && open && (
                        <div id={`ref-full-${m.id}-${r.idx}`} className="ref-full">
                          {full}
                          {full.length >= 1500 && <span className="ref-more-note">（片段较长，已截断显示）</span>}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* 兜底：refs 存在但格式不认识时按原文展示 */}
        {m.refs && refItems.length === 0 && (m.sources?.length ?? 0) === 0 && (
          <div className="ref-block">
            <div className="message-body text-xs leading-relaxed text-muted">{renderContent(m.refs)}</div>
          </div>
        )}

        {!m.streaming && !m.error && (
          <div className="msg-actions text-faint">
            <button
              onClick={handleCopy}
              title={copied ? "已复制" : "复制"}
              className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-xs transition hover:border-line-strong hover:bg-fill-2 hover:text-ink"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              <span>{copied ? "已复制" : "复制"}</span>
            </button>

            <button
              onClick={() => onForward?.(m.content)}
              title="转发到输入框"
              className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-xs transition hover:border-line-strong hover:bg-fill-2 hover:text-ink"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                <polyline points="16 6 12 2 8 6" />
                <line x1="12" y1="2" x2="12" y2="15" />
              </svg>
              <span>转发</span>
            </button>

            {onRetry && userQuestion && (
              <button
                onClick={() => onRetry(m.id, userQuestion ?? "")}
                title="重新生成"
                className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-xs transition hover:border-line-strong hover:bg-fill-2 hover:text-ink"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                  <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
                  <path d="M21 3v5h-5" />
                </svg>
                <span>重试</span>
              </button>
            )}

            {m.dbId && onDeleteTurn && (
              <button
                type="button"
                onClick={() => onDeleteTurn(m.dbId!)}
                title="删除这轮对话"
                aria-label="删除这轮对话"
                className="flex items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-xs text-faint transition hover:border-red/50 hover:bg-red/5 hover:text-red-ink"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                  <polyline points="3 6 5 6 21 6" />
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                </svg>
                <span>删除</span>
              </button>
            )}

            <div className="mx-1 h-3 w-px bg-line-strong" />

            <button
              onClick={() => {
                const next = feedback === "like" ? null : "like";
                setFeedback(next);
                if (next) onFeedback?.();
              }}
              title="赞"
              className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs transition hover:bg-fill-2 ${
                feedback === "like"
                  ? "border-accent bg-accent text-white"
                  : "border-line text-faint hover:border-line-strong hover:text-ink"
              }`}
            >
              <svg viewBox="0 0 24 24" fill={feedback === "like" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M7 10v12" />
                <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
              </svg>
            </button>

            <button
              onClick={() => {
                const next = feedback === "dislike" ? null : "dislike";
                setFeedback(next);
                if (next) onFeedback?.();
              }}
              title="踩"
              className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs transition hover:bg-fill-2 ${
                feedback === "dislike"
                  ? "border-solid bg-solid text-solid-text"
                  : "border-line text-faint hover:border-line-strong hover:text-ink"
              }`}
            >
              <svg viewBox="0 0 24 24" fill={feedback === "dislike" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                <path d="M17 14V2" />
                <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
              </svg>
            </button>

            <span className="ml-auto text-fs-xs text-faint">{m.content.length} 字</span>
          </div>
        )}
      </div>
    </div>
  );
});

export default Bubble;
