"use client";

/**
 * 大类占比饼图（B4）。
 *
 * 用 SVG 手写环形图，不引图表库：这里只需要「几片扇形 + 能点」，引一个
 * 20 万字符的库换 40 行路径计算，不值当，也难保证和这套设计令牌一致。
 *
 * 无障碍上有一处必要的变通：SVG 里没有原生 `<button>`，所以扇区用
 * `<g role="button" tabIndex={0}>` + 键盘 Enter/Space 触发，**同时**在右侧图例里
 * 放真正的 `<button>`。两条路径调用同一个 `onSelect`，读屏用户与键盘用户都不会被挡在门外。
 */

import { type KeyboardEvent as ReactKeyboardEvent } from "react";

import { formatDuration, formatPercent } from "../../logic/focus";
import type { CategoryStat } from "../../types";

const CX = 100;
const CY = 100;
const R_OUT = 88;
const R_IN = 52;

/** 六套配色在 CSS 里按类名切换，深色主题换一组更亮的，不在 JS 里写两个色表 */
const SLICE_CLASSES = [
  "campus-pie-c1",
  "campus-pie-c2",
  "campus-pie-c3",
  "campus-pie-c4",
  "campus-pie-c5",
  "campus-pie-c6",
] as const;

function colorClass(index: number): string {
  return SLICE_CLASSES[index % SLICE_CLASSES.length];
}

/** 极坐标 → 直角坐标。0 度指向 12 点，顺时针为正 */
function polar(radius: number, degree: number): [number, number] {
  const rad = ((degree - 90) * Math.PI) / 180;
  return [CX + radius * Math.cos(rad), CY + radius * Math.sin(rad)];
}

/** 一段环形扇区。内外两段弧拼成闭合路径 */
export function sectorPath(from: number, to: number): string {
  const large = to - from > 180 ? 1 : 0;
  const [x1, y1] = polar(R_OUT, from);
  const [x2, y2] = polar(R_OUT, to);
  const [x3, y3] = polar(R_IN, to);
  const [x4, y4] = polar(R_IN, from);
  return [
    `M${x1.toFixed(2)} ${y1.toFixed(2)}`,
    `A${R_OUT} ${R_OUT} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
    `L${x3.toFixed(2)} ${y3.toFixed(2)}`,
    `A${R_IN} ${R_IN} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)}`,
    "Z",
  ].join(" ");
}

interface PieChartProps {
  slices: readonly CategoryStat[];
  totalSeconds: number;
  /** 当前展开的大类；null 表示没展开 */
  selected: string | null;
  onSelect: (category: string | null) => void;
}

export default function PieChart({ slices, totalSeconds, selected, onSelect }: PieChartProps) {
  if (slices.length === 0) return null;

  // 只有一类时画不了扇形（起止角重合），直接画一个整环
  const single = slices.length === 1;

  let cursor = 0;
  const arcs = slices.map((slice, index) => {
    const ratio = totalSeconds > 0 ? slice.seconds / totalSeconds : 0;
    const from = cursor;
    const to = cursor + ratio * 360;
    // 最后一片兜到 360，避免浮点误差在 12 点方向留一条细缝
    cursor = index === slices.length - 1 ? 360 : to;
    return { slice, from, to: Math.max(to, from + 0.6), index };
  });

  function toggle(category: string) {
    onSelect(selected === category ? null : category);
  }

  function onKeyDown(event: ReactKeyboardEvent, category: string) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    toggle(category);
  }

  const centerName = selected ?? "合计";

  return (
    <div className="campus-pie">
      <div className="campus-pie-graphic">
        <svg
          className="campus-pie-svg"
          viewBox="0 0 200 200"
          role="img"
          aria-label={`大类分布，共 ${slices.length} 类，合计 ${formatDuration(totalSeconds)}`}
        >
          {single ? (
            <>
              <circle cx={CX} cy={CY} r={R_OUT} className={`campus-pie-slice ${colorClass(0)}${selected ? " is-on" : ""}`} />
              <circle cx={CX} cy={CY} r={R_IN} className="campus-pie-hole" />
            </>
          ) : (
            arcs.map(({ slice, from, to, index }) => {
              const on = selected === slice.category;
              return (
                <g
                  key={slice.category}
                  role="button"
                  tabIndex={0}
                  aria-label={`${slice.category} ${formatPercent(slice.ratio)}，${on ? "已展开" : "未展开"}`}
                  aria-expanded={on}
                  className={`campus-pie-seg${on ? " is-on" : ""}${selected !== null && !on ? " is-dim" : ""}`}
                  onClick={() => toggle(slice.category)}
                  onKeyDown={(event) => onKeyDown(event, slice.category)}
                >
                  <path d={sectorPath(from, to)} className={`campus-pie-slice ${colorClass(index)}`} />
                </g>
              );
            })
          )}
        </svg>

        {/* 圆心报一个总数：环形图中间空着，正好放「现在看的是什么」 */}
        <div className="campus-pie-center" aria-hidden="true">
          <span className="campus-pie-center-name">{centerName}</span>
          <span className="campus-pie-center-num">{formatDuration(totalSeconds)}</span>
        </div>
      </div>

      <ul className="campus-pie-legend">
        {slices.map((slice, index) => {
          const on = selected === slice.category;
          return (
            <li key={slice.category}>
              <button
                type="button"
                className={`campus-pie-legend-btn${on ? " is-on" : ""}`}
                aria-expanded={on}
                onClick={() => toggle(slice.category)}
              >
                <span className={`campus-pie-dot ${colorClass(index)}`} aria-hidden="true" />
                <span className="campus-pie-legend-name">{slice.category}</span>
                <span className="campus-pie-legend-pct">{formatPercent(slice.ratio)}</span>
                <span className="campus-pie-legend-dur">{formatDuration(slice.seconds)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
