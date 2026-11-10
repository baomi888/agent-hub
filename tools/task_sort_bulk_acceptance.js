/**
 * 待办「排序 + 批量」的端到端验收（第二波第 3 项）。
 *
 * 为什么必须起真页面：这一项里最容易错的都不是"画错了"，而是
 *   - 拖完只在前端换了顺序、没真的发出去（刷新就回去）；
 *   - 拖一次发了 N 个请求，中途失败留下半新半旧；
 *   - 批量完成没停用联动提醒（列表都打勾了，到点还在响）；
 *   - 批量删除跳过了二次确认。
 * 这些在截图里全看不见，只能真的点一遍、再回头问接口。
 *
 * 覆盖：
 *   1. 初始顺序由优先级决定（甲>乙>丙）
 *   2. 把最后一条拖到最前 → 保存 → 接口里顺序真的变了，且**手动序压过优先级**
 *   3. 刷新后顺序还在（不是只改了本地 state）
 *   4. 键盘 ↑↓ 也能调（把手是 button，读屏和键盘用户没有拖拽）
 *   5. 多选：没选东西时不出现底部操作条；选 2 条 → 批量完成 → 两条都 done
 *   6. 批量完成连带停用联动提醒（与单条 complete 同口径）
 *   7. 批量改清单真的改了 list_key（不是只在前端改）
 *   8. 批量删除必须经过确认框；取消后数据一点没动
 */
const puppeteer = require("puppeteer-core");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const BASE = "http://localhost:3100";
const USER = { username: "sort_probe", password: "Baomi@2026test" };

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

/** 同源请求（cookie 才带得上；直连 8000 是跨域，会 401） */
async function api(page, path, init) {
  return page.evaluate(
    async (p, i) => {
      const r = await fetch(p, { credentials: "include", ...(i || {}) });
      const body = r.ok ? await r.json().catch(() => null) : { __status: r.status };
      return body;
    },
    path,
    init
  );
}

const titles = async (page, scope) => {
  const b = await api(page, `/api/tasks?scope=${scope}`);
  return (b.items ?? []).map((t) => t.title);
};

const rowTitles = (page) =>
  page.$$eval(".rem-sortrow .rem-title", (els) => els.map((e) => e.textContent.trim()));

const listTitles = (page) =>
  page.$$eval(".rem-task .rem-title", (els) => els.map((e) => e.textContent.trim()));

/** 点一个文字按钮（页面里大量 `<button>文字</button>`）
 *
 * 用 startsWith 而不是相等：tab 上挂着计数（`全部4`），批量条上的主按钮
 * 在忙时会变成「处理中…」—— 相等匹配会把这两种情况都判成找不到。 */
async function clickText(page, box, text) {
  const ok = await page.evaluate(
    (sel, t) => {
      const b = [...document.querySelectorAll(`${sel} button`)].find(
        (x) => x.textContent.trim() === t || x.textContent.trim().startsWith(t)
      );
      if (!b) return false;
      b.click();
      return true;
    },
    box,
    text
  );
  return ok;
}

/**
 * 关掉两个「第一次进来才会碰到」的遮罩：上手说明 + 更新公告。
 *
 * ★ 这不是可选项。两个遮罩都是 `position: fixed` 的全屏层，JS 的 `.click()`
 *   能穿过去（它不看层级），但 `page.mouse` / `page.keyboard` 打到的是遮罩本身
 *   —— 于是会出现「拖拽没反应、方向键没反应，而批量按钮全过」这种假象：
 *   前半段真交互全红、后半段假交互全绿，看着像拖拽写错了。
 *
 * ★ 公告是**强制**的：没有关闭按钮，要滚到底才解锁 «开始使用»。
 *   这里直接把它滚到底再点，不是绕过产品逻辑 —— 只是在替用户完成「读完」。
 */
