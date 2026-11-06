/**
 * 校园工具集 · 课表周次计算（纯函数）
 *
 * 这里集中放课表最容易算错的三件事：星期归一、教学周、周次表达式解析。
 * 旧版把这三件事混在 DOM 操作里，出错时只能靠肉眼比对，所以先抽出来。
 */

import type { Course, PeriodPlan, ScheduleSnapshot } from "../types";

/** 周一为 0，周日为 6 —— 新结构统一口径 */
export const DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"] as const;

/** 一周七天。列数、日期条、月历表头都用它，不写裸 7 */
export const DAYS_IN_WEEK = 7;

/**
 * 只有一个字宽时用的周几写法（手机上那排七格日期条）。
 * 和 `DAY_LABELS` 的差别只是省掉「周」字 —— 一格 43px，两个字排不开。
 */
export const DOW_CHARS = "一二三四五六日";

/**
 * 日期 → 周几列号（周一 = 0）。
 * `(getDay() + 6) % 7` 这个式子原来在归周一、算今天、写星期文案三处各写一遍，
 * 抄错一处就是整列错位，统一走这里。
 */
export function columnOfDate(date: Date): number {
  return (date.getDay() + 6) % 7;
}

/** 学期周数上限 / 默认值。导入解析、学期弹窗、旧数据兜底三处共用 */
export const MAX_TERM_WEEKS = 30;
export const DEFAULT_TERM_WEEKS = 20;

/** 学期周数下限：再短就不是一个学期了，学期弹窗的输入框也按这个卡 */
export const MIN_TERM_WEEKS = 1;

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
        if (w > 0 && w <= MAX_TERM_WEEKS) weeks.add(w);
      }
      continue;
    }

    const single = Number.parseInt(token, 10);
    if (Number.isFinite(single) && single > 0 && single <= MAX_TERM_WEEKS) weeks.add(single);
  }

  return [...weeks].sort((a, b) => a - b);
}

/** 某门课在指定周是否上课 */
export function isActiveInWeek(weeks: readonly number[], week: number): boolean {
  return weeks.includes(week);
}

/**
 * 节次范围 → 显示文案，如 [1,2] → `第 1-2 节`、[3] → `第 3 节`。
 *
 * 区间也带「第」：课程详情里原来另写了一份 `sectionLabel`，两种写法并排出现
 * （`第 3 节` / `1-2 节`），同一张卡片里粗细不齐。现在只有这一个出口。
 */
export function formatSections(sections: readonly number[]): string {
  if (sections.length === 0) return "未定节次";
  const sorted = sections.slice().sort((a, b) => a - b);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  return first === last ? `第 ${first} 节` : `第 ${first}-${last} 节`;
}

/* ------------------------------------------------------------------ *
 * 以下是 2026-10-31 迁移时补充的部分：周视图布局、JSON 导入、旧数据迁移
 * ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ *
 * 作息：每天几节、每节几点开始（B2 · 需求 4.1 + A03）
 * ------------------------------------------------------------------ */

/** 每天最少 / 最多几节。8 节够放一个满课的工作日，14 节是「早八到晚十」的极限 */
export const MIN_PERIOD_COUNT = 8;
export const MAX_PERIOD_COUNT = 14;
export const DEFAULT_PERIOD_COUNT = 12;

/** 默认一节 45 分钟、课间 10 分钟、第一节 08:00 —— 国内高校最常见的作息 */
export const DEFAULT_LESSON_MINUTES = 45;
export const DEFAULT_BREAK_MINUTES = 10;

/** 单节时长 / 课间时长的可设范围，`PeriodDialog` 的输入框和这里的 clamp 用同一套 */
export const MIN_LESSON_MINUTES = 20;
export const MAX_LESSON_MINUTES = 120;
export const MIN_BREAK_MINUTES = 0;
export const MAX_BREAK_MINUTES = 60;
export const DEFAULT_FIRST_START = "08:00";

/**
 * 午休窗口。生成的下一节落进 12:00–14:00 时直接跳到 14:00，
 * 否则会出现「第 5 节 12:35 上课」这种一看就假的默认值。
 */
const LUNCH_FROM = 12 * 60;
const LUNCH_TO = 14 * 60;

/** `08:00` → 480（分钟）；非法输入返回 null，不让一个脏值把 12 节全带偏 */
export function toMinutes(hhmm: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return hour * 60 + minute;
}

