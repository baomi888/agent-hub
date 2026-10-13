"use client";

// 新页面用到的 UI 原子件。
//
// 为什么不直接写 Tailwind 工具类：这批页面和设计稿是 1:1 对图落地的，
// 原型里已经有一套按令牌写好的类（.card/.ctag/.stat/.dele…）。散成工具类会让
// 「设计稿改一处、tsx 要改十处」；共用类名则只改 console.css。
//
// 这里只放**有行为**的组件（开关、分段控件、降级提示），纯静态块直接用类名。
import type { ReactNode } from "react";
import CornIcon from "../CornIcon";
import type { Toast } from "@/lib/hooks/useToast";

/* ---------------- Toast 容器 ---------------- */

/** 复用工作台的 .toast-container / .toast 样式，保证两个路由的提示长得一样 */
export function ToastHost({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.type}`}>
          <span>{t.msg}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------- 开关 ---------------- */

/** 用 button 而不是 span：键盘要能 Tab 到、空格要能切，这是设置页的核心控件 */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      className="switch"
      onClick={() => onChange(!checked)}
    />
  );
}

/* ---------------- 分段控件 ---------------- */

export interface SegOption<T extends string> {
  value: T;
  label: string;
  /** 悬浮说明：这几个选项光看标题看不出差别（例如频率的「间隔」） */
  hint?: string;
}

export function Seg<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  stretch,
}: {
  options: SegOption<T>[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  /** 占满整行（表单里用），默认按内容宽度 */
  stretch?: boolean;
}) {
  return (
    <div
      className="seg"
      role="group"
      aria-label={ariaLabel}
      style={stretch ? { display: "flex", width: "100%" } : undefined}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          title={o.hint}
          onClick={() => onChange(o.value)}
          style={stretch ? { flex: 1 } : undefined}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- 提示块 ---------------- */

export function Callout({
  children,
  icon = "info",
}: {
  children: ReactNode;
  icon?: string;
}) {
  return (
    <div className="callout">
      <CornIcon name={icon} />
      <div>{children}</div>
    </div>
  );
}

/**
 * 「这个接口还没上线」。
 *
 * 存在的理由：`/api/delegate`、`/api/report`、`/api/agent/tools`、`/api/schedule/*`
 * 目前只在队友的 `feat/ai-module-20261013` 分支上。页面已经按契约写好，
 * 但今天跑在本分支上会取不到数据 —— 与其显示假数据假装可用，不如**明说**，
 * 并给出下方那块演示数据的来源。分支一合，这块自己就消失了。
 */
export function NotReady({ what, note }: { what: string; note?: ReactNode }) {
  return (
    <div className="not-ready">
      <CornIcon name="warn" />
      <div>
        <span className="badge">接口待上线</span>
        {what}尚未在当前后端分支提供，页面已按契约接好，接口一上线即自动生效。
        {note ? <div style={{ marginTop: 6 }}>{note}</div> : null}
      </div>
    </div>
  );
}

/* ---------------- 骨架 ---------------- */

/** 加载占位。宽度按文案长度给，避免所有骨架一样长显得像装饰 */
export function SkeletonLines({ widths = ["80%", "100%", "60%"] }: { widths?: string[] }) {
  return (
    <div>
      {widths.map((w, i) => (
        <div key={i} className="skel" style={{ width: w, marginTop: i ? 8 : 0 }} />
      ))}
    </div>
  );
}

/* ---------------- 带标签的输入 ---------------- */

/**
 * 表单项。用 `<label>` 包住控件，而不是 `htmlFor` —— 调用处有十几个输入，
 * 逐个生成 id 既啰嗦又容易漏，包一层天然就关联上了。
 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="f" title={hint}>
        {label}
      </span>
      {children}
    </label>
  );
}
