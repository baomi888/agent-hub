"use client";

/**
 * 最近使用图片缓存（方案 A，2026-11-25）。
 *
 * 为什么是「最近用过」而不是「整本相册」：
 * 浏览器安全沙箱不允许网页枚举相册（iOS/Android 都不给），只有用户主动选过的
 * 文件浏览器才拿得到。把这些图片存进 IndexedDB，下次点「+」第一屏就能铺出一排
 * 可直接勾选的缩略图 —— 这是纯网页能做的最接近原生 App 相册网格的形态。
 *
 * 隐私纪律：
 *  - 只存用户主动选过的图片，绝不扫描相册；
 *  - 提供 clearRecentImages() 供面板「清空」按钮一键删除；
 *  - 任何 IndexedDB 异常都静默降级（隐私模式/配额满），不影响上传主流程。
 */

const DB_NAME = "baomi.recent-images";
const STORE = "images";
const DB_VERSION = 1;
const MAX_ITEMS = 12;

export interface RecentImage {
  id: string; // name::size，重复选同一张会覆盖并刷新时间（LRU）
  name: string;
  type: string;
  blob: Blob;
  ts: number;
}

function openDb(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    try {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "id" });
          store.createIndex("ts", "ts");
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** 缓存用户本次选过的图片。失败静默降级，绝不阻断发送。 */
export async function saveRecentImages(files: File[]): Promise<void> {
  const imgs = files.filter((f) => f.type.startsWith("image/"));
  if (imgs.length === 0) return;
  const db = await openDb();
  if (!db) return;
  try {
    const now = Date.now();
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, "readwrite");
      const store = tx.objectStore(STORE);
      imgs.forEach((f, i) => {
        const rec: RecentImage = {
          id: `${f.name}::${f.size}`,
          name: f.name,
          type: f.type || "image/jpeg",
          blob: f,
          ts: now + i,
        };
        store.put(rec);
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    });
    await trim(db);
  } catch {
    // 缓存是锦上添花，失败不影响主流程
  } finally {
    db.close();
  }
}

/** 只保留最近 MAX_ITEMS 张，防止 IndexedDB 无限膨胀。 */
async function trim(db: IDBDatabase): Promise<void> {
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const keysReq = store.index("ts").getAllKeys();
    keysReq.onsuccess = () => {
      const keys = keysReq.result as IDBValidKey[];
      const excess = keys.length - MAX_ITEMS;
      for (let i = 0; i < excess; i += 1) store.delete(keys[i]);
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
    tx.onabort = () => resolve();
  });
}

/** 读出最近图片，按时间倒序（最新在前）。失败返回空数组。 */
export async function loadRecentImages(): Promise<RecentImage[]> {
  const db = await openDb();
  if (!db) return [];
  try {
    const rows = await new Promise<RecentImage[]>((resolve) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = () => resolve((req.result as RecentImage[]) || []);
      req.onerror = () => resolve([]);
    });
    return rows.sort((a, b) => b.ts - a.ts).slice(0, MAX_ITEMS);
  } catch {
    return [];
  } finally {
    db.close();
  }
}

/** 清空缓存，供面板「清空」按钮调用。 */
export async function clearRecentImages(): Promise<void> {
  const db = await openDb();
  if (!db) return;
  try {
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    });
  } catch {
    // 静默降级
  } finally {
    db.close();
  }
}
