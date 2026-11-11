/**
 * Phase C 的前端验收：点「加入」是不是真的写进去了。
 *
 * 跑法（dev server 在 3100、**没有**开 NEXT_PUBLIC_CAPTURE_OFFLINE，后端在 8000）：
 *   NODE_PATH=.workbuddy/pp/node_modules node tools/capture_commit_e2e.js
 *
 * 这一份和 `capture_parse_api_e2e.js` 的分工：
 *   那份验"解析这一层接通了没有"（四种失败怎么说）；
 *   这份验"写库这一层写对了没有" —— 条数对不对、有没有写两遍、
 *   只设截止的任务有没有被顺手造出联动提醒、部分失败时抽屉关没关。
 *
 * 每一格都去**后端数数**，不看界面上的字。界面说「已加入 3 条」而后端只有 2 条，
 * 正是这一轮最该抓住的那种错。
 */
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = "http://localhost:3100";
// 专用账号：`cap_probe` 攒了几百条，而列表接口有上限（limit 被夹到 200），
// 用总数相减会被截断成 0 —— 上一版就是这样把"写进去了"误判成"没写"的。
// 所以这里固定用 `cap_commit`，并且**按 id 差集**数，不看总数。
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

async function waitStatus(p, want, timeout = 45000) {
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
      await sleep(350);
      return;
    } catch {
      await sleep(800);
    }
  }
  throw new Error("抽屉打不开");
}

