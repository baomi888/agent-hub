/**
 * 「缺时间就地补」的验收（离线 fixture + 真后端写库）。
 *
 * 跑法（前端必须用离线开关起，否则拿不到"没有时间的提醒"这个样本）：
 *   cd frontend && rm -rf .next && NEXT_PUBLIC_CAPTURE_OFFLINE=1 npx next dev -p 3100
 *   NODE_PATH=.workbuddy/pp/node_modules node tools/capture_fixtime_e2e.js
 *
 * 为什么必须走离线：真模型几乎不会吐出"提醒但一个时间都没给"的条目
 * （它会反问你几点）。而这一格恰恰是补时间唯一的入口条件，只能靠 fixture 造。
 * 写库那半段仍然是真链路 —— 补完之后点「加入」，去后端看提醒真的多了一条、
 * `fire_at` 就是我们填的那一刻。
 *
 * 每一格要么数后端，要么读 DOM 里那一行的状态，不看 toast 上的文字。
 */
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = "http://localhost:3100";
const USER = { username: "cap_commit", password: "Baomi@2026test" };
const OUT = path.resolve(__dirname, "shots");
const CHROME = "C:/Users/Fengjinghao/AppData/Local/Google/Chrome/Application/chrome.exe";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PASS = [];
const FAIL = [];
function check(name, cond, extra) {
  (cond ? PASS : FAIL).push(name);
  console.log(
    `  ${cond ? "PASS" : "FAIL"} ${name}${!cond && extra !== undefined ? `  <- ${JSON.stringify(extra)}` : ""}`
  );
}

/** 往受控 input 里写值：必须用原生 setter，否则 React 收不到 onChange。 */
async function setInput(p, sel, value) {
  await p.$eval(
    sel,
    (el, v) => {
      const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      set.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    },
    value
  );
}

/** 公告页（更新说明）会整屏盖住入口，goto 之后必须重注一次 —— goto 会连 style 一起冲掉。 */
async function hideAnn(p) {
  await p.evaluate(() => {
    if (document.getElementById("cap-hide-ann")) return;
    const st = document.createElement("style");
    st.id = "cap-hide-ann";
    st.textContent = ".ann-page{display:none !important} nextjs-portal{display:none !important}";
    document.head.appendChild(st);
  });
  await sleep(300);
}

async function openDrawer(p) {
  for (let i = 0; i < 3; i += 1) {
    try {
      await p.waitForSelector(".rem-cap-strip", { timeout: 10000 });
      await p.click(".rem-cap-strip");
      await p.waitForSelector(".rem-cap-drawer", { timeout: 6000 });
      await sleep(350);
      return;
    } catch {
      await sleep(800);
    }
  }
  throw new Error("抽屉打不开");
}

async function snapshot(p) {
  return p.evaluate(async () => {
    const r = await (await fetch("/api/reminders?scope=all", { credentials: "include" })).json();
    const t = await (await fetch("/api/tasks?scope=all&limit=500", { credentials: "include" })).json();
    return { reminders: r.items, tasks: t.items };
  });
}

function added(before, after) {
  const br = new Set(before.reminders.map((i) => i.id));
  return after.reminders.filter((i) => !br.has(i.id));
}

