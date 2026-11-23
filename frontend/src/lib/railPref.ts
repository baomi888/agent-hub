// 侧栏收起偏好（2026-11-22）
//
// 为什么放在模块里而不是 ConsoleLayout 的 useState：
//   · 要跨刷新保留（localStorage）；
//   · 用 useState + useEffect 读它，会在 effect 里同步 setState —— React 的
//     lint 会拦（级联渲染），而且首帧会先按"展开"渲染一次再跳成"收起"。
// 用 useSyncExternalStore：服务端/水合首帧给 false，客户端读真实值，
// React 自己会把这一帧对齐，不需要我们在 effect 里补一次 setState。
const KEY = "zhisui.rail.collapsed";
const listeners = new Set<() => void>();
/** null = 还没读过；localStorage 读失败也判定为"展开" */
let cached: boolean | null = null;

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function subscribeRail(cb: () => void): () => void {
  listeners.add(cb);
  // 另一个标签页改了偏好 → 这一边跟着变（同源 storage 事件）
  const onStorage = (e: StorageEvent) => {
    if (e.key !== KEY) return;
    cached = null;
    cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

export function getRailCollapsed(): boolean {
  if (cached === null) cached = read();
  return cached;
}

/** SSR 与水合首帧：读不到 localStorage，一律按展开渲染 */
export function getRailCollapsedServer(): boolean {
  return false;
}

export function toggleRailCollapsed(): void {
  const next = !getRailCollapsed();
  cached = next;
  try {
    localStorage.setItem(KEY, next ? "1" : "0");
  } catch {
    /* 隐私模式写不进去：只影响下次刷新，本次仍然生效 */
  }
  listeners.forEach((cb) => cb());
}
