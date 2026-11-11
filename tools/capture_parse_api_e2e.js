/**
 * Phase B 的前端验收：真实接口链路 + 三种失败映射。
 *
 * 跑法（要求 dev server 跑在 3100，且**没有**开 NEXT_PUBLIC_CAPTURE_OFFLINE）：
 *   node tools/capture_parse_api_e2e.js
 *
 * 和 `capture_shell_acceptance.js` 的分工：
 *   那个验 11 个状态的样子（离线 fixture，可复现）；
 *   这个验"接口这一层接得对不对"——真调一次、429 怎么说、502 怎么说、断网怎么说。
 *
 * 为什么必须两条都留：只跑离线 fixture 的话，把 fixture 换成真接口这件事
 * 一行都没被验过；只跑真接口的话，11 个状态里的"提交中/部分失败"根本复现不出来。
 */
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = "http://localhost:3100";
const USER = { username: "cap_probe", password: "Baomi@2026test" };
const OUT = path.resolve(__dirname, "shots");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PASS = [];
const FAIL = [];
function check(name, cond, extra) {
  (cond ? PASS : FAIL).push(name);
  console.log(`  ${cond ? "PASS" : "FAIL"} ${name}${!cond && extra !== undefined ? `  <- ${JSON.stringify(extra)}` : ""}`);
}

const CHROME = "C:/Users/Fengjinghao/AppData/Local/Google/Chrome/Application/chrome.exe";

/** 等抽屉里出现某个状态（data-status 由组件写在 .rem-cap-drawer 上）。 */
async function waitStatus(p, want, timeout = 30000) {
  await p.waitForFunction(
    (w) => document.querySelector(".rem-cap-drawer")?.getAttribute("data-status") === w,
    { timeout },
    want
  );
}

async function openDrawer(p) {
  for (let i = 0; i < 3; i += 1) {
    try {
      await p.waitForSelector(".rem-cap-strip", { timeout: 10000 });
      await p.click(".rem-cap-strip");
      await p.waitForSelector(".rem-cap-drawer", { timeout: 6000 });
      await sleep(400);
      return;
    } catch {
      await sleep(800);
    }
  }
  throw new Error("抽屉打不开");
}

async function typeAndSend(p, text) {
  await p.click(".rem-cap-input");
  await p.type(".rem-cap-input", text, { delay: 3 });
  await sleep(120);
  await p.keyboard.press("Enter");
}

async function reset(p) {
  // 用 URL 参数钉死浅色：layout 里 URL 参数优先于 localStorage，
  // 不钉的话 headless Chrome 报 prefers-color-scheme: dark，主按钮会拍成金色
  await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "networkidle2", timeout: 60000 });
  await p.evaluate(() => {
    if (document.getElementById("cap-hide-ann")) return;
    const st = document.createElement("style");
    st.id = "cap-hide-ann";
    st.textContent = ".ann-page{display:none !important} nextjs-portal{display:none !important}";
    document.head.appendChild(st);
  });
  await sleep(300);
}

async function snap(p) {
  return p.evaluate(() => {
    const q = (s) => document.querySelector(s);
    return {
      status: q(".rem-cap-drawer")?.getAttribute("data-status") ?? null,
      title: q(".rem-cap-title")?.textContent?.trim() ?? null,
      why: q(".rem-cap-fail-why")?.textContent?.trim() ?? null,
      note: q(".rem-cap-fail-note")?.textContent?.trim() ?? null,
      rows: document.querySelectorAll(".rem-cap-axisrow").length,
      input: q(".rem-cap-input")?.value ?? null,
      // 结果态的「你说的」是回看区（.rem-cap-quote，带编号），
      // 整理中/失败态才是 .rem-cap-srcquote。两处都要认。
      quote:
        (q(".rem-cap-quote") || q(".rem-cap-srcquote p"))?.textContent?.trim() ?? null,
      retry: q(".rem-cap-fail-retry")?.textContent?.trim() ?? null,
      proc: !!q(".rem-cap-proc"),
      sub: q(".rem-cap-sub")?.textContent?.trim() ?? null,
    };
  });
}

