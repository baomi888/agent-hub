// 2026-11-25 验证：豆包/DeepSeek 式输入面板（codex 工作区未提交改动）
//   A. 390 宽：底部「+」弹出 attach-sheet（拍照/相册/文件），面板不溢出、按钮 44px+
//   B. 手机端选文件走 addFiles 上限 9 + 提示 + 发送键常驻
//   C. 桌面宽：点「+」弹出 attach-popover（上传/拍照/截图提问），外部点击关闭
//   D. CapToggles「深度思考/智能搜索」仍在、textArea 仍可输入
// 用法：node tools/verify_input_panel_20261125.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9348);
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
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq; waits.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) return { __err: (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text).slice(0, 300) };
  return r.result?.result?.value;
};
const shot = async (file) => {
  const r = await send("Page.captureScreenshot", { format: "png" });
  if (!r.result?.data) { console.log("SHOT-FAIL", file, JSON.stringify(r).slice(0, 200)); return; }
  writeFileSync("shots/" + file, Buffer.from(r.result.data, "base64"));
  console.log("SHOT", file);
};
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(ok ? "PASS" : "FAIL", name, detail);
};

await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
await send("Page.navigate", { url: BASE + "/" });
await sleep(2500);
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
  await sleep(450);
  await ev(`(() => { const go = document.querySelector(".ann-go"); if (go && !go.disabled) go.click(); })()`);
  await sleep(500);
};
await dismissPopups();
await send("Page.navigate", { url: BASE + "/" });
await sleep(3500);
await dismissPopups();

// ============ A. CapToggles 仍在 + 输入框可交互 ============
const a0 = await ev(`(() => ({
  toggles: [...document.querySelectorAll("button")].filter(b => /检索|智能联网/.test(b.textContent)).map(b => b.textContent.trim()),
  hasTextarea: !!document.querySelector("textarea"),
  attachBtn: !!document.querySelector("button.attach"),
}))()`);
check("A0 深度思考/智能搜索药丸 + 输入框 + 附件按钮都在",
  a0?.toggles?.length === 2 && a0.hasTextarea && a0.attachBtn, JSON.stringify(a0));

// ============ B. 手机点「+」出 attach-sheet ============
await ev(`document.querySelector("button.attach")?.click()`);
await sleep(500);
const b1 = await ev(`(() => {
  const sheet = document.querySelector(".attach-sheet");
  const backdrop = document.querySelector(".attach-sheet-backdrop");
  const items = [...document.querySelectorAll(".attach-sheet-actions > button")].map(b => ({
    label: b.textContent.trim(), w: Math.round(b.getBoundingClientRect().width), h: Math.round(b.getBoundingClientRect().height),
  }));
  const r = sheet?.getBoundingClientRect();
  const br = backdrop?.getBoundingClientRect();
  return { open: !!sheet, backdrop: !!backdrop, items, sheetRect: r && { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right) },
    vw: innerWidth, vh: innerHeight, title: document.getElementById("attach-sheet-title")?.textContent };
})()`);
check("B1 attach-sheet 弹出、三入口拍照/相册/文件、触摸目标>=44px",
  b1?.open === true && b1.backdrop === true && b1.items?.length === 3 &&
  ["拍照", "相册", "文件"].every(l => b1.items.some(i => i.label === l)) &&
  b1.items.every(i => i.h >= 44), JSON.stringify(b1));
check("B2 面板不溢出 390 宽视口",
  b1?.sheetRect && b1.sheetRect.left >= 0 && b1.sheetRect.right <= b1.vw &&
  b1.sheetRect.bottom <= b1.vh + 1, JSON.stringify(b1?.sheetRect));
await shot("input_panel_mobile_sheet.png");

