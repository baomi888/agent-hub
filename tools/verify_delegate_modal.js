/**
 * 每日委托「表单改弹窗」实机验收（2026-11-19）：
 *   1. 页面无常驻表单、无固定动作条（那条黑条+按钮）
 *   2. 点「新建委托」才弹窗，表单在 dialog 里，宽版
 *   3. 手机 390 视口下弹窗可见、底部「创建」按钮可见、不溢出
 *   4. 点取消 / Esc 能关
 *
 * 跑法：NODE_PATH=.workbuddy/pp/node_modules node tools/verify_delegate_modal.js
 *      线上：BASE=https://myzhisui.cn NODE_PATH=... node tools/verify_delegate_modal.js
 * 只注册一次性测试账号，不碰线上数据；截图落 tools/_scratch/delegate_*.png。
 */
const puppeteer = require("puppeteer-core");
const path = require("path");

const BASE = (process.env.BASE || "http://127.0.0.1:3100").replace(/\/+$/, "");
const CHROME = "C:/Users/Fengjinghao/AppData/Local/Google/Chrome/Application/chrome.exe";
const OUT = path.resolve(__dirname, "_scratch");
const USER = { username: "dgmodal" + Math.random().toString(36).slice(2, 7), password: "Baomi@2026test" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ok = [], bad = [];
function check(cond, msg) { (cond ? ok : bad).push(msg); console.log(cond ? "  OK  " : "  FAIL", msg); }

async function prepShot(p) {
  await p.evaluate(() => {
    document.documentElement.classList.remove("dark");
    let s = document.getElementById("__shot_hide");
    if (!s) { s = document.createElement("style"); s.id = "__shot_hide"; document.head.appendChild(s); }
    s.textContent = ".zs-boot-splash,.zs-boot-auth,.ann-page{display:none !important}";
  });
  await p.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await sleep(250);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: true,
    args: ["--no-proxy-server", "--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 1 },
  });
  try {
    const p = await browser.newPage();
    const cdp = await p.createCDPSession();
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `
        const _orig = Storage.prototype.getItem;
        Storage.prototype.getItem = function(k) {
          if (k && (k.indexOf('baomi.announce.v1') === 0 || k.indexOf('baomi.guide.v1') === 0)) return '1';
          return _orig.apply(this, arguments);
        };`,
    });
    await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await sleep(400);
    const uid = await p.evaluate(async (u) => {
      await fetch("/api/auth/register/", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify(u) }).catch(() => {});
      await fetch("/api/auth/login/", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify(u) });
      const me = await fetch("/api/auth/me", { credentials: "include" });
      return (await me.json().catch(() => ({})))?.user?.id ?? null;
    }, USER);
    if (!uid) throw new Error("登录失败");
    await p.evaluate((id) => { try { localStorage.setItem(`baomi.guide.v1.${id}`, "1"); } catch {} }, uid);

    // ---- 桌面：进委托页 ----
    await p.goto(`${BASE}/delegate`, { waitUntil: "networkidle2", timeout: 60000 });
    await p.waitForFunction(() => document.body.innerText.includes("让知穗每天主动找你一次"), { timeout: 40000 }).catch(() => {});
    await sleep(500);
    const before = await p.evaluate(() => ({
      dialog: !!document.querySelector('.acct-dlg[role="dialog"]'),
      grid: !!document.querySelector(".del-form-grid"),
      fixedBar: !!document.querySelector(".form-actions"),
      hasNewBtn: [...document.querySelectorAll("button")].some((b) => /新建委托/.test(b.textContent || "")),
      hasEmptyCard: document.body.innerText.includes("让知穗每天主动找你一次"),
    }));
    check(!before.dialog, "进页面时没有委托表单弹窗");
    check(!before.grid, "页面无常驻表单 .del-form-grid（黑条根因已除）");
    check(!before.fixedBar, "页面无固定动作条 .form-actions（黑条本体已删）");
    check(before.hasNewBtn, "页头有「新建委托」按钮");
    check(before.hasEmptyCard, "空态引导卡在（我的委托列表）");
    await prepShot(p);
    await p.screenshot({ path: path.join(OUT, "delegate_page.png") });

    // ---- 点新建 → 弹窗 ----
    await p.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find((x) => /新建委托/.test(x.textContent || ""));
      b && b.click();
    });
    await sleep(400);
    const dlg = await p.evaluate(() => {
      const d = document.querySelector('.acct-dlg[role="dialog"]');
      if (!d) return { open: false };
      const r = d.getBoundingClientRect();
      const inputs = d.querySelectorAll("input,textarea,select").length;
      const createBtn = [...d.querySelectorAll("button")].some((b) => /创建/.test(b.textContent || ""));
      return {
        open: true, modal: d.getAttribute("aria-modal"), wide: d.classList.contains("acct-dlg--wide"),
        title: d.getAttribute("aria-label"), inputs, createBtn,
        grid: !!d.querySelector(".del-form-grid"),
        w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top),
      };
    });
    check(dlg.open, "点「新建委托」弹窗出现");
    check(dlg.modal === "true", `弹窗 aria-modal=true（实际 ${dlg.modal}）`);
    check(dlg.wide, "弹窗是宽版 acct-dlg--wide");
    check(/新建委托/.test(dlg.title || ""), `弹窗标题是「新建委托」（实际 ${dlg.title}）`);
    check(dlg.grid && dlg.inputs >= 8, `表单字段在弹窗里（${dlg.inputs} 个控件）`);
    check(dlg.createBtn, "弹窗底部有「创建」按钮");
    await prepShot(p);
    await p.screenshot({ path: path.join(OUT, "delegate_modal_desktop.png") });

    // ---- Esc 关闭 ----
    await p.keyboard.press("Escape");
    await sleep(300);
    check(!(await p.evaluate(() => !!document.querySelector('.acct-dlg[role="dialog"]'))), "Esc 能关弹窗");

    // ---- 手机 390 视口 ----
    await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
    await p.goto(`${BASE}/delegate`, { waitUntil: "networkidle2", timeout: 60000 });
    await sleep(500);
    await p.evaluate(() => {
      const b = [...document.querySelectorAll("button")].find((x) => /新建委托/.test(x.textContent || ""));
      b && b.click();
    });
    await sleep(400);
    const m = await p.evaluate(() => {
      const d = document.querySelector('.acct-dlg[role="dialog"]');
      if (!d) return { open: false };
      const r = d.getBoundingClientRect();
      const foot = d.querySelector(".del-dlg-foot");
      const fr = foot ? foot.getBoundingClientRect() : null;
      return {
        open: true, left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top),
        vw: window.innerWidth, vh: window.innerHeight,
        footVisible: !!fr && fr.bottom <= window.innerHeight + 1 && fr.top >= 0,
        scrollable: (() => { const b = d.querySelector(".acct-dlg-body"); return !!b && b.scrollHeight > b.clientHeight; })(),
      };
    });
    check(m.open, "手机端点新建弹窗出现");
    check(m.left >= -1 && m.right <= m.vw + 1, `手机弹窗不溢出（left=${m.left} right=${m.right} vw=${m.vw}）`);
    check(m.top >= 0, `手机弹窗顶部在视口内（top=${m.top}）`);
    check(m.footVisible, "手机弹窗底部「创建/取消」按钮可见");
    check(m.scrollable, "手机弹窗内容区可滚动（长表单不顶掉按钮）");
    await prepShot(p);
    await p.screenshot({ path: path.join(OUT, "delegate_modal_phone.png") });
    check(!(await p.evaluate(() => !!document.querySelector(".form-actions"))), "手机页面无固定黑条 form-actions");

    console.log(`\n==== 结果：${ok.length} OK / ${bad.length} FAIL ====`);
    if (bad.length) process.exitCode = 1;
  } catch (e) {
    console.error("验收脚本异常：", e);
    process.exitCode = 2;
  } finally {
    await browser.close();
  }
})();
