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
 *  5. **落回本地要广播**：云端那份写进 localStorage 时工具早已挂载完毕、读完了旧值，
 *     所以每次「采用云端」都走 `adoptCloud` —— 值真的变了才 `notifyExternalWrite`，
 *     由宿主（CampusToolsHub）决定重挂还是提示，见那里的注释。
 *
 * 为什么需要「上次同步时间戳」：本地快照里没有 `updated_at` 这个字段（它是同步的产物，
 * 不该污染工具的数据结构）。所以单独用 `campus.sync.at.<key>` 记一下本机最后一次
 * 确认过的时间，用来判断「云端这份是不是比我新」。
 */

import { CAMPUS_KEYS } from "./storage/keys";
import { notifyExternalWrite, readJson, removeJson, writeJson } from "./storage";

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
const DIRTY_PREFIX = "campus.sync.dirty.";
const OWNER_KEY = "campus.sync.owner";

/** 防抖窗口：拖课表时连续写入，2 秒后才发一次 */
const DEBOUNCE_MS = 2000;

const pending = new Map<string, { value: unknown; stamp: number }>();
let timer: ReturnType<typeof setTimeout> | null = null;
let inFlight = false;
let hooksInstalled = false;

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

function dirtyKey(key: string): string {
  return DIRTY_PREFIX + key;
}

/** 返回「本地这次改动发生在什么时候」；0 表示没有未上报的改动 */
function dirtyStamp(key: string): number {
  const raw = readJson<number>(dirtyKey(key));
  return typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? raw : 0;
}

function markDirty(key: string, stamp: number): void {
  writeJson(dirtyKey(key), stamp, { silent: true });
}

/**
 * 清 dirty 标记。只在「这次上报确实是最后一笔本地改动」时清：
 * PUT 在飞的时候用户又改了一次，新的 dirty 时间会更晚，不能把它一起清掉。
 */
function clearDirty(key: string, stamp: number): void {
  if (dirtyStamp(key) <= stamp) removeJson(dirtyKey(key));
}

/** 两份整份快照是不是同一份内容（都是 JSON，比字符串够用，也不引依赖） */
function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

/**
 * 采用云端那份：静默写本地 + 记水位 + **通知已挂载的工具**。
 *
 * 为什么要先比一次旧值：工具 mount 后会把读到的存档原样回写一次（`ready` 闸门之后
 * 那个 write 效应），如果值没变也叫醒界面，就会变成「重挂 → 回写 → 又被当成外部更新」
 * 的来回弹。只有内容真变了才通知。
 */
function adoptCloud(key: string, payload: unknown, stamp: number): void {
  const before = readJson(key);
  writeJson(key, payload, { silent: true });
  rememberStamp(key, stamp);
  if (!sameValue(before, payload)) notifyExternalWrite(key, payload);
}