/** 480 → `08:00`；跨零点回绕，`22:30 + 60` 得到 `23:30` 而不是 `24:30` */
export function fromMinutes(total: number): string {
  const wrapped = ((total % 1440) + 1440) % 1440;
  const h = String(Math.floor(wrapped / 60)).padStart(2, "0");
  const m = String(wrapped % 60).padStart(2, "0");
  return `${h}:${m}`;
}

/** `08:00` + 45 → `08:45` */
export function addMinutes(hhmm: string, minutes: number): string {
  const base = toMinutes(hhmm);
  return base === null ? hhmm : fromMinutes(base + minutes);
}

/**
 * 按「第一节几点 + 每节多长 + 课间多长」生成整天的开始时间。
 * 午休窗口默认跳过，关掉 `skipLunch` 就是连续排满。
 */
export function generateStarts(options: {
  count?: number;
  start?: string;
  duration?: number;
  gap?: number;
  skipLunch?: boolean;
} = {}): string[] {
  const count = clampCount(options.count ?? DEFAULT_PERIOD_COUNT);
  const duration = clampDuration(options.duration ?? DEFAULT_LESSON_MINUTES);
  const gap = clampGap(options.gap ?? DEFAULT_BREAK_MINUTES);
  const skipLunch = options.skipLunch ?? true;

  // DEFAULT_FIRST_START 一定是合法 `HH:MM`，这里留一个数值兜底而不是 `!`：
  // 将来有人把默认值改成 "8:00" 之外的脏值，也只会退到 08:00，不会抛错
  const first =
    toMinutes(options.start ?? DEFAULT_FIRST_START) ?? toMinutes(DEFAULT_FIRST_START) ?? 8 * 60;
  const starts: string[] = [];
  let cursor = first;

  for (let i = 0; i < count; i += 1) {
    starts.push(fromMinutes(cursor));
    let next = cursor + duration + gap;
    if (skipLunch && next >= LUNCH_FROM && next < LUNCH_TO) next = LUNCH_TO;
    cursor = next;
  }

  return starts;
}

function clampCount(value: number): number {
  const n = Math.trunc(value);
  if (!Number.isFinite(n)) return DEFAULT_PERIOD_COUNT;
  return Math.min(MAX_PERIOD_COUNT, Math.max(MIN_PERIOD_COUNT, n));
}

function clampDuration(value: number): number {
  const n = Math.trunc(value);
  if (!Number.isFinite(n)) return DEFAULT_LESSON_MINUTES;
  return Math.min(MAX_LESSON_MINUTES, Math.max(MIN_LESSON_MINUTES, n));
}

/** 课间可设 0（连堂），上限 60 —— 再长就不像「课间」了 */
export function clampGap(value: number): number {
  const n = Math.trunc(value);
  if (!Number.isFinite(n)) return DEFAULT_BREAK_MINUTES;
  return Math.min(MAX_BREAK_MINUTES, Math.max(MIN_BREAK_MINUTES, n));
}

/** 学期总周数兜底：旧存档、手填、URL 里来的脏值都走这一个口 */
export function clampTermWeeks(value: unknown): number {
  const n = Math.trunc(Number(value));
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_TERM_WEEKS;
  return Math.min(MAX_TERM_WEEKS, Math.max(MIN_TERM_WEEKS, n));
}

/**
 * 改节数时保留用户已经调过的时间。
 * 只按新长度重排尾部，已经改好的前几节原样保留 —— 改完节数发现上午的时间被清空，
 * 是最容易让人放弃设置的一种体验。
 */
export function resizeStarts(plan: PeriodPlan, nextCount: number): string[] {
  const count = clampCount(nextCount);
  const kept = plan.starts.slice(0, count).filter((time) => toMinutes(time) !== null);
  if (kept.length >= count) return kept;

  const last = kept[kept.length - 1] ?? DEFAULT_FIRST_START;
  const tail = generateStarts({
    count: count - kept.length,
    start: addMinutes(last, plan.duration + DEFAULT_BREAK_MINUTES),
    duration: plan.duration,
  });
  return [...kept, ...tail];
}

