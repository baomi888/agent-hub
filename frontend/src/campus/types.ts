/**
 * 校园工具集 · 共享类型
 *
 * 板块内所有类型收在这里，避免各工具各定义一套。
 * 与存储契约相关的字段说明见 campus/docs/02_数据与本地存储契约.md。
 */

/** GPA 换算算法：标准 4.0 / 北大 4.0 / 加权均分（不折算绩点） */
export type GpaAlgo = "std4" | "pku" | "wavg";

/** 归一化后的课程行（新结构，数值已转为 number） */
export interface GpaRow {
  name: string;
  /** 学分，无效输入按 0 计 */
  credit: number;
  /** 成绩；null 表示未填写，不计入统计 */
  score: number | null;
}

/**
 * 旧版存储行。
 * ⚠️ 字段是缩写且全部为字符串 —— 旧版直接存 input.value，没转数字。
 * 迁移时按字符串解析，不要假设是 number。
 */
export interface LegacyGpaRow {
  n?: string;
  c?: string;
  s?: string;
}

/** 工具标识 */
export type ToolId = "gpa" | "pomodoro" | "schedule" | "words" | "deadline" | "draw";

/**
 * 课表课程（新结构）。
 *
 * `day` 是 0 基列号（周一 = 0，周日 = 6）。旧版 v1 存的是 `dayOfWeek % 7`
 * （周一 = 1、周日 = 0），读旧数据时必须回拨，见 logic/schedule.ts 的 normalizeLegacyDay。
 */
export interface Course {
  id: number;
  name: string;
  /** 课块颜色，十六进制；导入时按 id 轮转分配 */
  color: string;
  day: number;
  /** 节次，逐节列出并升序，如 [1, 2] */
  sections: number[];
  teacher: string;
  classroom: string;
  /** 上课周次；null 表示每周都上 */
  weeks: number[] | null;
  notes: string;
}

/** 学期设置：第 1 周周一的日期 + 总周数 */
export interface TermSetting {
  /** YYYY-MM-DD，第 1 周周一 */
  start: string;
  weeks: number;
}

/** 课表存储快照 */
export interface ScheduleSnapshot {
  courses: Course[];
  /** 课程 id 自增游标，避免删课后 id 复用 */
  seq: number;
  /** 首访塞的示例课；真导入或添加后置 false */
  sample: boolean;
  term: TermSetting | null;
  /** 上次看的周次 */
  week: number;
}

/** 词库级别（来自 data/words/index.json） */
export interface WordLevel {
  id: string;
  name: string;
  count: number;
  groups?: number;
}

/**
 * 词条元组，顺序固定：`[单词, 释义, 例句?, 译文?]`。
 * 用元组而不是对象，是因为词库 JSON 本身就是这个形状，转一层没有收益。
 */
export type WordItem = [string, string, string?, string?];

/** 词卡练习进度 */
export interface WordsProgress {
  /** 上次练的级别 */
  lvl: string;
  /** 每个级别上次练到第几组 */
  last: Record<string, number>;
  /** 每个级别已练完的组号 */
  done: Record<string, number[]>;
}

/** 倒计时事项 */
export interface DeadlineItem {
  name: string;
  /** 本地零点的毫秒时间戳，避免时区把「还剩几天」算偏 */
  date: number;
}

/** 番茄钟当日统计 */
export interface PomodoroDaily {
  /** YYYY-MM-DD，跨天自动归零 */
  date: string;
  count: number;
}

/** 工具元信息（导航与状态展示用） */
export interface ToolMeta {
  id: ToolId;
  /** 导航短标签，两个到四个字 */
  label: string;
  /** 一句话说明它是干什么的 */
  hint: string;
  /** 是否已迁移到新实现；false 表示当前仍由旧静态页承载 */
  migrated: boolean;
}
