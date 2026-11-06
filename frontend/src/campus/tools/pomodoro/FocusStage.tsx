"use client";

/**
 * 专注视图（B3）。
 *
 * 它只是页面里的一块大区域，**不是打开就被全屏的覆盖层**。之前一打开就
 * `requestFullscreen`，结果整页被接管：标签栏没了、统计看不了、连预览窗都被顶掉 ——
 * 用户没法"正常使用"。现在默认就长在页面里，能滚、能切标签；点「全屏」才整屏。
 *
 * 两种全屏，对用户是同一件事：
 *  - **浏览器全屏**：桌面、Android Chrome 有，整块屏幕交给页面，连地址栏都没了；
 *  - **页内全屏**（`immersive`）：iPhone Safari 没有元素全屏 API，用 `position: fixed`
 *    铺满视口 + 安全区适配顶上。少一条系统 UI，但版式、大钟、横屏排版完全一致。
 * 判定与优先级在 `PomodoroTool`：先请求真全屏，失败了才落到这一层。
 *
 * 组件不持有任何计时状态 —— 时间仍由容器每 250ms 灌进来。
 * 所以展开 / 收起 / 进出全屏都不会丢计时，也不会出现两个定时器打架。
 *
 * 排版：宽屏时「大钟居中、操作区在右」，窄屏上下堆叠。
 */

import { useEffect, useRef, type RefObject } from "react";

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
  /** 是否整屏（浏览器全屏或页内全屏，任一为真） */
  fullscreen: boolean;
  /** 是否走的是页内全屏（CSS 铺满）。只用于加类名与文案 */
  immersive: boolean;
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
  immersive,
  onPause,
  onResume,
  onStop,
  onToggleFullscreen,
  onExit,
}: FocusStageProps) {
  const clockRef = useRef<HTMLDivElement | null>(null);

  // 挂载即把焦点移到视图内：键盘用户不必先 Tab 穿过背后的整页才能点到「暂停」。
  // 顺带把视图滚进视野 —— 这块区域顶掉了原来的卡片，不滚一下用户可能停在空白处。
  // 注意不锁 body 滚动（页内全屏时由容器单独锁）：视图是可滚页面的一部分，锁住反而像「页面卡死了」。
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
   * Esc：三级退让，保证「按一下退一层」，不会一下退出两层。
   *
   *  - 页内全屏：先退出全屏，仍留在专注视图（和浏览器全屏按 Esc 的行为对齐）；
   *  - 浏览器全屏：交给浏览器自己处理，这里不拦；
   *  - 都不是：收起视图。
   *
   * 之所以页内全屏要自己兜：它只是一层 CSS，浏览器不认识，没人接就会一路穿透到「收起」。
   */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (fullscreen && !immersive) return;
      event.preventDefault();
      if (immersive) {
        onToggleFullscreen();
        return;
      }
      onExit();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onExit, onToggleFullscreen, fullscreen, immersive]);

  const secondary =
    direction === "countdown"
      ? `已过 ${formatClock(elapsed)} · 剩余 ${formatClock(display)}`
      : `已过 ${formatClock(display)} · 不限时`;

  const className = [
    "campus-stage",
    fullscreen ? "is-full" : "",
    immersive ? "is-immersive" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section className={className} ref={containerRef} aria-label="专注视图">
      <div className="campus-stage-head">
        <div className="campus-stage-task">
          <b>{taskName ?? "未选择任务"}</b>
          <span className="campus-stage-cat">{category}</span>
        </div>

        <div className="campus-stage-meta">
          <span className="campus-stage-now" title="当前时刻">
            {formatWallClock(now)}
          </span>
          {/*
            这个按钮**一直渲染**。以前它挂在 `document.fullscreenEnabled` 后面，
            结果 iPhone 上整颗按钮消失 —— 而手机上恰恰最需要「把这块放大」，
            页内全屏正是为那种环境准备的。
          */}
          <button type="button" className="campus-btn is-ghost" onClick={onToggleFullscreen}>
            {fullscreen ? "退出全屏" : "全屏"}
          </button>
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

        {/*
          提示分两版：键盘类操作（Esc）只在电脑上出现。
          手机上印「按 Esc 收起」是给了一个按不到的操作，用户会去找那个键 ——
          触屏这版改成直接说点子哪里。判断走 CSS 媒体查询（见 campus.css 设备能力一节），
          不用 JS 判设备，免得服务端渲染的首帧先闪一版错的文案。
        */}
        <p className="campus-stage-hint">
          {paused ? (
            "已暂停，继续时会接着算。"
          ) : fullscreen ? (
            <>
              <span className="campus-desktop-only">
                点「退出全屏」回到页面；按 Esc 也行，计时不会中断。
              </span>
              <span className="campus-touch-only">点「退出全屏」回到页面，计时不会中断。</span>
            </>
          ) : (
            <>
              <span className="campus-desktop-only">随时可以切到别的标签；按 Esc 收起。</span>
              <span className="campus-touch-only">点「收起」回到计时卡；切去别处，计时也不会停。</span>
            </>
          )}
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
        {/* 只在竖屏且整屏时出现：横过来才是这块视图最好的形状，而旋转只能由用户自己转 */}
        <p className="campus-stage-rotate">横过来更沉浸</p>
      </div>
    </section>
  );
}
