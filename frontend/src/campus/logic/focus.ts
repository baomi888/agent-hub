/**
 * 校园工具集 · 番茄钟统计（纯函数）
 *
 * 所有「学习时长」口径都在这里定，界面层不自己算，避免同一个数字在
 * 首页、饼图、明细表出现三种答案。
 *
 * 三条口径，先说清楚：
 *
 *  1. **会话归属按开始时刻**。23:50 开始、00:20 结束的那一轮算前一天。
 *     理由是用户的心智是「我昨晚学了半小时」，不是「今天的 20 分钟 + 昨天的 10 分钟」。
 *  2. **时长为 0 的会话也保留**（记在条数里，不计时长）。这样「今天练了 3 次」
 *     不会因为每次都很短而变成 0 次。
 *  3. **区间一律左闭右开** `[from, to)`，终点取下一日的本地零点。
 *     用本地零点而不是 +86400000 毫秒，是为了避开时区与夏令时的边界。
 */

import type {
  CategoryDrilldown,
  CategoryStat,
  DayStat,
  FocusSession,
  PeriodSummary,
  StatsRange,
  TaskStat,
} from "../types";

/** 本地留存上限。一条约 160 字节，5000 条约 800 KB，localStorage 撑得住 */
export const MAX_SESSIONS = 5000;

/** 饼图最多显示几片，超出合并为「其他」 */
export const MAX_PIE_SLICES = 6;

// ==================== 时间工具 ====================

/** 本地日期键 `YYYY-MM-DD` */
export function dayKey(ts: number): string {
  const d = new Date(ts);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** 本地零点 */
export function startOfDay(ts: number): number {
  const d = new Date(ts);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** 前一天零点。不用减 86400000，避免夏令时那天差一小时 */
function prevDay(ts: number): number {
  const d = new Date(ts);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() - 1).getTime();
}

/** 本周一零点。中国习惯以周一为一周起点，与课表的 day 口径一致 */
export function startOfWeek(ts: number): number {
  const d = new Date(startOfDay(ts));
  const dow = d.getDay(); // 周日 = 0
  const back = dow === 0 ? 6 : dow - 1;
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() - back).getTime();
}

/**
 * 当前时间维度对应的区间。
 *
 * @param range 日 / 周 / 月 / 年
 * @param now   参考时刻（便于测试传固定值）
 */
export function rangeOf(range: StatsRange, now: number = Date.now()): { from: number; to: number } {
  const d = new Date(now);
  const y = d.getFullYear();
  const m = d.getMonth();

  switch (range) {
    case "day": {
      const from = startOfDay(now);
      return { from, to: new Date(y, m, d.getDate() + 1).getTime() };
    }
    case "week": {
      const from = startOfWeek(now);
      const start = new Date(from);
      return { from, to: new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7).getTime() };
    }
    case "month":
      return { from: new Date(y, m, 1).getTime(), to: new Date(y, m + 1, 1).getTime() };
    case "year":
    default:
      return { from: new Date(y, 0, 1).getTime(), to: new Date(y + 1, 0, 1).getTime() };
  }
}

// ==================== 聚合 ====================

/** 取出区间内的会话（左闭右开，按开始时刻归属） */
export function sessionsInRange(
  sessions: readonly FocusSession[],
  from: number,
  to: number,
): FocusSession[] {
  return sessions.filter((s) => s.startedAt >= from && s.startedAt < to);
}

/** 累计时长（秒） */
export function totalSeconds(sessions: readonly FocusSession[]): number {
  let sum = 0;
  for (const s of sessions) {
    if (Number.isFinite(s.seconds) && s.seconds > 0) sum += s.seconds;
  }
  return sum;
}

/**
 * 区间统计。
 *
 * @param sessions 全部会话（可以多于区间内，内部会筛）
 * @param from     起点（含）
 * @param to       终点（不含）
 */
