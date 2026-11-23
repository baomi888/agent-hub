// 真浏览器验收：390 宽下「更多」浮层不溢出；聊天框超量附件有提示且发送键仍可见。
// 用法：node tools/verify_upload_shell.mjs <baomi_session_token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9344);
const BASE = process.env.BAOMI_BASE || "http://127.0.0.1:3100";
const UPSTREAM = process.env.BAOMI_API_UPSTREAM || "https://myzhisui.cn";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 每次开一个新 tab：旧 page 的 WS 可能被上一个没退干净的脚本占着
const page = await (
  await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })
).json();
if (!page?.webSocketDebuggerUrl) { console.error("开新 tab 失败", page); process.exit(2); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let seq = 0; const waits = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && waits.has(m.id)) { waits.get(m.id)(m); waits.delete(m.id); }
  if (m.method === "Fetch.requestPaused" && process.env.BAOMI_FETCH_PROXY === "1") {
    const p = m.params; const u = new URL(p.request.url);
    const target = UPSTREAM + u.pathname + u.search;
    const headers = Object.entries(p.request.headers || {})
      .filter(([k]) => !["host", "cookie", "origin", "referer"].includes(k.toLowerCase()))
      .map(([k, v]) => ({ name: k, value: String(v) }));
    headers.push({ name: "Cookie", value: "baomi_session=" + TOKEN });
    void send("Fetch.continueRequest", { requestId: p.requestId, url: target, headers });
  }
};
const send = (method, params = {}) => new Promise((resolve) => {
  const id = ++seq; waits.set(id, resolve); ws.send(JSON.stringify({ id, method, params }));
});
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true });
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.text };
  return r.result?.result?.value;
};

await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
console.log("CDP_READY");
console.log("FETCH_ENABLING");
if (process.env.BAOMI_FETCH_PROXY === "1") {
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*", requestStage: "Request" }] });
}
console.log("FETCH_ENABLED");
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
console.log("METRICS_OK");
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
console.log("MEDIA_OK");
await send("Network.setCacheDisabled", { cacheDisabled: true });
await send("Network.setCookie", { name: "baomi_session", value: TOKEN, url: BASE, secure: false, sameSite: "Lax" });
console.log("COOKIE_OK");
await send("Page.navigate", { url: BASE + "/" });
console.log("NAV_SENT");

let ready = false;
for (let i = 0; i < 40; i++) {
  await sleep(700);
  const st = await ev(`!!document.querySelector('.composer-input')`);
  if (st === true) { ready = true; break; }
}
if (!ready) { console.error("页面没加载出输入框"); ws.close(); process.exit(1); }
await ev(`(() => { document.querySelectorAll('.guide-mask,.ann-page').forEach(n=>n.remove()); return 1 })()`);

// 1) 打开侧栏抽屉 → 点「更多」→ 量浮层边界
console.log("SHELL", JSON.stringify(await ev(`(() => ({
  openBtn: !!document.querySelector('button[aria-label="打开会话列表"]'),
  rail: !!document.querySelector('.side-left'),
  moreBtn: !!document.querySelector('button[title="更多"]')
}))()`)));
await ev(`(() => { const b=document.querySelector('button[aria-label="打开会话列表"]'); b&&b.click(); return !!b })()`);
await sleep(800);
const clicked = await ev(`(() => { const b=document.querySelector('button[title="更多"]'); b&&b.click(); return !!b })()`);
await sleep(500);
const pop = await ev(`(() => { const p=document.querySelector('.rail-more'); if(!p) return null; const r=p.getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,w:r.width} })()`);
console.log("POPOVER", JSON.stringify({ clicked, pop }));

// 2) 造 10 个 txt 附件塞进聊天框
const added = await ev(`(() => {
  try {
    const input = document.querySelector('.input-row input[type=file]');
    if (!input) return 'no-input';
    const dt = new DataTransfer();
    for (let i=0;i<10;i++) dt.items.add(new File(['x'.repeat(20)], 'test-'+i+'.txt', {type:'text/plain'}));
    input.files = dt.files;
    input.dispatchEvent(new Event('change', {bubbles:true}));
    return input.files.length;
  } catch (e) { return 'ERR:' + e.message; }
})()`);
await sleep(900);
const state = await ev(`(() => {
  const items = document.querySelectorAll('.attach-item').length;
  const btn = document.querySelector('.input-row button.send');
  const r = btn && btn.getBoundingClientRect();
  return { items, hasItemsWrap: !!document.querySelector('.attach-items'),
           notice: document.body.innerText.includes('多出来的'),
           sendVisible: !!(r && r.width>0 && r.bottom <= window.innerHeight && r.top >= 0),
           sendBottom: r ? Math.round(r.bottom) : null, vh: window.innerHeight };
})()`);
console.log("UPLOAD", JSON.stringify({ added, state }));

const ok = clicked === true && pop && pop.right <= 390 && pop.left >= 0
  && state.items <= 9 && state.notice && state.sendVisible;
console.log(ok ? "ALL PASS" : "FAIL");
const shot = await send("Page.captureScreenshot", { format: "png" });
if (shot.result?.data) {
  const fs = await import("node:fs/promises");
  await fs.writeFile("shots/upload_shell_verify.png", Buffer.from(shot.result.data, "base64"));
  console.log("SHOT shots/upload_upload_shell_verify.png".replace("upload_upload", "upload"));
}
ws.close();
process.exit(ok ? 0 : 1);
