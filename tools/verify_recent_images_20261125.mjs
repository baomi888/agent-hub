// 2026-11-25 验证方案 A：手机「+」面板的「最近使用图片」网格
//   A. 首次打开（缓存为空）：不显示最近区块、三个入口仍在、发送键可见
//   B. 选一张图片 → 写进 IndexedDB 缓存（不阻塞主流程）
//   C. 关面板重开：最近区块出现、缩略图可见、文案更新
//   D. 点缩略图：走 addFiles 加入待发送（发送键仍可见）
//   E. 点清空：缓存删除、区块消失
// 用法：node tools/verify_recent_images_20261125.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9350);
const BASE = process.env.BAOMI_BASE || "http://127.0.0.1:3100";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
import { writeFileSync } from "node:fs";

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
  if (r.result?.exceptionDetails) return { __err: (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text).slice(0, 300) };
  return r.result?.result?.value;
};
const shot = async (file) => {
  const r = await send("Page.captureScreenshot", { format: "png" });
  if (!r.result?.data) { console.log("SHOT-FAIL", file); return; }
  writeFileSync("shots/" + file, Buffer.from(r.result.data, "base64"));
  console.log("SHOT", file);
};
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(ok ? "PASS" : "FAIL", name, detail);
};
const waitForChat = async (ms = 20000) => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await ev(`!!document.querySelector("textarea")`) === true) return true;
    await sleep(500);
  }
  return false;
};

await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
await send("Page.navigate", { url: BASE + "/" });
await sleep(2200);
await send("Network.setCookie", { name: "baomi_session", value: TOKEN, url: BASE });

const dismissPopups = async () => {
  await ev(`(() => {
    document.querySelector(".guide-skip")?.click();
    const ann = document.querySelector(".ann-page");
    if (ann) {
      const body = document.querySelector(".ann-body");
      if (body) { body.scrollTop = body.scrollHeight; body.dispatchEvent(new Event("scroll")); }
    }
  })()`);
  await sleep(400);
  await ev(`(() => { const go = document.querySelector(".ann-go"); if (go && !go.disabled) go.click(); })()`);
  await sleep(450);
};
await dismissPopups();
await send("Page.navigate", { url: BASE + "/" });
await waitForChat();
await dismissPopups();

// 清空 IndexedDB 缓存，保证从「首次」状态开始（跨探针运行也稳定）
await ev(`new Promise((res) => { const r = indexedDB.deleteDatabase("baomi.recent-images"); r.onsuccess = r.onerror = r.onblocked = () => res(true); })`);
await sleep(400);

const openSheet = async () => {
  await ev(`document.querySelector("button.attach")?.click()`);
  await sleep(450);
};
const closeSheet = async () => {
  await ev(`document.querySelector(".attach-sheet-backdrop")?.click()`);
  await sleep(350);
};

// ============ A. 首次打开：无最近区块 ============
await openSheet();
const a1 = await ev(`(() => {
  const sheet = document.querySelector(".attach-sheet");
  const recent = document.querySelector(".attach-sheet-recent");
  const actions = [...document.querySelectorAll(".attach-sheet-actions > button")].map(b => b.textContent.trim());
  return {
    sheetVisible: !!sheet,
    recentVisible: !!recent,
    actions,
    sendVisible: (() => { const b = document.querySelector("button.send"); if (!b) return false; const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; })(),
  };
})()`);
check("A 首次打开：三入口在、无最近区块、发送键可见",
  a1?.sheetVisible === true && a1.recentVisible === false &&
  a1.actions?.length === 3 && a1.sendVisible === true, JSON.stringify(a1));
await shot("recent_A_first_open.png");

