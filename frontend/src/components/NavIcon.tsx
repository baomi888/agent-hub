/**
 * 导航图标：单色线性，一套 24×24 网格。
 *
 * 为什么不用 CornIcon：
 *   CornIcon 是双色插画（金色实块 + 纸白挖空 + 墨色描边），在 40px 以上很可爱，
 *   但导航里要的是 18–22px。那个尺寸下金色实块和纸白挖空糊成一坨，
 *   四个固有色互相打架，`notes` 与 `calendar` 在 12px 时根本分不出是两张图。
 *   导航图标的任务只有一个：**在一列里被一眼区分**，不是好看。
 *
 * 所以这里另起一套：24 网格、1.7 描边、只用 currentColor、零填充。
 * 12 个形状的轮廓刻意拉开距离（方的/圆的/折线的/带勾的），
 * 缩到 18px 也认得出来。
 */
import type { JSX } from "react";

export type NavIconName =
  | "chat"
  | "book"
  | "forum"
  | "task"
  | "clock"
  | "summary"
  | "star"
  | "globe"
  | "path"
  | "tools"
  | "settings"
  | "flow"
  | "dashboard"
  | "more";

type Props = {
  name: string;
  className?: string;
};

const S = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.85,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/* 对话：圆角气泡 + 左下尾巴 */
function Chat() {
  return (
    <g {...S}>
      <path d="M20 12.5c0 3.6-3.6 6.5-8 6.5-1 0-2-.15-2.9-.43L4.5 20l1.1-3.3C4.6 15.5 4 14.1 4 12.5 4 8.9 7.6 6 12 6s8 2.9 8 6.5Z" />
      <path d="M9 12.4h.01M12 12.4h.01M15 12.4h.01" strokeWidth={2} />
    </g>
  );
}

/* 知识库：摊开的一本书（单轮廓 + 中缝）。
   早先画成"左右两页各带一条竖边"，缩到 18px 就只剩两根竖线，
   连自己都认不出是书 —— 小尺寸的图标必须靠**外轮廓**认，不能靠内部细节。 */
function BookNav() {
  return (
    <g {...S}>
      <path d="M12 7.4c-1.7-1.5-4-2-6.5-1.5v11c2.5-.5 4.8 0 6.5 1.5 1.7-1.5 4-2 6.5-1.5v-11c-2.5-.5-4.8 0-6.5 1.5Z" />
      <path d="M12 7.4v11" />
    </g>
  );
}

/* 待办：勾选框 + 两条内容线（和「总结」的日历形状完全不同的方向性） */
function TaskNav() {
  return (
    <g {...S}>
      <rect x="4" y="4.5" width="9.5" height="9.5" rx="2.4" />
      <path d="M6.4 9.2 8.2 11l3.1-3.3" />
      <path d="M16.5 7.5H20M16.5 11.5H20M4.8 17.2h11.2M4.8 20h7.6" />
    </g>
  );
}

/* 每日委托：闹钟（带两个铃，和普通时钟区分） */
function ClockNav() {
  return (
    <g {...S}>
      <circle cx="12" cy="13" r="6.6" />
      <path d="M12 10.2V13l2 1.3M5 4.6 3.4 6.2M19 4.6l1.6 1.6" />
    </g>
  );
}

/* 总结：折线在最上、日历框在下（一眼是「回顾一段时间」） */
function SummaryNav() {
  return (
    <g {...S}>
      <rect x="4" y="5.5" width="16" height="14" rx="2.6" />
      <path d="M4 9.5h16M8 3.6v3.4M16 3.6v3.4" />
      <path d="M7.6 16.2l2.6-2.6 2.2 2.2 3.4-3.6" />
    </g>
  );
}

/* 打卡：圆章里的星（比裸露的五角星更像"盖一个章"） */
function StarNav() {
  return (
    <g {...S}>
      <circle cx="12" cy="12" r="8" />
      <path
        d="M12 7.6l1.35 2.74 3.02.44-2.18 2.13.51 3.01L12 14.5l-2.7 1.42.51-3.01-2.18-2.13 3.02-.44Z"
        strokeWidth={1.4}
      />
    </g>
  );
}

/* 四六级：地球（横纬线 + 一条竖经线） */
function GlobeNav() {
  return (
    <g {...S}>
      <circle cx="12" cy="12" r="8" />
      <path d="M4 12h16" />
      <path d="M12 4c2.2 2.3 3.3 5 3.3 8s-1.1 5.7-3.3 8c-2.2-2.3-3.3-5-3.3-8s1.1-5.7 3.3-8Z" />
    </g>
  );
}

