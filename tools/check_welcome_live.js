// 欢迎页「场景撤出」改版验证（2026-10-14 晚）
//
// 背景：主界面的三个场景快选（考研复习/课程笔记/论文写作）与模板广场同名模板
// 重复、且覆盖不到「求职」等主打场景 —— 决策整体撤出：欢迎页场景卡与输入框上方
// 场景 chips 全部移除，场景化入口统一走模板广场（/templates）。
//
// 本脚本验证四件事：
//   ① 欢迎区不再有场景相关元素（文本、.scenario-pick、.scenario chips 全为 0）
//   ② 首屏层级完整：三卡 + 四个辅路小胶囊 + 校园工具集外链
//      （三示例按窗口高度分档：≥920 高才出现，矮屏收起 —— 两个高度都验）
//   ③ 发消息请求体不再带 scenario 字段（旧机制彻底断开），且流式有回复
//   ④ 输入框上方只剩校园工具集（撤掉 chips 后这一行不空不乱）
//
// 端口说明：本机 3000 被 VMware NAT 服务（vmnat.exe）独占，前端 dev 用 3100。
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.BAOMI_BASE || "http://localhost:3100";
const USER = process.env.BAOMI_USER || "";
const PASS = process.env.BAOMI_PASS || "";
if (!USER || !PASS) { console.error("缺少 BAOMI_USER / BAOMI_PASS 环境变量（测试账号）"); process.exit(1); }
const SHOT_DIR = process.env.OUT_DIR || "C:/Users/HUAWEI/AppData/Local/Temp/welcome_verify";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let pass = 0, fail = 0;
const fails = [];
const check = (n, c, e = "") => {
  if (c) { pass++; console.log(`PASS  ${n} ${e}`); }
  else { fail++; fails.push(n); console.log(`FAIL  ${n} ${e}`); }
};

