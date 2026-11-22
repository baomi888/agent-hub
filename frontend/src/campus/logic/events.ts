/**
 * 大学年历表 · 逻辑层
 *
 * 这一层只做「算」，不碰 React、不碰 localStorage。界面拿到的是排好序、算好天数的数组。
 *
 * 三个必须在这里收口的规则：
 *
 * 1. **周期规则在读取时求值**，不在数据里写年份。
 *    大挑奇数年、小挑偶数年 —— 用户切到 2027，小挑自动消失、大挑自动出现。
 *
 * 2. **`exact` 只在它标的那一年生效**。
 *    `2026-09-10` 是 2026 年的确切日期，2027 年不能拿它当「9 月 10 日」用，
 *    否则每年都会显示一个去年才对的日期 —— 这比只写「9 月」更误导。
 *
 * 3. **学年 = 9 月到次年 8 月**。
 *    学生按学期过日子，不按自然年。1 月打开年历表却看到「这是 2027 学年」会很别扭，
 *    所以学年用起始年命名：2026 年 9 月 – 2027 年 8 月 = 「2026–2027 学年」。
 */

import type {
  EventCategoryId,
  EventCycle,
  EventItem,
  EventMark,
  EventWindow,
  EventsState,
} from "../types";
import { CERTS } from "../tools/events/certs";
import { CATEGORIES, EVENTS, type CategoryMeta } from "../tools/events/data";

/** 分类元信息与赛事数据对外复用：界面直接吃这两份，不必再往 tools 目录里绕一遍 */
export { CATEGORIES, EVENTS };
export type { CategoryMeta };

/** 全部条目：赛事 + 考证，共 83 条 */
export const ALL_ITEMS: readonly EventItem[] = [...EVENTS, ...CERTS];

/** 分类查表 */
export const CATEGORY_MAP: Record<EventCategoryId, CategoryMeta> = CATEGORIES.reduce(
  (acc, cat) => {
    acc[cat.id] = cat;
    return acc;
  },
  {} as Record<EventCategoryId, CategoryMeta>,
);

/** 分类的展示顺序（创新创业在前，升学就业在最后） */
export const CATEGORY_ORDER: readonly EventCategoryId[] = CATEGORIES.map((c) => c.id);

/* ==================== 学年与月份 ==================== */

/** 一个学年的月份顺序：9 月 → 次年 8 月 */
export const SCHOOL_MONTHS: readonly number[] = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8];

/**
 * 某个日期属于哪个学年。
 * 9 月及以后算新学年的起点，1–8 月仍属于上一年 9 月开始的那个学年。
 */
export function schoolYearOf(date: Date): number {
  const month = date.getMonth() + 1;
  return month >= 9 ? date.getFullYear() : date.getFullYear() - 1;
}

/** 学年标签：「2026–2027 学年」 */
export function schoolYearLabel(year: number): string {
  return `${year}–${year + 1} 学年`;
}

/**
 * 学年内的第 n 个月，返回 { year, month }。
 * `offset` 从 0 开始：0 = 9 月，3 = 12 月，4 = 次年 1 月。
 */
export function monthAt(schoolYear: number, offset: number): { year: number; month: number } {
  const idx = ((offset % 12) + 12) % 12;
  const month = SCHOOL_MONTHS[idx];
  return { year: month >= 9 ? schoolYear : schoolYear + 1, month };
}

/** 某个学年内，某个月对应的自然年 */
export function calendarYearOf(schoolYear: number, month: number): number {
  return month >= 9 ? schoolYear : schoolYear + 1;
}

