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

/** 写 JSON；返回是否成功，调用方一般不需要处理失败 */
export function writeJson(key: string, value: unknown): boolean {
  if (!storageAvailable()) return false;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    // 配额满 / 隐私模式：记不住这一笔可以，但页面不能挂
    return false;
  }
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
