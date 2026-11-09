/**
 * 校园工具集 · 单词熟记档案（复习本）
 *
 * 这个文件只有纯函数：输入旧档案 + 事件，返回新档案。不碰 localStorage、不碰 React，
 * 这样「什么样的词该进复习本」这条规则可以被逐值验证一次，而不是散在组件里各判一遍。
 *
 * 收词规则由负责人拍定（2026-11-08），是**两个独立条件**，任一成立就进本，
 * 但轻重分两档，一个词只需要一个结论：
 *
 *   | 条件 | 含义 | 标记 |
 *   | --- | --- | --- |
 *   | 翻看 ≥ 5 次 | 「看这么多遍还没记住，八成不熟」 | 可能不熟悉（unsure） |
 *   | 点过「不认识」 | 用户自己承认记不住 | 确定不熟悉（sure） |
 *
 * 两条都中时按「确定不熟悉」算 —— 后者是用户的原话，比推断更硬。
 * 「翻看」必须**跨会话累计**，否则一次刷词里同一个词根本翻不到 5 次（翻一次就判了）。
 *
 * 复习完成后的去向同样由负责人定：认识的移到「已掌握」分区，可回看、但不再进复习轮。
 */

import type { ReviewEntry, ReviewMark, WordItem, WordsMemory } from "../types";

/** 翻看达到这个次数 → 可能不熟悉。写成常量是因为它同时出现在判定和文案里 */
export const REVIEW_FLIP_THRESHOLD = 5;

/** 两档标记的中文名，列表和提示共用一份，避免两处写得不一样 */
export const MARK_LABEL: Record<ReviewMark, string> = {
  unsure: "可能不熟悉",
  sure: "确定不熟悉",
};

/**
 * 计数表容量上限。
 *
 * watch 里是「翻过但还没达标」的词，正常用户不会很多；
 * 但词库有 4543 个词，极端情况下挨个翻一遍会把 localStorage 撑大。
 * 超了按最后一次翻看时间淘汰最旧的 —— 记不住的词会被反复翻，最旧的那批本来就排不上。
 */
const MAX_WATCH = 800;

/** 待复习条目上限。超过就丢掉最早入本的，避免清单无限长到没人愿意翻 */
const MAX_ITEMS = 500;

/** 已掌握上限，同理 */
const MAX_MASTERED = 800;

/** 一次判定对复习本做了什么，调用方用它决定提示文案 */
export type CollectResult = {
  mem: WordsMemory;
  /** newly = 刚进本；upgraded = 原本是「可能」，这次升级成「确定」；none = 没变化 */
  change: "newly" | "upgraded" | "none";
  mark?: ReviewMark;
};

export function emptyMemory(): WordsMemory {
  return { v: 1, watch: {}, items: [], mastered: [] };
}

export function countPending(mem: WordsMemory): number {
  return mem.items.length;
}

/** 复习本里各档有多少条，给面板做小结用 */
export function countByMark(mem: WordsMemory): Record<ReviewMark, number> {
  const out: Record<ReviewMark, number> = { unsure: 0, sure: 0 };
  for (const item of mem.items) out[item.mark] += 1;
  return out;
}

/**
 * 把读出来的东西洗成可用档案。
 *
 * localStorage 里的东西不可信：用户可能手改、可能是上个版本的结构、
 * 可能只写进去一半（配额满）。这里只保证「返回的对象一定有 watch / items / mastered」，
 * 坏条目直接丢，不抛异常 —— 与 storage 封装的三条铁律一致。
 */
export function normalizeMemory(raw: unknown): WordsMemory {
  const base = emptyMemory();
  if (!raw || typeof raw !== "object") return base;

  const source = raw as Partial<WordsMemory>;

  if (source.watch && typeof source.watch === "object") {
    for (const [key, value] of Object.entries(source.watch)) {
      if (typeof key !== "string" || key === "") continue;
      const row = value as { f?: unknown; u?: unknown; t?: unknown };
      const f = Number(row?.f);
      const u = Number(row?.u);
      base.watch[key] = {
        f: Number.isFinite(f) && f > 0 ? Math.floor(f) : 0,
        u: Number.isFinite(u) && u > 0 ? Math.floor(u) : 0,
        t: Number.isFinite(Number(row?.t)) ? Number(row?.t) : 0,
      };
    }
  }

  base.items = readEntries(source.items);
  base.mastered = readEntries(source.mastered);

  return base;
}

function readEntries(input: unknown): ReviewEntry[] {
  if (!Array.isArray(input)) return [];
  const out: ReviewEntry[] = [];
  for (const row of input) {
    const item = row as Partial<ReviewEntry>;
    if (typeof item?.w !== "string" || item.w === "") continue;
    if (typeof item?.def !== "string") continue;
    out.push({
      w: item.w,
      def: item.def,
      egEn: typeof item.egEn === "string" ? item.egEn : undefined,
      egZh: typeof item.egZh === "string" ? item.egZh : undefined,
      lvl: typeof item.lvl === "string" ? item.lvl : "",
      at: Number.isFinite(Number(item.at)) ? Number(item.at) : 0,
      flips: Number.isFinite(Number(item.flips)) ? Number(item.flips) : 0,
      // 早期条目没有这个字段：当「可能」处理，宁可轻也不要假装用户认过账
      mark: item.mark === "sure" ? "sure" : "unsure",
      okAt: Number.isFinite(Number(item.okAt)) ? Number(item.okAt) : undefined,
    });
  }
  return out;
}

