// 消息正文渲染：URL 自动 linkify + **粗体** + 换行；引用 refs 文本解析
// 从 ChatArea.tsx 拆出（2026-09 重构），全部是纯函数，适合单测
import type { ReactNode } from "react";
import LinkCluster, { type LinkItem } from "./LinkCluster";

// 排除空白与常见分隔符；中文标点也要排除，否则「https://xxx。」会把句号吞进链接
const URL_RE = /https?:\/\/[^\s<>"{}|\\^`\[\]。，、；：！？（）【】《》「」『』]+/gi;

/** 展示用 URL：把百分号编码解码回中文/原文，href 仍用原始值，跳转不受影响 */
function displayUrl(url: string): string {
  try {
    return decodeURI(url);
  } catch {
    return url; // 含非法 % 序列时保持原文
  }
}

/**
 * 仅放行安全协议的外链。
 *
 * 模型（或它抓来的网页内容）可能输出 `[点我](javascript:alert(1))` 这类伪链接；
 * 裸 URL 的正则已限定 http(s)，但 **markdown 链接的目标 `[^)\s]+` 什么都收**，
 * 直接写进 href 就是一个 XSS 面。这里做协议白名单，白名单外一律降级为纯文本。
 */
const SAFE_HREF_RE = /^(https?:\/\/|mailto:|tel:)/i;
export function safeHref(url: string): string | null {
  const u = url.trim();
  return SAFE_HREF_RE.test(u) ? u : null;
}

export function splitByUrl(text: string): Array<{ type: "text" | "url"; value: string }> {
  const out: Array<{ type: "text" | "url"; value: string }> = [];
  let rest = text;
  while (rest) {
    const m = URL_RE.exec(rest);
    if (!m) {
      out.push({ type: "text", value: rest });
      break;
    }
    if (m.index > 0) out.push({ type: "text", value: rest.slice(0, m.index) });
    out.push({ type: "url", value: m[0] });
    rest = rest.slice(m.index + m[0].length);
    URL_RE.lastIndex = 0;
  }
  return out;
}

export function renderContent(text: string): ReactNode {
  return text.split("**").map((part, i) => {
    const isBold = i % 2 === 1;
    const children = splitByUrl(part).map((seg, j) => {
      if (seg.type === "url") {
        return (
          <a
            key={j}
            href={seg.value}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-link"
            title={displayUrl(seg.value)}
          >
            {displayUrl(seg.value)}
          </a>
        );
      }
      const lines = seg.value.split("\n").map((line, k, arr) => (
        <span key={k}>
          {line}
          {k < arr.length - 1 && <br />}
        </span>
      ));
      return <span key={j}>{lines}</span>;
    });
    return isBold ? <strong key={i}>{children}</strong> : <span key={i}>{children}</span>;
  });
}

// ============ Markdown 渲染（AI 回答正文专用） ============
// 模型输出大量使用标题/列表/表格/代码块，旧 renderContent 只认 **粗体**+URL，
// 排版全丢。这里做一个轻量块级解析器，输出 React 节点（不碰 innerHTML，天然防注入）。
// 设计取舍：
// - 单个换行按 <br/> 处理（聊天场景惯例，模型经常单换行分点）
// - 无序列表收 [-*•]，有序列表收 \d+[.、)]（中文场景「1、」很常见）
// - 流式渲染时未闭合的 ** 或 ` 走正则匹配不到 → 原样显示，不会崩
// - 小输入（引用预览/用户消息）仍走旧 renderContent，不强行 markdown 化

const MD_INLINE_RE =
  /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]+\]\([^)\s]+\))|(~~[^~\n]+~~)|(https?:\/\/[^\s<>"{}|\\^`\[\]。，、；：！？（）【】《》「」『』]+)/gi;

/** 行内元素：行内代码 / 粗体 / 链接 / 删除线 / 裸 URL */
export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(MD_INLINE_RE)) {
    if (m.index! > last) out.push(<span key={`${keyPrefix}-t${k++}`}>{text.slice(last, m.index)}</span>);
    const tok = m[0];
    const key = `${keyPrefix}-m${k++}`;
    if (tok.startsWith("`")) {
      out.push(<code key={key} className="md-code">{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith("**")) {
      out.push(<strong key={key}>{renderInline(tok.slice(2, -2), key)}</strong>);
    } else if (tok.startsWith("~~")) {
      out.push(<del key={key}>{renderInline(tok.slice(2, -2), key)}</del>);
    } else if (tok.startsWith("[")) {
      // [文案](https://…)：目标必须过协议白名单，否则只留文案、丢掉链接
      const mm = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(tok);
      const href = mm ? safeHref(mm[2]) : null;
      if (mm && href) {
        out.push(
          <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="inline-link" title={displayUrl(href)}>
            {renderInline(mm[1], key)}
          </a>
        );
      } else if (mm) {
        out.push(<span key={key}>{renderInline(mm[1], key)}</span>);
      } else {
        out.push(<span key={key}>{tok}</span>);
      }
    } else {
      // 裸 URL：正则已限定 http(s)，仍再走一次白名单兜底
      const href = safeHref(tok);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="inline-link" title={displayUrl(href)}>
            {displayUrl(href)}
          </a>
        ) : (
          <span key={key}>{tok}</span>
        )
      );
    }
    last = m.index! + tok.length;
  }
  if (last < text.length) out.push(<span key={`${keyPrefix}-t${k++}`}>{text.slice(last)}</span>);
  return out;
}

