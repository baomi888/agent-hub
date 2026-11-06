"use client";

/**
 * 课表周视图。
 *
 * 抽成独立组件是因为它要画两遍：页面里一遍、全屏里一遍。
 * 旧版靠 `paintWeek(railEl, daysEl, rowH, opt)` 把两个容器当参数传，思路一样，
 * 但这里是同一份 JSX，两个实例不可能出现「页面和全屏不一致」。
 *
 * 关键的排版规则全部来自 logic/schedule.ts 的 layoutDay：
 * 同一格有重叠课时左右分栏，谁也不盖谁 —— 两块绝对定位叠在同一个 top 上就是文字重叠。
 *
 * B2（2026-11-06）改动：
 *  - 节数与每节时间改由 `plan` 传入，不再读写死的 12 节常量（需求 4.1 / A03）；
 *  - 时间常显在节次下面，页面里也标，不用进全屏才看得到；
 *  - 课块内容按**高度分级**显示：1 节只留课名、2 节加教室、3 节以上才放老师。
 *    原来只要超过 1 节就三行全塞，4 节的块里下半截是空的 —— 就是「框框太空旷」（4.3）。
 *
 * B3（2026-11-06）改动：跟日历对上
 *  - 放假那天的课块变暗 + 课名加删除线（4.5），调休上班日不变暗；
 *  - 列头右上角一枚「休 / 班」小徽标（4.6），颜色之外还有字，色弱也分得清（A06）；
 *  - 今天那一列本来就整列淡底，B3 再给今天要上的课块加深描边（4.4 的「轻微高亮」）。
 *    两件事不互斥：整列淡底表示「你在这天」，课块变暗表示「不用上课」（C2 的取舍）。
 *  - 没设学期时 `dayInfos` 是 undefined，以上三条全部静默跳过 —— 没有日期就没有真假。
 *
 * B3.1（2026-11-06）改动：列头补月份
 *  - 原来每列只写日号（`24`），整张表看不出是几月，跨月那周更是「29 30 1 2」；
 *  - 现在 **1 号那天写 `10/1`**，其余日子仍只写日号。7 列都带月份会把列头撑成两行，
 *    整周的月份由上方的「10月5日 – 10月11日」交代；
 *  - 列头日期的 `title` 给出「2026 年 10 月 1 日」全称，桌面端悬停可查。
 */

import type { DayInfo } from "../../logic/holidays";
import { DAYS_IN_WEEK, DAY_LABELS, layoutDay } from "../../logic/schedule";
import type { Course, PeriodPlan } from "../../types";

/** 表头（周几 + 日期）的高度，课块的 top 要加上它 */
const HEAD_HEIGHT = 28;
/** 窄列模式（全屏手机）的表头：周几和日期分两行，高度多 8px */
const HEAD_HEIGHT_COMPACT = 36;

/** 表头实际高度；全屏算行高时要用同一个值，所以对外暴露 */
export function headHeightOf(compact: boolean): number {
  return compact ? HEAD_HEIGHT_COMPACT : HEAD_HEIGHT;
}

interface WeekGridProps {
  /** 已经按周次过滤好的课程 */
  courses: readonly Course[];
  /** 单节课的高度（像素）。页面里 46，全屏按屏高算 */
  rowHeight: number;
  /** 作息：每天几节、每节几点开始 */
  plan: PeriodPlan;
  /** 每一天的日期，null 表示还没设学期；长度为 7 */
  weekDates?: readonly (Date | null)[];
  /** 每一天的放假 / 调休信息，与 `weekDates` 对齐；没设学期时为 undefined */
  dayInfos?: readonly (DayInfo | null)[];
  /** 今天在第几列；-1 表示不高亮 */
  todayIndex?: number;
  /**
   * 窄列模式（B4，全屏手机）：列宽只有 45px 左右。
   * 表头改「周几 / 日期」两行，字号退一档，「休 / 班」徽标留在周几旁边。
   */
  compact?: boolean;
  /**
   * 矮行模式（B4，横屏手机）：一行只有 25px 上下。
   * 课名字号再退到 11px、行距压紧，并**只留课名**——教室和老师在这点高度里
   * 只会把课名挤掉一行，点开详情看更实在（需求 6.2 的「只见轮廓」）。
   */
  dense?: boolean;
  /**
   * 无表头模式（B6，手机页面内）：日期已经由表格上方的日期条交代，
   * 表头再写一遍就是同一件事说两遍，还白占 28px。
   * 置 true 时列头整行不渲染，`top` 基准回到 0。
   */
  bareHead?: boolean;
  /** 点课块：打开课程详情（B1 起，点击不再是「直接删除」） */
  onOpen: (course: Course) => void;
}

