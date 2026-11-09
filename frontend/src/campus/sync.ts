/**
 * 校园工具集 · 云端快照同步（规格 2.3）
 *
 * 口径（四句话，改之前先读完）：
 *  1. **整份快照**：不做字段级合并。冲突时 `updated_at` 新者胜、整份覆盖。
 *  2. **localStorage 永远是唯一读源**：云端只是「抄一份」。本地坏了才回读云端，
 *     所以断网、未登录、接口 500 都不影响任何工具的正常使用。
 *  3. **只同步白名单里的 5 个键**。番茄钟（`campus.pomodoro.*`）**永不上报** ——
 *     它已有 `focusSync.ts` 自己的一套同步，再接一遍会打架，
 *     症状是「专注记录偶尔少几条」，而且极难复现。
 *  4. **写入即备份**：`writeJson` 之后 2 秒防抖上报。防抖是为了「拖一门课改 5 个字段」
 *     不产生 5 次整份上传（一条课表快照带 20 门课，整份 20KB 上下）。
 *
 * 为什么需要「上次同步时间戳」：本地快照里没有 `updated_at` 这个字段（它是同步的产物，
 * 不该污染工具的数据结构）。所以单独用 `campus.sync.at.<key>` 记一下本机最后一次
 * 确认过的时间，用来判断「云端这份是不是比我新」。
 */

import { CAMPUS_KEYS } from "./storage/keys";
import { readJson, removeJson, writeJson } from "./storage";

/** 只同步这 5 个键；与后端 core/campus_store.py 的 ALLOWED_TOOLS 逐字对应 */
const SYNC_KEYS: readonly string[] = [
  CAMPUS_KEYS.schedule,
  CAMPUS_KEYS.week,
  CAMPUS_KEYS.term,
  CAMPUS_KEYS.words,
  CAMPUS_KEYS.deadline,
];

const SYNC_SET = new Set(SYNC_KEYS);
const STAMP_PREFIX = "campus.sync.at.";

/** 防抖窗口：拖课表时连续写入，2 秒后才发一次 */
const DEBOUNCE_MS = 2000;

const pending = new Map<string, unknown>();
let timer: ReturnType<typeof setTimeout> | null = null;
let inFlight = false;

function stampKey(key: string): string {
  return STAMP_PREFIX + key;
}

function lastStamp(key: string): number {
  const raw = readJson<number>(stampKey(key));
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

function rememberStamp(key: string, stamp: number): void {
  // silent：这个键本身不该触发下一次上报
  writeJson(stampKey(key), stamp, { silent: true });
}

async function putOne(key: string, payload: unknown, stamp: number): Promise<void> {
  const res = await fetch(`/api/campus/state/${encodeURIComponent(key)}`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload, updated_at: stamp }),
  });

  if (res.status === 409) {
    // 云端更新：直接采用它。**必须静默写**，否则会立刻又触发一次上报，
    // 形成「409 → 写本地 → 上报 → 409」的循环。
    const body = (await res.json().catch(() => null)) as
      | { detail?: { payload?: unknown; updated_at?: number } }
      | null;
    const cloud = body?.detail;
    if (cloud && cloud.payload !== undefined && cloud.payload !== null) {
      writeJson(key, cloud.payload, { silent: true });
      rememberStamp(key, Number(cloud.updated_at) || 0);
    }
    return;
  }

  if (res.ok) {
    rememberStamp(key, stamp);
  }
  // 其它失败（401/500/断网）一律不管：本地数据已经存好了，
  // 下一次写入会自然重试。这里重试会把"离线"变成一个不断重连的循环。
}

async function flush(): Promise<void> {
  if (inFlight) return;
  inFlight = true;
  const entries = Array.from(pending.entries());
  pending.clear();
  try {
    for (const [key, value] of entries) {
      await putOne(key, value, Date.now());
    }
  } finally {
    inFlight = false;
    // 刷的过程中又有新写入：再排一次
    if (pending.size > 0 && timer === null) schedule();
  }
}

function schedule(): void {
  if (typeof window === "undefined") return;
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    void flush();
  }, DEBOUNCE_MS);
}

/**
 * 本地写入钩子（由 `storage/index.ts` 的 `writeJson` 调用）。
 *
 * 不在这里判断"是否登录"：未登录时 PUT 会被 401，代价只是每 2 秒一次无用请求，
 * 而写下去的那次只在用户真改了课表时发生。为它去读一次 `/api/auth/me` 反而更贵。
 */
export function onLocalWrite(key: string, value: unknown): void {
  if (!SYNC_SET.has(key)) return;
  pending.set(key, value);
  schedule();
}

/**
 * 挂载时拉一次（规格：首次「云端没有 + 本地有」要自动上传一次，用户无感）。
 *
 * 三种情况：
 *  · 云端没有这条  → 本地有就传上去（首次开通云同步的那一下）；
 *  · 云端比本机新  → 采用云端（本地被覆盖）；
 *  · 云端不比本机新 → 什么都不做，等本地下次写入时把云端覆盖掉。
 */
export async function pullCampusState(): Promise<void> {
  if (typeof window === "undefined") return;
  let items: { tool: string; payload: unknown; updated_at: number }[] = [];
  try {
    const res = await fetch("/api/campus/state", { credentials: "include" });
    if (!res.ok) return; // 未登录 / 接口没上线：静默跳过，本地照常用
    const body = (await res.json()) as { items?: typeof items };
    items = Array.isArray(body.items) ? body.items : [];
  } catch {
    return; // 断网
  }

  const byTool = new Map(items.map((it) => [it.tool, it]));

  for (const key of SYNC_KEYS) {
    const cloud = byTool.get(key);
    const local = readJson(key);

    if (!cloud) {
      if (local !== null) void putOne(key, local, Date.now());
      continue;
    }

    const cloudStamp = Number(cloud.updated_at) || 0;
    if (local === null) {
      // 这台设备没数据：云端那份就是唯一读源，直接落地
      writeJson(key, cloud.payload, { silent: true });
      rememberStamp(key, cloudStamp);
      continue;
    }

    if (cloudStamp > lastStamp(key)) {
      writeJson(key, cloud.payload, { silent: true });
      rememberStamp(key, cloudStamp);
    }
  }
}

/** 清掉本机的同步水位（「退出登录」或「本地数据重置」时调用，避免旧水位挡住新数据） */
export function resetSyncStamps(): void {
  for (const key of SYNC_KEYS) removeJson(stampKey(key));
}

/** 给调试用的：立刻把挂起的写入发出去（页面上没有入口） */
export async function flushNow(): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  await flush();
}
