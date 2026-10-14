/* eslint-disable no-console */
/**
 * 知穗 · 线上四屏验收（每次 deploy 之后跑一次）
 *
 *   LIVE_BASE=http://8.163.62.25:3000 \
 *   NODE_PATH=<node workspace>/node_modules \
 *   node tools/shot_console_live.js
 *
 * 与 tools/shot_console.js（本地 dev 验收）的分工：
 *   - 本地那个跑 localhost:3000，看的是**开发态**；
 *   - 这个跑公网地址，看的是**部署后的真实产物**（`.next` build 出来的），
 *     且顺带验证「后端只在 127.0.0.1 上、公网 :8000 不可达」这条隔离。
 *
 * 它做六件事：
 *   1. 幂等拿账号：先试登录，失败再注册（不重复造号）
 *   1b.hybrid 探针（信息性）：`/api/hybrid/workflows` 200=今天的合并已部署；
 *      404=尚未 deploy（不是失败，只是提示）。四屏断言与它无关。
 *   2. 四屏逐个打开（浅色），断言**真实数据模式**：
 *      不得出现「接口待上线」（checkin 有 1 处常驻披露块，见 allowNotReady）
 *      且本页至少 1 个 /api 200（只查「没有待上线」会放跑 500 全挂的假健康）。
 *      —— 2026-10-14 起 AI 模块（delegate/report/schedule/agent）已部署到线上，
 *      旧版「必须显示降级 UI」的断言随之退役。
 *   3. 明暗各截一张，用 `?theme=` 硬跳转钉主题，并**断言实际底色亮度**
 *      （只比 class 或只比文件字节都放跑过 bug，见 2026-10-13 那次）
 *   4. console 错误分类：401/404 属预期，其余才算真错误
 *   5. 文案体检：emoji / 残留 `**` / 缺失关键文案
 *   （本地验证跑法：LIVE_BASE=http://localhost:3100，跳过「后端不可直连」检查）
 */
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const BASE = (process.env.LIVE_BASE || "http://8.163.62.25:3000").replace(/\/+$/, "");
const API_BASE = BASE.replace(/:3000$/, ":3000"); // 前端反代 /api/*
const USER = process.env.LIVE_USER || "zz_accept1014";
const PASS = process.env.LIVE_PASS || "BaomiAccept2026";
const CHROME_PATH =
  process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const OUT = process.env.OUT_DIR || path.resolve(__dirname, "..", "docs", "实机截图", "console-live-2026-10-14");

const PAGES = [
  { key: "delegate", path: "/delegate", must: ["每日委托"] },
  { key: "report", path: "/report", must: ["每日总结"] },
  // checkin 有一处**常驻**披露块：打卡接口（/api/checkin/*、streak）后端尚未实现，
  // 页面用本机记录推算并如实标注（后端补端点前，这处「接口待上线」应保留）。
  { key: "checkin", path: "/checkin", must: ["打卡"], allowNotReady: 1 },
  { key: "settings", path: "/settings", must: ["设置"] },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);

/** 相对亮度 0–255：浅色纸面 ≈240，深色墨底 ≈32 */
function luma(rgb) {
  const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(rgb || "");
  if (!m) return null;
  return 0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3];
}

/** 5 个采样点（相对视口的比例），避开按钮/文字密集区 */
const SAMPLE_PTS = [
  [0.66, 0.05],
  [0.66, 0.35],
  [0.66, 0.65],
  [0.3, 0.97],
  [0.95, 0.2],
];

/**
 * ★ 主题判定一律**读渲染像素**，不读 DOM 计算样式。
 *
 * 原因（2026-10-14 实测）：线上页面的 `html` 与 `body` 的 `backgroundColor`
 * 都是 `rgba(0, 0, 0, 0)`（透明），底色由更内层元素/伪元素绘制，
 * 按 DOM 取色会得到「透明」这种既像浅色又像深色的废值 ——
 * 上一轮就是靠「比字节 / 比 class」放跑了一整套假截图。
 * 这里把视口截成一张图，交给 canvas 读回真实像素，绕开全部样式层歧义。
 */
