/**
 * 待办可选的图标。**一处定义，两处用**：
 *  - 待办页的新建/修改抽屉（`reminders/page.tsx`）
 *  - 语音整理结果里逐条改图标（`reminders/AiCaptureDrawer.tsx`）
 *
 * 为什么只有 12 个而不是把 `CornIcon` 全部 32 个铺开：一屏摆开选择器就变成
 * 调色盘，用户会开始"随便点一个"，结果和自动猜标题没区别，还多花三秒。
 * 这 12 个按校园高频场景挑：作业 / 课程 / 考试 / 社团 / 生活 / 写作 /
 * 想法 / 重要 / 提醒 / 日程 / 买东西 / 实习。
 *
 * ★ 这里的名字必须存在于 `CornIcon` 的 `ICONS` 里，否则渲染成玉米。
 *   加图标时先往 `CornIcon` 加，再回来加进这张表。
 */
import type { CornIconName } from "@/components/CornIcon";

export const MARK_PICKS: { name: CornIconName; label: string }[] = [
  { name: "notes", label: "作业" },
  { name: "book", label: "课程" },
  { name: "bolt", label: "考试" },
  { name: "target", label: "社团" },
  { name: "home", label: "生活" },
  { name: "pen", label: "写作" },
  { name: "bulb", label: "想法" },
  { name: "star", label: "重要" },
  { name: "bell", label: "提醒" },
  { name: "calendar", label: "日程" },
  { name: "store", label: "买东西" },
  { name: "briefcase", label: "实习" },
];

/** 图标名 → 中文，给 `aria-label` / `title` 用（图上一律不写字）。 */
export const MARK_LABEL: Record<string, string> = Object.fromEntries(
  MARK_PICKS.map((p) => [p.name, p.label])
);
