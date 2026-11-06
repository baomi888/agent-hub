"use client";

/**
 * 课程表。
 *
 * 这是板块里逻辑最重的工具，迁移时守住三条：
 *  1. **day 的口径**：0 基列号（周一 = 0）。旧版 v1 存的是 `dayOfWeek % 7`，
 *     读旧数据必须整体回拨一天，否则整张表错位（旧版 10-29 修过的 bug，不能再犯）。
 *  2. **按周筛课**：带 weeks 的课只在该周出现；没带的当作每周都上。
 *     旧版曾经「存着 weeks 但画的时候全画上」，结果第 16 周才上的课挤进每一周。
 *  3. **同格分栏**：重叠的课并排，不叠在一起。
 *
 * 另有一处行为调整（已在审核报告注明）：打开课表默认停在**当前教学周**，
 * 旧版只有首次进入才这样，之后一律沿用上次翻到的周次。
 *
 * B1（2026-11-06，需求 2.1 / 2.2 / 3.1 / 5.1 + A01 / A02）改动：
 *  - 首访不再塞三门示例课，改为空状态引导 —— 否则新人以为那是自己的课表；
 *  - 「添加一门课」从**第二张常驻卡片**收成头部按钮 + 页内展开（番茄钟同款交互）；
 *  - 点课块从「直接删除」改成「打开详情」，详情里可编辑/删除 —— 改个教室不必删了重加；
 *  - 学期那行说明收进周次条右侧的小 chip，点开仍是学期弹窗；
 *  - 「清空课表」从主按钮降级为页脚文字按钮，避免误触。
 */

import { useEffect, useMemo, useState } from "react";
import {
  COVERED_YEARS,
  DATA_NOTE,
  dateKey,
  dayInfoOf,
  holidayLabelsOf,
  isYearCovered,
} from "../../logic/holidays";
import {
  COURSE_COLORS,
  courseInWeek,
  currentWeek,
  formatWeeks,
  mergeCourses,
  migrateLegacySchedule,
  normalizePeriodPlan,
  parseCourseWeeks,
  parseScheduleImport,
  parseSections,
  sectionTimeRange,
} from "../../logic/schedule";
import { readJson, writeJson } from "../../storage";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import type { Course, PeriodPlan, ScheduleSnapshot, TermSetting } from "../../types";
import { CourseDialog, CourseForm, EMPTY_DRAFT } from "./CourseEditor";
import type { CourseDraft } from "./CourseEditor";
import { ImportDialog, PeriodDialog, TermDialog } from "./ScheduleDialogs";
import type { ImportFeedback } from "./ScheduleDialogs";
import WeekGrid from "./WeekGrid";

/**
 * 页面内周视图的行高。
 * B2 从 44 提到 46：课名字号回到 13px 之后，44px 的格子会让文字贴着上下边线（需求 4.2/4.3）。
 */
const ROW_HEIGHT = 46;
const FULL_ROW_HEIGHT = 52;

const DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/* ------------------------------------------------------------------ *
 * 日期与初始化
 * ------------------------------------------------------------------ */

/** 把任意日期归一到那一周的周一 */
function mondayOf(date: Date): Date {
  const copy = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7));
  return copy;
}

/** 学期第 1 周的周一；日期非法返回 null */
function termMonday(start: string): Date | null {
  const date = new Date(`${start}T00:00:00`);
  if (Number.isNaN(date.getTime())) return null;
  return mondayOf(date);
}

/** 第 week 周第 day 天（day: 0 = 周一）的日期 */
function dateOfCell(monday: Date, week: number, day: number): Date {
  return new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + (week - 1) * 7 + day);
}

/**
 * `2026-08-24` → `8月24日`。
 *
 * B3.1 之前写的是 `8/24`：斜杠形式在中文语境里容易被读成「八月二十四」之外的东西
 * （分数、或者日/月），而且和后面「起」连在一起时更像一个编号。带上「月」「日」两个字
 * 才是一句能独立读懂的中文。
 */
