// 四屏落地页的本地实机校验。
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/shot_console.js
//
// 只在本机 dev 跑，注册一个临时账号（cdemoXXXX），不动线上数据。
// 校验项：页面能打开、导航切换生效、演示数据有标记、console 无报错、无 emoji、
//         触控目标达标、明暗两套主题都能渲染。
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:3000";
const OUT = path.resolve(__dirname, "..", "docs", "实机截图", "console-2026-10-13");
const USER = "cdemo" + Math.random().toString(36).slice(2, 7);
const PASS = "demo123456";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[console]", ...a);

/** 四个新页面的校验清单 */
const PAGES = [
  { key: "delegate", path: "/delegate", must: ["每日委托", "我的委托", "执行历史", "新建委托"] },
  { key: "report", path: "/report", must: ["每日总结", "今天的结论", "活跃时段", "其他日期"] },
  { key: "checkin", path: "/checkin", must: ["打卡", "今日所学", "连续打卡", "历史"] },
  { key: "settings", path: "/settings", must: ["设置", "账号", "外观", "用量与反馈", "调度进程"] },
];

/** 藏掉 Next 的开发工具浮标（右下角那个黑圆 N），否则每张证据图右上角都带它 */
async function hideDevBadge(page) {
  try {
    await page.addStyleTag({ content: "nextjs-portal{display:none !important}" });
  } catch {
    /* 浮标不在就算了 */
  }
}