/** 旧存档 → 完整作息。缺字段、长度不对、脏时间都在这里兜住（需求 A07） */
export function normalizePeriodPlan(raw: unknown): PeriodPlan {
  const record = (raw ?? {}) as { count?: unknown; starts?: unknown; duration?: unknown };
  const count = clampCount(Number(record.count) || DEFAULT_PERIOD_COUNT);
  const duration = clampDuration(Number(record.duration) || DEFAULT_LESSON_MINUTES);

  const rawStarts = Array.isArray(record.starts) ? record.starts : [];
  const starts: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const candidate = typeof rawStarts[i] === "string" ? rawStarts[i].trim() : "";
    const minutes = toMinutes(candidate);
    starts.push(minutes === null ? "" : fromMinutes(minutes));
  }

  // 有一个空位就整段按默认重排：留半截空白时间，周视图会是「有的行有时间，有的是空的」
  const complete = starts.every((time) => time !== "");
  return { count, duration, starts: complete ? starts : generateStarts({ count, duration }) };
}

/**
 * 一组节次 → `08:00–09:40`。
 * 结束时间取最后一节的开始 + 单节时长，不依赖「下一节几点」—— 最后一节没有下一节。
 */
export function sectionTimeRange(sections: readonly number[], plan: PeriodPlan): string {
  if (sections.length === 0) return "";
  const first = Math.min(...sections);
  const last = Math.max(...sections);
  const from = plan.starts[first - 1];
  const lastStart = plan.starts[last - 1];
  if (!from || !lastStart) return "";
  return `${from}–${addMinutes(lastStart, plan.duration)}`;
}

/**
 * 课块配色，按课程 id 轮转（B5 起换成莫兰迪色系）。
 *
 * 旧色板（#C8892A 金 / #EF8A56 橙 / #1E7A46 绿…）是给「深色描边 + 12% 淡底」那套画法用的：
 * 颜色只负责勾一条边，块里还是纸色。现在块改成实心底，颜色本身就是块，
 * 8 个高明度色块并排时饱和度必须压下来 —— 否则一屏看过去像色卡，不是课表。
 *
 * 明度统一落在 L* 70–78、饱和度 15–25%（莫兰迪的共同点：灰调、发闷、彼此不抢）。
 * 取值的硬门槛是 A03：深墨 #241F18 压在每一格上最低 7.05:1，教室那行 #3B332A 最低 5.35:1，
 * 都高于 4.5:1 的正文线（逐色实测见 campus/reviews/2026-11-06_课表B5莫兰迪配色.md）。
 *
 * 顺序按色相拉开：赭 → 橄榄 → 鼠尾草 → 青 → 蓝 → 紫 → 粉 → 米灰。
 * 相邻 id 的课因此不会撞色，连上四节的那种块也不会和隔壁混淆。
 */
export const COURSE_COLORS = [
  "#C9A990", "#ACB07E", "#A3BCA4", "#93B7B0",
  "#9DB1C6", "#AFA6C2", "#CBA5A6", "#C2B49A",
] as const;

/**
 * 旧色板 → 新色板的迁移表（B5）。
 *
 * 颜色是跟着课程一起存在 localStorage 里的十六进制值，只改 `COURSE_COLORS` 的话
 * 新课是新色、老用户的课表还是旧色，同一张表里两种风格并存。
 * 这张表在 `normalizeSnapshot` 读档时把旧值换成新值 —— 一次读档完成迁移，用户不用做任何操作，
 * 也不动旧键（回滚到旧版仍然是合法的旧色值）。
 *
 * 按**旧板下标一一对应**，不做"挑个近色"：旧板的橙#EF8A56 / 红#A03E28 在莫兰迪里没有真正的对应物，
 * 硬配对反而会把两门原本区分明显的课配成同一个色。按下标走能保住「相邻 id 不同色」这个性质。
 */
const LEGACY_COURSE_COLORS: Readonly<Record<string, string>> = {
  "#c8892a": "#C9A990",
  "#7c9a54": "#ACB07E",
  "#ef8a56": "#A3BCA4",
  "#2a5a8a": "#93B7B0",
  "#875b10": "#9DB1C6",
  "#1e7a46": "#AFA6C2",
  "#8a5a0a": "#CBA5A6",
  "#a03e28": "#C2B49A",
};

/**
 * 任意来源的色值 → 一个合法的课块色。
 *
 * 三档：旧板的值换成莫兰迪；本身就是合法 hex 的原样返回（用户手改导入 JSON 时可能填自己的色）；
 * 其它（空、拼错、"red"）退到第一个色，不让 UI 拿到一个画不出来的值。
 *
 * 必须在**所有**读档入口调用：旧的 `migrateLegacySchedule` 只覆盖 v1 迁移，
 * v2 存档是直接 `readJson` 出来的（补 periods 那条路），漏了那里老用户就看不到新配色。
 */
