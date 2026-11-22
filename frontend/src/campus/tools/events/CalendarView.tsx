"use client";

/**
 * 日历视图：一个月一张月历 + 当月清单。
 *
 * 三层信息，从下往上叠：
 *
 *  1. **底色** —— 每个日期格都有，空的也有。原来空白格是透明底，
 *     11 月这种没有确切日期的月份看着像日历塌了。
 *  2. **染底** —— 当天有「已核实到日」的事，格子底色换成该分类的淡彩；
 *     同一天有两个方向就用双色斜切，一眼看出「这天很挤」。
 *  3. **底部色带** —— 当天处于某类赛事窗口期内（但日期没核实到日），
 *     只画 2px 的细线，不染整格。整月都在窗口期的月份如果整格染，
 *     三十格同色等于没染；细线能说清「这个月这几个方向都开着」。
 *
 * 格子里最多显两条，多的折成 +N —— 格子是索引，不是阅读区。
 * 点任意一格只延展出「这一天」的条目；月份精度的事不按天摊，留在右栏本月清单。
 *
 * 移动端（<560）格子里只留色点不留字：390 宽除 7 列，一格约 46px，
 * 塞四个汉字会挤成两行、把整张月历撑变形。
 */

import type { CSSProperties } from "react";
import type { EventItem } from "../../types";
import {
  CATEGORIES,
  CATEGORY_MAP,
  buildMonthCells,
  fitsGrade,
  isAllYear,
  monthOccurrences,
  phaseText,
  type CalendarCell,
  type Occurrence,
} from "../../logic/events";

interface CalendarViewProps {
  items: readonly EventItem[];
  year: number;
  month: number;
  today: { year: number; month: number; day: number };
  grade: number | null;
  onShiftMonth: (delta: number) => void;
  onPickDay: (day: number) => void;
  onPickItem: (id: string) => void;
  onSkipItem: (id: string) => void;
}

const WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

/** 格子里最多显两条，多的折成 +N —— 格子是索引，不是阅读区 */
const CELL_MAX = 2;

/** 当天涉及哪几个分类（去重，保持出现顺序）—— 决定底色是单色还是双色斜切 */
function toneCategories(occurrences: readonly Occurrence[]): string[] {
  const seen: string[] = [];
  for (const occ of occurrences) {
    const color = CATEGORY_MAP[occ.item.category].color;
    if (!seen.includes(color)) seen.push(color);
  }
  return seen;
}

/** 把分类色塞进 CSS 变量，深浅色主题各自在样式表里取用 */
function toneStyle(cell: CalendarCell): CSSProperties | undefined {
  const colors = toneCategories(cell.occurrences);
  if (colors.length === 0) return undefined;
  const metas = cell.occurrences.map((occ) => CATEGORY_MAP[occ.item.category]);
  const style: Record<string, string> = {
    "--ev-c1": colors[0],
    "--ev-c1-dark": metas[0].dark,
    // 深色格底单独一组：靠 mix 凑不出来，见 CategoryMeta.cellDark 的注释
    "--ev-cell-c1": metas[0].cellDark,
  };
  if (colors[1]) {
    style["--ev-c2"] = colors[1];
    style["--ev-c2-dark"] = metas[1]?.dark ?? metas[0].dark;
    style["--ev-cell-c2"] = metas[1]?.cellDark ?? metas[0].cellDark;
  }
  return style as CSSProperties;
}

function CellContent({ cell, onPick }: { cell: CalendarCell; onPick: () => void }) {
  if (cell.day === null) return <span className="campus-ev-cell is-blank" aria-hidden="true" />;

  const shown = cell.occurrences.slice(0, CELL_MAX);
  const rest = cell.occurrences.length - shown.length;
  const tone = toneStyle(cell);
  const className = [
    "campus-ev-cell",
    cell.today ? "is-today" : "",
    tone ? (cell.occurrences.length > 1 ? "is-multi" : "is-tinted") : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type="button"
      className={className}
      style={tone}
      onClick={onPick}
      aria-label={
        cell.occurrences.length > 0
          ? `${cell.day} 日，${cell.occurrences.length} 件有确切日期`
          : `${cell.day} 日，没有确切日期的安排`
      }
    >
      <span className="campus-ev-cell-day">{cell.day}</span>

      {cell.occurrences.length > 0 && (
        <span className="campus-ev-cell-list">
          {shown.map((occ, i) => (
            <span key={`${occ.item.id}-${occ.phase}-${i}`} className="campus-ev-cell-item">
              <span
                className="campus-ev-cell-dot"
                style={
                  {
                    // 深浅两套色点交给 CSS 选：深色格底本身就是分类色，浅色版色点压上去会同色相糊掉
                    "--dot-l": CATEGORY_MAP[occ.item.category].color,
                    "--dot-d": CATEGORY_MAP[occ.item.category].dark,
                  } as CSSProperties
                }
                aria-hidden="true"
              />
              <span className="campus-ev-cell-name">{occ.item.short}</span>
            </span>
          ))}
        </span>
      )}

      {rest > 0 && <span className="campus-ev-cell-more">+{rest}</span>}
    </button>
  );
}

