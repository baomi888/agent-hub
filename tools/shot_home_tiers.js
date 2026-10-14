// 首屏「主次两层」与「模板 → 委托」链路的实机校验。
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/shot_home_tiers.js
//
// 背景（10-14 第二轮）：首屏原来是四宫格（模板广场 / 校园工具集 / 上传建库 / 让它去研究），
// 四件平级，结果「模板广场」这种一次性动作和「每日委托」这种天天用的能力一样醒目。
// 改成：
//   主线 3 个大卡（每日委托 / 每日总结 / 打卡记录）——指向四屏
//   辅路 4 个小胶囊（模板广场 / 上传资料建库 / 让它去研究 / 校园工具集）
// 并给模板广场每个模板加「设为每日委托」：预填委托指令后跳到 /delegate。
//
// 只打本机 dev，注册临时账号（hdemoXXXX），不动线上数据。
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3000";
const OUT = path.resolve(__dirname, "..", "docs", "实机截图", "home-tiers-2026-10-14");
const USER = "hdemo" + Math.random().toString(36).slice(2, 7);
const PASS = "demo123456";

const PHONE = { width: 390, height: 844, deviceScaleFactor: 1, isMobile: true, hasTouch: true };
const DESKTOP = { width: 1440, height: 900, deviceScaleFactor: 1 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[tiers]", ...a);

async function hideDevBadge(page) {
  try {
    await page.addStyleTag({ content: "nextjs-portal{display:none !important}" });
  } catch {
    /* 浮标不在就算了 */
  }
}

async function clickByText(page, text, tag = "button") {
  return page.evaluate(
    (t, g) => {
      const el = [...document.querySelectorAll(g)].find((x) =>
        (x.textContent || "").trim().includes(t)
      );
      if (!el) return false;
      el.click();
      return true;
    },
    text,
    tag
  );
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-proxy-server", "--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: DESKTOP,
  });
  const page = await browser.newPage();
  const problems = [];
  const realErrors = [];

  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    if (/401|404|Not Found|Unauthorized|ERR_CONNECTION_RESET/i.test(t)) return;
    realErrors.push(t);
  });
  page.on("pageerror", (e) => realErrors.push("PAGEERROR " + e.message));

  // ---------- 注册 ----------
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await sleep(1000);
  if (await page.evaluate(() => document.body.innerText.includes("注册"))) {
    await clickByText(page, "注册");
    await sleep(400);
    const u = await page.$('input:not([type="password"]):not([type="file"])');
    await u.type(USER);
    const p = await page.$('input[type="password"]');
    await p.type(PASS);
    await page.click('button[type="submit"]');
    log("注册临时账号", USER);
  }
  await page.waitForFunction(() => document.body.innerText.includes("资料库"), { timeout: 40000 });
  await sleep(1500);
  await hideDevBadge(page);

  // ---------- 1) 首屏两层结构 ----------
  const home = await page.evaluate(() => {
    const cards = [...document.querySelectorAll(".entry-card")].map((el) => ({
      text: (el.textContent || "").trim(),
      href: el.getAttribute("href"),
      w: Math.round(el.getBoundingClientRect().width),
      h: Math.round(el.getBoundingClientRect().height),
    }));
    const minis = [...document.querySelectorAll(".entry-mini")].map((el) =>
      (el.textContent || "").trim()
    );
    const legacy = document.querySelector(".intro-entry") ? 1 : 0;
    return { cards, minis, legacy };
  });
  log("主线大卡 =", JSON.stringify(home.cards));
  log("辅路小入口 =", JSON.stringify(home.minis));
  const wantHrefs = ["/delegate", "/report", "/checkin"];
  const gotHrefs = home.cards.map((c) => c.href);
  if (JSON.stringify(gotHrefs) !== JSON.stringify(wantHrefs)) {
    problems.push(`主线卡 href 应为 ${JSON.stringify(wantHrefs)}，实得 ${JSON.stringify(gotHrefs)}`);
  }
  if (home.cards.some((c) => c.h < 60)) {
    problems.push("主线大卡高度不足 60px，没和辅路拉开层级");
  }
  if (home.minis.length !== 4) problems.push(`辅路入口应有 4 个，实得 ${home.minis.length}`);
  for (const want of ["模板广场", "上传资料建库", "让它去研究", "校园工具集"]) {
    if (!home.minis.includes(want)) problems.push(`辅路里缺「${want}」`);
  }
  if (home.legacy) problems.push("旧的 .intro-entry 还在渲染");

  await page.screenshot({ path: path.join(OUT, "home-desktop.png") });

  // 点第一张主线卡：应跳到 /delegate
  await page.evaluate(() => document.querySelector(".entry-card")?.click());
  await sleep(2000);
  const landed = await page.evaluate(() => location.pathname);
  log("点主线第一张卡 →", landed);
  if (landed !== "/delegate") problems.push(`主线卡跳转失败：期望 /delegate 实得 ${landed}`);

  // ---------- 2) 手机视口下的首屏 ----------
  await page.setViewport(PHONE);
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await sleep(1800);
  await hideDevBadge(page);
  const phoneHome = await page.evaluate(() => {
    const vw = window.innerWidth;
    const over = [];
    for (const el of document.querySelectorAll(".welcome *")) {
      const b = el.getBoundingClientRect();
      if (b.width > 0 && b.right > vw + 1) over.push(el.className.toString().slice(0, 40));
    }
    return {
      over: over.slice(0, 5),
      overCount: over.length,
      cards: document.querySelectorAll(".entry-card").length,
      minis: document.querySelectorAll(".entry-mini").length,
    };
  });
  log("手机首屏 =", JSON.stringify(phoneHome));
  if (phoneHome.cards !== 3) problems.push(`手机首屏主线卡 ${phoneHome.cards} 个`);
  if (phoneHome.overCount) problems.push(`手机首屏横向溢出 ${phoneHome.overCount} 处：${phoneHome.over.join("｜")}`);
  await page.screenshot({ path: path.join(OUT, "home-phone.png"), fullPage: true });

  // ---------- 3) 模板广场 → 设为每日委托 ----------
  await page.setViewport(DESKTOP);
  await page.goto(BASE + "/templates", { waitUntil: "networkidle2", timeout: 60000 });
  await sleep(2200);
  await hideDevBadge(page);
  const tpl = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("article")];
    const first = cards[0];
    const btn = first
      ? [...first.querySelectorAll("button")].find((b) =>
          (b.textContent || "").includes("设为每日委托")
        )
      : null;
    return {
      count: cards.length,
      hasDelegateBtn: !!btn,
      firstName: first ? (first.querySelector("h3")?.textContent || "").trim() : "",
      hasInstructionField: document.body.innerText.includes("设为每日委托"),
    };
  });
  log("模板广场 =", JSON.stringify(tpl));
  if (!tpl.count) problems.push("模板广场没加载出卡片（后端 /api/templates 可能没起）");
  if (!tpl.hasDelegateBtn) problems.push("模板卡上没有「设为每日委托」按钮");
  await page.screenshot({ path: path.join(OUT, "templates.png") });

  if (tpl.hasDelegateBtn) {
    const clicked = await page.evaluate(() => {
      const first = document.querySelector("article");
      const btn = [...first.querySelectorAll("button")].find((b) =>
        (b.textContent || "").includes("设为每日委托")
      );
      btn.click();
      return true;
    });
    await sleep(2500);
    const after = await page.evaluate(() => {
      // 不猜 DOM 结构：表单里的 textarea / 文本框全读出来，看有没有被填
      const filled = [...document.querySelectorAll("textarea, input")]
        .map((el) => el.value || "")
        .filter((v) => v.trim().length > 0);
      return {
        path: location.pathname + location.search,
        filled,
        // 会话存储应该已经被消费掉
        preset: sessionStorage.getItem("baomi:delegate-preset"),
      };
    });
    log(
      "点「设为每日委托」后 =",
      JSON.stringify({
        path: after.path,
        filled: after.filled.map((v) => (v.length > 34 ? v.slice(0, 34) + "…" : v)),
        preset: after.preset,
      })
    );
    if (!after.path.startsWith("/delegate")) problems.push(`没跳到委托页：${after.path}`);
    if (after.filled.length < 2) {
      problems.push(`委托表单没被预填（只有 ${after.filled.length} 个非空字段，期望名称 + 指令）`);
    }
    if (after.preset) problems.push("sessionStorage 里的预填没被消费掉（下次新建会串味）");
    if (clicked) await page.screenshot({ path: path.join(OUT, "delegate-prefilled.png") });
  }

  // ---------- 汇总 ----------
  console.log("");
  if (realErrors.length) {
    console.log(`[tiers] 页面真错误 ${realErrors.length} 条：`);
    realErrors.slice(0, 8).forEach((e) => console.log("         !! " + String(e).slice(0, 160)));
  } else {
    console.log("[tiers] 页面真错误 0 条 ✓");
  }
  if (problems.length) {
    console.log(`[tiers] 不通过 ${problems.length} 条：`);
    problems.forEach((p) => console.log("         !! " + p));
    await browser.close();
    process.exit(1);
  }
  console.log(`[tiers] 全部通过 ✓  截图 → ${OUT}`);
  await browser.close();
})().catch((e) => {
  console.error("[tiers] 脚本失败：", e.message);
  process.exit(1);
});
