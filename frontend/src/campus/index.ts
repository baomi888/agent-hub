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
export { shuffle, drawWinners, parseNames } from "./logic/draw";
export {
  DAY_LABELS,
  normalizeLegacyDay,
  currentWeek,
  parseWeeks,
  isActiveInWeek,
  formatSections,
} from "./logic/schedule";

export type { GpaAlgo, GpaRow, LegacyGpaRow, ToolId, ToolMeta } from "./types";
