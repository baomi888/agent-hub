/**
 * Web Push 的浏览器侧全部逻辑。
 *
 * 三个状态必须分清，混在一起就会做出「点了没反应」的交互：
 *
 *   unsupported      浏览器根本没有 Push API（微信内置浏览器、Firefox 隐私窗口等）
 *   ios-needs-install iOS 16.4 以下、或 16.4+ 但还没「添加到主屏」——**这不是不支持**，
 *                    是差一步安装。这时唯一有意义的话是「先装到桌面」，
 *                    而不是「你的浏览器不支持」。
 *   ready            可以走授权流程
 *
 * ★★ 一个必须知道的事实：**安卓的 Web Push 底层一律走 Google 的 FCM
 *   （fcm.googleapis.com），这个域名在中国大陆连不通。**
 *   注意这不是"换一个浏览器就好"的问题：安卓上的 Edge、三星浏览器、
 *   各家 Chromium 壳全都用系统 FCM，换浏览器不解决。真正可用的只有：
 *     · iPhone/iPad 装到主屏（走苹果 APNs）
 *     · Windows 上的 Edge（走微软 WNS）
 *     · 手机装了 GMS 且能连 Google 的环境
 *   ⇒ 安卓的兜底是**日历订阅**（api/calendar.py + service/calendar.py）：
 *     系统日历用本机定时器，不吃网络。失败态与设置页都把用户往那边送。
 *   前端不做「你的手机收不到」的判断（网络环境会变、判断容易误伤），
 *   但授权失败时把浏览器的原始原因原样给用户看，别吞掉。
 */

import { api } from "./api";
import type { PushDevice } from "./types";
import { detectPlatform, type Platform } from "./pwa";

export type PushSupport = "insecure" | "unsupported" | "ios-needs-install" | "ready";

/** 浏览器侧能不能订阅。不看服务端（服务端能力在 /api/push/status 里）。 */
export function pushSupport(): PushSupport {
  if (typeof window === "undefined") return "unsupported";
  // ★★ 第一判必须是安全上下文，不能放在 "PushManager" in window 之后。
  //
  // 为什么：Service Worker 与 Push 只在安全上下文里可用，而**局域网 IP 的
  // http 不算安全上下文**（只有 localhost / 127.0.0.1 这两个特例算）。
  // 手机调试时走的是 http://192.168.x.x:3100、http://172.20.10.2:3100，
  // 于是 iOS 上 PushManager 恒为 undefined —— 后面的分支会把这种情形
  // 判成 "ios-needs-install"，告诉人「先添加到主屏」。这句话是**错的**：
  // 加到主屏也一样没有，因为主屏 web app 仍然要求安全上下文。
  // 本项目踩过一次：用户照提示加到主屏，回来一看还是开不了，去查组件。
  if (!window.isSecureContext) return "insecure";
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    // iOS 上「没装到主屏」的表现就是没有 PushManager（Safari 的限制），
    // 报「不支持」会把人劝退，其实只差一次「添加到主屏」
    return detectPlatform() === "ios" ? "ios-needs-install" : "unsupported";
  }
  if (!("Notification" in window)) return "unsupported";
  return "ready";
}

/** 当前浏览器的通知权限：'default'（还没问过）| 'granted' | 'denied'（拒绝过，改不回来） */
export function notificationPermission(): NotificationPermission | "unsupported" {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission;
}

/** base64url → Uint8Array。VAPID 公钥必须转成字节数组喂给 applicationServerKey，
 *  直接传字符串在现代 Chrome 上会抛 TypeError（它只认 ArrayBuffer/TypedArray）。 */
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** 给一个必然要设上限的 Promise 加超时。
 *
 * ★★ 为什么这两步都要超时：实测 Google Chrome 在连不上 FCM 时
 *   （中国大陆就是这样）`pushManager.subscribe()` **不报错、也不返回**，
 *   它会一直挂着等 Google 的注册服务。没有超时的话，用户点「打开提醒」
 *   看到的是一个永远转圈的按钮 —— 比直接说"不行"糟得多。
 *   同理 `serviceWorker.ready` 在没有 SW 的环境里也是永久 pending。 */
function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error(label)), ms)),
  ]);
}

const READY_TIMEOUT = 8000;
// 订阅本身要连推送服务：Edge/WNS 实测 1–3 秒，给到 20 秒已经非常宽松
const SUBSCRIBE_TIMEOUT = 20000;

/** 这台设备当前是否已有订阅（浏览器侧的事实，不等服务端） */
export async function currentEndpoint(): Promise<string | null> {
  if (pushSupport() !== "ready") return null;
  try {
    const reg = await withTimeout(
      navigator.serviceWorker.ready,
      READY_TIMEOUT,
      "service worker 未就绪"
    );
    const sub = await reg.pushManager.getSubscription();
    return sub ? sub.endpoint : null;
  } catch {
    return null;
  }
}

/** endpoint 的短指纹。**必须和服务端 `push_store.endpoint_key` 算出同一个值**：
 *  服务端是 `sha256(endpoint).hexdigest()[:12]`，这里是同算法的浏览器版。
 *  两边不一致 → 永远认不出本机，界面又会退回"账号级"的糊涂状态。 */
