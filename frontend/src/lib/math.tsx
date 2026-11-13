/**
 * 数学公式渲染（KaTeX）
 *
 * 为什么要单独一层：模型回答里的公式有三种写法，原样显示全是"乱码"——
 *   1. LaTeX 行内 `$x^2$`、`\(x^2\)`     → 用户看到一堆反斜杠和美元符
 *   2. LaTeX 独占 `$$...$$`、`\[...\]`   → 整段糊成一行
 *   3. 纯 Unicode（x²、α₁、∫）           → 字体缺字时变方框
 * 前两种交给 KaTeX 变成真正的排版公式；第三种本来就正常，只需要字体栈兜底。
 *
 * 三个实现要点：
 *   - 流式输出时 `$` 可能只来了一半，这里对未闭合的公式**不渲染**，原样保留，
 *     等闭合再变 —— 否则每个 token 都会闪一次残式子。
 *   - KaTeX 结果按 (tex + 是否块级) 做缓存。流式渲染会反复重算同一条消息，
 *     一条回答里十几个公式不缓存的话，每来一个字符都要重新排一遍版。
 *   - 只用 renderToString 的返回值塞 innerHTML。KaTeX 默认 trust: false，
 *     且输出由它自己转义，不接受任意 HTML —— 这条是防注入的底线，别改成
 *     trust: true 去用 \htmlClass 之类的扩展。
 */
import katex from "katex";
import type { ReactNode } from "react";
import { createElement } from "react";

export type MathSeg = { type: "text" | "inline" | "block"; value: string };

/** 行内公式的候选：$...$（不跨行）。真正的判定交给 looksLikeMath */
const INLINE_MATH_RE = /\$(?![\s$])([^$\n]{1,300}?)\$(?!\d)/g;
/** 块级公式：$$...$$ 或 \[...\]，允许跨行 */
const BLOCK_MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g;
/** \(...\) 也是行内公式，Word 复制粘贴和部分模型偏爱这种写法 */
const PAREN_MATH_RE = /\\\(([\s\S]+?)\\\)/g;

/**
 * `$...$` 里的内容到底是不是公式。
 *
 * 聊天里 `$` 也会出现在价格、Shell 变量里（"$PATH"、"$5 一杯"）。
 * 判据：至少含一个数学特征字符，且不能是纯数字 —— 宁可少渲染，
 * 也不要把「$5 到 $10」变成一块公式。
 */
export function looksLikeMath(s: string): boolean {
  if (!s.trim()) return false;
  if (/^\s|\s$/.test(s)) return false;
  // 纯数字/千分位（$5、$1,200）不是公式
  if (/^[\d,.\s]+$/.test(s)) return false;
  return /[\\^_{}=<>+\-*/()[\]]|[α-ωΑ-Ω∑∫√≤≥≠≈∞∂∇∈∉⊂⊆∪∩]|[a-zA-Z]/.test(s);
}

/**
 * 把一段文本切成「普通文字 / 行内公式 / 块级公式」。
 *
 * 先切块级（$$、\[），再在普通文字里切行内（$、\(）。顺序不能反：
 * 先切行内的话，`$$x$$` 会被当成两个空行内公式，块级语义就丢了。
 */
export function splitMath(text: string): MathSeg[] {
  const out: MathSeg[] = [];
  const pushText = (s: string) => {
    if (!s) return;
    // 普通文字里再切 \(...\) 与 $...$
    let last = 0;
    const re = new RegExp(`${PAREN_MATH_RE.source}|${INLINE_MATH_RE.source}`, "g");
    for (const m of s.matchAll(re)) {
      const idx = m.index ?? 0;
      const tok = m[0];
      const inner = m[1] ?? m[2] ?? "";
      const isParen = tok.startsWith("\\(");
      const ok = isParen || looksLikeMath(inner);
      if (!ok) continue; // 不是公式：留给后面的纯文本，原样显示
      if (idx > last) out.push({ type: "text", value: s.slice(last, idx) });
      out.push({ type: "inline", value: inner });
      last = idx + tok.length;
    }
    if (last < s.length) out.push({ type: "text", value: s.slice(last) });
  };

  let last = 0;
  for (const m of text.matchAll(BLOCK_MATH_RE)) {
    const idx = m.index ?? 0;
    const tok = m[0];
    const inner = (m[1] ?? m[2] ?? "").trim();
    if (!inner) continue;
    // 单字母/纯数字的 `$$` 多半是模型笔误，仍按块级渲染无妨，但太短就原样留着
    if (idx > last) pushText(text.slice(last, idx));
    out.push({ type: "block", value: inner });
    last = idx + tok.length;
  }
  if (last < text.length) pushText(text.slice(last));
  return out;
}

/** 公式渲染缓存：同一段 tex 在流式重渲染里会命中几十次 */
const CACHE = new Map<string, string>();
const CACHE_MAX = 600;

/**
 * tex → HTML。失败时返回 null，调用方回落成等宽原文（比抛错好：
 * 一条公式写错不该让整条回答白屏）。
 */
export function texToHtml(tex: string, display: boolean): string | null {
  const key = (display ? "D:" : "I:") + tex;
  const hit = CACHE.get(key);
  if (hit !== undefined) return hit || null;
  let html = "";
  try {
    html = katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      strict: "ignore",
      trust: false,
      // 同时输出 MathML：屏幕阅读器与「复制成文本」都靠它
      output: "htmlAndMathml",
    });
  } catch {
    html = "";
  }
  if (CACHE.size >= CACHE_MAX) CACHE.clear();
  CACHE.set(key, html);
  return html || null;
}

/** 单个公式节点；渲染失败就退回原样的 tex（不隐藏，用户至少知道写了什么） */
export function MathTex({ tex, display }: { tex: string; display?: boolean }): ReactNode {
  const html = texToHtml(tex, !!display);
  // 一律用 <span>：块级公式经常嵌在段落里，<div> 套在 <p> 里会被浏览器
  // 拆开（React 也会报 hydration 不匹配）。块级效果交给 CSS 的 display:block。
  if (!html) {
    return createElement(
      "code",
      { className: display ? "math-block math-raw" : "math-inline math-raw" },
      tex
    );
  }
  return createElement("span", {
    className: display ? "math-block" : "math-inline",
    // KaTeX 的输出由它自己转义，且 trust 已关闭，不接受任意 HTML
    dangerouslySetInnerHTML: { __html: html },
  });
}
