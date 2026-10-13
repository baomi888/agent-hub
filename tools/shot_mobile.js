// 移动端设计稿渲染校验：逐个手机框截图 + 断言图标注入与触控目标。
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/shot_mobile.js
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const RAW = path.resolve(__dirname, "..", "prototypes", "知穗_新页面设计稿_移动端.html");
const URL = "file:///" + encodeURI(RAW.replace(/\\/g, "/"));
const OUT = path.resolve(__dirname, "..", "docs", "实机截图", "design-mobile-2026-10-13");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[mobile]", ...a);

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-proxy-server", "--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: { width: 1600, height: 1100, deviceScaleFactor: 2 },
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push("PAGEERROR " + e.message));

  await page.goto(URL, { waitUntil: "load", timeout: 60000 });
  await sleep(700);

  // 1) 图标注入断言
  const ic = await page.evaluate(() => ({
    未替换占位: document.querySelectorAll("span[data-i]").length,
    已注入svg: document.querySelectorAll("svg.ic").length,
    手机框: document.querySelectorAll(".phone").length,
  }));
  log("图标：未替换 =", ic.未替换占位, "｜已注入 =", ic.已注入svg, "｜手机框 =", ic.手机框);

  // 2) 每个手机框内部自检：Tab 项数 / 是否超高（内容该滚动而不是撑破）
  const inner = await page.evaluate(() =>
    [...document.querySelectorAll(".phone")].map((p, i) => {
      const tabs = p.querySelectorAll(".tabbar button").length;
      const bars = p.querySelectorAll("svg").length;
      const body = p.querySelector(".abody");
      return {
        序号: i + 1,
        标题: (p.querySelector(".abar h2") || {}).textContent || "?",
        Tab项: tabs,
        图标数: bars,
        正文可滚动: body ? body.scrollHeight > body.clientHeight + 2 : null,
      };
    })
  );
  inner.forEach((r) => log("  屏", r.序号, JSON.stringify(r)));

  // 2b) 底部 Tab 选中态：图标色应取 --accent-ink（浅色下 rgb(135,91,16)），未选中为 --text-3
  const tabColor = await page.evaluate(() => {
    const col = (sel) => {
      const el = document.querySelector(".tabbar " + sel + " svg");
      return el ? getComputedStyle(el).color : "(无)";
    };
    const wt = (sel) => {
      const el = document.querySelector(".tabbar " + sel);
      return el ? getComputedStyle(el).fontWeight : "(无)";
    };
    return {
      选中图标: col('button[aria-current="page"]'),
      未选中图标: col("button:not([aria-current='page'])"),
      选中字重: wt('button[aria-current="page"]'),
      未选中字重: wt("button:not([aria-current='page'])"),
    };
  });
  log("Tab 选中态 →", JSON.stringify(tabColor));

  // 3) 触控目标统计（只统计"可直接点"的元素，段控件内部按钮单列）
  const tap = await page.evaluate(() => {
    const small = [];
    document.querySelectorAll(".phone button, .phone .switch, .phone label.rowline").forEach((el) => {
      const r = el.getBoundingClientRect();
      const cls = el.className || "";
      // 段控件内按钮设计值 40、btn-sm 设计值 36 —— 属于有意例外，单独归类
      const isSeg = el.closest(".seg") !== null;
      const isSm = cls.includes("btn-sm");
      const limit = isSeg ? 40 : isSm ? 36 : 44;
      if (r.height < limit - 0.5) {
        small.push({
          文本: (el.textContent || cls).trim().slice(0, 18),
          高: Math.round(r.height),
          应达: limit,
        });
      }
    });
    return small;
  });
  log("触控目标不足的：", tap.length);
  tap.slice(0, 10).forEach((t) => log("  !!", JSON.stringify(t)));

  // 4) 逐屏截图
  const phones = await page.$$(".phone");
  for (let i = 0; i < phones.length; i++) {
    await phones[i].screenshot({ path: path.join(OUT, `phone-${i + 1}.png`) });
  }
  log("已截", phones.length, "张手机框 →", "docs/实机截图/design-mobile-2026-10-13/");

  // 5) 真机宽度整页（390 视口，检查并排布局塌成单列后是否还可读）
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  await sleep(500);
  await page.screenshot({ path: path.join(OUT, "viewport-390-top.png") });
  log("已截 390 视口首屏");

  // 6) 深色
  await page.setViewport({ width: 1600, height: 1100, deviceScaleFactor: 2 });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await sleep(500);
  const darkShot = await page.$(".phones .phone-wrap:nth-child(3)");
  if (darkShot) await darkShot.screenshot({ path: path.join(OUT, "phone-3-dark.png") });
  log("已截深色");

  log("console 错误数 =", errors.length);
  errors.slice(0, 6).forEach((e) => log("  ERR:", e.slice(0, 180)));
  await browser.close();
})().catch((e) => {
  console.error("[mobile] 失败:", e.message);
  process.exit(1);
});
