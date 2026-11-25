// 2026-11-25 真浏览器验证：桌面截图框选 + 聊天语音输入
// 浏览器沙箱里无法真的调系统截图器，所以用 canvas.captureStream 伪造一帧，
// 覆盖 getDisplayMedia；ASR 能力/转写用 patch 的 fetch 返回，验证前端链路本身。
// 用法：BAOMI_CDP_PORT=9351 node tools/verify_shot_voice_20261125.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要本地测试 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9351);
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
const shot = async (file) => {
  const r = await send("Page.captureScreenshot", { format: "png" });
  if (r.result?.data) { writeFileSync("shots/" + file, Buffer.from(r.result.data, "base64")); console.log("SHOT", file); }
};
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(ok ? "PASS" : "FAIL", name, detail);
};
const waitFor = async (expr, ms = 15000) => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await ev(`!!(${expr})`) === true) return true;
    await sleep(300);
  }
  return false;
};

await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
// headless 默认深色；强制浅色，避免把"深色下才有的层次问题"当成浅色也正常
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });

// 必须在页面脚本之前打补丁： capabilities 走真实后端会依赖登录，截图器无法在 headless 选源
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `
    (() => {
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        if (url.includes("/api/capture/capabilities")) {
          return Promise.resolve(new Response(JSON.stringify({ voice: true, stream: false }), {
            status: 200, headers: { "Content-Type": "application/json" },
          }));
        }
        if (url.includes("/api/capture/transcribe")) {
          return Promise.resolve(new Response(JSON.stringify({ text: "测试语音识别" }), {
            status: 200, headers: { "Content-Type": "application/json" },
          }));
        }
        return nativeFetch(input, init);
      };
      const proto = navigator.mediaDevices && navigator.mediaDevices.constructor?.prototype;
      if (proto) {
        proto.getDisplayMedia = async function () {
          const c = document.createElement("canvas");
          c.width = 600; c.height = 400;
          const x = c.getContext("2d");
          x.fillStyle = "#F5F0E1"; x.fillRect(0, 0, 600, 400);
          x.fillStyle = "#C8892A"; x.fillRect(30, 30, 540, 340);
          x.fillStyle = "#3D2F1A"; x.font = "32px sans-serif"; x.fillText("FAKE SCREEN", 140, 210);
          return c.captureStream(10);
        };
        proto.getUserMedia = async function (constraints) {
          if (!constraints?.audio) throw new Error("unexpected video request");
          const AC = window.AudioContext || window.webkitAudioContext;
          const ctx = new AC({ sampleRate: 16000 });
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          const dest = ctx.createMediaStreamDestination();
          osc.frequency.value = 220; gain.gain.value = 0.05;
          osc.connect(gain); gain.connect(dest); osc.start();
          window.__fakeAudio = { ctx, osc };
          return dest.stream;
        };
      }
    })();
  `,
});

await send("Page.navigate", { url: BASE + "/" });
await sleep(1800);
await send("Network.setCookie", { name: "baomi_session", value: TOKEN, url: BASE });
await send("Page.navigate", { url: BASE + "/" });
if (!(await waitFor(`document.querySelector(".composer-input")`, 25000))) {
  console.error("没等到聊天输入框（可能在登录页）");
  process.exit(2);
}
const dismiss = async () => {
  await ev(`document.querySelector(".guide-skip")?.click()`);
  await sleep(350);
  await ev(`(() => { const b = document.querySelector(".ann-body"); if (b) { b.scrollTop = b.scrollHeight; b.dispatchEvent(new Event("scroll")); } })()`);
  await sleep(300);
  await ev(`(() => { const g = document.querySelector(".ann-go"); if (g && !g.disabled) g.click(); })()`);
  await sleep(350);
};
await dismiss();

// ================= A. 截图：整帧 → 框选 → 裁出选区 =================
await ev(`document.querySelector("button.attach")?.click()`);
await sleep(400);
const menuOpen = await ev(`[...document.querySelectorAll(".attach-popover .attach-menu-item")].some(b => b.textContent.includes("截图提问"))`);
check("A1 桌面浮层有截图提问入口", menuOpen === true, String(menuOpen));
await ev(`[...document.querySelectorAll(".attach-popover .attach-menu-item")].find(b => b.textContent.includes("截图提问"))?.click()`);
await waitFor(`document.querySelector(".shot-crop-mask")`, 12000);
const a2 = await ev(`(() => {
  const box = document.querySelector(".shot-crop-image-box");
  const r = box?.getBoundingClientRect();
  return { mask: !!document.querySelector(".shot-crop-mask"), w: Math.round(r?.width || 0), h: Math.round(r?.height || 0),
    buttons: [...document.querySelectorAll(".shot-crop-actions button")].map(b => b.textContent.trim()) };
})()`);
check("A2 框选层出现且拿到整帧", a2?.mask === true && a2?.w > 100 && a2?.h > 80 && a2?.buttons?.includes("整张使用") && a2?.buttons?.includes("用这一块"), JSON.stringify(a2));
await shot("shotvoice_A_cropper.png");