async function putOne(key: string, payload: unknown, stamp: number): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/campus/state/${encodeURIComponent(key)}`, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload, updated_at: stamp }),
    });
  } catch {
    // 断网：dirty 标记留着，下次进工具页（或 pagehide）再试。
    // 这里不抛：一次网络错误不该让同一次 flush 里后面几个键也不发。
    return;
  }

  if (res.status === 409) {
    // 云端更新：直接采用它。**必须静默写**，否则会立刻又触发一次上报，
    // 形成「409 → 写本地 → 上报 → 409」的循环。
    const body = (await res.json().catch(() => null)) as
      | { detail?: { payload?: unknown; updated_at?: number } }
      | null;
    const cloud = body?.detail;
    if (cloud && cloud.payload !== undefined && cloud.payload !== null) {
      adoptCloud(key, cloud.payload, Number(cloud.updated_at) || 0);
      clearDirty(key, stamp);
    }
    return;
  }

  if (res.ok) {
    // 服务端可能把未来时间戳夹到 now+60s，以服务端值为准记水位，
    // 否则本机会记着一个云端并不存在的更大值，之后的比较全错。
    let acked = stamp;
    try {
      const body = (await res.json()) as { updated_at?: unknown };
      const serverStamp = Number(body?.updated_at);
      if (Number.isFinite(serverStamp) && serverStamp > 0) acked = serverStamp;
    } catch {
      // 返回体不是 JSON 也不影响写入成功，用本地时间戳兜底
    }
    rememberStamp(key, acked);
    clearDirty(key, stamp);
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
    for (const [key, item] of entries) {
      // 每个键独立 try：一个失败不能让后面的键跟着丢。
      try {
        await putOne(key, item.value, item.stamp);
      } catch {
        // putOne 已吞掉网络错误；这里兜住 JSON/意外异常
      }
    }
  } finally {
    inFlight = false;
    // 刷的过程中又有新写入：再排一次
    if (pending.size > 0 && timer === null) schedule();
  }
}

function schedule(): void {
  if (typeof window === "undefined") return;
  installHooks();
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
  // 先落 dirty 再进内存队列：2 秒防抖窗口内关页面 / 刷新时，
  // pending 会随内存消失，dirty 还在，下次进页面能把这次改动补传上去。
  const stamp = Date.now();
  markDirty(key, stamp);
  pending.set(key, { value, stamp });
  schedule();
}

/**
 * `pagehide` / 切到后台时把挂起的写入发出去。
 *
 * 只靠 2 秒定时器的话，「改完课表立刻关标签页」这一笔会丢在内存里。
 * `visibilitychange` 也监听是因为手机切 App 不一定触发 pagehide。
 */
function installHooks(): void {
  if (hooksInstalled || typeof window === "undefined") return;
  hooksInstalled = true;
  const flushOnHide = () => {
    void flushNow();
  };
  window.addEventListener("pagehide", flushOnHide);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushOnHide();
  });
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
  installHooks();
  let items: { tool: string; payload: unknown; updated_at: number }[] = [];
  let owner = "";
  try {
    const res = await fetch("/api/campus/state", { credentials: "include" });
    if (!res.ok) return; // 未登录 / 接口没上线：静默跳过，本地照常用
    const body = (await res.json()) as { items?: typeof items; owner?: unknown };
    items = Array.isArray(body.items) ? body.items : [];
    owner = typeof body.owner === "string" ? body.owner : "";
  } catch {
    return; // 断网
  }

  // 换账号：本机缓存属于上一个人。直接清掉，否则「云端没有 + 本地有」
  // 会把 A 的课表 / DDL / 单词进度写进 B 的账号（隐私事故，不只是脏数据）。
  if (owner) {
    const known = readJson<string>(OWNER_KEY);
    if (known && known !== owner) clearLocalSnapshots();
    writeJson(OWNER_KEY, owner, { silent: true });
  }

  const byTool = new Map(items.map((it) => [it.tool, it]));

  for (const key of SYNC_KEYS) {
    const cloud = byTool.get(key);
    const local = readJson(key);
    const dirty = dirtyStamp(key);

    // 本地有未上报的改动：以「改动时间 vs 云端时间」决定谁赢。
    // 不能只看「云端比我记的水位新」——水位只在上报成功后才更新，
    // 离线改完再进来时云端那份可能更旧，直接采用会把本地改动覆盖掉。
    if (dirty > 0 && local !== null) {
      const cloudStamp = cloud ? Number(cloud.updated_at) || 0 : 0;
      if (!cloud || dirty >= cloudStamp) {
        await putOne(key, local, dirty);
      } else {
        adoptCloud(key, cloud.payload, cloudStamp);
        clearDirty(key, dirty);
      }
      continue;
    }
    if (local === null) removeJson(dirtyKey(key));

    if (!cloud) {
      if (local !== null) {
        const stamp = Date.now();
        markDirty(key, stamp);
        await putOne(key, local, stamp);
      }
      continue;
    }

    const cloudStamp = Number(cloud.updated_at) || 0;
    if (local === null) {
      // 这台设备没数据：云端那份就是唯一读源，直接落地
      adoptCloud(key, cloud.payload, cloudStamp);
      continue;
    }

    if (cloudStamp > lastStamp(key)) {
      adoptCloud(key, cloud.payload, cloudStamp);
    }
  }
}

/** 只清 5 个业务快照（换账号时用，不动账号级的水位之外的东西） */
function clearLocalSnapshots(): void {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  pending.clear();
  for (const key of SYNC_KEYS) {
    // 只有真的存过东西才通知：本来就没有的键去通知一次，会让界面白重挂一遍
    const had = readJson(key) !== null;
    removeJson(key);
    removeJson(stampKey(key));
    removeJson(dirtyKey(key));
    if (had) notifyExternalWrite(key, null);
  }
}

/** 清掉本机的同步水位与 dirty 标记（本地数据重置时调用，避免旧水位挡住新数据） */
export function resetSyncStamps(): void {
  for (const key of SYNC_KEYS) {
    removeJson(stampKey(key));
    removeJson(dirtyKey(key));
  }
  removeJson(OWNER_KEY);
}

/**
 * 退出登录：连业务快照一起清。
 *
 * 为什么不只清水位：同一台浏览器换人登录时，上一个人的课表 / DDL / 单词进度
 * 不能出现在下一个人的工具集里；更糟的是「云端为空 + 本地有」会自动上传，
 * 等于把 A 的数据写进 B 的账号。清掉是最小且可解释的做法。
 */
export function resetCampusLocal(): void {
  clearLocalSnapshots();
  resetSyncStamps();
}

/** 给调试用的：立刻把挂起的写入发出去（页面上没有入口） */
export async function flushNow(): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  await flush();
}
