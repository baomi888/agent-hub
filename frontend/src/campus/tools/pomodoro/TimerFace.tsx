"use client";

/**
 * 计时面板：时钟、进度、方向切换、开始/暂停/结束。
 *
 * 刻意不持有任何状态 —— 时间由容器每 250ms 灌进来，
 * 这样「全屏版」和「页面内版」可以共用同一个表盘（B3 全屏就是这样复用的），
 * 也能避免两处各跑一个定时器导致数字不一致。
 *
 * 时长用「时 + 分」两个框，而不是一个大分钟框：
 *  1. 想定 1 小时 30 分时，不用心算 90；
 *  2. 分钟框允许的值和小时框分开夹取，写 90 不会被偷偷进位；
 *  3. 常用时长做成芯片，一下点定 —— 番茄钟的多数场景就是 25 / 45 / 60 / 90 分钟。
 */

import type { FocusDirection } from "../../types";
import {
  MAX_HOURS,
  formatClock,
  formatDurationLabel,
  splitDuration,
} from "../../logic/pomodoro";

/** 一键预设。取番茄钟最常见的几档：标准番茄、一节课、整小时、一次长自习 */
const QUICK_DURATIONS = [25, 45, 60, 90] as const;

interface TimerFaceProps {
  direction: FocusDirection;
  /** 表盘显示秒数：倒计时为剩余，正计时为已过 */
  display: number;
  /** 已过秒数（暂停期间不增长），用于表盘下方的双时间行 */
  elapsed: number;
  /** 进度 0–100 */
  percent: number;
  running: boolean;
  paused: boolean;
  /** 没选任务时不能开始 */
  canStart: boolean;
  taskName: string | null;
  /** 已选定的总分钟数（倒计时用；正计时不显示时长） */
  minutes: number;
  onDurationChange: (hours: number, minutes: number) => void;
  onDirectionChange: (direction: FocusDirection) => void;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onReset: () => void;
  /** 进入全屏专注（只在计时中可用） */
  onFullscreen: () => void;
}

/** 输入框里的字符串 → 整数。空串、负数、乱码都当 0，夹取交给逻辑层 */
function toInt(value: string): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

export default function TimerFace({
  direction,
  display,
  elapsed,
  percent,
  running,
  paused,
  canStart,
  taskName,
  minutes,
  onDurationChange,
  onDirectionChange,
  onStart,
  onPause,
  onResume,
  onStop,
  onReset,
  onFullscreen,
}: TimerFaceProps) {
  const idled = !running && !paused;
  const duration = splitDuration(minutes);

  return (
    <div className="campus-pomo-face">
      <div className="campus-pomo-top">
        <div className="campus-seg" role="group" aria-label="计时方向">
          <button
            type="button"
            className={`campus-seg-btn${direction === "countdown" ? " is-on" : ""}`}
            onClick={() => onDirectionChange("countdown")}
            disabled={!idled}
            aria-pressed={direction === "countdown"}
          >
            倒计时
          </button>
          <button
            type="button"
            className={`campus-seg-btn${direction === "countup" ? " is-on" : ""}`}
            onClick={() => onDirectionChange("countup")}
            disabled={!idled}
            aria-pressed={direction === "countup"}
          >
            正计时
          </button>
        </div>

        <span className="campus-pomo-task" title={taskName ?? "未选择任务"}>
          {taskName ?? "未选择任务"}
        </span>
      </div>

      {/* 时钟不播报：每 250ms 念一次数字会让读屏用户无法操作 */}
      <div className="campus-pomo-clock" aria-live="off">
        {formatClock(display)}
      </div>

      {/* 双时间：主数字只显示一个方向，另一个方向用小字补上 ——
          倒计时的人常在中间想知道「已经花了多久」，正计时的人反过来想知道「什么时候该停」 */}
      <p className="campus-pomo-sub" aria-live="off">
        {direction === "countdown"
          ? `已过 ${formatClock(elapsed)} · 剩余 ${formatClock(display)}`
          : `已过 ${formatClock(display)} · 不限时`}
      </p>

      <div
        className="campus-pomo-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label="本轮进度"
      >
        <div className="campus-pomo-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="campus-pomo-ctrl">
        {direction === "countdown" && (
          <div className="campus-pomo-dur-wrap">
            <div className="campus-pomo-dur" role="group" aria-label="倒计时时长">
              <label className="campus-pomo-unit">
                <input
                  className="campus-input"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={MAX_HOURS}
                  value={String(duration.hours)}
                  aria-label="时长（小时）"
                  disabled={!idled}
                  onChange={(event) => onDurationChange(toInt(event.target.value), duration.minutes)}
                />
                <span>小时</span>
              </label>
              <label className="campus-pomo-unit">
                <input
                  className="campus-input"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={59}
                  value={String(duration.minutes)}
                  aria-label="时长（分钟）"
                  disabled={!idled}
                  onChange={(event) => onDurationChange(duration.hours, toInt(event.target.value))}
                />
                <span>分钟</span>
              </label>
            </div>

            <div className="campus-pomo-quick" role="group" aria-label="快捷时长">
              {QUICK_DURATIONS.map((value) => {
                const preset = splitDuration(value);
                return (
                  <button
                    key={value}
                    type="button"
                    className={`campus-pomo-chip${minutes === value ? " is-on" : ""}`}
                    aria-pressed={minutes === value}
                    disabled={!idled}
                    onClick={() => onDurationChange(preset.hours, preset.minutes)}
                  >
                    {formatDurationLabel(value)}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="campus-pomo-btns">
          {idled && (
            <button
              type="button"
              className="campus-btn is-primary"
              onClick={onStart}
              disabled={!canStart}
              title={canStart ? "开始专注" : "先在上面选一个任务"}
            >
              开始
            </button>
          )}
          {running && (
            <>
              <button type="button" className="campus-btn" onClick={onPause}>
                暂停
              </button>
              <button type="button" className="campus-btn" onClick={onStop}>
                结束
              </button>
            </>
          )}
          {paused && (
            <>
              <button type="button" className="campus-btn is-primary" onClick={onResume}>
                继续
              </button>
              <button type="button" className="campus-btn" onClick={onStop}>
                结束
              </button>
            </>
          )}
          {(running || paused) && (
            <button type="button" className="campus-btn is-ghost" onClick={onFullscreen}>
              全屏
            </button>
          )}
          {(running || paused) && (
            <button type="button" className="campus-btn is-ghost" onClick={onReset}>
              重置
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
