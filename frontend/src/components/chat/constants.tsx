// 对话区静态常量：模式标签、首屏示例、工具元数据、能力卡数据
// 从 ChatArea.tsx 拆出（2026-09 重构），纯数据 + 图标，无逻辑
import type { ReactNode } from "react";
import type { ChatMode } from "@/lib/types";

export const MODE_LABELS: { value: ChatMode; label: string; hint: string }[] = [
  { value: "auto", label: "自动", hint: "绑定知识库后自动优先检索" },
  { value: "rag", label: "检索", hint: "强制从知识库检索回答" },
  { value: "agent", label: "智能体", hint: "可调用工具：联网搜索 / 计算 / 天气" },
  { value: "llm", label: "纯对话", hint: "纯大模型对话，不检索" },
];

// 首屏示例：三条都是「不用先准备任何东西」的问题——
// 用户第一次来手上没有知识库，示例必须现在就能点、点了就有结果
export const EXAMPLES: { hint: string; q: string }[] = [
  { hint: "问天气", q: "北京今天天气怎么样？" },
  { hint: "算一算", q: "帮我算一下 (12 + 34) × 5" },
  { hint: "改文案", q: "把这句话改得更客气一点：你发的邮件我没收到" },
];

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
