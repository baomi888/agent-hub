// 对话区静态常量：首屏示例、工具元数据、能力入口
// 从 ChatArea.tsx 拆出（2026-09 重构），纯数据 + 图标，无逻辑
import type { ReactNode } from "react";
import type { CornIconName } from "@/components/CornIcon";

// 四档模式（MODE_LABELS）已于 2026-10-31 批 B 删除：模式条下架，能力由
// 输入区上方的两枚开关（检索 / 智能联网）直接表达。留在这里只会让
// 「哪些地方还在讲四档」变得看不出来。

// 单次对话附件上限，与 api/chat.py 的 MAX_CHAT_FILES 对齐。
// 管理员不受这个数约束：页面层按 me.is_admin 传 undefined 表示不限。
export const MAX_ATTACHMENTS = 9;

// ============ 首屏主操作区：两组、六个「真去一个地方」的入口 ============
//
// ★ 2026-11-15 重排（用户要求）。删掉的是两样东西：
//   ① 一行的四个小胶囊（公开知识库 / 上传资料建库 / 让它去研究 / 工具集）
//   ② 三张示例卡（理资料 / 写论文 / 出题目）
//   主线从 3 项扩到 6 项：每日委托 / 每日总结 / 打卡记录 + 论坛 / 公开知识库 / 待办。
//
// 为什么删示例卡：那三张卡把「一句话就能问」演示了一遍，但点下去是**填进输入框**，
// 不是去一个地方 —— 和另外六项是两种东西，摆在一起会让人以为它们同级。
// 想演示"能问什么"，用户直接打字比点一张卡更省事。
//
// 分组：组一「每天」= 知穗替你做（这是知穗区别于通用助手的地方）；
//       组二「常去」= 你自己去翻（待办是自己排的，另两个是别人的内容）。
// ★ 两组各 3 项，是**排版前提**：三端都正好 3 列 × 2 行，没有空格子。
//   加第 7 项之前先想清楚它落在哪一组、那一组还剩几格 —— 否则又会出现
//   「最后一行孤零零一个」的老问题（侧栏这次刚修过同一个毛病）。
export type HomeAction = {
  href: string;
  label: string;
  /** 说明只写用途、不超过 9 字（约 108px）；超了桌面三列时会被截断 */
  hint: string;
  /** CornIcon 的双色插画名（不是 NavIcon 的线性名） */
  icon: CornIconName;
};

export const HOME_GROUPS: {
  key: "daily" | "visit";
  title: string;
  items: HomeAction[];
}[] = [
  {
    key: "daily",
    title: "让知穗每天做点什么",
    items: [
      { href: "/delegate", label: "每日委托", hint: "定好时间，知穗去做", icon: "clock" },
      { href: "/report", label: "每日总结", hint: "把今日问答压成结论", icon: "calendar" },
      { href: "/checkin", label: "打卡记录", hint: "把学到的分享出去", icon: "star" },
    ],
  },
  {
    key: "visit",
    title: "常去的地方",
    items: [
      // 待办原本只在侧栏/「更多」里，用户要它进主操作区 —— 它确实是每天要看的。
      { href: "/reminders", label: "待办", hint: "今天要做什么", icon: "task" },
      // 「公开知识库」= /library 那个广场（用户 2026-11-15 定的名，第 14 批改）：
      // 上一批我按内部命名纪律落了「公开资料库」，用户明确要求改回「公开知识库」，
      // 于是纪律本身改成"私人的叫资料库、广场叫公开知识库"，两词指两个东西，
      // 不再互相顶（见 `tests/test_naming_consistency.py` 的 `_ALLOWED_LIBRARY`）。
      // 左栏按钮、广场 h1、面包屑、右侧资料库面板的绑定提示同步改成同一个词。
      { href: "/library", label: "公开知识库", hint: "挑一个库，直接问", icon: "store" },
      { href: "/forum", label: "论坛", hint: "看看别人在问什么", icon: "forum" },
    ],
  },
];

// 校园工具集入口（新版 React 实现，路由 /tools，免登录）。
//
// 2026-11-02 修正：原先写死 "/tools.html"，那是 frontend/public/ 下的旧静态原型页
// （页脚自述「非线上功能」），导致 Chat 入口点进去看到的永远是旧版，
// 新版的番茄钟云同步、任务库等功能全看不到。旧页文件保留作实现对照，
// 见 campus/logic/gpa.ts 的算法对照注释；next.config.ts 里 /tools.html 已 301 到 /tools。
export const TOOLS_URL = "/tools";

// 场景快选（SCENARIO_STARTERS）已随「场景选择撤出主界面」删除（10-14 晚）：
// 场景化入口统一走公开知识库广场，工作台不再摆场景选择器。

// ============ 工具调用：名称 → 中文标签 + 图标 ============
export const TOOL_META: Record<string, { label: string; icon: ReactNode }> = {
  kb_search: {
    label: "资料库检索",
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
