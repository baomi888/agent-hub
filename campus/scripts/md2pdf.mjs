/**
 * 把审核报告（Markdown）转成「一个文件、图片内嵌」的 PDF。
 *
 * 为什么需要：报告要发给微信里的人看。微信不渲染 Markdown，
 * 相对路径的图片在转发后必然失效 —— 对方点开只见一堆碎图。
 * 唯一稳的办法是把图片 base64 塞进同一个 HTML，再打印成 PDF（微信原生支持预览与转发）。
 *
 * 用法：
 *   node campus/scripts/md2pdf.mjs campus/reviews/2026-11-20_大学年历表.md
 *   node campus/scripts/md2pdf.mjs <输入.md> [输出.pdf] [文档标题]
 *
 * 输出：默认与输入同名的 .pdf；标题默认取文件名。
 * 依赖：系统 Edge 或 Chrome（Chromium 内核，用 --print-to-pdf 打页）。
 * 只支持报告里实际用到的 Markdown 子集：标题 / 段落 / 表格 / 图 / 链接 /
 * 行内代码 / 代码块 / 引用 / 列表 / 分隔线。够用即可，不追求通用。
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const [, , SRC, OUT_PDF_ARG, TITLE_ARG] = process.argv;
if (!SRC) {
  console.error("用法: node md2pdf.mjs <输入.md> [输出.pdf] [文档标题]");
  process.exit(1);
}

const OUT_PDF = OUT_PDF_ARG || SRC.replace(/\.md$/i, ".pdf");
const TITLE = TITLE_ARG;
const OUT_HTML = path.join(os.tmpdir(), `zhisui-report-${process.pid}.html`);

const baseDir = path.dirname(path.resolve(SRC));
const md = fs.readFileSync(SRC, "utf8").replace(/\r\n/g, "\n");
const stats = { images: 0, imagesMissing: [] };

/* ---------------- 行内语法 ---------------- */

