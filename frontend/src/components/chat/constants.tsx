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

// 首屏功能入口：替代旧「查资料/联网搜索/数学计算/查天气」四能力卡，
// 改为反映当前真实功能（模板广场 / 校园工具集 / 上传建库 / 研究）
// action 由 ChatArea 接管：template→开模板广场、tools→开工具集页、
// upload→开资料库面板、research→预填研究引导
export const INTRO_ENTRIES: {
  key: "template" | "tools" | "upload" | "research";
  label: string;
  hint: string;
  icon: CornIconName;
}[] = [
  { key: "template", label: "模板广场", hint: "挑一个场景化智能体", icon: "store" },
  { key: "tools", label: "校园工具集", hint: "GPA · 番茄钟 · 课表…", icon: "tools" },
  { key: "upload", label: "上传资料建库", hint: "让苞米读你的文件", icon: "notes" },
  { key: "research", label: "让它去研究", hint: "带题目去联网深挖", icon: "bulb" },
];

// 场景快选：首屏点一下进入对应场景并预填一句开场白（轻量版仅前端保存）
export const SCENARIO_STARTERS: Record<string, string> = {
  kaoyan: "帮我梳理这一章的考点和易错点",
  notes: "把我的笔记提炼成要点清单",
  paper: "给我一个论文的论证结构大纲",
};

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

// ============ 场景预设：考研复习 / 课程笔记 / 论文写作 ============
import type { ScenarioId } from "@/lib/types";

export const SCENARIOS: { id: ScenarioId; label: string; desc: string }[] = [
  { id: "kaoyan", label: "考研复习", desc: "知识点讲解 · 真题自测 · 错题复盘" },
  { id: "notes", label: "课程笔记", desc: "笔记整理 · 要点提炼 · 自动出题" },
  { id: "paper", label: "论文写作", desc: "学术规范 · 结构搭建 · 文献引用" },
];

export const scenarioLabel = (id: string | null | undefined) =>
  SCENARIOS.find((s) => s.id === id)?.label ?? null;

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