// 在图片上拖一个框（鼠标按下 → 移动 → 松开）
const r = await ev(`(() => { const b = document.querySelector(".shot-crop-image-box").getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; })()`);
const p1 = { x: Math.round(r.x + r.w * 0.2), y: Math.round(r.y + r.h * 0.2) };
const p2 = { x: Math.round(r.x + r.w * 0.6), y: Math.round(r.y + r.h * 0.6) };
await send("Input.dispatchMouseEvent", { type: "mousePressed", x: p1.x, y: p1.y, button: "left", buttons: 1, clickCount: 1 });
await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: p2.x, y: p2.y, button: "left", buttons: 1 });
await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: p2.x, y: p2.y, button: "left", buttons: 0, clickCount: 1 });
await sleep(350);
const a3 = await ev(`(() => {
  const sel = document.querySelector(".shot-crop-selection");
  const r = sel?.getBoundingClientRect();
  const label = sel?.querySelector(".shot-crop-size")?.textContent?.trim();
  const btn = [...document.querySelectorAll(".shot-crop-actions button")].find(b => b.textContent.trim() === "用这一块");
  return { hasSel: !!sel, w: Math.round(r?.width || 0), h: Math.round(r?.height || 0), label, enabled: btn && !btn.disabled };
})()`);
check("A3 拖拽产生选区且显示源像素尺寸", a3?.hasSel === true && a3?.w > 40 && a3?.h > 40 && /\d+\s*×\s*\d+/.test(a3?.label || "") && a3?.enabled === true, JSON.stringify(a3));
await shot("shotvoice_A_selected.png");

await ev(`[...document.querySelectorAll(".shot-crop-actions button")].find(b => b.textContent.trim() === "用这一块")?.click()`);
await waitFor(`document.querySelector(".attach-item img")`, 8000);
const a4 = await ev(`(async () => {
  const img = document.querySelector(".attach-item img");
  if (!img) return { count: 0 };
  const blob = await fetch(img.src).then(r => r.blob());
  const bmp = await createImageBitmap(blob);
  return { count: document.querySelectorAll(".attach-item").length, w: bmp.width, h: bmp.height, maskGone: !document.querySelector(".shot-crop-mask") };
})()`);
check("A4 选区裁成附件（约 240×160，不是整帧 600×400）",
  a4?.count === 1 && a4?.w > 180 && a4?.w < 300 && a4?.h > 120 && a4?.h < 220 && a4?.maskGone === true, JSON.stringify(a4));

// ================= B. 语音：能力门控 → 录音 → 追加文字 =================
const b1 = await ev(`(() => { const b = document.querySelector(".voice-mic"); if (!b) return null; const r = b.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), label: b.getAttribute("aria-label") }; })()`);
check("B1 后端开放语音后麦克风出现且与发送键同高", b1?.w === 40 && b1?.h === 40 && b1?.label === "用说的代替打字", JSON.stringify(b1));
await ev(`document.querySelector(".voice-mic")?.click()`);
await waitFor(`document.querySelector(".voice-live-strip")`, 8000);
await sleep(1200);
const b2 = await ev(`(() => { const b = document.querySelector(".voice-mic"); return { rec: b?.getAttribute("data-rec"), text: document.querySelector(".voice-live-text")?.textContent?.trim() || "" }; })()`);
check("B2 录音中有停止态和边说边出的提示", b2?.rec === "1" && b2?.text?.includes("正在听"), JSON.stringify(b2));
await shot("shotvoice_B_recording.png");
await ev(`document.querySelector(".voice-mic")?.click()`);
await waitFor(`document.querySelector(".composer-input")?.value.includes("测试语音识别")`, 12000);
const b3 = await ev(`(() => ({ text: document.querySelector(".composer-input")?.value || "", stripGone: !document.querySelector(".voice-live-strip") }))()`);
check("B3 说完转写结果追加进输入框", b3?.text?.includes("测试语音识别") && b3?.stripGone === true, JSON.stringify(b3));

console.log("RESULT", results.filter((x) => x.ok).length + "/" + results.length);
ws.close();
process.exit(results.every((x) => x.ok) ? 0 : 1);
