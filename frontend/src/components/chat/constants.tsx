// 对话区静态常量：首屏示例、工具元数据、能力入口
// 从 ChatArea.tsx 拆出（2026-09 重构），纯数据 + 图标，无逻辑
import type { ReactNode } from "react";
import type { CornIconName } from "@/components/CornIcon";

// 四档模式（MODE_LABELS）已于 2026-10-31 批 B 删除：模式条下架，能力由
// 输入区上方的两枚开关（检索 / 智能联网）直接表达。留在这里只会让
// 「哪些地方还在讲四档」变得看不出来。

// 首屏示例：三条都是「现在就能点、点了就有结果」的问题，
// 反映当前主力能力（资料库 / 场景化写作 / 出题），不再强调旧的四类工具
export const EXAMPLES: { hint: string; q: string }[] = [
  { hint: "理资料", q: "帮我把这份笔记整理成知识卡片" },
  { hint: "写论文", q: "给我搭一个论文的三级提纲" },
  { hint: "出题目", q: "根据课程笔记，给我出 3 道自测题" },
];

// 校园工具集静态页地址（frontend/public/tools.html，无需登录即可用）
export const TOOLS_URL = "/tools.html";

// 首屏入口（10-14 重排为「主次两层」）。
//
// 为什么拆两层：原来是一个四宫格（模板广场 / 校园工具集 / 上传建库 / 让它去研究），
// 四件平级 —— 但其中三件是**一次性动作**，而「每日委托 / 每日总结 / 打卡」才是
// 知穗区别于通用助手的部分（持续陪着你，而不是答完就走）。平级摆在一起，
// 差异化功能反而没有存在感。所以：
//   主线 = 三个持续陪伴功能（各自是独立页面，用 Link 跳）
//   辅路 = 三个一次性动作（留在首屏，但缩小成一行）
export const PRIMARY_ENTRIES: {
  href: string;
  label: string;
  hint: string;
  icon: CornIconName;
}[] = [
  // ★ 说明文字控制在 9 字以内（约 108px）。桌面三列时单卡 217px，
  //   44px 图标 + 两侧 13px 内边距 + 箭头之后只剩约 118px 给文字，
  //   10 字（120px）就会折行并把「知穗」「问答」这类词拆到两行。
  { href: "/delegate", label: "每日委托", hint: "定好时间，知穗去做", icon: "clock" },
  { href: "/report", label: "每日总结", hint: "把今日问答压成结论", icon: "calendar" },
  { href: "/checkin", label: "打卡记录", hint: "把学到的分享出去", icon: "star" },
];

// 辅路动作。tools → 开校园工具集静态页、upload → 开资料库上传、
// research → 预填研究引导（由 ChatArea 的 handleIntroEntry 接管）
export const SECONDARY_ENTRIES: {
  key: "template" | "tools" | "upload" | "research";
  label: string;
  icon: CornIconName;
}[] = [
  { key: "template", label: "模板广场", icon: "store" },
  { key: "upload", label: "上传资料建库", icon: "notes" },
  { key: "research", label: "让它去研究", icon: "bulb" },
  { key: "tools", label: "校园工具集", icon: "tools" },
];

// 场景快选（SCENARIO_STARTERS）已随「场景选择撤出主界面」删除（10-14 晚）：
// 场景化入口统一走模板广场，工作台不再摆场景选择器。

// ============ 工具调用：名称 → 中文标签 + 图标 ============
export const TOOL_META: Record<string, { label: string; icon: ReactNode }> = {
  kb_search: {
    label: "知识库检索",
    icon: (
      <svg viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="11" cy="11" r="7" />
        <path d="M21 21l-4.3-4.3" />
      </svg>
    ),
  },
  web_search: {
    label: "联网搜索",
    icon: (
      <svg viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3a14 14 0 0 1 3.5 9A14 14 0 0 1 12 21a14 14 0 0 1-3.5-9A14 14 0 0 1 12 3z" />
      </svg>
    ),
  },
  // calculator 工具已在后端删除（2026-10-30 统一入口，agent/tools.py 不再注册），
  // 但**展示映射保留**：老对话的气泡里存着 tool="calculator"，删掉这条会让历史记录
  // 显示成英文名 "calculator"。这张表是"名称→中文标签"的字典，不是可用工具清单 ——
  // 用户能不能用由后端决定，这里只负责把历史记录读得像人话。
  calculator: {
    label: "数学计算",
    icon: (
      <svg viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="4" y="2" width="16" height="20" rx="2" />
        <path d="M8 6h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01" />
      </svg>
    ),
  },
  get_weather: {
    label: "天气查询",
    icon: (
      <svg viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
      </svg>
    ),
  },
};

export const toolLabel = (name: string) => TOOL_META[name]?.label ?? name;

// 场景预设（SCENARIOS / scenarioLabel）已随「场景选择撤出主界面」删除（10-14 晚）：
// 原来的考研复习 / 课程笔记 / 论文写作三选一，与模板广场的同名模板重复，
// 且覆盖不到「求职」这类主打场景 —— 场景化入口统一收归模板广场。
// 后端 core/scenarios.py 的 prompt 机制保留（旧前端兼容 + 模板联动可复用）。

// 首屏四宫格（ABILITIES 四卡）已于 2026-10-31 批 B 删除：
// 它和顶栏「能力」弹层是同一份数据，两者一起下架。用户要什么能力，
// 现在由输入区上方的两枚开关直接回答（检索 / 智能联网），
// 天气与算术这类具体工具交给模型在会话里自己调。