export function summarize(
  sessions: readonly FocusSession[],
  from: number,
  to: number,
): PeriodSummary {
  const inRange = sessionsInRange(sessions, from, to);

  const catMap = new Map<string, { seconds: number; count: number }>();
  const taskMap = new Map<string, { taskName: string; category: string; seconds: number; count: number }>();
  const dayMap = new Map<string, { seconds: number; count: number }>();
  let total = 0;

  for (const s of inRange) {
    const sec = Number.isFinite(s.seconds) && s.seconds > 0 ? s.seconds : 0;
    total += sec;

    const category = s.category || "未分类";
    const cat = catMap.get(category) ?? { seconds: 0, count: 0 };
    cat.seconds += sec;
    cat.count += 1;
    catMap.set(category, cat);

    const task = taskMap.get(s.taskId) ?? {
      taskName: s.taskName,
      category,
      seconds: 0,
      count: 0,
    };
    task.seconds += sec;
    task.count += 1;
    taskMap.set(s.taskId, task);

    const key = dayKey(s.startedAt);
    const day = dayMap.get(key) ?? { seconds: 0, count: 0 };
    day.seconds += sec;
    day.count += 1;
    dayMap.set(key, day);
  }

  const ratio = (sec: number) => (total > 0 ? sec / total : 0);

  const categories: CategoryStat[] = [...catMap.entries()]
    .map(([category, v]) => ({ category, seconds: v.seconds, count: v.count, ratio: ratio(v.seconds) }))
    .sort((a, b) => b.seconds - a.seconds);

  const tasks: TaskStat[] = [...taskMap.entries()]
    .map(([taskId, v]) => ({
      taskId,
      taskName: v.taskName,
      category: v.category,
      seconds: v.seconds,
      count: v.count,
      ratio: ratio(v.seconds),
    }))
    .sort((a, b) => b.seconds - a.seconds);

  const byDay: DayStat[] = [...dayMap.entries()]
    .map(([date, v]) => ({ date, seconds: v.seconds, count: v.count }))
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));

  return { from, to, totalSeconds: total, sessionCount: inRange.length, categories, tasks, byDay };
}

/**
 * 大类下钻：点开饼图某一类后，看这一类内部各任务怎么分。
 *
 * 返回的 `ratio` 是**类内占比**（高数占数学的 62%），不是占全天。
 * 想看占全天的比例用 `ratioOfAll`。
 */
export function drillDown(summary: PeriodSummary, category: string): CategoryDrilldown {
  const cat: CategoryStat | undefined = summary.categories.find((c) => c.category === category);
  const rows = summary.tasks.filter((t) => t.category === category);
  const inner = rows.reduce((sum, t) => sum + t.seconds, 0);

  const tasks = rows.map((t) => ({
    taskId: t.taskId,
    taskName: t.taskName,
    seconds: t.seconds,
    count: t.count,
    ratio: inner > 0 ? t.seconds / inner : 0,
  }));

  return {
    category,
    totalSeconds: cat ? cat.seconds : inner,
    ratioOfAll: cat ? cat.ratio : 0,
    tasks,
  };
}

/**
 * 饼图分片收敛：超过 max 片时保留前 max-1 片，其余合并为「其他」。
 *
 * 合并后要重新排序 —— 剩余部分之和可能比第 max-1 片还大，
 * 直接追加到末尾会让「其他」莫名其妙地小。
 */
export function foldToTop(categories: readonly CategoryStat[], max: number = MAX_PIE_SLICES): CategoryStat[] {
  if (max <= 0) return [];
  if (categories.length <= max) return categories.slice();

  const head = categories.slice(0, max - 1);
  const rest = categories.slice(max - 1);
  const restSeconds = rest.reduce((sum, c) => sum + c.seconds, 0);
  const restCount = rest.reduce((sum, c) => sum + c.count, 0);
  const restRatio = rest.reduce((sum, c) => sum + c.ratio, 0);

  const merged: CategoryStat = {
    category: "其他",
    seconds: restSeconds,
    count: restCount,
    ratio: restRatio,
  };

  return [...head, merged].sort((a, b) => b.seconds - a.seconds);
}

/**
 * 连续学习天数。
 *
 * 今天还没开始学时从昨天往前数 —— 否则用户早上打开就看见「连续 0 天」，
 * 明明昨天刚学完，体验上像被清零了。
 */
export function streakDays(sessions: readonly FocusSession[], now: number = Date.now()): number {
  if (sessions.length === 0) return 0;

  const days = new Set(sessions.map((s) => dayKey(s.startedAt)));
  let cursor = startOfDay(now);

  if (!days.has(dayKey(cursor))) {
    cursor = prevDay(cursor);
    if (!days.has(dayKey(cursor))) return 0;
  }

  let count = 0;
  while (days.has(dayKey(cursor))) {
    count += 1;
    cursor = prevDay(cursor);
  }
  return count;
}

