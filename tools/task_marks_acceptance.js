/**
 * 待办类型图标（2026-11-10）的端到端验收。
 *
 * 跑法（puppeteer-core 装在工作区临时目录里）：
 *   cp tools/task_marks_acceptance.js .workbuddy/pp/_marks.js
 *   cd .workbuddy/pp && node _marks.js
 *
 * 为什么不用截图比对：图标"有没有显示"用眼睛就能看出来，这里要验的是
 * 眼睛不可靠的三件事 ——
 *   1) 每一条挂的是不是**这一类**的图标（页面照样有图，但全是玉米也说得过去）；
 *   2) 图标的实际渲染尺寸（CSS 写 20px、被 flex 压成 12px 是看不出来的）；
 *   3) 图标和勾选框、标题的先后顺序（挤进 44px 热区里会变成误触）。
 */

const puppeteer = require("puppeteer-core");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const BASE = "http://localhost:3100";
const USER = { username: "marks_probe", password: "Baomi@2026test" };

let pass = 0;
let fail = 0;
function check(label, ok, extra) {
  if (ok) {
    pass++;
    console.log("  PASS", label);
  } else {
    fail++;
    console.log("  FAIL", label, extra === undefined ? "" : JSON.stringify(extra));
  }
}

const api = (p, path, init) =>
  p.evaluate(
    async (pa, i) => {
      const r = await fetch(pa, { credentials: "include", ...(i || {}) });
      return r.ok ? r.json().catch(() => null) : { __status: r.status };
    },
    path,
    init
  );

/**
 * 关掉引导 + 公告两个全屏层。
 * 不关的话 `page.mouse` / 键盘打到的是遮罩（`.click()` 反而能穿过去），
 * 于是"按钮全绿、交互全红"，上一轮已经在排序上误判过一次。
 */