async function dismissOverlays(page) {
  const guide = await page.evaluate(() => {
    const b = document.querySelector(".guide-skip");
    if (!b) return false;
    b.click();
    return true;
  });
  if (guide) await sleep(500);

  const hasAnn = await page.evaluate(() => !!document.querySelector(".ann-body"));
  if (hasAnn) {
    await page.evaluate(() => {
      const body = document.querySelector(".ann-body");
      body.scrollTop = body.scrollHeight;
      body.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    await sleep(500);
    await page.evaluate(() => {
      const b = document.querySelector(".ann-go");
      if (b && !b.disabled) b.click();
    });
    await sleep(600);
  }
  // 等遮罩真的从 DOM 上消失，别在它还在退场动画里就往下点
  for (let i = 0; i < 20; i += 1) {
    const left = await page.evaluate(
      () => !!document.querySelector(".ann-page, .guide-mask")
    );
    if (!left) break;
    await sleep(150);
  }
  return guide || hasAnn;
}

/** 进列表并切到某个 tab（顺手把可能复活的遮罩关掉） */
async function openList(page, scope) {
  await dismissOverlays(page);
  if (scope) {
    await clickText(page, ".rem-tabs", scope);
    await sleep(600);
  }
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: "new",
    args: ["--no-sandbox"],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 900 });

  try {
    // ---------- 0) 登录 ----------
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

    // 清空，保证脚本可反复跑
    const old = await api(p, "/api/tasks?scope=all");
    const oldIds = (old.items ?? []).map((t) => t.id);
    if (oldIds.length) {
      await api(p, "/api/tasks/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: oldIds, action: "delete" }),
      });
    }

    // ---------- 1) 三条待办，优先级决定初始顺序 ----------
    const mk = async (title, priority, extra) =>
      api(p, "/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, priority, ...(extra || {}) }),
      });
    await mk("甲", 2);
    await mk("乙", 1);
    const c = await mk("丙", 0);
    const all = await titles(p, "all");
    check("初始顺序按优先级：甲 乙 丙", all.join("") === "甲乙丙", all.join("/"));

    await p.goto(`${BASE}/reminders`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await sleep(3000);
    await openList(p, "全部");
    check("列表里也是 甲 乙 丙", (await listTitles(p)).join("") === "甲乙丙");

    // ---------- 2) 进排序模式 ----------
    check("有「排序」入口", await clickText(p, ".rem-toolrow", "排序"));
    await sleep(400);
    check("排序行也是 甲 乙 丙", (await rowTitles(p)).join("") === "甲乙丙");

    // 把「丙」拖到最前：起手在第三行把手上，落到第一行中间
    const rows = await p.$$(".rem-sortrow");
    const grips = await p.$$(".rem-sortrow .rem-grip");
    const g2 = await grips[2].boundingBox();
    const r0 = await rows[0].boundingBox();
    await p.mouse.move(g2.x + g2.width / 2, g2.y + g2.height / 2);
    await p.mouse.down();
    await p.mouse.move(r0.x + r0.width / 2, r0.y + r0.height / 2);
    await sleep(200);
    await p.mouse.up();
    await sleep(300);
    check("拖动后本地顺序变成 丙 甲 乙", (await rowTitles(p)).join("") === "丙甲乙");

    check("点「完成」保存", await clickText(p, ".rem-selbar", "完成"));
    await sleep(1500);
    const afterDrag = await titles(p, "all");
    check(
      "接口里顺序变成 丙 甲 乙（手动序压过优先级）",
      afterDrag.join("") === "丙甲乙",
      afterDrag.join("/")
    );

    // ---------- 3) 刷新后还在 ----------
    await p.reload({ waitUntil: "domcontentloaded" });
    await sleep(2500);
    await openList(p, "全部");
    check("刷新后顺序仍是 丙 甲 乙", (await listTitles(p)).join("") === "丙甲乙");

    // ---------- 4) 键盘 ----------
    check("再进排序模式", await clickText(p, ".rem-toolrow", "排序"));
    await sleep(400);
    const beforeKey = (await rowTitles(p)).join("");
    const focused = await p.evaluate(() => {
      const g = document.querySelector(".rem-sortrow .rem-grip");
      if (!g) return "no-grip";
      g.focus();
      return document.activeElement.className;
    });
    await p.keyboard.press("ArrowDown");
    await sleep(250);
    const afterKeyRows = (await rowTitles(p)).join("");
    check(
      "↓ 把第一条换到第二位",
      afterKeyRows === "甲丙乙",
      `before=${beforeKey} focused=${focused} after=${afterKeyRows}`
    );
    await clickText(p, ".rem-selbar", "完成");
    await sleep(1500);
    const afterKey = await titles(p, "all");
    check("键盘调整也发出去了", afterKey.join("") === "甲丙乙", afterKey.join("/"));

    // ---------- 5) 多选 ----------
    await openList(p, "全部");
    await clickText(p, ".rem-toolrow", "多选");
    await sleep(400);
    check("刚进多选时没有底部操作条", (await p.$(".rem-bulkbar")) === null);

    // 点前两张卡（选择模式下点标题 = 选中）
    await p.evaluate(() => {
      const els = [...document.querySelectorAll(".rem-task .rem-title")];
      els[0].click();
      els[1].click();
    });
    await sleep(400);
    const barText = await p.$eval(".rem-bulkbar", (el) => el.textContent.trim()).catch(() => "");
    check("选中 2 条后出现操作条并显示 2", barText.startsWith("2"), barText);

    // 另建一条带提醒的，验证批量完成会停用联动提醒
    const withRemind = await mk("丁", 0, {
      remind_at: Math.floor(Date.now() / 1000) + 7200,
    });
    check("带提醒的待办建好了", !!withRemind.id);
    await p.reload({ waitUntil: "domcontentloaded" });
    await sleep(2500);
    await openList(p, "全部");
    await clickText(p, ".rem-toolrow", "多选");
    await sleep(300);
    await p.evaluate(() => {
      const els = [...document.querySelectorAll(".rem-task .rem-title")];
      els.find((e) => e.textContent.trim() === "甲")?.click();
      els.find((e) => e.textContent.trim() === "丁")?.click();
    });
    await sleep(400);
    await p.evaluate(() => document.querySelector(".rem-bulkbar .rem-bulkbtn[data-primary]").click());
    await sleep(1800);
    const done = await titles(p, "completed");
    check("批量完成：甲、丁 都进了已完成", done.includes("甲") && done.includes("丁"), done.join("/"));
    const t4 = (await api(p, `/api/tasks/${withRemind.id}`)) || {};
    check(
      "批量完成后联动提醒被停用（不会「打勾了还在响」）",
      t4.status === "done" && t4.reminder && t4.reminder.enabled === false,
      JSON.stringify(t4.reminder)
    );

    // ---------- 6) 批量改清单 ----------
    await openList(p, "全部");
    await clickText(p, ".rem-toolrow", "多选");
    await sleep(300);
    await p.evaluate(() => {
      [...document.querySelectorAll(".rem-task .rem-title")]
        .find((e) => e.textContent.trim() === "丙")
        ?.click();
    });
    await sleep(300);
    await p.select(".rem-bulkmove select", "course");
    await sleep(200);
    await clickText(p, ".rem-bulkbar", "移动");
    await sleep(1500);
    const moved = (await titles(p, "all")).length >= 0 ? await api(p, "/api/tasks?scope=all") : null;
    const bing = (moved?.items ?? []).find((t) => t.title === "丙");
    check("批量改清单真的改了 list_key", bing && bing.list_key === "course", bing?.list_key);

    // ---------- 7) 批量删除必须确认 ----------
    await openList(p, "全部");
    await clickText(p, ".rem-toolrow", "多选");
    await sleep(300);
    await p.evaluate(() => {
      [...document.querySelectorAll(".rem-task .rem-title")]
        .find((e) => e.textContent.trim() === "乙")
        ?.click();
    });
    await sleep(300);
    await clickText(p, ".rem-bulkbar", "删除");
    await sleep(500);
    const confirmTitle = await p.$eval(".rem-confirm h3", (el) => el.textContent.trim()).catch(() => "");
    check("批量删除弹出了确认框", confirmTitle.includes("删除选中的"), confirmTitle);
    await clickText(p, ".rem-confirm", "取消");
    await sleep(600);
    check("取消后乙还在", (await titles(p, "all")).includes("乙"));

    await clickText(p, ".rem-bulkbar", "删除");
    await sleep(500);
    await p.evaluate(() => document.querySelector(".rem-confirm .rem-danger").click());
    await sleep(1800);
    const rest = await titles(p, "all");
    check("确认后乙真的没了", !rest.includes("乙"), rest.join("/"));

    // ---------- 8) 收尾 ----------
    console.log(`\n${pass} passed, ${fails.length} failed`);
    if (fails.length) {
      fails.forEach((f) => console.log("  - " + f));
      process.exitCode = 1;
    }
    void c;
  } catch (e) {
    console.error("脚本异常：", e);
    process.exitCode = 2;
  } finally {
    await b.close();
  }
})();
