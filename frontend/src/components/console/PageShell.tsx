"use client";

// 控制台页面的**页头 + 内容**（不含侧栏，侧栏在 app/(console)/layout.tsx）。
//
// 一个页面长什么样，取决于页头说什么、内容怎么排；侧栏是"在哪儿"，
// 它不该随页面重建 —— 这就是把两者拆开的原因（详见 ConsoleLayout.tsx 顶部注释）。
import type { ReactNode } from "react";

interface Props {
  /** 眉题（设计稿里的 P4 · /delegate 那一行小字） */
  eyebrow: string;
  title: string;
  sub: ReactNode;
  /** 页头右侧的操作区 */
  actions?: ReactNode;
  /** 内容区最大宽度：需要阅读栏宽度的页面（如总结）传窄一点 */
  width?: number;
  children: ReactNode;
}

export default function PageShell({ eyebrow, title, sub, actions, width, children }: Props) {
  return (
    <div className="pg-wrap" style={width ? { maxWidth: width } : undefined}>
      <div className="pg-head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
          <p className="pg-sub">{sub}</p>
        </div>
        {actions ? <div className="pg-actions">{actions}</div> : null}
      </div>
      <div className="pg-body">{children}</div>
    </div>
  );
}