// ============ B. 选一张图片 → 写缓存 ============
// 用 DataTransfer 注入真实 File，触发 input 的 change（onChange 会清 value，不看 files.length）
const inject = await ev(`(() => {
  const input = document.querySelector('input[type=file][accept*="image"]');
  if (!input) return "NO_INPUT";
  const canvas = document.createElement("canvas");
  canvas.width = 40; canvas.height = 40;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#C8892A"; ctx.fillRect(0, 0, 40, 40);
  return new Promise((res) => canvas.toBlob((blob) => {
    const file = new File([blob], "最近用过的图.png", { type: "image/png" });
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    setTimeout(() => res("INJECTED"), 300);
  }, "image/png"));
})()`);
check("B 注入图片到相册 input", inject === "INJECTED", String(inject));
await sleep(700);
// 直接从 IndexedDB 读，确认写入成功（不依赖 UI）
const cached = await ev(`new Promise((res) => {
  const req = indexedDB.open("baomi.recent-images", 1);
  req.onsuccess = () => {
    const db = req.result;
    if (!db.objectStoreNames.contains("images")) { res(0); return; }
    const g = db.transaction("images", "readonly").objectStore("images").getAll();
    g.onsuccess = () => res(g.result.length);
    g.onerror = () => res(-1);
  };
  req.onerror = () => res(-2);
})`);
check("B 图片已写入 IndexedDB 缓存", cached === 1, "count=" + cached);

// ============ C. 关面板重开：最近区块出现 ============
await closeSheet();
await openSheet();
const c1 = await ev(`(() => {
  const recent = document.querySelector(".attach-sheet-recent");
  const thumb = document.querySelector(".attach-sheet-recent-item");
  const head = document.querySelector(".attach-sheet-head p");
  const img = thumb?.querySelector("img");
  return {
    recentVisible: !!recent,
    thumbCount: document.querySelectorAll(".attach-sheet-recent-item").length,
    thumbSrc: !!img?.getAttribute("src"),
    thumbLoaded: img ? (img.naturalWidth > 0) : false,
    head: head?.textContent || "",
    clearBtn: !!document.querySelector(".attach-sheet-recent-head button"),
  };
})()`);
// B 注入图片后 pending 已有 1 个附件，头部显示「已选择 1/9 个附件」是正确行为；
// 「点最近图片直接添加」只在 pending 为空时出现，这里不该断言它。
check("C 重开面板：最近区块+缩略图+清空按钮都在",
  c1?.recentVisible === true && c1.thumbCount === 1 &&
  c1.thumbLoaded === true && c1.clearBtn === true &&
  /已选择 1\/9 个附件/.test(c1.head), JSON.stringify(c1));
await shot("recent_C_grid.png");

// ============ D. 点缩略图加入待发送 ============
await ev(`document.querySelector(".attach-sheet-recent-item")?.click()`);
await sleep(500);
const d1 = await ev(`(() => {
  const rows = document.querySelectorAll(".attach-sheet-preview").length;
  const b = document.querySelector("button.send");
  const r = b?.getBoundingClientRect();
  return { pendingCount: rows, sendVisible: !!(r && r.width > 0 && r.height > 0), sheetOpen: !!document.querySelector(".attach-sheet") };
})()`);
check("D 点缩略图加入待发送、发送键仍可见",
  d1?.pendingCount === 1 && d1.sendVisible === true, JSON.stringify(d1));
await shot("recent_D_picked.png");

// ============ E. 清空缓存 ============
await ev(`document.querySelector(".attach-sheet-recent-head button")?.click()`);
await sleep(600);
const e1 = await ev(`(() => ({
  recentVisible: !!document.querySelector(".attach-sheet-recent"),
  pendingStill: document.querySelectorAll(".attach-sheet-preview").length,
}))()`);
const cachedAfter = await ev(`new Promise((res) => {
  const req = indexedDB.open("baomi.recent-images", 1);
  req.onsuccess = () => {
    const db = req.result;
    if (!db.objectStoreNames.contains("images")) { res(0); return; }
    const g = db.transaction("images", "readonly").objectStore("images").getAll();
    g.onsuccess = () => res(g.result.length);
  };
  req.onerror = () => res(-2);
})`);
check("E 清空后缓存为空、区块消失、不影响待发送",
  e1?.recentVisible === false && cachedAfter === 0 && e1.pendingStill === 1,
  JSON.stringify({ ...e1, cachedAfter }));
await shot("recent_E_cleared.png");

const pass = results.filter((r) => r.ok).length;
console.log("SUMMARY", `${pass}/${results.length}`);
ws.close();
process.exit(pass === results.length ? 0 : 1);