function monthDay(start: string): string {
  const [, month, day] = start.split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : start;
}

/** 一天 → `10月5日` */
function mdText(date: Date): string {
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

/**
 * 一周 → `10月5日 – 10月11日`。
 * 后半段也写月份：省掉之后是「10月5日 – 11日」，前半段有月份、后半段没有，
 * 扫一眼容易把它读成「5 日到 11 日」这一段里的某个东西。多四个字换零歧义。
 */
function weekRangeText(dates: readonly Date[]): string {
  if (dates.length === 0) return "";
  return `${mdText(dates[0])} – ${mdText(dates[dates.length - 1])}`;
}

/** 课程里出现的最晚周次；没有带周次的课则为 1 */
function maxWeekOf(courses: readonly Course[]): number {
  let max = 0;
  for (const course of courses) {
    for (const week of course.weeks ?? []) {
      if (week > max) max = week;
    }
  }
  return max || 1;
}

/**
 * 打开时该停在第几周。
 * 有学期就按日历算（这是用户打开课表最想看的），没学期才沿用存下来的周次。
 */
function initialWeek(term: TermSetting | null, stored: number): number {
  const fallback = Math.max(1, stored || 1);
  if (!term) return fallback;

  const monday = termMonday(term.start);
  if (!monday) return fallback;

  const now = currentWeek(monday, new Date());
  if (now <= 0) return fallback;
  return Math.min(Math.max(now, 1), term.weeks);
}

const EMPTY_SNAPSHOT: ScheduleSnapshot = {
  courses: [],
  seq: 0,
  sample: false,
  term: null,
  week: 1,
  periods: normalizePeriodPlan(undefined),
};

/**
 * 一组日期 → `10月5–7日` / `9月30日–10月2日` / `10月1日、10月3日`。
 * 只服务节假日那一行提示：跨节的假期在一周里往往是连续几天，
 * 全列出来太啰嗦，连号收成区间、断号照实列。
 *
 * B3.1：参数从「日号」改成完整 Date。只传日号时，跨月的假期（比如 9/30–10/2）
 * 会排成「30、1、2 号」—— 连续判断也失效（30 → 1 不算连号），
 * 读的人既不知道 1 号是几月，也看不出这是连着放的三天。
 */
function dayRangeText(dates: readonly Date[]): string {
  if (dates.length === 0) return "";

  const list = dates.slice().sort((a, b) => a.getTime() - b.getTime());
  const continuous = list.every((date, i) => {
    if (i === 0) return true;
    const prev = list[i - 1];
    // 用「前一天 + 1 天」比，跨月、跨年都成立；直接比日号会在 31 号断掉
    const next = new Date(prev.getFullYear(), prev.getMonth(), prev.getDate() + 1);
    return (
      next.getFullYear() === date.getFullYear() &&
      next.getMonth() === date.getMonth() &&
      next.getDate() === date.getDate()
    );
  });

  if (!continuous) return list.map(mdText).join("、");

  const first = list[0];
  const last = list[list.length - 1];
  if (list.length === 1) return mdText(first);
  return first.getMonth() === last.getMonth()
    ? `${first.getMonth() + 1}月${first.getDate()}–${last.getDate()}日`
    : `${mdText(first)}–${mdText(last)}`;
}

/* ------------------------------------------------------------------ *
 * 表单 → 课程
 * ------------------------------------------------------------------ */

/**
 * 校验并组装一门课；`base` 有值时是编辑，保留 id / 颜色 / 备注。
 *
 * `maxSection` 是当前「每天几节」。超出的节次**必须报错**，不能静默存下来 ——
 * 周视图只画 `count` 行，第 13 节不会显示在任何地方，用户会以为课加丢了。
 */
function buildCourse(
  draft: CourseDraft,
  maxSection: number,
  base?: Course,
): { course: Course } | { error: string } {
  const name = draft.name.trim();
  if (name === "") return { error: "请填写课程名。" };

  const sections = parseSections(draft.sections);
  if (sections.length === 0) return { error: "请填写节次，如 1,2 或 1-3。" };

  const overflow = sections.find((n) => n > maxSection);
  if (overflow !== undefined) {
    return {
      error: `每天只排了 ${maxSection} 节，第 ${overflow} 节放不下 —— 先去「上课时间」把节数加上去。`,
    };
  }

  const day = Math.min(6, Math.max(0, (Number.parseInt(draft.day, 10) || 1) - 1));

  return {
    course: {
      id: base?.id ?? 0,
      color: base?.color ?? "",
      name,
      day,
      sections,
      teacher: draft.teacher.trim(),
      classroom: draft.room.trim(),
      weeks: parseCourseWeeks(draft.weeks.trim()),
      notes: base?.notes ?? "",
    },
  };
}

/* ------------------------------------------------------------------ *
 * 组件
 * ------------------------------------------------------------------ */

export default function ScheduleTool() {
  const [snapshot, setSnapshot] = useState<ScheduleSnapshot>(EMPTY_SNAPSHOT);
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState<"week" | "all">("week");
  const [fullscreen, setFullscreen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [termOpen, setTermOpen] = useState(false);
  const [periodOpen, setPeriodOpen] = useState(false);
  const [hint, setHint] = useState("");

  // 添加 / 编辑
  const [addOpen, setAddOpen] = useState(false);
  const [addMessage, setAddMessage] = useState("");
  /** 加成功一门就把表单换掉（改 key），比手动清 6 个字段可靠 */
  const [formSeq, setFormSeq] = useState(0);
  const [activeId, setActiveId] = useState<number | null>(null);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const fresh = readJson<ScheduleSnapshot>(CAMPUS_KEYS.schedule);
    if (fresh && Array.isArray(fresh.courses)) {
      // 0.6.1 之前的存档没有 periods，缺了就补默认作息（需求 A07），不能让旧用户开页就崩
      setSnapshot({
        ...fresh,
        periods: normalizePeriodPlan(fresh.periods),
        week: initialWeek(fresh.term, fresh.week),
      });
      setReady(true);
      return;
    }

    const legacyRaw = readJson(LEGACY_KEYS.schedule);
    const migrated = legacyRaw === null ? null : migrateLegacySchedule(legacyRaw);

    if (migrated) {
      // 旧版把学期和「看到第几周」存在**另外两个键**里，migrateLegacySchedule 拿不到，这里补齐
      const legacyTerm = readJson<{ start?: string; weeks?: number }>(LEGACY_KEYS.term);
      if (!migrated.term && legacyTerm?.start && /^\d{4}-\d{2}-\d{2}$/.test(legacyTerm.start)) {
        migrated.term = {
          start: legacyTerm.start,
          weeks: Math.min(30, Math.max(1, Number(legacyTerm.weeks) || 20)),
        };
      }

      const legacyWeek = readJson<{ cur?: number }>(LEGACY_KEYS.week);
      if (legacyWeek && Number(legacyWeek.cur) > 0) {
        migrated.week = Math.trunc(Number(legacyWeek.cur));
      }

      setSnapshot({ ...migrated, week: initialWeek(migrated.term, migrated.week) });
      setReady(true);
      return;
    }

    // 全新用户：空表 + 空状态引导。旧版塞三门示例课，新人会以为那是自己的课表
    setSnapshot(EMPTY_SNAPSHOT);
    setReady(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // 变更即存。ready 为 false 时跳过，别用空课表覆盖用户的存档
  useEffect(() => {
    if (!ready) return;
    writeJson(CAMPUS_KEYS.schedule, snapshot);
  }, [snapshot, ready]);

  const term = snapshot.term;
  const plan = snapshot.periods;
  const monday = term ? termMonday(term.start) : null;
  const todayWeek = monday ? currentWeek(monday, new Date()) : 0;

  /** 能翻到第几周：有学期按学期总周数，否则看课程最晚周次 */
  const totalWeeks = term ? term.weeks : Math.max(maxWeekOf(snapshot.courses), 1);
  const week = Math.min(Math.max(snapshot.week, 1), totalWeeks);

  const visibleCourses = useMemo(
    () => snapshot.courses.filter((course) => courseInWeek(course, week)),
    [snapshot.courses, week],
  );

  const weekDates = monday
    ? Array.from({ length: 7 }, (_, day) => dateOfCell(monday, week, day))
    : undefined;

  /*
   * 日历联动（B3）。没设学期就没有日期，四条全部静默跳过（需求 D2 的降级）：
   * 与其猜一个日期，不如什么都不标 —— 标错一天比不标更让人不敢信这张表。
   */
  const dayInfos = weekDates ? weekDates.map((date) => dayInfoOf(date)) : undefined;
  const holidayNames = weekDates ? holidayLabelsOf(weekDates) : [];

  /** 看的这周落在常量表覆盖范围之外：不能默默当「这周没假期」，要如实说明 */
  const uncoveredYear =
    weekDates && !isYearCovered(weekDates[0].getFullYear())
      ? weekDates[0].getFullYear()
      : null;

  /** 今天那一列只在「正在看本周」时高亮，翻到别周还标红会让人误会 */
  const todayIndex = todayWeek === week ? (new Date().getDay() + 6) % 7 : -1;

  /** 从快照里取，编辑后自动是最新值，不会出现「弹窗里还是旧课名」 */
  const active = activeId === null ? null : (snapshot.courses.find((c) => c.id === activeId) ?? null);
  const hasCourses = snapshot.courses.length > 0;
  const noClassThisWeek = hasCourses && visibleCourses.length === 0;

  /**
   * 表格上方那行小字（需求 4.7 + B3.1）。
   *
   * 左边永远写「这一周是哪几天」—— 列头只放日号（24、25、26），
   * 光看日号分不清八月还是九月，跨月那周更是「29 30 1 2」。
   * 右边才是有假期时的说明：是什么假、哪几天。
   *
   * 「哪几天」必须写清：国庆跨两周，第 6 周只有周四到周日放假、周一到周三照常上课，
   * 只说「本周国庆节放假」会让人以为整周都不用去。
   */
  const offDates = weekDates
    ? weekDates.filter((_, i) => dayInfos?.[i]?.kind === "off")
    : [];

  const holidayNote = !weekDates
    ? ""
    : holidayNames.length > 0
      ? // 「放假」两个字已经说完「不用上课」，再补一句在手机上会把这一行挤到换行
        `${holidayNames.join("、")} · ${dayRangeText(offDates)}放假`
      : uncoveredYear !== null
        ? `${uncoveredYear} 年的放假安排还没收录（数据到 ${COVERED_YEARS[COVERED_YEARS.length - 1]} 年），这一周不标记`
        : "";

  function setWeek(next: number) {
    setSnapshot((prev) => ({ ...prev, week: Math.min(Math.max(next, 1), totalWeeks) }));
  }

  function addFromForm(draft: CourseDraft) {
    const built = buildCourse(draft, plan.count);
    if ("error" in built) {
      setAddMessage(built.error);
      return;
    }

    const nextId = snapshot.seq + 1;
    const course: Course = {
      ...built.course,
      id: nextId,
      color: COURSE_COLORS[(nextId - 1) % COURSE_COLORS.length],
    };

    setSnapshot((prev) => ({ ...prev, courses: [...prev.courses, course], seq: nextId, sample: false }));
    setAddMessage("");
    setFormSeq((n) => n + 1);
    setHint(`已添加《${course.name}》。`);
  }

  function saveEdited(draft: CourseDraft): string | null {
    if (!active) return "这门课已经不在了。";

    const built = buildCourse(draft, plan.count, active);
    if ("error" in built) return built.error;

    setSnapshot((prev) => ({
      ...prev,
      courses: prev.courses.map((item) => (item.id === active.id ? built.course : item)),
    }));
    setActiveId(null);
    setHint(`已保存《${built.course.name}》的修改。`);
    return null;
  }

  function removeCourse(course: Course) {
    const suffix = course.weeks && course.weeks.length > 0 ? `（${formatWeeks(course.weeks)}）` : "";
    if (!window.confirm(`删除《${course.name}》${suffix}？`)) return;

    setSnapshot((prev) => ({
      ...prev,
      courses: prev.courses.filter((item) => item.id !== course.id),
    }));
    setActiveId(null);
    setHint(`已删除《${course.name}》。`);
  }

  function clearAll() {
    if (!window.confirm("清空整张课表？这台设备上存的也会一起删掉。")) return;
    setSnapshot((prev) => ({ ...prev, courses: [], seq: 0, sample: false }));
    setHint("课表已清空。");
  }

  function handleImport(text: string): ImportFeedback {
    const parsed = parseScheduleImport(text);
    if (parsed.error) return { ok: false, message: parsed.error };
    if (parsed.courses.length === 0) {
      return { ok: false, message: "没解析出可用的课程，检查每门课是否都写了节次。" };
    }

    const merged = mergeCourses(snapshot.courses, parsed.courses, snapshot.seq);
    setSnapshot((prev) => ({
      ...prev,
      courses: merged.courses,
      seq: merged.nextId,
      sample: false,
    }));

    const parts = [`导入 ${merged.added} 门课`];
    if (merged.duplicated > 0) parts.push(`跳过重复 ${merged.duplicated} 门`);
    if (parsed.skipped > 0) parts.push(`丢弃无效 ${parsed.skipped} 条`);

    // 导入不拦节次超限（拦了等于整门课丢掉），但要如实说：这些课现在看不见
    const tooLate = parsed.courses.filter((course) =>
      course.sections.some((n) => n > plan.count),
    ).length;
    if (tooLate > 0) {
      parts.push(`其中 ${tooLate} 门有第 ${plan.count + 1} 节以后的课，去「上课时间」加节数才看得见`);
    }

    setHint(`${parts.join("，")}。`);

    // 导入成功就顺势问一次学期：不设学期，「今天」「节假日」都算不出来（需求 D2）
    if (!snapshot.term) {
      setTermOpen(true);
      return { ok: true, message: `${parts.join("，")}。接下来设一下学期，就能标出今天和节假日。` };
    }

    return { ok: true, message: `${parts.join("，")}。` };
  }

  function handleExport(): string {
    const payload = {
      courses: snapshot.courses.map((course) => ({
        name: course.name,
        dayOfWeek: course.day + 1,
        sections: course.sections,
        teacher: course.teacher || undefined,
        classroom: course.classroom || undefined,
        weeks: course.weeks ?? undefined,
        notes: course.notes || undefined,
      })),
    };
    return JSON.stringify(payload, null, 2);
  }

  function saveTerm(payload: { start: string; nowWeek: number | null; weeks: number }) {
    let start = payload.start;

    // 用户选了「直接告诉我现在是第几周」：从这周一往回推
    if (start === "" && payload.nowWeek !== null) {
      const thisMonday = mondayOf(new Date());
      const first = new Date(
        thisMonday.getFullYear(),
        thisMonday.getMonth(),
        thisMonday.getDate() - (payload.nowWeek - 1) * 7,
      );
      start = dateKey(first);
    }

    const nextTerm: TermSetting = { start, weeks: payload.weeks };
    const firstMonday = termMonday(start);
    const nextWeek = firstMonday ? currentWeek(firstMonday, new Date()) : 1;

    setSnapshot((prev) => ({
      ...prev,
      term: nextTerm,
      week: Math.min(Math.max(nextWeek, 1), payload.weeks),
    }));
    setTermOpen(false);
    setHint(`学期已设置：第 1 周从 ${start} 开始，共 ${payload.weeks} 周。`);
  }

  function savePeriod(next: PeriodPlan) {
    setSnapshot((prev) => ({ ...prev, periods: next }));
    setPeriodOpen(false);
    // 节数变少时，超出范围的课不是删掉，而是提醒一句 —— 用户往往是试一下，加回去就回来了
    const hidden = snapshot.courses.filter((course) =>
      course.sections.some((n) => n > next.count),
    ).length;
    setHint(
      hidden > 0
        ? `上课时间已更新。有 ${hidden} 门课排在第 ${next.count} 节以后，现在看不见 —— 节数加回去就回来了。`
        : `上课时间已更新：每天 ${next.count} 节，第一节 ${next.starts[0]} 开始。`,
    );
  }

  function goToday() {
    if (todayWeek > 0) {
      setWeek(todayWeek);
      return;
    }
    // 还没设学期，先请用户填一次 —— 旧版也是这个交互
    setTermOpen(true);
  }

  if (!ready) {
    return (
      <div id="campus-panel-schedule" role="tabpanel" aria-label="课程表">
        <div className="campus-card">
          <p className="campus-empty">正在读取这台设备上的课表…</p>
        </div>
      </div>
    );
  }

  return (
    <div id="campus-panel-schedule" role="tabpanel" aria-label="课程表">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">我的课表</h2>
          <div className="campus-head-actions">
            <button
              type="button"
              className={`campus-btn${addOpen ? " is-on" : ""}`}
              aria-expanded={addOpen}
              onClick={() => {
                setAddOpen((open) => !open);
                setAddMessage("");
              }}
            >
              {addOpen ? "收起" : "添加课程"}
            </button>
            <button
              type="button"
              className="campus-btn is-primary"
              onClick={() => setImportOpen(true)}
            >
              导入课表
            </button>
          </div>
        </div>

        {/* 添加课程：页内展开，不弹出、不推挤下面的课表 */}
        {addOpen && (
          <div className="campus-add-panel">
            <CourseForm
              key={formSeq}
              initial={EMPTY_DRAFT}
              submitLabel="添加"
              message={addMessage}
              maxSection={plan.count}
              onSubmit={addFromForm}
              onCancel={() => {
                setAddOpen(false);
                setAddMessage("");
              }}
            />
          </div>
        )}

        {hasCourses ? (
          <>
            {/* 周次条：翻周、选周、回本周，右侧一个学期 chip 替代原来的整行说明 */}
            <div className="campus-wkbar">
              <button
                type="button"
                className="campus-wk-btn"
                aria-label="上一周"
                disabled={week <= 1}
                onClick={() => setWeek(week - 1)}
              >
                ‹
              </button>

              <select
                className="campus-select"
                aria-label="选择周次"
                value={String(week)}
                style={{ maxWidth: 160 }}
                onChange={(event) => setWeek(Number.parseInt(event.target.value, 10))}
              >
                {Array.from({ length: totalWeeks }, (_, i) => i + 1).map((w) => {
                  const count = snapshot.courses.filter((course) => courseInWeek(course, w)).length;
                  return (
                    <option key={w} value={w}>
                      第 {w} 周{w === todayWeek ? " · 本周" : count === 0 ? " · 没课" : ` · ${count} 门`}
                    </option>
                  );
                })}
              </select>

              <button
                type="button"
                className="campus-wk-btn"
                aria-label="下一周"
                disabled={week >= totalWeeks}
                onClick={() => setWeek(week + 1)}
              >
                ›
              </button>

              <button type="button" className="campus-btn" onClick={goToday}>
                本周
              </button>

              <div className="campus-seg" role="group" aria-label="课表视图">
                <button
                  type="button"
                  className={`campus-seg-btn${mode === "week" ? " is-on" : ""}`}
                  aria-pressed={mode === "week"}
                  onClick={() => setMode("week")}
                >
                  按周
                </button>
                <button
                  type="button"
                  className={`campus-seg-btn${mode === "all" ? " is-on" : ""}`}
                  aria-pressed={mode === "all"}
                  onClick={() => setMode("all")}
                >
                  一览
                </button>
                <button
                  type="button"
                  className="campus-seg-btn"
                  onClick={() => setFullscreen(true)}
                >
                  全屏
                </button>
              </div>

              {/* 两颗 chip 常驻右端：学期、作息。原来学期说明独占一行，作息干脆没入口 */}
              <div className="campus-wk-chips">
                <button
                  type="button"
                  className="campus-term-chip"
                  onClick={() => setTermOpen(true)}
                  title="设置开学日期与总周数"
                >
                  {term ? `第 1 周 ${monthDay(term.start)}起 · 共 ${term.weeks} 周` : "还没设学期"}
                </button>

                <button
                  type="button"
                  className="campus-term-chip is-plain"
                  onClick={() => setPeriodOpen(true)}
                  title="设置每天几节、每节几点开始"
                >
                  每天 {plan.count} 节 · {plan.starts[0]} 起
                </button>
              </div>
            </div>

            {mode === "week" ? (
              <>
                {/* 没设学期就什么都不标（D2 降级），但要说明原因，否则用户以为是没做 */}
                {!term && (
                  <p className="campus-week-holiday is-muted">
                    还没设学期，所以不标今天和节假日 ——
                    <button type="button" className="campus-link-btn" onClick={() => setTermOpen(true)}>
                      设一下
                    </button>
                  </p>
                )}

                {/*
                 * 周区间 + 假期提示同一行（B3.1）。
                 * 左边这串日期是必需的：列头为了塞下 7 列只写日号（5、6、7），
                 * 看不出是几月；跨月那周更是「29 30 1 2」，靠列头根本读不出来。
                 */}
                {term && weekDates && (
                  <div className="campus-week-sub">
                    <span className="campus-week-range">{weekRangeText(weekDates)}</span>
                    {holidayNote !== "" && (
                      <span className="campus-week-holiday" title={DATA_NOTE}>
                        {holidayNote}
                      </span>
                    )}
                  </div>
                )}

                <div className="campus-week-wrap">
                  <WeekGrid
                    courses={visibleCourses}
                    rowHeight={ROW_HEIGHT}
                    plan={plan}
                    weekDates={weekDates}
                    dayInfos={dayInfos}
                    todayIndex={todayIndex}
                    onOpen={(course) => setActiveId(course.id)}
                  />
                </div>
              </>
            ) : (
              <div className="campus-ov-wrap">
                <table className="campus-ov">
                  <thead>
                    <tr>
                      <th scope="col">周</th>
                      {DAY_NAMES.map((name) => (
                        <th key={name} scope="col">
                          {name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from({ length: totalWeeks }, (_, i) => i + 1).map((w) => (
                      <tr key={w} className={w === todayWeek ? "is-now" : undefined}>
                        <th scope="row">
                          <button
                            type="button"
                            className="campus-ov-week"
                            onClick={() => {
                              setWeek(w);
                              setMode("week");
                            }}
                          >
                            {w}
                          </button>
                        </th>
                        {Array.from({ length: 7 }, (_, day) => {
                          const names = snapshot.courses
                            .filter((course) => course.day === day && courseInWeek(course, w))
                            .map((course) => course.name);
                          return (
                            <td key={day} title={names.join("、")}>
                              {names.join("、")}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {noClassThisWeek && (
              <p className="campus-card-note" style={{ marginTop: 10 }}>
                第 {week} 周没有排课
                {term && week !== todayWeek ? "，点「本周」跳回当前周。" : "。"}
              </p>
            )}
          </>
        ) : (
          <div className="campus-empty-state">
            <p className="campus-empty-title">还没有课表</p>
            <p className="campus-card-note">
              把整理好的 JSON 粘进来最快；也可以一门一门手动加，加完随时能导出备份。
            </p>
            <div className="campus-add-row" style={{ marginTop: 12 }}>
              <button
                type="button"
                className="campus-btn is-primary"
                onClick={() => setImportOpen(true)}
              >
                导入课表
              </button>
              {/* 表单已经展开时不再给「手动添加」——点了也是同一个表单，等于废话 */}
              {!addOpen && (
                <button type="button" className="campus-btn" onClick={() => setAddOpen(true)}>
                  手动添加
                </button>
              )}
            </div>
            <p className="campus-card-note" style={{ marginTop: 10 }}>
              不知道 JSON 从哪来？点「导入课表」，里面有现成的提示词，复制给知穗就行。
            </p>
          </div>
        )}

        {hint !== "" && (
          <p className="campus-card-note campus-hint" role="status">
            {hint}
          </p>
        )}

        {hasCourses && (
          <div className="campus-card-foot">
            <span>共 {snapshot.courses.length} 门课，存在这台设备上</span>
            <button type="button" className="campus-link-btn" onClick={clearAll}>
              清空课表
            </button>
          </div>
        )}
      </div>

      {/* 全屏：同一套 WeekGrid，行高放大、多显示上课时间 */}
      {fullscreen && (
        <div className="campus-fullscreen" role="dialog" aria-modal="true" aria-label="全屏课表">
          <div className="campus-fullscreen-bar">
            <div className="campus-fullscreen-title">
              第 {week} 周
              {/* 全屏顶栏够宽，直接给完整区间；「5 日起」看不出这周到几号 */}
              {weekDates ? ` · ${weekRangeText(weekDates)}` : ""}
              {/* 4.7 的口径：全屏时节日名挪到标题旁，右上角要留给「退出全屏」 */}
              {holidayNames.length > 0 && (
                <span className="campus-fullscreen-holiday">
                  {holidayNames.join("、")} · {dayRangeText(offDates)}放假
                </span>
              )}
            </div>
            <div className="campus-add-row" style={{ margin: 0 }}>
              <button
                type="button"
                className="campus-wk-btn"
                aria-label="上一周"
                disabled={week <= 1}
                onClick={() => setWeek(week - 1)}
              >
                ‹
              </button>
              <button type="button" className="campus-btn" onClick={goToday}>
                本周
              </button>
              <button
                type="button"
                className="campus-wk-btn"
                aria-label="下一周"
                disabled={week >= totalWeeks}
                onClick={() => setWeek(week + 1)}
              >
                ›
              </button>
              <button type="button" className="campus-btn" onClick={() => setFullscreen(false)}>
                退出全屏
              </button>
            </div>
          </div>

          <div className="campus-fullscreen-body">
            <WeekGrid
              courses={visibleCourses}
              rowHeight={FULL_ROW_HEIGHT}
              plan={plan}
              weekDates={weekDates}
              dayInfos={dayInfos}
              todayIndex={todayIndex}
              onOpen={(course) => setActiveId(course.id)}
            />
          </div>
        </div>
      )}

      {importOpen && (
        <ImportDialog
          onClose={() => setImportOpen(false)}
          onImport={handleImport}
          onExport={handleExport}
        />
      )}

      {termOpen && (
        <TermDialog
          defaultStart={term?.start ?? ""}
          defaultWeeks={term?.weeks ?? 20}
          currentWeekNo={todayWeek}
          onClose={() => setTermOpen(false)}
          onSave={saveTerm}
        />
      )}

      {periodOpen && (
        <PeriodDialog plan={plan} onClose={() => setPeriodOpen(false)} onSave={savePeriod} />
      )}

      {active && (
        <CourseDialog
          key={active.id}
          course={active}
          weekText={active.weeks && active.weeks.length > 0 ? formatWeeks(active.weeks) : "每周都上"}
          timeText={sectionTimeRange(active.sections, plan)}
          maxSection={plan.count}
          onSave={saveEdited}
          onDelete={() => removeCourse(active)}
          onClose={() => setActiveId(null)}
        />
      )}
    </div>
  );
}
