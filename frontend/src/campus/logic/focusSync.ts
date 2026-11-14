/**
 * 校园工具集 · 专注记录的云同步（B6）
 *
 * 这一层只做「搬运与合并」，不碰 React，也不直接读写 localStorage ——
 * 输入输出都是纯数据，因此可以在 Node 里用假的 fetch 跑完整流程。
 *
 * 三条设计决定：
 *
 *  1. **本地优先，云端补齐**。任何冲突都先看本地：本地是用户此刻正在用的一份。
 *     会话按 id 并集（追加型，不会冲突），任务按 updatedAt 后写胜。
 *
 *  2. **游客不阻塞**。没有登录就返回 `guest`，调用方照常计时、照常统计，
 *     只是不碰网络。第一次登录后，本机这些「没有 syncedAt」的记录会在下一次
 *     同步里整批上报 —— 这就是「游客数据并入账户」，不需要单独的迁移接口。
 *
 *  3. **失败不抛给用户**。返回 `error` 并带一句人话，调用方显示它、下次再试。
 *     断网、500、401 都不能影响正在跑的计时。
 */

import type { FocusSession, FocusTask } from "../types";
import { mergeSessions, pendingSync } from "./focus";
import { mergeTasks } from "./focusTask";

const BASE = "/api/campus/focus";
/** 单次上报条数。服务端上限 1000，这里取 200：一次请求体小，失败重试代价也小 */
const CHUNK = 200;

export type SyncStatus = "checking" | "guest" | "syncing" | "synced" | "error";

export interface SyncSnapshot {
  tasks: FocusTask[];
  sessions: FocusSession[];
}

export interface SyncOutcome {
  status: "guest" | "ok" | "error";
  /** 合并后的完整数据（`guest` / `error` 时原样返回输入） */
  snapshot: SyncSnapshot;
  /** 本次真正上报的会话条数 */
  pushedSessions: number;
  /** 本次真正写入的任务条数（服务端返回的 accepted） */
  pushedTasks: number;
  /** 本次从云端拉到多少条会话（含已存在的） */
  pulled: number;
  /** 给用户看的一句话 */
  message: string;
}

function chunk<T>(rows: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < rows.length; i += size) out.push(rows.slice(i, i + size));
  return out;
}

async function readJson<T>(res: Response): Promise<T | null> {
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** 当前登录的账号 id；未登录返回 null（401 是正常状态，不是错误） */
export async function whoAmI(): Promise<string | null> {
  try {
    const res = await fetch("/api/auth/me", { credentials: "include", cache: "no-store" });
    if (!res.ok) return null;
    const data = await readJson<{ user?: { id?: string } }>(res);
    const id = data?.user?.id;
    return typeof id === "string" && id !== "" ? id : null;
  } catch {
    return null;
  }
}

/** 批量上报会话，返回服务端接受的条数。分片发送，单片失败即抛出 */
export async function pushSessions(rows: readonly FocusSession[]): Promise<number> {
  let accepted = 0;
  for (const part of chunk(rows, CHUNK)) {
    const res = await fetch(`${BASE}/sessions`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessions: part }),
    });
    if (!res.ok) throw new Error(`上报记录失败（HTTP ${res.status}）`);
    const data = await readJson<{ accepted?: number }>(res);
    accepted += data?.accepted ?? 0;
  }
  return accepted;
}

/** 批量上报任务，服务端按 updatedAt 后写胜 */
export async function pushTasks(rows: readonly FocusTask[]): Promise<number> {
  let accepted = 0;
  for (const part of chunk(rows, CHUNK)) {
    const res = await fetch(`${BASE}/tasks`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tasks: part }),
    });
    if (!res.ok) throw new Error(`上报任务失败（HTTP ${res.status}）`);
    const data = await readJson<{ accepted?: number }>(res);
    accepted += data?.accepted ?? 0;
  }
  return accepted;
}

/** 拉取云端全部数据（会话最多 5000 条，与本地上限一致） */
export async function pullAll(): Promise<SyncSnapshot> {
  const [sessionsRes, tasksRes] = await Promise.all([
    fetch(`${BASE}/sessions`, { credentials: "include", cache: "no-store" }),
    fetch(`${BASE}/tasks`, { credentials: "include", cache: "no-store" }),
  ]);

  if (!sessionsRes.ok) throw new Error(`拉取记录失败（HTTP ${sessionsRes.status}）`);
  if (!tasksRes.ok) throw new Error(`拉取任务失败（HTTP ${tasksRes.status}）`);

  const sessionsData = await readJson<{ sessions?: FocusSession[] }>(sessionsRes);
  const tasksData = await readJson<{ tasks?: FocusTask[] }>(tasksRes);

  return {
    sessions: Array.isArray(sessionsData?.sessions) ? sessionsData.sessions : [],
    tasks: Array.isArray(tasksData?.tasks) ? tasksData.tasks : [],
  };
}

/**
 * 失败原因翻译成人话。
 *
 * `fetch` 在网络层失败时抛的是 `TypeError: Failed to fetch` —— 一句英文技术报错，
 * 原来被原样拼进「未同步」那一行给用户看。断网、被插件拦、DNS 挂都长这样，
 * 对用户是同一件事：连不上。
 *
 * HTTP 那类保留原文（例如「上报记录失败（HTTP 500）」）：里面带状态码，排查时有用。
 * ★ 返回的句子**不带结尾句号** —— 调用方（SyncBar）会在后面接一句后果说明。
 */
function syncErrorText(error: unknown): string {
  if (error instanceof TypeError) return "连不上服务器";
  return error instanceof Error ? error.message : "同步失败";
}

/**
 * 一次完整同步：上报待同步的 → 拉取云端的 → 合并。
 *
 * 顺序不能反：先推后拉，拉回来的那份才包含「刚刚推上去的记录」的 `syncedAt`，
 * 本地的待同步标记因此自动清掉，不必另写一套「标记已同步」的逻辑。
 */
export async function syncAll(local: SyncSnapshot): Promise<SyncOutcome> {
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  if (offline) {
    return {
      status: "error",
      snapshot: local,
      pushedSessions: 0,
      pushedTasks: 0,
      pulled: 0,
      message: "当前离线，记录先存在本机，联网后会自动补传",
    };
  }

  const uid = await whoAmI();
  if (!uid) {
    return {
      status: "guest",
      snapshot: local,
      pushedSessions: 0,
      pushedTasks: 0,
      pulled: 0,
      message: "数据只存在这台设备上，换台电脑或清了浏览器就没了。",
    };
  }

  try {
    const pending = pendingSync(local.sessions);
    const pushedSessions = pending.length > 0 ? await pushSessions(pending) : 0;
    const pushedTasks = local.tasks.length > 0 ? await pushTasks(local.tasks) : 0;

    const remote = await pullAll();

    return {
      status: "ok",
      snapshot: {
        sessions: mergeSessions(local.sessions, remote.sessions),
        tasks: mergeTasks(local.tasks, remote.tasks),
      },
      pushedSessions,
      pushedTasks,
      pulled: remote.sessions.length,
      message:
        pushedSessions > 0
          ? `已同步：上传 ${pushedSessions} 条记录，云端共 ${remote.sessions.length} 条。`
          : `已同步，云端共 ${remote.sessions.length} 条记录。`,
    };
  } catch (error) {
    return {
      status: "error",
      snapshot: local,
      pushedSessions: 0,
      pushedTasks: 0,
      pulled: 0,
      message: syncErrorText(error),
    };
  }
}
