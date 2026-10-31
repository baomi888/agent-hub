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
  { id: "gpa", label: "GPA 分析", hint: "录入成绩，算加权绩点", migrated: true },
  { id: "pomodoro", label: "番茄钟", hint: "25 分钟专注计时", migrated: false },
  { id: "schedule", label: "课程表", hint: "周视图课表与冲突检测", migrated: false },
  { id: "words", label: "单词卡", hint: "四六级词卡与复习队列", migrated: false },
  { id: "deadline", label: "倒计时", hint: "截止日期与剩余天数", migrated: false },
  { id: "draw", label: "群抽签", hint: "名单随机抽取", migrated: false },
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
          {tool.label}
        </button>
      ))}
    </div>
  );
}
