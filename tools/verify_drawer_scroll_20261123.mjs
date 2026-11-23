// 2026-11-23 验证脚本：
//   A. /knowledge 公开资料库 tab 的滚动 + /library/[kbId] 详情页滚动
//   B. 窄屏抽屉：点「更多」浮层里的模块后抽屉要收起
//   C. 左滑关闭抽屉
// 用法：node tools/verify_drawer_scroll_20261123.mjs <baomi_session_token>
// 前置：本地 uvicorn(8000) + next dev(3100)（BAOMI_BASE 可换目标）
const TOKEN = process.argv[2];
if (!TOKEN) { console.error("需要 token"); process.exit(2); }
const PORT = Number(process.env.BAOMI_CDP_PORT || 9346);
const BASE = process.env.BAOMI_BASE || "http://127.0.0.1:3100";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
import { writeFileSync } from "node:fs";

const page = await (
  await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })
).json();
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
// 手机模拟：390 宽、触屏
await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });

// 注入登录 cookie
await send("Page.navigate", { url: BASE + "/" });
await sleep(2500);
await send("Network.setCookie", { name: "baomi_session", value: TOKEN, url: BASE });

// ★ 摘掉会吃触摸的浮层：新手引导（guide-mask）与公告（ann-page）。
//   公告必须滚到底才解锁 —— 程序滚到底再补发 scroll 事件，点「继续」。
const dismissPopups = async () => {
  await ev(`(() => {
    document.querySelector(".guide-skip")?.click();
    const ann = document.querySelector(".ann-page");
    if (ann) {
      const body = document.querySelector(".ann-body");
      if (body) {
        body.scrollTop = body.scrollHeight;
        body.dispatchEvent(new Event("scroll"));
      }
    }
  })()`);
  await sleep(500);
  await ev(`(() => {
    const go = document.querySelector(".ann-go");
    if (go && !go.disabled) go.click();
  })()`);
  await sleep(600);
};

// ================= A1. /knowledge 公开资料库 tab + 滚动 =================
await send("Page.navigate", { url: BASE + "/knowledge" });
await sleep(4500);
await dismissPopups();

const tabsInfo = await ev(`(() => {
  const tabs = [...document.querySelectorAll(".lib-tabs button")];
  return { n: tabs.length, labels: tabs.map((b) => b.textContent.trim()) };
})()`);
check("A1. 资料库页有两个 tab", tabsInfo.n === 2, JSON.stringify(tabsInfo));

await ev(`[...document.querySelectorAll(".lib-tabs button")].find(b => b.textContent.includes("公开"))?.click()`);
await sleep(2500);

const pubInfo = await ev(`(() => {
  const emb = document.querySelector(".lib-root--embedded");
  const links = [...document.querySelectorAll('a[href*="/library/"]')].map(a => a.getAttribute("href"));
  return { embedded: !!emb, links: links.slice(0, 5) };
})()`);
check("A2. 公开 tab 渲染出广场（embedded）", !!pubInfo.embedded, JSON.stringify(pubInfo));

const scrollInfo = await ev(`(() => {
  const sc = document.querySelector(".console-scroll");
  if (!sc) return { err: "no .console-scroll" };
  sc.scrollTop = 300;
  const after = sc.scrollTop;
  const emb = document.querySelector(".lib-root--embedded");
  return {
    scrollHeight: sc.scrollHeight, clientHeight: sc.clientHeight,
    scrollTopAfter: after,
    embOverflow: emb ? getComputedStyle(emb).overflow : "none",
    embH: emb ? Math.round(emb.getBoundingClientRect().height) : -1,
  };
})()`);
check(
  "A3. /knowledge 公开 tab 可滚动",
  scrollInfo.scrollHeight > scrollInfo.clientHeight && scrollInfo.scrollTopAfter > 0,
  JSON.stringify(scrollInfo),
);
await shot("drawer_scroll_A_knowledge_public.png");

// ================= A4. 点进公开库详情页，验证滚动 =================
if (pubInfo.links?.length) {
  const href = pubInfo.links[0];
  await send("Page.navigate", { url: BASE + href });
  await sleep(3500);
  await dismissPopups();
  const detail = await ev(`(() => {
    const root = document.querySelector(".lib-root");
    if (!root) return { err: "no .lib-root" };
    const cs = getComputedStyle(root);
    root.scrollTop = 200;
    const after = root.scrollTop;
    root.scrollTop = 0;
    return {
      scrollHeight: root.scrollHeight, clientHeight: root.clientHeight,
      scrollTopAfter: after,
      height: cs.height, overflowY: cs.overflowY,
      bodyOverflow: getComputedStyle(document.body).overflow,
      htmlOverflow: getComputedStyle(document.documentElement).overflow,
    };
  })()`);
  check(
    "A4. /library/[kbId] 详情页可滚动",
    detail.scrollHeight > detail.clientHeight && detail.scrollTopAfter > 0,
    JSON.stringify(detail),
  );
  await shot("drawer_scroll_A_kbdetail.png");
}

