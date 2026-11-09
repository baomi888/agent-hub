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

/**
 * 新键：campus.<tool>.<purpose>.<version>
 *
 * `pomodoro` 没有对应的旧键 —— 旧版番茄钟的「今日已完成」只放内存，刷新就归零。
 * 这次迁移顺手补上了持久化，属于新增能力，已在审核报告里单列。
 */
export const CAMPUS_KEYS = {
  gpa: "campus.gpa.rows.v1",
  schedule: "campus.schedule.courses.v2",
  week: "campus.schedule.week.v1",
  term: "campus.schedule.term.v1",
  words: "campus.words.progress.v1",
  /**
   * 单词熟记档案：翻看计数 + 复习本 + 已掌握。
   * 与 `words` 分开存是刻意的 —— 复习本是跨级别、跨天累计的东西，
   * 而 `words` 只记「上次练到第几组」，两者生命周期不同，混在一起改结构会互相牵制。
   */
  wordsReview: "campus.words.review.v1",
  /**
   * 单词卡新手引导是否已看过。
   * 只记「看过」这个事实，不记步数 —— 引导是一次性的，看过的用户不该再被它挡住。
   * 与进度、复习本分开存：清进度不代表要把教学再看一遍，反过来也一样。
   */
  wordsIntro: "campus.words.intro.v1",
  deadline: "campus.deadline.items.v1",
  /**
   * 番茄钟当日计数。0.3.0 起统计改由 focusSessions 聚合得出，
   * 这个键只作为「旧数据兜底」保留，不再写入新值。
   */
  pomodoro: "campus.pomodoro.daily.v1",
  /** 长期任务库 */
  focusTasks: "campus.pomodoro.tasks.v1",
  /** 专注会话流水（统计唯一数据源），保留最近 5000 条 */
  focusSessions: "campus.pomodoro.sessions.v1",
  /** 提示音偏好：音色、音量（0.3.3 起不再有自定义音频链接字段） */
  focusSound: "campus.pomodoro.sound.v1",
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