async function clickByText(page, text, tag = "button") {
  return page.evaluate(
    (t, g) => {
      const el = [...document.querySelectorAll(g)].find((x) =>
        (x.textContent || "").trim().includes(t)
      );
      if (!el) return false;
      el.click();
      return true;
    },
    text,
    tag
  );
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-proxy-server", "--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: { width: 1440, height: 1000, deviceScaleFactor: 2 },
  });
  const page = await browser.newPage();
  const errors = [];
  const failedReqs = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push("PAGEERROR " + e.message));
  // 网络层失败单独记：console 里只有一句 ERR_CONNECTION_RESET，看不出是谁在断
  page.on("requestfailed", (r) => {
    const external = !r.url().startsWith("http://localhost:3000");
    failedReqs.push((external ? "[外部] " : "[本站] ") + (r.failure()?.errorText || "?") + " " + r.url().slice(0, 130));
  });

  // 1) 建临时账号
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await sleep(1000);
  if (await page.evaluate(() => document.body.innerText.includes("注册"))) {
    await clickByText(page, "注册");
    await sleep(400);
    const textInput = await page.$('input:not([type="password"]):not([type="file"])');
    await textInput.type(USER);
    const pwInput = await page.$('input[type="password"]');
    await pwInput.type(PASS);
    await page.click('button[type="submit"]');
    log("注册临时账号", USER);
  }
  await page.waitForFunction(() => document.body.innerText.includes("资料库"), { timeout: 40000 });
  await sleep(1000);

  // 2) 侧栏四屏入口是否出现
  const sideLinks = await page.evaluate(() =>
    [...document.querySelectorAll("aside a[href]")]
      .map((a) => a.getAttribute("href"))
      .filter((h) => ["/delegate", "/report", "/checkin", "/settings"].includes(h))
  );
  log("侧栏四屏入口 =", JSON.stringify(sideLinks), "｜期望 4 个");

  // 3) 用侧栏图标入口点进「每日委托」，验证确实是客户端跳转
  await page.evaluate(() => {
    const a = document.querySelector('aside a[href="/delegate"]');
    if (a) a.click();
  });
  await page.waitForFunction(() => location.pathname === "/delegate", { timeout: 20000 });
  log("侧栏跳转成功 →", await page.evaluate(() => location.pathname));

  // 4) 逐页校验
  for (const p of PAGES) {
    // 已经在 /delegate 上就不重复 goto，顺带验证客户端跳转后的状态
    if ((await page.evaluate(() => location.pathname)) !== p.path) {
      await page.evaluate((to) => {
        const a = document.querySelector(`a.nav-item[href="${to}"]`);
        if (a) a.click();
      }, p.path);
      await page.waitForFunction((to) => location.pathname === to, { timeout: 20000 }, p.path);
    }
    await sleep(1800);

    const info = await page.evaluate((must) => {
      const text = document.body.innerText;
      const missing = must.filter((m) => !text.includes(m));
      const emoji = text.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu) || [];
      const canary = text.match(/\*\*/g) || []; // 未解析的 markdown 强调 = 文案 bug
      const notReady = document.querySelectorAll(".not-ready").length;
      const demoBadge = [...document.querySelectorAll(".sec-label")].filter((e) =>
        e.innerText.includes("演示数据")
      ).length;
      const activeNav = (document.querySelector("a.nav-item[aria-current='page']") || {}).innerText;
      // 触控目标：所有 .btn / .switch / .icon-btn 的高度。
      // <32 = 不达标（WCAG 2.2 目标尺寸下限 24，这里是自定的更严线）；
      // 32~39 = 桌面密度下的紧凑档，允许但要点出来
      const small = [...document.querySelectorAll(".btn, .switch, .icon-btn")]
        .map((el) => {
          const r = el.getBoundingClientRect();
          return { t: (el.textContent || el.className).trim().slice(0, 16), h: Math.round(r.height) };
        })
        .filter((x) => x.h > 0 && x.h < 40);
      const tiny = small.filter((x) => x.h < 32);
      return { missing, emoji, canary, notReady, demoBadge, activeNav, small, tiny, len: text.length };
    }, p.must);

    log(
      `页面 ${p.path} → 缺失文案 ${JSON.stringify(info.missing)}｜当前导航「${info.activeNav}」｜` +
        `接口待上线块 ${info.notReady}｜演示标记 ${info.demoBadge}｜emoji ${info.emoji.length}｜` +
        `残留星号 ${info.canary.length}｜紧凑控件(32-39) ${info.small.length}｜不达标(<32) ${info.tiny.length}`
    );
    if (info.tiny.length) log("   !! 不达标：", JSON.stringify(info.tiny.slice(0, 6)));
  if (info.small.length) log("   紧凑控件：", JSON.stringify(info.small.slice(0, 6)));
  if (info.emoji.length) log("   emoji：", JSON.stringify(info.emoji.slice(0, 6)));

  }

  // 5) 明暗两套主题各截一轮，并断言「底色真的换了」。
  //
  // ⚠️ 这里修掉了一个连环坑（2026-10-13 发现）：
  //   ① puppeteer 每次 launch 都用全新的临时用户目录 ⇒ localStorage 是空的；
  //   ② 本机无头 Chrome 的 prefers-color-scheme 报的恰好是 dark；
  //   ③ app/layout.tsx 那段启动内联脚本在 localStorage 没有 theme 时回退到系统偏好。
  //   ⇒ 整轮渲染都是深色，所谓「浅色图」本来就是深色图。
  //   而只 localStorage.setItem('theme','dark') 又不会重新应用主题（同文档内不触发 storage
  //   事件，全项目也没有任何人监听）⇒ 再产出 4 张与「浅色图」几乎字节相同的假深色图。
  //   两边全错、还互相"印证"，只比字节根本发现不了 —— 所以现在改成断言**实际渲染底色亮度**。
  //   主题一律用启动脚本本来就支持的 ?theme= 参数 + 硬跳转显式钉住，不赌浏览器默认值。
  const lum = (css) => {
    const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(css || "");
    return m ? 0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3] : null;
  };
  const shots = [];
  for (const theme of ["light", "dark"]) {
    const wantDark = theme === "dark";
    for (const p of PAGES) {
      await page.goto(`${BASE}${p.path}?theme=${theme}`, {
        waitUntil: "networkidle2",
        timeout: 60000,
      });
      await sleep(1500);
      const st = await page.evaluate(() => {
        // 从内容根往上找第一个有实底的祖先，拿到真正铺在纸面上的颜色
        let el = document.querySelector(".console-page") || document.body;
        let bg = "none";
        while (el) {
          const c = getComputedStyle(el).backgroundColor;
          if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") {
            bg = c;
            break;
          }
          el = el.parentElement;
        }
        const nav = document.querySelector("a.nav-item[aria-current='page']");
        return {
          hasDark: document.documentElement.classList.contains("dark"),
          bg,
          nav: nav ? getComputedStyle(nav).color : "（找不到）",
        };
      });
      const l = lum(st.bg);
      // 浅色底 #F5F0E1 亮度约 240；深色底 #251F16 约 32。阈值留足余量。
      const ok = st.hasDark === wantDark && l !== null && (wantDark ? l < 100 : l > 150);
      if (!ok) {
        log(`   !! ${p.key}-${theme} 主题没生效：<html>.dark=${st.hasDark}｜底色 ${st.bg}（亮度 ${l}）`);
      }
      log(
        `  ${p.key.padEnd(9)} ${theme.padEnd(5)} → <html>.dark=${String(st.hasDark).padEnd(5)}｜底色 ${st.bg.padEnd(20)}｜导航取色 ${st.nav}`
      );
      shots.push({ key: p.key, theme, bg: st.bg, nav: st.nav, ok });
      await hideDevBadge(page);
      await page.screenshot({
        path: path.join(OUT, `${p.key}${wantDark ? "-dark" : ""}.png`),
        fullPage: true,
      });
    }
  }
  const bad = shots.filter((s) => !s.ok);
  log(
    "主题渲染断言：",
    bad.length === 0
      ? "8 张全部命中预期底色 ✓"
      : `!! ${bad.length} 张没命中：${JSON.stringify(bad.map((b) => `${b.key}-${b.theme}`))}`
  );

  // 6) 关键取色：当前导航项应是 --accent-ink（持久状态才用金，hover/focus 不用金）
  const navLight = (shots.find((s) => s.theme === "light") || {}).nav;
  const navDark = (shots.find((s) => s.theme === "dark") || {}).nav;
  log(
    `导航取色：浅色 ${navLight}（期望 rgb(135, 91, 16) = #875B10）｜` +
      `深色 ${navDark}（期望 rgb(224, 160, 58) = #E0A03A）`
  );
  if (navLight === navDark) log("   !! 明暗取色相同，主题切换没生效");

  // 37 条里有 401 与 404 —— 前者是登录前探活（预期），后者是队友分支才有的端点（预期）。
  // 真正要盯的是「其余」：React 水合告警、PAGEERROR、取数逻辑炸了都会落在这里。
  const expected = errors.filter((e) => /40[14] \((Unauthorized|Not Found)\)/.test(e));
  const real = errors.filter((e) => !/40[14] \((Unauthorized|Not Found)\)/.test(e));
  log(
    "console 错误 =",
    errors.length,
    "｜401/404（预期内）=",
    expected.length,
    "｜其余（必须为 0）=",
    real.length
  );
  real.slice(0, 10).forEach((e) => log("  !! 真错误:", e.slice(0, 240)));
  log("网络层失败（requestfailed）=", failedReqs.length);
  failedReqs.slice(0, 10).forEach((e) => log("   -", e));

  await browser.close();
})().catch((e) => {
  console.error("[console] 失败:", e.message);
  process.exit(1);
});
