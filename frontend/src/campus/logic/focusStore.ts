/**
 * 校园工具集 · 番茄钟数据读写
 *
 * 只负责「从 localStorage 拿东西、清洗、放回去」，不放任何业务规则 ——
 * 规则都在 logic/ 的纯函数里，这里多一点判断，那边就少一个可测的用例。
 *
 * 清洗是必需的：localStorage 用户可以手动改、旧版本可能留下结构不同的数据，
 * 一条脏数据不能让整个番茄钟白屏。
 */

import { CAMPUS_KEYS } from "../storage/keys";
import { readJson, writeJson } from "../storage";
import type { FocusSession, FocusTask } from "../types";
import { trimSessions } from "./focus";

/** 把任意来源的数据洗成任务数组，坏行直接丢 */
export function sanitizeTasks(raw: unknown): FocusTask[] {
  if (!Array.isArray(raw)) return [];

  const seen = new Set<string>();
  const result: FocusTask[] = [];

  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;

    const id = typeof row.id === "string" ? row.id.trim() : "";
    const name = typeof row.name === "string" ? row.name.trim() : "";
    // 没有 id 或名字的任务无法展示也无法选中，直接丢弃
    if (id === "" || name === "" || seen.has(id)) continue;

    const createdAt = Number.isFinite(row.createdAt) ? Number(row.createdAt) : Date.now();
    seen.add(id);

    result.push({
      id,
      name,
      category: typeof row.category === "string" ? row.category : "未分类",
      color: typeof row.color === "string" && row.color.startsWith("#") ? row.color : "#6b8afd",
      archived: row.archived === true,
      createdAt,
      lastUsedAt: Number.isFinite(row.lastUsedAt) ? Number(row.lastUsedAt) : createdAt,
      // 老数据没有这个字段就留 undefined，由 taskUpdatedAt() 退回 lastUsedAt
      updatedAt: Number.isFinite(row.updatedAt) ? Number(row.updatedAt) : undefined,
    });
  }

  return result;
}

/** 把任意来源的数据洗成会话数组 */
export function sanitizeSessions(raw: unknown): FocusSession[] {
  if (!Array.isArray(raw)) return [];

  const seen = new Set<string>();
  const result: FocusSession[] = [];

  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;

    const id = typeof row.id === "string" ? row.id.trim() : "";
    const startedAt = Number(row.startedAt);
    const endedAt = Number(row.endedAt);
    // 时间戳是统计的唯一依据，缺了就没法归到任何一天
    if (id === "" || seen.has(id) || !Number.isFinite(startedAt) || !Number.isFinite(endedAt)) {
      continue;
    }

    seen.add(id);
    result.push({
      id,
      taskId: typeof row.taskId === "string" ? row.taskId : "",
      taskName: typeof row.taskName === "string" ? row.taskName : "未命名任务",
      category: typeof row.category === "string" && row.category !== "" ? row.category : "未分类",
      startedAt,
      endedAt,
      seconds: Number.isFinite(row.seconds) ? Math.max(0, Number(row.seconds)) : 0,
      plannedSeconds: Number.isFinite(row.plannedSeconds) ? Math.max(0, Number(row.plannedSeconds)) : 0,
      direction: row.direction === "countup" ? "countup" : "countdown",
      mode:
        row.mode === "immersive" || row.mode === "strict" ? row.mode : "normal",
      pausedCount: Number.isFinite(row.pausedCount) ? Math.max(0, Number(row.pausedCount)) : 0,
      awayCount: Number.isFinite(row.awayCount) ? Math.max(0, Number(row.awayCount)) : 0,
      awaySeconds: Number.isFinite(row.awaySeconds) ? Math.max(0, Number(row.awaySeconds)) : 0,
      endReason:
        row.endReason === "early" ||
        row.endReason === "abandoned" ||
        row.endReason === "stopped"
          ? row.endReason
          : "finished",
      syncedAt: Number.isFinite(row.syncedAt) ? Number(row.syncedAt) : undefined,
    });
  }

  return result;
}

export function loadTasks(): FocusTask[] {
  return sanitizeTasks(readJson<unknown>(CAMPUS_KEYS.focusTasks));
}

export function saveTasks(tasks: readonly FocusTask[]): void {
  writeJson(CAMPUS_KEYS.focusTasks, tasks);
}

export function loadSessions(): FocusSession[] {
  // 超出上限的历史在这里就裁掉，避免旧数据一直拖着存储空间
  return trimSessions(sanitizeSessions(readJson<unknown>(CAMPUS_KEYS.focusSessions)));
}

export function saveSessions(sessions: readonly FocusSession[]): void {
  writeJson(CAMPUS_KEYS.focusSessions, trimSessions(sessions));
}
