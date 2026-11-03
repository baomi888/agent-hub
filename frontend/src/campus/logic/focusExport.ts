/**
 * 校园工具集 · 专注记录的导出 / 导入（B4）
 *
 * 存在的理由很实在：B6 云同步还没上线，而 `data/` 与 localStorage 都可能一夜之间没了
 * （换电脑、清缓存、隐私模式退出）。在同步做出来之前，一个 JSON 文件就是用户唯一的退路。
 *
 * 三条约定：
 *  1. **导入只补不改**：同 id 的记录保留本地版本。导入是「找回」，不是「覆盖」——
 *     用一份旧备份盖掉现在的最新数据，是这类功能最容易造成的真实事故。
 *  2. **导入必须能被拒绝**：不是本工具的文件、坏 JSON、空数据，都要给出具体原因，
 *     而不是"导入失败"四个字。
 *  3. **清洗复用读取时的同一套函数**（`sanitizeTasks` / `sanitizeSessions`），
 *     外部文件与本地存储因此享受同样的容错，不会因为多一条导入路径就多一套规则。
 */

import type { FocusSession, FocusTask } from "../types";
import { trimSessions } from "./focus";
import { sanitizeSessions, sanitizeTasks } from "./focusStore";

/** 文件类型标记，用来拒绝「随便一个 JSON」 */
export const EXPORT_KIND = "campus.focus";
export const EXPORT_VERSION = 1;

export interface FocusExport {
  kind: string;
  version: number;
  exportedAt: number;
  tasks: FocusTask[];
  sessions: FocusSession[];
}

/** 组装导出内容。会话带上 `syncedAt` —— 备份要能原样搬回去，不能丢同步状态 */
export function buildExport(
  tasks: readonly FocusTask[],
  sessions: readonly FocusSession[],
  now: number = Date.now(),
): FocusExport {
  return {
    kind: EXPORT_KIND,
    version: EXPORT_VERSION,
    exportedAt: now,
    tasks: tasks.map((t) => ({ ...t })),
    sessions: sessions.map((s) => ({ ...s })),
  };
}

/** 文件名：`campus-focus-2026-10-31.json`。用 ASCII，避免不同系统解压/传输时乱码 */
export function exportFileName(now: number = Date.now()): string {
  const d = new Date(now);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `campus-focus-${y}-${m}-${day}.json`;
}

export type ImportReason = "empty" | "not-json" | "wrong-kind" | "no-data";

export type ImportOutcome =
  | { ok: true; tasks: FocusTask[]; sessions: FocusSession[]; message: string; reason?: undefined }
  | { ok: false; tasks: FocusTask[]; sessions: FocusSession[]; message: string; reason: ImportReason };

/**
 * 解析一份备份。
 *
 * 失败也返回空数组而不是 undefined：调用方可以无条件解构，不必先判 `ok`。
 */
export function parseExport(raw: string): ImportOutcome {
  const text = raw.trim();
  if (text === "") {
    return { ok: false, reason: "empty", message: "文件是空的", tasks: [], sessions: [] };
  }

  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false, reason: "not-json", message: "这不是一个 JSON 文件", tasks: [], sessions: [] };
  }

  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { ok: false, reason: "not-json", message: "文件内容和这个工具备份的格式不一样", tasks: [], sessions: [] };
  }

  const row = data as Record<string, unknown>;
  if (row.kind !== EXPORT_KIND) {
    return {
      ok: false,
      reason: "wrong-kind",
      message: "这不是校园工具集导出的备份文件",
      tasks: [],
      sessions: [],
    };
  }

  const tasks = sanitizeTasks(row.tasks);
  const sessions = trimSessions(sanitizeSessions(row.sessions));

  if (tasks.length === 0 && sessions.length === 0) {
    return { ok: false, reason: "no-data", message: "备份里没有任何任务或记录", tasks, sessions };
  }

  return {
    ok: true,
    tasks,
    sessions,
    message: `读取到 ${tasks.length} 个任务、${sessions.length} 条记录`,
  };
}

export interface MergeResult {
  tasks: FocusTask[];
  sessions: FocusSession[];
  addedTasks: number;
  addedSessions: number;
}

/**
 * 把导入的数据并进本地：**同 id 保留本地**，只补本地没有的。
 *
 * 本地优先的理由：本地是已经被用户看见、继续在用的一份；
 * 备份可能是上周的，用旧的盖新的等于把他这一周的努力删掉。
 */
export function mergeImport(
  local: { tasks: readonly FocusTask[]; sessions: readonly FocusSession[] },
  incoming: { tasks: readonly FocusTask[]; sessions: readonly FocusSession[] },
): MergeResult {
  const taskIds = new Set(local.tasks.map((t) => t.id));
  const sessionIds = new Set(local.sessions.map((s) => s.id));

  const newTasks = incoming.tasks.filter((t) => !taskIds.has(t.id));
  const newSessions = incoming.sessions.filter((s) => !sessionIds.has(s.id));

  return {
    tasks: [...local.tasks, ...newTasks],
    sessions: trimSessions([...local.sessions, ...newSessions]),
    addedTasks: newTasks.length,
    addedSessions: newSessions.length,
  };
}
