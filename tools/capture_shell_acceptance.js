/**
 * 一句话整理（v13 工具型 · Phase A）的端到端验收。
 *
 * 跑法：
 *   cp tools/capture_shell_acceptance.js .workbuddy/pp/_cap.js
 *   cd .workbuddy/pp && node _cap.js
 *
 * 为什么不用截图比对：这一版**还没有后端**，所以"画得对不对"只能靠眼睛，
 * 而真正会出错的是眼睛看不见的几件事：
 *   1) 结果态是不是"一打开就存在"（条件挂载错了，收起时它也在 DOM 里）；
 *   2) 点发送的那一瞬间，结果是不是已经跳出来了（顺序错了，用户看不到"正在整理"）；
 *   3) 示例句是不是偷偷提交了（设计稿最容易实现错的一条）；
 *   4) 全不选时主按钮还能不能点（能点就等于允许"加入 0 条"）；
 *   5) 底栏是不是真的贴着底（v13 的承重结构就是它；飘起来就退回"一张纸"了）。
 *
 * v13 换了三层结构（顶部步骤条 / 中间独立滚动 / 底部固定 composer），
 * 所以断言也从"纸的留白"换成了"段与段的关系"：dock 必须贴底、body 必须能独立滚、
 * 顶部三行必须在不滚的情况下始终可见。
 */

const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const BASE = "http://localhost:3100";
const USER = { username: "cap_probe", password: "Baomi@2026test" };
const OUT = path.resolve(__dirname, "shots");

// 这句话是照着"说话颠三倒四"造的，每一段都在考一件事：
//   「呃」开头            → 口水句不能变成条目
//   「三点…不对，下午四点」→ 改口只留最后一次
//   「然后那个」          → 句中口水词不能进标题
//   「哦对了」            → 同上
//   「取件码 8-2-3012」   → 附注挂到上一条（取快递），不单独成事
//   体检排在房租前面      → 说完之后必须被时间轴重排
const SENTENCE =
  "呃，明天三点拿体检报告，不对，下午四点，今晚交房租，然后那个每周三练瑜伽，哦对了周五前发 PPT，明天取快递，取件码 8-2-3012，下周六闺蜜生日";

let pass = 0;
let fail = 0;
const failures = [];
function check(label, ok, extra) {
  if (ok) {
    pass++;
    console.log("  PASS", label);
  } else {
    fail++;
    failures.push(label);
    console.log("  FAIL", label, extra !== undefined ? JSON.stringify(extra) : "");
  }
}

async function shot(p, name) {
  // 鼠标挪开：.rem-cap-primary:hover 会变金，停在按钮上拍出来的主按钮是错的颜色
  await p.mouse.move(2, 2);
  // dev 角标用 CSS 藏，**不能** remove：「nextjs-portal」是 Next 的运行时宿主，
  // 从 DOM 里摘掉会把整页变成 "This page couldn't load"（本轮踩过）。
  await p.evaluate(() => {
    if (document.getElementById("cap-hide-portal")) return;
    const st = document.createElement("style");
    st.id = "cap-hide-portal";
    st.textContent = "nextjs-portal{display:none !important}";
    document.head.appendChild(st);
  });
  await sleep(120);
  await p.screenshot({ path: path.join(OUT, `${name}.png`) });
  console.log("  shot", name);
}

async function api(p, url, init) {
  return p.evaluate(
    async (u, i) => {
      const r = await fetch(u, { credentials: "include", ...(i || {}) });
      return r.ok ? r.json().catch(() => null) : null;
    },
    url,
    init
  );
}

async function login(p) {
  await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await sleep(800);
  const uid = await p.evaluate(async (u) => {
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
    if (!r.ok) return null;
    const me = await fetch("/api/auth/me", { credentials: "include" });
    return me.ok ? (await me.json()).user?.id : null;
  }, USER);
  // 首次登录会弹"上手说明"，它会盖住整页 —— 直接写它的 localStorage 键关掉
  await p.evaluate((id) => {
    try {
      localStorage.setItem(`baomi.guide.v1.${id ?? ""}`, "1");
    } catch {}
  }, uid);
  return uid;
}

async function dismissOverlays(p) {
  // 「更新公告」和本任务无关。**只能 CSS 藏，不能 remove**：
  // .ann-page 是 React 管理的节点，从 DOM 摘掉之后 React 卸载时会抛
  // "removeChild ... is not a child of this node"，整页变成 couldn't load（本轮踩过）。
  await p.evaluate(() => {
    if (document.getElementById("cap-hide-ann")) return;
    const st = document.createElement("style");
    st.id = "cap-hide-ann";
    st.textContent = ".ann-page{display:none !important}";
    document.head.appendChild(st);
  });
  await sleep(200);
}

