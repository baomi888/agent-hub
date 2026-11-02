/**
 * 校园工具集 · 长期任务库（纯函数）
 *
 * 需求 R5「长期存在的待办」：任务建一次，之后每天点开始就计时，不用反复填名字。
 * 所以这里的每个操作都围绕「任务要活得久」设计：
 *  - 去掉任务用**归档**而不是删除，否则历史统计会失去归属；
 *  - 同名任务不重复建，重新添加已归档的名字等于恢复它（用户想的是"我要接着做这个"，不是"再建一个"）。
 */

import type { FocusTask } from "../types";
import { resolveCategory } from "./focusCategory";

/** 任务名长度上限。太长的名字会把列表撑破，也会把饼图图例挤爆 */
export const MAX_TASK_NAME = 40;

/**
 * 任务色板。
 *
 * 与课表色板分开：课表的颜色属于「某门课」，任务的颜色属于「某个长期待办」，
 * 两者语义不同，将来任一边调整不该牵连另一边。
 */
export const FOCUS_COLORS = [
  "#6b8afd",
  "#f2994a",
  "#27ae60",
  "#9b51e0",
  "#eb5757",
  "#2d9cdb",
  "#c47f17",
  "#0f9b8e",
] as const;

/** 生成任务 id。带时间戳前缀便于调试时一眼看出创建先后 */
export function makeTaskId(now: number = Date.now(), salt = 0): string {
  return `t_${now.toString(36)}_${Math.abs(Math.floor(salt)).toString(36)}`;
}

/** 简单字符串散列，保证同名任务永远同一个颜色、不同任务尽量分散 */
function hashText(text: string): number {
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  }
  return hash;
}

/**
 * 任务的「最后修改时间」，用于云同步的后写胜比较。
 *
 * 老数据没有 `updatedAt`（0.5.0 之前），退回 `lastUsedAt` ——
 * 直接当 0 处理会让所有老任务在第一次同步时永远输给远端，等于一次静默的数据回退。
 */
export function taskUpdatedAt(task: FocusTask): number {
  if (Number.isFinite(task.updatedAt)) return Number(task.updatedAt);
  if (Number.isFinite(task.lastUsedAt)) return task.lastUsedAt;
  return Number.isFinite(task.createdAt) ? task.createdAt : 0;
}

/** 按名字取一个稳定的默认颜色 */
export function colorOf(name: string): string {
  const index = hashText(name) % FOCUS_COLORS.length;
  return FOCUS_COLORS[index];
}

/**
 * 建一个任务。
 *
 * @returns 名字为空白时返回 null —— 调用方据此提示，而不是悄悄建一个空任务
 */
export function makeTask(
  input: { name: string; category?: string | null; color?: string },
  id: string,
  now: number = Date.now(),
): FocusTask | null {
  const name = (input.name ?? "").trim().slice(0, MAX_TASK_NAME);
  if (name === "") return null;

  return {
    id,
    name,
    category: resolveCategory(name, input.category),
    color: input.color ?? colorOf(name),
    archived: false,
    createdAt: now,
    lastUsedAt: now,
    updatedAt: now,
  };
}

