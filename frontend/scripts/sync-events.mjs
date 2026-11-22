#!/usr/bin/env node
/**
 * 官网公告同步（构建前跑一次，也可以手动跑）
 *
 *   node scripts/sync-events.mjs
 *
 * 只做一件事：把各赛事/考试官网首页上「最新相关公告」的标题和链接抓下来，
 * 写成 public/data/events/notices.json。页面打开时读这个文件，显示在条目详情里。
 *
 * ── 刻意不做的：解析日期、覆盖数据 ──────────────────────────────
 *
 * 报名时间确实在公告正文里（例如「CET 将于 12 月 12 日举行」），技术上抓得出来。
 * 但各站措辞不同、每年改版，正则一旦错位就会把「12 月 12 日」安到别的考试上 ——
 * 一条错日期比一条旧日期伤人得多：同学照着错日期准备，错过就是一年。
 *
 * 所以这里只负责「把人送到官方原文」：
 *   · 抓得到 → 页面上多一行「官网最新：2026 年下半年 CET 报名工作启动（09-02）」+ 链接
 *   · 抓不到 → 静默失败，页面回落到原来的静态文案，什么都不显示
 *
 * 日期仍然由维护者每年人工核对一次（页面底部标了「数据最后核对 2026-11」）。
 *
 * ── 为什么可以「实时」 ────────────────────────────────────────
 *
 * 项目用 output: "standalone" 部署，public/ 是直接拷进产物目录的静态文件。
 * 想立刻更新，把新的 notices.json 换上去即可，不用重新构建、不用重启。
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../public/data/events/notices.json");

/**
 * 一个源 = 一个首页 + 一条匹配规则 + 要挂到哪些条目 id 上。
 *
 * match 用正则而不是关键词数组：官网页面上「计算机」三个字到处都是，
 * 但「计算机等级考试」这个组合只出现在 NCRE 的公告标题里。
 */
const SOURCES = [
  {
    name: "四六级",
    url: "https://cet.neea.edu.cn",
    match: /四级|六级|CET/i,
    ids: ["cet4", "cet6"],
  },
  {
    name: "计算机等级",
    url: "https://ncre.neea.edu.cn",
    match: /计算机等级|NCRE/i,
    ids: ["ncre2"],
  },
  {
    name: "教师资格",
    url: "https://ntce.neea.edu.cn",
    match: /教师资格/i,
    ids: ["ntce-written", "ntce-interview"],
  },
  {
    name: "研招网",
    url: "https://yz.chsi.com.cn",
    /*
     * 只留全国性文件。
     *
     * 这个首页的「报名公告」栏里大量是各省市招生考试机构的公告（甘肃、重庆、北京…），
     * 对广东学生没用，还会把真正全国性的通知挤到两条之外。
     * 判据用「教育部」+「全国硕士研究生招生工作管理」，地方公告不会同时满足。
     */
    match: /教育部.*(硕士|研究生)|全国硕士研究生招生工作管理/,
    ids: ["kaoyan"],
  },
  {
    name: "数学建模",
    url: "https://www.mcm.edu.cn",
    match: /数学建模/,
    ids: ["mcm"],
  },
  /*
   * 下面三家是 JS 渲染的站点：服务端返回的 HTML 里只有壳，公告在客户端拉接口后才出现。
   * 留着不删 —— 站点改版时可能重新给出服务端渲染的列表，删了就永远发现不了。
   * 抓不到时打 ○ 而不是 ✗，页面侧也照旧回落到静态文案（详见 notices.ts 的三条原则）。
   */
  {
    name: "挑战杯",
    url: "https://www.tiaozhanbei.net",
    match: /挑战杯/,
    ids: ["tiaozhanbei-academic", "tiaozhanbei-business"],
  },
  {
    name: "互联网+",
    url: "https://cy.ncss.cn",
    match: /互联网\+|创新创业大赛|大赛通知/,
    ids: ["internet-plus"],
  },
  {
    name: "蓝桥杯",
    url: "https://dasai.lanqiao.cn",
    match: /蓝桥杯/,
    ids: ["lanqiao"],
  },
];

/** 这些标题不是公告，抓下来只会占地方 */
const NOISE = /^(更多|查看全部|列表|首页|上一页|下一页|返回|登录|注册|联系我们)$/;
/** 栏目名、导航项长得也像公告（「CET 委员会」「考试介绍」），靠结尾词剔除 */
const SECTION = /(委员会|官网|网站|首页|栏目|简介|介绍|章程|联系我们|English)$/;

/**
 * 给标题打分，只留真正的公告。
 *
 * 首页上同时挂着「CET 委员会」（栏目名）和「2026 年下半年…报名工作启动」（公告），
 * 两者都能匹配 /CET/，只看正则分不出来。报名/通知这类词才是公告的标志 ——
 * 没有它们的，多半是导航或往年新闻。
 */
/**
 * 标题打分，只用来排序，不用来删。
 *
 * 「什么时候能报名」是学生唯一真正着急的事，所以「报名」单独给最高分 ——
 * 只写「通知」的可能是征题通知、教评会、颁奖公示，学生点进去找不到自己要的日期。
 * 但这类标题也不过滤掉：它至少说明官网在动，比一块空白有用。
 */
