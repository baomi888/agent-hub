/**
 * 校园工具集 · Deadline 倒计时（纯函数）
 *
 * 旧版把「还剩几天」写成 `Math.ceil((date - today) / 86400000)`，
 * 其中 today 是本地零点 —— 这个口径必须原样保留，否则用户会看到「明天考试」变成「还剩 0 天」。
 * 这里只是把这段算法规整成可测的形式，并集中处理日期字符串的时区陷阱。
 */

import type { DeadlineItem } from "../types";

const MS_PER_DAY = 86_400_000;

/** 剩余天数状态：已逾期 / 就是今天 / 七天内 / 正常 */
export type DeadlineStatus = "past" | "today" | "urgent" | "normal";

/** 把任意时间戳收敛到当天本地零点 */
export function startOfDay(time: number): number {
  const d = new Date(time);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * `YYYY-MM-DD` → 本地零点时间戳。
 *
 * 不能直接 `new Date("2026-11-01")` —— 那会按 UTC 解析，东八区会退回前一天，
 * 于是「11 月 1 日考试」显示成「10 月 31 日」。必须补 `T00:00:00` 走本地解析。
 */
export function parseDateInput(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = new Date(`${value}T00:00:00`).getTime();
  return Number.isFinite(time) ? time : null;
}

/** 时间戳 → `YYYY-MM-DD`，供 `<input type="date">` 使用（按本地日期，不用 toISOString） */
export function toDateInput(time: number): string {
  const d = new Date(time);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** 剩余天数：今天到期为 0，已过期为负 */
export function daysLeft(dateTime: number, now: number = Date.now()): number {
  return Math.ceil((startOfDay(dateTime) - startOfDay(now)) / MS_PER_DAY);
}

/** 状态判定，阈值与旧版一致（≤7 天标红） */
export function statusOf(days: number): DeadlineStatus {
  if (days < 0) return "past";
  if (days === 0) return "today";
  if (days <= 7) return "urgent";
  return "normal";
}

/** 状态文案，与旧版逐字一致，用户已经习惯了这套说法 */
export function statusText(days: number): string {
  if (days < 0) return `已逾期 ${-days} 天`;
  if (days === 0) return "就是今天";
  return `剩 ${days} 天`;
}

/** 按日期升序；同一天保持录入顺序（sort 在 ES2019 起稳定，可以依赖） */
export function sortByDate(items: readonly DeadlineItem[]): DeadlineItem[] {
  return items.slice().sort((a, b) => a.date - b.date);
}

/** 名称最长 30 字，超出截断 —— 与旧版的 maxlength 对齐 */
export function normalizeName(name: string): string {
  return name.trim().slice(0, 30);
}

/**
 * 旧数据迁移。
 *
 * 旧版存 `[{ name, date: <毫秒时间戳> }]`，结构和新版一致，所以这里主要是**校验**：
 * 名称非空、时间戳可解析、顺手截断超长名称。返回 null 表示这份数据整体不可用。
 */
export function migrateLegacyDeadline(raw: unknown): DeadlineItem[] | null {
  if (!Array.isArray(raw)) return null;

  const items: DeadlineItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as { name?: unknown; date?: unknown };

    const name = normalizeName(String(record.name ?? ""));
    const time = Number(record.date);
    if (name === "" || !Number.isFinite(time)) continue;

    items.push({ name, date: startOfDay(time) });
  }

  // 旧版允许「空数组」存在（用户把事项全删了），这时也应该照搬而不是塞示例
  return items;
}

/**
 * 首访示例：期末考试 30 天、课程论文 14 天、四六级 60 天。
 * 与旧版一致，让新手一进来就看到这个工具长什么样。
 */
export function sampleDeadlines(now: number = Date.now()): DeadlineItem[] {
  const base = startOfDay(now);
  return [
    { name: "期末考试", date: base + 30 * MS_PER_DAY },
    { name: "课程论文提交", date: base + 14 * MS_PER_DAY },
    { name: "四六级考试", date: base + 60 * MS_PER_DAY },
  ];
}
