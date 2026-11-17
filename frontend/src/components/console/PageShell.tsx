"use client";

// 控制台页面的**页头 + 内容**（不含侧栏，侧栏在 app/(console)/layout.tsx）。
//
// 一个页面长什么样，取决于页头说什么、内容怎么排；侧栏是"在哪儿"，
// 它不该随页面重建 —— 这就是把两者拆开的原因（详见 ConsoleLayout.tsx 顶部注释）。
import type { ReactNode } from "react";
import InfoNote from "./InfoNote";

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
   *
   * ★ 2026-11-17 全站清账：一句「这页是干什么的」都别再写。
   *   成熟的 App 不会在标题下面解释自己 —— 侧栏高亮、标题、内容已经说完了。
   *   现在只剩两页还传 sub，理由都是**用户会因此做错决定**：
   *   settings/reminder（安卓在国内收不到推送）、dashboard（管理员口径）。
   *   不传 sub 时页头走 `pg-head--solo`：标题放大到 --fs-page，独自占住这条带，
   *   否则一个 18px 的词挂在一整条空白上，像漏加载了副标题。
   */
  sub?: ReactNode;
  /**
   * 说明文里「首次进入该听一次」的那部分，收进 ⓘ（2026-11-16）。
   *
   * 传了 detail 之后，sub 被当作常驻的一句话主张（≤20 字），detail 按需展开。
   * sub 只负责"现在就该知道的那一句"，detail 负责口径与后果的完整表述 ——
   * 两者不要互相重复，否则展开等于白展开。
   */
  detail?: ReactNode;
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
  detail,
  actions,
  width,
  compact,
  children,
}: Props) {
  // 页头三种形态：
  //   · 有 sub       → 常规：标题 20px + 一句说明（只有上面那两页还用）
  //   · 没有 sub     → pg-head--solo：标题放大到 --fs-page 独撑页头
  //   · compact      → 窄屏省高度（标题压到 18px 的旧规则已被 solo 覆盖）
  const solo = !sub && !detail;
  const headClass = ["pg-head", solo ? "pg-head--solo" : "", compact ? "pg-head--compact" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    // 限宽页面居中：侧栏占了左边一块，内容靠左贴会留出一条大右空白
    <div
      className="pg-wrap"
      style={width ? { maxWidth: width, marginInline: "auto" } : undefined}
    >
      <div className={headClass}>
        <div>
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h2>{title}</h2>
          {sub && detail ? (
            <InfoNote claim={sub} detail={detail} />
          ) : sub ? (
            <p className="pg-sub">{sub}</p>
          ) : null}
        </div>
        {actions ? <div className="pg-actions">{actions}</div> : null}
      </div>
      <div className="pg-body">{children}</div>
    </div>
  );
}