/* 学习路径：折线节点（三个点 + 连起来的台阶） */
function PathNav() {
  return (
    <g {...S}>
      <path d="M4 18h4.5a2.5 2.5 0 0 0 2.5-2.5v-3A2.5 2.5 0 0 1 13.5 10H16a2 2 0 0 0 2-2V6" />
      <circle cx="4" cy="18" r="1.6" />
      <circle cx="18" cy="6" r="1.6" />
    </g>
  );
}

/* 工具集：扳手（开口朝右上） */
function ToolsNav() {
  return (
    <g {...S}>
      <path d="M14.5 9.5a3.8 3.8 0 0 1 5.1-3.6l-2.3 2.3 1.5 1.5 2.3-2.3a3.8 3.8 0 0 1-4.9 4.9l-6.4 6.4a2 2 0 0 1-2.8-2.8l6.4-6.4a3.8 3.8 0 0 1 1.1-2.3Z" />
    </g>
  );
}

/* 设置：三条滑杆 + 三个旋钮。
   试过齿轮：24 网格里画 8 个齿，缩到 19px 就糊成一颗太阳。滑杆是横向的，
   小尺寸下靠"三横 + 三个错开的点"就能认，和竖排的「知识库」也不会撞。 */
function SettingsNav() {
  return (
    <g {...S}>
      <path d="M4 7.6h3.4M11.4 7.6H20" />
      <path d="M4 12h8.6M16.6 12H20" />
      <path d="M4 16.4h1.6M9.6 16.4H20" />
      <circle cx="9.4" cy="7.6" r="1.9" />
      <circle cx="14.6" cy="12" r="1.9" />
      <circle cx="7.6" cy="16.4" r="1.9" />
    </g>
  );
}

/* 工作流：三个节点、两条连线 */
function FlowNav() {
  return (
    <g {...S}>
      <rect x="3.6" y="9.6" width="5.6" height="5" rx="1.6" />
      <rect x="14.8" y="3.8" width="5.6" height="5" rx="1.6" />
      <rect x="14.8" y="15.4" width="5.6" height="5" rx="1.6" />
      <path d="M9.2 12h2.4a1.6 1.6 0 0 0 1.6-1.6V8.9a1.6 1.6 0 0 1 1.6-1.5M9.2 12.2h2.4a1.6 1.6 0 0 1 1.6 1.6v1.5a1.6 1.6 0 0 0 1.6 1.5" />
    </g>
  );
}

/* 看板：三根柱子 */
function DashboardNav() {
  return (
    <g {...S}>
      <path d="M4 20h16" />
      <rect x="6" y="11" width="3.4" height="6" rx="1.2" />
      <rect x="11.4" y="6.4" width="3.4" height="10.6" rx="1.2" />
      <rect x="16.8" y="9" width="3.4" height="8" rx="1.2" />
    </g>
  );
}

/* 更多：2×2 圆角方块 */
function MoreNav() {
  return (
    <g {...S} strokeWidth={1.6}>
      <rect x="4.4" y="4.4" width="6" height="6" rx="1.8" />
      <rect x="13.6" y="4.4" width="6" height="6" rx="1.8" />
      <rect x="4.4" y="13.6" width="6" height="6" rx="1.8" />
      <rect x="13.6" y="13.6" width="6" height="6" rx="1.8" />
    </g>
  );
}

/* 论坛：上下两块版块条（左方块 + 右两条线）。
   刻意不用"对话气泡"——那和 chat 在 18px 下糊成同一个轮廓，
   而导航图标唯一的任务是"在一列里被一眼区分" */
function ForumNav() {
  return (
    <g {...S}>
      <rect x="4" y="4.6" width="16" height="6.6" rx="2" />
      <rect x="4" y="13.4" width="16" height="6" rx="2" />
      <path d="M7.6 7.9h3.4M7.6 16.4h6" strokeWidth={1.6} />
    </g>
  );
}

const ICONS: Record<NavIconName, () => JSX.Element> = {
  chat: Chat,
  book: BookNav,
  forum: ForumNav,
  task: TaskNav,
  clock: ClockNav,
  summary: SummaryNav,
  star: StarNav,
  globe: GlobeNav,
  path: PathNav,
  tools: ToolsNav,
  settings: SettingsNav,
  flow: FlowNav,
  dashboard: DashboardNav,
  more: MoreNav,
};

/** 未知名字回退到「更多」的四格，至少不会变成一个空白格。 */
export default function NavIcon({ name, className = "h-[18px] w-[18px]" }: Props) {
  const Draw = ICONS[name as NavIconName] ?? MoreNav;
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      width="100%"
      height="100%"
      style={{ display: "block" }}
      aria-hidden="true"
      focusable="false"
    >
      <Draw />
    </svg>
  );
}
