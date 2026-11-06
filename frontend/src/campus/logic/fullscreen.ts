/**
 * 校园工具集 · 全屏 / 屏幕常亮 / 横屏（B3）
 *
 * 这三个能力都依赖浏览器 API，支持度各不相同，所以统一约定：
 * **失败不抛错，返回 false**。全屏进不去就退化成普通覆盖层，屏幕不常亮就靠用户自己别锁屏，
 * 横屏锁不上就提示用户手动转 —— 功能可以打折，但不能因为一个 API 不支持就整块用不了。
 *
 * 支持度现状（2026 年）：
 *  - 全屏：桌面浏览器、Android Chrome 均支持；**iPhone Safari 至今不支持元素全屏**
 *    （只允许 `<video>` 全屏，`fullscreenEnabled` 与 webkit 前缀都为假）
 *  - 屏幕常亮：Chrome / Edge / Safari 16.4+ 支持；iOS Safari 不支持
 *  - 横屏锁定：**只有 Android Chrome 支持**，iOS 完全没有这个 API
 *
 * 因此本模块只负责「能用就用」：全屏失败时由 FocusStage 退到**页内全屏**
 * （`position: fixed` 铺满视口 + 安全区适配），横屏锁不上就优先按横屏排版、提示用户自己转。
 * 也就是说 iPhone 上「全屏」照样是能按的，只是少了浏览器那一层系统 UI —— 与
 * `campus/docs/06` 第 2.3 节的结论一致。
 */

/** Wake Lock 句柄。只用到 release，其余字段按需扩展 */
export interface WakeLockLike {
  readonly released: boolean;
  release: () => Promise<void>;
}

type FsDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => void | Promise<void>;
  mozFullScreenElement?: Element | null;
  mozCancelFullScreen?: () => void | Promise<void>;
};

type FsElement = HTMLElement & {
  webkitRequestFullscreen?: () => void | Promise<void>;
  mozRequestFullScreen?: () => void | Promise<void>;
};

type WakeLockNavigator = Navigator & {
  wakeLock?: { request: (type: "screen") => Promise<WakeLockLike> };
};

type OrientationLockable = ScreenOrientation & {
  lock?: (orientation: "landscape" | "portrait") => Promise<void>;
  unlock?: () => void;
};

/** 当前是否已在全屏（含各厂商前缀） */
export function currentFullscreenElement(): Element | null {
  if (typeof document === "undefined") return null;
  const doc = document as FsDocument;
  return doc.fullscreenElement ?? doc.webkitFullscreenElement ?? doc.mozFullScreenElement ?? null;
}

/**
 * 进入全屏。返回是否成功 —— 调用方据此决定要不要退到「页内全屏」（见 FocusStage）。
 *
 * 必须由用户手势触发（点击），否则浏览器会拒绝；这里不做排队重试，
 * 失败就降级，比"偷偷重试"更好理解。
 *
 * `request()` 的 Promise 在**过渡动画结束后**才 resolve，而 `fullscreenElement`
 * 由 fullscreenchange 事件写入，两者之间可能差一个任务队列 —— 立刻读会读到 null，
 * 把一次成功的全屏误判成失败（然后又被叠上一层 CSS 全屏，出现双份）。所以先等一帧再确认。
 */
export async function enterFullscreen(el: HTMLElement | null): Promise<boolean> {
  if (!el) return false;
  const target = el as FsElement;

  const request =
    target.requestFullscreen?.bind(target) ??
    target.webkitRequestFullscreen?.bind(target) ??
    target.mozRequestFullScreen?.bind(target);

  if (!request) return false;

  try {
    await request();
  } catch {
    return false;
  }

  if (currentFullscreenElement() !== null) return true;
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  return currentFullscreenElement() !== null;
}

/** 退出全屏。本来就不在全屏时什么都不做（不报错） */
export async function exitFullscreen(): Promise<void> {
  if (typeof document === "undefined") return;
  if (currentFullscreenElement() === null) return;

  const doc = document as FsDocument;
  const exit = doc.exitFullscreen?.bind(doc) ?? doc.webkitExitFullscreen?.bind(doc) ?? doc.mozCancelFullScreen?.bind(doc);
  if (!exit) return;

  try {
    await exit();
  } catch {
    // 没进全屏却调退出、或用户已用系统手势退出，都会走到这里，忽略即可
  }
}

