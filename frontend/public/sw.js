/* 知穗Agent · Service Worker
 *
 * 目标：让站点满足「可安装」条件（Android Chrome 要求有 fetch 处理器的 SW），
 * 顺带拿到离线兜底能力。这个站是登录制 + 流式对话，所以缓存策略必须极度保守：
 *
 *   绝不缓存：HTML 文档、/api/*（含 SSE）、RSC 的 ?_rsc= 请求
 *   只缓存：/_next/static/（文件名带 hash，内容不可变）、/fonts/、品牌图标、离线页
 *
 * 为什么不能缓存 HTML：Next 的 App Router 每个路由都可能是动态渲染的，
 * 缓存住会出现「登录了还显示未登录」「看到别人会话」这类事故，代价远大于收益。
 * 所以导航请求一律 network-first，断网时才落离线页。
 *
 * 更新机制：故意不调 skipWaiting()。新版本等用户关掉所有标签后自然激活，
 * 避免「正在聊天的页面」在切换路由时请求到已被清理的旧 chunk 而白屏。
 */

const VERSION = "v3";
const PRECACHE = `baomi-pre-${VERSION}`;
const ASSETS = `baomi-assets-${VERSION}`;

const OFFLINE_URL = "/offline.html";

/* 安装时预取的固定资源：断网时要靠它们撑起离线页 */
const PRECACHE_URLS = [
  OFFLINE_URL,
  "/icon.svg",
  "/mascot.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "/manifest.webmanifest",
];

/* 内容不可变、可长期命中的资源 */
const IMMUTABLE_PREFIXES = ["/_next/static/", "/fonts/"];

/* 需要「先用缓存、后台更新」的品牌资产 */
const STALE_WHILE_REVALIDATE = new Set([
  "/icon.svg",
  "/mascot.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-192.png",
  "/icon-maskable-512.png",
  "/apple-touch-icon.png",
]);

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(PRECACHE);
      // 逐个 add：任何一个 404 都不能让整批失败，否则 SW 直接装不上
      await Promise.all(
        PRECACHE_URLS.map((url) =>
          cache.add(new Request(url, { cache: "reload" })).catch(() => {})
        )
      );
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k !== PRECACHE && k !== ASSETS)
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // 只接管 GET；POST/PATCH 等一律放行（登录、改密码、上传头像都走这里）
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 接口、鉴权、流式对话：一律不碰
  if (url.pathname.startsWith("/api/")) return;

  const offlineResponse = async () => {
    const cache = await caches.open(PRECACHE);
    return (
      (await cache.match(OFFLINE_URL)) ||
      new Response("网络不可用，请检查连接后重试。", {
        status: 503,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      })
    );
  };

  // 页面导航：网络优先，断网落离线页
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        /* 先看系统级在线状态：断网时直接给离线页，不去等 fetch 超时，
           用户打开就能看到"没网"，而不是盯着一片空白等十几秒。
           （实测 Chrome 的 offline 模拟下，SW 内的 fetch 仍可能命中 HTTP 缓存返回旧 HTML，
             所以不能只靠 try/catch 判断。） */
        if (navigator.onLine === false) return offlineResponse();
        try {
          return await fetch(request, { cache: "no-store" });
        } catch {
          return offlineResponse();
        }
      })()
    );
    return;
  }

  // 带 hash 的构建产物与字体：缓存优先，命中即返回
  if (IMMUTABLE_PREFIXES.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(ASSETS);
        const hit = await cache.match(request);
        if (hit) return hit;
        const fresh = await fetch(request);
        if (fresh.ok && fresh.type === "basic") {
          cache.put(request, fresh.clone()).catch(() => {});
        }
        return fresh;
      })()
    );
    return;
  }

  // 品牌图标：先用缓存，后台悄悄更新（换图标后不需要用户手动清缓存）
  if (STALE_WHILE_REVALIDATE.has(url.pathname)) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(PRECACHE);
        const hit = await cache.match(request);
        const network = fetch(request)
          .then((res) => {
            if (res.ok) cache.put(request, res.clone()).catch(() => {});
            return res;
          })
          .catch(() => null);
        return hit || (await network) || Response.error();
      })()
    );
    return;
  }

  // 其余（HTML 片段、RSC 请求、上传文件等）交给浏览器默认行为
});
