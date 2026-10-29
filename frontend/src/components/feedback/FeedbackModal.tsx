"use client";

/**
 * 首页顶栏「反馈」按钮打开的弹窗（2026-10-28 用户：主页也要一个提意见的入口，
 * 点了和在设置里提一样，开发者能看到）。
 *
 * 为什么是弹窗而不是跳去设置页：
 *   用户想提意见的那一刻，人正在对话里（刚被答错、刚发现按钮不好用）。
 *   把他送到另一个页面，中间要重新定位「说的是哪件事」，多半就不提了。
 *   设置页那份**保留**：那里是"我主动想提"，这里是"我正好遇到了"。
 *
 * 内容直接复用 FeedbackCard（chrome=false），所以：
 *   · 分类按钮仍来自后端 `/api/user-feedback/dict`
 *   · 提交后仍能看到「我提过的」和开发者回复
 *   —— 两处入口写同一份代码，不会出现"这边能提、那边看不到回复"。
 *
 * 交互取舍：
 *   · **点遮罩不关**（和 GuideModal 一致）：反馈是用户刚敲的一段话，误触空白就没了，
 *     代价比"逃不出去"大；关闭走右上角 × 或 Esc，两个都是显式动作。
 *   · 打开时焦点给第一个分类按钮而不是文本框：先让他确认"我要说的是哪一类"。
 *   · Esc 关闭时把焦点还给触发按钮 —— 键盘用户不至于被扔回页面顶部。
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import FeedbackCard from "../FeedbackCard";

/** 只在客户端 portal：SSR 没有 document，用 getServerSnapshot 给 false 避免水合不一致 */
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

interface Props {
  open: boolean;
  onClose: () => void;
  /** 记进反馈来源的页面标识（管理员看后台时能区分"首页提的"和"设置页提的"） */
  page: string;
}

export default function FeedbackModal({ open, onClose, page }: Props) {
  const cardRef = useRef<HTMLDivElement>(null);
  const canPortal = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);

  const close = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    if (!open) return;
    // 触发元素要在"刚打开"这一刻抓：本组件常驻挂载（open=false 时返回 null），
    // 拿首次渲染时的 activeElement 是过期的 —— 那时还没点按钮，抓到的是 body。
    // 这个副作用在 open 变化后紧接着跑，此刻 activeElement 仍是刚点的按钮。
    const opener = document.activeElement as HTMLElement | null;

    // 先让内容挂载出来再抢焦点（同一次 effect 里 focus 会落在还没渲染的节点上）
    const t = window.setTimeout(() => {
      const first = cardRef.current?.querySelector<HTMLElement>(
        ".fbx-body button:not([disabled]), .fbx-body textarea"
      );
      first?.focus();
    }, 0);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey, true);
      opener?.focus?.();
    };
  }, [open, close]);

  if (!canPortal || !open) return null;

  return createPortal(
    <div className="fbx-mask" role="presentation">
      <div
        ref={cardRef}
        className="fbx-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fbx-title"
        tabIndex={-1}
      >
        <div className="fbx-head">
          <span className="fbx-eyebrow">反馈</span>
          <h3 id="fbx-title" className="fbx-title">
            哪里不对，直接说
          </h3>
          <button
            type="button"
            className="fbx-close"
            onClick={close}
            title="关闭（Esc 也行）"
            aria-label="关闭反馈"
          >
            关闭
          </button>
        </div>
        <p className="fbx-sub">
          出错了、答得不对、少个想要的功能 —— 都行。<b>开发者会看到</b>
          ，处理进度和回复就在下面。
        </p>
        <div className="fbx-body">
          <FeedbackCard page={page} chrome={false} />
        </div>
        <div className="fbx-foot">
          <span>不用留联系方式；愿意被回访再留一个。</span>
        </div>
      </div>
    </div>,
    document.body
  );
}