// 点拍输入口（手机应触发 capture input 的 click，不弹相机 modal）
await ev(`[...document.querySelectorAll(".attach-sheet-actions > button")].find(b => b.textContent.includes("拍照"))?.click()`);
await sleep(400);
const b3 = await ev(`({ sheetOpen: !!document.querySelector(".attach-sheet"), cameraMask: !!document.querySelector(".camera-capture-mask"), cameraInput: !!document.querySelector('input[capture]') })`);
check("B3 手机拍照走系统相机 input（不弹桌面预览 modal）",
  b3?.cameraInput === true && b3.cameraMask === false, JSON.stringify(b3));

// 关闭面板并注入 10 文件验证上限
await ev(`document.querySelector(".attach-sheet-close")?.click()`);
await sleep(400);
const b4 = await ev(`(() => {
  const input = document.querySelector('input[accept*=".docx"]');
  if (!input) return { err: "no-file-input" };
  const dt = new DataTransfer();
  for (let i=0;i<10;i++) dt.items.add(new File(['x'.repeat(20)], 'probe-'+i+'.txt', {type:'text/plain'}));
  input.files = dt.files;
  input.dispatchEvent(new Event('change', {bubbles:true}));
  return { injected: 10 };
})()`);
await sleep(700);
const b5 = await ev(`(() => {
  const items = document.querySelectorAll(".attach-items .attach-item");
  const send = [...document.querySelectorAll("button")].find(b => /发送/.test(b.getAttribute("aria-label")||b.title||b.textContent));
  const sr = send?.getBoundingClientRect();
  return { items: items.length, notice: document.body.textContent.includes("一次最多上传 9 个附件"),
    sendVisible: !!sr && sr.top >= 0 && sr.bottom <= innerHeight && sr.height > 0,
    note: [...document.querySelectorAll("p")].map(p=>p.textContent).find(t=>t && t.includes("文件将提取")) || "" };
})()`);
check("B4 10 个文件 -> 收 9 个 + 超限提示 + 发送键可见",
  b5?.items === 9 && b5.notice === true && b5.sendVisible === true, JSON.stringify(b5));
await shot("input_panel_mobile_items.png");

// ============ C. 桌面浮层 ============
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setTouchEmulationEnabled", { enabled: false });
await send("Page.navigate", { url: BASE + "/" });
await sleep(4000);
await dismissPopups();
await ev(`document.querySelector("button.attach")?.click()`);
await sleep(400);
const c1 = await ev(`(() => {
  const pop = document.querySelector(".attach-popover");
  const items = [...document.querySelectorAll(".attach-popover .attach-menu-item")].map(b => b.textContent.trim());
  const sheet = document.querySelector(".attach-sheet");
  // 手机 sheet 是 CSS display:none（不是 DOM 不存在）—— 量 computed display
  const sheetHidden = !sheet || getComputedStyle(sheet).display === "none";
  return { open: !!pop, items, sheetHidden, expanded: document.querySelector("button.attach")?.getAttribute("aria-expanded") };
})()`);
check("C1 桌面「+」弹浮层：上传/拍照/截图提问，手机 sheet 不出现",
  c1?.open === true && c1.items?.length === 3 &&
  ["上传文件或图片", "拍照", "截图提问"].every(l => c1.items.some(i => i.includes(l))) &&
  c1.sheetHidden === true && c1.expanded === "true", JSON.stringify(c1));
await shot("input_panel_desktop_popover.png");
// 关闭监听的是 pointerdown（不是 click）—— 派发 pointerdown 才走得通
await ev(`document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }))`);
await sleep(350);
const c2 = await ev(`({ open: !!document.querySelector(".attach-popover"), expanded: document.querySelector("button.attach")?.getAttribute("aria-expanded") })`);
check("C2 外部点击关闭浮层", c2?.open === false && c2.expanded === "false", JSON.stringify(c2));

const pass = results.filter(r => r.ok).length;
console.log(`SUMMARY ${pass}/${results.length}`);
ws.close(); process.exit(pass === results.length ? 0 : 1);