/** 把一段文本拆成行内节点的数组；供表格单元格等复用 */
const inlineOf = (s: string, kp: string) => renderInline(s, kp);

interface MdTable {
  head: string[];
  rows: string[][];
}

/** 解析一行表格单元格：| a | b | → [a, b]（容忍首尾无竖线） */
function splitTableRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

const isTableSep = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line);

// ============ 末尾链接收口 ============
// 联网搜完之后，模型常把整排检索结果贴在回答最后（有时夹在要点之间）。
// 一整屏 https://… 会把结论挤没，用户也分不出哪条值得点。
// 做法：只把**末尾**那一串摘出来折成「N 个链接」胶囊，内容一条不丢。

const URL_ONLY_RE = /https?:\/\/[^\s<>"{}|\\^`\[\]。，、；：！？（）【】《》「」『』]+/g;

/** 一行是不是「几乎只有一条链接」：去掉链接与列表符号后，剩下的字很少 */
function asLinkLine(line: string): LinkItem | null {
  const t = line.trim();
  if (!t) return null;
  const hits = t.match(URL_ONLY_RE);
  // 一行里有两条以上链接时判断不了该留哪条做标题，交给行内渲染，别硬收
  if (!hits || hits.length !== 1) return null;
  const url = hits[0];
  if (!safeHref(url)) return null;
  const label = t
    .replace(URL_ONLY_RE, " ")
    .replace(/^[-*•]\s*/, "")
    .replace(/^\d+[.、)]\s*/, "")
    .trim()
    // 「（链接）」「详见：」这类前缀是噪声，去掉
    .replace(/^[（(【\[]?\s*(链接|原文|来源|详见|参考|点击此处|点击)[：:]?\s*/, "")
    .replace(/[：:，,。.、;；\-—|]+$/g, "")
    .trim();
  // 剩下的字数超过 16：说明这行有实质内容，是一句话而不是一条链接
  if (label.length > 16) return null;
  return { url, label };
}

/** 链接上一行若是「- 日華琪爽炊（茂南区…）：这是它的页面」，取括号/冒号前那截当标题 */
function guessLabel(prev?: string): string {
  const t = (prev ?? "").trim();
  if (!t || !/^([-*•]|\d+[.、)])\s/.test(t)) return "";
  const body = t.replace(/^([-*•]|\d+[.、)])\s*/, "");
  const m = /^([^（(：:]{2,14})/.exec(body);
  return m ? m[1].trim() : "";
}

/**
 * 把回答末尾成串的裸链接摘出来。
 *
 * 只处理末尾：这是「先讲结论、末尾排来源」的典型形态。
 * 中途成串的链接不摘 —— 那里信息密度最高，硬收会把论点一起藏起来。
 * 摘不满 2 条就整体放弃：单条链接是正文的一部分，不是「一堆链接」。
 */
export function splitTrailingLinks(text: string): { body: string; links: LinkItem[] } {
  const empty = { body: text, links: [] as LinkItem[] };
  if (!text) return empty;
  const lines = text.split("\n");
  const links: LinkItem[] = [];
  let cut = lines.length;
  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i].trim();
    if (!t) continue; // 链接之间 / 末尾的空行不算阻断
    const hit = asLinkLine(t);
    if (!hit) break;
    if (!hit.label) hit.label = guessLabel(lines[i - 1]);
    links.unshift(hit);
    cut = i;
  }
  if (links.length < 2) return empty;
  // 末尾常还挂着一行「参考资料：」，一起收掉，否则正文里留个突兀的冒号
  let end = cut;
  while (end > 0 && !lines[end - 1].trim()) end--;
  if (
    end > 0 &&
    /^[#>*\s]*(参考(资料|来源|链接)?|来源|相关链接|网页来源|引用|链接)[：:]?\s*$/.test(lines[end - 1].trim())
  ) {
    end--;
  }
  return { body: lines.slice(0, end).join("\n"), links };
}