export default function WeekGrid({
  courses,
  rowHeight,
  plan,
  weekDates,
  dayInfos,
  todayIndex = -1,
  compact = false,
  dense = false,
  bareHead = false,
  onOpen,
}: WeekGridProps) {
  const count = plan.count;
  const bodyHeight = count * rowHeight;
  const headHeight = bareHead ? 0 : headHeightOf(compact);
  const rootClass = `campus-week${compact ? " is-compact" : ""}${dense ? " is-dense" : ""}${
    bareHead ? " is-bare" : ""
  }`;

  return (
    <div className={rootClass}>
      <div className="campus-week-rail" aria-hidden="true">
        <div className="campus-week-rail-head" style={{ height: headHeight }} />
        {Array.from({ length: count }, (_, i) => (
          <div key={i} className="campus-week-slot" style={{ height: rowHeight }}>
            <span className="campus-week-slot-no">{i + 1}</span>
            <span className="campus-week-slot-time">{plan.starts[i] ?? ""}</span>
          </div>
        ))}
      </div>

      <div className="campus-week-days">
        {/*
         * 横向分节线。
         * 原来只有左侧节次栏有边框，日期列里一条横线都没有，节次和时间看着像浮在空中，
         * 用户数不清「这一格是第几节」。一条 repeating 渐变比画 7×12 个 div 便宜得多，
         * 行高变化时也只需要改一个 background-size。
         */}
        <div
          className="campus-week-lines"
          aria-hidden="true"
          style={{ top: headHeight, height: bodyHeight, backgroundSize: `100% ${rowHeight}px` }}
        />

        {Array.from({ length: DAYS_IN_WEEK }, (_, day) => {
          const blocks = layoutDay(courses.filter((course) => course.day === day));
          const date = weekDates?.[day] ?? null;
          const info = dayInfos?.[day] ?? null;
          const isToday = day === todayIndex;
          const off = info?.kind === "off";

          return (
            <div
              key={day}
              className={`campus-week-day${isToday ? " is-today" : ""}`}
              style={{ height: headHeight + bodyHeight }}
            >
              <div
                className={`campus-week-day-head${compact ? " is-compact" : ""}`}
                style={{ height: bareHead ? 0 : headHeight }}
                hidden={bareHead}
              >
                <span className="campus-week-dow">{DAY_LABELS[day]}</span>
                {date && (
                  <span
                    className="campus-week-date"
                    title={`${date.getFullYear()} 年 ${date.getMonth() + 1} 月 ${date.getDate()} 日`}
                  >
                    {/*
                     * 每天写「月/日」（`10/5`），不写裸日号。
                     * 只写 `5` 时，整张表看不出是几月；跨月那周更是「29 30 1 2」，
                     * 1 号到底是十月还是十一月只能靠猜 —— 这是 2026-11-06 反馈的原话。
                     * 列头只有 28px 高，写不下「10月5日」，`10/5` 是这套宽度里最省字的写法。
                     */}
                    {date.getMonth() + 1}/{date.getDate()}
                  </span>
                )}
                {info && (
                  <span
                    className={`campus-week-mark is-${info.kind}`}
                    title={`${date ? `${date.getMonth() + 1} 月 ${date.getDate()} 日 · ` : ""}${info.label} · ${
                      info.kind === "off" ? "放假，不用上课" : "调休上班，要上课"
                    }`}
                  >
                    {info.kind === "off" ? "休" : "班"}
                  </span>
                )}
              </div>

              {blocks.map((block) => {
                const span = block.end - block.start + 1;
                const width = 100 / block.cols;
                const { course } = block;

                /*
                 * 教室 / 老师的出现门槛随宽度走（B4）。
                 * 宽列维持「2 节给教室、3 节给老师」；
                 * 窄列（45px）往里塞教室是对折的：「机房-A」只剩「机房…」，
                 * 花了 2 行的高度换 2 个字，不如把空间留给课名 —— 完整信息点开看。
                 */
                const roomAt = compact ? 3 : 2;
                const teacherAt = compact ? 4 : 3;

                const label = [
                  `${course.name}（第${block.start}${span > 1 ? `-${block.end}` : ""}节）`,
                  course.classroom ? `@${course.classroom}` : "",
                  course.teacher,
                  off ? "放假停课" : "",
                ]
                  .filter(Boolean)
                  .join(" ");

                return (
                  <button
                    key={course.id}
                    type="button"
                    className={`campus-week-block${block.cols > 1 ? " is-split" : ""}${
                      off ? " is-off" : ""
                    }${isToday ? " is-today-block" : ""}${span >= 2 ? " is-tall" : ""}${
                      span >= 3 ? " is-long" : ""
                    }`}
                    title={`点击查看：${label}`}
                    aria-label={`${label}，点击查看详情`}
                    onClick={() => onOpen(course)}
                    style={{
                      top: headHeight + (block.start - 1) * rowHeight + 1,
                      height: span * rowHeight - 2,
                      left: `calc(${(block.col * width).toFixed(4)}% + 1px)`,
                      width: `calc(${width.toFixed(4)}% - 2px)`,
                      // 课程色直接做底色（B5 起块是实心的，不再是 12% 淡底 + 同色描边）。
                      // 底色不透明，背后那条横向分节线不会再透过来，所以不用再垫一层卡片色。
                      // 放假那天（4.5）换成灰底：这门课在这天被取消了，不该还留着课程色。
                      background: off ? "var(--course-off)" : course.color,
                      // 描边留 1px 透明：块宽是按 calc(% - 2px) 算的，删掉 border 会多出 2px 顶到隔壁列
                      borderColor: "transparent",
                    }}
                  >
                    <span className="campus-week-block-name">{course.name}</span>
                    {!dense && span >= roomAt && course.classroom && (
                      <span className="campus-week-block-room">{course.classroom}</span>
                    )}
                    {!dense && span >= teacherAt && course.teacher && (
                      <span className="campus-week-block-teacher">{course.teacher}</span>
                    )}
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
