/**
 * 校园工具集 · 法定节假日与调休（B3，需求 4.5 / 4.6 / 4.7）
 *
 * **为什么写死在代码里**
 * 板块铁律是「免登录、纯前端、无后端接口」。节假日只有两条路：
 *  ① 内置常量表 —— 离线可用、零请求，代价是每年要发一次版；
 *  ② 运行时请求第三方接口 —— 免维护，但校园网墙掉或离线时静默失效，
 *     而且课表是「打开就要准」的东西，不能指望网络。
 * 选 ①。更新方式见文末 `DATA_NOTE`。
 *
 * **为什么不按周末判断**
 * 周末上课是真实存在的（补课、选修、成人教育），把周六周日一律算成「不用上课」
 * 会给这些人一个错信息。这里只认国务院发布的放假与调休，其余日子一律「平常」。
 *
 * 数据来源（均为国务院办公厅通知，逐字核对过 gov.cn 原文，核对日 2026-11-06）：
 *  - 2026 年：国办 2025-11-04 发布《关于 2026 年部分节假日安排的通知》——
 *    元旦 1/1–1/3（1/4 上班）、春节 2/15–2/23 共 9 天（2/14、2/28 上班）、
 *    清明 4/4–4/6、劳动节 5/1–5/5（5/9 上班）、端午 6/19–6/21、
 *    中秋 9/25–9/27、国庆 10/1–10/7（9/20、10/10 上班）
 *  - 2027 年：国办 2026-11-05 发布，国办发明电〔2026〕10 号 ——
 *    元旦 1/1–1/3、春节 2/5–2/13 共 9 天（1/31、2/14 上班）、清明 4/3–4/5、
 *    劳动节 5/1–5/5（5/8 上班）、端午 6/9 共 1 天、中秋 9/15 共 1 天、
 *    国庆 10/1–10/7（9/26、10/9 上班）
 *
 * **加新年份的时候照这个格式抄通知原文，不要凭「往年规律」推** —— 春节 9 天、
 * 端午中秋只放 1 天这些都不是规律，是当年通知写的；推错一天，用户就白跑一趟教室。
 */

/** 这一天是什么性质：放假 / 调休上班。平常日子不产生记录，查询返回 null */
export type DayKind = "off" | "work";

export interface DayInfo {
  kind: DayKind;
  /** 节日名，如「春节」；调休上班日带「调休」二字 */
  label: string;
}

interface YearPlan {
  /** [起, 止, 节日名]，起止都含在假期内 */
  off: readonly (readonly [string, string, string])[];
  /** [日期, 说明]，调休上班的日子 */
  work: readonly (readonly [string, string])[];
}

const PLANS: Record<number, YearPlan> = {
  2026: {
    off: [
      ["2026-01-01", "2026-01-03", "元旦"],
      ["2026-02-15", "2026-02-23", "春节"],
      ["2026-04-04", "2026-04-06", "清明节"],
      ["2026-05-01", "2026-05-05", "劳动节"],
      ["2026-06-19", "2026-06-21", "端午节"],
      ["2026-09-25", "2026-09-27", "中秋节"],
      ["2026-10-01", "2026-10-07", "国庆节"],
    ],
    work: [
      ["2026-01-04", "元旦调休"],
      ["2026-02-14", "春节调休"],
      ["2026-02-28", "春节调休"],
      ["2026-05-09", "劳动节调休"],
      ["2026-09-20", "国庆节调休"],
      ["2026-10-10", "国庆节调休"],
    ],
  },
  2027: {
    off: [
      ["2027-01-01", "2027-01-03", "元旦"],
      ["2027-02-05", "2027-02-13", "春节"],
      ["2027-04-03", "2027-04-05", "清明节"],
      ["2027-05-01", "2027-05-05", "劳动节"],
      ["2027-06-09", "2027-06-09", "端午节"],
      ["2027-09-15", "2027-09-15", "中秋节"],
      ["2027-10-01", "2027-10-07", "国庆节"],
    ],
    work: [
      ["2027-01-31", "春节调休"],
      ["2027-02-14", "春节调休"],
      ["2027-05-08", "劳动节调休"],
      ["2027-09-26", "国庆节调休"],
      ["2027-10-09", "国庆节调休"],
    ],
  },
};

/** 数据覆盖的年份，界面上要如实标出来（4.5 的口径：说明「数据截至哪一年」） */
export const COVERED_YEARS: readonly number[] = Object.keys(PLANS)
  .map(Number)
  .sort((a, b) => a - b);

/** 界面提示用的一句话。改了表就改这里，两处永远一致 */
export const DATA_NOTE = `节假日按国务院办公厅 ${COVERED_YEARS.join("、")} 年安排`;

/** 本地日期 → `YYYY-MM-DD`。用本地时区，不能用 `toISOString()`（那是 UTC，晚上会差一天） */
export function dateKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** `YYYY-MM-DD` → 本地零点的 Date；非法返回 null */
function parseKey(key: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 把一段假期展开成逐日 key（含首尾） */
function expandRange(from: string, to: string): string[] {
  const start = parseKey(from);
  const end = parseKey(to);
  if (!start || !end) return [];

  const keys: string[] = [];
  const cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  // 上限 60 天只是防呆：表写错了不至于把浏览器转死
  for (let i = 0; i < 60 && cursor.getTime() <= end.getTime(); i += 1) {
    keys.push(dateKey(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return keys;
}

/**
 * 逐日索引。220 天左右的表在模块加载时建一次，查询是 O(1) —— 周视图每次渲染
 * 要查 7 天，建表这点开销远小于每次翻周都遍历一遍区间。
 */
const DAY_INDEX: ReadonlyMap<string, DayInfo> = buildIndex();

function buildIndex(): Map<string, DayInfo> {
  const index = new Map<string, DayInfo>();
  for (const plan of Object.values(PLANS)) {
    for (const [from, to, label] of plan.off) {
      for (const key of expandRange(from, to)) {
        index.set(key, { kind: "off", label });
      }
    }
    // 调休上班日放在后面：万一哪天表里写重了，以「上班」为准更符合直觉
    // （放假和上班同时命中的唯一可能是录入错误，此时提示「要上课」风险更小）
    for (const [key, label] of plan.work) {
      index.set(key, { kind: "work", label });
    }
  }
  return index;
}

/** 查某天是不是放假 / 调休上班；平常日子返回 null */
export function dayInfoOf(date: Date): DayInfo | null {
  return DAY_INDEX.get(dateKey(date)) ?? null;
}

/**
 * 一周（7 个日期）里涉及的节日名，去重后按出现顺序返回。
 * 4.7 要在课表右上角提示「这一周因为什么放假」，跨节的假期（国庆 7 天跨两周）两周边都要显示。
 */
export function holidayLabelsOf(dates: readonly (Date | null)[]): string[] {
  const labels: string[] = [];
  for (const date of dates) {
    if (!date) continue;
    const info = dayInfoOf(date);
    if (info?.kind === "off" && !labels.includes(info.label)) labels.push(info.label);
  }
  return labels;
}

/** 数据是否覆盖这一年 —— 表外的年份不显示休/班，避免给出错的判断 */
export function isYearCovered(year: number): boolean {
  return COVERED_YEARS.includes(year);
}