/** 同名判定：忽略大小写与首尾空白，避免「高数」和「高数 」变成两个任务 */
function sameName(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/**
 * 添加任务。
 *
 * 三种情况：
 *  1. 没同名 → 真添加，`created: true`；
 *  2. 有同名且未归档 → 不重复建，直接返回原来那个；
 *  3. 有同名但已归档 → 恢复它（取消归档并刷新使用时间），历史统计仍归在这个任务名下。
 */
export function addTask(
  tasks: readonly FocusTask[],
  task: FocusTask,
  now: number = Date.now(),
): { tasks: FocusTask[]; task: FocusTask; created: boolean } {
  const existing = tasks.find((item) => sameName(item.name, task.name));

  if (existing) {
    const restored: FocusTask = { ...existing, archived: false, lastUsedAt: now, updatedAt: now };
    return {
      tasks: tasks.map((item) => (item.id === existing.id ? restored : item)),
      task: restored,
      created: false,
    };
  }

  return { tasks: [...tasks, task], task, created: true };
}

/** 归档：从可选列表里消失，但历史记录里仍叫得出名字 */
export function archiveTask(
  tasks: readonly FocusTask[],
  id: string,
  now: number = Date.now(),
): FocusTask[] {
  return tasks.map((item) => (item.id === id ? { ...item, archived: true, updatedAt: now } : item));
}

/** 恢复归档任务 */
export function restoreTask(
  tasks: readonly FocusTask[],
  id: string,
  now: number = Date.now(),
): FocusTask[] {
  return tasks.map((item) => (item.id === id ? { ...item, archived: false, updatedAt: now } : item));
}

/** 记录一次使用，用于「最近在用」排序 */
export function touchTask(
  tasks: readonly FocusTask[],
  id: string,
  now: number = Date.now(),
): FocusTask[] {
  return tasks.map((item) =>
    item.id === id ? { ...item, lastUsedAt: now, updatedAt: now } : item,
  );
}

/** 改大类（手动覆盖自动归类），精确任务优先于关键词猜测 */
export function setTaskCategory(
  tasks: readonly FocusTask[],
  id: string,
  category: string,
  now: number = Date.now(),
): FocusTask[] {
  return tasks.map((item) =>
    item.id === id ? { ...item, category, updatedAt: now } : item,
  );
}

/** 改名失败的原因：空名 / 与其他任务重名 / 任务不存在 */
export type RenameReason = "empty" | "duplicate" | "missing";

/**
 * 改名结果：失败时带原因，调用方据此给出具体提示，而不是一句"改不了"。
 *
 * 成功分支也显式声明 `reason?: undefined`：这样 `outcome.reason` 在两边都存在，
 * 调用方不必先靠 `ok` 窄化才能读到它 —— 不同 TS 版本对 `!ok` 的窄化行为不一致，
 * 让它依赖窄化会在换编译器时突然报错。
 */
export type RenameOutcome =
  | { ok: true; tasks: FocusTask[]; task: FocusTask; changed: boolean; reason?: undefined }
  | { ok: false; reason: RenameReason };

/**
 * 给任务改名。
 *
 * 四条规则：
 *  1. 名字去掉首尾空白后为空 → 拒绝，不把任务变成一行看不见的字；
 *  2. 与原名相同（忽略大小写与空白）→ 视为没改，返回 `changed: false`，不写一次没必要的存储；
 *  3. 撞上别的任务（含已归档）→ 拒绝。同名并存会让「按名字归类」和同名去重失效，
 *     用户也无法在列表里分辨哪个是哪个；
 *  4. 大类若是当初按名字猜出来的，跟着新名字重猜；手动设过的不动 ——
 *     否则把「高数第三章」改成「英语阅读」后，它还挂在数学下面。
 *
 * 颜色**不跟着改**：颜色是用户认任务的第一眼标记，改名后换色反而像换了个任务。
 */
export function renameTask(
  tasks: readonly FocusTask[],
  id: string,
  rawName: string,
  now: number = Date.now(),
): RenameOutcome {
  const target = tasks.find((item) => item.id === id);
  if (!target) return { ok: false, reason: "missing" };

  const name = (rawName ?? "").trim().slice(0, MAX_TASK_NAME);
  if (name === "") return { ok: false, reason: "empty" };

  if (sameName(name, target.name)) {
    return { ok: true, tasks: tasks.slice(), task: target, changed: false };
  }

  if (tasks.some((item) => item.id !== id && sameName(item.name, name))) {
    return { ok: false, reason: "duplicate" };
  }

  // 当前大类等于「按旧名字猜出来的结果」，说明它是自动归类，可以跟着新名字变
  const autoCategory = target.category === resolveCategory(target.name, null);
  const renamed: FocusTask = {
    ...target,
    name,
    category: autoCategory ? resolveCategory(name, null) : target.category,
    updatedAt: now,
  };

  return {
    ok: true,
    tasks: tasks.map((item) => (item.id === id ? renamed : item)),
    task: renamed,
    changed: true,
  };
}

/** 当前可选任务：未归档，按最近使用降序；没被记过使用时间的按创建时间算 */
export function activeTasks(tasks: readonly FocusTask[]): FocusTask[] {
  return tasks
    .filter((item) => !item.archived)
    .slice()
    .sort((a, b) => {
      const diff = (b.lastUsedAt || b.createdAt) - (a.lastUsedAt || a.createdAt);
      if (diff !== 0) return diff;
      return a.name.localeCompare(b.name);
    });
}

/** 已归档任务，按名字排，供「恢复」入口使用 */
export function archivedTasks(tasks: readonly FocusTask[]): FocusTask[] {
  return tasks
    .filter((item) => item.archived)
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function findTask(
  tasks: readonly FocusTask[],
  id: string | null | undefined,
): FocusTask | null {
  if (!id) return null;
  return tasks.find((item) => item.id === id) ?? null;
}

/**
 * 合并本地与云端的任务（B6 云同步）。
 *
 * 同 id 时比较 `updatedAt`，**大的赢**；相等保留本地 ——
 * 本地是用户此刻在屏幕上看到的那一份，没有理由在时间相同时把他看到的东西换掉。
 * 本地没有的直接补进来，换设备靠这一步拿到旧任务。
 *
 * 会话那边不需要类似的函数：会话是追加型，`mergeSessions` 只做并集即可。
 */
export function mergeTasks(
  local: readonly FocusTask[],
  remote: readonly FocusTask[],
): FocusTask[] {
  const byId = new Map<string, FocusTask>();
  for (const task of local) byId.set(task.id, task);

  for (const task of remote) {
    const hit = byId.get(task.id);
    if (!hit) {
      byId.set(task.id, task);
      continue;
    }
    if (taskUpdatedAt(task) > taskUpdatedAt(hit)) byId.set(task.id, task);
  }

  return [...byId.values()];
}
