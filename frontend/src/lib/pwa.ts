/**
 * PWA 安装相关的平台判断与小工具。
 *
 * beforeinstallprompt 只在 Chromium 系浏览器里才有，而且可能在任何时刻触发一次 ——
 * 所以必须在应用启动时就挂上监听并把它扣住（preventDefault），
 * 等用户真正去点「安装」的时候再拿出来用。晚一步就永远拿不到了。
 */

export type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

declare global {
  interface Window {
    __baomiInstallPrompt?: InstallPromptEvent;
  }
}

export type Platform = "ios" | "android" | "desktop" | "other";

/** 判断当前是不是"已经从桌面图标打开"的状态（此时不该再提示安装） */
export function isInstalled(): boolean {
  if (typeof window === "undefined") return false;
  const standalone = window.matchMedia("(display-mode: standalone)").matches;
  // iOS Safari 不实现 display-mode，用自己的私有标记
  const iosStandalone = (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return standalone || iosStandalone;
}

export function detectPlatform(): Platform {
  if (typeof navigator === "undefined") return "other";
  const ua = navigator.userAgent;
  // iPadOS 13+ 的 UA 伪装成 Mac，要靠触点数区分
  const isIOS =
    /iPad|iPhone|iPod/.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (isIOS) return "ios";
  if (/Android/i.test(ua)) return "android";
  if (/Windows|Macintosh|Linux|CrOS/i.test(ua)) return "desktop";
  return "other";
}

/**
 * 给 useSyncExternalStore 用的订阅函数。
 *
 * 这些状态（平台、是否已安装、有没有安装事件）都住在浏览器里，
 * 既不能放 useState 初始值（服务端没有 window，会 hydration 打架），
 * 也不该在 useEffect 里 setState（React 会判为级联渲染，eslint 直接报错）。
 * useSyncExternalStore 就是给"外部数据源"准备的官方答案。
 */
export function subscribePwa(onChange: () => void): () => void {
  const events = ["baomi:installable", "baomi:installed"];
  events.forEach((e) => window.addEventListener(e, onChange));
  return () => events.forEach((e) => window.removeEventListener(e, onChange));
}

/** SSR 期间一律当作"未安装、非移动端"，客户端接管后再按真实值渲染 */
export const PWA_SERVER_SNAPSHOT = {
  platform: "other" as Platform,
  installed: false,
  canPrompt: false,
  eligible: false,
};

/** 监听安装事件并扣住它；返回取消函数（供 useEffect 清理） */
export function captureInstallPrompt(): () => void {
  if (typeof window === "undefined") return () => {};

  const onPrompt = (e: Event) => {
    e.preventDefault();
    window.__baomiInstallPrompt = e as InstallPromptEvent;
    window.dispatchEvent(new Event("baomi:installable"));
  };
  const onInstalled = () => {
    window.__baomiInstallPrompt = undefined;
    window.dispatchEvent(new Event("baomi:installed"));
  };

  window.addEventListener("beforeinstallprompt", onPrompt);
  window.addEventListener("appinstalled", onInstalled);
  return () => {
    window.removeEventListener("beforeinstallprompt", onPrompt);
    window.removeEventListener("appinstalled", onInstalled);
  };
}

/** 触发浏览器自己的安装弹窗；没有可用事件时返回 false，由调用方给文字说明 */
export async function promptInstall(): Promise<boolean> {
  const evt = typeof window !== "undefined" ? window.__baomiInstallPrompt : undefined;
  if (!evt) return false;
  try {
    await evt.prompt();
    const choice = await evt.userChoice;
    window.__baomiInstallPrompt = undefined;
    return choice.outcome === "accepted";
  } catch {
    return false;
  }
}
