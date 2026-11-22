"use client";

/**
 * 时间轴视图：一屏看完整学年。
 *
 * 按月纵向排，9 月在最上面 —— 学生的时间感是「开学 → 期末 → 下学期」，
 * 按自然年 1–12 排会把人劈成两半。
 *
 * 顶部「接下来 60 天」是这个页面真正的用处：整张年历表看的是「有哪些」，
 * 60 天清单回答的是「我现在该准备什么」。信息差最贵的地方是报名截止，不是比赛当天。
 */

import type { EventItem } from "../../types";
import {
  CATEGORY_MAP,
  SCHOOL_MONTHS,
  calendarYearOf,
  dateText,
  daysText,
  fitsGrade,
  isAllYear,
  monthOccurrences,
  phaseText,
  upcomingEntries,
} from "../../logic/events";

interface TimelineViewProps {
  items: readonly EventItem[];
  schoolYear: number;
  today: Date;
  grade: number | null;
  onPickItem: (id: string) => void;
  onPickMonth: (year: number, month: number) => void;
}

/** 近期提醒看多少天 */
const UPCOMING_DAYS = 60;
/** 默认展开几条，多的折起来 */
const UPCOMING_SHOW = 6;

export default function TimelineView({
  items,
  schoolYear,
  today,
  grade,
  onPickItem,
  onPickMonth,
}: TimelineViewProps) {
  // 全年可报的条目单独收在底部：它们没有「某个月」可言，混进月份里反而占满每一格
  const allYearItems = items.filter(isAllYear);
  const monthItems = items.filter((i) => !isAllYear(i));

  const upcoming = upcomingEntries(items, today, UPCOMING_DAYS, {});

  return (
    <div className="campus-ev-timeline">
      <section className="campus-ev-soon">
        <h3 className="campus-ev-soon-title">
          接下来 {UPCOMING_DAYS} 天
          <span className="campus-ev-soon-count">{upcoming.length} 件</span>
        </h3>
        {upcoming.length === 0 ? (
          <p className="campus-empty">近两个月没有已登记的事。可以去日历里翻翻下半学期。</p>
        ) : (
          <ul className="campus-ev-soon-list">
            {upcoming.slice(0, UPCOMING_SHOW).map((u, i) => (
              <li key={`${u.item.id}-${u.phase}-${i}`}>
                <button type="button" className="campus-ev-soon-row" onClick={() => onPickItem(u.item.id)}>
                  <span className="campus-ev-soon-days">{daysText(u.daysLeft)}</span>
                  <span className="campus-ev-soon-name">
                    {u.item.short}
                    <span className="campus-ev-soon-phase">{phaseText(u.phase, u.item.kind)}</span>
                  </span>
                  <span className="campus-ev-soon-date">{dateText(u.date, u.precise)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {upcoming.length > UPCOMING_SHOW && (
          <p className="campus-ev-soon-more">
            还有 {upcoming.length - UPCOMING_SHOW} 件，切到「日历」逐月看。
          </p>
        )}
      </section>

      {SCHOOL_MONTHS.map((month) => {
        const year = calendarYearOf(schoolYear, month);
        const list = monthOccurrences(monthItems, year, month);
        const isNow = today.getFullYear() === year && today.getMonth() + 1 === month;

        return (
          <section
            key={`${year}-${month}`}
            className={`campus-ev-month${isNow ? " is-now" : ""}`}
          >
            <div className="campus-ev-month-head">
              <button
                type="button"
                className="campus-ev-month-label"
                onClick={() => onPickMonth(year, month)}
                title="在日历里打开这个月"
              >
                <span className="campus-ev-month-num">{month}</span>
                <span className="campus-ev-month-unit">月</span>
                {isNow && <span className="campus-ev-month-now">本月</span>}
              </button>
              <span className="campus-ev-month-count">
                {list.length > 0 ? `${list.length} 件` : ""}
              </span>
            </div>

            {list.length === 0 ? (
              <p className="campus-ev-month-empty">没有登记的事</p>
            ) : (
              <ul className="campus-ev-rows">
                {list.map((occ, i) => {
                  const cat = CATEGORY_MAP[occ.item.category];
                  return (
                    <li key={`${occ.item.id}-${occ.phase}-${i}`}>
                      <button
                        type="button"
                        className="campus-ev-row"
                        onClick={() => onPickItem(occ.item.id)}
                      >
                        <span
                          className="campus-ev-row-mark"
                          style={{ background: cat.color }}
                          aria-hidden="true"
                        />
                        <span className="campus-ev-row-when">
                          {occ.precise && occ.day ? `${occ.day} 日` : "本月"}
                          <span className="campus-ev-row-phase">
                            {phaseText(occ.phase, occ.item.kind)}
                          </span>
                        </span>
                        <span className="campus-ev-row-name">
                          {occ.item.name}
                          {fitsGrade(occ.item, grade) && <span className="campus-ev-fit">适合</span>}
                        </span>
                        <span className="campus-ev-row-cat">{cat.name}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}

      {allYearItems.length > 0 && (
        <section className="campus-ev-allyear">
          <h3 className="campus-ev-month-title">全年可报</h3>
          <p className="campus-ev-month-empty">这几项没有固定月份，按考点批次安排。</p>
          <ul className="campus-ev-rows">
            {allYearItems.map((item) => {
              const cat = CATEGORY_MAP[item.category];
              return (
                <li key={item.id}>
                  <button type="button" className="campus-ev-row" onClick={() => onPickItem(item.id)}>
                    <span className="campus-ev-row-mark" style={{ background: cat.color }} aria-hidden="true" />
                    <span className="campus-ev-row-when">全年</span>
                    <span className="campus-ev-row-name">
                      {item.name}
                      {fitsGrade(item, grade) && <span className="campus-ev-fit">适合</span>}
                    </span>
                    <span className="campus-ev-row-cat">{cat.name}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
