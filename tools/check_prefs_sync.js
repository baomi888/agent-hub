// 本地四步手验（自动化版）：设置页改「8 段」→ 回工作台 → 刷新 → 回设置页，看是否还是 8 段。
//
// 背景：`baomi.prefs` 曾有两套读写实现。usePrefs（page.tsx）读端只回 3 个字段、
// 写端却写全量 6 个 ⇒ 用户刚在设置页设好的 topK / chunkSize / chunkOverlap，
// 一回工作台就被默认值静默重置（用户设置永久丢失且不易察觉）。
// 本脚本就是 docs/前后端可更新点_2026-10-14.md 第四节要求的四步手验的自动化版本。
//
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/check_prefs_sync.js
// 前置：本地后端 127.0.0.1:8000 + 前端 localhost:3000 都在跑（只打本机，不动线上）。
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3000";
const USER = "pdemo" + Math.random().toString(36).slice(2, 7);
const PASS = "demo123456";

const TOPK_LABEL = '[aria-label="每次回答参考几段资料"]';
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

const readPrefs = (page) =>
  page.evaluate(() => {
    try {
      return JSON.parse(localStorage.getItem("baomi.prefs") || "{}");
    } catch {
      return {};
    }
  });

/** 设置页 Seg 里选中的那一项文本 */
const selectedTopK = (page) =>
  page.evaluate((sel) => {
    const g = document.querySelector(sel);
    if (!g) return null;
    const on = [...g.querySelectorAll("button")].find(
      (b) => b.getAttribute("aria-pressed") === "true"
    );
    return on ? on.textContent.trim() : null;
  }, TOPK_LABEL);

async function ensureLogin(page) {
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle2" });
  await sleep(900);
  if (!(await page.$('input[type="password"]'))) {
    console.log("  已有登录态，跳过注册");
    return;
  }
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
  console.log(`  注册并登录：${USER}`);
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

    // 步骤 1：清空偏好，回到干净起点，再在设置页选「8 段」
    await page.evaluate(() => localStorage.removeItem("baomi.prefs"));
    await page.reload({ waitUntil: "networkidle2" });
    await sleep(900);
    const clicked = await page.evaluate((sel) => {
      const g = document.querySelector(sel);
      if (!g) return false;
      const b = [...g.querySelectorAll("button")].find((x) => x.textContent.includes("8"));
      if (!b) return false;
      b.click();
      return true;
    }, TOPK_LABEL);
    check("设置页找到并点击「8 段」", clicked, true);
    await sleep(400);
    check("步骤1 设置页写盘后 topK", (await readPrefs(page)).topK, 8);

    // 步骤 2：回工作台（usePrefs 挂载 → 读回 → 写盘）—— 这一步过去会把 8 冲成 3
    await page.goto(`${BASE}/`, { waitUntil: "networkidle2" });
    await sleep(1800);
    check("步骤2 回工作台后 topK", (await readPrefs(page)).topK, 8);

    // 步骤 3：刷新工作台再读一次
    await page.reload({ waitUntil: "networkidle2" });
    await sleep(1800);
    check("步骤3 刷新工作台后 topK", (await readPrefs(page)).topK, 8);

    // 步骤 4：回设置页，看选中项是不是还是 8
    await page.goto(`${BASE}/settings`, { waitUntil: "networkidle2" });
    await sleep(1200);
    const sel = await selectedTopK(page);
    check("步骤4 回设置页选中项仍是 8 段", !!sel && sel.includes("8"), true);

    // 顺带：chunkSize / chunkOverlap 也不能被冲掉（同一类不对称的另两个字段）
    const final = await readPrefs(page);
    check("chunkSize 仍在", typeof final.chunkSize, "number");
    check("chunkOverlap 仍在", typeof final.chunkOverlap, "number");
  } finally {
    await browser.close();
  }

  console.log(
    fails.length
      ? `\n✗ 未通过 ${fails.length} 项：${fails.join(" / ")}`
      : "\n✓ 四步手验全部通过：设置页改的检索条数不再被工作台冲掉"
  );
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => {
  console.error("脚本异常：", e.message);
  process.exit(2);
});
