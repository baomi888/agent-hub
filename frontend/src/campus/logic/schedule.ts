/**
 * 校园工具集 · 课表周次计算（纯函数）
 *
 * 这里集中放课表最容易算错的三件事：星期归一、教学周、周次表达式解析。
 * 旧版把这三件事混在 DOM 操作里，出错时只能靠肉眼比对，所以先抽出来。
 */

/** 周一为 0，周日为 6 —— 新结构统一口径 */
export const DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"] as const;

/**
 * 旧版 v1 的 day 是 `dayOfWeek % 7`（周一 = 1，周日 = 0），
 * 新结构是 `(dayOfWeek - 1) % 7`（周一 = 0，周日 = 6）。
 *
 * ⚠️ 迁移旧数据时必须调一次，且**只能调一次**。重复调用会整体错位。
 */
export function normalizeLegacyDay(legacyDay: number): number {
  const day = Math.trunc(legacyDay);
  if (!Number.isFinite(day)) return 0;
  return (((day - 1) % 7) + 7) % 7;
}

const MS_PER_DAY = 86_400_000;

/** 去掉时分秒，只留自然日，避免跨零点计算漂移 */
function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * 由学期起始日算当前教学周（第 1 周起）。
 * 起始日之前返回 0，表示「学期还没开始」。
 */
export function currentWeek(termStart: Date, today: Date = new Date()): number {
  const diffDays = Math.floor((startOfDay(today) - startOfDay(termStart)) / MS_PER_DAY);
  if (diffDays < 0) return 0;
  return Math.floor(diffDays / 7) + 1;
}

/**
 * 解析周次表达式，如 `"1-8,10,12-14"` → `[1..8, 10, 12..14]`。
 * 非法片段直接跳过，不让一条脏输入毁掉整张课表。
 */
export function parseWeeks(text: string): number[] {
  const weeks = new Set<number>();

  for (const piece of text.split(/[,，、\s]+/)) {
    const token = piece.trim();
    if (token === "") continue;

    const range = token.match(/^(\d+)\s*[-~]\s*(\d+)$/);
    if (range) {
      const from = Number.parseInt(range[1], 10);
      const to = Number.parseInt(range[2], 10);
      if (!Number.isFinite(from) || !Number.isFinite(to)) continue;
      for (let w = Math.min(from, to); w <= Math.max(from, to); w += 1) {
        if (w > 0 && w <= 30) weeks.add(w);
      }
      continue;
    }

    const single = Number.parseInt(token, 10);
    if (Number.isFinite(single) && single > 0 && single <= 30) weeks.add(single);
  }

  return [...weeks].sort((a, b) => a - b);
}

/** 某门课在指定周是否上课 */
export function isActiveInWeek(weeks: readonly number[], week: number): boolean {
  return weeks.includes(week);
}

/** 节次范围 → 显示文案，如 [1,2] → "1-2 节" */
export function formatSections(sections: readonly number[]): string {
  if (sections.length === 0) return "未定节次";
  const sorted = sections.slice().sort((a, b) => a - b);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  return first === last ? `第 ${first} 节` : `${first}-${last} 节`;
}
