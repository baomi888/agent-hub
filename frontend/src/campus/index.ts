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
  MAX_HOURS,
  formatClock,
  clampMinutes,
  splitDuration,
  joinDuration,
  formatDurationLabel,
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
export {
  MAX_SESSIONS,
  MAX_PIE_SLICES,
  dayKey,
  startOfDay,
  startOfWeek,
  rangeOf,
  sessionsInRange,
  totalSeconds,
  summarize,
  drillDown,
  foldToTop,
  streakDays,
  makeSessionId,
  sortSessions,
  trimSessions,
  mergeSessions,
  pendingSync,
  formatDuration,
  formatPercent,
  averageSecondsPerDay,
  rangeLabel,
} from "./logic/focus";
export {
  EXPORT_KIND,
  EXPORT_VERSION,
  buildExport,
  exportFileName,
  parseExport,
  mergeImport,
} from "./logic/focusExport";

export type { FocusExport, ImportOutcome, ImportReason, MergeResult } from "./logic/focusExport";
export {
  DEFAULT_CATEGORY,
  CATEGORY_RULES,
  listCategories,
  normalizeCategory,
  guessCategory,
  resolveCategory,
} from "./logic/focusCategory";
export {
  MIN_RECORD_SECONDS,
  createTimer,
  isPaused,
  elapsedOf,
  displaySeconds,
  ratioOf,
  isDue,
  pauseTimer,
  resumeTimer,
  buildSession,
  shouldRecord,
  humanizeSeconds,
} from "./logic/timer";
export {
  MAX_TASK_NAME,
  FOCUS_COLORS,
  makeTaskId,
  colorOf,
  makeTask,
  addTask,
  archiveTask,
  restoreTask,
  touchTask,
  setTaskCategory,
  renameTask,
  activeTasks,
  archivedTasks,
  findTask,
  taskUpdatedAt,
  mergeTasks,
} from "./logic/focusTask";
export {
  sanitizeTasks,
  sanitizeSessions,
  loadTasks,
  saveTasks,
  loadSessions,
  saveSessions,
} from "./logic/focusStore";
export { shouldCelebrate, daySlotOf, doneHeadline, doneTaskLabel, doneSummaryText } from "./logic/focusDone";
export { CHIME_PRESETS, findChime, DEFAULT_CHIME, clampVolume, isChimeId } from "./logic/chime";
export { DEFAULT_SOUND_PREFS, sanitizeSoundPrefs, loadSoundPrefs, saveSoundPrefs } from "./logic/soundPrefs";
export { primeAudio, playChime, playFinishSound, isAudioSupported } from "./logic/audio";
export {
  enterFullscreen,
  exitFullscreen,
  requestWakeLock,
  releaseWakeLock,
  tryLockLandscape,
  unlockOrientation,
  currentFullscreenElement,
  subscribeFullscreen,
  isFullscreenSnapshot,
  formatWallClock,
} from "./logic/fullscreen";

export type { WakeLockLike } from "./logic/fullscreen";
export { whoAmI, pushSessions, pushTasks, pullAll, syncAll } from "./logic/focusSync";
export type { SyncStatus, SyncSnapshot, SyncOutcome } from "./logic/focusSync";

export type { DrawOutcome } from "./logic/draw";
export type { WordSession } from "./logic/words";
export type { DayBlock, ImportResult } from "./logic/schedule";
export type { DeadlineStatus } from "./logic/deadline";
export type { CategoryRule } from "./logic/focusCategory";
export type { TimerState } from "./logic/timer";
export type { RenameOutcome, RenameReason } from "./logic/focusTask";
export type { ChimeNote, ChimePreset } from "./logic/chime";

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
  FocusDirection,
  FocusMode,
  FocusEndReason,
  FocusTask,
  FocusSession,
  ChimeId,
  SoundPrefs,
  StatsRange,
  CategoryStat,
  TaskStat,
  DayStat,
  PeriodSummary,
  DrilldownTask,
  CategoryDrilldown,
} from "./types";