(async () => {
  const fs = require("fs");
  fs.mkdirSync(SHOT_DIR, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ["--no-sandbox", "--no-proxy-server", "--proxy-bypass-list=*", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  // 抓 /api/chat/stream 的请求体（验证 scenario 字段已断）
  let chatBody = null;
  page.on("request", (r) => {
    if (r.url().includes("/api/chat/stream") && r.method() === "POST") {
      chatBody = r.postData() || "";
    }
  });

  // ================= 登录 =================
  await page.goto(`${BASE}/`, { waitUntil: "networkidle2", timeout: 90000 });
  const hasPwd = await page.waitForSelector('input[type="password"]', { timeout: 20000 })
    .then(() => true).catch(() => false);
  if (hasPwd) {
    await page.evaluate((u, p) => {
      const ins = [...document.querySelectorAll("input")];
      const set = (el, v) => {
        const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        s.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
      };
      set(ins.find((i) => i.type !== "password"), u);
      set(ins.find((i) => i.type === "password"), p);
      const b = [...document.querySelectorAll("button")].find(
        (x) => (x.textContent || "").trim() === "登录" && !x.closest("[role=tablist]")
      );
      b && b.click();
    }, USER, PASS);
    await sleep(3500);
  }
  // 拿一个空会话才能看到欢迎页：登录后会默认落进最近一个会话（useSessions 的
  // 自动选中），里面已有历史消息 → 不新建的话消息区是聊天视图，三卡/示例断言
  // 会全挂（10-14 首跑踩过：5 条 FAIL 全源于此）。
  await page.waitForSelector("button.btn-new", { timeout: 30000 });
  await page.click("button.btn-new");
  await page.waitForSelector(".entry-card", { timeout: 30000 });
  await sleep(800);

  // ================= ① 场景元素全部消失 =================
  const scene = await page.evaluate(() => ({
    text: document.body.innerText || "",
    pick: document.querySelectorAll(".scenario-pick").length,
    chips: document.querySelectorAll(".scenario").length,
    label: document.querySelectorAll(".scenario-label").length,
  }));
  check("★ 无「选个场景开始」区块", !scene.text.includes("选个场景开始"));
  check("★ .scenario-pick 场景卡 = 0", scene.pick === 0, `(实际 ${scene.pick})`);
  check("★ .scenario chips = 0 且 label = 0", scene.chips === 0 && scene.label === 0,
    `(chips=${scene.chips}, label=${scene.label})`);
  // 查场景卡的 desc 特征串（只在被删的三个场景卡里出现过，别的文案不会误伤：
  // 示例问题里的「课程笔记」是正常引导语，不能拿它当判据 —— 首跑误报过）
  check("场景卡 desc 特征串不再出现（知识点讲解·笔记整理·学术规范）",
    !["知识点讲解 · 真题自测", "笔记整理 · 要点提炼", "学术规范 · 结构搭建"]
      .some((t) => scene.text.includes(t)));
  check("副标题已更新（不含「选个场景」）", scene.text.includes("直接问我"));

  // ================= ② 首屏层级完整 =================
  const layout = await page.evaluate(() => ({
    entryCards: document.querySelectorAll(".entry-card").length,
    entryMinis: document.querySelectorAll(".entry-mini").length,
    examples: document.querySelectorAll(".welcome-eg").length,
    toolsLink: !!document.querySelector('a.tools-link[href="/tools.html"]'),
    primary: [...document.querySelectorAll(".entry-card")].map((x) => x.textContent.trim()),
  }));
  check("三卡（持续陪伴）齐", layout.entryCards === 3, `(实际 ${layout.entryCards})`);
  check("辅路小胶囊 4 个", layout.entryMinis === 4, `(实际 ${layout.entryMinis})`);
  // 2026-10-28 起示例便签按窗口高度分档：只有 ≥920px 高才放出来（矮屏让位给
  // "一屏放全"，见 styles/welcome.css 的两档说明）。这里 900 高，应当是收起的。
  check("三示例（便签）在 900 高窗口收起（分档低档）", layout.examples === 0, `(实际 ${layout.examples})`);
  check("校园工具集外链在（.tools-link → /tools.html）", layout.toolsLink);
  check("三卡内容对（每日委托/每日总结/打卡记录）",
    ["每日委托", "每日总结", "打卡记录"].every((t) => layout.primary.join("｜").includes(t)),
    layout.primary.join("｜"));

  // 分档的两端都要钉：只测"矮屏收起"的话，把示例永久隐藏也能过
  await page.setViewport({ width: 1280, height: 1000 });
  await sleep(500);
  const tallExamples = await page.evaluate(() => document.querySelectorAll(".welcome-eg").length);
  check("★ 高窗口（1000px）三示例回来（分档高档）", tallExamples === 3, `(实际 ${tallExamples})`);
  await page.setViewport({ width: 1280, height: 900 });
  await sleep(400);

  await page.screenshot({ path: `${SHOT_DIR}/welcome-after.png` });

  // ================= ③ 发消息：无 scenario 字段 + 有回复 =================
  await page.evaluate(() => {
    const ta = document.querySelector("textarea");
    const s = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    s.call(ta, "只回复两个字：收到");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.click('button[title="发送"]');
  // 等流式结束：助手气泡出现且有文字（或超时 90s）
  const gotReply = await page.waitForFunction(() => {
    const bubbles = document.querySelectorAll(".bubble-ai");
    return [...bubbles].some((b) => (b.textContent || "").trim().length > 0);
  }, { timeout: 90000 }).then(() => true).catch(() => false);
  check("★ 发送后收到流式回复", gotReply);
  check("★ 请求体不再含 scenario 字段", chatBody !== null && !chatBody.includes("scenario"),
    chatBody === null ? "(未捕获请求)" : chatBody.slice(0, 120));

  // ================= ④ 底部行：只剩校园工具集 =================
  const bottom = await page.evaluate(() => {
    const link = document.querySelector(".tools-link");
    const r = link ? link.getBoundingClientRect() : null;
    return {
      toolsVisible: !!r && r.width > 0,
      chipsAtBottom: document.querySelectorAll(".chat-measure .scenario").length,
    };
  });
  check("输入框上方校园工具集仍在且可见", bottom.toolsVisible);
  check("输入框上方无场景 chips", bottom.chipsAtBottom === 0);

  const real = errors.filter((e) => !/favicon|404 \(Not Found\)|Failed to load resource/i.test(e));
  check("全程无 JS 运行时错误", real.length === 0, real.slice(0, 2).join(" | "));

  await browser.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  if (fails.length) console.log("失败项: " + fails.join(" | "));
  console.log(`截图: ${SHOT_DIR}/welcome-after.png`);
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("异常:", e); process.exit(2); });