async function sampleViewport(page) {
  const vp = page.viewport();
  const b64 = await page.screenshot({
    encoding: "base64",
    clip: { x: 0, y: 0, width: vp.width, height: vp.height },
  });
  const cols = await page.evaluate(
    async ({ b, pts }) => {
      const img = new Image();
      img.src = "data:image/png;base64," + b;
      await img.decode();
      const cv = document.createElement("canvas");
      cv.width = img.width;
      cv.height = img.height;
      const g = cv.getContext("2d");
      g.drawImage(img, 0, 0);
      return pts.map(([rx, ry]) => {
        const x = Math.min(img.width - 1, Math.max(0, Math.round(rx * img.width)));
        const y = Math.min(img.height - 1, Math.max(0, Math.round(ry * img.height)));
        const d = g.getImageData(x, y, 1, 1).data;
        return `rgb(${d[0]}, ${d[1]}, ${d[2]})`;
      });
    },
    { b: b64, pts: SAMPLE_PTS }
  );
  // 5 个点亮度的中位数：避开个别采样点落在深色按钮上的干扰
  const ls = cols.map(luma).filter((v) => v !== null).sort((a, b) => a - b);
  const median = ls.length ? ls[Math.floor(ls.length / 2)] : null;
  return { cols, l: median };
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: ["--no-proxy-server", "--proxy-bypass-list=*", "--no-sandbox"],
    defaultViewport: { width: 1440, height: 1000, deviceScaleFactor: 1 },
  });

  const problems = [];
  const consoleErrors = [];
  const netFails = [];

  const page = await browser.newPage();
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    const url = (m.location() && m.location().url) || "";
    // 预期内的三类：端点没上线的 404、未登录的 401、以及**外链 CDN 字体**的加载失败
    // （字体走 cdn.jsdelivr.net，国内网络时通时断 —— 那是外部资源问题，
    //   但页面对它的依赖本身另有隐患，见 README/规范里「字体离线化」那条）
    const expected =
      /401|404|Not Found|Unauthorized/i.test(t) ||
      /cdn\.jsdelivr\.net|fonts\.googleapis|fonts\.gstatic/.test(url);
    consoleErrors.push({ text: t, url, expected });
  });
  page.on("requestfailed", (r) => netFails.push(`${r.url()} :: ${r.failure()?.errorText}`));
  // 当前页段的 /api 200 计数（每次导航前清空）——新版断言「真实数据到达」用
  const api200 = [];
  page.on("response", (r) => {
    try {
      if (r.url().includes("/api/") && r.status() === 200) api200.push(r.url());
    } catch {
      /* 忽略 */
    }
  });

  // ---- 0) 网络可达性 + 隔离（后端不该对外） ----
  log("=== 0. 连通性 ===");
  const connChecks = [
    ["首页", `${BASE}/`, 200],
    ["auth 配置", `${API_BASE}/api/auth/config`, 200],
  ];
  // 「后端不可公网直连」只对线上地址有意义：本地跑时后端本来就在 8000
  if (!/localhost|127\.0\.0\.1/.test(BASE)) {
    connChecks.push(["后端 :8000 直连（应连不上）", BASE.replace(/:\d+$/, ":8000") + "/health", null]);
  }
  for (const [label, url, want] of connChecks) {
    let code = "000";
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
      code = res.status;
    } catch {
      code = "000";
    }
    const ok = want === null ? code === "000" : code === want;
    log(`  ${ok ? "OK " : "!! "} ${label} => ${code}`);
    if (!ok) problems.push(`${label} 期望 ${want ?? "连不上"}，实得 ${code}`);
  }

  // ---- 1) 幂等拿账号 ----
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 60000 });
  const acct = await page.evaluate(
    async ([u, p]) => {
      const post = async (p2) => {
        const r = await fetch(p2, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: u, password: p }),
        });
        return { status: r.status, body: await r.text() };
      };
      const li = await post("/api/auth/login/");
      if (li.status === 200) return { via: "login", ...li };
      const rg = await post("/api/auth/register/");
      return { via: "register", ...rg };
    },
    [USER, PASS]
  );
  log(`\n=== 1. 账号（${acct.via}）=> HTTP ${acct.status} ===`);
  if (acct.status !== 200) problems.push(`取账号失败：${acct.body.slice(0, 200)}`);

  // ---- 1b) hybrid 探针（信息性，不改变四屏断言） ----
  // 2026-10-14 实测：线上已部署 AI 模块（delegate/report/schedule/agent 均 401=存在），
  // 四屏走真实数据；唯一 404 的是当天新合并的 hybrid。探针只报告部署进度：
  // /api/hybrid/workflows 200=已部署；404=尚未 deploy。
  const hybridProbe = await page.evaluate(async () => {
    const r = await fetch("/api/hybrid/workflows", { credentials: "include" });
    return r.status;
  });
  log(
    `\n=== 1b. hybrid 探针 === /api/hybrid/workflows => ${hybridProbe}` +
      `（${hybridProbe === 200 ? "已部署" : hybridProbe === 404 ? "未部署（本次合并还没 deploy）" : "异常状态"}）`
  );
  if (hybridProbe !== 200 && hybridProbe !== 404) {
    problems.push(`hybrid 探针异常状态 ${hybridProbe}（401=登录票据没生效；5xx=后端故障）`);
  }

  // ---- 2) 四屏 + 文案体检（断言方向随版本） ----
  log("\n=== 2. 四屏（浅色）===");
  const shots = [];
  for (const p of PAGES) {
    api200.length = 0;
    await page.goto(`${BASE}${p.path}?theme=light`, { waitUntil: "networkidle2", timeout: 60000 });
    await sleep(1800);
    await page.addStyleTag({ content: "nextjs-portal{display:none!important}" });

    const st = await page.evaluate(() => {
      const txt = document.body.innerText;
      const EMOJI = /[\u{1F300}-\u{1FAFF}\u{1F000}-\u{1F02F}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/u;
      return {
        title: document.title,
        len: txt.length,
        notReady: (txt.match(/接口待上线/g) || []).length,
        demo: (txt.match(/演示数据/g) || []).length,
        emoji: (txt.match(new RegExp(EMOJI, "gu")) || []).length,
        stars: (txt.match(/\*\*/g) || []).length,
        isDark: document.documentElement.classList.contains("dark"),
        domBg: getComputedStyle(document.body).backgroundColor,
        nav: (() => {
          const el = document.querySelector("a.nav-item[aria-current='page']");
          return el ? getComputedStyle(el).color : null;
        })(),
        text: txt,
      };
    });
    const px = await sampleViewport(page);
    const got200 = api200.length;

    const miss = p.must.filter((m) => !st.text.includes(m));
    // 真实数据模式的统一断言：待上线块 ≤ 按页豁免数；且必须有真实 /api 200
    // （只查「没有待上线」会放跑 500 全挂的假健康）
    const allowNotReady = p.allowNotReady ?? 0;
    const verOk = st.notReady <= allowNotReady && got200 >= 1;
    const ok = miss.length === 0 && !st.isDark && px.l !== null && px.l > 180 && verOk;
    log(
      `  ${ok ? "OK " : "!! "} ${p.path.padEnd(11)} 待上线块=${st.notReady} 演示标记=${st.demo} ` +
        `api200=${got200} emoji=${st.emoji} 残留**=${st.stars} 像素=${px.cols[1]}(${px.l === null ? "?" : px.l.toFixed(0)})`
    );
    if (miss.length) problems.push(`${p.path} 缺关键文案：${miss.join("/")}`);
    if (st.emoji) problems.push(`${p.path} 出现 emoji ${st.emoji} 处`);
    if (st.stars) problems.push(`${p.path} 残留字面 ** ${st.stars} 处`);
    if (st.isDark) problems.push(`${p.path} 浅色模式下仍有 .dark`);
    if (px.l === null || px.l <= 180) problems.push(`${p.path} 浅色像素异常 ${px.cols.join(" / ")}`);
    if (st.notReady > allowNotReady)
      problems.push(`${p.path} 出现「接口待上线」${st.notReady} 处（允许 ${allowNotReady}）—— 本页接口没部署？`);
    if (got200 < 1) problems.push(`${p.path} 本页 0 个 /api 200（真实数据没到达）`);

    shots.push({ key: p.key, theme: "light", bg: px.cols[1], l: px.l, nav: st.nav });
    await page.screenshot({ path: path.join(OUT, `${p.key}.png`), fullPage: true });
  }

  // ---- 3) 深色过一遍 ----
  log("\n=== 3. 四屏（深色）===");
  for (const p of PAGES) {
    await page.goto(`${BASE}${p.path}?theme=dark`, { waitUntil: "networkidle2", timeout: 60000 });
    await sleep(1800);
    await page.addStyleTag({ content: "nextjs-portal{display:none!important}" });
    const st = await page.evaluate(() => ({
      isDark: document.documentElement.classList.contains("dark"),
      nav: (() => {
        const el = document.querySelector("a.nav-item[aria-current='page']");
        return el ? getComputedStyle(el).color : null;
      })(),
    }));
    const px = await sampleViewport(page);
    const ok = st.isDark && px.l !== null && px.l < 80;
    log(
      `  ${ok ? "OK " : "!! "} ${p.path.padEnd(11)} 像素=${px.cols[1]}(${px.l === null ? "?" : px.l.toFixed(0)}) nav=${st.nav}`
    );
    if (!st.isDark) problems.push(`${p.path} 深色模式未挂 .dark`);
    if (px.l === null || px.l >= 80) problems.push(`${p.path} 深色像素异常 ${px.cols.join(" / ")}`);
    shots.push({ key: p.key, theme: "dark", bg: px.cols[1], l: px.l, nav: st.nav });
    await page.screenshot({ path: path.join(OUT, `${p.key}-dark.png`), fullPage: true });
  }

  // ---- 4) 明暗必须真的不同（比亮度，不比字节） ----
  log("\n=== 4. 明暗差异 ===");
  for (const p of PAGES) {
    const a = shots.find((s) => s.key === p.key && s.theme === "light");
    const b = shots.find((s) => s.key === p.key && s.theme === "dark");
    const delta = Math.abs(a.l - b.l);
    const ok = delta > 80;
    log(`  ${ok ? "OK " : "!! "} ${p.key.padEnd(11)} Δ亮度=${delta.toFixed(0)}`);
    if (!ok) problems.push(`${p.key} 明暗亮度差仅 ${delta.toFixed(0)}，主题没真正切换`);
  }

  // ---- 5) console / 网络 ----
  const realErrors = consoleErrors.filter((e) => !e.expected);
  log("\n=== 5. 浏览器侧 ===");
  log(`  console 错误 ${consoleErrors.length} 条（其中预期内 401/404：${consoleErrors.length - realErrors.length}）`);
  realErrors.slice(0, 8).forEach((e) => log(`    !! ${e.text.slice(0, 120)}  @ ${e.url || "(无 url)"}`));
  const realNet = netFails.filter((s) => !/cdn\.jsdelivr\.net|fonts\.googleapis/.test(s));
  log(`  网络失败 ${netFails.length} 条（排除字体 CDN 后 ${realNet.length}）`);
  realNet.slice(0, 8).forEach((s) => log(`    !! ${s.slice(0, 160)}`));
  if (realErrors.length) problems.push(`console 真错误 ${realErrors.length} 条`);
  if (realNet.length) problems.push(`网络层失败 ${realNet.length} 条`);

  await browser.close();

  log("\n================ 结论 ================");
  if (hybridProbe === 404) {
    log("（注：hybrid 探针 404 = 当天的合并尚未部署；deploy 后重跑本脚本，此处应为 200）");
  }
  if (problems.length === 0) {
    log("线上四屏验收：全部通过");
  } else {
    log(`发现 ${problems.length} 处问题：`);
    problems.forEach((p) => log(`  - ${p}`));
  }
  log(`截图目录：${OUT}`);
  process.exit(problems.length === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error("验收脚本异常：", e);
  process.exit(2);
});
