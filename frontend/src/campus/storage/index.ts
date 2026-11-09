/**
 * 校园工具集 · 本地存储封装
 *
 * 为什么必须收口：旧版把 localStorage 调用散落在各工具里，键名与结构各写一套，
 * 改结构时没人知道有几处要跟着改。这个文件是板块内**唯一**允许碰 localStorage 的地方。
 *
 * 三条铁律（对应 docs/02 第 3 节）：
 *  1. 读不出就算没有，不抛异常（隐私模式 / 脏数据）；
 *  2. 写不进就返回 false，不让页面崩（配额满）；
 *  3. 兼容旧键时不删旧键，只回写新键。
 */

import { CAMPUS_PREFIX } from "./keys";

/** SSR 阶段没有 window，所有读写都要先过这道判断 */
function storageAvailable(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

/** 读 JSON；键不存在或解析失败一律返回 null */
export function readJson<T = unknown>(key: string): T | null {
  if (!storageAvailable()) return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * 写 JSON；返回是否成功，调用方一般不需要处理失败。
 *
 * `silent: true` = 这次写入**不上报云端**。只有两种情况用它：
 *  1. 同步层自己把云端数据落回本地（否则会形成「写入 → 上报 → 冲突 → 写入」的环）；
 *  2. 同步层记水位（`campus.sync.at.*`），那本来就不是业务数据。
 * 普通业务一律不带 silent —— 「本地改了却没同步上去」是这一层最要命的静默失败。
 */
export function writeJson(
  key: string,
  value: unknown,
  opts?: { silent?: boolean }
): boolean {
  if (!storageAvailable()) return false;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    if (!opts?.silent) notifySync(key, value);
    return true;
  } catch {
    // 配额满 / 隐私模式：记不住这一笔可以，但页面不能挂
    return false;
  }
}

/* ------------------------------------------------------------------ *
 * 外部改写通知（工具页同步刷新）
 *
 * 三条铁律里的第三条只说「写」；这里补的是「读」那一侧：云端数据落回 localStorage
 * 时，**已经挂载**的工具是不知道的 —— 它们只在 mount effect 里读一次
 * （ScheduleTool / DeadlineTool / WordsTool 都是这个写法）。
 *
 * 两个 API 的权限是单向的：
 *  · `notifyExternalWrite` 只给同步层调（云端落地、换账号清空）；
 *  · `subscribeExternalWrite` 只给宿主订阅，工具组件不订阅、也不许直接调前者
 *    —— 工具要知道「数据变了」，唯一正确的方式是被重挂后重新读，见 CampusToolsHub。
 * ------------------------------------------------------------------ */

export type ExternalWriteListener = (key: string, value: unknown) => void;

const externalListeners = new Set<ExternalWriteListener>();

/**
 * 订阅「有人绕过工具，直接改写了我负责的键」。
 *
 * @returns 取消订阅；组件卸载时必须调，否则会往已卸载的实例上 setState。
 */
export function subscribeExternalWrite(listener: ExternalWriteListener): () => void {
  externalListeners.add(listener);
  return () => {
    externalListeners.delete(listener);
  };
}

/**
 * 广播一次外部改写。**只允许同步层调用**（业务代码里写 business key 应走 `writeJson`）。
 *
 * 单个订阅者抛错不能连累其它订阅者：这条通知是「刷新提示」，不是业务流程，
 * 为一个渲染异常让整页崩掉不划算。
 */
export function notifyExternalWrite(key: string, value: unknown): void {
  if (externalListeners.size === 0) return;
  for (const listener of Array.from(externalListeners)) {
    try {
      listener(key, value === undefined ? null : value);
    } catch {
      // 忽略单个订阅者的异常
    }
  }
}

/**
 * 通知同步层（动态 import：`sync.ts` 反过来要用 readJson/writeJson，
 * 静态导入会形成循环依赖，打包器能过但运行时顺序不稳）。
 */
function notifySync(key: string, value: unknown): void {
  void import("../sync")
    .then((mod) => mod.onLocalWrite(key, value))
    .catch(() => {
      // 同步层挂了不能影响本地保存 —— 它此刻已经写成功了
    });
}

/** 删除一个键（仅用于「清空数据」这类用户主动操作，不要用它做迁移清理） */
export function removeJson(key: string): void {
  if (!storageAvailable()) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // 忽略
  }
}

/**
 * 带迁移的读取：新键 → 旧键 → 迁移 → 回写新键。
 *
 * @param newKey     新键（campus.*）
 * @param legacyKeys 旧键列表，按优先级排列
 * @param migrate    把旧结构转成新结构的函数；返回 null 表示这条旧数据不可用
 * @returns 新结构数据；全都没有时返回 null，由调用方决定用示例还是空态
 */
export function readWithMigration<T>(
  newKey: string,
  legacyKeys: readonly string[],
  migrate: (raw: unknown) => T | null,
): T | null {
  const fresh = readJson<T>(newKey);
  if (fresh !== null) return fresh;

  for (const legacyKey of legacyKeys) {
    const raw = readJson(legacyKey);
    if (raw === null) continue;

    const migrated = migrate(raw);
    if (migrated === null) continue;

    // 回写新键，下次直接命中；旧键保留，兼容用户回滚到旧页面
    writeJson(newKey, migrated);
    return migrated;
  }

  return null;
}

/** 判断一个键是否属于本板块（调试面板用） */
export function isCampusKey(key: string): boolean {
  return key.startsWith(CAMPUS_PREFIX);
}
