"use client";

// 账号自助三个弹窗（头像 / 密码 / 登录名）共用的外壳。
//
// 为什么抽出来：三个弹窗的"容易做错的部分"完全一样 —— portal 到 body、
// 打开时把焦点送进来、Esc 关闭、关闭后把焦点还给触发按钮、Tab 不许跑到背后
// 的页面上。这些细节每写一遍都会漏一处（本项目在 FeedbackModal / GuideModal
// 上各踩过一次），所以只保留一份实现，三个弹窗只写"内容 + 按钮"。
//
// 与 GuideModal 的一个刻意差异：**多模态共存时让路**。两个弹窗同时开着时，
// 各自的 focusin 守卫会互相把焦点抢回来，实测会直接
// "Maximum call stack size exceeded" 白屏。所以焦点被另一个 role=dialog
// 拿走时本组件不抢。
import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** SSR 阶段没有 document，用 getServerSnapshot 给 false，避免水合前后不一致 */
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

/** 可聚焦元素。Tab 环与"打开后先聚焦谁"都用它 */
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface Props {
  open: boolean;
  title: string;
  /** 一句话说明。这里也是"告诉用户后果"的位置（例如改密码会让其它设备下线） */
  desc?: ReactNode;
  /** 关闭时调用。提交中（busy）时 Esc 不生效，避免半途中断请求 */
  onClose: () => void;
  /** 提交中：禁用 Esc，按钮由调用方自己置灰 */
  busy?: boolean;
  children: ReactNode;
  /** 底部按钮区 */
  footer: ReactNode;
}

export default function DialogShell({
  open,
  title,
  desc,
  onClose,
  busy = false,
  children,
  footer,
}: Props) {
  const cardRef = useRef<HTMLDivElement>(null);
  /** 触发本次弹窗的元素。关闭后把焦点还给它，键盘用户不会被扔回页面顶部 */
  const openerRef = useRef<HTMLElement | null>(null);
  const canPortal = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  // 用 ref 存这两个变化频繁的值：放进下面的 effect 依赖会让 effect 重跑，
  // 重跑就会重新抓 opener、重新抢焦点 —— 表现为"打字打到一半光标跳走"。
  // 在 effect 里同步（而不是渲染期间赋值）：渲染期间写 ref 会破坏 React 的
  // 可中断渲染假设，react-hooks/refs 会直接报错。
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  useEffect(() => {
    closeRef.current = onClose;
    busyRef.current = busy;
  }, [onClose, busy]);

  useEffect(() => {
    if (!open) return;

    // 必须在这一刻抓：本组件常驻挂载（open=false 时返回 null），
    // 拿首次渲染时的 activeElement 是过期的
    openerRef.current = document.activeElement as HTMLElement | null;

    // 延到下一个宏任务再聚焦：此时 portal 内容已插入 DOM。
    // 优先 [data-autofocus]，没有就聚焦第一个可聚焦元素 ——
    // 表单弹窗因此可以把光标直接放进第一个输入框。
    const t = window.setTimeout(() => {
      const card = cardRef.current;
      if (!card) return;
      const el =
        card.querySelector<HTMLElement>("[data-autofocus]") ||
        card.querySelector<HTMLElement>(FOCUSABLE);
      el?.focus();
    }, 0);

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || busyRef.current) return;
      // 捕获阶段拦下：避免同时开着的公告页/引导页也收到 Esc 各关一次
      e.stopPropagation();
      closeRef.current();
    };

    const onFocusIn = (e: FocusEvent) => {
      const card = cardRef.current;
      if (!card) return;
      const target = e.target as Node | null;
      if (target && card.contains(target)) return;
      // 焦点被另一个弹窗拿走了：让它去。两边互抢会递归到爆栈
      if (target instanceof Element && target.closest('[role="dialog"]')) return;
      card.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    };

    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusin", onFocusIn);
      // 关闭后还焦点。元素可能已经卸载（例如整页跳转），所以要 try
      try {
        openerRef.current?.focus?.();
      } catch {
        /* 触发元素已不在 DOM 里，忽略 */
      }
    };
  }, [open]);

  if (!open || !canPortal) return null;

  return createPortal(
    // 点遮罩**不关**：这是表单弹窗，用户可能刚输了一半密码，
    // 误触空白就清空的代价比"必须点取消"大。关闭走 Esc 或底部按钮，都是显式动作。
    <div className="acct-mask">
      <div
        className="acct-dlg"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={cardRef}
      >
        <div className="acct-dlg-head">
          <h2 className="acct-dlg-title">{title}</h2>
          {desc && <p className="acct-dlg-desc">{desc}</p>}
        </div>
        <div className="acct-dlg-body">{children}</div>
        <div className="acct-dlg-foot">{footer}</div>
      </div>
    </div>,
    document.body,
  );
}
