/**
 * 校园工具集 · 四六级词卡（纯函数）
 *
 * 这一版要保住旧版踩过的坑修复（内测 8.2「复习队列把用户卡死」）：
 *  **点「不认识」也必须前进**。第一版不前进，于是用户卡在同一个词上，
 *  必须全部点「认识」才能往下走，看起来就是死循环。
 *
 * 现在的规则（与旧版 tools.html 的 wJudge 完全一致）：
 *  1. 无论认识与否，当前词都前进一位；
 *  2. 「不认识」的词进本轮复习堆，等整组过完，只用它们开下一轮；
 *  3. 下一轮里全部认识，这一组才算完成。
 */

import type { WordItem, WordsProgress } from "../types";

/** 一组多少个词，与词库 index.json 的 group_size 对齐 */
export const GROUP_SIZE = 20;

/** 一次练习的运行时状态。渲染层只读它，不改它 —— 状态推进全部走 judge() */
export interface WordSession {
  /** 本轮要过的词（每轮开始时确定） */
  pool: WordItem[];
  /** 当前词下标 */
  idx: number;
  /** 组内第几遍：1 是第一遍，≥2 是复习轮 */
  round: number;
  /** 下一轮的池子：本轮点了「不认识」的词 */
  heavy: WordItem[];
  /** 本组是否已认完 */
  done: boolean;
  /** 累计判断次数 */
  seen: number;
  /** 其中认识次数 */
  known: number;
}

/** 一组有多少词；词库为空时也返回 1，避免出现「第 1 / 0 组」 */
export function groupCount(total: number): number {
  if (!Number.isFinite(total) || total <= 0) return 1;
  return Math.max(1, Math.ceil(total / GROUP_SIZE));
}

/** 取第 group 组的词（group 从 1 开始）。越界返回空数组，由调用方兜底 */
export function groupSlice(words: readonly WordItem[], group: number): WordItem[] {
  const g = Math.max(1, Math.floor(group));
  const start = (g - 1) * GROUP_SIZE;
  return words.slice(start, start + GROUP_SIZE);
}

/** 开一组：清空计数，重置轮次 */
export function startSession(pool: readonly WordItem[]): WordSession {
  const items = pool.slice();
  return {
    pool: items,
    idx: 0,
    round: 1,
    heavy: [],
    done: items.length === 0,
    seen: 0,
    known: 0,
  };
}

/**
 * 判定当前词。返回**新对象**，不改入参，方便直接丢给 setState。
 *
 * 学习阶段结束时（走完一整轮还有没记住的），用复习堆开下一轮；
 * 复习堆空则本组完成。
 */
export function judge(session: WordSession, know: boolean): WordSession {
  if (session.done || session.pool.length === 0) return session;

  const current = session.pool[session.idx];
  const heavy = session.heavy.slice();

  // 同名词去重：词库里偶尔有同形词，重复塞进复习堆只会让这一轮变长
  if (!know && !heavy.some((item) => item[0] === current[0])) {
    heavy.push(current);
  }

  const nextIdx = session.idx + 1;
  const nextSeen = session.seen + 1;
  const nextKnown = session.known + (know ? 1 : 0);

  // 还在一轮中间：继续往下走，两个分支都前进
  if (nextIdx < session.pool.length) {
    return { ...session, idx: nextIdx, heavy, seen: nextSeen, known: nextKnown };
  }

  // 一轮走完
  if (heavy.length > 0) {
    return {
      pool: heavy,
      idx: 0,
      round: session.round + 1,
      heavy: [],
      done: false,
      seen: nextSeen,
      known: nextKnown,
    };
  }

  return {
    ...session,
    pool: session.pool,
    idx: session.pool.length,
    heavy: [],
    done: true,
    seen: nextSeen,
    known: nextKnown,
  };
}

/** 空进度 */
export function emptyProgress(): WordsProgress {
  return { lvl: "", last: {}, done: {} };
}

/** 某个级别已练完的组号（升序，副本） */
export function doneGroups(progress: WordsProgress, lvl: string): number[] {
  const list = progress.done[lvl];
  return Array.isArray(list) ? list.slice().sort((a, b) => a - b) : [];
}

/** 标记某组练完，返回新进度对象 */
export function markGroupDone(progress: WordsProgress, lvl: string, group: number): WordsProgress {
  const existing = doneGroups(progress, lvl);
  if (existing.includes(group)) return progress;

  const next = existing.concat(group).sort((a, b) => a - b);
  return { ...progress, done: { ...progress.done, [lvl]: next } };
}

/** 记录某级别看到第几组，下次进来接着练 */
export function rememberGroup(progress: WordsProgress, lvl: string, group: number): WordsProgress {
  return { ...progress, lvl, last: { ...progress.last, [lvl]: group } };
}

/**
 * 旧进度迁移（旧键 `baomi_words_v2`）。
 *
 * 旧结构：`{ lvl: string, last: { [lvl]: number }, done: { [lvl]: number[] } }`。
 * 字段名一致，所以这里主要是清洗：过滤掉非数字组号、非字符串级别。
 */
export function migrateLegacyProgress(raw: unknown): WordsProgress | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as { lvl?: unknown; last?: unknown; done?: unknown };

  const result = emptyProgress();
  if (typeof record.lvl === "string") result.lvl = record.lvl;

  if (record.last && typeof record.last === "object") {
    for (const [key, value] of Object.entries(record.last as Record<string, unknown>)) {
      const group = Number(value);
      if (Number.isFinite(group) && group >= 1) result.last[key] = Math.floor(group);
    }
  }

  if (record.done && typeof record.done === "object") {
    for (const [key, value] of Object.entries(record.done as Record<string, unknown>)) {
      if (!Array.isArray(value)) continue;
      const groups = value
        .map(Number)
        .filter((n) => Number.isFinite(n) && n >= 1)
        .map((n) => Math.floor(n))
        .sort((a, b) => a - b);
      // 去重：旧数据里出现重复组号时，UI 上的「已练 N 组」会虚高
      result.done[key] = [...new Set(groups)];
    }
  }

  return result;
}