const esc = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** 图片转 data URI；找不到就留个占位框，别静默丢掉（丢图比难看更糟） */
function imageTag(src, alt) {
  const rel = src.replace(/^\.\//, "");
  const abs = path.resolve(baseDir, rel);
  if (!fs.existsSync(abs)) {
    stats.imagesMissing.push(rel);
    return `<span class="missing-img">图片缺失：${esc(rel)}</span>`;
  }
  const buf = fs.readFileSync(abs);
  const ext = path.extname(abs).slice(1).toLowerCase();
  const mime = ext === "jpg" ? "jpeg" : ext === "svg" ? "svg+xml" : ext;
  stats.images += 1;
  return `<img src="data:image/${mime};base64,${buf.toString("base64")}" alt="${esc(alt)}">`;
}

/** 行内：图片 → 链接 → 粗体 → 行内代码 → 转义 */
function inline(text) {
  const slots = [];
  const hold = (html) => {
    slots.push(html);
    return `\u0000${slots.length - 1}\u0000`;
  };

  // 图片：![]() —— alt 不参与 figcaption（图注用整段说明更自然）
  let out = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, src) => hold(imageTag(src, alt)));

  // 链接：站内相对链接（图片原图、其他 md）在 PDF 里点不动，降级为纯文本
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    if (/^https?:/i.test(href)) {
      return hold(`<a href="${esc(href)}">${esc(label)}</a>`);
    }
    return hold(`<span class="local-link">${esc(label)}</span>`);
  });

  out = out.replace(/`([^`]+)`/g, (_, code) => hold(`<code>${esc(code)}</code>`));
  out = out.replace(/\*\*([^*]+)\*\*/g, (_, b) => hold(`<strong>${esc(b)}</strong>`));

  out = esc(out);
  // 还原占位符（占位符是 \u0000N\u0000，转义不会动它）
  out = out.replace(/\u0000(\d+)\u0000/g, (_, i) => slots[Number(i)]);
  return out;
}

/* ---------------- 块级解析 ---------------- */

const lines = md.split("\n");
const html = [];
let i = 0;

const isTableSep = (s) => /^\|[\s:|-]+\|$/.test(s.trim());
const splitRow = (s) =>
  s
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

/** 「打开原图」这类死链在 PDF 里没有意义，整段跳过 */
const isDeadLinkPara = (s) => /^\[(打开|查看)原图\]\([^)]+\)$/.test(s.trim());

while (i < lines.length) {
  const line = lines[i];

  // 代码块
  if (/^```/.test(line.trim())) {
    const buf = [];
    i += 1;
    while (i < lines.length && !/^```/.test(lines[i].trim())) {
      buf.push(lines[i]);
      i += 1;
    }
    i += 1;
    html.push(`<pre>${esc(buf.join("\n"))}</pre>`);
    continue;
  }

  // 标题
  const h = line.match(/^(#{1,6})\s+(.*)$/);
  if (h) {
    const level = h[1].length;
    const text = h[2].trim();
    // 文档标题由封面承担，正文里的第一个 h1 不重复
    if (!(level === 1 && html.length === 0)) {
      html.push(`<h${level}>${inline(text)}</h${level}>`);
    }
    i += 1;
    continue;
  }

  // 分隔线
  if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
    html.push('<hr>');
    i += 1;
    continue;
  }

  // 表格
  if (/^\|/.test(line.trim()) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
    const head = splitRow(line);
    i += 2;
    const body = [];
    while (i < lines.length && /^\|/.test(lines[i].trim())) {
      body.push(splitRow(lines[i]));
      i += 1;
    }
    const th = head.map((c) => `<th>${inline(c)}</th>`).join("");
    const trs = body
      .map((cells) => {
        const tds = head
          .map((_, idx) => `<td>${inline(cells[idx] ?? "")}</td>`)
          .join("");
        return `<tr>${tds}</tr>`;
      })
      .join("");
    html.push(`<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`);
    continue;
  }

  // 引用
  if (/^>\s?/.test(line)) {
    const buf = [];
    while (i < lines.length && /^>\s?/.test(lines[i])) {
      buf.push(lines[i].replace(/^>\s?/, ""));
      i += 1;
    }
    html.push(`<blockquote>${inline(buf.join(" "))}</blockquote>`);
    continue;
  }

  // 列表（只处理单层，报告里没有嵌套）
  if (/^(\s*)([-*]|\d+\.)\s+/.test(line)) {
    const ordered = /^\s*\d+\./.test(line);
    const items = [];
    while (i < lines.length && /^(\s*)([-*]|\d+\.)\s+/.test(lines[i])) {
      items.push(lines[i].replace(/^(\s*)([-*]|\d+\.)\s+/, ""));
      i += 1;
      // 列表项的续行（缩进）
      while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^(\s*)([-*]|\d+\.)\s+/.test(lines[i])) {
        items[items.length - 1] += ` ${lines[i].trim()}`;
        i += 1;
      }
    }
    const tag = ordered ? "ol" : "ul";
    html.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
    continue;
  }

  // 空行
  if (!line.trim()) {
    i += 1;
    continue;
  }

  // 段落：连续的普通行合并
  const buf = [];
  while (
    i < lines.length &&
    lines[i].trim() &&
    !/^(#{1,6})\s/.test(lines[i]) &&
    !/^```/.test(lines[i].trim()) &&
    !/^\|/.test(lines[i].trim()) &&
    !/^>\s?/.test(lines[i]) &&
    !/^(\s*)([-*]|\d+\.)\s+/.test(lines[i]) &&
    !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())
  ) {
    buf.push(lines[i].trim());
    i += 1;
  }
  const para = buf.join(" ");
  if (isDeadLinkPara(para)) continue;

  // 单独一行就是一张图：包成 figure，图不受文本行高影响
  const onlyImg = para.match(/^!\[([^\]]*)\]\(([^)\s]+)\)$/);
  if (onlyImg) {
    html.push(`<figure>${imageTag(onlyImg[2], onlyImg[1])}</figure>`);
    continue;
  }
  html.push(`<p>${inline(para)}</p>`);
}

/* ---------------- 组装 ---------------- */

const docTitle = TITLE || path.basename(SRC, ".md");
const today = new Date();
const stamp = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(
  today.getDate(),
).padStart(2, "0")}`;

const css = `
:root {
  --ink: #2c2a26;
  --ink-2: #55524b;
  --ink-3: #8a857c;
  --line: #e2ddd4;
  --line-2: #cfc8bc;
  --paper: #ffffff;
  --tint: #f7f5f0;
  --accent: #4E8A84;
}
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0;
  font-family: "Microsoft YaHei", "PingFang SC", "Source Han Sans SC", "Noto Sans CJK SC", sans-serif;
  font-size: 10.5pt;
  line-height: 1.72;
  color: var(--ink);
  background: var(--paper);
}
.wrap { max-width: 100%; }

/* 封面头 */
.cover {
  padding: 0 0 14pt;
  margin-bottom: 18pt;
  border-bottom: 2.5pt solid var(--accent);
}
.cover .kicker { font-size: 9pt; letter-spacing: .18em; color: var(--accent); font-weight: 700; }
.cover h1 { margin: 6pt 0 4pt; font-size: 21pt; line-height: 1.3; letter-spacing: -.01em; }
.cover .meta { font-size: 9pt; color: var(--ink-3); }
.cover .meta b { color: var(--ink-2); font-weight: 600; }

h2 {
  margin: 22pt 0 8pt;
  padding-bottom: 5pt;
  font-size: 15pt;
  border-bottom: 1pt solid var(--line-2);
  page-break-after: avoid;
}
h3 { margin: 17pt 0 6pt; font-size: 12pt; page-break-after: avoid; }
h4 { margin: 13pt 0 5pt; font-size: 10.5pt; color: var(--ink-2); page-break-after: avoid; }
p { margin: 7pt 0; }
strong { font-weight: 700; }

