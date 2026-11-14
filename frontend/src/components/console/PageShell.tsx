"use client";

// 控制台页面的**页头 + 内容**（不含侧栏，侧栏在 app/(console)/layout.tsx）。
//
// 一个页面长什么样，取决于页头说什么、内容怎么排；侧栏是"在哪儿"，
// 它不该随页面重建 —— 这就是把两者拆开的原因（详见 ConsoleLayout.tsx 顶部注释）。
import type { ReactNode } from "react";

interface Props {
  /**
   * 眉题（标题上方那行小字）。**默认不传**。
   *
   * 2026-11-14 全站文案审查后只剩 admin 那两个页面（dashboard / flow）配了
   * 「管理 · 内部」，它标记"这里是调试面"——这件事布局表达不出来。
   * 其余 11 页原来那句（「校园生活」「你的偏好」「每日总结」…）都在替
   * 侧栏高亮和标题重说一遍，已全部删除。
   * 加新页面时先问一句：这句话布局说得出来吗？说得出来就别加。
   */
  eyebrow?: string;
  title: string;
  /**
   * 标题下面那句说明。**只有它承载布局看不见的事实时才传**：
   * 口径、限制、后果、隐私边界。
   *
   * 删之前先反问：去掉这句，用户会不会做一个错误的决定？
   * 会 → 它是 R2，留，但一屏只准出现一次；
   * 不会（只是在把下面几个分区 / 几张卡念一遍）→ 它就是 R1，别写。
   */
  sub?: ReactNode;
  /** 页头右侧的操作区 */
  actions?: ReactNode;
  /** 内容区最大宽度：需要阅读栏宽度的页面（如总结）传窄一点 */
  width?: number;
  /**
   * 手机上把页头压扁：说明收成最多两行、标题压到 18px。
   *
   * 只在窄屏生效，桌面完全不变。默认 false：别的页面没量过
   * 「省下来能不能换来一条内容」，不跟着动。
   * （原来它还会藏掉眉题，2026-11-14 眉题全站退场后那段规则已无对象，一并删了。）
   */
  compact?: boolean;
  children: ReactNode;
}

export default function PageShell({
  eyebrow,
  title,
  sub,
  actions,
  width,
  compact,
  children,
}: Props) {
  return (
    <div className="pg-wrap" style={width ? { maxWidth: width } : undefined}>
      <div className={compact ? "pg-head pg-head--compact" : "pg-head"}>
        <div>
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h2>{title}</h2>
          {sub ? <p className="pg-sub">{sub}</p> : null}
        </div>
        {actions ? <div className="pg-actions">{actions}</div> : null}
      </div>
      <div className="pg-body">{children}</div>
    </div>
  );
}
