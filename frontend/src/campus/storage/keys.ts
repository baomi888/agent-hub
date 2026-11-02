/**
 * 校园工具集 · 存储键名
 *
 * 两套键名并存是刻意的：
 *  - CAMPUS_KEYS 是新的，点号分层、尾带版本；
 *  - LEGACY_KEYS 是旧静态页 tools.html 用的，下划线风格。
 * 新代码只写新键，读取时按「新键 → 旧键 → 迁移回写」的顺序兼容老用户数据。
 * 旧键一律不主动删除：用户可能回滚到旧版页面，删了等于清空他的课程和成绩。
 */

/** 板块存储前缀，调试时用它筛 localStorage */
export const CAMPUS_PREFIX = "campus.";

/** 新键：campus.<tool>.<purpose>.<version> */
export const CAMPUS_KEYS = {
  gpa: "campus.gpa.rows.v1",
  schedule: "campus.schedule.courses.v2",
  week: "campus.schedule.week.v1",
  term: "campus.schedule.term.v1",
  words: "campus.words.progress.v1",
  deadline: "campus.deadline.items.v1",
} as const;

/** 旧键：线上 tools.html 实际使用的键名（已核对源码） */
export const LEGACY_KEYS = {
  gpa: "baomi_tools_gpa_v1",
  schedule: "baomi_tools_sched_v1",
  week: "baomi_tools_week_v1",
  term: "baomi_tools_term_v1",
  words: "baomi_words_v2",
  deadline: "baomi_deadline_v1",
} as const;