/**
 * 日均时长（秒）。
 *
 * 分母用「区间内已过的天数」，不是区间总天数：本月 3 号看月统计，
 * 除以 31 会让数字小得离谱，让人以为这几天白学了。
 * 未来的日期没有学习记录不是"效率低"，是"还没到"。
 */
export function averageSecondsPerDay(summary: PeriodSummary, now: number = Date.now()): number {
  const start = startOfDay(summary.from);
  const limit = Math.min(summary.to, now);
  // 用日历天数差而不是毫秒除法：夏令时那天是 23 或 25 小时，直接除会少一天
  const days = Math.max(1, Math.round((startOfDay(limit) - start) / 86_400_000) + 1);
  return summary.totalSeconds / days;
}

/** 区间标题：日「10 月 31 日」/ 周「10 月 27 日 – 11 月 2 日」/ 月「2026 年 10 月」/ 年「2026 年」 */
export function rangeLabel(range: StatsRange, now: number = Date.now()): string {
  const { from, to } = rangeOf(range, now);
  const start = new Date(from);
  const last = new Date(to - 1);

  switch (range) {
    case "day":
      return `${start.getMonth() + 1} 月 ${start.getDate()} 日`;
    case "week":
      return `${start.getMonth() + 1} 月 ${start.getDate()} 日 – ${last.getMonth() + 1} 月 ${last.getDate()} 日`;
    case "month":
      return `${start.getFullYear()} 年 ${start.getMonth() + 1} 月`;
    case "year":
    default:
      return `${start.getFullYear()} 年`;
  }
}

// ==================== 会话维护 ====================

/** 生成会话 id。时间戳 + 随机后缀，前端离线也能唯一，服务端按 id 幂等 */
export function makeSessionId(now: number = Date.now()): string {
  const rand = Math.random().toString(36).slice(2, 8);
  return `s_${now.toString(36)}_${rand}`;
}

/** 按开始时刻升序排列（存储顺序稳定，便于比对 diff） */
export function sortSessions(sessions: readonly FocusSession[]): FocusSession[] {
  return sessions.slice().sort((a, b) => a.startedAt - b.startedAt);
}

/** 裁到最近 max 条。只留最近的，旧的先走 —— 统计价值随时间迅速衰减 */
export function trimSessions(sessions: readonly FocusSession[], max: number = MAX_SESSIONS): FocusSession[] {
  const sorted = sortSessions(sessions);
  if (sorted.length <= max) return sorted;
  return sorted.slice(sorted.length - max);
}

/**
 * 合并本地与服务端会话（B6 云同步用）。
 *
 * 同一 id 时**以本地内容为准**：会话一旦生成就不该再变，本地才是写入方；
 * 远端只用来补齐本地没有的（换设备），以及把同步标记带回来。
 */
export function mergeSessions(
  local: readonly FocusSession[],
  remote: readonly FocusSession[],
): FocusSession[] {
  const byId = new Map<string, FocusSession>();
  for (const s of local) byId.set(s.id, s);

  for (const r of remote) {
    const hit = byId.get(r.id);
    if (!hit) {
      byId.set(r.id, r);
      continue;
    }
    if (hit.syncedAt === undefined && r.syncedAt !== undefined) {
      byId.set(r.id, { ...hit, syncedAt: r.syncedAt });
    }
  }

  return sortSessions([...byId.values()]);
}

/** 待同步的会话：没有 syncedAt 的都算 */
export function pendingSync(sessions: readonly FocusSession[]): FocusSession[] {
  return sessions.filter((s) => s.syncedAt === undefined);
}

// ==================== 展示格式化 ====================

/**
 * 秒 → 中文时长。
 * 刻意不显示秒：「1 小时 3 分 22 秒」在生活中没有意义，反而占地方。
 */
export function formatDuration(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  if (safe < 60) return safe === 0 ? "0 分钟" : "不到 1 分钟";

  const totalMin = Math.round(safe / 60);
  if (totalMin < 60) return `${totalMin} 分钟`;

  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return m === 0 ? `${h} 小时` : `${h} 小时 ${m} 分`;
}

/** 比例 → 百分比文案。整数不拖小数位，避免「50.0%」这种噪声 */
export function formatPercent(ratio: number): string {
  if (!Number.isFinite(ratio) || ratio <= 0) return "0%";
  const pct = Math.round(ratio * 1000) / 10;
  return Number.isInteger(pct) ? `${pct}%` : `${pct.toFixed(1)}%`;
}
