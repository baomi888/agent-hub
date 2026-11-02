/**
 * 番茄钟「本轮完成」提醒的判定与文案（纯函数）。
 *
 * 为什么单独一层：
 *  1. 判定规则必须和「误触不记录」对齐，散在组件里只能靠肉眼试；
 *  2. 文案按一天中的时段变化，23 点、0 点这种边界最容易写错；
 *  3. 提出来之后可以用脚本把每个时段跑一遍，改文案不必碰组件。
 *
 * 这里不碰时间、不碰 DOM —— 时段由调用方把 `hour` 传进来，
 * 所以测试里可以精确构造"深夜 23:30"而不用改系统时钟。
 */

import type { FocusSession } from "../types";
import { humanizeSeconds } from "./timer";

/**
 * 值不值得打断用户。
 *
 * 只有「倒计时走到 00:00」才弹窗：那一刻是没人看着也会发生的事件，需要把人叫回来。
 * 手动「结束 / 重置」不弹 —— 那是用户自己的动作，页面内的提示已经够了，
 * 再弹一层只会让「结束」这个按钮变得不敢点。
 */
export function shouldCelebrate(session: FocusSession | null): boolean {
  if (!session) return false;
  return session.endReason === "finished" && session.seconds > 0;
}

export type DaySlot = "night" | "dawn" | "morning" | "noon" | "afternoon" | "evening";

/** 深夜 23–4、清晨 5–7、上午 8–11、中午 12–13、下午 14–17、晚上 18–22 */
export function daySlotOf(hour: number): DaySlot {
  // 非法输入按上午处理：这是最常见的专注时段，落到别的档都会显得奇怪
  const h = Number.isFinite(hour) ? Math.floor(hour) : 9;
  if (h >= 23 || h < 5) return "night";
  if (h < 8) return "dawn";
  if (h < 12) return "morning";
  if (h < 14) return "noon";
  if (h < 18) return "afternoon";
  return "evening";
}

const HEADLINES: Record<DaySlot, string> = {
  night: "深夜的一段，早点睡比多熬一小时更值。",
  dawn: "天刚亮就坐下，这一段很难得。",
  morning: "上午的一段，正是脑子最好使的时候。",
  noon: "午间的一段，别忘了吃饭。",
  afternoon: "下午的一段，节奏稳住了。",
  evening: "晚上的一段，今天又攒下一点。",
};

export function doneHeadline(hour: number): string {
  return HEADLINES[daySlotOf(hour)];
}

/** 「高等数学 · 数学」；没填大类就只显示任务名 */
export function doneTaskLabel(name: string, category: string): string {
  const trimmed = name.trim();
  const cat = category.trim();
  if (trimmed === "") return "未命名任务";
  return cat === "" ? trimmed : `${trimmed} · ${cat}`;
}

/**
 * 「今日 3 段 · 1 小时 15 分」。
 *
 * 调用方传的是**含本轮在内**的今日汇总 —— 弹窗刚出现时如果显示"今日 2 段"而用户刚做完第 3 段，
 * 会让人怀疑记录没写上。
 */
export function doneSummaryText(count: number, seconds: number): string {
  const safeCount = Number.isFinite(count) ? Math.max(0, Math.round(count)) : 0;
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  if (safeCount <= 0) return "今日还没有记录";
  return `今日 ${safeCount} 段 · ${humanizeSeconds(safeSeconds)}`;
}