async function reset(p) {
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

async function typeAndSend(p, text) {
  await p.click(".rem-cap-input");
  await p.type(".rem-cap-input", text, { delay: 3 });
  await sleep(120);
  await p.keyboard.press("Enter");
}

/**
 * 等到结果态；撞上 429（每人每分钟 8 次 parse）就等一个窗口再来一次。
 *
 * 一个脚本要 parse 四次，跑两遍就会踩到限流 —— 这不是产品 bug，
 * 是测试节奏太快。等它一轮比调大 prompt 配额老实。
 */
async function parseTo(p, text, tries = 3) {
  for (let i = 0; i < tries; i += 1) {
    await typeAndSend(p, text);
    const got = await p
      .waitForFunction(
        () => {
          const st = document.querySelector(".rem-cap-drawer")?.getAttribute("data-status");
          return st === "ready" || st === "failed";
        },
        { timeout: 60000 }
      )
      .then(() => true)
      .catch(() => false);
    if (!got) throw new Error("等不到结果：解析一直卡在整理中");
    const st = await p.evaluate(
      () => document.querySelector(".rem-cap-drawer")?.getAttribute("data-status")
    );
    if (st === "ready") return;
    const why = await p.evaluate(
      () => document.querySelector(".rem-cap-fail-why")?.textContent?.trim() ?? ""
    );
    if (!/人多|稍等/.test(why)) throw new Error(`解析失败：${why}`);
    console.log("  撞到限流，等 25s 再来…");
    await sleep(25000);
  }
  throw new Error("限流一直没恢复");
}

/** 后端现在有哪些待办 / 哪些提醒。**所有断言都以它为准**，不看界面上写的字。
 *  返回 id 列表而不是条数：列表接口有上限，总数相减会被截断。 */
async function snapshot(p) {
  return p.evaluate(async () => {
    const t = await (await fetch("/api/tasks?scope=all&limit=500", { credentials: "include" })).json();
    const r = await (await fetch("/api/reminders?scope=all", { credentials: "include" })).json();
    return { tasks: t.items, reminders: r.items };
  });
}

/** 这次跑完新多出来的（按 id 差集）。 */
function added(before, after) {
  const bt = new Set(before.tasks.map((i) => i.id));
  const br = new Set(before.reminders.map((i) => i.id));
  return {
    tasks: after.tasks.filter((i) => !bt.has(i.id)),
    reminders: after.reminders.filter((i) => !br.has(i.id)),
  };
}

/** 界面上这一批 proposal 里有几条待办、几个日程（用来算"应该增加多少"）。 */
async function kindCounts(p) {
  return p.evaluate(() => ({
    task: document.querySelectorAll('.rem-cap-node[data-kind="task"]').length,
    reminder: document.querySelectorAll('.rem-cap-node[data-kind="reminder"]').length,
    rows: document.querySelectorAll(".rem-cap-axisrow").length,
  }));
}

async function snap(p) {
  return p.evaluate(() => {
    const q = (s) => document.querySelector(s);
    return {
      drawer: !!q(".rem-cap-drawer"),
      status: q(".rem-cap-drawer")?.getAttribute("data-status") ?? null,
      rows: document.querySelectorAll(".rem-cap-axisrow").length,
      bad: document.querySelectorAll('.rem-cap-flag[data-tone="bad"]').length,
      issue: q(".rem-cap-issue")?.textContent?.trim() ?? null,
      failbar: q(".rem-cap-failbar")?.textContent?.trim() ?? null,
      toast: [...document.querySelectorAll(".toast, [class*='toast']")]
        .map((n) => n.textContent.trim())
        .join(" | ")
        .slice(0, 200),
      primaryDisabled: q(".rem-cap-primary")?.disabled ?? null,
      input: q(".rem-cap-input")?.value ?? null,
      title: q(".rem-cap-title")?.textContent?.trim() ?? null,
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
    }).catch(() => {});
    await fetch("/api/auth/login/", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include",
      body: JSON.stringify(u),
    });
    const me = await fetch("/api/auth/me", { credentials: "include" });
    const j = await me.json().catch(() => ({}));
    return j?.user?.id ?? null;
  }, USER);
  if (!uid) throw new Error("登录失败");
  await p.evaluate((id) => {
    try { localStorage.setItem(`baomi.guide.v1.${id}`, "1"); } catch {}
  }, uid);

  // 空账号进 /reminders 看到的是空态（"还没有待办"），AI 整理那条入口只在**有内容**时挂。
  // 所以先放一条基线任务进去 —— 这是搭场景，不是造假数据：所有断言都只看
  // "这次跑完比跑之前多了什么"（按 id 差集）。
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

  // ==================== A 真的写进去了 ====================
  console.log("\n[A] 点「加入」→ 后端真的多出这几条");
  await reset(p);
  const before = await snapshot(p);
  await openDrawer(p);
  await parseTo(p, "明天交高数作业，周五晚上七点社团开会");
  const kinds = await kindCounts(p);
  console.log(`  解析出 ${kinds.rows} 条：${kinds.task} 待办 / ${kinds.reminder} 日程`);
  check("A0 解析出的条数 > 0", kinds.rows > 0, kinds);

  await p.evaluate(() => document.querySelector(".rem-cap-primary")?.click());
  // 全成功 → 抽屉自己关
  const closed = await p
    .waitForFunction(() => !document.querySelector(".rem-cap-drawer"), { timeout: 20000 })
    .then(() => true)
    .catch(() => false);
  check("A1 全部写成功后抽屉自动关闭", closed, closed);

  await sleep(600);
  const after = await snapshot(p);
  const d1 = added(before, after);
  check("A2 待办按解析结果进了库", d1.tasks.length === kinds.task, {
    got: d1.tasks.map((i) => i.title),
    want: kinds.task,
  });
  check("A3 提醒按解析结果进了库", d1.reminders.length === kinds.reminder, {
    got: d1.reminders.map((i) => i.title),
    want: kinds.reminder,
  });

  // 只设截止的任务不许被顺手造一条联动提醒：
  // 联动提醒的标记是 `source="task"`，而列表接口会把它过滤掉，
  // 所以从**任务自己**身上看 remind_at 是不是 null 才算数。
  const orphan = d1.tasks.map((i) => ({
    title: i.title,
    remind_at: i.remind_at,
    due_at: i.due_at,
  }));
  check(
    "A4 没要求提醒的待办没有 remind_at（没被顺手造联动提醒）",
    orphan.every((i) => i.remind_at === null),
    orphan
  );

  const s1 = await snap(p);
  check("A5 toast 说清加入了几条", /已加入 \d+ 条/.test(s1.toast), s1.toast);
  console.log(`  toast: ${s1.toast}`);
  // 成功态的"证据"不在抽屉里（它已经关了），在刷新后的列表上
  await p.screenshot({ path: path.join(OUT, "commit_00_after_commit.png") });

  // ==================== B 双击只写一次 ====================
  console.log("\n[B] 双击「加入」→ 只写一遍");
  await reset(p);
  const before2 = await snapshot(p);
  await openDrawer(p);
  await parseTo(p, "明天交高数作业");
  const kinds2 = await kindCounts(p);
  // 同一个事件循环里点两次：这正是用户"点快了"的样子（第二次点击时
  // 第一次的 await 还没回来，React 也还没重渲染出 disabled）
  await p.evaluate(() => {
    const btn = document.querySelector(".rem-cap-primary");
    btn?.click();
    btn?.click();
  });
  await p.waitForFunction(() => !document.querySelector(".rem-cap-drawer"), { timeout: 20000 }).catch(() => {});
  await sleep(800);
  const after2 = await snapshot(p);
  const d2 = added(before2, after2);
  check("B1 连点两次只写了一条", d2.tasks.length === kinds2.task, {
    got: d2.tasks.length,
    want: kinds2.task,
  });

  // ==================== C 全不选：按钮禁用，点了也不关 ====================
  console.log("\n[C] 全不选 → 主按钮禁用，点它不写库、不关抽屉");
  await reset(p);
  const before3 = await snapshot(p);
  await openDrawer(p);
  await parseTo(p, "明天交高数作业");
  await p.evaluate(() => {
    const all = [...document.querySelectorAll(".rem-cap-link")];
    all[0]?.click(); // 「全不选」
  });
  await sleep(200);
  const c = await snap(p);
  check("C1 全不选后主按钮禁用", c.primaryDisabled === true, c.primaryDisabled);
  await p.evaluate(() => document.querySelector(".rem-cap-primary")?.click());
  await sleep(600);
  const c2 = await snap(p);
  const after3 = await snapshot(p);
  const d3 = added(before3, after3);
  check("C2 点禁用的主按钮不关抽屉", c2.drawer === true, c2.drawer);
  check("C3 一条都没写进去", d3.tasks.length + d3.reminders.length === 0, d3);
  await p.screenshot({ path: path.join(OUT, "commit_01_none_picked.png") });

  // ==================== D 一条写失败：其余落地，抽屉不关 ====================
  console.log("\n[D] 写库时一条 400 → 失败项留下并说明原因");
  await reset(p);
  await openDrawer(p);
  await parseTo(p, "明天交高数作业，周五晚上七点社团开会");
  const before4 = await snapshot(p);

  await p.setRequestInterception(true);
  let blockedOnce = false;
  const blockOne = (req) => {
    // 只挡第一笔写库请求，后面的照常放行 —— 这样"部分失败"才是真的部分。
    // **不能用 p.off 摘掉自己**：摘掉之后剩下的请求没人 continue，
    // 会一直挂在拦截队列里，整个页面像死了（这一版就踩过）。
    if (
      !blockedOnce &&
      req.method() === "POST" &&
      /\/api\/(tasks|reminders)$/.test(new URL(req.url()).pathname)
    ) {
      blockedOnce = true;
      return req.respond({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "这条后端不收" }),
      });
    }
    return req.continue();
  };
  p.on("request", blockOne);

  await p.evaluate(() => document.querySelector(".rem-cap-primary")?.click());
  await sleep(1500);
  const d = await snap(p);
  check("D1 有失败时抽屉不关", d.drawer === true, d.drawer);
  check("D2 失败那一行被标出来", d.bad === 1, d.bad);
  check("D3 行下面写了原因", /后端不收|没加上/.test(d.issue || ""), d.issue);
  check("D4 底栏说清「已加入 X 条，Y 条没加上」", /已加入 \d+ 条，\d+ 条没加上/.test(d.failbar || ""), d.failbar);
  const after4 = await snapshot(p);
  const d4 = added(before4, after4);
  check("D5 没失败的那几条照样落地", d4.tasks.length + d4.reminders.length === 1, {
    tasks: d4.tasks.length,
    reminders: d4.reminders.length,
  });
  await p.screenshot({ path: path.join(OUT, "commit_02_partial.png") });
  p.off("request", blockOne);
  await p.setRequestInterception(false);

  // ==================== E 重开抽屉是干净的 ====================
  console.log("\n[E] 关掉再打开 → 回到第一格，不留上次的条目");
  await reset(p);
  await openDrawer(p);
  const e = await snap(p);
  check("E1 重开后是输入态", e.status === "idle", e.status);
  check("E2 轴上没有上次的条目", e.rows === 0, e.rows);
  check("E3 输入框是空的", (e.input || "") === "", e.input);
  check("E4 底栏不是「加入 N 条」", !/加入 \d+ 条/.test(e.title || ""), e.title);

  console.log(`\n页面异常：${errors.length ? errors.join(" / ") : "无"}`);
  console.log(`\n${PASS.length} passed, ${FAIL.length} failed`);
  await browser.close();
  if (FAIL.length > 0) process.exit(1);
})().catch((e) => {
  console.error("FAIL:", e.message);
  process.exit(1);
});