// ================= B. 抽屉：点「更多」浮层里的模块后要收起 =================
await send("Page.navigate", { url: BASE + "/" });
await sleep(4500);
await dismissPopups();
await shot("drawer_scroll_B1_home.png");

await ev(`(() => { document.querySelector("button.hamburger")?.click(); })()`);
await sleep(1200);
const drawerOpen1 = await ev(`(() => {
  const aside = document.querySelector("aside.side-left");
  return aside ? { open: aside.classList.contains("open"), transform: getComputedStyle(aside).transform.slice(0, 40) } : { open: false };
})()`);
check("B1. 汉堡打开抽屉", drawerOpen1.open === true, JSON.stringify(drawerOpen1));
await shot("drawer_scroll_B2_drawer.png");

await ev(`(() => {
  const btn = [...document.querySelectorAll("aside.side-left .rail-item")].find(b => b.getAttribute("aria-haspopup") === "menu");
  if (btn) btn.click();
})()`);
await sleep(900);
const pop = await ev(`(() => {
  const p = document.querySelector(".rail-more");
  return { open: !!p, items: p ? p.querySelectorAll(".rail-more-item").length : 0 };
})()`);
check("B2. 「更多」浮层打开", pop.open && pop.items > 0, JSON.stringify(pop));
await shot("drawer_scroll_B3_more.png");

const clicked = await ev(`(() => {
  const item = document.querySelector(".rail-more .rail-more-item");
  if (!item) return { href: null };
  const href = item.getAttribute("href");
  item.click();
  return { href };
})()`);
await sleep(2500);
const afterNav = await ev(`(() => {
  const aside = document.querySelector("aside.side-left");
  const mask = document.querySelector(".rail-more-mask");
  return {
    path: location.pathname,
    drawerOpen: aside ? aside.classList.contains("open") : null,
    moreMask: !!mask,
    transform: aside ? getComputedStyle(aside).transform.slice(0, 40) : null,
  };
})()`);
check(
  "B3. 点浮层模块跳转后抽屉收起",
  afterNav.path === clicked.href &&
    (afterNav.drawerOpen === false || afterNav.drawerOpen === null),
  // drawerOpen === null：跳到的是独立路由（/library 等，无 console 外壳），
  // aside 根本不存在 —— 抽屉不可能挡人，同样算过
  JSON.stringify({ clicked, afterNav }),
);
await shot("drawer_scroll_B4_after_nav.png");

// ================= C. 左滑关闭抽屉 =================
await send("Page.navigate", { url: BASE + "/" });
await sleep(4000);
await dismissPopups();
// 再关一次引导（导航回首页可能重新弹）
await ev(`(() => { document.querySelector(".guide-skip")?.click(); })()`);
await sleep(600);
const guideGone = await ev(`(() => !document.querySelector(".guide-mask"))()`);
console.log("guide dismissed:", guideGone);
await ev(`(() => { document.querySelector("button.hamburger")?.click(); })()`);
await sleep(1200);
const reopen = await ev(`(() => {
  const aside = document.querySelector("aside.side-left");
  return aside ? aside.classList.contains("open") : false;
})()`);
check("C0. 抽屉重新打开", reopen === true, JSON.stringify({ reopen }));

// 触摸序列：从抽屉中段往左拖 120px
const touchSeq = [
  { type: "touchStart", touchPoints: [{ x: 140, y: 400 }] },
  { type: "touchMove", touchPoints: [{ x: 80, y: 401 }] },
  { type: "touchMove", touchPoints: [{ x: 20, y: 402 }] },
  { type: "touchEnd", touchPoints: [] },
];
for (const e of touchSeq) {
  await send("Input.dispatchTouchEvent", e);
  await sleep(120);
}
await sleep(900);
const swipe = await ev(`(() => {
  const aside = document.querySelector("aside.side-left");
  return {
    drawerOpen: aside ? aside.classList.contains("open") : null,
    transform: aside ? getComputedStyle(aside).transform.slice(0, 40) : null,
    path: location.pathname,
  };
})()`);
check("C1. 左滑 120px 关闭抽屉", swipe.drawerOpen === false, JSON.stringify(swipe));
await shot("drawer_scroll_C1_after_swipe.png");

// 抽屉关了之后底栏可见可点
const navCheck = await ev(`(() => {
  const items = [...document.querySelectorAll(".tabbar .tabbar-item")];
  const visible = items.filter(i => i.getBoundingClientRect().width > 0).length;
  return { items: items.length, visible };
})()`);
check("C2. 抽屉关闭后底栏可见", navCheck.visible >= 4, JSON.stringify(navCheck));

const fails = results.filter((r) => !r.ok);
console.log("\n==== SUMMARY:", results.length - fails.length, "/", results.length, "passed ====");
if (fails.length) {
  console.log("FAILED:");
  fails.forEach((f) => console.log("  -", f.name, f.detail));
  process.exitCode = 1;
}
ws.close();
process.exit(process.exitCode || 0);