/** 第一行（这个脚本里永远只有一条）的界面状态。 */
async function row(p) {
  return p.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const r = q(".rem-cap-axisrow");
    return {
      rows: document.querySelectorAll(".rem-cap-axisrow").length,
      kind: r?.querySelector(".rem-cap-node")?.getAttribute("data-kind") ?? null,
      flag: r?.querySelector(".rem-cap-flag")?.textContent?.trim() ?? null,
      issue: r?.querySelector(".rem-cap-issue")?.textContent?.trim() ?? null,
      fixBtn: r?.querySelector(".rem-cap-fix-btn")?.textContent?.trim() ?? null,
      panel: !!r?.querySelector(".rem-cap-fix-panel"),
      hasDate: !!r?.querySelector('.rem-cap-fix-input[type="date"]'),
      hasTime: !!r?.querySelector('.rem-cap-fix-input[type="time"]'),
      fixErr: r?.querySelector(".rem-cap-fix-err")?.textContent?.trim() ?? null,
      hint: r?.querySelector(".rem-cap-fix-hint")?.textContent?.trim() ?? null,
      ticked: r?.querySelector(".rem-cap-nodewrap")?.getAttribute("aria-checked") === "true",
      axisTime: r?.querySelector(".rem-cap-axistime")?.textContent?.trim() ?? null,
      drawer: !!q(".rem-cap-drawer"),
      primary: q(".rem-cap-primary")?.textContent?.trim() ?? null,
      dock: q(".rem-cap-dock--result")?.textContent?.replace(/\s+/g, " ").trim() ?? null,
      primaryDisabled: q(".rem-cap-primary")?.disabled ?? null,
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
    try {
      localStorage.setItem("theme", "light");
    } catch {}
  });
  const errors = [];
  p.on("pageerror", (e) => errors.push(e.message));

  await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(500);
  const uid = await p.evaluate(async (u) => {
    await fetch("/api/auth/login/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(u),
    });
    const me = await fetch("/api/auth/me", { credentials: "include" });
    const j = await me.json().catch(() => ({}));
    return j?.user?.id ?? null;
  }, USER);
  if (!uid) throw new Error("登录失败");
  await p.evaluate((id) => {
    try {
      localStorage.setItem(`baomi.guide.v1.${id}`, "1");
    } catch {}
  }, uid);

  await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "networkidle2", timeout: 60000 });
  await hideAnn(p);
  await sleep(300);

  // 空账号进 /reminders 是空态，AI 整理那条入口只在**有内容**时挂
  await p.evaluate(async () => {
    const has = await (await fetch("/api/tasks?scope=all&limit=5", { credentials: "include" })).json();
    if ((has.items ?? []).length > 0) return;
    await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ title: "基线任务", list_key: "inbox" }),
    });
  });
  await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "networkidle2", timeout: 60000 });
  await hideAnn(p);
  await sleep(800);

  const before = await snapshot(p);
  await openDrawer(p);

  // ---------- A 解析出「有提醒、没时间」的那一条 ----------
  console.log("\n[A] 「提醒我开会」：提醒，但一个时间都没有");
  await p.click(".rem-cap-input");
  await p.type(".rem-cap-input", "提醒我开会", { delay: 3 });
  await sleep(120);
  await p.keyboard.press("Enter");
  await p.waitForFunction(
    () => document.querySelector(".rem-cap-drawer")?.getAttribute("data-status") === "ready",
    { timeout: 30000 }
  );
  await sleep(300);

  let r = await row(p);
  check("A1 解析出一条提醒", r.rows === 1 && r.kind === "reminder", r);
  check("A2 标的是「缺少时间」而不是「可直接加入」", r.flag === "缺少时间", r.flag);
  check("A3 缺时间的那条行上有「补个时间」", r.fixBtn === "补个时间", r.fixBtn);

  // ---------- B 不补时间直接点加入：拦住，并指向出口 ----------
  console.log("\n[B] 不补时间直接点加入");
  await p.click(".rem-cap-primary");
  await sleep(600);
  r = await row(p);
  check("B1 原因里写着下一步（「补上时间」）", /补上时间/.test(r.issue ?? ""), r.issue);
  check("B2 抽屉没关（失败项得留在原地）", r.drawer === true);
  let mid = await snapshot(p);
  check("B3 一条都没写进去", added(before, mid).length === 0, added(before, mid).length);
  // 截图抓到的原状：底栏一边写「1 条会提醒」一边写「1 条都没加上」，两句话互相打脸
  check("B4 底栏不承诺「1 条会提醒」（这条根本写不进去）", !/1 条会提醒/.test(r.dock ?? ""), r.dock);
  check("B5 主按钮不承诺「设 1 条提醒」", !/设 1 条提醒/.test(r.primary ?? ""), r.primary);
  check("B6 主按钮说「还不能加」，不说「加入 1 条」", /还不能加/.test(r.primary ?? ""), r.primary);

  // ---------- C 补时间的三种拦下 ----------
  console.log("\n[C] 补时间：不填 / 填过去的时间 / 填未来");
  await p.click(".rem-cap-fix-btn");
  await sleep(300);
  r = await row(p);
  check("C1 面板打开后有日期和时刻两个输入框", r.panel && r.hasDate && r.hasTime, r);

  // 只填日期、时刻空着就点确定
  await p.click(".rem-cap-fix-ok");
  await sleep(250);
  r = await row(p);
  check("C2 没填时刻 → 就地拦下", /日期和时刻都要选/.test(r.fixErr ?? ""), r.fixErr);
  check("C3 拦下时面板不收起（还能接着填）", r.panel === true);

  // 过去的时间
  const past = await p.evaluate(() => {
    const d = new Date(Date.now() - 3 * 3600 * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return {
      date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
    };
  });
  await setInput(p, '.rem-cap-fix-input[type="date"]', past.date);
  await setInput(p, '.rem-cap-fix-input[type="time"]', past.time);
  await sleep(150);
  await p.click(".rem-cap-fix-ok");
  await sleep(250);
  r = await row(p);
  check("C4 过去的时间 → 就地拦下", /已经过去/.test(r.fixErr ?? ""), r.fixErr);

  await p.screenshot({ path: path.join(OUT, "fixtime_01_past_rejected.png") });

  // 未来：3 小时后的整点
  const plan = await p.evaluate(() => {
    const d = new Date(Date.now() + 3 * 3600 * 1000);
    d.setMinutes(0, 0, 0);
    const pad = (n) => String(n).padStart(2, "0");
    return {
      date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
      ts: Math.floor(d.getTime() / 1000),
    };
  });
  await setInput(p, '.rem-cap-fix-input[type="date"]', plan.date);
  await setInput(p, '.rem-cap-fix-input[type="time"]', plan.time);
  await sleep(150);
  await p.click(".rem-cap-fix-ok");
  await sleep(350);
  r = await row(p);
  check("C5 补完 → 面板收起", r.panel === false);
  check("C6 补完 → 标签变成「可直接加入」", r.flag === "可直接加入", r.flag);
  check("C7 补完 → 上一次的红字也撤了", r.issue === null, r.issue);
  check("C8 补完 → 这条自动勾上", r.ticked === true);
  check("C9 补完 → 轴上出现那个时刻", r.axisTime === plan.time, {
    got: r.axisTime,
    want: plan.time,
  });
  await p.screenshot({ path: path.join(OUT, "fixtime_02_filled.png") });

  // ---------- D 加入：真写库，时刻对得上 ----------
  console.log("\n[D] 点加入：真写进去，时刻要对得上");
  await p.click(".rem-cap-primary");
  await sleep(1200);
  const after = await snapshot(p);
  const newOnes = added(before, after);
  check("D1 后端多了一条提醒", newOnes.length === 1, newOnes.map((i) => i.title));
  const made = newOnes[0] ?? {};
  check("D2 fire_at 就是补的那一刻", made.fire_at === plan.ts, {
    got: made.fire_at,
    want: plan.ts,
  });
  check("D3 没有顺手写进待办表", after.tasks.length === mid.tasks.length, {
    tasksBefore: mid.tasks.length,
    tasksAfter: after.tasks.length,
  });
  r = await row(p);
  check("D4 全成功 → 抽屉关了", r.drawer === false);
  await p.screenshot({ path: path.join(OUT, "fixtime_03_after_commit.png") });

  check("E1 没有未捕获的页面异常", errors.length === 0, errors.slice(0, 3));

  console.log(`\n${PASS.length} passed, ${FAIL.length} failed`);
  if (FAIL.length) console.log("失败：" + FAIL.join(" / "));
  await browser.close();
  process.exit(FAIL.length ? 1 : 0);
})().catch(async (e) => {
  console.error("脚本崩了：", e.message);
  process.exit(2);
});