function score(title) {
  let s = 0;
  if (/报名|报考/.test(title)) s += 6;
  if (/通知|简章|启动|公告|发布|安排/.test(title)) s += 3;
  if (/20\d{2}/.test(title)) s += 2;
  if (/大赛|竞赛|考试|招生/.test(title)) s += 1;
  return s;
}

/** 低于这个分不认：宁可少显示一行，也不要把导航项当公告 */
const MIN_SCORE = 5;

/** 从首页 HTML 里挑出匹配的公告链接（标题 + 绝对地址），最多留两条 */
function pickLinks(html, base, rule) {
  const candidates = [];
  const seen = new Set();
  const anchor = /<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]{0,120}?)<\/a>/gi;

  for (const m of html.matchAll(anchor)) {
    const href = m[1].trim();
    let text = m[2]
      .replace(/<[^>]+>/g, "")
      .replace(/&nbsp;/g, " ")
      .replace(/\s+/g, " ")
      .trim();

    /*
     * 有些站点的 <a> 写得不规范（href 不带引号、title 里嵌着引号），
     * 正则会把属性尾巴带进正文，出现「成绩查询有关安排">2026年上半年…」这种标题。
     * 属性到最后一个 > 就结束了，截掉前面那段。
     */
    if (text.includes(">")) text = text.slice(text.lastIndexOf(">") + 1).trim();
    text = text.replace(/^["'\s]+/, "").trim();
    // 站点列表会把长标题截成「…报名公...」，省略号留着只会让人以为标题就到这儿
    text = text.replace(/[.．…]{2,}$/, "").trim();

    if (text.length < 6 || text.length > 60) continue;
    if (NOISE.test(text)) continue;
    if (SECTION.test(text)) continue;
    if (!rule.match.test(text)) continue;
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) continue;

    let abs;
    try {
      abs = new URL(href, base).toString();
    } catch {
      continue;
    }
    if (seen.has(abs)) continue;
    seen.add(abs);

    const s = score(text);
    if (s >= MIN_SCORE) candidates.push({ title: text, url: abs, score: s });
  }

  // 分高的在前；同分保持页面原顺序（官网页面上越靠前越新）
  return candidates
    .sort((a, b) => b.score - a.score)
    .slice(0, 2)
    .map(({ title, url }) => ({ title, url }));
}

function decode(buf, contentType) {
  const head = Buffer.from(buf.slice(0, 4096)).toString("latin1");
  const m = head.match(/charset=["']?([\w-]+)/i);
  const charset = (m ? m[1] : /gb/i.test(contentType) ? "gbk" : "utf-8").toLowerCase();
  try {
    return new TextDecoder(charset === "gb2312" ? "gbk" : charset).decode(buf);
  } catch {
    return new TextDecoder("utf-8").decode(buf);
  }
}

async function fetchText(url, timeoutMs = 12_000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      redirect: "follow",
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        Accept: "text/html,application/xhtml+xml",
      },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    return decode(buf, res.headers.get("content-type") ?? "");
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  const notices = {};

  /*
   * 并发抓，不串行：串行时八个源最坏要 8×12 秒，挂在 prebuild 上会把
   * `npm run build` 拖到两分钟。并发之后整体取决于最慢的那一个。
   */
  const report = await Promise.all(
    SOURCES.map(async (src) => {
      try {
        const html = await fetchText(src.url);
        const links = pickLinks(html, src.url, src);
        for (const id of src.ids) {
          if (links.length > 0) notices[id] = links;
        }
        return { name: src.name, ok: true, found: links.length };
      } catch (err) {
        return { name: src.name, ok: false, found: 0, error: String(err?.message ?? err).slice(0, 60) };
      }
    }),
  );

  const total = Object.values(notices).reduce((n, list) => n + list.length, 0);
  const payload = {
    generatedAt: new Date().toISOString(),
    noticeCount: total,
    notices,
    sources: report,
  };

  // 全抓空时不覆盖旧文件：离线构建不该把线上已有的公告一起清掉
  if (total === 0) {
    try {
      const old = JSON.parse(await readFile(OUT, "utf-8"));
      console.log(`[sync-events] 本次一条也没抓到，保留旧文件（${old.noticeCount ?? 0} 条）`);
      return;
    } catch {
      /* 没有旧文件就照常写空 */
    }
  }

  await mkdir(dirname(OUT), { recursive: true });
  await writeFile(OUT, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");

  /*
   * 三态而不是两态。
   *
   * 原来「打开成功」一律打 ✓，于是「✓ 挑战杯 0 条」看起来像抓成功但其实什么都没拿到 ——
   * 这三家（挑战杯 / 互联网+ / 蓝桥杯）是 JS 渲染的站点，首屏 HTML 里没有公告链接。
   * 用 ○ 表示「站开着，但这层 HTML 里没有我们要的东西」，和真正的失败区分开。
   */
  console.log(`[sync-events] 写入 ${total} 条公告 → public/data/events/notices.json`);
  for (const r of report) {
    const mark = !r.ok ? "✗" : r.found > 0 ? "✓" : "○";
    const detail = !r.ok ? ` ${r.error}` : r.found > 0 ? ` ${r.found} 条` : " 页面无匹配公告（JS 渲染站常见）";
    console.log(`  ${mark} ${r.name}${detail}`);
  }
}

// prebuild 钩子调用：任何异常都不能拦住构建
main().catch((err) => {
  console.warn(`[sync-events] 跳过：${String(err?.message ?? err).slice(0, 80)}`);
});