export function normalizeCourseColor(value: unknown): string {
  const raw = String(value ?? "").toLowerCase();
  const mapped = LEGACY_COURSE_COLORS[raw] ?? raw;
  // 注意 `i` 标志：新色板是大写十六进制（#C9A990），少了这个标志会把它们全判成非法值，
  // 结果整张表都退回第一个色 —— 这一条曾经真的踩过（8 门课全变成同一个赭色）。
  return /^#[0-9a-f]{6}$/i.test(mapped) ? mapped : COURSE_COLORS[0];
}

/**
 * 解析节次输入。
 *
 * 收三种形状：数组 `[1,2]`、字符串 `"1,2"` / `"1-3"`、单个数字。
 * 中文逗号一律当英文逗号处理 —— 手机输入法切到中文标点是常态。
 */
export function parseSections(input: unknown): number[] {
  const collect = (token: string): number[] => {
    const range = token.match(/^(\d+)\s*[-~]\s*(\d+)$/);
    if (range) {
      const from = Number.parseInt(range[1], 10);
      const to = Number.parseInt(range[2], 10);
      const list: number[] = [];
      for (let i = Math.min(from, to); i <= Math.max(from, to); i += 1) list.push(i);
      return list;
    }
    const single = Number.parseInt(token, 10);
    return Number.isFinite(single) ? [single] : [];
  };

  let raw: number[] = [];
  if (typeof input === "number") {
    raw = [input];
  } else if (Array.isArray(input)) {
    raw = input.map(Number);
  } else if (typeof input === "string") {
    raw = input
      .replace(/，/g, ",")
      .split(",")
      .map((piece) => piece.trim())
      .filter(Boolean)
      .flatMap(collect);
  }

  // 去重 + 只留 1..14，脏输入不该把整张课表带崩。
  // 上限放宽到 14 是因为 A03 允许每天排 14 节，写死 12 会把导入的第 13 节直接吞掉。
  const valid = raw
    .filter((n) => Number.isFinite(n) && n >= 1 && n <= MAX_PERIOD_COUNT)
    .map(Math.trunc);
  return [...new Set(valid)].sort((a, b) => a - b);
}

/**
 * 解析周次；返回 null 表示「每周都上」。
 *
 * 与节次不同的是，这里**必须**区分「没填」和「填了但无效」：
 * 没填是每周都上，填了无效按每周都上处理也符合直觉，所以两者都落到 null。
 */
export function parseCourseWeeks(input: unknown): number[] | null {
  if (input === null || input === undefined || input === "") return null;

  let weeks: number[] = [];
  if (typeof input === "string") {
    // 容忍「第1-16周」这种从教务系统抄来的写法
    weeks = parseWeeks(input.replace(/[第周\s]/g, ""));
  } else if (Array.isArray(input)) {
    weeks = input
      .map(Number)
      .filter((n) => Number.isFinite(n) && n >= MIN_TERM_WEEKS && n <= MAX_TERM_WEEKS)
      .map(Math.trunc);
    weeks = [...new Set(weeks)].sort((a, b) => a - b);
  }

  return weeks.length > 0 ? weeks : null;
}

/** 某门课是否在指定周上课；没标周次 = 每周都上 */
export function courseInWeek(course: Course, week: number): boolean {
  const weeks = course.weeks;
  return !weeks || weeks.length === 0 || weeks.includes(week);
}

/** 判重键：课名 + 星期 + 节次 + 周次全一样才算同一门 */
export function courseKey(
  course: Pick<Course, "name" | "day" | "sections" | "weeks">,
): string {
  return [
    course.name.trim().toLowerCase(),
    course.day,
    course.sections.slice().sort((a, b) => a - b).join(","),
    (course.weeks ?? []).slice().sort((a, b) => a - b).join(","),
  ].join("|");
}

/** 周视图里一个课块的布局结果 */
export interface DayBlock {
  course: Course;
  /** 起始节（1 基） */
  start: number;
  /** 结束节（1 基，含） */
  end: number;
  /** 该块在第几列（0 基） */
  col: number;
  /** 这一簇共分几列 */
  cols: number;
}

