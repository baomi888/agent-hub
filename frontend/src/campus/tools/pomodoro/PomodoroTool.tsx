"use client";

/**
 * 番茄钟。
 *
 * 与旧版的唯一行为差异：**「今日已完成」会记住**。旧版这个数字只放内存，刷新就归零，
 * 用户第二天打开看到 0 会以为昨天的记录丢了。新增一个 campus.pomodoro.daily.v1 键存
 * `{ date, count }`，跨天自动归零（用日期键判断，不依赖定时器）。
 *
 * 计时用绝对时间戳（见 logic/pomodoro.ts 的说明），切到别的标签页再回来不会掉拍。
 */

import { useEffect, useState } from "react";
import {
  MAX_MINUTES,
  MIN_MINUTES,
  clampMinutes,
  formatClock,
  progressOf,
  remainSeconds,
  todayKey,
} from "../../logic/pomodoro";
import { readJson, writeJson } from "../../storage";
import { CAMPUS_KEYS } from "../../storage/keys";
import type { PomodoroDaily } from "../../types";

const DEFAULT_MINUTES = 25;

/** 250ms 一跳：秒数变化才触发重渲染，比 1s 一跳更跟手，也不会太耗 */
const TICK_MS = 250;

export default function PomodoroTool() {
  const [minutes, setMinutes] = useState(DEFAULT_MINUTES);
  const [remain, setRemain] = useState(DEFAULT_MINUTES * 60);
  /** 结束时刻；null 表示没在跑。用绝对时间避免后台节流导致计时失准 */
  const [endAt, setEndAt] = useState<number | null>(null);
  const [daily, setDaily] = useState<PomodoroDaily>({ date: todayKey(), count: 0 });
  const [ready, setReady] = useState(false);
  const [justFinished, setJustFinished] = useState(false);

  const running = endAt !== null;
  const total = minutes * 60;

  // 首屏恢复当日计数。effect 里同步外部存储是 React 文档承认的例外，
  // 此时页面已挂载、localStorage 可用，且只在挂载时跑一次。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const today = todayKey();
    const saved = readJson<PomodoroDaily>(CAMPUS_KEYS.pomodoro);
    if (saved && saved.date === today && Number.isFinite(saved.count)) {
      setDaily({ date: today, count: Math.max(0, Math.floor(saved.count)) });
    } else {
      // 跨天了：不沿用昨天的数字，直接归零
      setDaily({ date: today, count: 0 });
    }
    setReady(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!ready) return;
    writeJson(CAMPUS_KEYS.pomodoro, daily);
  }, [daily, ready]);

  useEffect(() => {
    if (endAt === null) return;

    const timer = window.setInterval(() => {
      const left = remainSeconds(endAt, Date.now());
      setRemain(left);

      if (left > 0) return;

      window.clearInterval(timer);
      setEndAt(null);
      setJustFinished(true);
      setDaily((prev) => {
        const today = todayKey();
        const base = prev.date === today ? prev.count : 0;
        return { date: today, count: base + 1 };
      });
    }, TICK_MS);

    return () => window.clearInterval(timer);
  }, [endAt]);

  function handleStart() {
    setJustFinished(false);
    // 剩余时间不合理（被重置过、或改了时长）就按当前时长重新计
    const next = remain <= 0 || remain > total ? total : remain;
    setRemain(next);
    setEndAt(Date.now() + next * 1000);
  }

  function handlePause() {
    if (endAt === null) return;
    setRemain(remainSeconds(endAt, Date.now()));
    setEndAt(null);
  }

  function handleReset() {
    setEndAt(null);
    setRemain(total);
    setJustFinished(false);
  }

  function handleMinutes(value: string) {
    const next = clampMinutes(Number.parseFloat(value), DEFAULT_MINUTES);
    setMinutes(next);
    // 没在跑的时候跟着改显示，跑起来以后不动，避免用户改数字把正在走的番茄钟搞乱
    if (!running) {
      setRemain(next * 60);
      setJustFinished(false);
    }
  }

  const percent = Math.round(progressOf(remain, total) * 100);

  return (
    <div id="campus-panel-pomodoro" role="tabpanel" aria-label="番茄钟">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">专注计时</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            今日已完成 <b>{daily.count}</b> 个
          </span>
        </div>

        {/* 时钟本身不播报，否则读屏软件每 250ms 念一次 */}
        <div className="campus-pomo-clock" aria-live="off">
          {formatClock(remain)}
        </div>

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
          <label className="campus-field" style={{ maxWidth: 150 }}>
            <span>时长（分钟）</span>
            <input
              className="campus-input"
              type="number"
              min={MIN_MINUTES}
              max={MAX_MINUTES}
              value={String(minutes)}
              onChange={(event) => handleMinutes(event.target.value)}
            />
          </label>

          <div className="campus-pomo-btns">
            <button
              type="button"
              className="campus-btn is-primary"
              onClick={handleStart}
              disabled={running}
            >
              开始
            </button>
            <button type="button" className="campus-btn" onClick={handlePause} disabled={!running}>
              暂停
            </button>
            <button type="button" className="campus-btn" onClick={handleReset}>
              重置
            </button>
          </div>
        </div>

        <p className="campus-card-note" aria-live="polite" style={{ marginTop: 12 }}>
          {justFinished
            ? "一个番茄完成了，站起来走两步再回来。"
            : "25 分钟专注 + 5 分钟休息是经典节奏。专注时建议关掉其他标签页。"}
        </p>
      </div>
    </div>
  );
}
