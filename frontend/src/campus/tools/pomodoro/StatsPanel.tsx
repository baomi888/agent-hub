"use client";

/**
 * 专注统计面板（B4 · R3）。
 *
 * 三块内容：数字概览 → 大类饼图 → 下钻明细，底部是导出/导入。
 * 顺序刻意如此：先看总量，再看分布，最后才看明细 —— 反过来会让人一上来就淹没在列表里。
 *
 * 所有聚合都走 `logic/focus.ts` 的纯函数，面板自己不做任何求和。
 * 同一个「今日累计」在计时卡片、饼图、下钻三处必须一模一样，唯一办法是只算一次。
 *
 * 0.5.4 起这块不再是页面里的常驻卡片，0.5.5 起也不是浮层，而是「专注统计」入口下方的
 * 页内展开区 —— 所以它自己不画卡片外壳、也不写标题，只负责内容；
 * 标题与「收起」在 EntryFold 里。
 */

import { type ChangeEvent, useMemo, useRef, useState } from "react";

import PieChart from "./PieChart";

import {
  averageSecondsPerDay,
  dayKey,
  drillDown,
  foldToTop,
  formatDuration,
  formatPercent,
  rangeLabel,
  rangeOf,
  streakDays,
  summarize,
} from "../../logic/focus";
import { buildExport, exportFileName, mergeImport, parseExport } from "../../logic/focusExport";
import type { FocusSession, FocusTask, StatsRange } from "../../types";

const RANGES: readonly { id: StatsRange; label: string }[] = [
  { id: "day", label: "日" },
  { id: "week", label: "周" },
  { id: "month", label: "月" },
  { id: "year", label: "年" },
];

/** 下钻右侧数值的两种口径 */
type DrillView = "ratio" | "duration";

export interface ImportedData {
  tasks: FocusTask[];
  sessions: FocusSession[];
  addedTasks: number;
  addedSessions: number;
}

interface StatsPanelProps {
  sessions: readonly FocusSession[];
  tasks: readonly FocusTask[];
  now: number;
  ready: boolean;
  /** 导入结果交回容器：落库与提示都由容器统一负责，面板不碰存储 */
  onImport: (data: ImportedData) => void;
  onFeedback: (text: string, ok: boolean) => void;
}

