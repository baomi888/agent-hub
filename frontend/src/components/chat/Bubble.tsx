"use client";

// 消息气泡：用户气泡 + AI 回答（工具调用条 / 计划卡 / 引用溯源 / 操作条）
// memo 优化：流式回答时每个 token 都会更新 messages 数组，
// 已完成的旧消息对象引用不变，跳过重渲染，避免长对话越答越卡
import { memo, useMemo, useState } from "react";
import type { UiMessage } from "./types";
import { TOOL_META, toolLabel } from "./constants";
import { parseRefs, renderContent, renderMarkdown } from "./content";
import CornIcon from "../CornIcon";
import PlanCard, { detectPlanTopic, isPlanRequest, parsePlan } from "./plan";
import { HybridTraceView } from "@/components/HybridTrace";
import WebSources from "./WebSources";
import type { WebSource } from "@/lib/types";
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
  onSediment,
}: {
  m: UiMessage;
  userQuestion?: string;
  onCopy?: (text: string) => void;
  onForward?: (text: string) => void;
  onRetry?: (assistantId: string, question: string) => void;
  /** 剪贴板不可用等静默失败，交给上层出 toast */
  onCopyFail?: () => void;
  /** 赞/踩：按后端 messages 表的 id 落库，刷新后仍在（id 缺失时上层会静默跳过）。
   *  comment 是「踩」时可选的改进原因，留空则只记评分。 */
  onFeedback?: (msgDbId: number, rating: "up" | "down", comment?: string) => void;
  /** 打开该文件全文预览（未绑定资料库时不传，入口就不显示） */
  onPreviewFile?: (source: string) => void;
  /** 删除该消息及其所在的一轮对话 */
  onDeleteTurn?: (msgDbId: number) => void;
  /**
   * 「存进资料库」：把这条消息里讲到的事收进知识库。
   *
   * ★ 只挂在**用户自己**的消息上，这是产品口径决定的（`kb/dialogue_import.py`
   *   默认只收 user 的话）。助手的长回答是按句生成的段落，整段进库既不可溯源
   *   也会把 top-k 占满；而用户说「负责的老陈工号 KX-3301」这种，才是资料。
   *   所以不做「存这条回答」，免得按钮承诺了后端给不了的东西。
   */
  /**
   * 打开「存进资料库」。
   *
   * 带 role 是因为两种消息的存法根本不同：用户的短句是「就这一句，
   * 原样存」；助手的回答是「一条里有好几节，每节单独成段」。
   * 弹层靠它决定显示单句还是分节列表。
   */
  onSediment?: (msgDbId: number, text: string, role: "user" | "assistant") => void;
}) {
  const [feedback, setFeedback] = useState<"like" | "dislike" | null>(null);
  // 「踩」后展开的改进原因输入框：点踩即记一票，这里只收集可选的文字补充
  const [comment, setComment] = useState("");
  const [showComment, setShowComment] = useState(false);
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

  // 踩后提交改进原因：投票已在点击时记录，这里只把 comment 覆盖写回同一条反馈
  const submitComment = () => {
    const text = comment.trim();
    if (m.dbId && text) onFeedback?.(m.dbId, "down", text);
    setShowComment(false);
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
          {/* 用户消息下的轻量动作。原来只有「删除」，整条消息下面空得很，
              而「把自己刚说清楚的事记下来」这件事没有任何入口 —— 它长在这里
              才符合时机：用户刚打完「工号 KX-3301 归老陈」，眼前就该有得点。
              少于 6 个字的多半是「继续」「好的」，挂上去只是噪音。 */}
          {!m.streaming && ((m.dbId && onDeleteTurn) || (m.dbId && onSediment)) && (
            <div className="mt-1 flex items-center justify-end gap-0.5">
              {m.dbId && onSediment && m.content.trim().length >= 6 && (
                <button
                  type="button"
                  onClick={() => onSediment(m.dbId!, m.content, "user")}
                  title="把这句话收进资料库，以后提问能查到"
                  className="msg-action-save"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
                  </svg>
                  <span>存进资料库</span>
                </button>
              )}
              {m.dbId && onDeleteTurn && (
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
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  const refId = (idx: string) => `${m.id}-ref-${idx}`;
  // 联网来源按 id 建索引：正文里的 [Wn] 要能直接找到对应网址。
  // 不用 useMemo —— 这里在用户消息的 early return 之后，放 hook 会违反调用规则；
  // 来源最多十来条，每次建 Map 的成本远小于一次依赖比较。
  const cites = new Map<number, WebSource>();
  const knownUrls = new Set<string>();
  for (const s of m.webSources ?? []) {
    cites.set(s.id, s);
    knownUrls.add(s.url);
  }
  // 资料库片段编号 → 锚点。命中才把正文里的 [n] 变成跳转角标：
  // 联网用 [Wn]、资料库用 [n]，两套编号在渲染层彻底分开（见 content.tsx 的注释）。
  // ★ 两个来源都要收：refItems 来自落库的 refs 文本（历史消息 / 刷新后重进），
  //   m.sources 来自本轮的 SSE sources 帧。只认前者的话，本轮刚问完、refs 还没
  //   回填的那一刻，正文里 [n] 会退回成裸方括号 —— 探针里就是这么翻车的。
  // 这个 Set 也顺带挡住了「正文写了 [7] 但本轮只有 3 条」这种越界编号。
  const refIdxs = new Set<number>();
  for (const r of refItems) refIdxs.add(Number(r.idx));
  for (const s of m.sources ?? []) refIdxs.add(s.id);
  const refAnchor = (n: number) => (refIdxs.has(n) ? refId(`${n}`) : null);
  // 正文已经内联了资料库角标时，末尾不再重复排一排同样的数字。
  // 老消息（改造前存的，正文里没有 [n]）仍然走末尾那排 —— 那是它唯一的入口。
  let inlineKbCited = false;
  if (refIdxs.size > 0) {
    // [n] 后面紧跟 ( 的是 markdown 链接 [文案](url)，不算引用，排除掉
    for (const hit of m.content.matchAll(/\[(\d{1,2})\](?!\()/g)) {
      if (refIdxs.has(Number(hit[1]))) {
        inlineKbCited = true;
        break;
      }
    }
  }
  const toolNames = m.toolCalls
    ? Array.from(new Set(m.toolCalls.map((t) => toolLabel(t.name))))
    : [];

  return (
    <div className="ai-msg flex gap-3">
      {/* 44px 才能看清神态：眨眼 / 腮红 / 思考时嘴变○，28px 时这些彩蛋全是噪点 */}
      <HubMark className="h-11 w-11 flex-shrink-0 mt-2" thinking={m.streaming} />
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

        <div
          className="bubble-ai whitespace-pre-wrap text-text"
          data-streaming={m.streaming && !!m.content ? "true" : undefined}
        >
          {/* 混合架构执行轨迹（仅 mode=hybrid 的消息有）。
              放在正文之前 —— 与下面的依据提示条同一个道理：用户边流边读，
              「这一步是谁在执行」必须先于答案出现，否则读完整段答案才发现
              中途换了执行者，就来不及判断这段结果可不可信了。
              fallback = auto 判成「工作流起跑」但后端引擎开关关闭，静默退回智能体，
              必须明说，否则用户以为看到的就是混合轨迹。 */}
          {(m.hybridTrace?.length || m.hybridMode || m.hybridRunId) && (
            <HybridTraceView
              items={m.hybridTrace ?? []}
              mode={m.hybridMode}
              running={m.streaming}
              fallback={m.hybridMode === "agent" && !m.hybridRunId}
            />
          )}

          {/* 依据不足提示条：没有一条片段达到"可以直接当依据"的水平。
              放在正文之前——用户是边流边读的，提示必须比答案先出现，
              否则等读完一段"看起来很自信"的回答才看到提示就晚了。
              两档话术不同：low=完全没找到，medium=只找到沾边内容。 */}
          {m.grounding?.weak && !m.error && (
            <div className="grounding-alert" role="status">
              <svg className="ga-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 9v4" />
                <path d="M12 17h.01" />
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              </svg>
              <div className="ga-body">
                <span className="ga-title">
                  {m.grounding.strength === "medium"
                    ? "只找到内容沾边的资料"
                    : "知识库里没有找到直接依据"}
                </span>
                {m.grounding.strength === "low"
                  ? `已检索到 ${m.grounding.count} 条片段，但相似度都低于 0.45${
                      m.grounding.best !== null ? `（最高 ${m.grounding.best.toFixed(2)}）` : ""
                    }，下面的回答不构成资料依据。`
                  : `检索到 ${m.grounding.count} 条片段${
                      m.grounding.best !== null ? `（最高相似度 ${m.grounding.best.toFixed(2)}）` : ""
                    }，只是沾边、不够构成直接依据，请以你自己的判断为准。`}
                <span className="ga-hint">
                  补一份对口文档进这个知识库，或直接改用联网搜索，会更准。
                </span>
              </div>
            </div>
          )}
          {m.error ? (
            <div className="warn px-3 py-2 text-sm">
              <div className="flex items-center gap-1.5 font-semibold">
                <CornIcon name="warn" className="h-4 w-4 shrink-0" />
                暂时无法完成请求
              </div>
              <details className="mt-1">
                <summary className="cursor-pointer text-xs opacity-80">查看详情</summary>
                <div className="message-body text-xs">{renderContent(m.error)}</div>
              </details>
            </div>
          ) : !m.streaming && isPlanRequest(userQuestion) ? (
            (() => {
              const plan = parsePlan(m.content);
              if (!plan) return renderMarkdown(m.content, false, cites, knownUrls, refAnchor);
              // 优先用后端回传的主题（能从历史推断）；没有才退回本句关键词匹配。
              // PlanCard 只用到 topic.name（抬头文案），hint 在这里给空串即可。
              const topic = m.planTopic
                ? { name: m.planTopic, hint: "" }
                : detectPlanTopic(userQuestion);
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
              {/* 进度说明优先：带资料库提问时首帧要等十几秒，
                  "思考中..." 说不了"我在翻你的资料库"。文案在 lib/chatStatus 里翻译过 */}
              <span className="thinking-label">
                {m.status || "思考中..."}
              </span>
            </div>
          ) : (
            <>
              {/* AI 正文走 markdown 渲染：标题/列表/表格/代码块/链接都排出来 */}
              {renderMarkdown(m.content, !!m.streaming, cites, knownUrls, refAnchor)}
              {m.stopped && <span className="stopped-tag">已停止生成</span>}
              {/* 流式光标不在这里渲染：它不是一段文字，是"这一句还没写完"的记号。
                  span 跟在 markdown 块后面会另起一行、单独挂个 ▍，很怪；
                  改由 .bubble-ai[data-streaming] 的 :last-child::after 挂在最后一个
                  句子的末尾（见 kb.css）。 */}
              {/* 引用角标：点一下跳到下面第 N 条参考片段。
                  正文里已经内联过带编号的（新消息多半如此）就不再排这一排 ——
                  同一批编号在一屏里出现两遍，读者会以为有两套参考资料。 */}
              {!m.streaming && refItems.length > 0 && !inlineKbCited && (
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

        {/* 联网来源：与下面的资料库引用块并列，但不合并 ——
            那边每条带页码和相似度（本地文件），这边每条是网址（网页），
            混在一个列表里必然出现「文件名 + 外链」这种自相矛盾的条目 */}
        {!m.streaming && (m.webSources?.length ?? 0) > 0 && (
          <WebSources
            items={m.webSources ?? []}
            /* 同一屏里还有资料库来源时才加抬头：只有联网时多了这行是废话 */
            label={refIdxs.size > 0 ? "网页来源" : undefined}
          />
        )}

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
                score: typeof s.score === "number" ? s.score.toFixed(3) : "",
                preview: s.preview,
              }))).map((r) => {
                const idxNum = Number(r.idx);
                const full = srcMap.get(idxNum)?.text;
                const open = openSrc === idxNum;
                // 取不带页码的纯文件名：老消息只有 refs 文本，里面的 source 带着「 第N页」
                const fileSource =
                  srcMap.get(idxNum)?.source ?? r.source.replace(/\s*第\d+页\s*$/, "");
                // 依据强度：后端判定为准。老消息（改造前存的）没有这个字段，
                // 退化成按相似度数值本地分级，避免历史记录里一片空白。
                // 0.45 这个线要和后端 RETRIEVE_LOW_SIM 保持一致。
                const strength =
                  srcMap.get(idxNum)?.strength ??
                  (r.score === ""
                    ? null
                    : Number(r.score) >= 0.45
                      ? "high"
                      : Number(r.score) > 0.25
                        ? "medium"
                        : "low");
                const strengthText =
                  strength === "high" ? "强" : strength === "medium" ? "中" : strength ? "弱" : "";
                const strengthTip =
                  strength === "high"
                    ? "这条相似度较高，可作为回答依据"
                    : strength === "medium"
                      ? "这条只是沾边，不足以独立支撑回答"
                      : "这条相似度过低，不足以作为回答依据";
                return (
                  <div key={r.idx} id={refId(r.idx)} className="ref-item">
                    <span className="ref-idx">{r.idx}</span>
                    <div className="ref-body">
                      <div className="ref-src">
                        {renderContent(r.source)}
                        {r.score && <span className="ref-meta">相似度 {r.score}</span>}
                        {strengthText && (
                          <span
                            className={`ref-strength${strength === "high" ? "" : " is-weak"}`}
                            title={strengthTip}
                          >
                            {strengthText}
                          </span>
                        )}
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
          <>
            {/* ★ 常显，不放进 hover 工具条（2026-11-01）。
                `.msg-actions` 默认 opacity:0，桌面端不悬停就什么都看不见——
                把「存这条回答」藏进去，等于这个功能不存在。用户原话是
                「得让用户能知晓」，不是「技术上有个按钮」。
                复制/转发这类工具动作仍留在 hover 条里，它们不需要被发现。 */}
            {m.dbId && onSediment && m.content.trim().length >= 80 && (
              <div className="mt-2 flex">
                <button
                  type="button"
                  onClick={() => onSediment(m.dbId!, m.content, "assistant")}
                  title="把这条回答收进资料库，以后提问能查到"
                  className="msg-action-save"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
                  </svg>
                  <span>存进资料库</span>
                </button>
              </div>
            )}

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
                if (next && m.dbId) onFeedback?.(m.dbId, "up");
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
                if (next && m.dbId) {
                  // 先记一票：原因框只是可选的补充，不能因为没填就不计数
                  onFeedback?.(m.dbId, "down");
                  setShowComment(true);
                } else {
                  setShowComment(false);
                }
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

          {/* 踩后的改进原因：投票已记录，这里只收可选文字。提交会按同一条消息覆盖写入，
              看板「最近反馈」就能直接显示用户踩的理由。 */}
          {showComment && feedback === "dislike" && m.dbId && (
            <div className="fb-comment">
              <label className="fb-comment-label" htmlFor={`fb-c-${m.dbId}`}>
                这条回答哪里不太对？（可选，帮你我复盘改进）
              </label>
              <textarea
                id={`fb-c-${m.dbId}`}
                className="fb-comment-input"
                value={comment}
                autoFocus
                rows={2}
                maxLength={300}
                placeholder="例如：引用来源不清晰 / 步骤有跳步 / 和我的资料矛盾 / 语气太绝对…"
                onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitComment();
                }}
              />
              <div className="fb-comment-actions">
                <button
                  type="button"
                  className="fb-comment-skip"
                  onClick={() => setShowComment(false)}
                >
                  跳过
                </button>
                <button
                  type="button"
                  className="btn-new fb-comment-submit"
                  onClick={submitComment}
                >
                  提交反馈
                </button>
              </div>
            </div>
          )}
          </>
        )}
      </div>
    </div>
  );
});

export default Bubble;
