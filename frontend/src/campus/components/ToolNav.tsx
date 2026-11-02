"use client";

/**
 * 板块工具导航。
 *
 * 工具清单是板块的「目录」，新增工具必须同时改这里和 ROADMAP.md，
 * 否则会出现「代码写了但入口没开」的半成品。
 *
 * migrated = false 表示该工具仍是旧静态页的实现，新页面只放占位说明。
 * 这样迁移进度一眼可见，也不会假装能用。
 */

import type { ToolId, ToolMeta } from "../types";

export const TOOL_LIST: readonly ToolMeta[] = [
  {
    id: "gpa",
    label: "GPA 分析",
    hint: "录入成绩，算加权绩点",
    // 图标一律：17×17、描边随文字色（currentColor）、只有小面积点缀用固定色，
    // 这样选中态（金色文字）和深色主题都能自然跟上。
    icon: (
      <svg
        className="campus-tab-ico"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M4 21V14" />
        <path d="M10 21V9" />
        <path d="M16 21V5" />
        <path d="M3 21h18" />
        <path d="M16 5l1-1.7 1.7 1-1 1.7z" fill="#F5B840" strokeWidth={1.1} />
      </svg>
    ),
    migrated: true,
  },
  {
    id: "pomodoro",
    label: "番茄钟",
    hint: "25 分钟专注计时",
    icon: (
      <svg
        className="campus-tab-ico"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="14.5" r="7" fill="#EF8A56" fillOpacity={0.85} />
        <path d="M12 7.5c0-2 1.6-3 3.2-3" stroke="#7C9A54" />
        <path d="M12 7.5c-1.4-1-3-1-4 0" stroke="#7C9A54" />
        <path d="M9.4 13.6c2.6 1.6 5.2 0 6.6-.3" strokeWidth={1.4} />
      </svg>
    ),
    migrated: true,
  },
  {
    id: "schedule",
    label: "课程表",
    hint: "周视图课表与冲突检测",
    icon: (
      <svg
        className="campus-tab-ico"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="4" y="5" width="16" height="16" rx="3" fill="#FDFAF2" />
        <path d="M4 10h16" />
        <path d="M8 3v3M16 3v3" />
        <path d="M8 14h3" stroke="#C8892A" strokeWidth={2.4} />
        <path d="M13 14h4" stroke="#7C9A54" strokeWidth={2.4} />
      </svg>
    ),
    migrated: true,
  },
  {
    id: "words",
    label: "单词卡",
    hint: "四六级词卡与复习队列",
    icon: (
      <svg
        className="campus-tab-ico"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path
          d="M12 6.5C9 4.5 5 4.5 3 5.2v12.6c2-.7 6-.7 9 1.2 3-1.9 7-1.9 9-1.2V5.2c-2-.7-6-.7-9 1.3Z"
          fill="#FDFAF2"
        />
        <path d="M12 6.5v12.5" />
        <path d="M6 9h3M15 9h3M6 12h3M15 12h3" strokeWidth={1.3} />
      </svg>
    ),
    migrated: true,
  },
  {
    id: "deadline",
    label: "倒计时",
    hint: "截止日期与剩余天数",
    icon: (
      <svg
        className="campus-tab-ico"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M6 4h12M6 20h12" />
        <path d="M7 4l4.5 8L7 20M17 4l-4.5 8L17 20" fill="#F5B840" fillOpacity={0.12} />
        <path d="M10.5 12l1.5 4 1.5-4z" fill="#EF8A56" />
      </svg>
    ),
    migrated: true,
  },
  {
    id: "draw",
    label: "群抽签",
    hint: "名单随机抽取",
    icon: (
      <svg
        className="campus-tab-ico"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M8.5 9h7l-1 11h-5z" fill="#FDFAF2" />
        <path d="M8.5 9h7" strokeWidth={2.6} />
        <path d="M11 9V4.5M13 9V3.5M12 9V5" stroke="#7C9A54" />
        <circle cx="13.2" cy="3.2" r="1.5" fill="#F5B840" />
      </svg>
    ),
    migrated: true,
  },
];

interface ToolNavProps {
  active: ToolId;
  onChange: (id: ToolId) => void;
}

export default function ToolNav({ active, onChange }: ToolNavProps) {
  return (
    <div className="campus-tabs" role="tablist" aria-label="校园工具切换">
      {TOOL_LIST.map((tool) => (
        <button
          key={tool.id}
          type="button"
          role="tab"
          aria-selected={tool.id === active}
          aria-controls={`campus-panel-${tool.id}`}
          data-migrated={tool.migrated}
          className={`campus-tab${tool.id === active ? " is-active" : ""}`}
          onClick={() => onChange(tool.id)}
        >
          {tool.icon}
          {tool.label}
        </button>
      ))}
    </div>
  );
}
