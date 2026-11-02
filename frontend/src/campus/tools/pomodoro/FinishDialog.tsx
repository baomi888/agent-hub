"use client";

/**
 * 本轮完成的提醒弹窗。
 *
 * 只在「倒计时走到 00:00」时挂载（判定在 PomodoroTool 的 finish("finished")），
 * 手动结束 / 重置不弹 —— 那两种是用户主动收手，页面内的提示已经够了。
 *
 * 交互约定：
 *  - 「再来一轮」沿用同一任务、同一时长立刻开下一轮，手不用离开键盘；
 *  - 「先歇会」/ Esc / 点遮罩：关掉弹窗，表盘停在 00:00，等下次手动开始。
 *
 * 状态一律从父级传入，这里不持有任何业务数据 —— 弹窗只是一种显示方式，
 * 专注视图（B3）用的就是同一份状态、另一个壳。
 */

import { useEffect, useRef } from "react";

import { lockPageScroll } from "../../logic/scrollLock";

import { doneHeadline, doneSummaryText, doneTaskLabel } from "../../logic/focusDone";
import { humanizeSeconds } from "../../logic/timer";

interface FinishDialogProps {
  taskName: string;
  category: string;
  seconds: number;
  /** 含本轮在内的今日汇总 */
  todayCount: number;
  todaySeconds: number;
  onAgain: () => void;
  onClose: () => void;
}

export default function FinishDialog({
  taskName,
  category,
  seconds,
  todayCount,
  todaySeconds,
  onAgain,
  onClose,
}: FinishDialogProps) {
  const againRef = useRef<HTMLButtonElement>(null);

  // 挂载即把焦点放到主按钮：键盘用户直接回车就能开下一轮，
  // 读屏也会立刻念出弹窗标题。顺带锁住背景滚动，免得滚轮把页面带偏。
  //
  // 锁滚动走 `lockPageScroll` 而不是 `body.style.overflow`：
  // 板块页的滚动在 `.campus-scroll` 上，html/body 本来就是 overflow:hidden，
  // 改 body 一点用都没有（这条以前是失效的，0.5.5 修正）。
  //
  // 关掉时把焦点还回原处：弹窗这一层卸载后，焦点会掉到 body，
  // 键盘用户得从页首重新 Tab 才能回到计时按钮。
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    againRef.current?.focus();
    const unlock = lockPageScroll();

    return () => {
      unlock();
      /*
       * 归还焦点：优先还回打开它的那个元素；它已经不在了（完成一轮后
       * 「开始」按钮被换成了「暂停 / 结束」的那一块，弹窗关掉后才又变回来），
       * 就退到计时区的第一个按钮 —— 也就是用户下一步最可能按的「开始」。
       * 不这么做，焦点会掉到 body，键盘用户得从页首重新 Tab 回来。
       */
      const openerAlive = opener?.isConnected === true;
      const target = openerAlive ? opener : document.querySelector<HTMLElement>(".campus-pomo-btns .campus-btn");
      target?.focus({ preventScroll: true });
    };
  }, []);

  // Esc 关闭。绑在 window 上而不是卡片上：焦点有时会落在遮罩按钮上，
  // 绑在卡片里就会漏掉那一路。
  // 不调 stopPropagation —— 监听都在 window 上，同目标的多个监听拦不住彼此，
  // 真要拦得用 stopImmediatePropagation，那会把别的 Esc 一起废掉。
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      onClose();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="campus-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="campus-pomo-done-title"
      aria-describedby="campus-pomo-done-today"
    >
      <button
        type="button"
        className="campus-modal-mask"
        aria-label="关闭完成提醒"
        onClick={onClose}
      />

      <div className="campus-modal-card campus-pomo-done">
        <p className="campus-pomo-done-badge">本轮完成</p>

        <h2 id="campus-pomo-done-title" className="campus-pomo-done-title">
          {doneHeadline(new Date().getHours())}
        </h2>

        <p className="campus-pomo-done-num">{humanizeSeconds(seconds)}</p>
        <p className="campus-pomo-done-task">{doneTaskLabel(taskName, category)}</p>
        <p id="campus-pomo-done-today" className="campus-pomo-done-today">
          {doneSummaryText(todayCount, todaySeconds)}
        </p>

        <div className="campus-pomo-done-actions">
          <button ref={againRef} type="button" className="campus-btn is-primary" onClick={onAgain}>
            再来一轮
          </button>
          <button type="button" className="campus-btn" onClick={onClose}>
            先歇会
          </button>
        </div>
      </div>
    </div>
  );
}
