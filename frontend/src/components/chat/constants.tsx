// 对话区静态常量：模式标签、首屏示例、工具元数据、能力卡数据
// 从 ChatArea.tsx 拆出（2026-09 重构），纯数据 + 图标，无逻辑
import type { ReactNode } from "react";
import type { ChatMode } from "@/lib/types";
import type { CornIconName } from "@/components/CornIcon";

export const MODE_LABELS: { value: ChatMode; label: string; hint: string }[] = [
  { value: "auto", label: "自动", hint: "绑定知识库后自动优先检索" },
  { value: "rag", label: "检索", hint: "强制从知识库检索回答" },
  { value: "agent", label: "智能体", hint: "可调用工具：联网搜索 / 计算 / 天气" },
  { value: "llm", label: "纯对话", hint: "纯大模型对话，不检索" },
  // 混合：流程型任务走工作流（步骤确定、不会漏），开放型任务交给智能体（灵活），
  // 两种模式在**一次任务内**自动换手，轨迹可见。
  // ⚠️ 后端开关关掉时会静默退回「智能体」，所以 hint 里说明了它可能不出现轨迹。
  { value: "hybrid", label: "混合", hint: "流程与智能体自动换手，过程可见" },
];

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
  { href: "/delegate", label: "每日委托", hint: "定好时间，知穗自己去做", icon: "clock" },
  { href: "/report", label: "每日总结", hint: "把今天的问答压成结论", icon: "calendar" },
  { href: "/checkin", label: "打卡记录", hint: "把学到的东西分享出去", icon: "star" },
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

// 首屏四宫格：呼应设计稿「查资料 / 联网搜索 / 数学计算 / 查天气」，
// 点一下直接切到对应模式并预填一句能立刻发的问题（不用先去顶部切模式）
export const ABILITIES: {
  mode: ChatMode;
  label: string;
  hint: string;
  example: string;
  icon: ReactNode;
}[] = [
  {
    mode: "rag",
    label: "查资料",
    hint: "从你的知识库里找答案",
    example: "帮我从资料里找一下项目的里程碑",
    icon: TOOL_META.kb_search.icon,
  },
  {
    mode: "agent",
    label: "联网搜索",
    hint: "实时搜最新的信息",
    example: "上网搜一下最近一周的 AI 行业新闻",
    icon: TOOL_META.web_search.icon,
  },
  {
    mode: "agent",
    label: "数学计算",
    hint: "算账、换算、解方程",
    example: "帮我算一下 (128 + 64) × 0.85 等于多少",
    icon: TOOL_META.calculator.icon,
  },
  {
    mode: "agent",
    label: "查天气",
    hint: "问任意城市的天气",
    example: "上海明天天气怎么样？",
    icon: TOOL_META.get_weather.icon,
  },
];