export function renderMarkdown(text: string, streaming = false): ReactNode {
  // 流式期间不摘：最后一条链接还在一个字一个字地长，摘了会来回跳（胶囊数字乱变）
  const split = streaming ? { body: text, links: [] as LinkItem[] } : splitTrailingLinks(text);
  const lines = split.body.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const nk = () => `md-${key++}`;

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块 ```（流式未闭合时收到底）
    if (/^\s*```/.test(line)) {
      const lang = line.replace(/^\s*```/, "").trim();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++; // 跳过闭合 ```（不存在则已到底）
      out.push(
        <pre key={nk()} className="md-pre" data-lang={lang || undefined}>
          <code>{buf.join("\n")}</code>
        </pre>
      );
      continue;
    }

    // 标题 # ~ ####
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const lvl = h[1].length;
      const content = inlineOf(h[2], nk());
      out.push(
        lvl === 1 ? <h3 key={nk()} className="md-h md-h1">{content}</h3>
        : lvl === 2 ? <h4 key={nk()} className="md-h md-h2">{content}</h4>
        : <h5 key={nk()} className="md-h md-h3">{content}</h5>
      );
      i++;
      continue;
    }

    // 分隔线
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push(<hr key={nk()} className="md-hr" />);
      i++;
      continue;
    }

    // 表格：当前行含 | 且下一行是分隔行
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]) && !/^\s*[-*•]\s/.test(line)) {
      const head = splitTableRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitTableRow(lines[i++]));
      out.push(
        <div key={nk()} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>{head.map((c, ci) => <th key={ci}>{inlineOf(c, `${ci}-h`)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inlineOf(c, `${ri}-${ci}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // 引用块 >
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(<blockquote key={nk()} className="md-quote">{inlineOf(buf.join("\n"), nk())}</blockquote>);
      continue;
    }

    // 无序列表（连续行收成一组；缩进两层以内都算同级，嵌套列表暂不展开）
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s+/, ""));
      out.push(
        <ul key={nk()} className="md-ul">
          {items.map((it, ii) => <li key={ii}>{inlineOf(it, `${ii}-u`)}</li>)}
        </ul>
      );
      continue;
    }

    // 有序列表（1. / 1、 / 1)）
    if (/^\s*\d+[.、)]\s*/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.、)]\s*/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.、)]\s*/, ""));
      out.push(
        <ol key={nk()} className="md-ol">
          {items.map((it, ii) => <li key={ii}>{inlineOf(it, `${ii}-o`)}</li>)}
        </ol>
      );
      continue;
    }

    // 空行：段落分隔
    if (!line.trim()) {
      i++;
      continue;
    }

    // 普通段落：连续非空、且不属于上面任何块级的行合成一段；段内单换行 → <br/>
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*```/.test(lines[i]) &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !/^\s*[-*•]\s+/.test(lines[i]) &&
      !/^\s*\d+[.、)]\s*/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i])
    ) {
      buf.push(lines[i++]);
    }
    if (buf.length) {
      out.push(
        <p key={nk()} className="md-p">
          {buf.map((l, li) => (
            <span key={li}>
              {inlineOf(l, `${li}-p`)}
              {li < buf.length - 1 && <br />}
            </span>
          ))}
        </p>
      );
    } else {
      // 兜底：不属于任何已知块级又进不了段落（理论到不了），跳过防死循环
      i++;
    }
  }

  return (
    <div className="md">
      {out}
      {split.links.length > 0 && <LinkCluster items={split.links} />}
    </div>
  );
}

// ============ 引用角标：解析后端 refs 字符串 ============
// 格式：**[1]** 文件名.pdf 第2页 ｜ score=0.812 ｜ 预览片段...
export interface RefItem {
  idx: string;
  source: string;
  score: string;
  preview: string;
}

export function parseRefs(refs?: string): RefItem[] {
  if (!refs) return [];
  return refs
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((line) => {
      const m = line.match(/^\*\*\[(\d+)\]\*\*\s*([\s\S]*)$/);
      const idx = m?.[1] ?? "";
      const parts = (m?.[2] ?? line).split("｜").map((s) => s.trim());
      const source = parts[0] ?? "";
      const scorePart = parts.find((s) => s.startsWith("score="));
      return {
        idx,
        source,
        score: scorePart ? scorePart.replace("score=", "") : "",
        preview: parts.filter((s) => s !== source && s !== scorePart).join(" "),
      };
    })
    .filter((r) => r.idx);
}
