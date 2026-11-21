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

/** 行内公式的候选：$...$（不跨行）。真正的判定交给 looksLikeMath。
 *
 * 三条边界，每一条都对应线上踩过的真实错例：
 *   - 开头的 `$` 后面不能是反斜杠：否则 `$$...$$` 的左两个 $ 会被当成行内起始，
 *     捕获组里带进一个 `$`，块级公式就永远切不出来。
 *   - 开头的 `$` 后面**可以是空白**：模型极爱写 `$ f(x) $`（美元符内侧带空格，
 *     LaTeX 排版习惯）。旧写法开头的字符类里排除了空白，把这类全挡掉了，
 *     结果是整页 `$ f'(x) < 0 $` 原样显示成乱码 —— 这就是"数学符号显示不出来"的根因。
 *   - 收尾的 `$` 后面不能是数字、也不能是另一个 `$`：前者防「$5 到 $10」粘连，
 *     后者防吃掉块级公式的右两个 $。
 */
const INLINE_MATH_RE = /\$(?![\\$])([^$\n]{1,300}?)\$(?![\d$])/g;
/** 块级公式：$$...$$ 或 \[...\]，允许跨行 */
const BLOCK_MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g;
/** \(...\) 也是行内公式，Word 复制粘贴和部分模型偏爱这种写法 */
const PAREN_MATH_RE = /\\\(([\s\S]+?)\\\)/g;

/**
 * `$...$` 里的内容到底是不是公式。
 *
 * 聊天里 `$` 也会出现在价格、Shell 变量里（"$PATH"、"$5 一杯"）。
 * 判据：至少含一个数学特征字符 —— 宁可少渲染，也不要把「$5 到 $10」变成一块公式。
 *
 * 两条**不要**再犯的守卫（2026-11-21 用真实 DeepSeek 回答跑出来的）：
 *   1. 不要因"首尾带空格"否决：模型写 `$ f(x) $` 是 LaTeX 习惯，旧版这条把
 *      整批公式判成非公式，原样漏成 `$ f'(x) < 0 $`。
 *   2. 不要因"纯数字"一刀切否决：讲题时表格和结论里全是 `$0$`、`$1$`、`$2$`
 *      （驻点值、最值），一刀切会让整张表格的单元格全变成美元符原文。
 *
 * 但"纯数字全放行"会误伤 Shell 变量对：`$HOME 和 $PATH` 之间会被当成一段公式。
 * 所以数字要分开判：
 *   - 纯 ASCII 数字（`0`、`1`、`-1`、`3,000`）→ 是公式（表格里的数值）；
 *   - 含字母的（`HOME 和 `、`PATH`）→ 不是公式，那多半是 `$VAR` 变量。
 */
export function looksLikeMath(s: string): boolean {
  const t = s.trim();
  if (!t) return false;
  // 纯数字（可带负号/千分位/小数点）单独放行，供讲题表格使用
  if (/^-?[\d,]+(\.\d+)?$/.test(t)) return true;
  // 单个字母/单个希腊字母单独放行：`$x$`、`$f$` 是讲题里最常见的行内变量
  if (/^[a-zA-Z]$|^[α-ωΑ-Ω]$/.test(t)) return true;
  // 其余必须带数学特征字符，否则一律按普通文字处理（挡住 $HOME 和 $PATH）
  return /[\\^_{}=<>+\-*/()[\]]|[α-ωΑ-Ω∑∫√≤≥≠≈∞∂∇∈∉⊂⊆∪∩]/.test(t);
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
