// 本地 UI 校验：资料库「处理方式」白话档位 + 对话区检索设置。
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/shot_kb_local.js
// 只在本机 dev 环境跑，会注册一个临时账号（uidemoXXXX），不影响线上。
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3000";
const OUT = path.resolve(__dirname, "..", "docs", "screenshots");
const SAMPLE = path.resolve(__dirname, "..", "data", "sample.txt");
const USER = "uidemo" + Math.random().toString(36).slice(2, 7);
const PASS = "demo123456";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[shot]", ...a);

async function clickByText(page, text, tag = "button") {
  return page.evaluate(
    (t, g) => {
      const el = [...document.querySelectorAll(g)].find(
        (x) => (x.textContent || "").trim().includes(t)
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
    defaultViewport: { width: 1440, height: 960, deviceScaleFactor: 2 },
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push("PAGEERROR " + e.message));
  // 关键校验：上传/重建实际带上的切片参数
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("/api/kb/") && (r.method() === "POST" || r.method() === "PATCH")) {
      const q = u.includes("?") ? u.slice(u.indexOf("?")) : "";
      log("  → 请求", r.method(), u.split("/api")[1].split("?")[0], q);
    }
    // 上传成功后应自动带一条绑定请求 PATCH /api/sessions/{id} {kb_id}
    if (u.includes("/api/sessions/") && r.method() === "PATCH") {
      log("  → 绑定请求 PATCH /api/sessions/…", (r.postData() || "").slice(0, 120));
    }
  });

  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await sleep(1000);

  // 1) 登录门：切到注册并建临时账号
  const onGate = await page.evaluate(() => document.body.innerText.includes("注册"));
  if (onGate) {
    await clickByText(page, "注册");
    await sleep(400);
    const textInput = await page.$('input:not([type="password"]):not([type="file"])');
    await textInput.type(USER);
    const pwInput = await page.$('input[type="password"]');
    await pwInput.type(PASS);
    await page.click('button[type="submit"]');
    log("注册临时账号", USER);
  }

  // 2) 等工作台首屏（资料库面板标题）
  await page.waitForFunction(() => document.body.innerText.includes("资料库"), {
    timeout: 40000,
  });
  await sleep(1200);
  await page.screenshot({ path: path.join(OUT, "ui-1-workbench.png") });
  log("截图 1：工作台");

  // 3) 展开「我的文件」，露出处理方式档位
  await clickByText(page, "我的文件");
  await page.waitForFunction(() => document.body.innerText.includes("这份资料想怎么读"), {
    timeout: 15000,
  });
  await sleep(500);
  const panel = await page.$('aside[aria-label="资料库"]');
  if (panel) await panel.screenshot({ path: path.join(OUT, "ui-2-read-mode.png") });
  log("截图 2：处理方式档位");

  // 4) 切到「完整」档并展开高级设置：验证数字跟着档位走（应为 1000/100）
  await clickByText(page, "完整");
  await sleep(300);
  await clickByText(page, "高级设置");
  await sleep(500);
  if (panel) await panel.screenshot({ path: path.join(OUT, "ui-3-advanced.png") });
  const nums = await page.evaluate(() => {
    const aside = document.querySelector('aside[aria-label="资料库"]');
    return [...(aside ? aside.querySelectorAll('input[type="number"]') : [])].map((i) => i.value);
  });
  log("截图 3：高级设置。档位数字 =", JSON.stringify(nums), "（应为 1000/100）");

  // 5) 建一个库，验证列表标签、下一步引导、以及请求里真正带上的参数
  const nameInput = await page.$('input[aria-label="资料库名称"]');
  await nameInput.type("UI 校验库");
  // 必须限定在资料库面板里找：对话区也有一个附件的 file input
  const fileInput = await page.$('aside[aria-label="资料库"] input[type="file"]');
  if (fileInput && fs.existsSync(SAMPLE)) {
    await fileInput.uploadFile(SAMPLE);
    log("已选择文件，等待建库…");
    await page
      .waitForFunction(() => document.querySelectorAll(".kb-row").length > 0, {
        timeout: 90000,
      })
      .catch(() => log("  !! 等待建库超时"));
    await sleep(2500);
    if (panel) await panel.screenshot({ path: path.join(OUT, "ui-4-built.png") });
    const built = await page.evaluate(() => {
      const r = document.querySelector(".kb-row");
      if (!r) return null;
      const aside = document.querySelector('aside[aria-label="资料库"]');
      const tip = (aside ? aside.innerText : "").match(/「[^」]+」已就绪[^\n]*/);
      return {
        行文本: (r.querySelector(".kb-meta") || r).innerText.replace(/\s+/g, " "),
        已绑定: r.classList.contains("bound"),
        ariaPressed: (r.querySelector("button[aria-pressed]") || {}).getAttribute?.("aria-pressed") ?? null,
        引导: tip ? tip[0] : null,
      };
    });
    log("截图 4：建库后 →", JSON.stringify(built, null, 0), "（已绑定 true = 自动绑定生效）");
    // 补 token 前这里是空的（类名静默失效），现在应有实际色值
    const tokenColor = await page.evaluate(() => {
      const s = document.querySelector("aside[aria-label='资料库'] .text-accent-ink");
      return s ? getComputedStyle(s).color : "（找不到该元素）";
    });
    log("  text-accent-ink 计算色 =", tokenColor, "｜期望 rgb(135, 91, 16) = #875B10");
  } else {
    log("跳过上传：找不到 file input 或样例文件");
  }

  // 6) 对话区检索设置（齿轮）
  await page.click("button.gear");
  await sleep(600);
  await page.screenshot({ path: path.join(OUT, "ui-5-topk.png") });
  log("截图 5：检索设置面板");

  log("console 错误数 =", errors.length);
  errors.slice(0, 8).forEach((e) => log("  ERR:", e.slice(0, 200)));

  // 7) 浅色模式再截一遍：设计基线是暖米黄纸面，深色只是其中一档
  await page.evaluate(() => localStorage.setItem("theme", "light"));
  await page.reload({ waitUntil: "networkidle2" });
  await sleep(2500);
  await page.screenshot({ path: path.join(OUT, "ui-6-light.png") });
  await clickByText(page, "我的文件");
  await sleep(700);
  await clickByText(page, "管理文件");
  await sleep(700);
  const panel2 = await page.$('aside[aria-label="资料库"]');
  if (panel2) await panel2.screenshot({ path: path.join(OUT, "ui-7-light-panel.png") });
  log("截图 6/7：浅色模式 + 库内文件与「重新整理」");

  // 浅底上金色必须走可读版 #875B10（--accent 只有 2.6:1），这条是设计硬约束
  const tokenLight = await page.evaluate(() => {
    const s = document.querySelector(".text-accent-ink");
    return s ? getComputedStyle(s).color : "（找不到）";
  });
  log("浅色下 text-accent-ink 计算色 =", tokenLight, "｜期望 rgb(135, 91, 16)");

  await browser.close();
})().catch((e) => {
  console.error("[shot] 失败:", e.message);
  process.exit(1);
});
