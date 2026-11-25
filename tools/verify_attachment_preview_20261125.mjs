// 2026-11-25 真浏览器验证：点附件预览（图片放大 / 文档抽正文 / 手机面板入口）
// 用法：BAOMI_CDP_PORT=9353 BAOMI_BASE=http://127.0.0.1:3100 node tools/verify_attachment_preview_20261125.mjs <token>
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要测试 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9353);
const BASE = process.env.BAOMI_BASE || "http://127.0.0.1:3100";
const DOMAIN = new URL(BASE).hostname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

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
const waitFor = async (expr, ms = 15000) => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await ev(`!!(${expr})`) === true) return true;
    await sleep(250);
  }
  return false;
};
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(ok ? "PASS" : "FAIL", name, detail);
};

await send("Page.enable"); await send("Runtime.enable"); await send("DOM.enable"); await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
await send("Network.setCookie", { name: "baomi_session", value: TOKEN, domain: DOMAIN, path: "/" });
// 硬约束 52：启动页 + 公告遮罩用存储补丁摘掉，否则拍到的是弹层
await send("Page.addScriptToEvaluateOnNewDocument", {
  source: `
    (() => {
      const orig = Storage.prototype.getItem;
      Storage.prototype.getItem = function (k) {
        if (typeof k === "string" && (k.indexOf("baomi.announce.v1") === 0 || k.indexOf("baomi.guide.v1") === 0)) return "1";
        return orig.apply(this, arguments);
      };
    })();
  `,
});
await send("Page.navigate", { url: BASE + "/" });
check("textarea出现", await waitFor(`document.querySelector("textarea")`));
check("启动动画退场", await waitFor(`!document.querySelector(".zs-boot-splash")`, 20000));

// 通过隐藏 input 注入两个真实文件：一张图 + 一个 txt
const doc = await send("DOM.getDocument", { depth: 1 });
const nodes = await send("DOM.querySelectorAll", {
  nodeId: doc.result.root.nodeId,
  selector: 'input[type="file"][accept*=".pdf"]',
});
const nodeId = nodes.result?.nodeIds?.[0];
check("找到上传输入框", !!nodeId, `nodeId=${nodeId}`);
// 夹具随仓库提交，脚本换机器也能自包含运行
const files = [
  resolve("tests/fixtures/preview_image.png"),
  resolve("tests/fixtures/preview_text.txt"),
];
const setFiles = await send("DOM.setFileInputFiles", { nodeId, files });
check("文件注入成功", !setFiles.error, setFiles.error || "");
check("两个附件 chip 出现", await waitFor(`document.querySelectorAll(".attach-item").length >= 2`, 15000));

// 图片：点 chip → 大图 → 点图放大 → Esc 关闭
await ev(`document.querySelectorAll(".attach-preview-trigger")[0].click()`);
check("图片预览弹层出现", await waitFor(`document.querySelector(".apv-mask .apv-img")`, 10000));
const imgState = await ev(`(() => {
  const i = document.querySelector(".apv-img");
  return { src: (i?.src || "").slice(0, 5), w: i?.naturalWidth || 0, h: i?.naturalHeight || 0,
           z: getComputedStyle(document.querySelector(".apv-mask")).zIndex };
})()`);
check("大图是本地 blob 且真的加载", imgState.src === "blob:" && imgState.w > 0, JSON.stringify(imgState));
check("预览层级高于拍照/截图弹层", Number(imgState.z) >= 96, `z=${imgState.z}`);
await ev(`document.querySelector(".apv-img").click()`);
check("点大图切换放大", await ev(`!!document.querySelector(".apv-img.is-zoomed")`));
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
check("Esc 关闭预览", await waitFor(`!document.querySelector(".apv-mask")`, 5000));

// 文档：点 txt chip → 后端临时抽正文 → 文本上屏
await ev(`document.querySelectorAll(".attach-preview-trigger")[1].click()`);
check("文档预览正文出现", await waitFor(
  `Array.from(document.querySelectorAll(".pv-text")).some(e => e.textContent.includes("预览正文测试"))`, 15000));
const txtState = await ev(`(() => {
  const t = document.querySelector(".pv-text");
  return { len: (t?.textContent || "").length, hasSecondLine: (t?.textContent || "").includes("第二行") };
})()`);
check("抽出的正文完整", txtState.len > 20 && txtState.hasSecondLine, JSON.stringify(txtState));
const shot = await send("Page.captureScreenshot", { format: "png" });
if (shot.result?.data) { writeFileSync("shots/attachment_preview_doc.png", Buffer.from(shot.result.data, "base64")); console.log("SHOT shots/attachment_preview_doc.png"); }
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });

// 手机面板：点「+」→ attach-sheet 里的附件卡片也能点开预览
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
await sleep(600);
await ev(`document.querySelector(".attach")?.click()`);
check("手机附件面板打开", await waitFor(`document.querySelector(".attach-sheet") && document.querySelector(".attach-sheet-preview-open")`, 8000));
await ev(`document.querySelector(".attach-sheet-preview-open").click()`);
check("手机面板附件可点开预览", await waitFor(`document.querySelector(".apv-mask")`, 8000));
const mobile = await ev(`(() => {
  const mask = document.querySelector(".apv-mask");
  const card = document.querySelector(".apv-card");
  const r = card?.getBoundingClientRect();
  return { vw: innerWidth, right: r ? Math.round(r.right) : 0, left: r ? Math.round(r.left) : 0, hasImg: !!document.querySelector(".apv-img") };
})()`);
check("手机预览没有溢出屏幕", mobile.right <= mobile.vw + 1 && mobile.left >= -1 && mobile.hasImg, JSON.stringify(mobile));
await sleep(400); // 等 pvFade 动画结束再拍，否则截图看着像半透明叠加
const shot2 = await send("Page.captureScreenshot", { format: "png" });
if (shot2.result?.data) { writeFileSync("shots/attachment_preview_mobile.png", Buffer.from(shot2.result.data, "base64")); console.log("SHOT shots/attachment_preview_mobile.png"); }

console.log("TOTAL", results.length, "PASS", results.filter(r => r.ok).length);
ws.close();
process.exit(results.every(r => r.ok) ? 0 : 1);
