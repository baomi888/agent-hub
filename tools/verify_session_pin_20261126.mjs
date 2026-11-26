// 2026-11-26 会话置顶真浏览器验证：桌面 hover 入口 + 手机左滑入口 + 后端落库
// 用法：BAOMI_CDP_PORT=9354 node tools/verify_session_pin_20261126.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要测试 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9354);
const BASE = process.env.BAOMI_BASE || "http://127.0.0.1:3100";
const DOMAIN = new URL(BASE).hostname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
import { writeFileSync } from "node:fs";

const apiFetch = (path, opts = {}) => fetch(BASE + path, {
  ...opts,
  headers: { Cookie: `baomi_session=${TOKEN}`, ...(opts.headers || {}) },
});

const page = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
if (!page?.webSocketDebuggerUrl) { console.error("开新 tab 失败", page); process.exit(2); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let seq = 0; const waits = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && waits.has(m.id)) { waits.get(m.id).resolve(m); waits.delete(m.id); }
};
const send = (method, params = {}) => new Promise((resolve) => {
  const id = ++seq; waits.set(id, { resolve });
  ws.send(JSON.stringify({ id, method, params }));
});
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) return { __err: (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text).slice(0, 400) };
  return r.result?.result?.value;
};
const waitFor = async (expr, ms = 20000) => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await ev(`!!(${expr})`) === true) return true;
    await sleep(300);
  }
  return false;
};
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(ok ? "PASS" : "FAIL", name, detail);
};
const shot = async (file) => {
  const r = await send("Page.captureScreenshot", { format: "png" });
  if (r.result?.data) { writeFileSync(file, Buffer.from(r.result.data, "base64")); console.log("SHOT", file); }
};

await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
await send("Network.enable");
await send("Network.setCookie", { name: "baomi_session", value: TOKEN, domain: DOMAIN, path: "/" });
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `(() => {
    const orig = Storage.prototype.getItem;
    Storage.prototype.getItem = function (k) {
      if (typeof k === "string" && (k.indexOf("baomi.announce.v1") === 0 || k.indexOf("baomi.guide.v1") === 0)) return "1";
      return orig.apply(this, arguments);
    };
  })();`,
});

// 用 API 建两条独立会话，避免依赖测试账号里已有的历史数据
const suffix = String(Date.now());
const post = async (title) => {
  const r = await apiFetch("/api/sessions/", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) throw new Error("建会话失败 " + r.status);
  return r.json();
};
const oldTitle = "置顶探针-旧-" + suffix;
const newTitle = "置顶探针-新-" + suffix;
const older = await post(oldTitle);
const newer = await post(newTitle);
const list = async () => (await (await apiFetch("/api/sessions/")).json()).sessions;
// 线上冷启动比本地慢：启动页可能**在 textarea 出现之后才挂上**。
// 先等输入区，再等启动页退场，最后才认会话列表 —— 顺序反了会在空壳上开点。
const waitApp = async () => {
  await waitFor(`document.querySelector("textarea")`, 30000);
  await sleep(800);
  await waitFor(`!document.querySelector(".zs-boot-splash")`, 20000);
  await ev(`document.querySelectorAll(".guide-skip,.announce-close,.tb-notice-x,.modal-close").forEach(b => b.click())`);
  await sleep(700);
  return waitFor(`document.querySelectorAll(".session").length >= 2`, 25000);
};
const waitPin = async (sid, pinned) => {
  const until = Date.now() + 12000;
  while (Date.now() < until) {
    if ((await list()).find((s) => s.id === sid)?.pinned === pinned) return true;
    await sleep(300);
  }
  return false;
};
const before = await list();
check("两条测试会话已建且新会话在前",
  before.findIndex((s) => s.id === newer.id) < before.findIndex((s) => s.id === older.id));

// ---------- 桌面 ----------
await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: BASE + "/" });
check("桌面会话列表加载", await waitApp());

const findRow = (title) => `Array.from(document.querySelectorAll(".session")).find(e => e.textContent.includes(${JSON.stringify(title)}))`;
const desktopBtn = await ev(`(() => { const r = ${findRow(oldTitle)}; const b = r && r.querySelector('button[aria-label^="置顶会话"]'); return !!b; })()`);
check("桌面 hover 区有置顶按钮", desktopBtn === true);

