// 2026-11-25 手机档麦克风/发送键同尺寸验证（用户截图反馈：两个圆按钮大小不一致）
// 用法：BAOMI_CDP_PORT=9352 node tools/verify_mic_size_20261125.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要本地测试 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9352);
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

await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
await send("Network.enable");
await send("Network.setCookie", {
  name: "baomi_session", value: TOKEN, domain: "127.0.0.1", path: "/",
});
// 麦克风按钮按 capabilities 门控，patch 掉能力探测避免依赖本地密钥配置
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `
    (() => {
      // 硬约束 52：公告/引导遮罩每次导航都摘，用存储补丁而不是删 DOM 节点
      const origGet = Storage.prototype.getItem;
      Storage.prototype.getItem = function (k) {
        if (typeof k === "string" && (k.indexOf("baomi.announce.v1") === 0 || k.indexOf("baomi.guide.v1") === 0)) return "1";
        return origGet.apply(this, arguments);
      };
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        if (url.includes("/api/capture/capabilities")) {
          return Promise.resolve(new Response(JSON.stringify({ voice: true, stream: true }), {
            status: 200, headers: { "Content-Type": "application/json" },
          }));
        }
        return nativeFetch(input, init);
      };
    })();
  `,
});
await send("Page.navigate", { url: BASE + "/" });
check("textarea出现", await waitFor(`document.querySelector("textarea")`));
// splash 动画没结束时 DOM 里虽有输入区，截图却是启动页；等外壳退场再拍
await waitFor(`!document.querySelector(".zs-boot-splash")`, 15000);
// 弹层（引导/公告）遮罩会盖住输入区：只关遮罩，不删 React 节点
await ev(`document.querySelectorAll(".guide-skip,.announce-close,.tb-notice-x,.modal-close").forEach(b => b.click())`);
await sleep(1200);
check("麦克风按钮渲染", await waitFor(`document.querySelector(".input-row .voice-mic")`, 10000));

const size = await ev(`(() => {
  const row = document.querySelector(".input-row");
  const mic = row?.querySelector(".voice-mic");
  const send = row?.querySelector(".send");
  const rect = e => e ? { w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height) } : null;
  const svgRect = e => e ? Math.round(e.querySelector("svg")?.getBoundingClientRect().width || 0) : 0;
  const cs = e => e ? { position: getComputedStyle(e).position } : null;
  const before = mic ? getComputedStyle(mic, "::before") : null;
  return {
    mic: rect(mic), send: rect(send), micSvg: svgRect(mic), sendSvg: svgRect(send),
    rowGap: row ? getComputedStyle(row).gap : "",
    micPos: cs(mic), micBeforeInset: before ? [before.top, before.right, before.bottom, before.left] : null,
    overflow: mic && send ? Math.round(mic.getBoundingClientRect().right - send.getBoundingClientRect().left) : null,
  };
})()`);
console.log("SIZE", JSON.stringify(size));
check("麦克风与发送键等宽等高", size.mic?.w === size.send?.w && size.mic?.h === size.send?.h,
  `mic=${size.mic?.w}x${size.mic?.h} send=${size.send?.w}x${size.send?.h}`);
check("手机档两键均为32px", size.mic?.w === 32 && size.send?.w === 32);
check("两个图标同为14px", size.micSvg === 14 && size.sendSvg === 14,
  `mic=${size.micSvg} send=${size.sendSvg}`);
check("两键未重叠", typeof size.overflow === "number" && size.overflow <= 0,
  `gap=${size.overflow}`);
check("麦克风热区伪元素定位正确", size.micPos?.position === "relative" &&
  Array.isArray(size.micBeforeInset) && size.micBeforeInset[0] === "-6px" && size.micBeforeInset[1] === "0px",
  JSON.stringify(size.micBeforeInset));

const shot = await send("Page.captureScreenshot", { format: "png" });
if (shot.result?.data) { writeFileSync("shots/mic_size_mobile.png", Buffer.from(shot.result.data, "base64")); console.log("SHOT shots/mic_size_mobile.png"); }
console.log("TOTAL", results.length, "PASS", results.filter(r => r.ok).length);
ws.close();
process.exit(results.every(r => r.ok) ? 0 : 1);
