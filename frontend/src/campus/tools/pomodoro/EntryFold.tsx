"use client";

/**
 * 入口下方的延展区（最近记录 / 专注统计共用）。
 *
 * 为什么不用弹窗：这两块是「顺手看一眼」的内容，不是必须现在处理的事。
 * 弹窗会盖住正在跑的计时和下面的任务列表，看完还得再关一次；
 * 延展区就在按钮正下方长出来，页面不动、上下文还在，看完再点一下收回。
 *
 * 外壳只管三件事：标题行、收起按钮、展开时的淡入。
 * 业务状态一律由调用方持有 —— 收起不该丢数据，也不该停计时。
 */

import { useEffect, useRef, type ReactNode } from "react";

interface EntryFoldProps {
  title: string;
  /** 标题右侧的一句补充，例如「共 12 条」 */
  note?: string;
  /** 区域 id：入口按钮的 aria-controls 指过来，调用方传字面量保证唯一 */
  regionId: string;
  /** 给 aria-labelledby 用，同样由调用方传字面量 */
  titleId: string;
  onClose: () => void;
  children: ReactNode;
}

export default function EntryFold({ title, note, regionId, titleId, onClose, children }: EntryFoldProps) {
  const headRef = useRef<HTMLDivElement>(null);

  /*
   * 展开的内容长在按钮下方，而按钮本身常在页面底部 —— 手机上整块在视口外，
   * 看起来像"点了没反应"。所以要把标题行带进视口。
   *
   * 锚点是**标题行**，不是整块：整块可能比视口还高（统计面板 600px+），
   * 对一整块用 nearest 会把页面滚很远、把刚点的按钮顶出屏幕。
   * 标题行只有 34px，滚进来就够说明"下面开了"，剩下的用户自己滚。
   */
  useEffect(() => {
    headRef.current?.scrollIntoView({ block: "nearest" });
  }, []);

  /*
   * 收起时把焦点还给入口按钮。
   *
   * 这块是被条件渲染的，收起的那一刻「收起」按钮整个从 DOM 里消失，
   * 焦点就掉回 body —— 键盘用户想再展开一次，得从页首重新 Tab。
   * 入口按钮上有 `aria-controls={regionId}`，按这个反查最稳，不必再加一份 ref。
   *
   * 只在焦点真的掉到 body 时才抢回来：如果用户点了别处再用 Esc 收起，
   * 焦点已经在别的地方，这里不该把他拽回去。
   */
  useEffect(() => {
    return () => {
      const active = document.activeElement;
      const lostFocus = active === null || active === document.body;
      if (!lostFocus) return;

      const opener = document.querySelector<HTMLElement>(`[aria-controls="${regionId}"]`);
      opener?.focus({ preventScroll: true });
    };
  }, [regionId]);

  /*
   * Esc 收起。绑 window 而不是容器：焦点可能停在内部的导入按钮上。
   *
   * 这里不再调 `event.stopPropagation()` —— 它原本想拦住别的 Esc 监听，
   * 但监听都挂在 window 上，同一个目标上的多个监听彼此不会因为冒泡被拦掉，
   * 要拦得用 stopImmediatePropagation，而那样又会把完成弹窗的 Esc 一起废掉。
   * 两个展开区互斥，同时只会有一个在听，不存在抢键。
   */
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      onClose();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <section id={regionId} className="campus-pomo-fold" aria-labelledby={titleId}>
      <div ref={headRef} className="campus-pomo-fold-head">
        <h2 id={titleId} className="campus-pomo-fold-title">
          {title}
        </h2>
        {note && <span className="campus-pomo-fold-note">{note}</span>}
        <button type="button" className="campus-pomo-fold-close" onClick={onClose}>
          收起
        </button>
      </div>

      {children}
    </section>
  );
}