const activeBefore = await ev(`document.querySelector('.session[aria-current="true"]')?.textContent || ""`);
const clickedDesktop = await ev(`(() => { const r = ${findRow(oldTitle)}; const b = r && r.querySelector('button[aria-label^="置顶会话"]'); if (!b) return false; b.click(); return true; })()`);
check("桌面置顶按钮点击成功", clickedDesktop === true);
await waitPin(older.id, true);
await waitFor(`Array.from(document.querySelectorAll(".group-head")).some(h => h.textContent.includes("置顶"))`, 10000);
const afterPin = await list();
const row = afterPin.find((s) => s.id === older.id);
check("桌面点击后后端落库 pinned=true", row?.pinned === true, JSON.stringify({ pinned: row?.pinned }));
const groupInfo = await ev(`(() => {
  const heads = Array.from(document.querySelectorAll(".group-head"));
  const h = heads.find(x => x.textContent.includes("置顶"));
  const items = h ? Array.from(h.parentElement.querySelectorAll(".session")) : [];
  return { first: heads[0]?.textContent || "", count: items.length, hasTitle: items.some(e => e.textContent.includes(${JSON.stringify(oldTitle)})), mark: items.some(e => e.querySelector(".session-pin-mark")) };
})()`);
check("置顶分组在最上且含目标会话与图钉",
  groupInfo.first.includes("置顶") && groupInfo.hasTitle && groupInfo.mark === true, JSON.stringify(groupInfo));
const activeAfter = await ev(`document.querySelector('.session[aria-current="true"]')?.textContent || ""`);
check("点置顶没有误切会话", activeBefore === activeAfter, `${activeBefore} => ${activeAfter}`);
await shot("shots/session_pin_desktop.png");

const clickedUnpin = await ev(`(() => { const h = Array.from(document.querySelectorAll(".group-head")).find(x => x.textContent.includes("置顶")); const r = h && Array.from(h.parentElement.querySelectorAll(".session")).find(e => e.textContent.includes(${JSON.stringify(oldTitle)})); const b = r && r.querySelector('button[aria-label^="取消置顶会话"]'); if (!b) return false; b.click(); return true; })()`);
check("桌面取消置顶按钮点击成功", clickedUnpin === true);
check("取消置顶后端落库 false", await waitPin(older.id, false));

// ---------- 手机 ----------
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
await send("Page.navigate", { url: BASE + "/" });
check("手机会话列表加载", await waitApp());
const swipe = await ev(`(() => {
  const r = ${findRow(oldTitle)};
  const acts = r && r.parentElement.querySelector(".session-swipe-acts");
  return acts ? { width: Math.round(acts.getBoundingClientRect().width), buttons: acts.querySelectorAll("button").length, labels: Array.from(acts.querySelectorAll("button")).map(b => b.getAttribute("aria-label") || "") } : null;
})()`);
check("手机左滑区三按钮且宽192", swipe?.width === 192 && swipe?.buttons === 3, JSON.stringify(swipe));
check("左滑区含置顶入口", (swipe?.labels || []).some((l) => l.includes("置顶会话")), JSON.stringify(swipe?.labels));

const clickedMobile = await ev(`(() => { const r = ${findRow(oldTitle)}; const b = r && r.parentElement.querySelector('.session-swipe-btn.is-pin'); if (!b) return false; b.click(); return true; })()`);
check("手机左滑置顶按钮点击成功", clickedMobile === true);
check("手机点击后后端落库 pinned=true", await waitPin(older.id, true));
await waitFor(`Array.from(document.querySelectorAll(".group-head")).some(h => h.textContent.includes("置顶"))`, 10000);
check("手机置顶分组含目标会话", await ev(`(() => {
  const h = Array.from(document.querySelectorAll(".group-head")).find(x => x.textContent.includes("置顶"));
  return !!(h && Array.from(h.parentElement.querySelectorAll(".session")).some(e => e.textContent.includes(${JSON.stringify(oldTitle)})));
})()`) === true);
// 抽屉默认是关着的：不打开就截图只会拍到聊天页，看不到置顶分组的真实排版
await ev(`document.querySelector('button.hamburger')?.click()`);
check("手机抽屉展开后置顶分组可见", await waitFor(`(() => {
  const h = Array.from(document.querySelectorAll(".group-head")).find(x => x.textContent.includes("置顶"));
  return !!h && h.getBoundingClientRect().width > 0;
})()`, 10000));
await sleep(500);
await shot("shots/session_pin_mobile.png");

console.log("TOTAL", results.length, "PASS", results.filter((r) => r.ok).length);
ws.close();
process.exit(results.every((r) => r.ok) ? 0 : 1);
