// 2026-11-26 手机左滑修复真浏览器验证：
//   ① 展开后标题不被三格按钮盖住；② 从操作区按钮上右滑能收回。
// 用法：BAOMI_CDP_PORT=9355 BAOMI_BASE=http://127.0.0.1:3100 node tools/verify_swipe_fix_20261126.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要测试 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9355);
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
// 摘公告/引导遮罩：否则抽屉一开就被弹层盖住，量到的坐标全是假的（硬约束 52）
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `(() => {
    const orig = Storage.prototype.getItem;
    Storage.prototype.getItem = function (k) {
      if (typeof k === "string" && (k.indexOf("baomi.announce.v1") === 0 || k.indexOf("baomi.guide.v1") === 0)) return "1";
      return orig.apply(this, arguments);
    };
  })();`,
});

const suffix = String(Date.now());
const title = "左滑修复探针-" + suffix;
const r = await apiFetch("/api/sessions/", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ title }),
});
if (!r.ok) throw new Error("建会话失败 " + r.status);
const conv = await r.json();

await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
await send("Page.navigate", { url: BASE + "/" });
// 冷启动顺序：textarea → splash 退场 → 会话行（线上尤其重要）
await waitFor(`document.querySelector("textarea")`, 30000);
await sleep(800);
await waitFor(`!document.querySelector(".zs-boot-splash")`, 20000);
await ev(`document.querySelectorAll(".guide-skip,.announce-close,.tb-notice-x,.modal-close").forEach(b => b.click())`);
await sleep(700);
check("会话行已加载", await waitFor(`document.querySelectorAll(".session").length >= 1`, 25000));

// 打开抽屉
await ev(`document.querySelector("button.hamburger")?.click()`);
check("抽屉展开", await waitFor(`(() => { const a = document.querySelector(".side-left"); return !!a && a.classList.contains("open") && a.getBoundingClientRect().left >= 0; })()`, 12000));
// 抽屉滑入有 0.28s 过渡：不等它停稳就取坐标，touch 起点会落在屏幕外，左滑静默失效
await sleep(600);

const findWrap = (t) => `(() => {
  const row = Array.from(document.querySelectorAll(".session")).find(e => e.textContent.includes(${JSON.stringify(t)}));
  if (!row) return null;
  row.scrollIntoView({ block: "center" });
  return row.parentElement;
})()`;
const geom = await ev(`(() => {
  const wrap = ${findWrap(title)};
  if (!wrap) return null;
  const row = wrap.querySelector(".session");
  const acts = wrap.querySelector(".session-swipe-acts");
  const name = row.querySelector(".session-name");
  const pin = acts.querySelector(".is-pin");
  const rc = (e) => { const b = e.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), right: Math.round(b.right) }; };
  return {
    wrap: rc(wrap), row: rc(row), name: rc(name), acts: rc(acts), pin: rc(pin),
    cls: wrap.className,
    transform: getComputedStyle(acts).transform,
    padRight: getComputedStyle(row).paddingRight,
    btnCount: acts.querySelectorAll("button").length,
  };
})()`);
check("目标会话行可见且三格按钮齐全", !!geom && geom.wrap.w > 200 && geom.btnCount === 3, JSON.stringify(geom?.acts));
check("收起态操作区在行外", geom && geom.transform.includes("144"), geom?.transform);

// ---------- 左滑展开：touch 事件起点在文字层 ----------
const swipe = async (x0, y0, dx) => {
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x0, y: y0, id: 1 }] });
  for (let i = 1; i <= 4; i++) {
    await send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x0 + (dx * i) / 4, y: y0, id: 1 }] });
    await sleep(40);
  }
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
};
const sx = geom.wrap.x + 40;
const sy = geom.wrap.y + geom.wrap.h / 2;
await swipe(sx, sy, -100);
check("左滑后操作区展开", await waitFor(`(() => { const w = ${findWrap(title)}; return !!w && w.querySelector(".session-swipe-acts").classList.contains("open"); })()`, 6000));
await sleep(350); // 等 .22s 过渡结束再量，否则量到中间态

const opened = await ev(`(() => {
  const wrap = ${findWrap(title)};
  const row = wrap.querySelector(".session");
  const acts = wrap.querySelector(".session-swipe-acts");
  const name = row.querySelector(".session-name");
  const btns = Array.from(acts.querySelectorAll("button")).map(b => Math.round(b.getBoundingClientRect().width));
  return {
    wrapClass: wrap.className,
    actsW: Math.round(acts.getBoundingClientRect().width),
    actsLeft: Math.round(acts.getBoundingClientRect().left),
    nameRight: Math.round(name.getBoundingClientRect().right),
    nameW: Math.round(name.getBoundingClientRect().width),
    padRight: getComputedStyle(row).paddingRight,
    btns,
  };
})()`);
check("展开后标题给操作区让位（不重叠）",
  opened && opened.nameRight <= opened.actsLeft + 1 && opened.padRight === "148px",
  JSON.stringify(opened));
check("展开后标题仍有可读宽度（hover 组不占位）",
  opened && opened.nameW >= 40, JSON.stringify({ nameW: opened?.nameW }));
check("操作区宽 144、三格各 48",
  opened && opened.actsW === 144 && opened.btns.every((w) => w === 48),
  JSON.stringify(opened?.btns));
await shot("shots/swipe_fix_opened.png");

// ---------- 右滑收起：touch 事件起点在「置顶」按钮上（旧版这里收不到） ----------
await swipe(opened.actsLeft + 24, sy, 70);
check("从按钮上右滑后操作区收回",
  await waitFor(`(() => { const w = ${findWrap(title)}; return !!w && !w.querySelector(".session-swipe-acts").classList.contains("open"); })()`, 6000));
await sleep(350);
const closed = await ev(`(() => {
  const wrap = ${findWrap(title)};
  const acts = wrap.querySelector(".session-swipe-acts");
  return { cls: wrap.className, transform: getComputedStyle(acts).transform, padRight: getComputedStyle(wrap.querySelector(".session")).paddingRight };
})()`);
check("收回后行恢复原宽度且按钮推出行外",
  closed && !closed.cls.includes("is-swiped") && closed.transform.includes("144") && closed.padRight === "2px",
  JSON.stringify(closed));
await shot("shots/swipe_fix_closed.png");

console.log("TOTAL", results.length, "PASS", results.filter((x) => x.ok).length);
ws.close();
process.exit(results.every((x) => x.ok) ? 0 : 1);