export default function CalendarView({
  items,
  year,
  month,
  today,
  grade,
  onShiftMonth,
  onPickDay,
  onPickItem,
  onSkipItem,
}: CalendarViewProps) {
  const cells = buildMonthCells(items, year, month, today);
  const monthList = monthOccurrences(items, year, month);
  const preciseCount = monthList.filter((occ) => occ.precise).length;

  /*
   * 「本月窗口」：月份精度的条目整月都在窗口期内，画在格子上就是三十格同一条线，
   * 重复三十次不叫信息，叫噪音。集中成日历顶部一行，一眼看清「这个月哪几个方向开着」。
   * 格子只负责两件具体的事：有底色、有确切日期的染色。
   */
  const windowSummary = CATEGORIES.map((cat) => ({
    cat,
    count: monthList.filter(
      (occ) => !occ.precise && !isAllYear(occ.item) && occ.item.category === cat.id,
    ).length,
  })).filter((group) => group.count > 0);
  const windowTotal = windowSummary.reduce((sum, group) => sum + group.count, 0);
  // 全年可报的（普通话/雅思/托福）每月都在，不单列的话「窗口 26 + 全年 3 = 本月 29」这条账对不上
  const allYearCount = monthList.filter((occ) => isAllYear(occ.item)).length;

  return (
    <div className="campus-ev-cal">
      <div className="campus-ev-cal-main">
        <div className="campus-ev-cal-head">
          <button
            type="button"
            className="campus-ev-nav"
            aria-label="上一个月"
            onClick={() => onShiftMonth(-1)}
          >
            ‹
          </button>
          <span className="campus-ev-cal-title">
            {year} 年 {month} 月
          </span>
          <button
            type="button"
            className="campus-ev-nav"
            aria-label="下一个月"
            onClick={() => onShiftMonth(1)}
          >
            ›
          </button>
        </div>

        {windowSummary.length > 0 && (
          <div className="campus-ev-windowband">
            <span className="campus-ev-windowband-label">
              本月窗口 <b>{windowTotal}</b> 件
              <span className="campus-ev-windowband-dirs"> · {windowSummary.length} 个方向</span>
            </span>
            {windowSummary.map((group) => (
              <span key={group.cat.id} className="campus-ev-windowband-item">
                <i style={{ background: group.cat.color }} aria-hidden="true" />
                {group.cat.name}
                <b>{group.count}</b>
              </span>
            ))}
            {allYearCount > 0 && (
              <span className="campus-ev-windowband-item is-muted">
                <i aria-hidden="true" />
                全年可报
                <b>{allYearCount}</b>
              </span>
            )}
          </div>
        )}

        <div className="campus-ev-week" aria-hidden="true">
          {WEEK_LABELS.map((w) => (
            <span key={w}>{w}</span>
          ))}
        </div>

        <div className="campus-ev-grid">
          {cells.map((cell, i) => (
            <CellContent
              key={cell.day === null ? `blank-${i}` : `day-${cell.day}`}
              cell={cell}
              onPick={() => cell.day !== null && onPickDay(cell.day)}
            />
          ))}
        </div>

        <div className="campus-ev-legend">
          {preciseCount > 0 ? (
            <span className="campus-ev-legend-text">
              有色格子 = 已核实到日的安排，共 <b>{preciseCount}</b> 件 · 点格子只看当天
            </span>
          ) : (
            <span className="campus-ev-legend-text">
              本月没有核实到日的安排，日期以各官网公告为准
            </span>
          )}
        </div>
      </div>

      <div className="campus-ev-month-list">
        <h3 className="campus-ev-month-title">
          本月 {monthList.length > 0 ? `${monthList.length} 件` : "没有登记"}
        </h3>
        {monthList.length === 0 ? (
          <p className="campus-empty">这个月没有已登记的事。换个分类筛选看看？</p>
        ) : (
          <ul className="campus-ev-rows">
            {monthList.map((occ, i) => {
              const cat = CATEGORY_MAP[occ.item.category];
              return (
                <li key={`${occ.item.id}-${occ.phase}-${i}`} className="campus-ev-row-item">
                <button
                  type="button"
                  className="campus-ev-row"
                  onClick={() => onPickItem(occ.item.id)}
                >
                  <span className="campus-ev-row-mark" style={{ background: cat.color }} aria-hidden="true" />
                  <span className="campus-ev-row-when">
                    {occ.precise && occ.day ? `${occ.day} 日` : "本月"}
                    <span className="campus-ev-row-phase">{phaseText(occ.phase, occ.item.kind)}</span>
                  </span>
                  <span className="campus-ev-row-name">
                    {occ.item.name}
                    {fitsGrade(occ.item, grade) && <span className="campus-ev-fit">适合</span>}
                  </span>
                  <span className="campus-ev-row-cat">{cat.name}</span>
                </button>
                <button
                  type="button"
                  className="campus-ev-row-skip is-compact"
                  onClick={() => onSkipItem(occ.item.id)}
                  aria-label={`对「${occ.item.name}」不感兴趣，从日历上收走`}
                  title="不感兴趣，从日历上收走"
                >
                  ×
                </button>
              </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