/**
 * 全屏状态的「可订阅快照」，配合 React 的 `useSyncExternalStore` 使用。
 *
 * 用它而不是 `useEffect + setState`：后者会在 effect 里同步 setState，
 * 引起级联渲染，React 官方也明确不建议。这里订阅的是浏览器这个外部系统，
 * 正是 `useSyncExternalStore` 的用途。
 *
 * 早期还有一个 `watchFullscreen(onChange)` 的回调版（0.5.1 之前 FocusStage 在用），
 * 现在唯一的调用方已经换成这里的快照订阅，回调版没有使用者了，已删除 ——
 * 同一个浏览器事件留两条订阅路径，迟早会出现两条状态不一致。
 */
export function subscribeFullscreen(onChange: () => void): () => void {
  if (typeof document === "undefined") return () => {};

  const handler = () => onChange();
  document.addEventListener("fullscreenchange", handler);
  document.addEventListener("webkitfullscreenchange", handler);

  return () => {
    document.removeEventListener("fullscreenchange", handler);
    document.removeEventListener("webkitfullscreenchange", handler);
  };
}

/** 当前是否全屏。服务端渲染时恒为 false（没有 document） */
export function isFullscreenSnapshot(): boolean {
  return currentFullscreenElement() !== null;
}

/**
 * 请求屏幕常亮。
 *
 * 浏览器在标签页切走时会自动释放锁，切回来必须重新申请 ——
 * 所以这个函数是「申请」，不是「一次性开关」，调用方要挂在 visibilitychange 上。
 */
export async function requestWakeLock(): Promise<WakeLockLike | null> {
  if (typeof navigator === "undefined") return null;
  const nav = navigator as WakeLockNavigator;
  if (!nav.wakeLock) return null;

  try {
    return await nav.wakeLock.request("screen");
  } catch {
    // 电量低于阈值、系统省电模式、页面不可见都会拒绝，静默降级
    return null;
  }
}

/** 释放常亮锁。已经释放过的不重复释放（会抛 InvalidStateError） */
export async function releaseWakeLock(lock: WakeLockLike | null): Promise<void> {
  if (!lock || lock.released) return;
  try {
    await lock.release();
  } catch {
    // 忽略：锁可能已经被浏览器在切走标签页时释放了
  }
}

/**
 * 尝试锁定横屏。只有 Android Chrome 支持，iOS 一定失败。
 * 返回值只用于内部判断，界面上不区分「锁上了」和「用户自己转了」。
 *
 * 注意：`screen.orientation.lock()` 在 iOS 上连方法都不存在，
 * 而 Android Chrome 要求**文档已进入全屏**才给锁 —— 所以调用顺序必须是
 * 「先进全屏 → 再锁横屏」，反过来一定失败。
 */
export async function tryLockLandscape(): Promise<boolean> {
  if (typeof screen === "undefined") return false;
  const orientation = screen.orientation as OrientationLockable | undefined;
  if (!orientation?.lock) return false;

  try {
    await orientation.lock("landscape");
    return true;
  } catch {
    return false;
  }
}

/**
 * 解除横屏锁，回到「用户自己转」的状态。
 *
 * 锁横屏是作用在**整个页面**上的：退出全屏时不解除，用户回到页面内视图、
 * 或者切去别的标签，屏幕还横着，看起来就像这个站把人锁住了。
 * 有些实现里没有 unlock，直接忽略。
 */
export function unlockOrientation(): void {
  if (typeof screen === "undefined") return;
  const orientation = screen.orientation as OrientationLockable | undefined;
  try {
    orientation?.unlock?.();
  } catch {
    // 本来就没锁过、或浏览器不支持，都走这里，忽略
  }
}

/** 当前时刻 `HH:MM`。全屏时看不到系统状态栏，需要自己报时 */
export function formatWallClock(ts: number): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
