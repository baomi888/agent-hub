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
