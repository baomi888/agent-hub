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

/** 允许的时长区间（分钟），与旧版输入框的 min/max 一致 */
export const MIN_MINUTES = 1;
export const MAX_MINUTES = 120;

/** 秒 → `mm:ss`。超过 99 分钟也不会截断（100 分钟显示 100:00） */
export function formatClock(totalSeconds: number): string {
  // 负数一律显示 00:00，不显示 -00:05 这种吓人的东西
  const safe = Number.isFinite(totalSeconds) ? Math.max(0, Math.floor(totalSeconds)) : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** 把用户输入的分钟数收敛到合法区间；非法输入回落到默认 25 */
export function clampMinutes(value: number, fallback = 25): number {
  if (!Number.isFinite(value)) return fallback;
  const rounded = Math.round(value);
  return Math.min(MAX_MINUTES, Math.max(MIN_MINUTES, rounded));
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