hr { border: 0; border-top: 1pt solid var(--line); margin: 16pt 0; }

blockquote {
  margin: 9pt 0;
  padding: 7pt 11pt;
  border-left: 3pt solid var(--accent);
  background: var(--tint);
  color: var(--ink-2);
  page-break-inside: avoid;
}
blockquote p { margin: 0; }

ul, ol { margin: 7pt 0; padding-left: 20pt; }
li { margin: 3pt 0; }

code {
  font-family: "Cascadia Mono", Consolas, "Microsoft YaHei", monospace;
  font-size: 9pt;
  padding: 1pt 3.5pt;
  border-radius: 3pt;
  background: var(--tint);
  border: .5pt solid var(--line);
}
pre {
  margin: 9pt 0;
  padding: 9pt 11pt;
  background: var(--tint);
  border: .5pt solid var(--line);
  border-radius: 4pt;
  font-family: "Cascadia Mono", Consolas, "Microsoft YaHei", monospace;
  font-size: 8.5pt;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  page-break-inside: avoid;
}
pre code { padding: 0; border: 0; background: none; font-size: inherit; }

table {
  width: 100%;
  margin: 10pt 0;
  border-collapse: collapse;
  font-size: 9pt;
  page-break-inside: auto;
}
th, td {
  padding: 5pt 7pt;
  border: .5pt solid var(--line-2);
  text-align: left;
  vertical-align: top;
}
th { background: var(--tint); font-weight: 700; color: var(--ink-2); }
tr { page-break-inside: avoid; }
tbody tr:nth-child(even) td { background: #fbfaf7; }

figure { margin: 10pt 0; page-break-inside: avoid; }
img {
  display: block;
  width: 100%;
  max-width: 100%;
  height: auto;
  border: .5pt solid var(--line-2);
  border-radius: 4pt;
}
.local-link { color: var(--ink-2); }
a { color: var(--accent); text-decoration: none; word-break: break-all; }
.missing-img {
  display: block;
  padding: 14pt;
  border: 1pt dashed #c0392b;
  color: #c0392b;
  font-size: 9pt;
  text-align: center;
}

@page { size: A4; margin: 15mm 14mm 16mm; }
@media print {
  h2, h3, h4 { page-break-after: avoid; }
}
`;

const body = html.join("\n");
const out = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>${esc(docTitle)}</title>
<style>${css}</style>
</head>
<body>
<div class="wrap">
  <div class="cover">
    <div class="kicker">知穗 · 校园工具集</div>
    <h1>${esc(docTitle)}</h1>
    <div class="meta">导出时间 ${stamp} · 正文图片 ${stats.images} 张已内嵌 · 由 <b>${path.basename(SRC)}</b> 转换</div>
  </div>
${body}
</div>
</body>
</html>`;

fs.writeFileSync(OUT_HTML, out, "utf8");
console.log(`图片 ${stats.images} 张已内嵌 → 中间 HTML`);
if (stats.imagesMissing.length) {
  console.log(`⚠ 缺失图片 ${stats.imagesMissing.length} 张（PDF 里会留红色占位框）：`);
  stats.imagesMissing.forEach((p) => console.log("  - " + p));
}

/* ---------------- 打印 PDF ---------------- */

const BROWSERS = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
];
const browser = BROWSERS.find((p) => fs.existsSync(p));
if (!browser) {
  console.error("找不到 Edge / Chrome，无法打印 PDF。HTML 已留在：" + OUT_HTML);
  process.exit(2);
}

const profile = path.join(os.tmpdir(), `zhisui-pdf-profile-${process.pid}`);
const args = [
  "--headless=new",
  "--disable-gpu",
  "--no-pdf-header-footer",
  "--run-all-compositor-stages-before-draw",
  "--virtual-time-budget=15000",
  `--user-data-dir=${profile}`,
  `--print-to-pdf=${path.resolve(OUT_PDF)}`,
  "file:///" + OUT_HTML.replace(/\\/g, "/"),
];

const r = spawnSync(browser, args, { encoding: "utf8", timeout: 180_000 });
const errLine = (r.stderr ?? "").split("\n").find((l) => /bytes written|error/i.test(l) && !/fallback_task_provider/.test(l));

// 清掉临时目录，别在系统临时区留垃圾
try {
  fs.rmSync(OUT_HTML, { force: true });
  fs.rmSync(profile, { recursive: true, force: true });
} catch {
  /* 删不掉不影响交付 */
}

if (!fs.existsSync(OUT_PDF)) {
  console.error("PDF 未生成。", errLine ?? r.stderr?.slice(0, 300));
  process.exit(3);
}
const kb = Math.round(fs.statSync(OUT_PDF).size / 1024);
console.log(`PDF: ${OUT_PDF}（${kb} KB）`);