/** 造一点"今天有 N 条待办"的上下文：没有它，说明块下面那句就永远是 0 条。 */
async function seed(p) {
  await api(p, "/api/tasks?scope=all");
  const day = new Date();
  day.setHours(23, 59, 59, 0);
  const due = Math.floor(day.getTime() / 1000);
  for (const [title, list_key] of [
    ["交高数作业", "homework"],
    ["取快递", "life"],
  ]) {
    await api(p, "/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, list_key, due_at: due }),
    });
  }
  return { due };
}

async function openDrawer(p) {
  // 页面首屏偶尔还没渲染完，点了个空；重试一次再判失败
  for (let attempt = 0; attempt < 2; attempt++) {
    await p.evaluate(() => document.querySelector(".rem-cap-strip")?.click());
    try {
      await p.waitForSelector(".rem-cap-input", { timeout: 5000 });
      await sleep(450);
      return;
    } catch {
      const dbg = await p.evaluate(() => ({
        strip: !!document.querySelector(".rem-cap-strip"),
        drawer: !!document.querySelector(".rem-cap-drawer"),
        guide: !!document.querySelector(".ann-page"),
        head: document.body.innerText.slice(0, 120).replace(/\n/g, "|"),
      }));
      console.log("  openDrawer 重试前页面：", JSON.stringify(dbg));
      await p.evaluate(() => {
        if (document.getElementById("cap-hide-ann")) return;
        const st = document.createElement("style");
        st.id = "cap-hide-ann";
        st.textContent = ".ann-page{display:none !important}";
        document.head.appendChild(st);
      });
      await sleep(600);
    }
  }
  throw new Error("抽屉打不开（.rem-cap-input 一直没出现）");
}

async function setText(p, value) {
  await p.evaluate((v) => {
    const input = document.querySelector(".rem-cap-input");
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    setter.call(input, v);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, value);
  await sleep(180);
}

/** 一次取齐当前界面的关键几何与文案 */
async function snap(p) {
  return p.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const drawer = q(".rem-cap-drawer");
    const dock = q(".rem-cap-dock");
    const body = q(".rem-cap-body");
    const top = q(".rem-cap-top");
    const rect = (el) => (el ? el.getBoundingClientRect() : null);
    const dRect = rect(drawer);
    const dockRect = rect(dock);
    const topRect = rect(top);
    const bodyRect = rect(body);
    return {
      strip: !!q(".rem-cap-strip"),
      drawer: !!drawer,
      status: drawer?.getAttribute("data-status") ?? null,
      phase: drawer?.getAttribute("data-phase") ?? null,
      step: Number(drawer?.getAttribute("data-step") ?? 0),
      title: q(".rem-cap-title")?.textContent?.trim() ?? null,
      sub: q(".rem-cap-sub")?.textContent?.trim() ?? null,
      // 顶部三行固定：任何时候都得在视口里
      topVisible: !!topRect && topRect.top >= -1 && topRect.bottom > 0,
      // 底栏贴底：dock 的下沿必须贴着抽屉下沿（容差 2px）
      dockBottomGap:
        dockRect && dRect ? Math.round(dRect.bottom - dockRect.bottom) : null,
      dockInView: !!dockRect && dockRect.bottom <= window.innerHeight + 1,
      // 中间必须能独立滚（不是整页滚）
      bodyScrollable: !!body && body.scrollHeight > body.clientHeight,
      bodyOwnsOverflow: !!body && getComputedStyle(body).overflowY === "auto",
      drawerScrollTop: drawer ? Math.round(drawer.scrollTop) : null,
      input: q(".rem-cap-input")?.value ?? null,
      sendDisabled: q(".rem-cap-send")?.disabled ?? null,
      inputFontSize: q(".rem-cap-input")
        ? getComputedStyle(q(".rem-cap-input")).fontSize
        : null,
      steps: [...document.querySelectorAll(".rem-cap-step")].map((s) => s.textContent.trim()),
      currentStep: q('.rem-cap-step[data-current="1"]')?.textContent?.trim() ?? null,
      summarybar: q(".rem-cap-summarybar")?.textContent?.trim() ?? null,
      route: q(".rem-cap-route")?.textContent?.trim() ?? null,
      srcQuote: q(".rem-cap-srcquote p")?.textContent?.trim() ?? null,
      proc: !!q(".rem-cap-proc"),
      axis: document.querySelectorAll(".rem-cap-axisrow").length,
      primaryText: q(".rem-cap-primary")?.textContent?.trim() ?? null,
      primaryDisabled: q(".rem-cap-primary")?.disabled ?? null,
      linkText: q(".rem-cap-link")?.textContent?.trim() ?? null,
      warnCount: document.querySelectorAll(".rem-cap-warnline").length,
      // 「不提醒」从一行黄字提示改成了每行自己的可点状态词
      nrmdCount: document.querySelectorAll(".rem-cap-nrmd-btn").length,
      nrmdLabels: [...document.querySelectorAll(".rem-cap-nrmd-btn")].map((b) =>
        b.textContent.trim()
      ),
      nrmdPanels: document.querySelectorAll(".rem-cap-nrmd-panel").length,
      chips: document.querySelectorAll(".rem-cap-chips--dock .rem-cap-chip-btn").length,
      // 11-11 第二轮：示例从底栏 chips 搬进了空态卡，卡本身可点。
      // chips 保留采集，用来断言它是 0（不然"又搬回去了"没人拦得住）
      demoCard: !!q(".rem-cap-demo"),
      demoRows: document.querySelectorAll(".rem-cap-demo-row").length,
      // 11-11 第三轮：卡底和抽屉底的**实际计算值**。
      // 用户的原话是「画框和背景色一模一样」，这句话只有比对真实渲染色值才能验，
      // 在 CSS 里断言 var(--memo-bg) != var(--card-bg) 隔了一层 —— 令牌本身也可能同值。
      demoBg: q(".rem-cap-demo")
        ? getComputedStyle(q(".rem-cap-demo")).backgroundColor
        : null,
      drawerBg: drawer ? getComputedStyle(drawer).backgroundColor : null,
      lead: q(".rem-cap-lead")?.textContent?.trim() ?? null,
      // 去 AI 味：抽屉里不许有头像 / 聊天气泡 / 第一人称旁白
      hasAvatar: !!q(".rem-cap-drawer img, .rem-cap-drawer .chat-avatar"),
      bubble: !!q(".rem-cap-user, .rem-cap-chatbubble"),
      firstPerson: /我听到|我先把|我在整理|我拿不准|我建议/.test(drawer?.innerText ?? ""),
      sparkCount: document.querySelectorAll(".rem-cap-drawer .rem-cap-spark").length,
      width: dRect ? Math.round(dRect.width) : null,
      vh: window.innerHeight,
      scrollX: Math.round(document.documentElement.scrollWidth - window.innerWidth),
    };
  });
}

