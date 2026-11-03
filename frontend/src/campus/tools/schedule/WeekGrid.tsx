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
 */

import { PERIODS, PERIOD_TIMES, layoutDay } from "../../logic/schedule";
import type { Course } from "../../types";

/** 表头（周几 + 日期）的高度，课块的 top 要加上它 */
const HEAD_HEIGHT = 28;

interface WeekGridProps {
  /** 已经按周次过滤好的课程 */
  courses: readonly Course[];
  /** 单节课的高度（像素）。页面里 44，全屏按屏高算 */
  rowHeight: number;
  /** 是否显示上课时间（全屏视图用） */
  showTime?: boolean;
  /** 每一天的日期，null 表示还没设学期；长度为 7 */
  weekDates?: readonly (Date | null)[];
  /** 今天在第几列；-1 表示不高亮 */
  todayIndex?: number;
  onRemove: (course: Course) => void;
}

export default function WeekGrid({
  courses,
  rowHeight,
  showTime = false,
  weekDates,
  todayIndex = -1,
  onRemove,
}: WeekGridProps) {
  const bodyHeight = PERIODS * rowHeight;

  return (
    <div className="campus-week">
      <div className="campus-week-rail" aria-hidden="true">
        <div className="campus-week-rail-head" style={{ height: HEAD_HEIGHT }} />
        {Array.from({ length: PERIODS }, (_, i) => (
          <div key={i} className="campus-week-slot" style={{ height: rowHeight }}>
            <span className="campus-week-slot-no">{i + 1}</span>
            {showTime && <span className="campus-week-slot-time">{PERIOD_TIMES[i]}</span>}
          </div>
        ))}
      </div>

      <div className="campus-week-days">
        {Array.from({ length: 7 }, (_, day) => {
          const blocks = layoutDay(courses.filter((course) => course.day === day));
          const date = weekDates?.[day] ?? null;
          const isToday = day === todayIndex;

          return (
            <div
              key={day}
              className={`campus-week-day${isToday ? " is-today" : ""}`}
              style={{ height: HEAD_HEIGHT + bodyHeight }}
            >
              <div className="campus-week-day-head" style={{ height: HEAD_HEIGHT }}>
                <span>周{"一二三四五六日"[day]}</span>
                {date && <span className="campus-week-date">{date.getDate()}</span>}
              </div>

              {blocks.map((block) => {
                const span = block.end - block.start + 1;
                const width = 100 / block.cols;
                const { course } = block;

                const label = [
                  `${course.name}（第${block.start}${span > 1 ? `-${block.end}` : ""}节）`,
                  course.classroom ? `@${course.classroom}` : "",
                  course.teacher,
                ]
                  .filter(Boolean)
                  .join(" ");

                return (
                  <button
                    key={course.id}
                    type="button"
                    className={`campus-week-block${span === 1 ? " is-one" : ""}${
                      block.cols > 1 ? " is-split" : ""
                    }`}
                    title={`点击删除：${label}`}
                    aria-label={`${label}，点击删除`}
                    onClick={() => onRemove(course)}
                    style={{
                      top: HEAD_HEIGHT + (block.start - 1) * rowHeight + 1,
                      height: span * rowHeight - 2,
                      left: `calc(${(block.col * width).toFixed(4)}% + 1px)`,
                      width: `calc(${width.toFixed(4)}% - 2px)`,
                      // 8 位十六进制末两位是透明度：浅色底 + 同色描边，深浅主题都看得清
                      background: `${course.color}1f`,
                      borderColor: course.color,
                    }}
                  >
                    <span className="campus-week-block-name">{course.name}</span>
                    {course.classroom && (
                      <span className="campus-week-block-room">{course.classroom}</span>
                    )}
                    {course.teacher && (
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