/**
 * 收入 / 升级一个词。
 *
 * 已经在本子里：只有「可能 → 确定」算变化，重复收同一个词不该产生新条目。
 * 不在本子里：新建，并把计数表里的那条删掉（它已经升级，不需要再被统计）。
 */
function upsert(
  mem: WordsMemory,
  word: WordItem,
  lvl: string,
  flips: number,
  mark: ReviewMark,
  now: number,
): CollectResult {
  const w = word[0];
  const hit = mem.items.find((item) => item.w === w);

  if (hit) {
    if (mark === "sure" && hit.mark !== "sure") {
      // 升级：改成「确定不熟悉」，翻看次数取更大的那个，入本时间保持最早
      const items = mem.items.map((item) =>
        item.w === w
          ? { ...item, mark: "sure" as ReviewMark, flips: Math.max(item.flips, flips) }
          : item,
      );
      return { mem: { ...mem, items }, change: "upgraded", mark: "sure" };
    }
    return { mem, change: "none" };
  }

  const entry: ReviewEntry = {
    w,
    def: word[1],
    egEn: word[2],
    egZh: word[3],
    lvl,
    at: now,
    flips,
    mark,
  };

  const items = [entry, ...mem.items].slice(0, MAX_ITEMS);
  const watch = { ...mem.watch };
  delete watch[w];

  return { mem: { ...mem, items, watch }, change: "newly", mark };
}

/**
 * 翻到背面看一眼：计数 +1；达到阈值就按「可能不熟悉」收进复习本。
 *
 * 只在「正面 → 背面」这一次跳变时调用（来回点翻转不该把次数刷上去）。
 */
export function trackFlip(
  mem: WordsMemory,
  word: WordItem,
  lvl: string,
  now: number,
): CollectResult {
  const w = word[0];
  if (!w) return { mem, change: "none" };

  const prev = mem.watch[w];
  const flips = (prev?.f ?? 0) + 1;

  if (flips >= REVIEW_FLIP_THRESHOLD) {
    // 先把计数落进候选表（保证条目里的 flips 是全量），再试收词
    const withCount: WordsMemory = {
      ...mem,
      watch: { ...mem.watch, [w]: { f: flips, u: prev?.u ?? 0, t: now } },
    };
    return upsert(withCount, word, lvl, flips, "unsure", now);
  }

  const next: WordsMemory = {
    ...mem,
    watch: { ...mem.watch, [w]: { f: flips, u: prev?.u ?? 0, t: now } },
  };

  const keys = Object.keys(next.watch);
  if (keys.length > MAX_WATCH) {
    const drop = keys
      .sort((a, b) => next.watch[a].t - next.watch[b].t)
      .slice(0, keys.length - MAX_WATCH);
    for (const key of drop) delete next.watch[key];
  }

  return { mem: next, change: "none" };
}

/**
 * 判了一次「认识 / 不认识」。
 *
 * 「不认识」= 确定不熟悉，直接收进复习本（或把「可能」升级成「确定」）。
 * 「认识」不动档案 —— 在普通刷词里判认识，不代表这个词明天还记得，
 * 不该因此从复习本里消失（从复习本移除只发生在复习模式下判认识，见 markMastered）。
 */
export function trackJudge(
  mem: WordsMemory,
  word: WordItem,
  lvl: string,
  know: boolean,
  now: number,
): CollectResult {
  if (know) return { mem, change: "none" };

  const w = word[0];
  const prev = mem.watch[w];
  const flips = prev?.f ?? 0;

  const withUnknown: WordsMemory = {
    ...mem,
    watch: { ...mem.watch, [w]: { f: flips, u: (prev?.u ?? 0) + 1, t: now } },
  };

  return upsert(withUnknown, word, lvl, flips, "sure", now);
}

/** 复习本里判「认识」：移到已掌握分区 */
export function markMastered(mem: WordsMemory, w: string, now: number): WordsMemory {
  const hit = mem.items.find((item) => item.w === w);
  if (!hit) return mem;

  const mastered = [{ ...hit, okAt: now }, ...mem.mastered].slice(0, MAX_MASTERED);
  return { ...mem, items: mem.items.filter((item) => item.w !== w), mastered };
}

/** 已掌握的词点「再练一次」：退回待复习 */
export function restoreToReview(mem: WordsMemory, w: string): WordsMemory {
  const hit = mem.mastered.find((item) => item.w === w);
  if (!hit) return mem;

  const back: ReviewEntry = { ...hit, at: Date.now() };
  delete back.okAt;

  const items = [back, ...mem.items].slice(0, MAX_ITEMS);
  return { ...mem, items, mastered: mem.mastered.filter((item) => item.w !== w) };
}

/** 从待复习里删掉一个（「这个词我确实不用记」） */
export function removePending(mem: WordsMemory, w: string): WordsMemory {
  return { ...mem, items: mem.items.filter((item) => item.w !== w) };
}

/** 从已掌握里删掉一个 */
export function removeMastered(mem: WordsMemory, w: string): WordsMemory {
  return { ...mem, mastered: mem.mastered.filter((item) => item.w !== w) };
}

/** 清空已掌握（复习本用久了会有几百条，给一个一键清的地方） */
export function clearMastered(mem: WordsMemory): WordsMemory {
  return { ...mem, mastered: [] };
}

/** 复习本条目 → 刷词用的元组，顺序与 WordItem 一致 */
export function entryToItem(entry: ReviewEntry): WordItem {
  return [entry.w, entry.def, entry.egEn, entry.egZh];
}
