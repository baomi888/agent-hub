// 消息正文渲染：URL 自动 linkify + **粗体** + 换行；引用 refs 文本解析
// 从 ChatArea.tsx 拆出（2026-09 重构），全部是纯函数，适合单测
import type { ReactNode } from "react";
import type { WebSource } from "@/lib/types";
import LinkCluster, { type LinkItem } from "./LinkCluster";

// 排除空白与常见分隔符；中文标点也要排除，否则「https://xxx。」会把句号吞进链接
const URL_RE = /https?:\/\/[^\s<>"{}|\\^`\[\]。，、；：！？（）【】《》「」『』]+/gi;

/** 展示用 URL：把百分号编码解码回中文/原文，href 仍用原始值，跳转不受影响 */
function decodeUrl(url: string): string {
  try {
    return decodeURI(url);
  } catch {
    return url; // 含非法 % 序列时保持原文
  }
}

/**
 * 正文里显示的链接文字：长网址只留「域名/首段…」。
 *
 * 一行里塞一条 60 字的网址会把整段排版搅烂（中文行被撑开、换行点在乱七八糟
 * 的地方），这正是用户说的"正文被链接塞满"。href 本来就是完整地址，显示上
 * 缩短不影响任何功能 —— 想看全文可以悬停（title 给完整地址）。
 */
function displayUrl(url: string): string {
  const full = decodeUrl(url);
  if (full.length <= 36) return full;
  try {
    const u = new URL(url);
    const tail = (u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname));
    return tail.length <= 36 ? tail : `${tail.slice(0, 34)}…`;
  } catch {
    return `${full.slice(0, 34)}…`;
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
            title={decodeUrl(seg.value)}
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
  /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]+\]\([^)\s]+\))|(~~[^~\n]+~~)|(\[\d{1,2}\])|(https?:\/\/[^\s<>"{}|\\^`\[\]。，、；：！？（）【】《》「」『』]+)/gi;

/** 行内元素：行内代码 / 粗体 / 链接 / 删除线 / 引用角标 / 裸 URL */
export function renderInline(
  text: string,
  keyPrefix = "i",
  cites?: Map<number, WebSource>
): ReactNode[] {
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
      out.push(<strong key={key}>{renderInline(tok.slice(2, -2), key, cites)}</strong>);
    } else if (tok.startsWith("~~")) {
      out.push(<del key={key}>{renderInline(tok.slice(2, -2), key, cites)}</del>);
    } else if (tok.startsWith("[")) {
      // 引用角标 [n]：**只有本条回答真有联网来源、且 n 命中时**才变成可点徽标。
      // 命中不了就保持原样 —— 正文里「第[1]步」「[1] 先做…」这类编号很多，
      // 一律加框会变成满屏彩色小块，比不加更糟。
      const pure = /^\[(\d{1,2})\]$/.exec(tok);
      const cited = pure ? cites?.get(Number(pure[1])) : undefined;
      if (pure && cited) {
        const chref = safeHref(cited.url);
        out.push(
          chref ? (
            <a
              key={key}
              href={chref}
              target="_blank"
              rel="noopener noreferrer"
              className="ws-cite"
              title={`${pure[1]} · ${cited.title || cited.domain}（打开原网页）`}
            >
              {pure[1]}
            </a>
          ) : (
            <span key={key}>{tok}</span>
          )
        );
        last = m.index! + tok.length;
        continue;
      }
      // [文案](https://…)：目标必须过协议白名单，否则只留文案、丢掉链接
      const mm = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(tok);
      const href = mm ? safeHref(mm[2]) : null;
      if (mm && href) {
        out.push(
          <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="inline-link" title={decodeUrl(href)}>
            {renderInline(mm[1], key, cites)}
          </a>
        );
      } else if (mm) {
        out.push(<span key={key}>{renderInline(mm[1], key, cites)}</span>);
      } else {
        out.push(<span key={key}>{tok}</span>);
      }
    } else {
      // 裸 URL：正则已限定 http(s)，仍再走一次白名单兜底
      const href = safeHref(tok);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="inline-link" title={decodeUrl(href)}>
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

// 表格单元格等复用的行内渲染在 renderMarkdown 内部定义（要带上 cites 才认得出角标）

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
  // 网址**必须在行尾**才算"一条链接"。
  // 反例：「可以看这篇 https://… 讲得很细」——链接夹在句子里，两边都是正文，
  // 这种行一旦被当成来源收走，用户就再也读不到那句话了（改造前就会，2026-11-03
  // 补测试时暴露）。行尾只允许留收尾标点。
  const after = t.slice(t.indexOf(url) + url.length).replace(/[）)】\]。，,、；;：:\s]/g, "");
  if (after) return null;
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
  const byBlock = splitNumberedBlocks(text);
  if (byBlock.links.length >= 2) return byBlock;
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
  if (end > 0 && REF_HEAD_RE.test(lines[end - 1].trim())) end--;
  return { body: lines.slice(0, end).join("\n"), links };
}

// ============ 编号块收口（联网搜索结果的真实形态） ============
// web_search 交给模型的是「**[1]** 标题 / 摘要 / 网址」这种交错块，模型照抄后
// 正文末尾长这样：
//     **[1]** 茂名旅游攻略
//        滨海城市，适合两日游。
//        https://…
//     **[2]** 知乎讨论
//        …
//        https://…
// 这种形态**永远进不了上面那套纯链接行规则**：从末尾往上第一行是网址（收下），
// 第二行开始就是摘要，立刻 break —— 只收到 1 条，然后整体放弃。
// 结果就是「明明写了收口，用户还是看到满屏链接」，2026-11-03 的录屏反馈就是它。
//
// 所以这里换一种切法：按**编号行**切块，一块 = 编号标题 + 到下一个编号行之间的
// 一切（摘要、网址），只认「含网址」的块，并且这些块必须一路连续到文末。