(async () => {
  if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: "new",
    args: ["--no-sandbox"],
  });

  try {
    const p = await browser.newPage();
    p.on("pageerror", (e) => console.log("  !! PAGEERROR", e.message.split("\n")[0]));
    p.on("console", (m) => {
      if (m.type() === "error" && !/Failed to load resource/.test(m.text()))
        console.log("  !! CONSOLE", m.text().slice(0, 200));
    });
    await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
    const uid = await login(p);
    if (!uid) throw new Error("登录失败，后面的断言没有意义");
    console.log("uid", uid);
    await seed(p);

    for (const [tag, w, h] of [
      ["390", 390, 844],
      ["1440", 1440, 900],
    ]) {
      await p.setViewport({ width: w, height: h, deviceScaleFactor: tag === "390" ? 2 : 1 });
      await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "domcontentloaded" });
      await sleep(2600);
      await dismissOverlays(p);
      await p.evaluate(() => {
        document.querySelector(".rem-cap-strip")?.scrollIntoView({ block: "center" });
      });
      await sleep(400);

      // ---------- 入口 ----------
      let s = await snap(p);
      check(`${tag} 页面上有「一句话添加」入口`, s.strip, s);
      await shot(p, `cap_strip_${tag}_light`);

      // ---------- 打开：只能看到输入态 ----------
      await openDrawer(p);
      s = await snap(p);
      check(`${tag} 抽屉已打开`, s.drawer, s);
      check(`${tag} 打开时是输入态（结果区条件挂载）`, s.status === "idle" && s.axis === 0, s);
      check(`${tag} 输入态没有结果底栏`, s.primaryText === null, s);
      check(`${tag} 标题是「整理成待办和提醒」`, s.title === "整理成待办和提醒", s.title);
      // 11-11 第二轮：输入态不再有第二行说明。
      // 原来那句「自动拆成待办和提醒；确认后才会加入」正被下面那张演示卡演着，
      // 再写一遍就是同一句话讲两遍。空态靠"主张句 + 三行预演"说话，不靠一句功能说明。
      check(`${tag} 输入态不再挂说明句`, s.sub === null, s.sub);
      check(
        `${tag} 顶部没有步骤条（三步是文档的心智，不是用户要读的）`,
        s.steps.length === 0,
        s.steps
      );
      check(`${tag} 空输入时发送 disabled`, s.sendDisabled === true, s);
      const fsOk = tag === "390" ? parseFloat(s.inputFontSize) >= 16 : parseFloat(s.inputFontSize) >= 14;
      check(`${tag} 输入框字号 ${s.inputFontSize}`, fsOk, s.inputFontSize);
      check(`${tag} 无横向滚动（${s.scrollX}px）`, s.scrollX <= 0, s.scrollX);
      check(`${tag} 顶栏在视口内`, s.topVisible === true, s);

      // v13 的承重结构：输入栏贴底 + 中间独立滚动。它飘起来就退回"一张纸"了。
      check(`${tag} 底栏贴着抽屉底（差 ${s.dockBottomGap}px）`, s.dockBottomGap === 0, s.dockBottomGap);
      check(`${tag} 底栏在视口内`, s.dockInView === true, s);
      check(`${tag} 中间区自己吃滚动`, s.bodyOwnsOverflow === true, s);
      check(`${tag} 抽屉本身不滚（滚的是中间）`, s.drawerScrollTop === 0, s.drawerScrollTop);

      // 去 AI 味（P0）：头像 / 气泡 / 第一人称 / 满屏 spark
      check(`${tag} 抽屉里没有头像`, s.hasAvatar === false, s);
      check(`${tag} 抽屉里没有聊天气泡`, s.bubble === false, s);
      check(`${tag} 抽屉里没有第一人称旁白`, s.firstPerson === false, s);
      check(`${tag} 抽屉里 spark 数量为 0`, s.sparkCount === 0, s.sparkCount);
      // 空态：一句主张 + 一张三行的预演卡，底栏不再有第二排示例句
      check(`${tag} 空态有主张句`, (s.lead ?? "") === "想到哪说到哪", s.lead);
      check(`${tag} 空态预演卡是三行`, s.demoRows === 3, s.demoRows);
      check(`${tag} 底栏不再挂示例 chips`, s.chips === 0, s.chips);
      // 「画框和背景色一模一样」—— 用户说的就是这两个值相等。
      // 比的是渲染后的 computed color，不是 CSS 里的令牌名：令牌名不同也可能同值。
      check(
        `${tag} 卡底和抽屉底不是同一个颜色`,
        !!s.demoBg && !!s.drawerBg && s.demoBg !== s.drawerBg,
        { 卡: s.demoBg, 抽屉: s.drawerBg }
      );
      // 而且要偏冷。只调明度救不了同色相 —— 这条盯的是色相本身（蓝通道 > 红通道）
      const demoRgb = (s.demoBg ?? "").match(/\d+/g)?.map(Number) ?? [];
      check(
        `${tag} 卡底是冷色（蓝通道高于红通道）`,
        demoRgb.length === 3 && demoRgb[2] > demoRgb[0] + 8,
        s.demoBg
      );
      await shot(p, `cap_input_${tag}_light`);

      if (tag === "1440") check("1440 抽屉宽 520", s.width === 520, s.width);
      if (tag === "390") check("390 抽屉占满宽度", s.width === 390, s.width);

      // ---------- 示例卡：只填，不提交 ----------
      const filled = await p.evaluate(() => {
        const card = document.querySelector(".rem-cap-demo");
        card?.click();
        return true;
      });
      await sleep(300);
      s = await snap(p);
      check(`${tag} 点示例卡后输入框被填上`, (s.input ?? "").includes("交高数作业"), {
        input: s.input,
        filled,
      });
      check(`${tag} 示例卡没有自动提交`, s.status === "idle" && s.axis === 0, s);
      check(`${tag} 填了字之后示例卡收起`, s.demoCard === false, s.demoCard);
      await shot(p, `cap_typing_${tag}_light`);

      // ---------- 发送的瞬间：结果必须还不在 ----------
      await setText(p, SENTENCE);
      s = await snap(p);
      check(`${tag} 填了字发送可用`, s.sendDisabled === false, s);

      // 同步读 DOM 读不到：React 的 setState 提交发生在事件派发之后。所以逐帧抓，
      // 只要在「结果态出现」之前抓到过「原话卡 + 整理中」，就说明中间态是真画出来的。
      const instant = await p.evaluate(async () => {
        document.querySelector(".rem-cap-send")?.click();
        for (let i = 0; i < 60; i += 1) {
          await new Promise((r) => requestAnimationFrame(r));
          const now = {
            ready: document.querySelector(".rem-cap-drawer")?.getAttribute("data-status") === "ready",
            quote: document.querySelector(".rem-cap-srcquote p")?.textContent ?? null,
            proc: !!document.querySelector(".rem-cap-proc"),
            // 整理中输入栏不许撤：撤了下半屏空一大块、输入框还跳了位置
            dock: !!document.querySelector(".rem-cap-dock"),
            inputLocked: document.querySelector(".rem-cap-input")?.disabled ?? null,
            frame: i,
          };
          if (now.proc || now.ready) return now;
        }
        return { ready: false, quote: null, proc: false, dock: null, inputLocked: null, frame: -1 };
      });
      check(`${tag} 出结果之前结果态还不存在`, instant.ready === false, instant);
      check(`${tag} 出结果之前先有「你说的」`, (instant.quote || "").includes("体检"), instant);
      check(`${tag} 出结果之前先有「正在整理…」`, instant.proc === true, instant);
      check(`${tag} 整理中输入栏还在原位（不撤、不跳）`, instant.dock === true, instant.dock);
      check(`${tag} 整理中输入框锁住`, instant.inputLocked === true, instant.inputLocked);
      check(
        `${tag} 整理中的文案不是第一人称`,
        !/我在整理|我先把/.test(instant.quote || ""),
        instant.quote
      );

      // ---------- 结果态：一条按时间排好的轴 ----------
      await sleep(900);
      s = await snap(p);
      check(`${tag} 结果态出现`, s.status === "ready", s);
      check(`${tag} 整理中已收起`, s.proc === false, s);
      check(`${tag} 说出来 6 件事就 6 条`, s.axis === 6, s.axis);
      check(`${tag} 结果底栏出现`, s.primaryText !== null, s);
      check(`${tag} 标题改成「确认这 6 条」`, s.title === "确认这 6 条", s.title);
      check(
        `${tag} 汇总条写清两类对象（${s.summarybar}）`,
        (s.summarybar || "").includes("条待办") && (s.summarybar || "").includes("个日程"),
        s.summarybar
      );

      const axis = await p.evaluate(() =>
        [...document.querySelectorAll(".rem-cap-axisrow")].map((r) => ({
          at: r.getAttribute("data-at"),
          day: r.querySelector(".rem-cap-axisday")?.textContent?.trim() ?? null,
          time: r.querySelector(".rem-cap-axistime")?.textContent?.trim() ?? null,
          title: r.querySelector(".rem-cap-axisttl > span")?.textContent?.trim() ?? null,
          no: r.querySelector(".rem-cap-no")?.textContent?.trim() ?? null,
          note: r.querySelector(".rem-cap-axisnote")?.textContent?.trim() ?? null,
          flag: r.querySelector(".rem-cap-flag")?.textContent?.trim() ?? null,
          flagTone: r.querySelector(".rem-cap-flag")?.getAttribute("data-tone") ?? null,
        }))
      );

      const ats = axis.map((r) => Number(r.at));
      check(
        `${tag} 轴按时间升序（${axis.map((r) => r.day).join(" → ")}）`,
        ats.every((v, i) => i === 0 || v >= ats[i - 1]),
        ats
      );
      // 说话顺序是 体检(1) → 房租(2) → 瑜伽(3)…，排完必须不是这个顺序 ——
      // 这是"说话颠三倒四，它按时间排好了"唯一能被机器验的形态。
      //
      // 不断言"第 2 句一定排到第 1 位"：房租是「今晚 23:59」，瑜伽是「每周三 20:00」。
      // 周三跑这条时瑜伽先到，周二跑时房租先到 —— 那不是回归，是星期几。
      // 第一版把它写死成"房租第一"，于是每天换一天就红一次（2026-11-11 踩到）。
      const noOrder = axis.map((r) => r.no).join("");
      check(`${tag} 结果被时间重排过，不是说的顺序`, noOrder !== "123456", noOrder);
      const atsMs = axis.map((r) => Number(r.at)).filter((v) => Number.isFinite(v));
      check(
        `${tag} 第一行是全部条目里最早到点的那条`,
        atsMs.length > 0 && Number(axis[0]?.at) === Math.min(...atsMs),
        [axis[0]?.at, Math.min(...atsMs)]
      );
      check(
        `${tag} 改口生效：只记下午四点，不记三点`,
        axis.some((r) => r.time === "16:00") && !axis.some((r) => r.time === "03:00"),
        axis.map((r) => r.time)
      );

      const days = axis.map((r) => r.day).filter((d) => d !== null);
      check(
        `${tag} 同一天只在第一行写日期（${days.length} 个锚点不重复）`,
        days.length === new Set(days).size,
        days
      );
      check(`${tag} 第一行有日期锚点`, !!axis[0]?.day, axis[0]);
      check(
        `${tag} 口水词没有变成条目`,
        !axis.some((r) => /呃|然后|哦对了|不对/.test(r.title || "")),
        axis.map((r) => r.title)
      );
      check(
        `${tag} 取件码挂在取快递这条上（不单独成事）`,
        (axis.find((r) => (r.title || "").includes("取快递"))?.note || "").includes("8-2-3012"),
        axis.map((r) => r.note)
      );

      // v13：每条结果都要有状态标签，且用「可直接加入 / 需要确认 / 缺少时间」，不许用"我拿不准"
      check(
        `${tag} 每条都有状态标签`,
        axis.every((r) => r.flag !== null),
        axis.map((r) => r.flag)
      );
      check(
        `${tag} 状态标签用「可直接加入」这类事实词`,
        axis.every((r) => /可直接加入|需要确认|缺少时间/.test(r.flag || "")),
        axis.map((r) => r.flag)
      );

      // 不变量 #1：只设截止不会响。上一版是整屏一行黄字提示、用户读完还是不懂；
      // 现在挂在那行自己身上：状态词「不提醒」，点开才展开后果说明。
      check(`${tag} 旧的整屏黄字提示已撤掉`, s.warnCount === 0, s.warnCount);
      check(
        `${tag} 没有提醒的待办各自挂一个「不提醒」`,
        s.nrmdCount > 0 && s.nrmdLabels.every((t) => t.startsWith("不提醒")),
        s.nrmdLabels
      );
      check(`${tag} 默认不展开`, s.nrmdPanels === 0, s.nrmdPanels);

      await p.click(".rem-cap-nrmd-btn");
      const opened = await p.evaluate(() => {
        const btn = document.querySelector(".rem-cap-nrmd-btn");
        const panel = document.querySelector(".rem-cap-nrmd-panel");
        const dock = document.querySelector(".rem-cap-dock");
        return {
          expanded: btn?.getAttribute("aria-expanded") ?? null,
          controls: btn?.getAttribute("aria-controls") ?? null,
          panelId: panel?.id ?? null,
          panels: document.querySelectorAll(".rem-cap-nrmd-panel").length,
          text: panel?.textContent?.trim() ?? null,
          // 展开的那块必须在 dock 上方看得见，不能被压住
          inView: panel
            ? panel.getBoundingClientRect().bottom <=
              (dock?.getBoundingClientRect().top ?? 1e9) + 1
            : false,
        };
      });
      check(`${tag} 点开「不提醒」→ aria-expanded 翻转`, opened.expanded === "true", opened.expanded);
      check(
        `${tag} 点开只说后果和去处，不用「截止」这类词`,
        /不会弹窗/.test(opened.text || "") &&
          /列表/.test(opened.text || "") &&
          !/截止|完成语义|联动/.test(opened.text || ""),
        opened.text
      );
      // 读屏靠 aria-controls 找说明块：写成自证（比自己）等于没验
      check(
        `${tag} 说明块的 id 就是按钮 aria-controls 指的那一个`,
        !!opened.controls && opened.controls === opened.panelId,
        [opened.controls, opened.panelId]
      );
      check(`${tag} 同时只展开一行`, opened.panels === 1, opened.panels);
      check(`${tag} 展开的说明完整落在 dock 上方`, opened.inView, opened.inView);

      await shot(p, `cap_result_${tag}_nrmd`);

      await p.click(".rem-cap-nrmd-btn");
      const collapsed = await p.evaluate(() => ({
        expanded: document.querySelector(".rem-cap-nrmd-btn")?.getAttribute("aria-expanded"),
        panels: document.querySelectorAll(".rem-cap-nrmd-panel").length,
      }));
      check(
        `${tag} 再点一次收起`,
        collapsed.expanded === "false" && collapsed.panels === 0,
        collapsed
      );

      // 后果写清：有提醒就写提醒数
      check(
        `${tag} 主按钮写清条数与提醒数（${s.primaryText}）`,
        /加入 6 条/.test(s.primaryText || "") && /3 条提醒/.test(s.primaryText || ""),
        s.primaryText
      );

      await shot(p, `cap_result_${tag}_light`);

      // ---------- 点编号 → 原话里那一句点亮 ----------
      // 按编号找，不按位置：第一行今天是瑜伽还是房租取决于星期几，
      // 写死 `querySelector(".rem-cap-no")` 会在换一天之后点到别的条目上。
      await p.evaluate(() => {
        const all = [...document.querySelectorAll(".rem-cap-no")];
        (all.find((b) => (b.textContent || "").trim() === "2") ?? all[0])?.click();
      });
      await sleep(900);
      const lit = await p.evaluate(() =>
        [...document.querySelectorAll(".rem-cap-srcmark")].map((m) => ({
          no: (m.querySelector(".rem-cap-srcno")?.textContent ?? "").trim(),
          on: m.getAttribute("data-on") === "1",
          text: (m.textContent ?? "").replace(/\s+/g, ""),
        }))
      );
      const litOnes = lit.filter((m) => m.on);
      check(`${tag} 原话里有 6 个编号`, lit.length === 6, lit.length);
      check(`${tag} 只亮被点的那一句`, litOnes.length === 1 && litOnes[0].no === "2", lit);
      check(
        `${tag} 亮的是「今晚交房租」那句原话`,
        (litOnes[0]?.text ?? "").includes("交房租"),
        litOnes[0]
      );
      await shot(p, `cap_result_${tag}_link`);

      // ---------- 勾选联动 ----------
      await p.evaluate(() => document.querySelector(".rem-cap-axisrow .rem-cap-nodewrap")?.click());
      await sleep(250);
      const afterOne = await p.evaluate(
        () => document.querySelector(".rem-cap-primary")?.textContent?.trim() ?? null
      );
      check(
        `${tag} 取消一条后主按钮文案少一条`,
        /加入 5 条/.test(afterOne || ""),
        afterOne
      );

      const linkLabel = await p.evaluate(() => {
        const l = document.querySelector(".rem-cap-link");
        const t = l?.textContent?.trim() ?? null;
        l?.click();
        return t;
      });
      await sleep(250);
      s = await snap(p);
      check(`${tag} 部分选中时开关显示「全选」`, linkLabel === "全选", linkLabel);
      check(`${tag} 点「全选」后主按钮恢复可用（${s.primaryText}）`, s.primaryDisabled === false, s);

      await p.evaluate(() => document.querySelector(".rem-cap-link")?.click());
      await sleep(250);
      s = await snap(p);
      check(`${tag} 全不选后主按钮 disabled`, s.primaryDisabled === true, s);
      check(
        `${tag} 全不选时按钮说的是「至少选一条」`,
        /至少选一条/.test(s.primaryText || ""),
        s.primaryText
      );

      // 真鼠标点：disabled 按钮会吞掉点击。不用合成事件 —— React 对 disabled 元素上的合成 click
      // 直接过滤，那种测法等于空转（反证时实测过：去掉 commit 守卫它也照样绿）。
      const primaryBox = await p.evaluate(() => {
        const b = document.querySelector(".rem-cap-primary");
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      });
      if (primaryBox) await p.mouse.click(primaryBox.x, primaryBox.y);
      await sleep(250);
      s = await snap(p);
      check(`${tag} 全不选：点主按钮不关抽屉`, s.drawer === true && s.status === "ready", {
        box: primaryBox,
        drawer: s.drawer,
        status: s.status,
      });

      // ---------- Esc 关闭 + 重开复位 ----------
      await p.keyboard.press("Escape");
      await sleep(400);
      s = await snap(p);
      check(`${tag} Esc 能关掉抽屉`, s.drawer === false, s);

      await openDrawer(p);
      s = await snap(p);
      check(
        `${tag} 重开后标题回到「整理成待办和提醒」`,
        s.title === "整理成待办和提醒",
        s.title
      );
      check(`${tag} 重开后输入是空的`, s.input === "", s.input);
      check(`${tag} 重开后结果态不存在`, s.status === "idle" && s.axis === 0, s);

      // ---------- 语音：有服务就真按钮，没服务就不存在 ----------
      // 2026-11-11：ASR 接通之后，旧断言「点了应该告诉你还没接」已经过时 ——
      // 现在的正确行为是"真的开始录"，那条链路由 tools/voice_ui_e2e.js 的 14 条专测
      // （含假麦克风、失败态、权限拒绝）。这里改守总闸：
      // **麦克风在不在，必须跟后端能力一致**。没服务却摆一个按钮，就是半成品感。
      const voiceCap = await p.evaluate(async () => {
        try {
          const r = await fetch("/api/capture/capabilities", { credentials: "include" });
          return (await r.json()).voice === true;
        } catch {
          return false;
        }
      });
      // 这里**不点**：真录音会把输入框锁住（voice.phase !== idle），后面所有用例都得跟着改，
      // 而"点了会怎样"在 voice_ui_e2e 里已经测过了。
      const mic = await p.evaluate(() => {
        const el = document.querySelector(".rem-cap-mic");
        return { has: !!el, disabled: el?.disabled === true };
      });
      check(`${tag} 麦克风是否存在，跟后端能力一致`, mic.has === voiceCap, { mic, voiceCap });
      if (voiceCap) {
        check(`${tag} 接了转写，麦克风可点（不是灰的假按钮）`, mic.disabled === false, mic);
      }
      const fakeListening = await p.evaluate(
        () =>
          !!document.querySelector(".is-listening") ||
          document.body.innerText.includes("正在听") ||
          document.body.innerText.includes("语音输入还没接")
      );
      check(`${tag} 没动它就不该有任何「在听」的迹象`, fakeListening === false, fakeListening);

      // ---------- 0 条 ----------
      await setText(p, "呃，然后那个，哦对了");
      await p.evaluate(() => document.querySelector(".rem-cap-send")?.click());
      await sleep(1000);
      s = await snap(p);
      check(`${tag} 全是口水句时 0 条`, s.axis === 0, s.axis);
      check(`${tag} 0 条时标题说「没有整理出安排」`, s.title === "没有整理出安排", s.title);
      check(`${tag} 0 条时不挂结果底栏（没有「加入 0 条」）`, s.primaryText === null, s.primaryText);
      check(`${tag} 0 条时给了输入框当出口`, s.input !== null, s);
      const junkTodo = await p.evaluate(() =>
        document.body.innerText.includes("然后那个 待办")
      );
      check(`${tag} 口水句没有变成一条待办`, junkTodo === false, null);
      await shot(p, `cap_zero_${tag}_light`);

      // ---------- 解析失败：原文保留 + 同一个输入框 ----------
      await p.keyboard.press("Escape");
      await sleep(300);
      await openDrawer(p);
      await setText(p, "__demo:fail 明天交作业");
      await p.evaluate(() => document.querySelector(".rem-cap-send")?.click());
      await sleep(900);
      s = await snap(p);
      check(`${tag} 失败态出现`, s.status === "failed", s.status);
      check(`${tag} 失败态标题说「这次没整理出来」`, s.title === "这次没整理出来", s.title);
      check(`${tag} 失败态保留原文`, (s.srcQuote || "").includes("交作业"), s.srcQuote);
      check(`${tag} 失败态把原文填回输入框`, (s.input || "").includes("交作业"), s.input);
      check(`${tag} 失败态的输入框里没有演示前缀`, !(s.input || "").includes("__demo"), s.input);
      check(`${tag} 失败态不挂结果底栏`, s.primaryText === null, s.primaryText);
      await shot(p, `cap_fail_${tag}_light`);

      // ---------- 需确认：要给注意力路由 ----------
      await p.keyboard.press("Escape");
      await sleep(300);
      await openDrawer(p);
      await setText(p, "周五社团开会，明天交高数作业，今晚取快递");
      await p.evaluate(() => document.querySelector(".rem-cap-send")?.click());
      await sleep(1000);
      s = await snap(p);
      const hasConfirm = await p.evaluate(
        () => document.querySelectorAll('.rem-cap-flag[data-tone="ask"]').length
      );
      check(`${tag} 「周五开会」被标成需要确认`, hasConfirm >= 1, s);
      check(
        `${tag} 有需确认时出现「先处理 N 条需确认 ↓」`,
        /先处理 \d+ 条需确认/.test(s.route || ""),
        s.route
      );
      // 判据不是"必须滚了"——1440 上内容本来就放得下、scrollTop 一直是 0。
      // 真正要钉的是：点完之后那条（含追问卡）完整落在整理区的可视范围内。
      const scrolled = await p.evaluate(async () => {
        const row = document.querySelector('[data-first-confirm="1"]');
        const body = document.querySelector(".rem-cap-body");
        const before = body?.scrollTop ?? 0;
        document.querySelector(".rem-cap-route")?.click();
        await new Promise((r) => setTimeout(r, 900));
        const r = row?.getBoundingClientRect();
        const b = body?.getBoundingClientRect();
        return {
          before,
          after: body?.scrollTop ?? 0,
          visible: !!r && !!b && r.top >= b.top - 1 && r.bottom <= b.bottom + 1,
          rowBottom: r ? Math.round(r.bottom) : null,
          bodyBottom: b ? Math.round(b.bottom) : null,
        };
      });
      check(
        `${tag} 点它之后需确认那条完整落在整理区里`,
        scrolled.visible === true,
        scrolled
      );
      await shot(p, `cap_confirm_${tag}_light`);

      // ---------- 部分失败 ----------
      await p.keyboard.press("Escape");
      await sleep(300);
      await openDrawer(p);
      await setText(p, "__demo:partial 明天交高数作业，周五社团开会，每晚背单词");
      await p.evaluate(() => document.querySelector(".rem-cap-send")?.click());
      await sleep(1000);
      await p.evaluate(() => document.querySelector(".rem-cap-primary")?.click());
      await sleep(800);
      s = await snap(p);
      const failedRows = await p.evaluate(
        () => document.querySelectorAll('.rem-cap-flag[data-tone="bad"]').length
      );
      check(`${tag} 部分失败时失败项被标出来`, failedRows === 1, failedRows);
      const failBar = await p.evaluate(
        () => document.querySelector(".rem-cap-failbar")?.textContent?.trim() ?? null
      );
      check(
        `${tag} 底栏说清「已加入 X 条，Y 条没加上」`,
        /已加入 \d+ 条，\d+ 条没加上/.test(failBar || ""),
        failBar
      );
      check(`${tag} 部分失败时抽屉不关（失败项还能重试）`, s.drawer === true, s.drawer);
      await shot(p, `cap_partial_${tag}_light`);
    }
  } finally {
    await browser.close();
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  if (fail > 0) {
    console.log("FAILED:", failures.join(" | "));
    process.exit(1);
  }
})();
