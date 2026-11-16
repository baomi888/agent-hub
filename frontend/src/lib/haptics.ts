/**
 * 触觉反馈（评审 P2-5，2026-11-16）。
 *
 * 为什么需要一层桥：App 壳是 WebView（`mobile/App.tsx`），页面里切 tab 是 Next 的
 * 客户端路由，**原生侧完全感知不到**。所以要反过来由网页发消息：
 *   window.ReactNativeWebView.postMessage({t:"haptic"}) → 壳 onMessage → expo-haptics
 *
 * 三种环境，一套调用：
 *   1. App 壳里：走 postMessage（iOS 唯一可行的路 —— WKWebView 没有 vibrate）
 *   2. 安卓浏览器 / 安卓 Chrome：`navigator.vibrate` 能用，给一次极短触感
 *   3. iOS Safari、桌面浏览器：两者都没有 → 静默什么都不做（不能报错、不能弹提示）
 *
 * 为什么要 try/catch 包住：`postMessage` 在页面卸载、跨域 iframe 里会抛；
 * 一次触感失败绝不能影响切页 —— 触觉是锦上添花，不是功能。
 */

type HapticKind = "select" | "success";

type RnBridge = { postMessage?: (msg: string) => void };

export function haptic(kind: HapticKind = "select"): void {
  try {
    const bridge = (window as unknown as { ReactNativeWebView?: RnBridge }).ReactNativeWebView;
    if (bridge?.postMessage) {
      bridge.postMessage(JSON.stringify({ t: "haptic", kind }));
      return;
    }
    // 浏览器兜底：安卓 Chrome 支持 vibrate；iOS Safari 上这个函数不存在，直接跳过。
    // 时长刻意压到 8ms —— 再长在安卓上会变成"整机在响"，比没有还糟。
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(kind === "success" ? [8, 40, 8] : 8);
    }
  } catch {
    /* 触觉失败一律吞掉：不能让一次触感把导航搞挂 */
  }
}
