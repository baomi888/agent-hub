"use client";

/**
 * 检索词增强提示条（roadmap B5）—— 把 B3 的「检索时会怎么理解你」摆到输入框上面。
 *
 * 为什么要有这个东西
 * ------------------
 * 后端检索前会在用户原话后面**追加**规范术语（`高数怎么复习` → `高数怎么复习 高等数学`），
 * 让口语词和术语都能命中。这件事如果用户看不见，就是**偷偷动了他的问题**；
 * 看见了并且能撤掉，才是"帮他检索得更准"。技术方案里那三条原则（可见 / 可撤销 / 不改展示）
 * 落到界面上就是这一个组件：
 *
 *   · 可见   —— 输入停顿后自动出现，明说会额外按几个术语检索
 *   · 可撤销 —— 「这次不用」，这一句立刻恢复只按原话检索
 *   · 不改展示 —— 增强只在检索侧追加；用户发出去的话、聊天记录里显示的，一个字都不变
 *
 * ★ 2026-10-24 从「替换」改成「追加」后，这个组件的 UI 也跟着变了：
 *   以前展示「原话 / 检索」两行对照（检索那行是改过的句子），
 *   现在展示「原话（高亮被识别的词）/ 补充（术语 chips）」——因为原话根本没被改，
 *   再摆一份"检索后的句子"只会把原话重复念一遍，又长又让人以为被改写了。
 *
 * 为什么是浮层而不是占一行的字段
 * ------------------------------
 * 它在输入框上方，如果参与布局，每次出现/消失都会把输入框顶下去 30px——打字时最烦这个。
 * 所以锚点高度为 0，面板绝对定位向上弹出（见 enhance.css 的 .qe-anchor）。
 *
 * 为什么请求失败什么都不显示
 * --------------------------
 * 这是"每打一句话就可能跑一次"的辅助能力，不是用户主动发起的操作。
 * 限流（429）或超时如果弹红字，用户会以为聊天坏了，而实际增强只是增益。
 * 详见 lib/api.ts::previewEnhance —— 它在那一层就把异常吞干净了。
 */
import { useEffect, useRef, useState } from "react";
import { previewEnhance } from "@/lib/api";
import type { EnhanceHit, EnhanceResult } from "@/lib/types";

/** 打字停顿多久才去问一次。太短会边打边发请求，太长又跟不上手速。 */
const DEBOUNCE_MS = 700;
/** 少于两个字没有任何可校准的内容，不必打扰。 */
const MIN_CHARS = 2;

/** 把原话按命中区间切成「普通片段 / 被替换片段」。
 *
 * 防御性跳过越界区间：前端和词表可能不是同一版（词表热加载、后端已更新），
 * 脏的下标不能让整个输入区白屏。`start` 用 `h.start < at` 挡住反向/重叠区间。
 */
function segments(original: string, hits: EnhanceHit[]) {
  const out: { text: string; hit?: EnhanceHit }[] = [];
  let at = 0;
  for (const h of hits) {
    if (h.start < at || h.end > original.length || h.end <= h.start) continue;
    if (h.start > at) out.push({ text: original.slice(at, h.start) });
    out.push({ text: original.slice(h.start, h.end), hit: h });
    at = h.end;
  }
  if (at < original.length) out.push({ text: original.slice(at) });
  return out;
}

export default function QueryEnhance({ text }: { text: string }) {
  /**
   * 结果连同"它是哪句话的结果"一起存。
   *
   * 不在 effect 里用 `setResult(null)` 去清理过期结果 —— React 19 的
   * `react-hooks/set-state-in-effect` 会直接报错（effect 里同步 setState 会引发级联渲染）。
   * 改成"结果自带归属"，文本一变它就自动不匹配、自动不渲染，一个 setState 都不用发。
   */
  const [state, setState] = useState<{ text: string; data: EnhanceResult } | null>(null);
  const [open, setOpen] = useState(false);
  /** 点过「这次不用」的那句话。改了字就不再相等，提示会重新出现。 */
  const [dismissed, setDismissed] = useState("");
  /** 请求序号：打字快了会有多个请求在飞，只认最后发出的那个 */
  const seqRef = useRef(0);

  const q = text.trim();

  useEffect(() => {
    if (q.length < MIN_CHARS || q === dismissed) return;
    const seq = ++seqRef.current;
    const timer = setTimeout(() => {
      previewEnhance(q).then((r) => {
        if (seqRef.current !== seq) return; // 过期响应：用户已经改成别的句子了
        setState(r && r.changed ? { text: q, data: r } : null);
        // 换了句子就收起：上一句展开着，下一句应该先以"提示条"形态出现
        setOpen(false);
      });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [q, dismissed]);

  // 三件事对不上就整个不渲染（而不是显示上一句话的结果）：
  // 有结果、结果属于当前这句话、用户没点过「这次不用」。
  // 宁可晚 700ms 出现，也不要给错的信息。
  if (!state || state.text !== q || q === dismissed) return null;

  const result = state.data;
  // 去重后的术语数，与下面 chips 一一对应。用 hits.length 会出现"3 处"却只列 2 个词的困惑。
  const n = result.added.length;

  if (!open) {
    return (
      <div className="qe" role="status" aria-live="polite">
        <button type="button" className="qe-bar" onClick={() => setOpen(true)}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
          </svg>
          <span>
            会额外按 <b>{n}</b> 个规范术语检索
          </span>
          <span className="qe-bar-more">看看</span>
        </button>
      </div>
    );
  }

  return (
    <div className="qe" role="region" aria-label="检索词增强预览">
      <div className="qe-card">
        <div className="qe-head">
          <span className="qe-title">检索时会同时按规范术语找资料</span>
          {/* 正文只报词条数：文件名对用户没意义，但排障时又要看，所以塞进 title */}
          <span className="qe-src" title={`当前词表：${result.vocab.file}`}>
            {result.vocab.terms} 条词
          </span>
          <button type="button" className="qe-x" onClick={() => setOpen(false)} aria-label="收起">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <dl className="qe-rows">
          <div>
            <dt>原话</dt>
            <dd>
              {segments(result.original, result.hits).map((s, i) =>
                s.hit ? (
                  <mark
                    key={i}
                    className="qe-mark"
                    title={`这个词同时按「${s.hit.canonical}」检索`}
                  >
                    {s.text}
                  </mark>
                ) : (
                  <span key={i}>{s.text}</span>
                )
              )}
            </dd>
          </div>
          <div>
            <dt>补充</dt>
            <dd className="qe-terms">
              {result.added.map((t, i) => (
                <span key={i} className="qe-term">
                  {t}
                </span>
              ))}
            </dd>
          </div>
        </dl>

        {result.clarify.length > 0 && (
          <div className="qe-clarify">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 16v-4M12 8h.01" />
            </svg>
            <div>
              <p className="qe-clarify-q">这句话里有词可能有多个意思：</p>
              {result.clarify.map((q, i) => (
                <p key={i}>{q}</p>
              ))}
              <p className="qe-clarify-hint">说得再具体一点，检索会更准。</p>
            </div>
          </div>
        )}

        <div className="qe-foot">
          <span className="qe-note">只改检索，你发出去的话和聊天记录都不变</span>
          <button
            type="button"
            className="qe-dismiss"
            onClick={() => {
              // 用当前输入 q 而不是 result.original：两者理论相同，但 q 是这一句的键，
              // 拿别的值当键会让「这次不用」偶发失效。
              setDismissed(q);
              setOpen(false);
            }}
          >
            这次不用
          </button>
        </div>
      </div>
    </div>
  );
}
