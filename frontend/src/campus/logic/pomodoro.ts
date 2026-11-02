/**
 * 校园工具集 · 番茄钟（纯函数）
 *
 * 旧版用 `setInterval(() => remain--, 1000)` 计时，看着没问题，实际有两个坑：
 *
 *  1. **后台节流**：切到别的标签页，浏览器会把定时器压到 ≥1 次/秒甚至更低，
 *     跑完一段回来剩余时间就对不上，用户以为番茄钟坏了（ROADMAP 里的验收项）。
 *     解法是不累加「过了几秒」，而是记「什么时候结束」，每次刷新都算差值。
 *  2. **浮点累积**：连续减 1 在长时间下不会有问题，但配合节流就会漏拍。
 *
 * 所以这里所有函数都围绕「绝对时间点」设计，不保存已经走过多少秒。
 */

/** 允许的时长区间（分钟）：1 分钟到 12 小时 */
export const MIN_MINUTES = 1;
export const MAX_MINUTES = 720;

/**
 * 小时数上限。
 * 由 MAX_MINUTES 推出来而不是另写一个数：两个常量各写一遍迟早对不上，
 * 改上限时只需要动 MAX_MINUTES。
 */
export const MAX_HOURS = Math.floor(MAX_MINUTES / 60);

/**
 * 秒 → 表盘字符串。
 *
 * 满 1 小时才出现小时位（`1:30:00`），不到一小时仍是 `25:00` ——
 * 每个番茄都写成 `00:25:00` 会多出两个没意义的 0，扫一眼反而更慢。
 */
export function formatClock(totalSeconds: number): string {
  // 负数一律显示 00:00，不显示 -00:05 这种吓人的东西
  const safe = Number.isFinite(totalSeconds) ? Math.max(0, Math.floor(totalSeconds)) : 0;
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours === 0 ? `${mm}:${ss}` : `${hours}:${mm}:${ss}`;
}

/** 把用户输入的分钟数收敛到合法区间；非法输入回落到默认 25 */
export function clampMinutes(value: number, fallback = 25): number {
  if (!Number.isFinite(value)) return fallback;
  const rounded = Math.round(value);
  return Math.min(MAX_MINUTES, Math.max(MIN_MINUTES, rounded));
}

/**
 * 总分钟 → 「几小时几分」，供「时 + 分」两个输入框显示。
 * 负数与非数字都当 0，避免输入框出现 NaN。
 */
export function splitDuration(totalMinutes: number): { hours: number; minutes: number } {
  const safe = Number.isFinite(totalMinutes) ? Math.max(0, Math.round(totalMinutes)) : 0;
  return { hours: Math.floor(safe / 60), minutes: safe % 60 };
}

/**
 * 「几小时几分」→ 总分钟。
 *
 * 两段各自夹取（小时 0–MAX_HOURS、分钟 0–59）再整体夹到 [MIN, MAX]。
 * 分那栏写 90 会被当成 59，而不是悄悄进位成 1 小时 30 分 ——
 * 输入途中数字自己跳走比"多按一次"更让人困惑。
 */
export function joinDuration(hours: number, minutes: number): number {
  const h = Number.isFinite(hours) ? Math.min(MAX_HOURS, Math.max(0, Math.trunc(hours))) : 0;
  const m = Number.isFinite(minutes) ? Math.min(59, Math.max(0, Math.trunc(minutes))) : 0;
  return Math.min(MAX_MINUTES, Math.max(MIN_MINUTES, h * 60 + m));
}

/** 时长的人类可读写法，用于快捷按钮：「25 分」「1 小时」「1 小时 30 分」 */
export function formatDurationLabel(totalMinutes: number): string {
  const { hours, minutes } = splitDuration(totalMinutes);
  if (hours === 0) return `${minutes} 分`;
  if (minutes === 0) return `${hours} 小时`;
  return `${hours} 小时 ${minutes} 分`;
}

/**
 * 还剩多少秒。
 *
 * 用 `ceil` 而不是 `floor`：刚开始的一瞬间应该是 25:00 而不是 24:59，
 * 到点那一刻返回 0，不会卡在 00:01 跳不过去。
 */
export function remainSeconds(endAt: number, now: number): number {
  if (!Number.isFinite(endAt) || !Number.isFinite(now)) return 0;
  return Math.max(0, Math.ceil((endAt - now) / 1000));
}

/**
 * 剩余秒数对应的进度（0–1），用于画进度条。
 * 传 totalSeconds = 0 时返回 0，避免除零得到 NaN。
 */
export function progressOf(remain: number, totalSeconds: number): number {
  if (!Number.isFinite(remain) || !Number.isFinite(totalSeconds) || totalSeconds <= 0) return 0;
  const done = totalSeconds - Math.max(0, remain);
  return Math.min(1, Math.max(0, done / totalSeconds));
}

/** 本地日期键 `YYYY-MM-DD`，「今日完成」按自然日归零要用到 */
export function todayKey(date: Date = new Date()): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}