/** 编号行：`**1** 标题` / `[1] 标题` / `1. 标题` / `1、标题`（数字最多两位，避免吞「2026 年」） */
const NUM_HEAD_RE = /^[\s>*]*(?:\*\*)?\s*\[?(\d{1,2})\]?\s*(?:\*\*)?\s*[.、:：)）]?\s+(.+)$/;

/** 「参考资料：」这类抬头，收口时一起收掉，否则正文里留个突兀的冒号 */
const REF_HEAD_RE =
  /^[#>*\s]*(参考(资料|来源|链接)?|来源|相关链接|网页来源|引用|链接)[：:]?\s*(?:\*\*)?\s*$/;

interface SrcBlock {
  title: string;
  url: string;
  start: number;
  end: number;
}

/** 扫全文找「编号块」：编号行往下 4 行内出现的第一条网址就算这个块的出处 */
function findNumberedBlocks(lines: string[]): SrcBlock[] {
  const out: SrcBlock[] = [];
  for (let i = 0; i < lines.length; i++) {
    const m = NUM_HEAD_RE.exec(lines[i].trim());
    if (!m) continue;
    const limit = Math.min(i + 4, lines.length - 1);
    for (let j = i; j <= limit; j++) {
      const hit = lines[j].match(URL_ONLY_RE);
      if (hit && hit[0] && safeHref(hit[0])) {
        out.push({ title: m[2].replace(/\*\*/g, "").trim(), url: hit[0], start: i, end: j });
        break;
      }
      // 撞到下一个编号行 = 本块没有网址，不收，别把下一块的地址算到它头上
      if (j > i && NUM_HEAD_RE.test(lines[j].trim())) break;
    }
  }
  return out;
}

/**
 * 收掉正文里的「来源块串」：连续 ≥2 个「编号标题 + 摘要 + 网址」块，连同它们的
 * 「参考资料：」抬头一起抽走，正文只留结论。
 *
 * ★ 不再限定"必须在文末"。2026-11-03 用一次**真实联网回答**验证时发现：
 *   模型把 8 条搜索结果整段抄在回答**开头**，然后才写正文（一、海滨 二、山林…）。
 *   只认末尾的版本对这条回答一条都收不到 —— 而它正是用户抱怨的那种回答。
 *   编号块本身的形态足够特征（行首 [n]/n. + 标题 + 四行内必有网址），
 *   误伤正文的风险来自"编号步骤列表"，而那种列表里没有网址，进不了这里。
 *
 * 块与块之间只隔空行才算同一串：中间夹着正文就断开，不做跨段的贪婪合并。
 */
function splitNumberedBlocks(text: string): { body: string; links: LinkItem[] } {
  const empty = { body: text, links: [] as LinkItem[] };
  if (!text) return empty;
  const lines = text.split("\n");
  const all = findNumberedBlocks(lines);
  if (all.length < 2) return empty;

  const runs: SrcBlock[][] = [];
  let cur: SrcBlock[] = [];
  for (const b of all) {
    if (cur.length) {
      const prev = cur[cur.length - 1];
      if (lines.slice(prev.end + 1, b.start).some((l) => l.trim())) {
        if (cur.length >= 2) runs.push(cur);
        cur = [];
      }
    }
    cur.push(b);
  }
  if (cur.length >= 2) runs.push(cur);
  if (!runs.length) return empty;

  const drop = new Set<number>();
  for (const run of runs) {
    let from = run[0].start;
    // 串上方若挂着「参考资料：」抬头，一起删掉，否则正文里剩个孤零零的冒号
    let probe = from - 1;
    while (probe >= 0 && !lines[probe].trim()) probe--;
    if (probe >= 0 && REF_HEAD_RE.test(lines[probe].trim())) from = probe;
    for (let i = from; i <= run[run.length - 1].end; i++) drop.add(i);
  }

  const links = runs.flatMap((run) =>
    // 标题截到 60 字：卡片标题区只有一行多的宽度，再长也显示不出来
    run.map((b) => ({ url: b.url, label: (b.title || "").slice(0, 60) }))
  );
  const kept = lines.filter((_, i) => !drop.has(i));
  // 抽走整段后会留下连续空行，压回一个段落间隔
  const body = kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return { body, links };
}

export function renderMarkdown(
  text: string,
  streaming = false,
  cites?: Map<number, WebSource>,
  /** 已在「来源卡片」里展示过的网址：正文收口时不再重复列一遍 */
  knownUrls?: Set<string>
): ReactNode {
  // 把一段文本拆成行内节点；带上 cites 之后，正文里的 [n] 才能变成可点徽标
  const inlineOf = (s: string, kp: string) => renderInline(s, kp, cites);
  // 流式期间不摘：最后一条链接还在一个字一个字地长，摘了会来回跳（胶囊数字乱变）
  const split = streaming ? { body: text, links: [] as LinkItem[] } : splitTrailingLinks(text);
  // 后端已经给出「来源卡片」的那几条，不要再在下面列一遍：同一条链接出现两个
  // 可点入口（一枚"2 个链接"胶囊 + 一枚"2 个来源"胶囊），用户只会觉得重复。
  // 只滤掉重复项，模型自己补进来的、来源里没有的链接仍然收口保留。
  const restLinks = knownUrls ? split.links.filter((l) => !knownUrls.has(l.url)) : split.links;
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
      {restLinks.length > 0 && <LinkCluster items={restLinks} />}
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
