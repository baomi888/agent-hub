// Toast 无障碍 + 委托预览浮层 Esc 的实机校验。
//
// 背景（10-14 第四轮）：
//  ① Toast 是「操作结果」的唯一反馈通道，但两个渲染点都是裸 <div>，没有
//     role/aria-live ⇒ 屏幕阅读器用户点完按钮得不到任何回应。
//  ② /delegate 的预览浮层是全项目唯一不响应 Esc 的弹层（其余 7 处都响应），
//     且关闭后不归还焦点。
// 本地后端没有 /api/delegate ⇒ 页面走演示模式，而 demoGuard 分支**照常渲染浮层**，
// 所以这条链路能在本地端到端验证。
//
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/check_toast_a11y.js
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3000";
const USER = "ademo" + Math.random().toString(36).slice(2, 7);
const PASS = "demo123456";
const DIALOG = '[role="dialog"][aria-label="预览生成内容"]';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const fails = [];
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(
    `${ok ? "  [OK]" : "  [!!]"} ${name} → ${JSON.stringify(actual)}` +
      (ok ? "" : `  （期望 ${JSON.stringify(expected)}）`)
  );
  if (!ok) fails.push(name);
}

async function ensureLogin(page) {
  await page.goto(`${BASE}/` , { waitUntil: "networkidle2" });
  await sleep(900);
  if (!(await page.$('input[type="password"]'))) return;
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find(
      (x) => x.textContent.trim() === "注册"
    );
    if (b) b.click();
  });
  await sleep(300);
  const inputs = await page.$$("input");
  await inputs[0].type(USER);
  await inputs[1].type(PASS);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle2", timeout: 20000 }).catch(() => {}),
    page.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find((x) =>
        x.textContent.includes("注册并登录")
      );
      if (b) b.click();
    }),
  ]);
  await sleep(1200);
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-proxy-server", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

  try {
    await ensureLogin(page);

    // ---- 委托页：点「预览」→ 出 toast + 出浮层 ----
    await page.goto(`${BASE}/delegate`, { waitUntil: "networkidle2" });
    await sleep(1500);
    const clicked = await page.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find(
        (x) => x.textContent.trim() === "预览"
      );
      if (!b) return false;
      // 必须先 focus 再 click：程序化 click() 不会转移焦点，
      // 而真实鼠标点击会让按钮获得焦点 —— 不补这一下，测出来的
      // activeElement 是 body，会把「焦点归还」误判成失败。
      b.focus();
      b.click();
      return true;
    });
    check("找到并点击「预览」按钮", clicked, true);
    await sleep(700);

    // ① Toast 容器语义
    const toastSem = await page.evaluate(() => {
      const c = document.querySelector(".toast-container");
      if (!c) return null;
      return {
        role: c.getAttribute("role"),
        live: c.getAttribute("aria-live"),
        count: c.querySelectorAll(".toast").length,
      };
    });
    check("委托页 toast 容器 role", toastSem && toastSem.role, "status");
    check("委托页 toast 容器 aria-live", toastSem && toastSem.live, "polite");
    check("委托页确实弹出了 toast", toastSem && toastSem.count >= 1, true);

    // ② 浮层出现 + Esc 关闭 + 焦点归还
    const opened = await page.$(DIALOG);
    check("预览浮层已打开", !!opened, true);

    await page.keyboard.press("Escape");
    await sleep(500);
    check("按 Esc 后浮层已关闭", !(await page.$(DIALOG)), true);
    const focused = await page.evaluate(() =>
      document.activeElement ? document.activeElement.textContent.trim() : null
    );
    check("焦点已归还给触发按钮", focused, "预览");

    // ---- 工作台：另一个 toast 渲染点也要带同样语义 ----
    await page.goto(`${BASE}/`, { waitUntil: "networkidle2" });
    await sleep(1200);
    const homeSem = await page.evaluate(() => {
      const c = document.querySelector(".toast-container");
      return c ? { role: c.getAttribute("role"), live: c.getAttribute("aria-live") } : null;
    });
    check("工作台 toast 容器 role", homeSem && homeSem.role, "status");
    check("工作台 toast 容器 aria-live", homeSem && homeSem.live, "polite");
  } finally {
    await browser.close();
  }

  console.log(
    fails.length
      ? `\n✗ 未通过 ${fails.length} 项：${fails.join(" / ")}`
      : "\n✓ Toast 语义与浮层 Esc / 焦点归还全部通过"
  );
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => {
  console.error("脚本异常：", e.message);
  process.exit(2);
});
