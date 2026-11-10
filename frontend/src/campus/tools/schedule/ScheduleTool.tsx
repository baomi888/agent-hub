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
 */

import { useEffect, useMemo, useState } from "react";
import {
  COURSE_COLORS,
  courseInWeek,
  currentWeek,
  formatWeeks,
  mergeCourses,
  migrateLegacySchedule,
  parseCourseWeeks,
  parseScheduleImport,
  parseSections,
} from "../../logic/schedule";
import { readJson, writeJson } from "../../storage";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import type { Course, ScheduleSnapshot, TermSetting } from "../../types";
import { ImportDialog, TermDialog } from "./ScheduleDialogs";
import type { ImportFeedback } from "./ScheduleDialogs";
import WeekGrid from "./WeekGrid";

/** 页面内周视图的行高。全屏用更大的值，让一屏尽量摊开 */
const ROW_HEIGHT = 44;
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

/** 本地日期 → YYYY-MM-DD */
function toDateString(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${m}-${d}`;
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

/** 首访的三门示例课，与旧版完全一致 */
const SAMPLE_COURSES: ReadonlyArray<Omit<Course, "id" | "color">> = [
  { name: "高等数学", day: 0, sections: [1, 2], teacher: "张教授", classroom: "教三-201", weeks: null, notes: "" },
  { name: "大学英语", day: 2, sections: [3, 4], teacher: "", classroom: "外语楼-305", weeks: null, notes: "" },
  { name: "数据结构", day: 4, sections: [5, 6], teacher: "", classroom: "机房-A", weeks: null, notes: "" },
];

function seedCourses(): { courses: Course[]; seq: number } {
  let seq = 0;
  const courses = SAMPLE_COURSES.map((item) => {
    seq += 1;
    return { ...item, id: seq, color: COURSE_COLORS[(seq - 1) % COURSE_COLORS.length] };
  });
  return { courses, seq };
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
};

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
  const [hint, setHint] = useState("");

  // 手动添加表单
  const [draft, setDraft] = useState({ name: "", day: "1", sections: "", room: "", teacher: "", weeks: "" });

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const fresh = readJson<ScheduleSnapshot>(CAMPUS_KEYS.schedule);
    if (fresh && Array.isArray(fresh.courses)) {
      setSnapshot({ ...fresh, week: initialWeek(fresh.term, fresh.week) });
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

    // 全新用户：铺三门示例课，和旧版首访一致
    const seed = seedCourses();
    setSnapshot({ courses: seed.courses, seq: seed.seq, sample: true, term: null, week: 1 });
    setReady(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // 变更即存。ready 为 false 时跳过，别用空课表覆盖用户的存档
  useEffect(() => {
    if (!ready) return;
    writeJson(CAMPUS_KEYS.schedule, snapshot);
  }, [snapshot, ready]);

  const term = snapshot.term;
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

  /** 今天那一列只在「正在看本周」时高亮，翻到别周还标红会让人误会 */
  const todayIndex = todayWeek === week ? (new Date().getDay() + 6) % 7 : -1;

  function setWeek(next: number) {
    setSnapshot((prev) => ({ ...prev, week: Math.min(Math.max(next, 1), totalWeeks) }));
  }

  function removeCourse(course: Course) {
    const suffix = course.weeks && course.weeks.length > 0 ? `（${formatWeeks(course.weeks)}）` : "";
    if (!window.confirm(`删除《${course.name}》${suffix}？`)) return;

    setSnapshot((prev) => ({
      ...prev,
      courses: prev.courses.filter((item) => item.id !== course.id),
    }));
  }

  function addCourse() {
    const name = draft.name.trim();
    const sections = parseSections(draft.sections);

    if (name === "") {
      setHint("请填写课程名。");
      return;
    }
    if (sections.length === 0) {
      setHint("请填写节次，如 1,2 或 1-3。");
      return;
    }

    const day = Math.min(6, Math.max(0, (Number.parseInt(draft.day, 10) || 1) - 1));
    const nextId = snapshot.seq + 1;

    const course: Course = {
      id: nextId,
      name,
      color: COURSE_COLORS[(nextId - 1) % COURSE_COLORS.length],
      day,
      sections,
      teacher: draft.teacher.trim(),
      classroom: draft.room.trim(),
      weeks: parseCourseWeeks(draft.weeks.trim()),
      notes: "",
    };

    setSnapshot((prev) => ({ ...prev, courses: [...prev.courses, course], seq: nextId, sample: false }));
    setDraft({ name: "", day: draft.day, sections: "", room: "", teacher: "", weeks: "" });
    setHint(`已添加《${name}》，存在这台设备上。`);
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
      start = toDateString(first);
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

  const emptyHint = term
    ? week === todayWeek
      ? `第 ${week} 周没有排课，翻一下周次看看。`
      : `第 ${week} 周没有排课，点「本周」跳回当前周。`
    : "还没有课。点「导入课表」粘一段 JSON，或在下面手动添加一门。";

  return (
    <div id="campus-panel-schedule" role="tabpanel" aria-label="课程表">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">我的课表</h2>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="campus-btn" onClick={() => setTermOpen(true)}>
              学期
            </button>
            <button type="button" className="campus-btn is-primary" onClick={() => setImportOpen(true)}>
              导入课表
            </button>
          </div>
        </div>

        {/* 周次条：能前后翻、能直接选周、能记一次「这周是第几周」 */}
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
            <button type="button" className="campus-seg-btn" onClick={() => setFullscreen(true)}>
              全屏
            </button>
          </div>
        </div>

        {term && (
          <p className="campus-card-note" style={{ marginTop: 8 }}>
            第 1 周从 {term.start} 开始，一学期 {term.weeks} 周。
            {todayWeek > 0
              ? todayWeek === week
                ? ` 日历上现在就是第 ${todayWeek} 周。`
                : ` 日历上现在是第 ${todayWeek} 周，点「本周」跳回去。`
              : ""}
          </p>
        )}

        {mode === "week" ? (
          <div className="campus-week-wrap">
            <WeekGrid
              courses={visibleCourses}
              rowHeight={ROW_HEIGHT}
              weekDates={weekDates}
              todayIndex={todayIndex}
              onRemove={removeCourse}
            />
          </div>
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

        <p className="campus-card-note" style={{ marginTop: 12 }} aria-live="polite">
          {hint !== "" ? hint : emptyHint}
          {" "}
          点课块可删除该课程。
        </p>
      </div>

      {/* 手动添加 */}
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">添加一门课</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            共 {snapshot.courses.length} 门课
          </span>
        </div>

        <div className="campus-add-row">
          <input
            className="campus-input"
            type="text"
            placeholder="课程名，如：高等数学"
            aria-label="课程名"
            value={draft.name}
            style={{ flex: "1.4 1 140px" }}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            onKeyDown={(event) => {
              if (event.key === "Enter") addCourse();
            }}
          />

          <select
            className="campus-select"
            aria-label="星期"
            value={draft.day}
            style={{ flex: "0 1 100px" }}
            onChange={(event) => setDraft({ ...draft, day: event.target.value })}
          >
            {DAY_NAMES.map((name, index) => (
              <option key={name} value={String(index + 1)}>
                {name}
              </option>
            ))}
          </select>

          <input
            className="campus-input"
            type="text"
            placeholder="节次，如 1,2 或 1-3"
            aria-label="节次"
            value={draft.sections}
            style={{ flex: "1 1 120px" }}
            onChange={(event) => setDraft({ ...draft, sections: event.target.value })}
          />

          <input
            className="campus-input"
            type="text"
            placeholder="教室（可选）"
            aria-label="教室"
            value={draft.room}
            style={{ flex: "1 1 110px" }}
            onChange={(event) => setDraft({ ...draft, room: event.target.value })}
          />

          <input
            className="campus-input"
            type="text"
            placeholder="老师（可选）"
            aria-label="老师"
            value={draft.teacher}
            style={{ flex: "1 1 110px" }}
            onChange={(event) => setDraft({ ...draft, teacher: event.target.value })}
          />

          <input
            className="campus-input"
            type="text"
            placeholder="周次 1-16（可选）"
            aria-label="周次"
            value={draft.weeks}
            style={{ flex: "1 1 130px" }}
            onChange={(event) => setDraft({ ...draft, weeks: event.target.value })}
          />

          <button type="button" className="campus-btn is-primary" onClick={addCourse}>
            添加
          </button>
        </div>

        <div className="campus-add-row" style={{ marginTop: 8 }}>
          <button type="button" className="campus-btn is-danger" onClick={clearAll}>
            清空课表
          </button>
          <span className="campus-card-note" style={{ margin: 0 }}>
            课表存在这台设备上，刷新不丢。
          </span>
        </div>
      </div>

      {/* 全屏：同一套 WeekGrid，行高放大、多显示上课时间 */}
      {fullscreen && (
        <div className="campus-fullscreen" role="dialog" aria-modal="true" aria-label="全屏课表">
          <div className="campus-fullscreen-bar">
            <div className="campus-fullscreen-title">
              第 {week} 周
              {weekDates
                ? ` · ${weekDates[0].getMonth() + 1}月${weekDates[0].getDate()}日 起`
                : ""}
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
              showTime
              weekDates={weekDates}
              todayIndex={todayIndex}
              onRemove={removeCourse}
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
    </div>
  );
}