/**
 * 同一天内的课块布局：先按起始节分簇，簇内贪心分列，重叠的课并排显示。
 *
 * 为什么必须分栏：两块都绝对定位在同一个 top/height 上，后画的会把先画的整块盖住，
 * 用户看到的就是文字重叠的「乱码」。这是旧版 10-29 修过的问题（第 1061 行注释）。
 */
export function layoutDay(courses: readonly Course[]): DayBlock[] {
  const items = courses
    .map((course) => {
      const sections = course.sections.slice().sort((a, b) => a - b);
      return {
        course,
        start: sections[0],
        end: sections[sections.length - 1] ?? sections[0],
      };
    })
    .sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));

  const clusters: (typeof items)[] = [];
  let cluster: typeof items = [];
  let clusterEnd = 0;

  for (const item of items) {
    if (cluster.length > 0 && item.start > clusterEnd) {
      clusters.push(cluster);
      cluster = [];
      clusterEnd = 0;
    }
    cluster.push(item);
    clusterEnd = Math.max(clusterEnd, item.end);
  }
  if (cluster.length > 0) clusters.push(cluster);

  const result: DayBlock[] = [];
  for (const group of clusters) {
    // colEnd[i] = 第 i 列已经占到第几节
    const colEnd: number[] = [];
    const placed = group.map((item) => {
      let col = colEnd.findIndex((end) => end < item.start);
      if (col < 0) {
        col = colEnd.length;
        colEnd.push(item.end);
      } else {
        colEnd[col] = item.end;
      }
      return { ...item, col };
    });

    for (const item of placed) {
      result.push({ ...item, cols: colEnd.length });
    }
  }

  return result;
}

