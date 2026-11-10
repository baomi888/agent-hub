/**
 * 校园工具集 · 倒计时 ↔ 待办（Phase 3，实施单 §7.2）
 *
 * 这一层只做「两种表示之间的换算」，不碰网络、不碰 localStorage ——
 * 因为它是唯一需要被单独验证的部分：日期算错一天，用户看到的是
 * 「11 月 24 日交论文」变成「11 月 23 日」，而界面上一切正常。
 *
 * ## 为什么倒计时要接进 tasks（而不是继续用自己那份 localStorage）
 *
 * 用户的原话是「倒计时不是独立对象，终态是 `task.due_at` 的视图」。
 * 接进去之前，同一个 DDL 在三个地方各存一份：工具集 localStorage、
 * 云端快照 `campus.deadline.items.v1`、提醒中心的 tasks ——
 * 在工具集里改个日期，提醒中心不知道；在提醒中心标成完成，工具集还在那儿倒数。
 *
 * ## 三种精度要分清（这是本文件唯一容易写错的地方）
 *
 *  · 工具集里选的是**日期**（`<input type="date">`），落到本地零点的毫秒时间戳；
 *  · 后端 `tasks.due_at` 存的是**Unix 秒**，语义是「这一天的 23:59:59」；
 *  · 倒计时的「还剩几天」必须把两边都收敛到**本地零点**再相减，
 *    否则 `Math.ceil((due - now) / 86400000)` 会在 23:59 之后把「就是今天」
 *    算成「剩 0 天」而状态判成逾期。
 *
 * 所以：写入走 `dueAtFromDate`（日期 → 当天 23:59:59 的秒），
 * 读出走 `dateFromDueAt`（秒 → 当天本地零点毫秒），中间值不落地、不外传。
 */

import type { Task } from "@/lib/types";
import { startOfDay } from "./deadline";
import type { DeadlineItem } from "../types";

/** 倒计时列表的一行。比 `DeadlineItem` 多一个 id —— 云端模式要靠它增删改 */
export interface DeadlineRow {
  id: string;
  name: string;
  /** 本地零点的毫秒时间戳（与 `DeadlineItem.date` 同口径，所以显示逻辑不用改） */
  date: number;
}

/**
 * 日期（本地零点毫秒）→ `due_at`（当天 23:59:59 的 Unix 秒）。
 *
 * 为什么不是 00:00：`due_at` 一旦落在零点，任何「按天展示」的地方都会把它
 * 读成「昨天」—— 东八区的 23:59:59 折成 UTC 还是当天，零点折成 UTC 就退回前一天了。
 * 用当天最后一秒，语义也正确：23:59:59 之前交都算没迟。
 */
export function dueAtFromDate(dateMs: number): number {
  const d = new Date(dateMs);
  const end = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 0);
  return Math.floor(end.getTime() / 1000);
}

/**
 * `due_at`（Unix 秒）→ 本地零点毫秒。
 *
 * 不走 `new Date(sec * 1000).setHours(0,0,0,0)` 那种写法：
 * `startOfDay` 已经在 `logic/deadline.ts` 里被「还剩几天」用着了，
 * 两边必须同一口径，否则列表排序和天数计算会各错一天。
 */
export function dateFromDueAt(sec: number): number {
  return startOfDay(sec * 1000);
}

/**
 * 待办列表 → 倒计时的行。
 *
 * 只留**未完成且排了截止日**的：倒计时回答的是「还有什么没交」，
 * 已完成的不该继续倒数，没有截止日的本来就不该出现在这里
 * （它在提醒中心的「今天」里，那边才是它的位置）。
 */
export function toDeadlineRows(tasks: Task[]): DeadlineRow[] {
  const rows: DeadlineRow[] = [];
  for (const t of tasks) {
    if (t.status !== "open") continue;
    if (t.due_at === null || t.due_at === undefined) continue;
    rows.push({ id: t.id, name: t.title, date: dateFromDueAt(t.due_at) });
  }
  // 与 `sortByDate` 同序：按日期升序，同日按名称，保证两次渲染顺序一致
  rows.sort((a, b) => (a.date === b.date ? a.name.localeCompare(b.name) : a.date - b.date));
  return rows;
}

/** 迁移结果。`left` 是**没迁成**的那些，调用方要留着，不能丢。 */
export interface MigrateResult {
  moved: number;
  left: DeadlineItem[];
  /** 第一条失败的原因，用来给用户一句人话；全成功时为 null */
  error: string | null;
}

/**
 * 把本机那份倒计时逐条写进 tasks。
 *
 * ★ 逐条而不是一次性批量：中途失败（断网 / 限额 / 一条标题过长）
 *   时，已经成功的那些**不能重来**，否则用户会看到两份一模一样的 DDL。
 *   所以约定是「成功一条就从待迁列表里去掉一条」，返回的 `left`
 *   就是下次进入时该继续迁的剩余部分 —— 天然幂等，不需要在 tasks 表上
 *   加 `origin` 列或唯一索引（那会把"用户真的想要两条同名 DDL"也堵掉）。
 *
 * ★ 顺序固定按日期升序：让失败发生在同一头，重试时前半段不会反复重算。
 */
export async function migrateLocalDeadlines(
  items: DeadlineItem[],
  create: (item: DeadlineItem) => Promise<unknown>,
): Promise<MigrateResult> {
  const queue = [...items].sort((a, b) => a.date - b.date);
  const left: DeadlineItem[] = [];
  let moved = 0;
  let error: string | null = null;

  for (const item of queue) {
    // 已经失败过：剩下的原样留给下次，不再继续撞同一类错误
    if (error !== null) {
      left.push(item);
      continue;
    }
    try {
      await create(item);
      moved += 1;
    } catch (e) {
      error = e instanceof Error ? e.message : "同步失败";
      left.push(item);
    }
  }

  return { moved, left, error };
}