export async function endpointKey(endpoint: string): Promise<string> {
  try {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("")
      .slice(0, 12);
  } catch {
    return "";
  }
}

/**
 * 从账号的设备列表里认出**本机**。
 *
 * ★★ 这是「账号有几台」和「这台开没开」的分界点。少了这一步，设置页会拿
 *   账号级的 `subscribed` 冒充本机状态：手机开了 → 电脑上也显示"已开启"，
 *   于是"发一条测试"把通知发到手机、"关掉"又什么都没删，用户完全看不懂。
 */
export async function findMyDevice(list: PushDevice[]): Promise<PushDevice | null> {
  if (!list?.length) return null;
  const ep = await currentEndpoint();
  if (!ep) return null;
  const key = await endpointKey(ep);
  if (!key) return null;
  return list.find((d) => d.ep_key === key && !d.disabled) || null;
}

/** 设备在界面上叫什么。用户认的是"iPhone""Windows 上的 Edge"，不是 UA 原文。 */
export function deviceLabel(platform: string, ua: string): string {
  const u = ua || "";
  if (platform === "ios" || /iPhone|iPad|iPod/.test(u)) {
    return /iPad/.test(u) ? "iPad" : "iPhone";
  }
  if (platform === "android" || /Android/.test(u)) return "安卓手机";
  const os = /Windows/i.test(u)
    ? "Windows"
    : /Macintosh|Mac OS X/i.test(u)
      ? "Mac"
      : /CrOS/i.test(u)
        ? "ChromeOS"
        : /Linux/i.test(u)
          ? "Linux"
          : "";
  const browser = /Edg\//.test(u)
    ? "Edge"
    : /Firefox\//.test(u)
      ? "Firefox"
      : /OPR\//.test(u)
        ? "Opera"
        : /Chrome\//.test(u)
          ? "Chrome"
          : /Safari\//.test(u)
            ? "Safari"
            : "";
  if (os && browser) return `${os} 上的 ${browser}`;
  return os || browser || "一台设备";
}

export type EnableResult =
  | { ok: true; devices: number }
  | { ok: false; reason: "denied" | "unsupported" | "no-key" | "subscribe-failed"; detail?: string };

/**
 * 走完整条路：要权限 → subscribe → 上报后端。
 *
 * 失败原因要**分得够细**，因为用户能采取的行动完全不同：
 *   denied          → 只能去浏览器/系统设置里改，给指引
 *   subscribe-failed → 多半是连不上推送服务（FCM 在国内不通就长这样），
 *                      文案要说「换 Edge 或装到主屏再试」
 */
export async function enablePush(): Promise<EnableResult> {
  if (pushSupport() !== "ready") return { ok: false, reason: "unsupported" };

  const status = await api.push.status();
  const key = status?.key || "";
  if (!status?.supported || !key) return { ok: false, reason: "no-key" };

  let permission: NotificationPermission;
  try {
    // ★ 必须由用户手势触发（点按钮）。放在页面加载时调，Chrome 会直接拒绝，
    //   而且那次拒绝是**永久的**——这个代价太大，不能试。
    permission = await Notification.requestPermission();
  } catch (e) {
    return { ok: false, reason: "subscribe-failed", detail: String(e) };
  }
  if (permission !== "granted") return { ok: false, reason: "denied" };

  try {
    const reg = await withTimeout(
      navigator.serviceWorker.ready,
      READY_TIMEOUT,
      "service worker 未就绪"
    );
    const existing = await reg.pushManager.getSubscription();
    const sub =
      existing ||
      (await withTimeout(
        reg.pushManager.subscribe({
          // Chrome 强制要求为 true（用它自己的通知展示），Safari 也接受
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(key) as BufferSource,
        }),
        SUBSCRIBE_TIMEOUT,
        "订阅超时：这台设备连不上推送服务"
      ));
    const json = sub.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } };
    const endpoint = json.endpoint || sub.endpoint;
    const p256dh = json.keys?.p256dh;
    const auth = json.keys?.auth;
    if (!endpoint || !p256dh || !auth) {
      return { ok: false, reason: "subscribe-failed", detail: "浏览器没有返回完整订阅" };
    }
    const platform: Platform = detectPlatform();
    const res = await api.push.subscribe({
      endpoint,
      keys: { p256dh, auth },
      ua: navigator.userAgent,
      platform,
    });
    return { ok: true, devices: res.devices };
  } catch (e) {
    // AbortError「Registration failed - push service error」= 连不上 FCM/APNs/WNS。
    return { ok: false, reason: "subscribe-failed", detail: String(e) };
  }
}

/** 关掉：先撤销浏览器订阅（否则它还会继续在后台接收），再删服务端记录 */
export async function disablePush(): Promise<boolean> {
  if (pushSupport() !== "ready") return false;
  try {
    const reg = await withTimeout(
      navigator.serviceWorker.ready,
      READY_TIMEOUT,
      "service worker 未就绪"
    );
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return true;
    const endpoint = sub.endpoint;
    await sub.unsubscribe().catch(() => {});
    await api.push.unsubscribe(endpoint);
    return true;
  } catch {
    return false;
  }
}