/** 周次显示文案：`[1,2,3,5]` → 「第1-3,5周」，空则「每周」 */
export function formatWeeks(weeks: readonly number[] | null): string {
  if (!weeks || weeks.length === 0) return "每周";

  const sorted = weeks.slice().sort((a, b) => a - b);
  const parts: string[] = [];
  let start = sorted[0];
  let prev = sorted[0];

  for (let i = 1; i <= sorted.length; i += 1) {
    const current = sorted[i];
    if (current === prev + 1) {
      prev = current;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}-${prev}`);
    start = current;
    prev = current;
  }

  return `第${parts.join(",")}周`;
}

/**
 * 导入 JSON 的解析结果。
 * `courses` 是解析成功的课程；`skipped` 是被丢掉的行数，要如实告诉用户「进来多少、丢了多少」。
 */
export interface ImportResult {
  courses: Omit<Course, "id" | "color">[];
  skipped: number;
  /** 整体性错误（不是合法 JSON / 没有 courses 数组） */
  error: string | null;
}

/**
 * 去掉聊天窗口爱加的 ``` 围栏（B7）。
 *
 * 用户的真实动作是「复制整段回复」，不是「精确选中 JSON」，于是粘进来的十次里有八次长这样：
 *
 *   ```json
 *   { "courses": [ … ] }
 *   ```
 *
 * 直接 `JSON.parse` 必然失败，而原来的提示只说「不是合法 JSON」—— 用户盯着满屏正常的
 * JSON 看不出问题在哪。这里只在**首尾**剥一层围栏，中间内容一个字不动：正文里出现的
 * ``` 不会被误吃。
 */
function stripCodeFence(text: string): string {
  const trimmed = text.trim();
  const match = /^```[a-zA-Z]*\s*\n?([\s\S]*?)\n?```$/.exec(trimmed);
  return match ? match[1].trim() : trimmed;
}

/** 解析「小律同款」课表 JSON：`{ courses: [{ name, dayOfWeek, sections, weeks, ... }] }` */
export function parseScheduleImport(text: string): ImportResult {
  const empty: ImportResult = { courses: [], skipped: 0, error: null };

  let data: unknown;
  try {
    data = JSON.parse(stripCodeFence(text));
  } catch {
    return { ...empty, error: "这段内容不是合法 JSON，检查一下引号和逗号。" };
  }

  const list = (data as { courses?: unknown })?.courses;
  if (!Array.isArray(list)) {
    return { ...empty, error: "JSON 里没找到 courses 数组，格式见下方示例。" };
  }

  const parsed: Omit<Course, "id" | "color">[] = [];
  let skipped = 0;

  for (const entry of list) {
    if (!entry || typeof entry !== "object") {
      skipped += 1;
      continue;
    }
    const record = entry as Record<string, unknown>;

    const sections = parseSections(record.sections ?? record.section ?? []);
    if (sections.length === 0) {
      // 没有节次就没法排进周视图，只能丢掉
      skipped += 1;
      continue;
    }

    const rawDay = record.dayOfWeek ?? record.day;
    const day = rawDay === undefined || rawDay === null ? 0 : normalizeLegacyDay(Number(rawDay));

    const name = String(record.name ?? "").trim();
    parsed.push({
      name: name === "" ? "未命名课程" : name,
      day,
      sections,
      teacher: String(record.teacher ?? ""),
      classroom: String(record.classroom ?? ""),
      weeks: parseCourseWeeks(record.weeks),
      notes: String(record.notes ?? ""),
    });
  }

  return { courses: parsed, skipped, error: null };
}

/** 合并导入结果：按 courseKey 判重，保留已有课程 */
export function mergeCourses(
  existing: readonly Course[],
  incoming: readonly Omit<Course, "id" | "color">[],
  startId: number,
): { courses: Course[]; added: number; duplicated: number; nextId: number } {
  const seen = new Set(existing.map(courseKey));
  const merged = existing.slice();
  let nextId = startId;
  let added = 0;
  let duplicated = 0;

  for (const item of incoming) {
    const key = courseKey(item);
    if (seen.has(key)) {
      duplicated += 1;
      continue;
    }
    seen.add(key);
    nextId += 1;
    merged.push({
      ...item,
      id: nextId,
      color: COURSE_COLORS[(nextId - 1) % COURSE_COLORS.length],
    });
    added += 1;
  }

  return { courses: merged, added, duplicated, nextId };
}

/* ------------------------------------------------------------------ *
 * 旧数据迁移
 * ------------------------------------------------------------------ */

/**
 * 旧版课表存档 → 新结构。
 *
 * 两个必须处理的差异：
 *  1. `day` 的口径变过。v1 存的是 `dayOfWeek % 7`（周一 = 1、周日 = 0），
 *     v2 起是 `(dayOfWeek - 1) % 7`（周一 = 0、周日 = 6）。v1 数据要整体回拨一天，
 *     否则一升级整张表错位 —— 这正是旧版 10-29 修的那个 bug。
 *  2. 字段可能缺失或类型不对，逐项兜底，坏一行不能毁掉整张课表。
 */
export function migrateLegacySchedule(raw: unknown): ScheduleSnapshot | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as {
    v?: unknown;
    cseq?: unknown;
    sample?: unknown;
    courses?: unknown;
    term?: unknown;
    week?: unknown;
  };

  if (!Array.isArray(data.courses)) return null;

  const legacy = Number(data.v ?? 1) < 2;
  const courses: Course[] = [];

  for (const entry of data.courses) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;

    const sections = parseSections(record.sections);
    if (sections.length === 0) continue;

    const name = String(record.name ?? "").trim();
    if (name === "") continue;

    // v2 的 day 已经是 0 基，只做模 7 兜底；v1 才需要回拨
    const rawDay = Math.trunc(Number(record.day ?? 0)) || 0;
    const day = legacy ? normalizeLegacyDay(rawDay) : ((rawDay % 7) + 7) % 7;

    courses.push({
      id: Math.trunc(Number(record.id ?? 0)) || 0,
      name,
      color: normalizeCourseColor(record.color),
      day,
      sections,
      teacher: String(record.teacher ?? ""),
      classroom: String(record.classroom ?? ""),
      weeks: parseCourseWeeks(record.weeks),
      notes: String(record.notes ?? ""),
    });
  }

  const seq = Math.max(
    Math.trunc(Number(data.cseq ?? 0)) || 0,
    0,
    ...courses.map((course) => course.id),
  );

  const termRecord = data.term as { start?: unknown; weeks?: unknown } | null | undefined;
  const termStart = typeof termRecord?.start === "string" ? termRecord.start : "";
  const termWeeksCount = Math.trunc(Number(termRecord?.weeks ?? 0));

  return {
    courses,
    seq,
    sample: Boolean(data.sample),
    term:
      /^\d{4}-\d{2}-\d{2}$/.test(termStart)
        ? {
            start: termStart,
            weeks: clampTermWeeks(termWeeksCount),
          }
        : null,
    week: Math.max(1, Math.trunc(Number(data.week ?? 1)) || 1),
    // 旧版是「12 节写死」，没有存作息。补一套默认值，用户第一次进「上课时间」才需要改
    periods: normalizePeriodPlan(undefined),
  };
}
