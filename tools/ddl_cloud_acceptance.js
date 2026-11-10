/**
 * 倒计时接入 tasks 的端到端验收（Phase 3 / 实施单 §7.2）。
 *
 * 为什么不用单元测试：这里唯一会错的地方是**日期换算**（本地日期 → 当天 23:59:59
 * 的 Unix 秒 → 回到本地零点）。这种错在纯函数里看着都对，只有在真实时区、
 * 真实后端序列化之后才露出来，症状是「11 月 24 日交论文」变成「11 月 23 日」。
 * 所以宁可起真页面、盯真接口。
 *
 * 覆盖：
 *   1. 未登录时工具照常可用，数据只落 localStorage（这一页的承诺）
 *   2. 登录后首次进入，本地 3 条被逐条迁成 tasks，due_at = 当天 23:59:59
 *   3. 迁完本地键被清空，且标记按账号落盘（不会二次迁移）
 *   4. 工具里加一条 → 提醒中心立刻能看到同一条（同一份数据）
 *   5. 点「交了」→ 工具里消失，task.status = done
 *   6. 已逾期的显示「已逾期」而不是「剩 0 天」
 */
const puppeteer = require("puppeteer-core");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const BASE = "http://localhost:3100";
const USER = { username: "ddl_probe", password: "Baomi@2026test" };