export default function StatsPanel({ sessions, tasks, now, ready, onImport, onFeedback }: StatsPanelProps) {
  const [range, setRange] = useState<StatsRange>("week");
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<DrillView>("ratio");
  const fileRef = useRef<HTMLInputElement | null>(null);

  // 区间只随「哪一天」变化：计时中 now 每 250ms 跳一次，不该带着整块统计重算
  const today = dayKey(now);
  const summary = useMemo(() => {
    const bounds = rangeOf(range, now);
    return summarize(sessions, bounds.from, bounds.to);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, range, today]);

  const slices = useMemo(() => foldToTop(summary.categories), [summary]);

  // 换了区间后原来展开的类可能已经不存在了：这里直接判无效，不用 effect 去清状态
  const activeCategory =
    selected !== null && summary.categories.some((item) => item.category === selected) ? selected : null;
  const drill = activeCategory === null ? null : drillDown(summary, activeCategory);

  const average = averageSecondsPerDay(summary, now);
  const streak = streakDays(sessions, now);
  const empty = summary.sessionCount === 0;

  function handleExport() {
    const data = buildExport(tasks, sessions, Date.now());
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = exportFileName(Date.now());
    document.body.appendChild(link);
    link.click();
    link.remove();
    // 立刻 revoke 会让部分浏览器来不及取文件，等下载启动后再回收
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    onFeedback(`已导出 ${sessions.length} 条记录、${tasks.length} 个任务。`, true);
  }

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const parsed = parseExport(await file.text());
      if (!parsed.ok) {
        onFeedback(`导入失败：${parsed.message}`, false);
        return;
      }

      const merged = mergeImport({ tasks, sessions }, parsed);
      if (merged.addedTasks === 0 && merged.addedSessions === 0) {
        onFeedback("导入完成，但这批数据和本地完全重复，没有新增。", true);
        return;
      }

      onImport(merged);
      onFeedback(
        `已导入：新增 ${merged.addedTasks} 个任务、${merged.addedSessions} 条记录（同一条不重复计入）。`,
        true,
      );
    } catch {
      onFeedback("读不到这个文件，可能没有访问权限。", false);
    } finally {
      // 清空 value，否则再选同一个文件不会触发 change
      event.target.value = "";
    }
  }

  return (
    <div className="campus-pomo-stats">
      {/* 区间与日期同一行：展开区里纵向空间宝贵，两行字只表达一件事不划算 */}
      <div className="campus-stats-top">
        <p className="campus-card-note campus-stats-period">{rangeLabel(range, now)}</p>
        <div className="campus-seg" role="group" aria-label="统计区间">
          {RANGES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`campus-seg-btn${range === item.id ? " is-on" : ""}`}
              aria-pressed={range === item.id}
              onClick={() => setRange(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="campus-stats-nums">
        <div className="campus-stats-num">
          <span className="campus-stats-num-label">累计</span>
          <b>{formatDuration(summary.totalSeconds)}</b>
        </div>
        <div className="campus-stats-num">
          <span className="campus-stats-num-label">日均</span>
          <b>{formatDuration(average)}</b>
        </div>
        <div className="campus-stats-num">
          <span className="campus-stats-num-label">连续</span>
          <b>{streak} 天</b>
        </div>
        <div className="campus-stats-num">
          <span className="campus-stats-num-label">专注</span>
          <b>{summary.sessionCount} 次</b>
        </div>
      </div>

      {!ready ? (
        <p className="campus-card-note">正在读取记录…</p>
      ) : empty ? (
        <div className="campus-stats-empty">
          <p>这个区间还没有记录。</p>
          <p className="campus-card-note">选一个任务，专注一轮（至少 30 秒），这里就会有数据。</p>
        </div>
      ) : (
        <>
          <PieChart
            slices={slices}
            totalSeconds={summary.totalSeconds}
            selected={activeCategory}
            onSelect={setSelected}
          />

          {drill && (
            <div className="campus-drill">
              <div className="campus-drill-head">
                <div className="campus-drill-title">
                  <b>{drill.category}</b>
                  <span className="campus-card-note" style={{ margin: 0 }}>
                    合计 {formatDuration(drill.totalSeconds)} · 占全区间 {formatPercent(drill.ratioOfAll)}
                  </span>
                </div>

                <div className="campus-seg" role="group" aria-label="下钻数值口径">
                  <button
                    type="button"
                    className={`campus-seg-btn${view === "ratio" ? " is-on" : ""}`}
                    aria-pressed={view === "ratio"}
                    onClick={() => setView("ratio")}
                  >
                    占比
                  </button>
                  <button
                    type="button"
                    className={`campus-seg-btn${view === "duration" ? " is-on" : ""}`}
                    aria-pressed={view === "duration"}
                    onClick={() => setView("duration")}
                  >
                    时长
                  </button>
                </div>
              </div>

              <ul className="campus-drill-list">
                {drill.tasks.map((task) => (
                  <li key={task.taskId} className="campus-drill-item">
                    <span className="campus-drill-name" title={task.taskName}>
                      {task.taskName}
                    </span>
                    <span className="campus-drill-bar" aria-hidden="true">
                      <i style={{ width: `${Math.max(2, Math.round(task.ratio * 100))}%` }} />
                    </span>
                    <span className="campus-drill-value">
                      {view === "ratio" ? formatPercent(task.ratio) : formatDuration(task.seconds)}
                    </span>
                    <span className="campus-drill-count">{task.count} 次</span>
                  </li>
                ))}
              </ul>

              {drill.tasks.length === 1 && (
                <p className="campus-card-note campus-drill-note">这一类下只有一个任务，所以占比是 100%。</p>
              )}
            </div>
          )}
        </>
      )}

      <div className="campus-stats-io">
        <div className="campus-stats-io-btns">
          <button type="button" className="campus-btn" onClick={handleExport} disabled={sessions.length === 0}>
            导出备份
          </button>
          <button type="button" className="campus-btn" onClick={() => fileRef.current?.click()}>
            导入备份
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="campus-sr-only"
            onChange={handleFile}
            aria-label="选择备份文件"
          />
        </div>
        <p className="campus-card-note campus-stats-io-note">
          数据存在这台设备的浏览器里。换设备前导出一份，在新设备导入即可搬过去（同一条记录不会重复计）。
        </p>
      </div>
    </div>
  );
}
