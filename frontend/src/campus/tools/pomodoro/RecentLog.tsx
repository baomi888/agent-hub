"use client";

/**
 * 最近记录列表（原「最近记录」卡片的内容）。
 *
 * 从容器里搬出来只为一件事：它现在长在入口下方的展开区里，
 * 展开区比原来那张卡高得多，显示条数可以放宽（原来只给 5 条，是为了不让卡片变长）。
 * 排序仍是「最新的在上」，与后台记录顺序无关 —— sessions 存的是追加顺序。
 */

import { humanizeSeconds } from "../../logic/timer";
import type { FocusSession } from "../../types";

/** 展开区里大约能放下这些行，再多就跟着页面一起滚 */
const MAX_ROWS = 50;

interface RecentLogProps {
  sessions: readonly FocusSession[];
}

export default function RecentLog({ sessions }: RecentLogProps) {
  const rows = sessions.slice(-MAX_ROWS).reverse();

  if (rows.length === 0) {
    return (
      <div className="campus-pomo-empty">
        <p>还没有记录。</p>
        <p className="campus-card-note">完整走完一轮（至少 30 秒）就会写一条，这里按时间倒序排。</p>
      </div>
    );
  }

  return (
    <>
      <ul className="campus-pomo-log">
        {rows.map((item) => (
          <li key={item.id} className="campus-pomo-log-item">
            <span className="campus-pomo-log-time">
              {new Date(item.startedAt).toLocaleString("zh-CN", {
                month: "numeric",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span className="campus-pomo-log-name" title={item.taskName}>
              {item.taskName}
            </span>
            <span className="campus-pomo-log-cat">{item.category}</span>
            <span className="campus-pomo-log-dur">{humanizeSeconds(item.seconds)}</span>
          </li>
        ))}
      </ul>

      {sessions.length > MAX_ROWS && (
        <p className="campus-card-note campus-pomo-fold-foot">
          只显示最近 {MAX_ROWS} 条，共 {sessions.length} 条。完整记录在「专注统计」里按区间看。
        </p>
      )}
    </>
  );
}
