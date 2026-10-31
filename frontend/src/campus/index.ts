/**
 * 校园工具集 · 对外导出
 *
 * 板块外只允许从这里 import，不要直接摸内部路径。
 * 内部目录结构可以调整，这里保持稳定。
 */

export { default as CampusToolsHub } from "./components/CampusToolsHub";
export { default as ToolNav, TOOL_LIST } from "./components/ToolNav";

export { CAMPUS_KEYS, LEGACY_KEYS, CAMPUS_PREFIX } from "./storage/keys";
export { readJson, writeJson, removeJson, readWithMigration, isCampusKey } from "./storage";

export { computeGpa, scoreToGpa, parseLegacyGpa, SAMPLE_ROWS } from "./logic/gpa";
export { shuffle, drawWinners, drawOutcome, parseNames } from "./logic/draw";
export {
  DAY_LABELS,
  PERIODS,
  COURSE_COLORS,
  normalizeLegacyDay,
  currentWeek,
  parseWeeks,
  parseSections,
  parseCourseWeeks,
  isActiveInWeek,
  courseInWeek,
  formatSections,
  formatWeeks,
  courseKey,
  layoutDay,
  parseScheduleImport,
  mergeCourses,
  migrateLegacySchedule,
} from "./logic/schedule";
export {
  GROUP_SIZE,
  groupCount,
  groupSlice,
  startSession,
  judge,
  emptyProgress,
  doneGroups,
  markGroupDone,
  rememberGroup,
  migrateLegacyProgress,
} from "./logic/words";
export {
  MIN_MINUTES,
  MAX_MINUTES,
  formatClock,
  clampMinutes,
  remainSeconds,
  progressOf,
  todayKey,
} from "./logic/pomodoro";
export {
  daysLeft,
  statusOf,
  statusText,
  sortByDate,
  parseDateInput,
  toDateInput,
  migrateLegacyDeadline,
  sampleDeadlines,
} from "./logic/deadline";

export type { DrawOutcome } from "./logic/draw";
export type { WordSession } from "./logic/words";
export type { DayBlock, ImportResult } from "./logic/schedule";
export type { DeadlineStatus } from "./logic/deadline";

export type {
  GpaAlgo,
  GpaRow,
  LegacyGpaRow,
  ToolId,
  ToolMeta,
  Course,
  TermSetting,
  ScheduleSnapshot,
  WordItem,
  WordLevel,
  WordsProgress,
  DeadlineItem,
  PomodoroDaily,
} from "./types";
