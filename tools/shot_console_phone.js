// 四屏落地页的**手机视口**实机校验：能不能滚 + 侧栏四入口有没有文字 + 工具集入口搬到哪了。
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/shot_console_phone.js
//
// 与 shot_mobile.js 的区别：那个验的是 prototypes/ 下的设计稿 HTML，本脚本验的是
// 真正跑起来的 Next 页面（localhost:3000）。
//
// 背景（10-14 用户反馈）：
//   ① 手机上打开四屏「不能上下滑动，被固定死了」——base.css 把 html/body 设成
//      height:100% + overflow:hidden，四屏没有自带滚动容器，内容超出就被裁。
//   ② 侧栏四个入口是纯图标方块，用户不知道是干什么的 —— 改成图标 + 文字。
//   ③ 校园工具集从侧栏移到对话栏。
//
// 只打本机 dev，注册临时账号（mdemoXXXX），不动线上数据。
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3000";
const OUT = path.resolve(__dirname, "..", "docs", "实机截图", "mobile-2026-10-14");
const USER = "mdemo" + Math.random().toString(36).slice(2, 7);
const PASS = "demo123456";

// iPhone 12 逻辑尺寸。deviceScaleFactor 用 1 —— 2x 全页图一张 2MB+，
// 这轮只用来复盘布局，不需要印刷级清晰度。
const PHONE = { width: 390, height: 844, deviceScaleFactor: 1, isMobile: true, hasTouch: true };
const DESKTOP = { width: 1440, height: 900, deviceScaleFactor: 1 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[phone]", ...a);

const PAGES = [
  { key: "delegate", path: "/delegate", must: ["每日委托"] },
  { key: "report", path: "/report", must: ["每日总结"] },
  { key: "checkin", path: "/checkin", must: ["打卡"] },
  { key: "settings", path: "/settings", must: ["设置"] },
];

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

/** 滚动实测：把容器滚到底，看 scrollTop 有没有真的动 */
async function probeScroll(page) {
  return page.evaluate(() => {
    const el = document.querySelector(".console-scroll");
    if (!el) return { found: false };
    const before = el.scrollTop;
    el.scrollTop = 100000;
    const after = el.scrollTop;
    el.scrollTop = 0;
    return {
      found: true,
      overflowY: getComputedStyle(el).overflowY,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      before,
      after,
      // 内容没超出视口时滚不动是正常的，不算失败
      contentOverflows: el.scrollHeight > el.clientHeight + 1,
      scrolled: after > before,
    };
  });
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
    // 端点没上线的 404、未登录的 401 都是预期内
    if (/401|404|Not Found|Unauthorized|ERR_CONNECTION_RESET/i.test(t)) return;
    realErrors.push(t);
  });
  page.on("pageerror", (e) => realErrors.push("PAGEERROR " + e.message));

  // ---------- 1) 桌面视口：先注册，再验侧栏与对话栏 ----------
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
  await sleep(1200);
  await hideDevBadge(page);

  // 1a) 侧栏四入口：数量 / 是否有文字 / 热区
  const entries = await page.evaluate(() =>
    [...document.querySelectorAll("aside .side-entry")].map((el) => {
      const r = el.getBoundingClientRect();
      return {
        text: (el.textContent || "").trim(),
        title: el.getAttribute("title") || "",
        w: Math.round(r.width),
        h: Math.round(r.height),
      };
    })
  );
  log("侧栏四入口 =", JSON.stringify(entries));
  if (entries.length !== 4) problems.push(`侧栏入口应有 4 个，实得 ${entries.length}`);
  for (const e of entries) {
    if (!e.text) problems.push(`侧栏入口「${e.title || "?"}」没有文字`);
    if (e.h < 44) problems.push(`侧栏入口「${e.text}」热区高 ${e.h}px < 44px`);
  }

  // 1b) 侧栏不该再有「校园工具集」，对话栏该有
  const moved = await page.evaluate(() => {
    const aside = document.querySelector("aside");
    const asideHasTools = aside ? (aside.innerText || "").includes("校园工具集") : false;
    const link = document.querySelector("a.tools-link");
    return {
      asideHasTools,
      chatHasTools: !!link,
      chatText: link ? (link.textContent || "").trim() : "",
      chatHref: link ? link.getAttribute("href") : "",
    };
  });
  log("工具集入口迁移 =", JSON.stringify(moved));
  if (moved.asideHasTools) problems.push("侧栏里仍有「校园工具集」");
  if (!moved.chatHasTools) problems.push("对话栏里找不到 .tools-link");
  if (moved.chatText !== "校园工具集") problems.push(`对话栏入口文案 = ${moved.chatText}`);
  if (moved.chatHref !== "/tools.html") problems.push(`对话栏入口 href = ${moved.chatHref}`);

  await page.screenshot({ path: path.join(OUT, "desktop-home.png") });

  // ---------- 2) 手机视口 ----------
  await page.setViewport(PHONE);
  await sleep(600);

  const scrollRows = [];
  for (const p of PAGES) {
    await page.goto(BASE + p.path, { waitUntil: "networkidle2", timeout: 60000 });
    await sleep(1600);
    await hideDevBadge(page);

    const st = await page.evaluate(() => {
      const txt = document.body.innerText;
      const vw = window.innerWidth;
      // 横向溢出必须逐元素量右边界：.console-scroll 是 overflow-x:hidden，
      // 溢出会被静默裁掉，documentElement.scrollWidth 根本测不出来（10-14 踩过：
      // 设置页「修改密码」按钮被裁一半，断言却全绿）。
      const over = [];
      for (const el of document.querySelectorAll(".console-scroll *")) {
        const b = el.getBoundingClientRect();
        if (b.width === 0) continue;
        if (b.right > vw + 1) {
          over.push(`${el.tagName}.${String(el.className || "").slice(0, 44)} right=${Math.round(b.right)}`);
        }
      }
      return {
        text: txt,
        emoji: (txt.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu) || []).length,
        over: over.slice(0, 6),
        overCount: over.length,
      };
    });
    const missing = p.must.filter((m) => !st.text.includes(m));
    if (missing.length) problems.push(`${p.path} 缺文案：${missing.join("/")}`);
    if (st.emoji) problems.push(`${p.path} 出现 emoji ${st.emoji} 处`);
    if (st.overCount) {
      problems.push(`${p.path} 横向溢出 ${st.overCount} 处：${st.over.join(" ｜ ")}`);
    }

    const sc = await probeScroll(page);
    if (!sc.found) {
      problems.push(`${p.path} 找不到 .console-scroll（四屏没有滚动容器 ⇒ 手机上滚不动）`);
    } else if (sc.contentOverflows && !sc.scrolled) {
      problems.push(`${p.path} 内容 ${sc.scrollHeight}px 超出视口 ${sc.clientHeight}px，但滚不动`);
    }
    scrollRows.push({
      page: p.key,
      overflowY: sc.overflowY,
      scrollHeight: sc.scrollHeight,
      clientHeight: sc.clientHeight,
      scrolled: sc.scrolled,
    });

    await page.screenshot({ path: path.join(OUT, `${p.key}.png`) });
    // 滚到底再拍一张：证明滚完能看到页脚，而不是只有首屏
    if (sc.contentOverflows) {
      await page.evaluate(() => {
        const el = document.querySelector(".console-scroll");
        if (el) el.scrollTop = el.scrollHeight;
      });
      await sleep(500);
      await page.screenshot({ path: path.join(OUT, `${p.key}-bottom.png`) });
    }
  }

  log("四屏滚动实测：");
  for (const r of scrollRows) {
    console.log(
      `         ${r.page.padEnd(9)} overflow-y=${String(r.overflowY).padEnd(6)} ` +
        `内容 ${r.scrollHeight}px / 视口 ${r.clientHeight}px  ` +
        `滚动生效=${r.scrolled ? "是" : "（内容未超出）"}`
    );
  }

  // 2b) 打开抽屉看侧栏（手机上侧栏默认收起）
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await sleep(1500);
  const opened = await page.evaluate(() => {
    const btn = document.querySelector('button[aria-label="打开会话列表"]');
    if (!btn) return false;
    btn.click();
    return true;
  });
  await sleep(900);
  await hideDevBadge(page);
  log("移动端抽屉打开 =", opened);
  const drawer = await page.evaluate(() => {
    const aside = document.querySelector(".side-left");
    if (!aside) return { found: false };
    const r = aside.getBoundingClientRect();
    return {
      found: true,
      open: aside.classList.contains("open"),
      w: Math.round(r.width),
      entries: [...aside.querySelectorAll(".side-entry")].map((el) => ({
        text: (el.textContent || "").trim(),
        h: Math.round(el.getBoundingClientRect().height),
      })),
      hasTools: (aside.innerText || "").includes("校园工具集"),
    };
  });
  log("抽屉内容 =", JSON.stringify(drawer));
  if (!drawer.found) problems.push("移动端找不到侧栏抽屉");
  else {
    if (!drawer.open) problems.push("移动端抽屉没打开");
    if (drawer.entries.length !== 4) problems.push(`抽屉里入口 ${drawer.entries.length} 个`);
    if (drawer.hasTools) problems.push("抽屉里仍有「校园工具集」");
  }
  await page.screenshot({ path: path.join(OUT, "drawer.png") });

  // 抽屉里点一个入口，验证 2×2 能点、跳转正常
  const jumped = await page.evaluate(() => {
    const a = document.querySelector(".side-left a.side-entry");
    if (!a) return null;
    const href = a.getAttribute("href");
    a.click();
    return href;
  });
  await sleep(2000);
  const landed = await page.evaluate(() => location.pathname);
  log("抽屉点入口 →", jumped, "｜落点", landed);
  if (jumped && landed !== jumped) problems.push(`抽屉入口跳转失败：期望 ${jumped} 实得 ${landed}`);
  if (jumped) {
    await hideDevBadge(page);
    await page.screenshot({ path: path.join(OUT, "drawer-jump.png") });
  }

  // ---------- 汇总 ----------
  console.log("");
  if (realErrors.length) {
    console.log(`[phone] 页面真错误 ${realErrors.length} 条：`);
    realErrors.slice(0, 8).forEach((e) => console.log("         !! " + String(e).slice(0, 160)));
  } else {
    console.log("[phone] 页面真错误 0 条 ✓");
  }
  if (problems.length) {
    console.log(`[phone] 不通过 ${problems.length} 条：`);
    problems.forEach((p) => console.log("         !! " + p));
    await browser.close();
    process.exit(1);
  }
  console.log(`[phone] 全部通过 ✓  截图 → ${OUT}`);
  await browser.close();
})().catch((e) => {
  console.error("[phone] 脚本失败：", e.message);
  process.exit(1);
});
