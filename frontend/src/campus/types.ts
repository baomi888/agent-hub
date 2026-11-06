/**
 * 校园工具集 · 共享类型
 *
 * 板块内所有类型收在这里，避免各工具各定义一套。
 * 与存储契约相关的字段说明见 campus/docs/02_数据与本地存储契约.md。
 */

import type { ReactNode } from "react";

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

/**
 * 一天的作息（B2 · 2026-11-06，需求 4.1 + A03）。
 *
 * 只存「每节几点开始」，不存结束时间 —— 结束时间由下一节推、最后一节按 `duration` 算。
 * 存两套时间一定会出现「第 1 节 08:00–09:40，第 2 节 09:30 开始」这种自相矛盾的存档，
 * 而用户真正要改的只有开始时间。
 */
export interface PeriodPlan {
  /** 每天几节（8–14）。这是「手机一屏看全」能否成立的关键变量 */
  count: number;
  /** 每节开始时间 `HH:MM`，长度恒等于 count */
  starts: string[];
  /** 每节课上多少分钟；算结束时间与「一键重排」用 */
  duration: number;
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
  /**
   * 作息。0.6.1 之前没有这个字段，读取时由 `normalizePeriodPlan()` 补默认值
   * （需求 A07：缺字段不能报错，也不能让整张表的行高突然变成 0）。
   */
  periods: PeriodPlan;
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

/** 番茄钟当日统计（0.2.0 遗留结构，保留仅为兼容旧数据） */
export interface PomodoroDaily {
  /** YYYY-MM-DD，跨天自动归零 */
  date: string;
  count: number;
}

/* ==================== 番茄钟 · 任务与会话 ==================== */

/** 计时方向：倒计时（番茄）/ 正计时（码表） */
export type FocusDirection = "countdown" | "countup";

/**
 * 提示音色。
 *
 * 五种全部是 Web Audio 实时合成，不加载任何音频文件（省体积、无版权问题、离线可用）；
 * `none` 表示静音。
 *
 * 「自定义音频链接」在 0.3.3 去掉了：能用的只是 `.mp3` 这类文件直链，
 * 而使用者想连的是歌单页 —— 那是一个网页，`<audio>` 拿不到里面的音频。
 * 保留它等于让用户反复试一个注定失败的入口，所以整条路径下掉。
 * 旧数据里可能还存着 `custom`，读出来会当成无效值回落到默认音色。
 */
export type ChimeId = "bell" | "marimba" | "musicbox" | "wind" | "drop" | "none";

/** 提示音偏好，存 `campus.pomodoro.sound.v1` */
export interface SoundPrefs {
  chime: ChimeId;
  /** 音量 0–1 */
  volume: number;
}

/**
 * 专注模式。
 * `immersive` / `strict` 本轮暂不启用（需求 R4 已暂缓），字段先留在结构里，
 * 这样将来补模式功能不用改存储结构、不用迁移历史数据。
 */
export type FocusMode = "normal" | "immersive" | "strict";

/** 会话结束原因 */
export type FocusEndReason = "finished" | "early" | "abandoned" | "stopped";

/**
 * 长期任务。
 *
 * `category` 是统计数据的分组维度（饼图按它聚合，如「高数」归入「数学」）。
 * 任务名可以随便写，但大类要保持少而稳定，否则饼图会碎成几十片。
 */
export interface FocusTask {
  id: string;
  name: string;
  /** 大类；手动指定优先，未指定时由 logic/focusCategory 按关键词猜 */
  category: string;
  /** 课块同源色板里的十六进制色，饼图与任务列表共用 */
  color: string;
  /** 归档而不删除：删了会丢历史统计的归属 */
  archived: boolean;
  createdAt: number;
  lastUsedAt: number;
  /**
   * 最后修改时间（改名 / 归档 / 改大类都会顶它），云同步「后写胜」的依据。
   * 0.5.0 之前的数据没有这个字段，读取时用 `taskUpdatedAt()` 退回 `lastUsedAt`。
   */
  updatedAt?: number;
}

/**
 * 一次专注会话 —— 所有统计的唯一数据源。
 *
 * 刻意冗余存 `taskName` 与 `category`：任务改名、改大类、归档之后，
 * 历史记录仍按当时的名字和大类统计，不会「一改任务名，去年记录就换了标签」。
 */
export interface FocusSession {
  id: string;
  taskId: string;
  taskName: string;
  category: string;
  startedAt: number;
  endedAt: number;
  /** 有效秒数，已扣除暂停；不从 endedAt - startedAt 反推，暂停过就对不上 */
  seconds: number;
  /** 计划时长；正计时为 0 */
  plannedSeconds: number;
  direction: FocusDirection;
  mode: FocusMode;
  pausedCount: number;
  /** 切走次数，仅 immersive / strict 统计；其他模式恒为 0 */
  awayCount: number;
  awaySeconds: number;
  endReason: FocusEndReason;
  /** 已同步到服务端的时间；本地未同步为 undefined（B6 云同步用） */
  syncedAt?: number;
}

/* ==================== 番茄钟 · 统计 ==================== */

/** 统计时间维度 */
export type StatsRange = "day" | "week" | "month" | "year";

/** 按大类聚合的一行 */
export interface CategoryStat {
  category: string;
  seconds: number;
  /** 会话条数 */
  count: number;
  /** 占区间总时长的比例，0–1；总时长为 0 时为 0 */
  ratio: number;
}

/** 按任务聚合的一行（大类饼图的下钻内容） */
export interface TaskStat {
  taskId: string;
  taskName: string;
  category: string;
  seconds: number;
  count: number;
  ratio: number;
}

/** 某一天的合计，用于趋势与连续天数 */
export interface DayStat {
  /** YYYY-MM-DD（本地时区） */
  date: string;
  seconds: number;
  count: number;
}

/** 一个时间区间的完整统计结果 */
export interface PeriodSummary {
  /** 区间起点（含），本地零点 */
  from: number;
  /** 区间终点（不含） */
  to: number;
  totalSeconds: number;
  sessionCount: number;
  /** 已按秒数降序 */
  categories: CategoryStat[];
  /** 已按秒数降序 */
  tasks: TaskStat[];
  /** 按日期升序 */
  byDay: DayStat[];
}

/**
 * 下钻结果里的任务行。
 *
 * 单独一个类型而不是复用 TaskStat，是因为 `ratio` 的语义不同：
 * TaskStat.ratio 是「占区间总时长」，DrilldownTask.ratio 是「在该大类内部」。
 * 共用一个类型迟早会有人读错，宁可多定义八行。
 */
export interface DrilldownTask {
  taskId: string;
  taskName: string;
  seconds: number;
  count: number;
  /** 在该大类内部的占比，0–1 */
  ratio: number;
}

/** 点开一个大类之后的明细（饼图下钻） */
export interface CategoryDrilldown {
  category: string;
  /** 该大类合计秒数 */
  totalSeconds: number;
  /** 该大类占区间总时长的比例，0–1 */
  ratioOfAll: number;
  /** 该大类下的任务，已按秒数降序 */
  tasks: DrilldownTask[];
}

/** 工具元信息（导航与状态展示用） */
export interface ToolMeta {
  id: ToolId;
  /** 导航短标签，两个到四个字 */
  label: string;
  /** 一句话说明它是干什么的 */
  hint: string;
  /**
   * 导航图标。
   *
   * 沿用旧版 tools.html 的内联 SVG（17×17，描边 currentColor），
   * 不引图标库：六个图标占不到 2 KB，装一整个库反而多背几十 KB。
   */
  icon: ReactNode;
  /** 是否已迁移到新实现；false 表示当前仍由旧静态页承载 */
  migrated: boolean;
}