/** YYYY-MM 键，用于记住上次看的月份 */
export function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}`;
}

/** 从 YYYY-MM 解析，失败返回 null */
export function parseMonthKey(key: string): { year: number; month: number } | null {
  const m = /^(\d{4})-(\d{2})$/.exec(key);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  if (month < 1 || month > 12) return null;
  return { year, month };
}

/* ==================== 周期 ==================== */

/**
 * 这一年办不办。
 *
 * `biennial-odd` / `biennial-even` 与 `odd` / `even` 的判定其实一样，
 * 分成四个值是为了让数据说人话：「两年一届、奇数年办」和「每年都办、但只在奇数年有国赛」
 * 是两件事，未来若要区分「省赛每年、国赛隔年」得留得下这个差别。
 */
export function occursInYear(cycle: EventCycle, year: number): boolean {
  const odd = year % 2 === 1;
  switch (cycle) {
    case "annual":
      return true;
    case "odd":
    case "biennial-odd":
      return odd;
    case "even":
    case "biennial-even":
      return !odd;
  }
}

/** 周期的人话说明，详情页用 */
export function cycleText(cycle: EventCycle): string {
  switch (cycle) {
    case "annual":
      return "每年举办";
    case "odd":
    case "biennial-odd":
      return "奇数年举办";
    case "even":
    case "biennial-even":
      return "偶数年举办";
  }
}

/* ==================== 窗口展开 ==================== */

/** 这个窗口在某一年覆盖了哪几个月（1–12） */
export function monthsOf(win: EventWindow): number[] {
  if (win.from <= win.to) {
    const out: number[] = [];
    for (let m = win.from; m <= win.to; m += 1) out.push(m);
    return out;
  }
  // 跨年窗口（如 10 月 – 次年 3 月）：在任意自然年里，1–to 与 from–12 都在窗口内。
  // 这不是 bug —— 「10 月到次年 3 月」这件事，2027 年的 1 月确实还在其中。
  const out: number[] = [];
  for (let m = 1; m <= win.to; m += 1) out.push(m);
  for (let m = win.from; m <= 12; m += 1) out.push(m);
  return out;
}

/** 是否「全年可报」（1–12 月全覆盖）。这类条目不参与日历染色，只在底部单列 */
export function isAllYear(item: EventItem): boolean {
  return item.runs.some((w) => w.from === 1 && w.to === 12);
}

/** 一条目在某月的一次发生 */
export interface Occurrence {
  item: EventItem;
  window: EventWindow;
  /** 报名阶段还是比赛/考试阶段 */
  phase: "signup" | "run";
  /** 该月的具体某天（1–31）；只有已核实的 exact 才有 */
  day?: number;
  /** 是否精确到日 */
  precise: boolean;
}

/**
 * 把一条目展开成「某年某月的一次发生」。
 *
 * 同一个月的报名与比赛会各出一条 —— 它们是两件事：
 * 「8 月要报名」和「9 月要比赛」，漏掉报名比漏掉比赛更常见。
 */
export function occurrencesInMonth(
  item: EventItem,
  year: number,
  month: number,
): Occurrence[] {
  if (!occursInYear(item.cycle, year)) return [];

  const out: Occurrence[] = [];

  const exactDay = (win: EventWindow): number | undefined => {
    if (!win.exact) return undefined;
    // exact 只在它标的那一年生效，见文件头第 2 条
    const [y, m, d] = win.exact.split("-").map(Number);
    if (y !== year || m !== month) return undefined;
    return d;
  };

  if (item.signup) {
    for (const win of item.signup) {
      if (monthsOf(win).includes(month)) {
        out.push({ item, window: win, phase: "signup", day: exactDay(win), precise: false });
      }
    }
  }

  for (const win of item.runs) {
    if (monthsOf(win).includes(month)) {
      const day = exactDay(win);
      out.push({ item, window: win, phase: "run", day, precise: day !== undefined });
    }
  }

  return out;
}

/** 某月内所有发生，按「有确切日期的排前面」 */
export function monthOccurrences(items: readonly EventItem[], year: number, month: number): Occurrence[] {
  const out: Occurrence[] = [];
  for (const item of items) out.push(...occurrencesInMonth(item, year, month));
  return out.sort((a, b) => {
    if (a.precise !== b.precise) return a.precise ? -1 : 1;
    return (a.day ?? 0) - (b.day ?? 0);
  });
}

/* ==================== 过滤 ==================== */

export interface FilterOptions {
  /** 已处置的条目 id → 状态；已处置的默认不显示 */
  marks: Record<string, EventMark>;
  /** 只看这些分类；空数组或未传 = 全部 */
  categories?: EventCategoryId[];
  /** 只看升学就业（大四开关） */
  careerOnly?: boolean;
  /** 搜索关键词 */
  keyword?: string;
}

/** 覆盖默认过滤：已处置（考过/不感兴趣）的条目从年历表消失 */
export function isHidden(item: EventItem, marks: Record<string, EventMark>): boolean {
  return marks[item.id] !== undefined;
}

/** 条目是否通过当前筛选 */
export function matchFilter(item: EventItem, options: FilterOptions): boolean {
  if (isHidden(item, options.marks)) return false;
  if (options.careerOnly && item.category !== "career") return false;
  if (options.categories && options.categories.length > 0) {
    if (!options.categories.includes(item.category)) return false;
  }
  if (options.keyword) {
    const k = options.keyword.trim().toLowerCase();
    if (k !== "") {
      const hay = `${item.name} ${item.short} ${item.intro} ${item.fitFor} ${(item.tags ?? []).join(" ")}`.toLowerCase();
      if (!hay.includes(k)) return false;
    }
  }
  return true;
}

/** 按筛选条件过滤整份数据 */
export function filterItems(items: readonly EventItem[], options: FilterOptions): EventItem[] {
  return items.filter((item) => matchFilter(item, options));
}

/* ==================== 日历 ==================== */

/** 一个日历格 */
export interface CalendarCell {
  /** 该格对应的日；不属于本月的补白格为 null */
  day: number | null;
  /** 该日发生的事（只含已核实到日的） */
  occurrences: Occurrence[];
  /** 是否今天 */
  today: boolean;
}

/** 月份天数 */
export function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

/**
 * 生成月历格子（周一为第一列）。
 *
 * 补白格保留位置而不是丢掉：丢掉的话 1 号会跑到周日的格子里，
 * 整张月历的星期就全错位了。
 */
export function buildMonthCells(
  items: readonly EventItem[],
  year: number,
  month: number,
  today: { year: number; month: number; day: number } | null,
): CalendarCell[] {
  const first = new Date(year, month - 1, 1);
  // getDay() 周日 = 0，这里换成周一 = 0
  const lead = (first.getDay() + 6) % 7;
  const total = daysInMonth(year, month);

  const byDay = new Map<number, Occurrence[]>();
  /*
   * 只有「已核实到日」的条目落进格子。
   *
   * 月份精度的条目曾经试过统一挂 1 号：1 号变成「数模 + 统计建模 + …+32」一坨，
   * 其余 30 格全空 —— 既没说清哪天有事，又把 1 号伪装成一个特殊日子。
   * 它们现在由日历上方的「本月窗口」条统一交代，不占格子。
   */
  for (const occ of monthOccurrences(items, year, month)) {
    if (!occ.precise || occ.day === undefined) continue;
    const list = byDay.get(occ.day) ?? [];
    list.push(occ);
    byDay.set(occ.day, list);
  }

  const cells: CalendarCell[] = [];
  for (let i = 0; i < lead; i += 1) {
    cells.push({ day: null, occurrences: [], today: false });
  }
  for (let d = 1; d <= total; d += 1) {
    cells.push({
      day: d,
      occurrences: byDay.get(d) ?? [],
      today: today !== null && today.year === year && today.month === month && today.day === d,
    });
  }
  // 补齐到整周，末行不满会看着像被裁掉
  while (cells.length % 7 !== 0) cells.push({ day: null, occurrences: [], today: false });

  return cells;
}

/* ==================== 近期 ==================== */

/** 一条「即将发生」 */
export interface UpcomingEntry {
  item: EventItem;
  phase: "signup" | "run";
  /** 日期 YYYY-MM-DD；月份精度时取该月 1 日 */
  date: string;
  daysLeft: number;
  precise: boolean;
  /** 窗口说明，如「省赛 7–8 月」 */
  note?: string;
}

/** 两个日期相差多少天（按本地零点算，避开夏令时与时分秒） */
export function daysBetween(from: Date, to: Date): number {
  const a = new Date(from.getFullYear(), from.getMonth(), from.getDate()).getTime();
  const b = new Date(to.getFullYear(), to.getMonth(), to.getDate()).getTime();
  return Math.round((b - a) / 86_400_000);
}

/**
 * 未来 N 天内需要留意的事。
 *
 * 只收两类，这是刻意的收窄：
 *
 *  1. **已核实到日的事件** —— 真正有 deadline 的；
 *  2. **窗口的最后一个月** —— 「再不报就没了」。起始月不提醒：
 *     「4 月可以开始准备」这种话每条赛事都说一遍，60 天清单会变成两张月历的复制品
 *     （实测过：放开起始月，60 天里能堆出 56 条，全是「还有 11 天」）。
 *
 * 信息差最贵的确实是报名截止，而截止只有一次 —— 提醒最后一个月就够了。
 */
export function upcomingEntries(
  items: readonly EventItem[],
  today: Date,
  days: number,
  marks: Record<string, EventMark>,
): UpcomingEntry[] {
  const out: UpcomingEntry[] = [];
  const seen = new Set<string>();
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());

  for (let offset = 0; offset <= days; offset += 1) {
    const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + offset);
    const year = d.getFullYear();
    const month = d.getMonth() + 1;

    for (const occ of monthOccurrences(items, year, month)) {
      if (isHidden(occ.item, marks)) continue;

      if (occ.precise) {
        // 精确条目只在它那一天出现，别在整月里重复 31 次
        if (occ.day !== d.getDate()) continue;
        const key = `${occ.item.id}-${occ.phase}-exact`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          item: occ.item,
          phase: occ.phase,
          date: `${year}-${String(month).padStart(2, "0")}-${String(occ.day).padStart(2, "0")}`,
          daysLeft: offset,
          precise: true,
          note: occ.window.note,
        });
        continue;
      }

      // 月份精度：只在窗口的最后一个月、每月 1 日登记一次
      if (d.getDate() !== 1) continue;
      if (month !== occ.window.to) continue;

      const key = `${occ.item.id}-${occ.phase}-last`;
      if (seen.has(key)) continue;
      seen.add(key);

      const firstOfMonth = new Date(year, month - 1, 1);
      const start = firstOfMonth < base ? base : firstOfMonth;
      out.push({
        item: occ.item,
        phase: occ.phase,
        date: `${year}-${String(month).padStart(2, "0")}-01`,
        daysLeft: daysBetween(today, start),
        precise: false,
        note: occ.window.note,
      });
    }
  }

  // 同一天里报名排在比赛前面：报名错过就没有后面了
  return out.sort((a, b) => {
    if (a.daysLeft !== b.daysLeft) return a.daysLeft - b.daysLeft;
    if (a.phase !== b.phase) return a.phase === "signup" ? -1 : 1;
    return a.item.name.localeCompare(b.item.name, "zh");
  });
}

/* ==================== 年级 ==================== */

/**
 * 这条对某年级是否「适合」。
 *
 * 数据里 `grades` 不填 = 全年级适合，返回 true。
 * 判断成「无关」的不会隐藏条目，只在列表里不打「适合你」标记 ——
 * 藏起来等于替用户决定他不该看，而年历表的价值恰恰是「原来还有这个」。
 */
export function fitsGrade(item: EventItem, grade: number | null): boolean {
  if (grade === null) return false;
  if (!item.grades || item.grades.length === 0) return true;
  return item.grades.includes(grade);
}

/* ==================== 状态 ==================== */

/** 初始状态 */
export function emptyEventsState(now: Date): EventsState {
  return {
    v: 1,
    marks: {},
    pins: [],
    grade: null,
    careerOnly: false,
    view: "calendar",
    month: monthKey(now.getFullYear(), now.getMonth() + 1),
    introSeen: false,
  };
}

/**
 * 把读出来的东西洗成合法状态。
 *
 * localStorage 里的东西不可信：手改过、旧版本写的、写一半断电的都可能。
 * 这里逐字段校验，坏字段退回默认值，绝不让页面因为一个坏字段白屏。
 */
export function normalizeEventsState(raw: unknown, now: Date): EventsState {
  const base = emptyEventsState(now);
  if (typeof raw !== "object" || raw === null) return base;
  const r = raw as Record<string, unknown>;

  const marks: Record<string, EventMark> = {};
  if (typeof r.marks === "object" && r.marks !== null) {
    for (const [k, v] of Object.entries(r.marks as Record<string, unknown>)) {
      if (v === "attended" || v === "passed" || v === "skip") marks[k] = v;
    }
  }

  const pins: { id: string; note?: string }[] = [];
  if (Array.isArray(r.pins)) {
    for (const p of r.pins) {
      if (typeof p === "object" && p !== null && typeof (p as { id?: unknown }).id === "string") {
        const note = (p as { note?: unknown }).note;
        pins.push({
          id: (p as { id: string }).id,
          note: typeof note === "string" && note.trim() !== "" ? note : undefined,
        });
      }
    }
  }

  const grade =
    r.grade === 1 || r.grade === 2 || r.grade === 3 || r.grade === 4 ? (r.grade as 1 | 2 | 3 | 4) : null;

  const view =
    r.view === "calendar" || r.view === "timeline" || r.view === "mine" ? r.view : base.view;

  const month = typeof r.month === "string" && parseMonthKey(r.month) ? r.month : base.month;

  return {
    v: 1,
    marks,
    pins,
    grade,
    careerOnly: r.careerOnly === true,
    view,
    month,
    introSeen: r.introSeen === true,
  };
}

/** 已处置条目的分组统计，给「我的」视图用 */
export interface MarkedGroup {
  mark: EventMark;
  label: string;
  items: EventItem[];
}

export function groupByMark(items: readonly EventItem[], marks: Record<string, EventMark>): MarkedGroup[] {
  const groups: MarkedGroup[] = [
    { mark: "attended", label: "参加过的比赛", items: [] },
    { mark: "passed", label: "已通过的考试", items: [] },
    { mark: "skip", label: "不感兴趣的", items: [] },
  ];
  const byId = new Map(items.map((i) => [i.id, i]));

  for (const [id, mark] of Object.entries(marks)) {
    const item = byId.get(id);
    if (!item) continue;
    const group = groups.find((g) => g.mark === mark);
    if (group) group.items.push(item);
  }

  for (const g of groups) g.items.sort((a, b) => a.name.localeCompare(b.name, "zh"));
  return groups.filter((g) => g.items.length > 0);
}

/* ==================== 置顶提醒 ==================== */

/** 一条置顶提醒的展示形态 */
export interface PinView {
  id: string;
  item: EventItem | null;
  /** 用户自定义文案；空则用条目名 */
  label: string;
  /** 下一次发生：日期与剩余天数；算不出为 null */
  next: { date: string; daysLeft: number; precise: boolean; phase: "signup" | "run" } | null;
  /**
   * 这条已经被标记为「参加过 / 不感兴趣」。
   *
   * 必须单独标出来：标记过的条目不再参与时间计算，`next` 自然是 null，
   * 但界面上写「今年不办」是错的 —— 不是不办，是用户自己关掉了。
   */
  marked: boolean;
}

/**
 * 把置顶项算成可展示的形式。
 *
 * 找不到对应条目的置顶（数据里删了、id 写错了）不丢弃而是标 `item: null`，
 * 让用户在「我的」里能看见并删掉它 —— 悄悄消失会让人以为是自己记错了。
 */
export function buildPins(
  pins: readonly { id: string; note?: string }[],
  items: readonly EventItem[],
  today: Date,
  marks: Record<string, EventMark>,
): PinView[] {
  const byId = new Map(items.map((i) => [i.id, i]));

  return pins.map((pin) => {
    const item = byId.get(pin.id) ?? null;
    let next: PinView["next"] = null;

    if (item) {
      // 先看今天起 366 天内的第一次发生（覆盖一整年，跨年窗口也能算到）
      const hits = upcomingEntries([item], today, 366, marks);
      if (hits.length > 0) {
        next = {
          date: hits[0].date,
          daysLeft: hits[0].daysLeft,
          precise: hits[0].precise,
          phase: hits[0].phase,
        };
      }
    }

    return {
      id: pin.id,
      item,
      label: pin.note?.trim() ? pin.note : (item?.short ?? pin.id),
      next,
      marked: item !== null && marks[item.id] !== undefined,
    };
  });
}

/** 剩余天数的人话 */
export function daysText(daysLeft: number): string {
  if (daysLeft === 0) return "今天";
  if (daysLeft === 1) return "明天";
  if (daysLeft < 0) return `已过 ${Math.abs(daysLeft)} 天`;
  return `还有 ${daysLeft} 天`;
}

/** 日期显示：精确显示到日，月份精度只显示到月 */
export function dateText(date: string, precise: boolean): string {
  const [, m, d] = date.split("-");
  const month = Number(m);
  if (!precise || d === "01") return `${month} 月`;
  return `${month} 月 ${Number(d)} 日`;
}

/** 阶段的短标签 */
export function phaseText(phase: "signup" | "run", kind: EventItem["kind"]): string {
  if (phase === "signup") return "报名";
  if (kind === "contest") return "比赛";
  if (kind === "cert") return "考试";
  return "考试";
}