(async () => {
  if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--font-render-hinting=none"],
  });
  const ctx = await browser.createBrowserContext();
  const p = await ctx.newPage();
  await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  await p.evaluateOnNewDocument(() => {
    try { localStorage.setItem("theme", "light"); } catch {}
  });
  const errors = [];
  p.on("pageerror", (e) => errors.push(e.message));

  await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(500);
  const uid = await p.evaluate(async (u) => {
    await fetch("/api/auth/register/", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include",
      body: JSON.stringify(u),
    });
    await fetch("/api/auth/login/", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include",
      body: JSON.stringify(u),
    });
    const me = await fetch("/api/auth/me", { credentials: "include" });
    const j = await me.json().catch(() => ({}));
    return j?.user?.id ?? j?.id ?? null;
  }, USER);
  if (!uid) throw new Error("登录失败");
  await p.evaluate((id) => {
    try { localStorage.setItem(`baomi.guide.v1.${id}`, "1"); } catch {}
  }, uid);

  // ---------- A 真实链路：不拦截，真的调后端 ----------
  console.log("\n[A] 真接口（这条要花 2–5 秒，真的在调模型）");
  await reset(p);
  await openDrawer(p);
  const t0 = Date.now();
  await typeAndSend(p, "明天交高数作业，周五晚上七点社团开会");
  // 先确认中间态是真的（真实接口慢，这一帧反而容易抓到）
  const sawParsing = await p
    .waitForFunction(() => !!document.querySelector(".rem-cap-proc"), { timeout: 3000 })
    .then(() => true)
    .catch(() => false);
  await waitStatus(p, "ready", 45000);
  const ms = Date.now() - t0;
  const a = await snap(p);
  console.log(`  真实链路耗时 ${ms}ms`);
  check("A1 真实接口在 45s 内出结果", ms < 45000, ms);
  check("A2 中间态「正在整理…」真的出现过", sawParsing, sawParsing);
  check("A3 结果态有条目", a.rows > 0, a.rows);
  check("A4 标题写条数", /确认这 \d+ 条/.test(a.title || ""), a.title);
  // 结果态没有输入框（底栏换成了「加入 N 条」），原话在这张引用卡上。
  // 第一版断言去查 .rem-cap-input，结果拿到 null —— 是断言写错，不是功能坏。
  check("A5 原话还在「你说的」卡上", /交高数作业/.test(a.quote || ""), a.quote);
  await p.screenshot({ path: path.join(OUT, "api_01_live_result.png") });

  // ---------- B 429：人多 ----------
  console.log("\n[B] 429 → 说「人多」，不说「说错了」");
  await reset(p);
  await p.setRequestInterception(true);
  const handler = (req) => {
    if (!req.url().includes("/api/capture/parse")) return req.continue();
    return req.respond({
      status: 429,
      contentType: "application/json",
      headers: { "Retry-After": "15" },
      // detail 里**不带**时长：真后端只把它放在 retry_after 里，
      // 前端自己拼「（约 N 秒后可重试）」。两边都写就是重复括号。
      body: JSON.stringify({ detail: "现在用的人有点多，稍等一下再试", retry_after: 15 }),
    });
  };
  p.on("request", handler);
  await openDrawer(p);
  await typeAndSend(p, "明天交高数作业");
  await waitStatus(p, "failed", 15000);
  const b = await snap(p);
  check("B1 进入失败态", b.status === "failed", b.status);
  check("B2 说清是「人多」而不是「没整理出来」", /人多|稍等|秒后/.test(b.why || ""), b.why);
  // 时长只能出现一次：「约 15 秒后可重试（约 15 秒后可重试）」是真机出现过的重复
  check("B5 等待时长只有一处", (b.why || "").split("秒后可重试").length === 2, b.why);
  check("B3 原文还在输入框里", /交高数作业/.test(b.input || ""), b.input);
  check("B4 给了「原样再试一次」的出口", /再试一次/.test(b.retry || ""), b.retry);
  await p.screenshot({ path: path.join(OUT, "api_02_429.png") });

  // ---------- C 502：模型没整理出来 ----------
  console.log("\n[C] 502 → 说「换个说法」");
  p.off("request", handler);
  const handler502 = (req) => {
    if (!req.url().includes("/api/capture/parse")) return req.continue();
    return req.respond({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: "AI 没整理出来，请再说一次（模型返回的不是 JSON）" }),
    });
  };
  p.on("request", handler502);
  await reset(p);
  await openDrawer(p);
  await typeAndSend(p, "明天交高数作业");
  await waitStatus(p, "failed", 15000);
  const c = await snap(p);
  check("C1 进入失败态", c.status === "failed", c.status);
  check("C2 提示「换个说法」", /换个说法|没读懂/.test(c.why || ""), c.why);
  check("C3 不把上游细节端给用户", !/JSON|模型/.test(`${c.why}${c.note}`), c.why);
  await p.screenshot({ path: path.join(OUT, "api_03_502.png") });

  // ---------- D 断网 ----------
  console.log("\n[D] 断网 → 不说「你的话说错了」");
  p.off("request", handler502);
  const handlerAbort = (req) => {
    if (!req.url().includes("/api/capture/parse")) return req.continue();
    return req.abort("failed");
  };
  p.on("request", handlerAbort);
  await reset(p);
  await openDrawer(p);
  await typeAndSend(p, "明天交高数作业");
  await waitStatus(p, "failed", 15000);
  const d = await snap(p);
  check("D1 进入失败态", d.status === "failed", d.status);
  check("D2 说「网络」而不是「话没听懂」", /网络/.test(d.why || ""), d.why);
  await p.screenshot({ path: path.join(OUT, "api_04_offline.png") });

  if (errors.length) console.log("\nPAGE ERRORS:", errors.slice(0, 3));
  console.log(`\n${PASS.length} passed, ${FAIL.length} failed`);
  await browser.close();
  process.exit(FAIL.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
