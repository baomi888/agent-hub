"use client";

/**
 * 专注视图（B3）。
 *
 * 它只是页面里的一块大区域，**不是全屏覆盖层**。之前一打开就 `requestFullscreen`，
 * 结果整页被接管：标签栏没了、统计看不了、连预览窗都被顶掉 —— 用户没法"正常使用"。
 * 现在默认就长在页面里，能滚、能切标签；想要整屏的人，点视图里的「全屏」，那是一次显式选择。
 *
 * 组件不持有任何计时状态 —— 时间仍由容器每 250ms 灌进来。
 * 所以展开 / 收起 / 进出全屏都不会丢计时，也不会出现两个定时器打架。
 *
 * 排版：宽屏时「大钟居中、操作区在右」，窄屏上下堆叠。
 */

import { useEffect, useRef, useState, type RefObject } from "react";

import { formatClock } from "../../logic/pomodoro";
import { formatWallClock } from "../../logic/fullscreen";
import type { FocusDirection } from "../../types";

interface FocusStageProps {
  /** 外部传入的容器 ref，用户点「全屏」时对它请求全屏 */
  containerRef: RefObject<HTMLElement | null>;
  taskName: string | null;
  category: string;
  direction: FocusDirection;
  /** 主数字：倒计时为剩余，正计时为已过 */
  display: number;
  /** 已过秒数（暂停期间不增长） */
  elapsed: number;
  percent: number;
  running: boolean;
  paused: boolean;
  /** 当前时刻，用于全屏时自己报时 */
  now: number;
  /** 是否真的处在浏览器全屏里。由容器监听 fullscreenchange 灌进来 */
  fullscreen: boolean;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onToggleFullscreen: () => void;
  onExit: () => void;
}

export default function FocusStage({
  containerRef,
  taskName,
  category,
  direction,
  display,
  elapsed,
  percent,
  running,
  paused,
  now,
  fullscreen,
  onPause,
  onResume,
  onStop,
  onToggleFullscreen,
  onExit,
}: FocusStageProps) {
  const clockRef = useRef<HTMLDivElement | null>(null);
  /** 浏览器是否支持元素全屏。不支持就别摆一个按了没反应的按钮 */
  const [canFullscreen, setCanFullscreen] = useState(false);

  // 依赖 `document.fullscreenEnabled`，只能在挂载后读（服务端没有 document）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCanFullscreen(Boolean(document.fullscreenEnabled));
  }, []);

  // 挂载即把焦点移到视图内：键盘用户不必先 Tab 穿过背后的整页才能点到「暂停」。
  // 顺带把视图滚进视野 —— 原来那块卡片被它顶掉了，不滚一下用户可能停在空白处。
  // 注意不锁 body 滚动：视图是可滚页面的一部分，锁住反而像「页面卡死了」。
  useEffect(() => {
    clockRef.current?.focus({ preventScroll: true });
    containerRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });

    /*
     * 收起时把焦点还给「全屏」按钮。
     * 钟本身是聚焦目标，视图一收它就没了，焦点掉回 body —— 和展开区、完成弹窗
     * 是同一类问题：键盘用户得从页首重新 Tab 一遍。
     * 只在焦点真的丢了的时候接回来，不抢用户已经移走的焦点。
     */
    return () => {
      const active = document.activeElement;
      if (active !== null && active !== document.body) return;
      document.querySelector<HTMLElement>(".campus-pomo-btns .campus-btn.is-ghost")?.focus({ preventScroll: true });
    };
  }, [containerRef]);

  /**
   * Esc：全屏时交给浏览器退出全屏（不拦），非全屏时收起视图。
   * 全屏被拒（iOS 某些版本、嵌入式 WebView）的情况下浏览器不会处理 Esc，于是这里兜住。
   */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (fullscreen) return;
      event.preventDefault();
      onExit();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onExit, fullscreen]);

  const secondary =
    direction === "countdown"
      ? `已过 ${formatClock(elapsed)} · 剩余 ${formatClock(display)}`
      : `已过 ${formatClock(display)} · 不限时`;

  return (
    <section
      className={`campus-stage${fullscreen ? " is-fs" : ""}`}
      ref={containerRef}
      aria-label="专注视图"
    >
      <div className="campus-stage-head">
        <div className="campus-stage-task">
          <b>{taskName ?? "未选择任务"}</b>
          <span className="campus-stage-cat">{category}</span>
        </div>

        <div className="campus-stage-meta">
          <span className="campus-stage-now" title="当前时刻">
            {formatWallClock(now)}
          </span>
          {canFullscreen && (
            <button type="button" className="campus-btn is-ghost" onClick={onToggleFullscreen}>
              {fullscreen ? "退出全屏" : "全屏"}
            </button>
          )}
          <button type="button" className="campus-btn is-ghost" onClick={onExit}>
            收起
          </button>
        </div>
      </div>

      <div className="campus-stage-main">
        <div className="campus-stage-clock" ref={clockRef} tabIndex={-1} aria-live="off">
          {formatClock(display)}
        </div>
        <div className="campus-stage-sub">{secondary}</div>

        <div
          className="campus-stage-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-label="本轮进度"
        >
          <div className="campus-stage-fill" style={{ width: `${percent}%` }} />
        </div>

        <p className="campus-stage-hint">
          {paused ? "已暂停，继续时会接着算。" : "随时可以切到别的标签；按 Esc 收起。"}
        </p>
      </div>

      <div className="campus-stage-actions">
        {running && (
          <button type="button" className="campus-btn" onClick={onPause}>
            暂停
          </button>
        )}
        {paused && (
          <button type="button" className="campus-btn is-primary" onClick={onResume}>
            继续
          </button>
        )}
        <button type="button" className="campus-btn" onClick={onStop}>
          结束
        </button>
        <p className="campus-stage-rotate">横过来更沉浸</p>
      </div>
    </section>
  );
}