async function dismissOverlays(p) {
  const skipped = await p.evaluate(() => {
    const b = document.querySelector(".guide-skip");
    if (!b) return false;
    b.click();
    return true;
  });
  if (skipped) await sleep(500);
  const hasAnn = await p.evaluate(() => !!document.querySelector(".ann-body"));
  if (hasAnn) {
    await p.evaluate(() => {
      const b = document.querySelector(".ann-body");
      b.scrollTop = b.scrollHeight;
      b.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    await sleep(400);
    await p.evaluate(() => document.querySelector(".ann-go")?.click());
    await sleep(500);
  }
}

const click = (p, sel, text) =>
  p.evaluate(
    (s, t) => {
      const b = [...document.querySelectorAll(`${s} button`)].find((x) =>
        x.textContent.trim().startsWith(t)
      );
      if (!b) return false;
      b.click();
      return true;
    },
    sel,
    text
  );

/** 标题 → data-mark（不按顺序比：列表顺序由优先级和截止时间决定，和本例无关） */
async function marksByTitle(p) {
  return p.evaluate(() => {
    const out = {};
    for (const row of document.querySelectorAll(".rem-task")) {
      const title = row.querySelector(".rem-title")?.textContent?.trim();
      out[title] = row.querySelector(".rem-mark")?.getAttribute("data-mark") ?? null;
    }
    return out;
  });
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: "new",
    args: ["--no-sandbox"],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });

  await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  const uid = await p.evaluate(async (u) => {
    await fetch("/api/auth/register/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(u),
    });
    await fetch("/api/auth/login/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(u),
    });
    const me = await fetch("/api/auth/me", { credentials: "include" });
    return me.ok ? (await me.json()).user?.id : null;
  }, USER);
  if (!uid) {
    console.log("登录失败，后面的断言没有意义");
    await b.close();
    process.exit(1);
  }
  await p.evaluate((u) => {
    localStorage.setItem(`baomi.guide.v1.${u}`, "1");
    for (const id of ["2026-11", "2026-11-b"])
      localStorage.setItem(`baomi.announce.v1.${u}.${id}`, "1");
  }, uid);

  // 清掉上一轮留下的（脚本要能重复跑）
  const old = await api(p, "/api/tasks?scope=all");
  const oldIds = (old.items ?? []).map((t) => t.id);
  if (oldIds.length)
    await api(p, "/api/tasks/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: oldIds, action: "delete" }),
    });

  const now = Math.floor(Date.now() / 1000);
  const seed = [
    ["交高数作业（第 4 章）", "homework", "notes", now + 86400 * 2],
    ["背四六级单词 60 个", "exam", "bolt", now + 86400 * 5],
    ["线性代数（周三 3-4 节）", "course", "book", now + 86400],
    ["社团例会：定下周招新", "club", "target", null],
    ["买 A4 纸和打印卡", "life", "home", null],
    // 收件箱三条：前两条靠标题猜，最后一条猜不出才落到玉米
    ["把奖学金材料交到学院", "inbox", "notes", null],
    ["取快递（菜鸟驿站）", "inbox", "home", null],
    ["想一下寒假去哪", "inbox", "corn", null],
  ];
  for (const [title, list_key, _mark, due_at] of seed) {
    await api(p, "/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(due_at ? { title, list_key, due_at } : { title, list_key }),
    });
  }

  console.log("\n== 1. 每条挂的是不是这一类的图标 ==");
  await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "domcontentloaded" });
  await sleep(3500);
  await dismissOverlays(p);
  await click(p, ".rem-tabs", "全部");
  await sleep(700);

  const got = await marksByTitle(p);
  for (const [title, , want] of seed) {
    check(`「${title}」→ ${want}`, got[title] === want, { got: got[title] });
  }
  check("八条都在列表里", Object.keys(got).length === 8, Object.keys(got).length);

  console.log("\n== 2. 渲染尺寸与顺序 ==");
  const geom = await p.evaluate(() => {
    const rows = [...document.querySelectorAll(".rem-task")];
    return rows.map((r) => {
      const check = r.querySelector(".rem-check").getBoundingClientRect();
      const mark = r.querySelector(".rem-mark").getBoundingClientRect();
      const title = r.querySelector(".rem-title").getBoundingClientRect();
      return {
        w: Math.round(mark.width),
        h: Math.round(mark.height),
        order: check.right <= mark.left + 1 && mark.right <= title.left + 1,
      };
    });
  });
  check(
    "图标实际渲染 20×20（没有被 flex 压扁）",
    geom.every((g) => g.w === 20 && g.h === 20),
    geom.map((g) => `${g.w}x${g.h}`).join(",")
  );
  check(
    "顺序是 勾选框 → 图标 → 标题（图标没挤进 44px 热区）",
    geom.every((g) => g.order)
  );

  console.log("\n== 3. 下一件与联动提醒 ==");
  const nextMark = await p.evaluate(
    () => document.querySelector(".rem-next-mark")?.getAttribute("data-mark") ?? null
  );
  check("「下一件」也有图标（明天到期的线性代数 → book）", nextMark === "book", nextMark);
  const nextIcon = await p.evaluate(() => {
    const e = document.querySelector(".rem-next-mark");
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return `${Math.round(r.width)}x${Math.round(r.height)}`;
  });
  check("下一件的图标比列表里大一号（26px）", nextIcon === "26x26", nextIcon);

  console.log("\n== 4. 排序模式同样带图标 ==");
  await click(p, ".rem-toolrow", "排序");
  await sleep(600);
  const sortMarks = await p.evaluate(() =>
    [...document.querySelectorAll(".rem-sortrow")].map(
      (r) => r.querySelector(".rem-mark")?.getAttribute("data-mark") ?? null
    )
  );
  check("排序行里每条都有图标", sortMarks.length >= 8 && sortMarks.every(Boolean), sortMarks);
  await click(p, ".rem-selbar", "取消");
  await sleep(400);

  console.log("\n== 5. 已完成的不改图标，暂停/完成后仍在 ==");
  await p.evaluate(() => {
    const btn = [...document.querySelectorAll(".rem-task")].find(
      (r) => r.querySelector(".rem-title")?.textContent?.trim() === "买 A4 纸和打印卡"
    );
    btn?.querySelector(".rem-check")?.click();
  });
  await sleep(1200);
  await click(p, ".rem-tabs", "已完成");
  await sleep(700);
  const doneMark = await p.evaluate(
    () => document.querySelector(".rem-task .rem-mark")?.getAttribute("data-mark") ?? null
  );
  check("已完成里的「买 A4 纸」仍是 home", doneMark === "home", doneMark);

  console.log(`\n${pass} passed, ${fail} failed`);
  await b.close();
  process.exit(fail ? 1 : 0);
})();