let pass = 0;
const fails = [];
function check(name, ok, detail) {
  if (ok) {
    pass += 1;
    console.log(`  ok   ${name}`);
  } else {
    fails.push(`${name}${detail ? ` — ${detail}` : ""}`);
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

/** 直接打后端，绕开浏览器（带 cookie 才能过鉴权） */
async function serverJson(page, path) {
  return page.evaluate(async (p) => {
    const r = await fetch(p, { credentials: "include" });
    return r.ok ? r.json() : { __status: r.status };
  }, path);
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: "new",
    args: ["--no-sandbox"],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 900 });

  // ---------- 1) 未登录：工具可用，数据只在本机 ----------
  await p.goto(`${BASE}/tools`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(2500);
  const loggedOut = await p.evaluate(() => {
    const btns = [...document.querySelectorAll(".campus-nav button, .campus-nav a")];
    return btns.map((x) => x.textContent.trim()).filter(Boolean);
  });
  // 切到倒计时面板
  await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) =>
      x.textContent.trim() === "倒计时"
    );
    b?.click();
  });
  await sleep(800);
  const localPanel = await p.evaluate(() => ({
    hasInput: !!document.querySelector("#campus-panel-deadline input[type=date]"),
    note: document.querySelector("#campus-panel-deadline .campus-card-note")?.textContent ?? "",
    rows: document.querySelectorAll("#campus-panel-deadline .campus-dl-item").length,
  }));
  check("未登录也能打开倒计时", localPanel.hasInput, JSON.stringify(localPanel));

  // 清空示例数据，写 3 条已知日期（含一条已逾期、一条就是今天）
  const seeded = await p.evaluate(() => {
    const d = new Date();
    const at = (offsetDays) => {
      const x = new Date(d.getFullYear(), d.getMonth(), d.getDate() + offsetDays);
      return x.getTime();
    };
    const items = [
      { name: "交数据结构实验报告", date: at(-2) },
      { name: "期末考试", date: at(0) },
      { name: "交论文", date: at(14) },
    ];
    localStorage.setItem("campus.deadline.items.v1", JSON.stringify(items));
    return items;
  });
  await p.reload({ waitUntil: "domcontentloaded" });
  await sleep(2500);
  await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) =>
      x.textContent.trim() === "倒计时"
    );
    b?.click();
  });
  await sleep(800);
  const localRows = await p.evaluate(() =>
    [...document.querySelectorAll("#campus-panel-deadline .campus-dl-item")].map((x) => ({
      name: x.querySelector(".campus-dl-name")?.textContent ?? "",
      days: x.querySelector(".campus-dl-days")?.textContent ?? "",
    }))
  );
  check("未登录：3 条都显示", localRows.length === 3, JSON.stringify(localRows));
  check(
    "未登录：逾期那条显示「已逾期」而不是「剩 0 天」",
    localRows.some((r) => r.days.includes("已逾期")),
    JSON.stringify(localRows.map((r) => r.days))
  );

  // ---------- 2) 登录后迁移 ----------
  await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
  const auth = await p.evaluate(async (u) => {
    await fetch("/api/auth/register/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(u),
    });
    const r = await fetch("/api/auth/login/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(u),
    });
    const me = await fetch("/api/auth/me", { credentials: "include" });
    return { login: r.status, uid: me.ok ? (await me.json()).user?.id : null };
  }, USER);
  check("登录成功", auth.login === 200 && !!auth.uid, JSON.stringify(auth));

  const before = await serverJson(p, `/api/tasks?scope=all`);
  check("迁移前 tasks 为空", (before.items ?? []).length === 0, JSON.stringify(before).slice(0, 200));

  await p.goto(`${BASE}/tools`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(4000);
  await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) =>
      x.textContent.trim() === "倒计时"
    );
    b?.click();
  });
  await sleep(1500);

  const after = await serverJson(p, `/api/tasks?scope=all`);
  const tasks = after.items ?? [];
  check("迁移后 tasks 有 3 条", tasks.length === 3, JSON.stringify(tasks.map((t) => t.title)));

  // due_at 必须是当地当天 23:59:59
  const expectEnd = (offsetDays) => {
    const d = new Date();
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate() + offsetDays, 23, 59, 59, 0);
    return Math.floor(x.getTime() / 1000);
  };
  const byTitle = Object.fromEntries(tasks.map((t) => [t.title, t]));
  for (const [title, off] of [
    ["交数据结构实验报告", -2],
    ["期末考试", 0],
    ["交论文", 14],
  ]) {
    const t = byTitle[title];
    check(
      `「${title}」due_at = 当天 23:59:59`,
      !!t && t.due_at === expectEnd(off),
      t ? `期望 ${expectEnd(off)} 实际 ${t.due_at}` : "没这条"
    );
  }
  check(
    "迁移后清单是收件箱",
    tasks.length === 3 && tasks.every((t) => t.list_key === "inbox"),
    JSON.stringify(tasks.map((t) => t.list_key))
  );

  const localAfter = await p.evaluate(() => localStorage.getItem("campus.deadline.items.v1"));
  check("迁移后本地键被清空", localAfter === "[]" || localAfter === null, String(localAfter));

  const flag = await p.evaluate((uid) => localStorage.getItem(`campus.deadline.items.v1.migrated.${uid}`), auth.uid);
  check("落了按账号的迁移标记", !!flag, String(flag));

  // 二次进入不重复迁移
  await p.goto(`${BASE}/tools`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(3500);
  const again = await serverJson(p, `/api/tasks?scope=all`);
  check("再进一次不会重复迁移", (again.items ?? []).length === 3, String((again.items ?? []).length));

  // ---------- 3) 工具里加一条 → 提醒中心能看到 ----------
  await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) =>
      x.textContent.trim() === "倒计时"
    );
    b?.click();
  });
  await sleep(800);
  const added = await p.evaluate(() => {
    const panel = document.querySelector("#campus-panel-deadline");
    const name = panel.querySelector("input[type=text]");
    const date = panel.querySelector("input[type=date]");
    const d = new Date();
    const ymd = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
      d.getDate() + 3
    ).padStart(2, "0")}`;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(name, "线性代数小测");
    name.dispatchEvent(new Event("input", { bubbles: true }));
    setter.call(date, ymd);
    date.dispatchEvent(new Event("input", { bubbles: true }));
    const btn = [...panel.querySelectorAll("button")].find((x) => x.textContent.trim() === "添加");
    btn?.click();
    return ymd;
  });
  await sleep(1800);
  const withNew = await serverJson(p, `/api/tasks?scope=all`);
  check(
    "工具里加的条目进了 tasks",
    (withNew.items ?? []).some((t) => t.title === "线性代数小测"),
    JSON.stringify((withNew.items ?? []).map((t) => t.title))
  );

  await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(4000);
  // 3 天后到期的不在「今天」而在「接下来」—— 先切 tab，否则断言会误判成"没同步"
  await p.evaluate(() => {
    const btns = [...document.querySelectorAll(".rem-tabs button")];
    btns.find((b) => b.textContent.includes("接下来"))?.click();
  });
  await sleep(1200);
  const inReminders = await p.evaluate(() =>
    [...document.querySelectorAll(".rem-task .rem-title")].map((x) => x.textContent.trim())
  );
  check(
    "提醒中心能看到同一条",
    inReminders.includes("线性代数小测"),
    JSON.stringify(inReminders)
  );

  // ---------- 4) 点「交了」 ----------
  await p.goto(`${BASE}/tools`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(3500);
  await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")].find((x) =>
      x.textContent.trim() === "倒计时"
    );
    b?.click();
  });
  await sleep(1000);
  const clicked = await p.evaluate(() => {
    const rows = [...document.querySelectorAll("#campus-panel-deadline .campus-dl-item")];
    const row = rows.find((r) => r.textContent.includes("线性代数小测"));
    const btn = [...(row?.querySelectorAll("button") ?? [])].find(
      (x) => x.textContent.trim() === "交了"
    );
    btn?.click();
    return !!btn;
  });
  check("行上有「交了」按钮", clicked);
  await sleep(1800);
  // all 只返回 status='open'，所以完成之后要去 completed 里找
  const afterDone = await serverJson(p, `/api/tasks?scope=completed`);
  const target = (afterDone.items ?? []).find((t) => t.title === "线性代数小测");
  check(
    "「交了」把 task 标成 done",
    target?.status === "done",
    JSON.stringify((afterDone.items ?? []).map((t) => [t.title, t.status]))
  );
  const rowsNow = await p.evaluate(() =>
    [...document.querySelectorAll("#campus-panel-deadline .campus-dl-item .campus-dl-name")].map(
      (x) => x.textContent
    )
  );
  check(
    "完成后从倒计时里消失",
    !rowsNow.includes("线性代数小测"),
    JSON.stringify(rowsNow)
  );

  await b.close();

  console.log(`\n${pass} passed, ${fails.length} failed`);
  if (fails.length) {
    for (const f of fails) console.log("  - " + f);
    process.exit(1);
  }
})().catch((e) => {
  console.error("验收脚本崩了：", e);
  process.exit(2);
});
