// 四屏（delegate/report/checkin/settings）+ flow 的「登录门 + 数据加载」冒烟验证
//
// 背景（2026-10-14 晚发现的真 bug，本轮修复）：
//   四屏的页面组件自己持有数据加载 effect，而登录门在 ConsoleShell 那一层。
//   页面 effect 挂在页面组件上（不受外壳分支影响），未登录打开时会照常请求
//   → 全部 401 → 页面进入降级/错误态；而「登录完成」发生在 ConsoleShell 自己的
//   useAuth 实例里，页面感知不到 —— 登录后界面一直停在登录前的失败结果上，
//   必须手动刷新才恢复。修复：api.ts 增加 AUTHORIZED_EVENT（登录/注册成功广播，
//   与 401 的 UNAUTHORIZED_EVENT 对称）+ 页面 effect 增加 auth 守卫并依赖 auth.me。
//
// 本脚本验证三件事：
//   ① 未登录打开四屏不发业务请求（守卫生效，登录前零噪音）
//   ② 就地登录后（不刷新）页面自动补加载（AUTHORIZED_EVENT 生效）
//   ③ 逐屏导航：各屏渲染、无 401、数据请求到达后端、无 JS 错误
//
// 端口说明：本机 3000 被 VMware NAT 服务（vmnat.exe）独占，前端 dev 用 3100。
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.BAOMI_BASE || "http://localhost:3100";
const USER = process.env.BAOMI_USER || "flowuitest";
const PASS = process.env.BAOMI_PASS || "FlowTest2026x";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let pass = 0, fail = 0;
const fails = [];
const check = (n, c, e = "") => {
  if (c) { pass++; console.log(`PASS  ${n} ${e}`); }
  else { fail++; fails.push(n); console.log(`FAIL  ${n} ${e}`); }
};

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ["--no-sandbox", "--no-proxy-server", "--proxy-bypass-list=*", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  const apiCalls = [];
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("/api/")) apiCalls.push({ status: r.status(), path: u.replace(BASE, ""), t: Date.now() });
  });

  // ================= ① 未登录打开 /delegate：登录门 + 零业务请求 =================
  await page.goto(`${BASE}/delegate`, { waitUntil: "networkidle2", timeout: 90000 });
  const hasPwd = await page.waitForSelector('input[type="password"]', { timeout: 20000 })
    .then(() => true).catch(() => false);
  check("未登录打开 /delegate 出现登录门", hasPwd);

  const bizBefore = apiCalls.filter((c) => !c.path.startsWith("/api/auth"));
  check("★ 未登录不发业务请求（守卫生效）", bizBefore.length === 0,
    bizBefore.slice(0, 3).map((c) => `${c.status} ${c.path}`).join(" | "));

  // ================= ② 就地登录（不刷新）→ 页面自动补加载 =================
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
  await sleep(4000);

  const loginIdx = apiCalls.findIndex((c) => c.path.startsWith("/api/auth/login") && c.status === 200);
  check("就地登录成功（login 200）", loginIdx >= 0);
  const afterLogin = loginIdx >= 0 ? apiCalls.slice(loginIdx + 1) : [];
  const bizAfter = afterLogin.filter((c) => !c.path.startsWith("/api/auth"));
  check("★ 登录后（不刷新）页面自动补加载（AUTHORIZED_EVENT 生效）",
    bizAfter.some((c) => c.status === 200),
    bizAfter.slice(0, 4).map((c) => `${c.status} ${c.path}`).join(" | "));

  // ================= ③ 逐屏导航冒烟 =================
  const screens = [
    { path: "/delegate", title: "每日委托" },
    { path: "/report", title: "每日总结" },
    { path: "/checkin", title: "打卡" },
    { path: "/settings", title: "设置" },
    { path: "/flow", title: "工作流与切换规则" },
  ];
  for (const s of screens) {
    const before = apiCalls.length;
    await page.goto(BASE + s.path, { waitUntil: "networkidle2", timeout: 90000 });
    await page.waitForFunction(
      (t) => (document.body.innerText || "").includes(t), { timeout: 20000 }, s.title
    ).catch(() => {});
    await sleep(1800);
    const body = await page.evaluate(() => document.body.innerText || "");
    const calls = apiCalls.slice(before);
    const hit401 = calls.filter((c) => c.status === 401);
    const ok = calls.filter((c) => c.status === 200);
    check(`${s.path} 渲染出「${s.title}」`, body.includes(s.title), `(${body.trim().length} 字)`);
    check(`${s.path} 无 401（登录态贯穿）`, hit401.length === 0, hit401.slice(0, 2).map((c) => c.path).join(","));
    check(`${s.path} 数据请求到达后端（≥1 个 200）`, ok.length >= 1, `(200×${ok.length})`);
  }

  const real = errors.filter((e) => !/favicon|404 \(Not Found\)|Failed to load resource/i.test(e));
  check("全程无 JS 运行时错误", real.length === 0, real.slice(0, 2).join(" | "));

  await browser.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  if (fails.length) console.log("失败项: " + fails.join(" | "));
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("异常:", e); process.exit(2); });
