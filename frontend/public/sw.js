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
 *
 * 推送（v4 起）：push / notificationclick 两个事件。
 *   · 用户在别的标签页正看着本站 → **不弹通知**，只 postMessage 让页面自己刷新「今天」条。
 *     通知的价值在于把人叫回来，人已经在场时弹一个横幅只是噪音。
 *     这个判断只能放在 SW 里做：服务端只知道「有人调过接口」，不知道他是不是正盯着屏幕。
 *   · 同一个 tag 的通知会互相替换，不会一天堆三条。
 */

const VERSION = "v5";
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

/* ============================ 推送 ============================ */

/* 服务端发来的 payload 形如
     { title, body, url, tag, force? }
   （见 service/push.py#build_message 与 api/push.py 的测试端点）。
   字段一律容错——通知内容坏了顶多是一条难看的横幅，绝不能让 SW 抛错
   （未捕获异常会让推送被静默丢弃）。

   `force: true` = **无条件弹**，不管用户是不是正开着页面。只给「发送一条测试」
   用：那个按钮的全部意义就是"证明能收到通知"，跳过弹窗等于什么都没发生。
   v5 之前没有这个开关，用户在设置页上当着自己的面点测试 —— 页面可见 → 被下面
   那段静默逻辑吞掉 → 手机上响、电脑上什么都不出现，还以为电脑没订阅。 */
self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "知穗", body: event.data ? event.data.text() : "" };
  }
  const force = payload.force === true;

  event.waitUntil(
    (async () => {
      /* 台账上"用户就在站里"这件事：有可见窗口就只把消息递给页面，不弹通知。
         页面收到后**什么都不用做**——推送内容是从页面正在看的同一份数据算出来的
         （今天还有什么没做完），人就在场，数据不会因为这条通知而变化。
         真要做点什么（比如让「今天」条重取一次），在页面里监听 'message' 即可，
         这里不预设推送内容之外的行为。
         includeUncontrolled：这个 SW 刚激活、旧标签页还没被接管时也能问到。

         ★ 判据是 `focused`，不是单看 `visibilityState`。标签页在活动位置、但
           用户已经切到别的应用时，Chromium 仍报 visible；只按 visible 判会把
           每日提醒静默吞掉——那正是"我明明开了提醒却从来没响过"的来源。
           只有窗口真的在前台（用户此刻就盯着它）才值得压掉这一条。 */
      try {
        const clientList = await self.clients.matchAll({
          type: "window",
          includeUncontrolled: true,
        });
        const looking = clientList.filter(
          (c) => c.visibilityState === "visible" && c.focused === true
        );
        if (looking.length) {
          looking.forEach((c) => {
            try {
              c.postMessage({ type: "baomi:push", payload });
            } catch {
              /* 跨源/已关闭的窗口，忽略 */
            }
          });
          if (!force) return;
        }
      } catch {
        /* 查询客户端失败就当没人看着，继续弹通知 */
      }

      const title = payload.title || "知穗";
      const options = {
        body: payload.body || "",
        icon: "/icon-192.png",
        /* 不设 badge：安卓状态栏那个位图要求单色透明，用彩色图标会显示成一团白块。
           不设时系统回落成应用图标，比"白方块"体面。 */
        tag: payload.tag || "baomi", // 同 tag 替换，不堆叠
        data: { url: payload.url || "/" },
        lang: "zh-CN",
      };
      await self.registration.showNotification(title, options);
    })()
  );
});

/* 点通知：优先把已有的标签页提到前面并导航过去，没有才新开。
   直接 openWindow 会让常用的人攒出一堆标签页——他本来就有这个站在后台开着。 */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    (async () => {
      const clientList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const c of clientList) {
        if (new URL(c.url).origin !== self.location.origin) continue;
        try {
          await c.focus();
          /* navigate 只有部分浏览器实现（Chrome/Edge 有，Safari 没有）。
             没有的话至少把窗口提到前面，用户自己在站内点一下也能到。 */
          if (typeof c.navigate === "function") {
            try {
              await c.navigate(target);
            } catch {
              /* 页面正在跳转时会抛，忽略 */
            }
          } else {
            c.postMessage({ type: "baomi:push-click", url: target });
          }
        } catch {
          /* 聚焦失败就继续找下一个 */
        }
        return;
      }
      await self.clients.openWindow(target);
    })()
  );
});
