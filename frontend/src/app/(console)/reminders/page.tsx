"use client";

// P7 · 待办（2026-11-09 建，2026-11-10 重排，2026-11-10 晚改为「待办为主对象」）
//
// 这个页面只回答一个问题：**什么时候，让我做什么**。
// 三件事刻意不放在这里：
//  1. 推送设备（Web Push 订阅）**在这里**（2026-11-11 从设置页搬进来）。
//     当初留着它的理由是「账号级基础设施，别的模块也可能借它推」，实际用下来
//     用户只会在一个场景找它：关掉某台旧设备的提醒。让这件事发生在待办页里，
//     比维护一个「账号级基础设施」的位置更实在。
//  2. 每日委托的正文留在 /delegate —— 提醒只管"什么时候提醒你去看"，不管"写什么"；
//  3. 番茄钟、课表等工具的编辑留在工具集 —— 这里只提供「关闭该来源」这一个开关。
//
// ============ 两类对象，不要第三类（docs/提醒中心UI对标与优化方案 §13）============
//
//   待办 Task      用户创建、有 checkbox、可选 remind_at
//   联动提醒       课表/番茄钟/委托/打卡/四六级/单词/DDL/系统算出的事件，没有完成态
//
// ★ 联动提醒**不能完成**，只能推迟 / 暂停 / 关来源。给它一个 checkbox 是错的：
//   勾掉之后来源那边还在，第二天照样来，用户会认为「这个勾是假的」。
// ★ 倒计时不是第三类对象：它是 `task.due_at != null && status='open'` 的一个视图。
//   这里没有任何「新建倒计时」的入口，也没有第二套 DDL 数据。
//
// ============ due_at 与 remind_at 必须分开 ============
//
//   due_at    = 最晚什么时候交（「周五 23:59」）
//   remind_at = 什么时候提醒我（「周五 20:00」）
//
// **只设 due_at 不会响**（后端不建 linked reminder）。这句话必须在界面上写出来，
// 否则用户设完截止日期等一夜，第二天说「这个提醒坏了」。
//
// ============ 时间口径 ============
//
// 卡片上的时刻取 `next_fire_at` 而不是 `at`：`at` 是**来源值**（课表第 3 节、
// 委托原定 20:00），`override.at` 才是用户改过的。直接显示 `at` 会出现
// 「我明明改成 22:30，列表还写 20:00」——用户会当成没保存。
//
// 待办的时间一律是 Unix 秒（后端口径），展示前用 `toMs()` 转毫秒。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon, { ICON_NAMES } from "@/components/CornIcon";
import type { CornIconName } from "@/components/CornIcon";
import AiCaptureDrawer, {
  Spark,
  commitFailMessage,
  reminderDraftOf,
  taskDraftOf,
} from "@/components/reminders/AiCaptureDrawer";
import type { CommitOutcome } from "@/components/reminders/AiCaptureDrawer";
import ReminderAccess from "@/components/reminders/ReminderAccess";
import MailAccess from "@/components/reminders/MailAccess";
import CourseCalendar from "@/components/reminders/CourseCalendar";
import { Callout, Field, Seg, SkeletonLines, Switch, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { MARK_PICKS } from "@/lib/taskIcons";
import { fmtHM, fmtRemaining, toMs } from "@/lib/time";
import type {
  CaptureProposal,
  PushStatus,
  ReminderAction,
  ReminderDraft,
  ReminderFire,
  ReminderFreq,
  ReminderItem,
  ReminderPrefs,
  ReminderSource,
  ReminderSummary,
  RemindPref,
  Task,
  TaskBulkAction,
  TaskDraft,
  TaskListKey,
  TaskPatch,
  TaskPriority,
  TaskSummary,
} from "@/lib/types";

const ACTION_LABEL: Record<string, string> = {
  notify: "只提醒",
  agent_deliver: "让助手帮我写",
  suggest_focus: "开始专注",
  open_tool: "打开工具",
};

const FREQ_OPTIONS: { value: ReminderFreq; label: string }[] = [
  { value: "daily", label: "每天" },
  { value: "weekdays", label: "工作日" },
  { value: "weekly", label: "每周" },
  { value: "once", label: "仅一次" },
];

/**
 * 把任意分钟吸附到服务端的档位上（0/15/30/45）。
 *
 * 后端 `core/reminder_core._snap_time` 会把分钟就近吸附到这四档；前端先吸附一次，
 * 用户看到的值和存进去的值才一致。不吸附的话，「选了 20 分、存成 15 分」
 * 在用户那里就是「选了没生效」，而且他找不到原因。
 * 就近规则与后端逐字相同（取绝对差最小、并列时取靠前一档）。
 */
function snapMinute(daily: RemindPref, minute: number): number {
  if (daily.steps.includes(minute)) return minute;
  return daily.steps.reduce((best, s) => (Math.abs(s - minute) < Math.abs(best - minute) ? s : best));
}

/** `daily.time` → `<input type="time">` 要的 `HH:MM`（老数据可能没有前导零） */
function dailyTimeValue(daily: RemindPref): string {
  const [h, m] = daily.time.split(":").map((n) => Number(n));
  if (!Number.isFinite(h) || !Number.isFinite(m)) return "08:00";
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/**
 * 「每天几点」卡片右侧的状态。
 *
 * 「已关闭」必须说清改时间会把它重新打开：`set_system_time` 的语义就是
 * 「把这条改到这个点并且启用」（老接口 POST /api/remind 如此）。不说的话，
 * 用户关掉之后碰了一下时间，第二天又收到通知，会以为是关不掉。
 */
function dailyHint(daily: RemindPref): string {
  if (!daily.exists) return `还没设置过 · 现在按默认的 ${daily.time}`;
  if (!daily.enabled) return `已关闭 · 改时间会重新开启`;
  return `已开启 · 每天 ${daily.time}`;
}

/**
 * 每一档「提醒时做什么」到点之后**到底会发生什么**。
 *
 * 三个词原本全是行话：用户看得见「让助手帮我写」，但不知道谁会写、写什么；
 * 「开始专注」更猜不出来。下拉里塞不下解释，就紧跟在控件下面写一行 ——
 * 这是**选择依据**，不是补充说明，放在 hover 提示里等于没有（手机上根本没有 hover）。
 */
const ACTION_HINT: Record<string, string> = {
  notify: "到点发一条通知，在通知栏里显示这件事，点开回到这个页面。",
  agent_deliver: "到点把内容准备好，点通知直接看（今天的简报 / 委托）。",
  suggest_focus: "到点直接打开专注计时，不用自己再去工具集里找。",
  open_tool: "到点打开对应的工具页面。",
};

/**
 * 「让助手帮我写」只在**手里真有内容**的来源上成立：每日回顾、四六级、打卡、
 * 每日委托。自己建的提醒上没有东西可写，选它只会得到一条和「只提醒」一模一样的
 * 通知 —— 那不如不列出来，免得用户以为助手会替他写作业。
 *
 * 老数据（在那个过滤之前建的）里可能有这个组合，界面上仍要能显示与保存，
 * 见 `actionList` 的兜底那一段。
 */
const AGENT_SOURCES = new Set(["system", "cet", "checkin", "delegate"]);

const ACTION_OPTIONS: { value: ReminderAction; label: string }[] = [
  { value: "notify", label: "只提醒" },
  { value: "agent_deliver", label: "让助手帮我写" },
  { value: "suggest_focus", label: "开始专注" },
];

function actionOptionsFor(source: string): { value: ReminderAction; label: string }[] {
  return AGENT_SOURCES.has(source)
    ? ACTION_OPTIONS
    : ACTION_OPTIONS.filter((o) => o.value !== "agent_deliver");
}

const WEEK_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/** 六个清单。中文只活在前端，落库是英文键（改文案不该迁数据） */
const LIST_OPTIONS: { value: TaskListKey; label: string }[] = [
  { value: "inbox", label: "收件箱" },
  { value: "course", label: "课程" },
  { value: "homework", label: "作业" },
  { value: "club", label: "社团" },
  { value: "life", label: "生活" },
  { value: "exam", label: "考试" },
];

const LIST_LABEL: Record<TaskListKey, string> = {
  inbox: "收件箱",
  course: "课程",
  homework: "作业",
  club: "社团",
  life: "生活",
  exam: "考试",
};

/**
 * 清单 → 左端图标。
 *
 * ★ 六个清单必须**各不相同**：这一列是"扫一眼认类型"的唯一线索，
 *   两个清单共用一个图标（比如都退到 corn）等于这一列白加。
 * ★ `inbox` 用知穗（corn）是有意的：收件箱自己不是一类事，它装的是
 *   "还没归类的东西"，用吉祥物正好表达"暂时算我的"。
 */
const LIST_MARK: Record<TaskListKey, CornIconName> = {
  inbox: "corn",
  course: "book",
  homework: "notes",
  club: "target",
  life: "home",
  exam: "bolt",
};

/**
 * 自选图标那一排的清单与中文名都挪到 `lib/taskIcons.ts` 了 ——
 * 语音整理结果里也要让用户改图标，两处必须用同一张表，否则
 * "在待办页选得到的图标，在整理屏上选不到"。
 */

/**
 * 收件箱里的条目按标题猜一个更贴的图标。
 *
 * 为什么要猜：新待办默认落在收件箱，而"交高数作业"这条在用户心里就是作业。
 * 只按清单给图标的话，他把作业记下来看到的永远是玉米，"作业有作业图标"
 * 这件事就只在手动归过类之后才成立。
 *
 * 为什么**只**猜收件箱：一旦归了类，清单就是权威答案。这时再按标题猜
 * "这条像考试"，只会和 meta 里写着的那两个字打架。
 * 猜错也不丢信息 —— meta 一直写着真实清单名，图标只是提示。
 */
const MARK_HINTS: [RegExp, CornIconName][] = [
  [/作业|习题|论文|报告|实验|刷题|周报|交|写|presentation/i, "notes"],
  [/考试|期中|期末|四六级|雅思|托福|考研|测验|补考|证书|报名/i, "bolt"],
  [/上课|课程|讲座|预习|复习|网课|学分|开题/i, "book"],
  [/社团|例会|开会|招新|策划|比赛|演出|活动|聚餐/i, "target"],
  [/快递|取|买|缴费|宿舍|食堂|体检|报销|办证|理发|洗衣/i, "home"],
];

/**
 * 一条待办该用哪个图标。纯函数，便于单独验：**自选 > 清单 > 标题**。
 *
 * 三级而不是两级，是因为三者各有各的失效场景：
 *  - 用户自己选过（`task.icon`）→ 他说了算，哪怕和清单不搭
 *  - 没选过、但归了清单 → 清单图标比猜标题准（"取快递"在作业清单里也是作业）
 *  - 收件箱里的 → 只能猜标题，猜错也不丢信息（meta 一直写着真实清单名）
 */
function taskMark(task: Pick<Task, "list_key" | "title" | "icon">): CornIconName {
  const own = task.icon;
  // 用 `ICON_NAMES.has` 而不是直接转换：前端认不出的名字（后端宽松校验放行了新图标、
  // 而旧版前端还没跟上）应当退回清单图标，而不是渲染成玉米让用户以为没生效
  if (own && ICON_NAMES.has(own)) return own as CornIconName;
  if (task.list_key !== "inbox") return LIST_MARK[task.list_key] ?? "corn";
  const title = task.title ?? "";
  for (const [re, name] of MARK_HINTS) {
    if (re.test(title)) return name;
  }
  return "corn";
}

/**
 * 四档优先级。参考苹果提醒事项的做法：**用颜色分档，不用文字分档** ——
 * 名字只在编辑面板里出现，列表上用一小段颜色表示「这件事有多急」。
 *
 * 0 普通是「没有标记」，所以它不取任何语义色：一旦给普通也上色，
 * 四档就变成四种标签，满屏都是彩条，反而看不出哪条真的急。
 */
const PRIORITY_OPTIONS: { value: string; label: string }[] = [
  { value: "0", label: "普通" },
  { value: "1", label: "日常" },
  { value: "2", label: "重要" },
  { value: "3", label: "紧急" },
];

const PRIORITY_LABEL: Record<number, string> = { 0: "普通", 1: "日常", 2: "重要", 3: "紧急" };

/** 编辑面板里的四颗色块：名字 + 色号（色号是 CSS 变量名，不写死颜色值） */
const PRIORITY_PICKS: { value: TaskPriority; label: string }[] = [
  { value: 0, label: "普通" },
  { value: 1, label: "日常" },
  { value: 2, label: "重要" },
  { value: 3, label: "紧急" },
];

type Scope = "today" | "upcoming" | "all" | "completed";

const SCOPE_OPTIONS: { value: Scope; label: string }[] = [
  { value: "today", label: "今天" },
  { value: "upcoming", label: "接下来" },
  { value: "all", label: "全部" },
  { value: "completed", label: "已完成" },
];

// ==================== 小工具 ====================

const pad2 = (n: number) => String(n).padStart(2, "0");

/** Unix 秒 → `YYYY-MM-DD`（input[type=date] 要的本地日期，不能用 toISOString：那是 UTC） */
function dateValue(ts: number | null): string {
  if (!ts) return "";
  const d = new Date(toMs(ts));
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** Unix 秒 → `HH:MM` */
function timeValue(ts: number | null): string {
  return ts ? fmtHM(toMs(ts)) : "";
}

/**
 * 日期 + 时刻 → Unix 秒。
 *
 * 只给日期时落 **23:59:59**（用户说的「周五截止」就是当天结束），
 * 与实施单 §2.2 的约定一致；这也是「只选日期」不会变成 00:00 就过期的原因。
 */
function stampOf(date: string, time: string): number | null {
  if (!date) return null;
  const [y, mo, d] = date.split("-").map(Number);
  if (!y || !mo || !d) return null;
  if (time) {
    const [h, mi] = time.split(":").map(Number);
    return Math.floor(new Date(y, mo - 1, d, h || 0, mi || 0, 0, 0).getTime() / 1000);
  }
  return Math.floor(new Date(y, mo - 1, d, 23, 59, 59, 0).getTime() / 1000);
}

/** 「11/13」「今天」「明天」——待办右侧的日期，比裸时间戳好读 */
function shortDay(ts: number, now: number): string {
  const d = new Date(toMs(ts));
  const n = new Date(now);
  const day0 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
  const diff = Math.round(
    (new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - day0) / 86_400_000
  );
  if (diff === 0) return "今天";
  if (diff === 1) return "明天";
  if (diff === -1) return "昨天";
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 「11/13」——抽屉底部预览用。不带「今天/明天」是因为渲染期不能取当前时间
 *  （react-hooks/purity），而预览这一行用绝对日期反而更明确。 */
function mdOf(ts: number): string {
  const d = new Date(toMs(ts));
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 剩余天数（向上取整）。用「还有 3 天」而不是「72 小时」，倒计时才像倒计时 */
function daysLeft(ts: number, now: number): number {
  return Math.ceil((toMs(ts) - now) / 86_400_000);
}

/**
 * 每分钟走一次的「现在」。
 *
 * 首帧刻意给 `null`：在 useState 的初值里直接取当前时间，会让服务端渲染与
 * 首次客户端渲染拿到两个不同的时间，hydration 直接对不上；在渲染期取当前时间
 * 还会被 `react-hooks/purity` 判红（同一个组件两次渲染结果不同）。
 * 拿不到就先把倒计时和游标藏起来，一秒后它们自己出现。
 */
function useNowMinute(): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (alive) setNow(Date.now());
    };
    // 用 setTimeout 而不是在 effect 里同步 setState：后者会触发
    // react-hooks/set-state-in-effect（首帧级联渲染）。
    const first = window.setTimeout(tick, 0);
    const id = window.setInterval(tick, 60_000);
    return () => {
      alive = false;
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, []);
  return now;
}

/**
 * 抽屉 / 确认框的键盘与焦点：Esc 关、Tab 不落到背景、关掉之后焦点还给触发按钮。
 *
 * 为什么不用 `<dialog>`：它的 `showModal()` 必须在 effect 里调，而本项目
 * 开了 `react-hooks/set-state-in-effect`；手写这一小段反而更可控，
 * 也不会在 Safari 旧版上出现「弹层样式塌掉」。
 *
 * `onClose` 走 ref：调用处传的多半是内联箭头函数，直接进依赖数组会让
 * 每次输入都重建监听、并把焦点抢回第一个输入框（打字打到一半光标乱跳）。
 */
function useDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const focusables = () =>
      Array.from(
        node?.querySelectorAll<HTMLElement>(
          'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'
        ) ?? []
      ).filter((el) => el.getBoundingClientRect().height > 0);

    // 焦点进第一个可聚焦元素（通常是标题输入框）
    focusables()[0]?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const list = focusables();
      if (list.length === 0) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // 关掉之后焦点回到打开它的那个按钮，否则键盘用户会被扔回页面顶部
      previous?.focus?.();
    };
  }, [open]);

  return ref;
}

// ==================== 今天的时间轴 ====================

/** 一天有多少分钟。事件定位、静默段、游标都按它换算，别在三处各写 1440。 */
const DAY_MIN = 1440;

/** 带子上小时刻度的间隔（分钟）。60 = 整点一道，24 道正好铺满。 */
const TICK_MIN = 60;

/** 主刻度的小时间隔：6 小时一道重的，下面跟一个数字。 */
const MAJOR_HOURS = [0, 6, 12, 18, 24];

/** `HH:MM` → 当天分钟数。静默时段是自由文本，格式不对就当没配。 */
function hhmmToMin(v: string | null | undefined): number | null {
  if (!v) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(v.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const mi = Number(m[2]);
  if (!Number.isFinite(h) || !Number.isFinite(mi) || h > 24 || mi > 59) return null;
  return h * 60 + mi;
}

type QuietBand = { from: number; to: number };

/**
 * 静默时段在带子上是一段斜纹 —— 这是时间轴上的第二个真信息：
 * 「这一段到点也不响」，比再画一个圆点有用得多。
 * 它常常跨零点（23:00–07:00），所以拆成「0 点到结束」和「开始到 24 点」两截。
 */
function quietBands(start: string | null | undefined, end: string | null | undefined): QuietBand[] {
  const a = hhmmToMin(start);
  const b = hhmmToMin(end);
  if (a === null || b === null || a === b) return [];
  if (a > b) {
    return [
      { from: 0, to: b },
      { from: a, to: DAY_MIN },
    ];
  }
  return [{ from: a, to: b }];
}

/** 某个分钟点是否落在静默段里（用于把穿过去的整点线压淡） */
function inQuiet(bands: QuietBand[], min: number): boolean {
  return bands.some((b) => min >= b.from && min < b.to);
}

/**
 * 「这条还有多久 / 过去多久」。
 *
 * 齿孔只有 11px，放不下字；但一枚齿孔**只**说明"这个点有事"，
 * 说明不了"这件事跟我现在的距离"——而那才是用户扫一眼轴想知道的。
 * 所以这句挂在悬停气泡里，不占静态面积。
 */
function fmtDelta(diffMin: number): string {
  if (diffMin === 0) return "就是现在";
  const abs = Math.abs(diffMin);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  const span = h > 0 ? (m > 0 ? `${h} 小时 ${m} 分` : `${h} 小时`) : `${m} 分`;
  return diffMin > 0 ? `还有 ${span}` : `已过 ${span}`;
}

type AxisEvent = {
  /** 点这枚齿孔跳到哪张卡（同一分钟的多条合并成一枚，取第一条） */
  id: string;
  min: number;
  label: string;
  /** 这一枚上叠了几条，以及它们的标题，给悬停提示和读屏用 */
  count: number;
  title: string;
  /**
   * 圆点 = 待办（可以勾掉），菱形 = 联动提醒（只能推迟）。
   * 两类对象画在同一条轴上，形状是唯一能一眼分清的信号 ——
   * 同一分钟里混了两种时按「有待办」画圆点：那一条更要紧。
   */
  kind: "task" | "event";
};

/**
 * 一天的进度带。
 *
 * 上一版是一条 1px 细线 + 几个 7px 空心圆，970px 宽的卡里 66px 高只画了 7 个点，
 * 剩下全是空白；而且「今天已经过去多少」「夜里这一段到点不响」这两件事根本看不出来。
 *
 * 这一版把轴做成一条有厚度的带子，从下往上叠四层，每层都是真信息：
 *  1. 带子底：暖纸色凹槽 + 每小时一道刻度 —— 空白变成网格，而不是留白；
 *  2. 静默斜纹：prefs 里的静默时段画成斜纹段，「这一段不响」一眼可见；
 *  3. 齿孔：每条（待办或联动提醒）一枚，墨色实心点，骑在带子上；
 *  4. 游标：一条竖线 + 顶部胶囊，标出「现在走到哪」。
 *
 * 时间标签不常驻：970px 里 40px 一个刻度，`12:30` 这类标签排开会连成 `10:2712:30`。
 * 悬停/聚焦齿孔才浮出「12:30 · 午休」，具体几点平时看卡片左侧。
 * 手机上只留齿孔和 0/6/12/18/24 五个数字里的三个 —— 390 宽里 1 小时只值 16px。
 */
function TodayAxis({
  events,
  now,
  quietStart,
  quietEnd,
  onJump,
}: {
  events: AxisEvent[];
  now: number | null;
  quietStart: string | null | undefined;
  quietEnd: string | null | undefined;
  onJump: (id: string) => void;
}) {
  const nowMin = useMemo(() => {
    if (now === null) return null;
    const d = new Date(now);
    return d.getHours() * 60 + d.getMinutes();
  }, [now]);

  const quiet = useMemo(() => quietBands(quietStart, quietEnd), [quietStart, quietEnd]);

  return (
    <div className="rem-tape-wrap">
      <div className="rem-tape" role="group" aria-label="今天的提醒时间轴">
      <div className="rem-tape-body" aria-hidden="true">
        {/* 斜纹垫在刻度下面：静默段盖住 6 点那道贯穿刻度时，刻度还得透出来 */}
        {quiet.map((q) => (
          <span
            key={`q${q.from}`}
            className="rem-tape-quiet"
            style={{
              left: `${(q.from / DAY_MIN) * 100}%`,
              width: `${((q.to - q.from) / DAY_MIN) * 100}%`,
            }}
          />
        ))}
        {Array.from({ length: DAY_MIN / TICK_MIN }, (_, i) => i).map((h) => (
          <i
            key={`t${h}`}
            className="rem-tick"
            data-major={h % 6 === 0 ? "1" : "0"}
            /* 落进静默段的刻度要压淡：整点线是贯穿整条带子的，
               它穿过斜纹时会被读成"轴在这里断了"，而不是"这一段不响"。 */
            data-quiet={inQuiet(quiet, h * TICK_MIN) ? "1" : "0"}
            style={{ left: `${(h / (DAY_MIN / TICK_MIN)) * 100}%` }}
          />
        ))}
      </div>

      {events.map((e) => {
        // 已过去的和还没到的必须看得出差别：全都画成一样的墨点，
        // 用户扫一眼分不清"9 点那条已经响过了"和"晚上 9 点还没到"。
        const delta = nowMin === null ? null : e.min - nowMin;
        return (
          <button
            key={`e${e.min}`}
            type="button"
            className="rem-ev"
            data-kind={e.kind}
            data-past={delta !== null && delta < 0 ? "1" : "0"}
            data-many={e.count > 1 ? "1" : "0"}
            // 0 点和 24 点的齿孔要往回收一点，否则一半在带子外面
            style={{ left: `${Math.min(99.6, Math.max(0.4, (e.min / DAY_MIN) * 100))}%` }}
            onClick={() => onJump(e.id)}
            aria-label={`${e.label} ${e.title}${e.count > 1 ? ` 等 ${e.count} 条` : ""}${
              delta === null ? "" : `，${fmtDelta(delta)}`
            }，跳到这条`}
          >
            <i aria-hidden="true" />
            <b aria-hidden="true">
              <em>{e.label}</em>
              <span className="rem-ev-title">{e.title}</span>
              {e.count > 1 ? <span className="rem-ev-more">+{e.count - 1}</span> : null}
              {delta !== null ? (
                <span className="rem-ev-delta">{fmtDelta(delta)}</span>
              ) : null}
            </b>
          </button>
        );
      })}

      {nowMin !== null && now !== null ? (
        <div
          className="rem-now"
          style={{ left: `${(nowMin / DAY_MIN) * 100}%` }}
          aria-hidden="true"
        >
          <b>{fmtHM(now)}</b>
        </div>
      ) : null}

        <div className="rem-tape-hours" aria-hidden="true">
          {MAJOR_HOURS.map((h) => (
            <span key={`h${h}`} style={{ left: `${(h / 24) * 100}%` }}>
              {h}
            </span>
          ))}
        </div>
      </div>

      {/* 斜纹没有图例就只是一块脏底纹。上一轮评审的原话是「斜纹含义无解释」——
          补一行最小字号的说明。放在 .rem-tape **外面**：轴本身 60px 的高度是
          量过的契约（高度再涨，卡里就会重新出现大片空档），图例是轴底下的一行注解。 */}
      {quiet.length > 0 && quietStart && quietEnd ? (
        <p className="rem-tape-legend">
          <span className="rem-legend-hatch" aria-hidden="true" />
          <span>
            免打扰 {quietStart}–{quietEnd}
          </span>
          <span className="rem-legend-more">，这段时间不会发提醒</span>
        </p>
      ) : null}
    </div>
  );
}

// ==================== 推送健康条 ====================

/**
 * 「这台设备到点到底会不会响」。
 *
 * ★ `subscribed` 是**账号级**的（名下任意一台设备订阅了就是 true）。
 *   不要因为「本机没订阅、手机订阅了」就报警 —— 账号级推送仍然能送达。
 * ★ 推送正常时**整块不渲染**（不是 display:none）：留在 DOM 里的话，
 *   `role="status"` 会把一句「不会响」读给读屏用户，而事实是会响。
 */
function PushHealth({ status, onOpen }: { status: PushStatus | null; onOpen: () => void }) {
  // 端点没上线 / 请求失败 → null，静默隐藏（不该在提醒页顶上报错）
  if (!status) return null;
  if (status.supported && status.subscribed) return null;

  // 2026-11-13：一句说完。原来标题 + 一句说明各占一行，390 下是三行，
  // 把首屏又往下推了一截；而第二句（"在设置里打开「提醒权限」"）和右边的
  // 「去设置」按钮说的是同一件事 —— 按钮已经指路了，话就不必再说一遍。
  const title = status.supported
    ? "还没开提醒权限，到点不会通知你"
    : "这个浏览器收不到提醒";
  return (
    <div className="rem-health" role="status">
      <CornIcon name="warn" className="h-4 w-4" />
      <div>
        <b>{title}</b>
        {status.supported ? null : (
          <span>
            {/* 安卓的网页通知底层走 Google 服务，国内不通；日历那条路已删，
                所以这里没有第二条路可指，只能说实话。 */}
            安卓的网页通知在国内连不上，换台电脑或 iPhone 打开即可。
          </span>
        )}
      </div>
      {/* 用按钮而不是链接：已经在待办页上了，链接会整页重载一次，
          回来滚到顶部、列表重新拉一遍 —— 只是切个标签，不该有这些代价。 */}
      <button type="button" className="inline-link" onClick={onOpen}>
        去设置
      </button>
    </div>
  );
}

// ==================== 下一件 ====================

type UpNext =
  | { kind: "task"; at: number; task: Task }
  | { kind: "event"; at: number; item: ReminderItem };

/**
 * 「现在最该看哪一条」。
 *
 * 从今天要响的待办（remind_at 优先，没提醒就看 due_at）和今天的联动提醒里，
 * 取时刻最早的一条。整块**没有候选时不渲染**，不留一个空 hero。
 */
function NextCard({
  next,
  now,
  listLabel,
  sourceLabel,
  onComplete,
  onSnooze,
  onEditTask,
  onEditEvent,
}: {
  next: UpNext;
  now: number | null;
  listLabel: Record<string, string>;
  sourceLabel: Record<string, string>;
  onComplete: (task: Task) => void;
  onSnooze: (item: ReminderItem) => void;
  onEditTask: (task: Task) => void;
  onEditEvent: (item: ReminderItem) => void;
}) {
  const isTask = next.kind === "task";
  const title = isTask ? next.task.title : next.item.title;
  const when = fmtHM(toMs(next.at));
  const rest = now === null ? null : fmtRemaining(toMs(next.at), new Date(now));
  /* 「还有 9 小时 16 分」默认是灰的，半小时内才转金。
     为什么是 30 分钟：再远的时候它只是元信息，金色该留给"选中"和"主操作"；
     半小时是"现在真该准备了"的那条线，也让这一处成为**唯一会变的强调**。 */
  const soon = now !== null && toMs(next.at) - now >= 0 && toMs(next.at) - now <= 30 * 60 * 1000;
  const meta = isTask
    ? `${listLabel[next.task.list_key] ?? ""}${next.task.priority > 0 ? ` · ${PRIORITY_LABEL[next.task.priority]}` : ""}`
    : sourceLabel[next.item.source] ?? next.item.source;

  return (
    <section className="rem-next" aria-label="下一件">
      <div className="rem-next-head">
        <span className="rem-next-when">{when}</span>
        <span className="rem-next-rest" data-soon={soon ? "1" : undefined}>
          {rest ?? ""}
        </span>
      </div>
      <h3 className="rem-next-title">
        {/* 只给待办挂图标：联动提醒的时刻由来源（课表/番茄钟）决定，
            它不属于任何清单，硬套一个清单图标就是编造 */}
        {isTask ? (
          <span className="rem-next-mark" aria-hidden="true" data-mark={taskMark(next.task)}>
            <CornIcon name={taskMark(next.task)} className="h-full w-full" />
          </span>
        ) : null}
        <span>{title}</span>
      </h3>
      <p className="rem-next-meta">
        {meta}
        {isTask && next.task.due_at
          ? ` · ${shortDay(next.task.due_at, now ?? next.at)} 截止`
          : ""}
      </p>
      <div className="rem-next-acts">
        {isTask ? (
          <button type="button" className="rem-next-main" onClick={() => onComplete(next.task)}>
            完成
          </button>
        ) : (
          <button type="button" className="rem-next-main" onClick={() => onSnooze(next.item)}>
            推迟 10 分钟
          </button>
        )}
        <button
          type="button"
          className="btn-import"
          onClick={() => (isTask ? onEditTask(next.task) : onEditEvent(next.item))}
        >
          改时间
        </button>
      </div>
    </section>
  );
}

// ==================== 待办卡片 ====================

/** 提醒状态 → 一句人话（只在异常时显示，正常待响不挂标签） */
function remindState(task: Task): { text: string; tone: "muted" | "warn" } | null {
  const r = task.reminder;
  if (!r?.last_status) return null;
  if (r.last_status === "sent") return { text: "已提醒", tone: "muted" };
  if (r.last_status === "skipped_quiet") return { text: "没提醒 · 免打扰时间", tone: "warn" };
  if (r.last_status === "skipped_cap") return { text: "没提醒 · 今天次数已满", tone: "warn" };
  if (r.last_status === "skipped_empty") return { text: "没提醒 · 没有内容", tone: "warn" };
  if (r.last_status === "failed") {
    const missed = (r.last_detail || "").startsWith("missed:");
    return { text: missed ? "没提醒 · 当时设备不在线" : "没发出去", tone: "warn" };
  }
  return { text: r.last_status, tone: "warn" };
}

function TaskCard({
  task,
  now,
  dim,
  flash,
  pick,
  picked,
  onToggle,
  onEdit,
  onDelete,
  onSnooze,
  onPick,
  onStyle,
}: {
  task: Task;
  now: number | null;
  dim: boolean;
  flash: boolean;
  /** 多选模式：最左那格改成「选中」，标题上也一点就选 */
  pick: boolean;
  picked: boolean;
  onToggle: (task: Task) => void;
  onEdit: (task: Task) => void;
  onDelete: (task: Task) => void;
  onSnooze: (task: Task) => void;
  onPick: (task: Task) => void;
  /** 就地改外观：图标与等级。写回后由上层 reload */
  onStyle: (task: Task, patch: { icon?: string | null; priority?: TaskPriority }) => void;
}) {
  const [open, setOpen] = useState(false);
  // 图标面板与「⋯」菜单互斥：两个都开着会叠在同一个角落
  const [styleOpen, setStyleOpen] = useState(false);
  const done = task.status === "done";
  // 选择模式下这一格表达的是「选中」，不是「完成」——所以视觉要换形状
  // （方形 vs 圆形），否则用户会把「勾选」当成「做完了」。
  const checked = pick ? picked : done;
  // 时间列：有提醒时间就显示它（那才是「会响」的时刻），否则显示截止日
  const when = task.remind_at
    ? { main: fmtHM(toMs(task.remind_at)), small: "提醒" }
    : task.due_at
      ? { main: shortDay(task.due_at, now ?? toMs(task.due_at)), small: "截止" }
      : { main: "—", small: "无日期" };

  // 截止日期在副标题里**只在两种情况下出现**，其余交给最左边的时间列：
  //   ① 已逾期 —— 时间列显示的是「今天/截止」，说不出"晚了两天"；
  //   ② 设了提醒 —— 时间列这时显示的是提醒时刻，截止日就没地方写了。
  // 没设提醒又没过期的不写：时间列本来就是「今天 / 截止」，再写一遍
  // 只会把「不提醒」挤成省略号（390 下量过，主内容只有 172px）。
  const overdue =
    task.due_at !== null && now !== null && toMs(task.due_at as number) < now;
  const dueText = task.due_at
    ? overdue
      ? `逾期 ${Math.max(1, Math.ceil((now - toMs(task.due_at)) / 86_400_000))} 天`
      : task.remind_at
        ? `${shortDay(task.due_at, now ?? toMs(task.due_at))} 截止`
        : ""
    : "";
  // ★ 「不提醒」只在这种情况出现：有截止日期但没设提醒 —— 这是最容易误会的
  //   组合（用户以为"到点会响"）。设了提醒的不再重复写「提醒 22:00」，
  //   时间列已经把它顶在最左边了，重复一遍只会把「不提醒」挤进省略号。
  const remindText = !task.remind_at && task.due_at ? "不提醒" : "";
  const state = done ? null : remindState(task);

  const run = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  return (
    <article
      className="rem-card rem-task"
      id={`rem-card-${task.id}`}
      data-off={dim || done ? "1" : undefined}
      data-flash={flash ? "1" : undefined}
      data-priority={task.priority || undefined}
      data-done={done ? "1" : undefined}
      data-pick={pick ? "1" : undefined}
      data-picked={pick && picked ? "1" : undefined}
    >
      {/* 待办的 ○：真实 checkbox，热区 44（视觉 18）。联动提醒没有这一格。
          ★ 选择模式下**同一个位置**换成「选中」：一行里放两个 checkbox，
            用户永远分不清哪个是完成、哪个是选中。原来那格（完成）在
            底部操作条里有，点错了还能补救。 */}
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        aria-label={
          pick
            ? `${picked ? "取消选中" : "选中"}「${task.title}」`
            : `${done ? "取消完成" : "完成"}「${task.title}」`
        }
        className="rem-check"
        onClick={() => (pick ? onPick(task) : onToggle(task))}
      >
        <span aria-hidden="true">
          {checked ? <CornIcon name="check" className="h-3 w-3" /> : null}
        </span>
      </button>

      <div className="rem-when">
        {when.main}
        <small>{when.small}</small>
      </div>

      <div className="rem-main">
        <div className="rem-head">
          {/* 图标从"装饰"升级成按钮：主界面上就能换图标和等级，不必进抽屉。
              点它开一个小面板（图标 12 个 + 等级 4 档），选完立刻写回。
              ★ 多选模式下不响应：那一刻这一行只有一个动作（选中）。 */}
          <button
            type="button"
            className="rem-markbtn"
            data-mark={taskMark(task)}
            aria-label={`改「${task.title}」的图标和等级`}
            aria-expanded={pick ? undefined : styleOpen}
            aria-haspopup={pick ? undefined : "dialog"}
            disabled={pick}
            onClick={() => {
              setOpen(false);
              setStyleOpen((v) => !v);
            }}
          >
            <CornIcon name={taskMark(task)} className="h-full w-full" />
          </button>
          <button
            type="button"
            className="rem-title"
            onClick={() => (pick ? onPick(task) : onEdit(task))}
            title={pick ? "点一下选中" : "改标题、时间或截止日期"}
          >
            {task.title}
          </button>
          {/* 等级标记：既是「看得见」也是「点得开」。
              ★ 上一版等级只体现为卡片左边 2px 的色条 —— 手机上基本看不出来，
                而且唯一入口是左边那颗任务图标（看着就是装饰），所以用户会
                得出「根本没有等级功能」的结论。这一版把等级做成标题右边的
                文字标记，**未设等级时也显示灰字「普通」**：不留白，用户才
                知道这个维度存在；点它就开同一个面板。 */}
          <button
            type="button"
            className="rem-pritag"
            data-p={task.priority}
            data-open={styleOpen ? "1" : undefined}
            title={pick ? undefined : "点一下改等级"}
            aria-label={
              pick
                ? undefined
                : `「${task.title}」的等级：${PRIORITY_LABEL[task.priority] ?? "普通"}，点一下可改`
            }
            aria-haspopup={pick ? undefined : "dialog"}
            aria-expanded={pick ? undefined : styleOpen}
            disabled={pick}
            onClick={() => {
              setOpen(false);
              setStyleOpen((v) => !v);
            }}
          >
            <i aria-hidden="true" />
            {PRIORITY_LABEL[task.priority] ?? "普通"}
          </button>
        </div>
        <p className="rem-meta">
          <b>{LIST_LABEL[task.list_key] ?? task.list_key}</b>
          {/* 「不提醒」排在截止日期之前：窄屏会被省略号吃掉末尾，而这句是
              这一行里唯一"不做就会误事"的信息，所以还要给警示色。
              ★ 它同时是**入口**：点一下直接打开抽屉（提醒区已在第一屏），
                否则用户看到「不提醒」三个字也没有任何下一步可做 ——
                那三个字就只是抱怨，不是功能。 */}
          {remindText ? (
            <button
              type="button"
              className="rem-state rem-state-btn"
              data-tone="warn"
              title="点一下给这条待办设提醒"
              onClick={() => onEdit(task)}
            >
              {" "}
              · {remindText}
            </button>
          ) : null}
          {dueText ? <span> · {dueText}</span> : null}
          {state ? (
            <span className="rem-state" data-tone={state.tone}>
              {" "}
              · {state.text}
            </span>
          ) : null}
        </p>
      </div>

      {/* 已完成的卡片：「⋯」换成一颗明确的「删除」。
          这是勾完之后唯一的下一步 —— 收在菜单里等于让用户为了扔一张
          已经划掉的卡，再点两下。取消完成仍然可以勾回去，所以不丢退路。 */}
      {pick ? null : done ? (
        <button
          type="button"
          className="rem-donebtn"
          aria-label={`删除「${task.title}」`}
          onClick={() => onDelete(task)}
        >
          <CornIcon name="trash" className="h-4 w-4" />
        </button>
      ) : (
        <button
          type="button"
          className="rem-morebtn"
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={`「${task.title}」的更多动作`}
          onClick={() => {
            setStyleOpen(false);
            setOpen((v) => !v);
          }}
        >
          ⋯
        </button>
      )}

      {open && !pick ? (
        <>
          <div className="rem-scrim" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="rem-menu" role="menu">
            <button type="button" role="menuitem" onClick={run(() => onEdit(task))}>
              改内容或时间
            </button>
            {/* 单列一项：用户找的是「提醒」这个词，藏在「改这条待办」后面
                等于让他去猜。两个入口开的是同一个抽屉，但这一项说的是他的意图。 */}
            {!done ? (
              <button type="button" role="menuitem" onClick={run(() => onEdit(task))}>
                {task.remind_at ? "改提醒时间" : "设一个提醒"}
              </button>
            ) : null}
            {!done && task.reminder?.enabled ? (
              <button type="button" role="menuitem" onClick={run(() => onSnooze(task))}>
                推迟提醒 10 分钟
              </button>
            ) : null}
            {done ? (
              <button type="button" role="menuitem" onClick={run(() => onToggle(task))}>
                标记为未完成
              </button>
            ) : null}
            <hr />
            <button
              type="button"
              role="menuitem"
              data-danger="1"
              onClick={run(() => onDelete(task))}
            >
              删除
            </button>
          </div>
        </>
      ) : null}

      {/* 外观面板：图标 + 等级。
          为什么不拆成两个入口：这一行只有 390px，多做一颗按钮就得再挪一次
          时间列；而「换个图标」和「标成紧急」在用户脑子里本来就是同一件事——
          "这条看起来要不一样"。 */}
      {styleOpen && !pick ? (
        <>
          <div
            className="rem-scrim"
            onClick={() => setStyleOpen(false)}
            aria-hidden="true"
          />
          <div className="rem-stylepop" role="dialog" aria-label="图标与等级">
            <p className="rem-stylepop-hd">图标</p>
            <div className="rem-stylepop-icons">
              {MARK_PICKS.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  className="rem-iconbtn"
                  data-on={task.icon === p.name ? "1" : "0"}
                  aria-label={p.label}
                  aria-pressed={task.icon === p.name}
                  onClick={() => {
                    setStyleOpen(false);
                    onStyle(task, { icon: p.name });
                  }}
                >
                  <CornIcon name={p.name} className="h-full w-full" />
                </button>
              ))}
              {/* 「自动」：清掉自选，回到按清单和标题猜 */}
              <button
                type="button"
                className="rem-iconbtn rem-iconbtn-auto"
                data-on={!task.icon ? "1" : "0"}
                aria-pressed={!task.icon}
                onClick={() => {
                  setStyleOpen(false);
                  onStyle(task, { icon: null });
                }}
              >
                自动
              </button>
            </div>
            <p className="rem-stylepop-hd">等级</p>
            <div className="rem-stylepop-levels">
              {PRIORITY_PICKS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  className="rem-levelbtn"
                  data-level={p.value}
                  data-on={task.priority === p.value ? "1" : "0"}
                  aria-pressed={task.priority === p.value}
                  onClick={() => {
                    setStyleOpen(false);
                    onStyle(task, { priority: p.value });
                  }}
                >
                  <i aria-hidden="true" />
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </article>
  );
}

// ==================== 排序行 ====================

/**
 * 排序模式下的一行：把手 + 标题 + 时间。
 *
 * ★ 不复用 `TaskCard` 是故意的。排序时 checkbox 和「⋯」都用不了
 *   （点一下就把行挪走 / 把菜单盖在正在拖的行上），留着它们只会让人误触；
 *   而「有把手」本身就是「这个模式能干什么」的说明。
 *
 * ★ 把手用 `touch-action: none`：不加的话手指一按就开始滚动页面，
 *   `pointermove` 收不到，拖拽在手机上等于不存在。
 */
function SortRow({
  task,
  now,
  dragging,
  onDown,
  onMove,
  onUp,
  onKey,
}: {
  task: Task;
  now: number | null;
  dragging: boolean;
  onDown: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onMove: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onUp: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onKey: (e: React.KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const when = task.due_at
    ? shortDay(task.due_at, now ?? toMs(task.due_at))
    : task.remind_at
      ? fmtHM(toMs(task.remind_at))
      : "—";
  return (
    <article
      className="rem-card rem-sortrow"
      data-task-id={task.id}
      data-dragging={dragging ? "1" : undefined}
      data-priority={task.priority || undefined}
    >
      <button
        type="button"
        className="rem-grip"
        aria-label={`调整「${task.title}」的顺序，可拖动或用上下方向键`}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        onKeyDown={onKey}
      >
        <span aria-hidden="true" className="rem-grip-dots" />
      </button>
      {/* 时间列与标题列的位置和正常待办行一致（把手占了原来勾选框那格），
          所以进排序模式时整行的骨架不跳 */}
      <div className="rem-when">
        {when}
        <small>{task.due_at ? "截止" : task.remind_at ? "提醒" : "无日期"}</small>
      </div>
      <div className="rem-main">
        <div className="rem-head">
          <span className="rem-mark" aria-hidden="true" data-mark={taskMark(task)}>
            <CornIcon name={taskMark(task)} className="h-full w-full" />
          </span>
          <span className="rem-title">{task.title}</span>
          {/* 排序模式只读：这一行唯一的动作是拖动，标记做成纯展示，
              但**仍然显示**——否则一进排序模式，等级就整片消失，用户会以为被改了 */}
          <span className="rem-pritag" data-p={task.priority} data-static="1">
            <i aria-hidden="true" />
            {PRIORITY_LABEL[task.priority] ?? "普通"}
          </span>
        </div>
        <p className="rem-meta">
          <b>{LIST_LABEL[task.list_key] ?? task.list_key}</b>
        </p>
      </div>
    </article>
  );
}

// ==================== 联动提醒卡片 ====================

/**
 * 卡片左上角那个时刻；没有下一次就说清楚为什么。
 *
 * 「接下来」分组**不再**把星期塞进这一行（原来写 `周三 08:00`）：150px 的列里
 * 那串字会被卡片挤成两行，而且每周三的提醒本来应该一直说「每周三」，
 * 不是到了本周才提一次。星期改由下面的频率行承担（`每周三`）。
 */
function whenMain(item: ReminderItem): string {
  if (!item.enabled) return "已暂停";
  if (!item.next_fire_at) return item.freq === "once" ? "已提醒过" : "还没设时间";
  return fmtHM(toMs(item.next_fire_at));
}

/** 时刻下面那行频率：它同时承担"这条会不会重复"和"是周几" */
function freqText(item: ReminderItem): string {
  if (item.freq === "weekly" && item.weekday !== null) {
    return `每周${(WEEK_LABELS[item.weekday] ?? "每周").slice(1)}`;
  }
  if (item.freq === "weekdays") return "工作日";
  if (item.freq === "interval") return `每 ${item.every_minutes || 0} 分钟`;
  if (item.freq === "once") return "仅一次";
  return "每天";
}

/** 流水状态 → 两个字的短标签（卡片上没地方写整句） */
function fireShort(f: ReminderFire): string {
  if (f.status === "sent") return "已提醒";
  if (f.status === "skipped_quiet") return "没提醒 · 免打扰";
  if (f.status === "skipped_cap") return "没提醒 · 次数已满";
  if (f.status === "skipped_empty") return "没提醒 · 没有内容";
  if (f.status === "failed") {
    return (f.detail || "").startsWith("missed:") ? "没提醒" : "没发出去";
  }
  return f.status;
}

function ReminderCard({
  item,
  sourceLabel,
  onEdit,
  onToggle,
  onDelete,
  onCloseSource,
  onSnooze,
  onOpenSources,
  sourceOff,
  flash,
  nowNumber,
  fire,
}: {
  item: ReminderItem;
  sourceLabel: string;
  onEdit: (item: ReminderItem) => void;
  onToggle: (item: ReminderItem) => void;
  onDelete: (item: ReminderItem) => void;
  onCloseSource: (item: ReminderItem) => void;
  onSnooze: (item: ReminderItem) => void;
  onOpenSources: () => void;
  sourceOff: boolean;
  flash: boolean;
  /** 「现在」的毫秒数；null = 还没拿到（首帧），这时不显示倒计时 */
  nowNumber: number | null;
  /** 今天的那条送达流水（有就说明「已经响过 / 被跳过」） */
  fire: ReminderFire | null;
}) {
  const [open, setOpen] = useState(false);
  const canCloseSource = item.source !== "manual" && item.source !== "system";
  const dim = !item.enabled || sourceOff;

  // 宽屏右侧那句「还有 3 小时」：970px 的卡如果只看左边 300px，
  // 右边三分之二永远是空的 —— 与其留白，不如回答"我还能歇多久"。
  const rest = useMemo(() => {
    if (nowNumber === null || !item.enabled || !item.next_fire_at || sourceOff) return null;
    return fmtRemaining(toMs(item.next_fire_at), new Date(nowNumber));
  }, [nowNumber, item.enabled, item.next_fire_at, sourceOff]);

  // 每个动作点完都先收起菜单：留着它，用户下一次点卡片会以为没响应
  const run = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  return (
    <article
      className="rem-card rem-event"
      id={`rem-card-${item.id}`}
      data-off={dim ? "1" : undefined}
      data-flash={flash ? "1" : undefined}
      data-shape="event"
    >
      <div className="rem-when">
        {whenMain(item)}
        <small>{freqText(item)}</small>
      </div>

      <div className="rem-main">
        {/* 标题本身就是最高频的入口（改这条）。它比在卡片底下挂一排 11px
            灰字可点得多，也把「每张卡 4 个可点」压到 2 个。 */}
        <button
          type="button"
          className="rem-title"
          onClick={() => onEdit(item)}
          title="改时间或标题"
        >
          {item.title}
        </button>
        <p className="rem-meta">
          <b>{sourceLabel}</b>
          <span> · {ACTION_LABEL[item.action] || item.action}</span>
          {item.source_note ? <span> · {item.source_note}</span> : null}
          {sourceOff ? <span> · 这个功能已关闭</span> : null}
          {fire ? (
            <span className="rem-state" data-tone={fire.status === "sent" ? "muted" : "warn"}>
              {" "}
              · {fireShort(fire)}
            </span>
          ) : null}
        </p>
      </div>

      <span className="rem-rest" aria-hidden={rest ? undefined : true}>
        {rest}
      </span>

      <button
        type="button"
        className="rem-morebtn"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`「${item.title}」的更多动作`}
        onClick={() => setOpen((v) => !v)}
      >
        ⋯
      </button>

      {open ? (
        <>
          <div className="rem-scrim" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="rem-menu" role="menu">
            <button type="button" role="menuitem" onClick={run(() => onEdit(item))}>
              改时间 / 标题
            </button>
            {item.enabled && !sourceOff ? (
              <button type="button" role="menuitem" onClick={run(() => onSnooze(item))}>
                推迟 10 分钟
              </button>
            ) : null}
            {sourceOff ? (
              // 来源关着时「暂停这条」会把单条也关掉，和用户想要的相反 ——
              // 直接把他送到能做决定的地方。
              <button type="button" role="menuitem" onClick={run(onOpenSources)}>
                去打开「{sourceLabel}」的提醒
              </button>
            ) : (
              <button type="button" role="menuitem" onClick={run(() => onToggle(item))}>
                {item.enabled ? "暂停这条" : "恢复这条"}
              </button>
            )}
            <hr />
            {canCloseSource ? (
              <button type="button" role="menuitem" onClick={run(() => onCloseSource(item))}>
                不再接收「{sourceLabel}」的提醒
              </button>
            ) : (
              <p>这条是你自己建的，不跟着其他功能走</p>
            )}
            <button
              type="button"
              role="menuitem"
              data-danger="1"
              onClick={run(() => onDelete(item))}
            >
              删除
            </button>
          </div>
        </>
      ) : null}
    </article>
  );
}

// ==================== 送达记录 ====================

/**
 * 「提醒记录」。
 *
 * 这是整个页面最能建立信任的一块：用户不需要理解 cron，只需要知道
 * 「为什么没响」。所以每行都要给**原因**，而不是只有红叉。
 *
 * `failed` + `detail` 以 `missed:` 开头的是「错过了触发窗口」（设备离线 / worker 重启），
 * 不是发送失败 —— 一律叫「失败」会把人引向排查推送服务，方向全错。
 */
function FireDiary({ reminderId }: { reminderId: string | null }) {
  const [fires, setFires] = useState<ReminderFire[] | null>(null);

  useEffect(() => {
    if (!reminderId) return;
    let alive = true;
    void (async () => {
      const res = await api.reminders.fires(reminderId);
      if (alive) setFires(res?.items ?? []);
    })();
    return () => {
      alive = false;
    };
  }, [reminderId]);

  if (!reminderId) return null;

  return (
    <details className="rem-fires">
      <summary>提醒记录{fires && fires.length ? `（${fires.length} 条）` : ""}</summary>
      {fires === null ? (
        <p className="rem-fire-empty">正在读提醒记录…</p>
      ) : fires.length === 0 ? (
        <p className="rem-fire-empty">还没有提醒过。</p>
      ) : (
        <ul>
          {fires.slice(0, 5).map((f) => (
            <li key={`${f.planned_at}-${f.reminder_id}`} data-tone={fireTone(f)}>
              <span className="rem-fire-time">{fmtHM(toMs(f.fired_at || f.planned_at))}</span>
              <span className="rem-fire-text">{fireText(f)}</span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function fireTone(f: ReminderFire): string {
  if (f.status === "sent") return "ok";
  return "warn";
}

function fireText(f: ReminderFire): string {
  const d = f.detail || "";
  if (f.status === "sent") {
    return d.startsWith("merged_into:") ? "已提醒（和同一时间的其他提醒合并了）" : "已提醒";
  }
  if (f.status === "skipped_quiet") return "没提醒 · 免打扰时间";
  if (f.status === "skipped_cap") return `没提醒 · 今天已经提醒了 ${(d.match(/\d+/) ?? ["?"])[0]} 条`;
  if (f.status === "skipped_empty") {
    if (d.startsWith("source_disabled:")) return "没提醒 · 这个功能已关闭";
    return "没提醒 · 没有可提醒的内容";
  }
  if (f.status === "failed") {
    if (d.startsWith("missed:")) return "没提醒 · 当时设备不在线";
    if (d === "no_subscription") return "没发出去 · 没有能收提醒的设备";
    if (d === "push_not_configured") return "没发出去 · 提醒服务还没配置好";
    if (d === "subscription_gone") return "没发出去 · 这台设备的通知已失效";
    return "没发出去 · 原因不明";
  }
  return f.status;
}

// ==================== 待办抽屉 ====================

/**
 * 新建 / 修改待办。
 *
 * 字段顺序：标题 → 图标 → 清单 + 优先级 → **提醒** → 截止 → 备注。
 *
 * ★ 提醒排在截止**之前**，这是被用户反馈逼出来的：抽屉在手机上只有约 80vh，
 *   前面「标题输入 + 12 个图标网格 + 两个下拉 + 两个日期框」就要吃掉 400 多像素，
 *   提醒区排在后面正好掉到折叠线以下。用户不会去滚一个"看起来到底了"的抽屉，
 *   直接得出「待办不能设提醒」的结论 —— 功能是完整的，只是他没见过。
 *
 * `due_at` 与 `remind_at` 是两个独立控件，那句「只设截止时间不会响」
 * 不是装饰 —— 少了它，用户设完截止日期就会等一个永远不会来的通知。
 */
function TaskDrawer({
  open,
  editing,
  busy,
  onClose,
  onSubmit,
}: {
  open: boolean;
  editing: Task | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (draft: TaskDraft) => void;
}) {
  // 初值直接从 `editing` 取，**不写 effect**：调用方用 `key` 控制重挂载，
  // 打开另一条时组件是新的实例，"同步 props 到 state" 这件事根本不需要发生。
  const [title, setTitle] = useState(editing?.title ?? "");
  const [listKey, setListKey] = useState<TaskListKey>(editing?.list_key ?? "inbox");
  /** 自选图标。`null` = 没选，渲染时按清单/标题自动挑（见 `taskMark`） */
  const [icon, setIcon] = useState<string | null>(editing?.icon ?? null);
  const [priority, setPriority] = useState<TaskPriority>(editing?.priority ?? 0);
  const [note, setNote] = useState(editing?.note ?? "");
  const [dueDate, setDueDate] = useState(dateValue(editing?.due_at ?? null));
  const [dueTime, setDueTime] = useState(timeValue(editing?.due_at ?? null));
  const [remindOn, setRemindOn] = useState(Boolean(editing?.remind_at));
  const [remindDate, setRemindDate] = useState(dateValue(editing?.remind_at ?? null));
  const [remindTime, setRemindTime] = useState(timeValue(editing?.remind_at ?? null));

  const dialogRef = useDialog(open, onClose);
  if (!open) return null;

  const dueStamp = stampOf(dueDate, dueTime);
  const remindStamp = remindOn ? stampOf(remindDate, remindTime) : null;
  const canSubmit = title.trim().length > 0 && !busy;

  /** 打开「提醒我」时给一个合理默认：截止前 1 小时，否则今晚 20:00，都过了就顺延到下一个整点 */
  const defaultRemind = (): { date: string; time: string } => {
    const nowMs = Date.now();
    let ts = dueStamp ? toMs(dueStamp) - 3_600_000 : 0;
    if (!dueStamp) {
      const d = new Date(nowMs);
      d.setHours(20, 0, 0, 0);
      ts = d.getTime();
    }
    if (ts <= nowMs) {
      const d = new Date(nowMs);
      d.setHours(d.getHours() + 1, 0, 0, 0);
      ts = d.getTime();
    }
    const d = new Date(ts);
    return {
      date: `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`,
      time: `${pad2(d.getHours())}:${pad2(d.getMinutes())}`,
    };
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 sm:items-center sm:p-4">
      <div
        className="card rem-drawer-card w-full max-w-md"
        style={{ maxHeight: "88dvh" }}
        role="dialog"
        aria-modal="true"
        aria-label={editing ? "修改待办" : "新建待办"}
        ref={dialogRef}
      >
        <div className="card-head px-4 pb-2 pt-4">
          <h3 className="card-title">{editing ? "修改这条待办" : "新建待办"}</h3>
          <button
            type="button"
            className="text-[var(--text-3)]"
            onClick={onClose}
            aria-label="关闭"
          >
            <CornIcon name="close" className="h-5 w-5" />
          </button>
        </div>

        <div className="rem-dbody space-y-4 px-4 pb-4">
          <Field label="要做什么">
            <input
              className="input"
              value={title}
              maxLength={120}
              placeholder="例如：交高数作业"
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>

          <Field label="图标">
            {/* 自动档必须留着，而且必须排第一：绝大多数人不会选图标，
                他们要的是"我的作业看起来像作业"。自动档就是按清单+标题猜的那套，
                放在这儿是为了让"我没选"这件事看起来是个选项，而不是一个漏填的空。
                选中态用 aria-pressed 表达，样式靠 data-on —— 不给图标单独配色：
                这一排本来就花，再上色就变成调色盘了。 */}
            <div className="rem-iconpick" role="group" aria-label="选一个图标">
              <button
                type="button"
                className="rem-iconpick-b"
                data-auto="1"
                data-on={icon === null ? "1" : "0"}
                aria-pressed={icon === null}
                aria-label="自动选（跟随清单）"
                title="自动（跟随清单）"
                onClick={() => setIcon(null)}
              >
                <CornIcon name={LIST_MARK[listKey]} className="h-full w-full" />
              </button>
              {MARK_PICKS.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  className="rem-iconpick-b"
                  data-on={icon === p.name ? "1" : "0"}
                  aria-pressed={icon === p.name}
                  aria-label={p.label}
                  title={p.label}
                  onClick={() => setIcon(p.name)}
                >
                  <CornIcon name={p.name} className="h-full w-full" />
                </button>
              ))}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-2">
            <Field label="清单">
              {/* 原生 option 里放不了 svg，所以图标放框外：改清单时它当场换掉，
                  比"选完再看列表"更早知道这条会被归到哪一类 */}
              <div className="rem-listpick">
                {/* 这里**只**表示清单，不能被上面的自选图标顶掉：
                    它俩回答的是不同问题（"归到哪一类" vs "这条长什么样"），
                    混用会让"我选了星星，为什么清单显示的还是作业"变成一个问题 */}
                <span className="rem-mark" aria-hidden="true" data-mark={LIST_MARK[listKey]}>
                  <CornIcon name={LIST_MARK[listKey]} className="h-full w-full" />
                </span>
                <select
                  className="input"
                  value={listKey}
                  onChange={(e) => setListKey(e.target.value as TaskListKey)}
                >
                  {LIST_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
            </Field>
            <div>
              <span className="f">优先级</span>
              <Seg
                options={PRIORITY_OPTIONS}
                value={String(priority)}
                onChange={(v) => setPriority(Number(v) as TaskPriority)}
                ariaLabel="优先级"
                stretch
              />
            </div>
          </div>

          {/* 提醒：排在截止之前。
              ★ 上一版它排在「标题 + 12 个图标 + 清单/优先级 + 两个日期框」之后，
                抽屉在手机上只有约 80vh，提醒区正好掉到折叠线以下 ——
                用户不滚到底就看不到，于是得出「待办不能设提醒」的结论。
                把它提到截止前面，打开抽屉第一屏就能看见开关。 */}
          <div className="rem-timeblock">
            <div className="rem-remindhead">
              <div className="rem-step">
                提醒我 <i>到设定时间发通知；可以和截止时间不同</i>
              </div>
              <Switch
                checked={remindOn}
                label="提醒我"
                onChange={(next) => {
                  setRemindOn(next);
                  if (next && !remindDate) {
                    const d = defaultRemind();
                    setRemindDate(d.date);
                    setRemindTime(d.time);
                  }
                }}
              />
            </div>
            {remindOn ? (
              <div className="grid grid-cols-2 gap-2">
                <Field label="日期">
                  <input
                    type="date"
                    className="input"
                    value={remindDate}
                    onChange={(e) => setRemindDate(e.target.value)}
                  />
                </Field>
                <Field label="几点">
                  <input
                    type="time"
                    className="input"
                    value={remindTime}
                    onChange={(e) => setRemindTime(e.target.value)}
                  />
                </Field>
              </div>
            ) : (
              <p className="rem-noSound">
                不打开就不会收到任何通知；截止日期只用来排期和排序，它不会提醒你。
              </p>
            )}
          </div>

          {/* 截止：只到这里不会响。这句话必须写在控件旁边，而不是折叠在帮助里 */}
          <div className="rem-timeblock">
            <div className="rem-step">
              最晚什么时候完成 <i>这是截止时间；只设它，不会收到提醒</i>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="日期">
                <input
                  type="date"
                  className="input"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                />
              </Field>
              <Field label="几点（可不填）">
                <input
                  type="time"
                  className="input"
                  value={dueTime}
                  onChange={(e) => setDueTime(e.target.value)}
                />
              </Field>
            </div>
            {dueDate ? (
              <button
                type="button"
                className="rem-clear"
                onClick={() => {
                  setDueDate("");
                  setDueTime("");
                }}
              >
                清掉截止时间
              </button>
            ) : null}
          </div>

          <Field label="备注（可选）">
            <textarea
              className="input"
              rows={2}
              maxLength={1000}
              value={note}
              placeholder="第 3 章习题"
              onChange={(e) => setNote(e.target.value)}
            />
          </Field>

          {editing ? <FireDiary reminderId={editing.reminder?.id ?? null} /> : null}
        </div>

        {/* 底部预览：把上面几个字段的结论合成一句，省得用户回头再核一遍 */}
        <footer className="rem-preview">
          <p
            title={`${dueStamp ? `${mdOf(dueStamp)} 截止` : "无截止"} · ${
              remindStamp ? `${mdOf(remindStamp)} ${fmtHM(toMs(remindStamp))} 提醒` : "不提醒"
            }`}
          >
            <b>{dueStamp ? `${mdOf(dueStamp)} 截止` : "无截止时间"}</b>
            <span>
              {" "}
              · {remindStamp ? `${mdOf(remindStamp)} ${fmtHM(toMs(remindStamp))} 提醒` : "不提醒"}
            </span>
          </p>
          <div className="rem-preview-acts">
            <button type="button" className="btn-import" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn-new"
              disabled={!canSubmit}
              onClick={() =>
                onSubmit({
                  title: title.trim(),
                  list_key: listKey,
                  icon,
                  priority,
                  note: note.trim() || null,
                  due_at: dueStamp,
                  remind_at: remindStamp,
                })
              }
            >
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

// ==================== 提醒抽屉（新建 / 修改） ====================

function ReminderDrawer({
  open,
  editing,
  sourceName,
  sources,
  busy,
  onClose,
  onSubmit,
}: {
  open: boolean;
  editing: ReminderItem | null;
  /** 编辑时这条的来源叫法（来源只读，所以要单独传进来） */
  sourceName: string;
  sources: ReminderSource[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (draft: ReminderDraft) => void;
}) {
  const [title, setTitle] = useState(editing?.title ?? "");
  const [at, setAt] = useState((editing?.override?.at || editing?.at || "20:00").slice(0, 5));
  const [freq, setFreq] = useState<ReminderFreq>((editing?.freq as ReminderFreq) || "daily");
  const [weekday, setWeekday] = useState(editing?.weekday ?? 0);
  const [action, setAction] = useState<ReminderAction>(
    (editing?.action as ReminderAction) || "notify"
  );
  const [source, setSource] = useState(editing?.source ?? "manual");

  const dialogRef = useDialog(open, onClose);
  if (!open) return null;

  const canSubmit = title.trim().length > 0 && !busy;
  // 来源提醒的频率由来源决定（课表每周三就是每周三），界面上不给改。
  // 自己定的 / 每日回顾可以改，且改了要真的提交 —— 否则就是一个能点不生效的控件。
  const isOwn = !editing || editing.source === "manual" || editing.source === "system";

  const pickSource = editing ? sourceName : (sources.find((s) => s.key === source)?.label ?? "自己定的");
  // 选项按来源给。老数据里可能存着「自己定的 + 让助手帮我写」（过滤之前建的），
  // 这时要把它补回列表 —— 否则 select 找不到对应 option 会显示第一项，
  // 用户一保存就被静默改成了「只提醒」，他自己完全不知道。
  const sourceKey = editing ? editing.source : source;
  const baseActions = actionOptionsFor(sourceKey);
  const actionList = baseActions.some((o) => o.value === action)
    ? baseActions
    : [...baseActions, { value: action, label: ACTION_LABEL[action] ?? action }];
  const freqLine =
    freq === "weekly"
      ? `每周${(WEEK_LABELS[weekday] ?? "周一").slice(1)} ${at}`
      : freq === "once"
        ? `一次 · ${at}`
        : `${FREQ_OPTIONS.find((o) => o.value === freq)?.label ?? "每天"} ${at}`;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 sm:items-center sm:p-4">
      <div
        className="card rem-drawer-card w-full max-w-md"
        style={{ maxHeight: "88dvh" }}
        role="dialog"
        aria-modal="true"
        aria-label={editing ? "修改提醒" : "新建提醒"}
        ref={dialogRef}
      >
        <div className="card-head px-4 pb-2 pt-4">
          <h3 className="card-title">{editing ? "修改这条提醒" : "新建提醒"}</h3>
          <button
            type="button"
            className="text-[var(--text-3)]"
            onClick={onClose}
            aria-label="关闭"
          >
            <CornIcon name="close" className="h-5 w-5" />
          </button>
        </div>

        <div className="rem-dbody space-y-4 px-4 pb-4">
          {/* ① 说什么 */}
          <Field label="提醒你什么">
            <input
              className="input"
              value={title}
              maxLength={60}
              placeholder="例如：复盘今天"
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>

          {/* ② 来源（规格顺序的第一位）：它决定「关掉这个来源时，这条跟不跟着停」 */}
          {editing ? (
            <div>
              <span className="f">由哪个功能安排</span>
              <p className="rem-ro">
                这条提醒属于「{sourceName}」。建好后不能改，要换只能删掉重新建。
              </p>
            </div>
          ) : (
            <Field label="由哪个功能安排" hint="关闭这个功能后，它安排的提醒也会一起停">
              <select
                className="input"
                value={source}
                onChange={(e) => setSource(e.target.value)}
              >
                <option value="manual">我自己定的</option>
                {sources.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {/* ③ 什么时候 */}
          <div>
            <div className="rem-step">
              什么时候提醒你 <i>到时间就提醒；课表、打卡这类当天没有内容时不发</i>
            </div>
            {isOwn ? (
              <>
                <Seg
                  options={FREQ_OPTIONS}
                  value={freq}
                  onChange={setFreq}
                  ariaLabel="重复方式"
                  stretch
                />
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Field label="几点">
                    <input
                      type="time"
                      className="input"
                      value={at}
                      onChange={(e) => setAt(e.target.value)}
                    />
                  </Field>
                  {freq === "weekly" ? (
                    <Field label="周几">
                      <select
                        className="input"
                        value={weekday}
                        onChange={(e) => setWeekday(Number(e.target.value))}
                      >
                        {WEEK_LABELS.map((label, index) => (
                          <option key={label} value={index}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ) : null}
                </div>
              </>
            ) : (
              // 来源提醒：频率跟着来源走，能改的只有「几点」（落 override）
              <>
                <p className="rem-ro">
                  由「{sourceName}」安排：{editing ? freqText(editing) : ""}
                </p>
                <div className="mt-2">
                  <Field label="改到几点" hint="只改这一条，不影响这个功能里的其他提醒">
                    <input
                      type="time"
                      className="input"
                      value={at}
                      onChange={(e) => setAt(e.target.value)}
                    />
                  </Field>
                </div>
              </>
            )}
          </div>

          {/* ④ 做什么 */}
          <Field label="提醒时做什么">
            <select
              className="input"
              value={action}
              onChange={(e) => setAction(e.target.value as ReminderAction)}
            >
              {actionList.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          {/* 选择依据，不是补充说明：三个词都是行话，不解释等于让用户盲选 */}
          <p className="rem-actionhint">{ACTION_HINT[action] ?? ""}</p>

          {/*
           * 「怎么送达」：`service/reminder_tick.py` 只认 channels 里的 push，
           * 写别的（inapp / telegram）会被拦下并记进流水。所以这里**不放选择器** ——
           * 一个能点、点了却不生效的选项，比没有这个选项更让人以为"设置过了"。
           * 也不用输入框的外形，否则看着还是像能选。
           */}
          <div className="rem-deliver">
            <b>提醒方式 · 通知</b>
            <span>
              到时间后，提醒会出现在设备的通知栏。前提是「设置」页里已经开启通知。
            </span>
          </div>

          {editing ? <FireDiary reminderId={editing.id} /> : null}
        </div>

        {/* 底部预览：把上面四个字段的结论合成一句，省得用户回头再核一遍 */}
        <footer className="rem-preview">
          <p title={`${freqLine} · ${ACTION_LABEL[action]} · ${pickSource}`}>
            <b>{freqLine}</b>
            <span>
              {" "}
              · {ACTION_LABEL[action]} · {pickSource}
            </span>
          </p>
          <div className="rem-preview-acts">
            <button type="button" className="btn-import" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn-new"
              disabled={!canSubmit}
              onClick={() =>
                onSubmit({
                  title: title.trim(),
                  source,
                  freq,
                  at,
                  weekday: freq === "weekly" ? weekday : null,
                  action,
                  channels: "push",
                })
              }
            >
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

// ==================== 删除确认 ====================

/**
 * 二次确认。
 *
 * 后端没有 restore / soft-delete，所以**不做假撤销**（「已删除 · 撤销」点下去
 * 其实什么都没恢复，比不提供更伤信任）。这里只把「删了就没了」说清楚。
 * 也不用 `window.confirm`：它会把整页的键盘焦点丢掉，样式也不受主题控制。
 */
function ConfirmDelete({
  open,
  title,
  note,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  note: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useDialog(open, onCancel);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/30 p-4">
      <div
        className="card rem-confirm"
        role="dialog"
        aria-modal="true"
        aria-label="确认删除"
        ref={dialogRef}
      >
        <h3>{title}</h3>
        <p>{note}</p>
        <div className="rem-confirm-acts">
          <button type="button" className="btn-import" onClick={onCancel}>
            取消
          </button>
          <button type="button" className="rem-danger" disabled={busy} onClick={onConfirm}>
            {busy ? "删除中…" : "删除"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ==================== 页面 ====================

type Data = {
  tasks: Record<Scope, Task[]>;
  summary: TaskSummary | null;
  reminders: ReminderItem[];
  remSummary: ReminderSummary | null;
  sources: ReminderSource[];
  prefs: ReminderPrefs | null;
  push: PushStatus | null;
  /** 「每天几点做回顾」（/api/remind）。端点没上线时为 null，那张卡整块不渲染 */
  daily: RemindPref | null;
  /** 最近 50 条送达流水：用来把「今天已经响过的」捞回今天这一组 */
  fires: ReminderFire[];
};

const EMPTY_TASKS: Record<Scope, Task[]> = { today: [], upcoming: [], all: [], completed: [] };

/** 稳定空数组：放进 useMemo 返回值里，避免每次渲染都给下游一个新引用 */
const NO_TASKS: Task[] = [];

export default function RemindersPage() {
  const { me } = useAuth();
  const authedId = me?.user?.id ?? null;

  const [tab, setTab] = useState<"list" | "settings">("list");
  // `?tab=settings` 直接落到设置：提醒权限从「设置」搬进来之后，首页那条
  // 「打开提醒」的提示卡、教程页的「回到设置」都要能一步到位。用
  // window.location 而不是 useSearchParams：后者在 App Router 里会把整页
  // 拉进 Suspense，为了读一个参数不值得。
  useEffect(() => {
    const want = new URLSearchParams(window.location.search).get("tab");
    if (want === "settings") setTab("settings");
  }, []);
  const [scope, setScope] = useState<Scope>("today");
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<Data | null>(null);
  const [taskDrawer, setTaskDrawer] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  // AI 整理（v12 · Phase A）：只是一个壳，拆句在本地 fixture 里，一条都不入库
  const [captureOpen, setCaptureOpen] = useState(false);
  const [reminderDrawer, setReminderDrawer] = useState(false);
  const [editingReminder, setEditingReminder] = useState<ReminderItem | null>(null);
  const [pending, setPending] = useState<
    | { kind: "task"; task: Task }
    | { kind: "reminder"; item: ReminderItem }
    | { kind: "bulk"; ids: string[] }
    | null
  >(null);
  const [busy, setBusy] = useState(false);
  // 列表模式：正常 / 多选 / 排序。三者互斥 —— 同时开着会让人分不清
  // 「点这一下」是在选、在拖，还是在完成。
  const [mode, setMode] = useState<"normal" | "select" | "sort">("normal");
  const [picked, setPicked] = useState<string[]>([]);
  /** 批量「移到清单」的目标，默认收件箱 */
  const [bulkList, setBulkList] = useState<TaskListKey>("inbox");
  /** 排序模式下的本地顺序；null = 没进排序模式 */
  const [sortIds, setSortIds] = useState<string[] | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [quick, setQuick] = useState("");
  const [quickParse, setQuickParse] = useState<null | {
    taskId: string;
    title: string;
    dueAt: number;
  }>(null);
  const { toasts, showToast } = useToast();
  // 「现在」只在这一层取一次，时间轴、卡片、倒计时都读它 ——
  // 两处各起一个定时器会各走各的，遇到跨零界点还会互相差一分钟。
  const now = useNowMinute();

  // 取数拆成「拉」和「落地」两半：effect 里必须先把 await 走完再 setState，
  // 否则会被 react-hooks/set-state-in-effect 判成级联渲染（同页其他地方也一样）。
  //
  // 四个 scope 一次拉全：tab 之间切换不该再等一次网络，「下一件」和「倒计时」
  // 也需要 today + upcoming 同时在手。读接口都走 tryRequest，端点没上线时
  // 各自返回 null，页面显示空态而不是红一片。
  const fetchAll = useCallback(async () => {
    const [today, upcoming, all, completed, summary, list, remSummary, src, pf, push, fires, daily] =
      await Promise.all([
        api.tasks.list("today"),
        api.tasks.list("upcoming"),
        api.tasks.list("all"),
        api.tasks.list("completed"),
        api.tasks.summary(),
        api.reminders.list("all"),
        api.reminders.summary(),
        api.reminders.sources(),
        api.reminders.prefs(),
        api.push.status(),
        api.reminders.firesAll(50),
        api.remind.get(),
      ]);
    return {
      tasks: {
        today: today?.items ?? [],
        upcoming: upcoming?.items ?? [],
        all: all?.items ?? [],
        completed: completed?.items ?? [],
      },
      summary,
      reminders: list?.items ?? [],
      remSummary,
      sources: src?.sources ?? [],
      prefs: pf,
      push,
      fires: fires?.items ?? [],
      daily,
    } satisfies Data;
  }, []);

  const reload = useCallback(async () => {
    const next = await fetchAll();
    setData(next);
  }, [fetchAll]);

  useEffect(() => {
    if (!authedId) return;
    let alive = true;
    void (async () => {
      try {
        const next = await fetchAll();
        if (!alive) return;
        setData(next);
      } catch {
        if (alive) showToast("error", "提醒加载失败，稍后再试");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // showToast 来自 useCallback，稳定；不放进依赖会让 lint 报 set-state-in-effect
  }, [authedId, fetchAll, showToast]);

  // 三处空值兜底都包一层 useMemo：直接写 `data?.reminders ?? []` 会每次渲染
  // 产生一个新数组，让下面几个 useMemo 的依赖每帧都变（lint 也会报）。
  const tasks = useMemo(() => data?.tasks ?? EMPTY_TASKS, [data]);
  const reminders = useMemo(() => data?.reminders ?? [], [data]);
  const sources = useMemo(() => data?.sources ?? [], [data]);
  const remSummary = data?.remSummary ?? null;
  const prefs = data?.prefs ?? null;
  const daily = data?.daily ?? null;

  const listLabel = LIST_LABEL;

  const sourceLabel = useMemo(() => {
    const map: Record<string, string> = {
      manual: "自己定的",
      system: "每日回顾",
      cet: "四六级",
      checkin: "打卡",
      delegate: "每日委托",
      focus: "番茄钟",
      timetable: "课表",
      deadline: "DDL",
      words: "背单词",
    };
    for (const s of sources) map[s.key] = s.label;
    return map;
  }, [sources]);

  const sourceOn = useMemo(() => {
    const map: Record<string, boolean> = { manual: true, system: true };
    for (const s of sources) map[s.key] = s.enabled;
    return map;
  }, [sources]);

  // ===== 今天已经响过的那几条 =====
  //
  // 每天 08:00 的提醒一旦响过，`next_fire_at` 就跳到明天 → `summary.today` 立刻丢掉它，
  // 「今天」这一组于是只剩下还没到点的。用户问「今天响了没有」时，页面答不出来。
  // 所以回流水里捞当天那几条：**状态只能从流水来**，不能从下一次推算。
  const firesToday = useMemo(() => {
    const map = new Map<string, ReminderFire>();
    if (now === null) return map;
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    const start = d.getTime();
    const end = start + 86_400_000;
    for (const f of data?.fires ?? []) {
      const t = toMs(f.planned_at);
      if (t < start || t >= end) continue;
      const prev = map.get(f.reminder_id);
      if (!prev || f.planned_at > prev.planned_at) map.set(f.reminder_id, f);
    }
    return map;
  }, [data?.fires, now]);

  // ===== 联动提醒的分组 =====
  //
  // `next_fire_at = null` 且 freq=once 的，是**已触发的一次性提醒**（僵尸条目）。
  // 它不能混进「接下来」：那里每一条都承诺「还会再来一次」，而它不会。
  const groups = useMemo(() => {
    const todayIds = new Set((remSummary?.today ?? []).map((i) => i.id));
    const live = reminders.filter((i) => i.enabled && i.next_fire_at);
    const ended = reminders.filter((i) => i.freq === "once" && !i.next_fire_at);
    const paused = reminders.filter(
      (i) => !i.enabled && !(i.freq === "once" && !i.next_fire_at)
    );
    // 今天 = 还没到点的 ∪ 今天已经响过的
    const today = live.filter((i) => todayIds.has(i.id) || firesToday.has(i.id));
    const todaySet = new Set(today.map((i) => i.id));
    const rest = live.filter((i) => !todaySet.has(i.id));
    if (now === null) {
      return { today, soon: rest, later: [] as ReminderItem[], ended, paused };
    }
    const endOfToday = new Date(now);
    endOfToday.setHours(24, 0, 0, 0);
    const cutoff = endOfToday.getTime() + 7 * 86_400_000;
    const soon = rest.filter((i) => toMs(i.next_fire_at as number) < cutoff);
    const later = rest.filter((i) => toMs(i.next_fire_at as number) >= cutoff);
    return { today, soon, later, ended, paused };
  }, [reminders, remSummary, now, firesToday]);

  // ===== 「接下来」按 7 天切一刀 =====
  //
  // 分组语义修正（方案 §7）：不再出现「本周」——它和"这周还剩几天"绑死，
  // 周日晚上看会显示一个空组。改成「接下来 7 天 / 更远」，边界与
  // 联动提醒的 soon/later 用同一条线，两类对象不会在同一屏上打架。
  const upcomingSplit = useMemo(() => {
    let cut = Number.POSITIVE_INFINITY;
    if (now !== null) {
      const e = new Date(now);
      e.setHours(24, 0, 0, 0);
      cut = e.getTime() + 7 * 86_400_000;
    }
    const soon: Task[] = [];
    const later: Task[] = [];
    for (const t of tasks.upcoming) {
      const at = t.due_at ?? t.remind_at;
      if (at && toMs(at) < cut) soon.push(t);
      else later.push(t);
    }
    return { soon, later };
  }, [tasks.upcoming, now]);

  // ===== 今天的时间轴：待办（remind_at）+ 联动提醒（next_fire_at）=====
  // 同一分钟的多条并成一枚：服务端本来就会把它们合并成一条通知，
  // 轴上并排画两枚只会互相压住，点起来也分不清。
  const axisEvents = useMemo<AxisEvent[]>(() => {
    // 先摊平成种子数组，再按时间排序合并。
    // 不用「取 Map 里的对象再原地 push」的写法：那会被
    // react-hooks/immutability 判成改写外部值（r.title 传进闭包即视为可变）。
    const seeds: { ts: number; id: string; title: string; kind: "task" | "event" }[] = [];
    for (const t of tasks.today) {
      if (t.remind_at) {
        seeds.push({ ts: toMs(t.remind_at), id: t.id, title: t.title, kind: "task" });
      }
    }
    for (const r of groups.today) {
      if (r.next_fire_at) {
        seeds.push({ ts: toMs(r.next_fire_at), id: r.id, title: r.title, kind: "event" });
      }
    }
    const out: AxisEvent[] = [];
    for (const s of [...seeds].sort((a, b) => a.ts - b.ts)) {
      const at = new Date(s.ts);
      const min = at.getHours() * 60 + at.getMinutes();
      const last = out[out.length - 1];
      if (last && last.min === min) {
        out[out.length - 1] = {
          ...last,
          count: last.count + 1,
          title: `${last.title}、${s.title}`,
          kind: last.kind === "task" || s.kind === "task" ? "task" : "event",
        };
      } else {
        out.push({
          id: s.id,
          min,
          label: fmtHM(at),
          count: 1,
          title: s.title,
          kind: s.kind,
        });
      }
    }
    return out;
  }, [tasks.today, groups.today]);

  // ===== 今天勾掉的条目 =====
  //
  // 后端 complete() 之后 `status` 就是 done，`scope=today/all` 里立刻查不到它 ——
  // 上一版前端干脆把卡片从列表里删掉，于是"点一下勾，卡片没了"。用户要的是
  // 市面 To-Do 那种：勾上 → 划一道线留在原地 → 想扔再点删除。
  //
  // 所以这里把**今天完成的**捞回来，接到当前列表末尾（done 统一沉底）。
  // 判据用 `completed_at` 而不是本地"刚点过"的集合：刷新、换设备、第二天早上
  // 打开都还是对的，而内存集合一刷新就没了。
  const doneToday = useMemo<Task[]>(() => {
    if (now === null) return NO_TASKS;
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    const start = Math.floor(d.getTime() / 1000);
    const end = start + 86_400;
    return tasks.completed.filter(
      (t) => t.completed_at !== null && t.completed_at >= start && t.completed_at < end
    );
  }, [tasks.completed, now]);


  // ===== 下一件 =====
  //
  // 「下一件」必须是**还没发生**的那一条：把上午 9 点已经过掉的条目顶在最上面，
  // 用户打开页面看到的是一段历史，而不是「接下来干什么」。
  // 今天的都过完了，就顺延到「接下来」的第一条；再没有，才退回今天里最晚的那条
  // （那说明今天有事没做完，仍然值得看）。
  const nextUp = useMemo<UpNext | null>(() => {
    const cands: UpNext[] = [];
    for (const t of tasks.today) {
      const at = t.remind_at ?? t.due_at;
      if (at) cands.push({ kind: "task", at, task: t });
    }
    for (const r of groups.today) {
      if (r.next_fire_at) cands.push({ kind: "event", at: r.next_fire_at, item: r });
    }
    cands.sort((a, b) => a.at - b.at);
    if (now === null) return cands[0] ?? null;

    const future = cands.find((c) => toMs(c.at) >= now);
    if (future) return future;

    const nextTask = tasks.upcoming.find((t) => t.due_at || t.remind_at);
    if (nextTask) {
      return { kind: "task", at: nextTask.remind_at ?? (nextTask.due_at as number), task: nextTask };
    }
    return cands.length ? cands[cands.length - 1] : null;
  }, [tasks.today, tasks.upcoming, groups.today, now]);

  // ===== 倒计时：`due_at` 的视图，不是第二套数据 =====
  //
  // 只放「还没到而将有截止日」的待办：今天/逾期的已经在上面列过了，
  // 在这儿再列一遍就是同一件事说两遍。这一块回答的是「这学期还剩什么」。
  const countdown = useMemo(() => {
    if (now === null) return [];
    return tasks.upcoming
      .filter((t) => t.due_at !== null)
      .sort((a, b) => (a.due_at as number) - (b.due_at as number))
      .slice(0, 5);
  }, [tasks.upcoming, now]);

  // ===== 动作 =====
  const openTaskDrawer = useCallback((task: Task | null) => {
    setEditingTask(task);
    setTaskDrawer(true);
  }, []);

  const openReminderDrawer = useCallback((item: ReminderItem | null) => {
    setEditingReminder(item);
    setReminderDrawer(true);
  }, []);

  const submitTask = useCallback(
    async (draft: TaskDraft) => {
      setBusy(true);
      try {
        if (editingTask) {
          // 全字段提交：显式 null 才能清空对方（后端用 exclude_unset 收，
          // 这里不用挑「改过的字段」—— 少发一个字段就等于「别动它」，
          // 而用户清空日期时正需要发 null）。
          const patch: TaskPatch = {
            title: draft.title,
            list_key: draft.list_key,
            priority: draft.priority,
            note: draft.note,
            due_at: draft.due_at,
            remind_at: draft.remind_at,
          };
          await api.tasks.patch(editingTask.id, patch);
          showToast("success", "已更新");
        } else {
          await api.tasks.create(draft);
          showToast("success", "已记下");
        }
        setTaskDrawer(false);
        setEditingTask(null);
        await reload();
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "保存失败");
      } finally {
        setBusy(false);
      }
    },
    [editingTask, reload, showToast]
  );

  const submitReminder = useCallback(
    async (draft: ReminderDraft) => {
      setBusy(true);
      try {
        if (editingReminder) {
          // 改时间落 override 还是 at：来源提醒（课表/委托/DDL）不该被写回来源，
          // 手工提醒没有来源，直接改自己的 at 更直白。
          //
          // ⚠️ 这里以前对 weekly 传的是空值，后端 `_validate` 会直接 400
          //    （「时间要写成 HH:MM」）—— 表现为「每周」提醒连改标题都保存不了。
          //    现在一律传真实时刻；频率只在「自己定的 / 每日回顾」上提交，
          //    来源提醒的频率跟着来源走，传上去反而会把来源的规则改掉。
          const isOwn =
            editingReminder.source === "manual" || editingReminder.source === "system";
          // 时刻一定要是字符串：`time` 输入框永远有值（默认 20:00），
          // 而 `override.at` 的类型不接受 null —— 以前 weekly 传 null 正是这里出的错。
          const at = (
            draft.at ??
            editingReminder.override?.at ??
            editingReminder.at ??
            "20:00"
          ).slice(0, 5);
          const body = isOwn
            ? { title: draft.title, at, freq: draft.freq, weekday: draft.weekday }
            : { title: draft.title, override: { title: draft.title, at } };
          await api.reminders.patch(editingReminder.id, body);
          showToast("success", "已更新");
        } else {
          await api.reminders.create(draft);
          showToast("success", "已建好，到时间会提醒你");
        }
        setReminderDrawer(false);
        setEditingReminder(null);
        await reload();
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "保存失败");
      } finally {
        setBusy(false);
      }
    },
    [editingReminder, reload, showToast]
  );

  /**
   * 一句话整理 → 真正写库。**逐条写，不假装原子**。
   *
   * 为什么不是一个批量端点：三条待办里坏一条，用户最想要的是"两条进去、一条告诉我为什么"，
   * 而不是整批回滚、白说一遍。`allSettled` 而不是 `all`，就是为了让好的那几条落地。
   *
   * 返回值只报"哪几条没进去 + 为什么"，界面上的"已加入 X 条"由抽屉自己算 ——
   * 两边都算一遍，迟早在某个分支上对不上。
   */
  const commitCapture = useCallback(
    async (picked: CaptureProposal[]): Promise<CommitOutcome> => {
      const results = await Promise.allSettled(
        picked.map((item) => {
          if (item.kind === "task") return api.tasks.create(taskDraftOf(item));
          const draft = reminderDraftOf(item);
          // 抽屉已经拦过一轮，这里再挡一次：宁可少写一条，也不要把提醒写成哑弹
          if (!draft) return Promise.reject(new Error("这条还没有提醒时间"));
          return api.reminders.create(draft);
        })
      );

      const okIds: string[] = [];
      const issues: Record<string, string> = {};
      let remind = 0;
      results.forEach((r, i) => {
        const item = picked[i];
        if (r.status === "fulfilled") {
          okIds.push(item.id);
          if (item.remind_at !== null || item.recurrence !== null) remind += 1;
        } else {
          issues[item.id] = commitFailMessage(r.reason);
        }
      });

      const bad = Object.keys(issues).length;
      // **只有全成功才出 toast。**
      // 有一条没加上时抽屉不关、底栏和行内已经把话说完了；这时候再弹一个
      // "已加入 1 条" 的 toast，正好压在主轴按钮上（真机截图里出现过），
      // 用户会以为是两个不同的结果。
      if (bad === 0 && okIds.length > 0) {
        showToast(
          "success",
          remind > 0
            ? `已加入 ${okIds.length} 条，其中 ${remind} 条会提醒你`
            : `已加入 ${okIds.length} 条`
        );
      }

      // 写成功就必须刷新：不刷新的话，用户去列表里看不到刚加的那条，会以为又失败了
      if (okIds.length > 0) await reload();
      return { okIds, issues };
    },
    [reload, showToast]
  );

  /**
   * 完成 / 取消完成。
   *
   * 先乐观更新（勾上立刻有反馈），失败再拉回真值并 toast ——
   * 等一次网络往返才出现勾，手感像卡住。
   *
   * ★ 乐观更新**不再把卡片从列表里删掉**（上一版就是这么写的，
   *   于是用户点一下勾，那条直接从眼前消失，看不到"做完了"这件事）。
   *   现在只翻转它自己的 status，卡片留在原位、变成划线态；reload 之后
   *   后端不再返回它，由 `doneToday`（今天完成的）把它接回列表末尾。
   *   一套逻辑把"打完勾立刻可见"和"刷新后仍然可见"同时解决。
   */
  const toggleTask = useCallback(
    async (task: Task) => {
      const willDone = task.status !== "done";
      const flip = (t: Task): Task =>
        t.id !== task.id
          ? t
          : {
              ...t,
              status: willDone ? "done" : "open",
              completed_at: willDone ? Math.floor(Date.now() / 1000) : null,
            };
      setData((prev) =>
        prev
          ? {
              ...prev,
              tasks: {
                today: prev.tasks.today.map(flip),
                upcoming: prev.tasks.upcoming.map(flip),
                all: prev.tasks.all.map(flip),
                // 取消完成要能从「已完成」里立刻消失；完成则不必在这里补，
                // reload 会带回来（这里补会和 today 重复显示两次）
                completed: willDone
                  ? prev.tasks.completed
                  : prev.tasks.completed.filter((t) => t.id !== task.id),
              },
            }
          : prev
      );
      try {
        const res = willDone
          ? await api.tasks.complete(task.id)
          : await api.tasks.reopen(task.id);
        if (!willDone && res?.remind_at && !res.reminder?.enabled) {
          showToast("info", "提醒时间已过，请重新设置");
        } else {
          showToast("success", willDone ? "完成了" : "已恢复");
        }
        await reload();
      } catch {
        showToast("error", "没改成，稍后再试");
        await reload();
      }
    },
    [reload, showToast]
  );

  /**
   * 就地改外观（图标 / 等级）。
   *
   * 不做乐观更新：这两个字段的价值就在于"看得见的差别"，写回失败却先
   * 变了色，用户会以为成功了。PATCH 只有一个字段，等一次往返不慢。
   */
  const patchStyle = useCallback(
    async (task: Task, patch: { icon?: string | null; priority?: TaskPriority }) => {
      try {
        await api.tasks.patch(task.id, patch);
        await reload();
      } catch {
        showToast("error", "没改成，稍后再试");
      }
    },
    [reload, showToast]
  );

  const snoozeTask = useCallback(async (task: Task) => {
      // 待办的「推迟」= 把它的提醒时间往后挪 10 分钟（改的是同一个 linked reminder）
      const base = task.remind_at ?? Math.floor(Date.now() / 1000);
      try {
        await api.tasks.patch(task.id, { remind_at: base + 600 });
        showToast("success", "提醒已推迟 10 分钟");
        await reload();
      } catch {
        showToast("error", "推迟失败，稍后再试");
      }
    },
    [reload, showToast]
  );

  const confirmDelete = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.kind === "bulk") {
        await api.tasks.bulk(pending.ids, "delete");
        setPicked([]);
        setMode("normal");
      } else if (pending.kind === "task") {
        await api.tasks.remove(pending.task.id);
      } else {
        await api.reminders.remove(pending.item.id);
      }
      showToast("success", "已删除");
      setPending(null);
      await reload();
    } catch {
      showToast("error", "删不掉，稍后再试");
    } finally {
      setBusy(false);
    }
  }, [pending, reload, showToast]);

  // ===== 多选 / 批量 =====
  //
  // 为什么整页只维护一个 `mode`：多选和排序都以「点一下」为主交互，
  // 两个模式同时开着，用户没法从界面判断这一下是选中、是拖动、还是完成。
  const exitMode = useCallback(() => {
    setMode("normal");
    setPicked([]);
    setSortIds(null);
    setDragId(null);
  }, []);

  const beginPick = useCallback(() => {
    setSortIds(null);
    setPicked([]);
    setMode("select");
  }, []);

  const togglePick = useCallback((task: Task) => {
    setPicked((prev) =>
      prev.includes(task.id) ? prev.filter((id) => id !== task.id) : [...prev, task.id]
    );
  }, []);

  const beginSort = useCallback((list: Task[]) => {
    setPicked([]);
    // 本地顺序 = 当前列表顺序。进排序模式时不重排一次：用户看到的第一帧
    // 必须就是他刚才看的那个顺序，否则「我明明什么也没动」。
    setSortIds(list.map((t) => t.id));
    setMode("sort");
  }, []);

  const moveSort = useCallback((id: string, delta: number) => {
    setSortIds((prev) => {
      if (!prev) return prev;
      const from = prev.indexOf(id);
      const to = from + delta;
      if (from < 0 || to < 0 || to >= prev.length) return prev;
      const next = prev.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }, []);

  // 拖拽用 pointer 事件，不用 HTML5 的 draggable：后者在手机浏览器上
  // 根本不触发（而这个页面的主视口就是 390）。捕获指针之后 pointermove
  // 全部发给把手，所以要用坐标反查「手指/鼠标下面是哪一行」。
  const dragFrom = useRef<string | null>(null);

  const onGripDown = useCallback((e: React.PointerEvent<HTMLButtonElement>, id: string) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragFrom.current = id;
    setDragId(id);
  }, []);

  const onGripMove = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const from = dragFrom.current;
    if (!from) return;
    const hit = document.elementFromPoint(e.clientX, e.clientY);
    const to =
      hit instanceof Element
        ? hit.closest<HTMLElement>("[data-task-id]")?.dataset.taskId
        : undefined;
    if (!to || to === from) return;
    setSortIds((prev) => {
      if (!prev) return prev;
      const a = prev.indexOf(from);
      const b = prev.indexOf(to);
      if (a < 0 || b < 0) return prev;
      const next = prev.slice();
      const [moved] = next.splice(a, 1);
      next.splice(b, 0, moved);
      return next;
    });
  }, []);

  const onGripUp = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    dragFrom.current = null;
    setDragId(null);
  }, []);

  const saveSort = useCallback(async () => {
    const ids = sortIds ?? [];
    const before = (data?.tasks[scope] ?? []).map((t) => t.id);
    // 没动就不发请求：一次「什么都没改」的保存不该在服务端留一条 updated_at
    if (!ids.length || ids.join("|") === before.join("|")) {
      exitMode();
      return;
    }
    setBusy(true);
    try {
      await api.tasks.reorder(ids);
      showToast("success", "顺序已保存");
      setMode("normal");
      setSortIds(null);
      await reload();
    } catch {
      // 失败就留在排序模式：拖了半天的顺序不能因为一次网络抖动直接丢掉
      showToast("error", "顺序没保存上，再点一次「完成」试试");
    } finally {
      setBusy(false);
    }
  }, [data, exitMode, reload, scope, showToast, sortIds]);

  const runBulk = useCallback(
    async (action: TaskBulkAction) => {
      if (!picked.length) return;
      // 删除照旧走二次确认：批量删更容易手滑，不因为「多选」就免掉这一步
      if (action === "delete") {
        setPending({ kind: "bulk", ids: picked });
        return;
      }
      setBusy(true);
      try {
        const res = await api.tasks.bulk(
          picked,
          action,
          action === "move" ? bulkList : undefined
        );
        const n = res.changed;
        showToast(
          n > 0 ? "success" : "info",
          action === "complete"
            ? `完成了 ${n} 条`
            : action === "reopen"
              ? `恢复了 ${n} 条`
              : `已移到「${LIST_LABEL[bulkList]}」${n ? ` · ${n} 条` : ""}`
        );
        setPicked([]);
        setMode("normal");
        await reload();
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "没成功，稍后再试");
      } finally {
        setBusy(false);
      }
    },
    [bulkList, picked, reload, showToast]
  );

  const toggleReminder = useCallback(
    async (item: ReminderItem) => {
      try {
        await api.reminders.patch(item.id, { enabled: !item.enabled });
        await reload();
      } catch {
        showToast("error", "改不动，稍后再试");
      }
    },
    [reload, showToast]
  );

  const snoozeReminder = useCallback(
    async (item: ReminderItem) => {
      try {
        await api.reminders.snooze(item.id, 10);
        showToast("success", "已推迟 10 分钟");
        await reload();
      } catch {
        showToast("error", "推迟失败，稍后再试");
      }
    },
    [reload, showToast]
  );

  const closeSource = useCallback(
    async (item: ReminderItem) => {
      try {
        const res = await api.reminders.setSource(item.source, false);
        await reload();
        showToast(
          "success",
          `已关闭「${res?.sources.find((s) => s.key === item.source)?.label ?? item.source}」的提醒`
        );
      } catch {
        showToast("error", "没关掉，稍后再试");
      }
    },
    [reload, showToast]
  );

  const savePrefs = useCallback(
    async (patch: Partial<Pick<ReminderPrefs, "quiet_start" | "quiet_end" | "daily_cap">>) => {
      try {
        const next = await api.reminders.savePrefs(patch);
        setData((prev) => (prev ? { ...prev, prefs: next } : prev));
        showToast("success", "已保存");
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "保存失败");
      }
    },
    [showToast]
  );

  /**
   * 「每天最多提醒几次」的 −/+ 步进。
   *
   * 只留一个 number 输入框时，用户要先点进去、清空、再敲数字；手机上还会弹全键盘。
   * 这个值实际只在 1–20 之间挪一两格，−/+ 比键盘快，也输不出 0 或 99。
   * 点一次就存（输入框要等 blur）—— 按下本身就是一次明确的动作。
   */
  const bumpCap = useCallback(
    (delta: number) => {
      const min = prefs?.cap_min ?? 1;
      const max = prefs?.cap_max ?? 20;
      const cur = prefs?.daily_cap ?? 6;
      const next = Math.max(min, Math.min(max, cur + delta));
      if (next === cur) return;
      setData((prev) =>
        prev?.prefs ? { ...prev, prefs: { ...prev.prefs, daily_cap: next } } : prev
      );
      void savePrefs({ daily_cap: next });
    },
    [prefs, savePrefs]
  );

  /** 「每天几点做回顾」。
   *
   * 为什么不复用 `savePrefs`：那个走 `/api/reminders/prefs`（免打扰/上限/来源开关），
   * 这个走 `/api/remind` —— 老端点，一个值同时决定 Web Push 的时刻和日历里那条
   * 每日事件的起点。两处混用会让「设置页改了、日历那条没动」变成玄学。
   */
  const saveDaily = useCallback(
    async (hour: number, minute: number) => {
      try {
        const next = await api.remind.set(hour, minute);
        setData((prev) => (prev ? { ...prev, daily: next } : prev));
        showToast("success", `每天 ${next.time} 提醒`);
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "时间没存上，再试一次");
      }
    },
    [showToast]
  );

  const toggleSource = useCallback(
    async (key: string, next: boolean) => {
      try {
        const res = await api.reminders.setSource(key, next);
        setData((prev) => (prev ? { ...prev, sources: res?.sources ?? prev.sources } : prev));
      } catch {
        showToast("error", "开关没生效，稍后再试");
      }
    },
    [showToast]
  );

  /** 时间轴节点 / 倒计时 → 滚到那张卡并闪一下，否则滚过去也不知道该看哪条 */
  const jump = useCallback((id: string) => {
    setFlash(id);
    const el = document.getElementById(`rem-card-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => setFlash((cur) => (cur === id ? null : cur)), 1600);
  }, []);

  // ===== 快速添加 =====
  //
  // 只做确定性解析（frontend/src/components/reminders/quickParse.ts），不接模型：
  // 这一行是最高频入口，一次网络往返 + 一次 LLM 调用会让「记一条作业」变成两秒。
  // ★ 解析出截止时间时**不自动设提醒**，只在下面给一排 chip 让用户自己选。
  const addQuick = useCallback(async () => {
    const text = quick.trim();
    if (!text) return;
    const { parseQuickAdd } = await import("@/components/reminders/quickParse");
    const parsed = parseQuickAdd(text, new Date());
    if (!parsed.title) return;
    setBusy(true);
    try {
      const created = await api.tasks.create({
        title: parsed.title,
        list_key: parsed.list_key as TaskListKey | undefined,
        priority: parsed.priority,
        due_at: parsed.due_at ?? null,
        remind_at: parsed.remind_at ?? null,
      });
      setQuick("");
      showToast("success", "已记下");
      // 「只设了截止」时给一排「要不要提醒」的 chip —— 不偷偷替用户设
      if (created?.due_at && !created.remind_at) {
        setQuickParse({ taskId: created.id, title: created.title, dueAt: created.due_at });
      }
      await reload();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "添加失败");
    } finally {
      setBusy(false);
    }
  }, [quick, reload, showToast]);

  const applyQuickRemind = useCallback(
    async (offsetMin: number | null) => {
      if (!quickParse) return;
      const target =
        offsetMin === null ? null : Math.floor(quickParse.dueAt / 60) * 60 - offsetMin * 60;
      try {
        if (target !== null) {
          await api.tasks.patch(quickParse.taskId, { remind_at: target });
          showToast("success", "提醒已设好");
        }
        setQuickParse(null);
        await reload();
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "没设上");
      }
    },
    [quickParse, reload, showToast]
  );

  // ===== 渲染 =====
  const scopeCount: Record<Scope, number> = {
    today: tasks.today.length,
    upcoming: tasks.upcoming.length,
    all: tasks.all.length,
    completed: tasks.completed.length,
  };

  const card = (item: ReminderItem) => (
    <ReminderCard
      key={item.id}
      item={item}
      sourceLabel={sourceLabel[item.source] || item.source}
      onEdit={openReminderDrawer}
      onToggle={toggleReminder}
      onDelete={(it) => setPending({ kind: "reminder", item: it })}
      onCloseSource={closeSource}
      onSnooze={snoozeReminder}
      onOpenSources={() => setTab("settings")}
      sourceOff={sourceOn[item.source] === false}
      flash={flash === item.id}
      nowNumber={now}
      fire={firesToday.get(item.id) ?? null}
    />
  );

  const taskCard = (task: Task) => (
    <TaskCard
      key={task.id}
      task={task}
      now={now}
      dim={scope === "completed" || task.status === "done"}
      flash={flash === task.id}
      pick={mode === "select"}
      picked={picked.includes(task.id)}
      onToggle={toggleTask}
      onEdit={openTaskDrawer}
      onDelete={(t) => setPending({ kind: "task", task: t })}
      onSnooze={snoozeTask}
      onPick={togglePick}
      onStyle={patchStyle}
    />
  );

  /** 空分组整段不渲染：三个「0 条」不提供任何信息，只占首屏 */
  /**
   * 区里的小分段（「今天」「接下来 7 天」「更远」「今天完成」）。
   * 只在区里不止一段时出现 —— 只有一段还加一行标题，等于把「待办」写两遍。
   */
  const subBlock = (title: string, children: React.ReactNode[]) => {
    if (children.length === 0) return null;
    return (
      <div className="rem-sub" key={title}>
        <h3 className="rem-subhd">
          {title}
          <span>· {children.length} 条</span>
        </h3>
        <div className="space-y-2">{children}</div>
      </div>
    );
  };

  /** 当前 tab 的待办：多选与排序都只作用在它上面 */
  const scopeTasks = tasks[scope];
  const allPicked = scopeTasks.length > 0 && picked.length === scopeTasks.length;

  /** 排序模式下的行。按本地顺序取；取不到的 id 跳过 —— 别的端刚删掉一条时，
      整段不该因此渲染失败。 */
  const sortRows = (() => {
    if (!sortIds) return [];
    const map = new Map(scopeTasks.map((t) => [t.id, t]));
    return sortIds.map((id) => map.get(id)).filter((t): t is Task => Boolean(t));
  })();

  /**
   * tab 下面那一行。
   *
   * 正常模式下只放两个入口（排序 / 多选），**不常驻**批量按钮：
   * 一屏里同时出现「完成」和「批量完成」，人会以为它们是一回事。
   */
  const toolBar =
    mode === "select" ? (
      <div className="rem-selbar">
        <b>已选 {picked.length} 条</b>
        <button
          type="button"
          className="rem-toolbtn"
          onClick={() => setPicked(allPicked ? [] : scopeTasks.map((t) => t.id))}
        >
          {allPicked ? "取消全选" : "全选"}
        </button>
        <button type="button" className="rem-toolbtn" onClick={exitMode}>
          退出多选
        </button>
      </div>
    ) : mode === "sort" ? (
      <div className="rem-selbar">
        <b>拖动把手调整顺序</b>
        <button type="button" className="rem-toolbtn" disabled={busy} onClick={exitMode}>
          取消
        </button>
        <button
          type="button"
          className="rem-toolbtn"
          data-primary="1"
          disabled={busy}
          onClick={() => void saveSort()}
        >
          {busy ? "保存中…" : "完成"}
        </button>
      </div>
    ) : scopeTasks.length > 0 ? (
      <div className="rem-toolrow">
        {scope !== "completed" && scopeTasks.length > 1 ? (
          <button type="button" className="rem-toolbtn" onClick={() => beginSort(scopeTasks)}>
            排序
          </button>
        ) : null}
        <button type="button" className="rem-toolbtn" onClick={beginPick}>
          多选
        </button>
      </div>
    ) : null;

  /** 排序模式只留下「可排序的那几行」：日程提醒的时刻是来源定的，挪不动，
      混在一起只会让人拖了没反应。 */
  const sortSection =
    mode === "sort" ? (
      <section className="mt-4">
        <h3 className="rem-grouphead">
          调整顺序
          <span>· 拖动，或用方向键 ↑ ↓ 调整</span>
        </h3>
        <div className="space-y-2">
          {sortRows.map((t) => (
            <SortRow
              key={t.id}
              task={t}
              now={now}
              dragging={dragId === t.id}
              onDown={(e) => onGripDown(e, t.id)}
              onMove={onGripMove}
              onUp={onGripUp}
              onKey={(e) => {
                if (e.key === "ArrowUp") {
                  e.preventDefault();
                  moveSort(t.id, -1);
                } else if (e.key === "ArrowDown") {
                  e.preventDefault();
                  moveSort(t.id, 1);
                }
              }}
            />
          ))}
        </div>
      </section>
    ) : null;

  /* 添加入口（2026-11-13 第三批合并）。
     合并前这一页有**三个**入口，而且散在三个位置：
       · 页头「+ 新建」→ 表单抽屉（精确控制：优先级、图标、清单、提醒）
       · 列表上方那条大的「一句话添加」→ AI 拆分抽屉
       · 列表最底下一行输入框 → 直接加一条
     后两个说的是同一件事（"说一句就行"），却一个在顶上、一个要滚到底才看得见；
     空列表时那句引导文案指向的输入框，恰恰在屏幕外。
     现在合成一条：输入框 + 「添加」+ 「整理」。回车/添加 = 直接记一条；
     「整理」= 把一整段话拆成多条（原 AI 抽屉，能力一点没少）。
     页头「+ 新建」保留 —— 它管的是"我要精确设字段"，和"先记下来"是两件事。 */
  const quickAddBar =
    mode === "normal" && scope !== "completed" ? (
      <div className="rem-quick rem-quick--top">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void addQuick();
          }}
        >
          <input
            className="input"
            value={quick}
            placeholder="记一条：明天 9 点交作业"
            aria-label="快速添加待办"
            onChange={(e) => setQuick(e.target.value)}
          />
          <button type="submit" className="btn-import" disabled={!quick.trim() || busy}>
            添加
          </button>
          <button
            type="button"
            className="btn-import rem-quick-ai"
            onClick={() => setCaptureOpen(true)}
            aria-label="自动整理：把一整段话拆成几条待办和提醒"
            title="把一整段话拆成几条"
          >
            <Spark className="rem-quick-spark" />
            整理
          </button>
        </form>
        {quickParse ? (
          <div className="rem-quickchip" role="group" aria-label="要不要设提醒">
            <span>「{quickParse.title}」要提前提醒吗？</span>
            <button type="button" onClick={() => void applyQuickRemind(60)}>
              提前 1 小时
            </button>
            <button type="button" onClick={() => void applyQuickRemind(0)}>
              到时间提醒
            </button>
            <button type="button" onClick={() => void applyQuickRemind(null)}>
              不提醒
            </button>
          </div>
        ) : null}
      </div>
    ) : null;

  // ===== 当前 tab 的两个区（待办 / 提醒）=====
  //
  // 每一段是一个小标题 + 若干条。段的顺序就是阅读顺序：
  // 近的在前、远的在后、刚做完的垫底。
  const taskBlocks: { title: string; items: Task[] }[] = (() => {
    switch (scope) {
      case "today":
        return [{ title: "今天", items: tasks.today }];
      case "upcoming":
        return [
          { title: "接下来 7 天", items: upcomingSplit.soon },
          { title: "更远", items: upcomingSplit.later },
        ];
      case "completed":
        return [{ title: "已完成", items: tasks.completed }];
      default:
        return [{ title: "全部", items: tasks.all }];
    }
  })();

  // 今天完成的接在后面，单独一段。**不并进原来的段**：并进去就得重排时间顺序，
  // 一条刚勾掉的卡片会插到两小时前的位置，用户会以为点错了。
  const openIds = new Set(taskBlocks.flatMap((b) => b.items.map((t) => t.id)));
  const doneTail = scope === "completed" ? NO_TASKS : doneToday.filter((t) => !openIds.has(t.id));

  const remBlocks: { title: string; items: ReminderItem[] }[] = (() => {
    switch (scope) {
      case "today":
        return [{ title: "今天", items: groups.today }];
      case "upcoming":
        return [
          { title: "接下来 7 天", items: groups.soon },
          { title: "更远", items: groups.later },
        ];
      case "completed":
        return [];
      default:
        return [
          { title: "定时提醒", items: groups.today.concat(groups.soon, groups.later) },
          { title: "已结束", items: groups.ended },
          { title: "已暂停", items: groups.paused },
        ];
    }
  })();

  const taskTotal = taskBlocks.reduce((n, b) => n + b.items.length, 0) + doneTail.length;
  const remTotal = remBlocks.reduce((n, b) => n + b.items.length, 0);
  const showTaskSub = taskBlocks.length + (doneTail.length > 0 ? 1 : 0) > 1;
  const showRemSub = remBlocks.length > 1;

  const taskEmpty =
    scope === "today"
      ? "今天没有待办。"
      : scope === "upcoming"
        ? "接下来还没有安排。"
        : scope === "completed"
          ? "还没有完成的待办。"
          : "还没有待办。";
  const remEmpty =
    scope === "today"
      ? "今天没有定时提醒。"
      : scope === "upcoming"
        ? "接下来没有定时提醒。"
        : "还没有定时提醒。";

  /** 今天两区全空时兜底：把最近要做的几条挪上来，别让首屏一片空白 */
  const fallbackTasks =
    scope === "today" && taskTotal === 0 && remTotal === 0 ? tasks.upcoming.slice(0, 3) : NO_TASKS;

  const listView = (
    <>
      <PushHealth status={data?.push ?? null} onOpen={() => setTab("settings")} />

      {/* 范围 tab：四个视图的语义见实施单 §5.3。逾期不会消失，它留在「今天」 */}
      <div className="rem-tabs" role="group" aria-label="待办范围">
        {SCOPE_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={scope === o.value}
            onClick={() => {
              setScope(o.value);
              // 换 tab 就退出多选/排序：选中的 id 属于上一个列表，
              // 留着它们会在新列表里执行到看不见的行上。
              exitMode();
            }}
          >
            {o.label}
            <span className="rem-tabcount">{scopeCount[o.value]}</span>
          </button>
        ))}
      </div>

      {toolBar}

      {/* 多选 / 排序时不显示「下一件」：它会抢走视线，而它上面那颗勾
          作用的对象又不在选中的那几条里 */}
      {nextUp && scope !== "completed" && mode === "normal" ? (
        <NextCard
          next={nextUp}
          now={now}
          listLabel={listLabel}
          sourceLabel={sourceLabel}
          onComplete={toggleTask}
          onSnooze={snoozeReminder}
          onEditTask={openTaskDrawer}
          onEditEvent={openReminderDrawer}
        />
      ) : null}

      {/* 快速添加已经合并到列表上方那一条（见 quickAddBar）。
          原来这里还有一行输入框，要滚到底才看得见 —— 屏幕外的入口等于没有。 */}

      {sortSection}

      {/* ===== 时间轴：不随 tab 变 =====
          上一版只在「今天」这个 tab 渲染，切到「全部」整块消失，用户以为
          时间轴没了。它表示的永远是**今天**，跟当前 tab 筛的是哪一批无关，
          所以放在 tab 语义之外：任何 tab 都能看到「今天忙在哪个时段」。 */}
      {mode !== "sort" ? (
        <div className="card rem-axiscard">
          <div className="rem-axis-hd">
            <b>今天</b>
            <span>
              {axisEvents.length > 0 ? `${axisEvents.length} 个时间点` : "今天没有安排"}
            </span>
          </div>
          <TodayAxis
            events={axisEvents}
            now={now}
            quietStart={prefs?.quiet_start}
            quietEnd={prefs?.quiet_end}
            onJump={jump}
          />
        </div>
      ) : null}

      {/* ===== 待办区 =====
          区域常驻，哪怕 0 条 —— 「这里是空的」和「这里没有这个区」是两件事，
          上一版空分组 return null，用户在一屏里什么都找不到。 */}
      {mode !== "sort" ? (
        <section className="rem-sect" data-kind="task" aria-label="待办">
          <div className="rem-sect-hd">
            <h2 className="rem-sect-title">待办</h2>
            <span className="rem-sect-n">{taskTotal} 条</span>
          </div>
          {showTaskSub ? (
            taskBlocks.map((b) => subBlock(b.title, b.items.map(taskCard)))
          ) : (
            <div className="space-y-2">{taskBlocks[0]?.items.map(taskCard)}</div>
          )}
          {doneTail.length > 0 ? subBlock("今天完成", doneTail.map(taskCard)) : null}
          {taskTotal === 0 && doneTail.length === 0 ? (
            <div className="rem-sect-empty">
              <p>{taskEmpty}</p>
              {/* 今天一条都没有时，把最近要做的挪上来 —— 首屏全空会让人以为
                  数据丢了，而"今天真没事"和"清单是空的"看起来一模一样 */}
              {fallbackTasks.length > 0 ? (
                <>
                  <p className="rem-sect-hint">今天没有安排，这是接下来的：</p>
                  <div className="space-y-2">{fallbackTasks.map(taskCard)}</div>
                </>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {/* ===== 提醒区 =====
          「已完成」没有提醒：联动提醒没有完成态（勾掉它，来源那边照样来）。 */}
      {mode !== "sort" && scope !== "completed" ? (
        <section className="rem-sect" data-kind="rem" aria-label="定时提醒">
          <div className="rem-sect-hd">
            <h2 className="rem-sect-title">提醒</h2>
            <span className="rem-sect-n">{remTotal} 条</span>
          </div>
          {remTotal === 0 ? (
            <div className="rem-sect-empty">
              <p>{remEmpty}</p>
            </div>
          ) : showRemSub ? (
            remBlocks.map((b) => subBlock(b.title, b.items.map(card)))
          ) : (
            <div className="space-y-2">{remBlocks[0]?.items.map(card)}</div>
          )}
        </section>
      ) : null}

      {mode !== "sort" && scope === "today" && countdown.length > 0 ? (
        <section className="mt-4">
          <h3 className="rem-grouphead">
            倒计时
            <span>· {countdown.length} 项</span>
            <button type="button" className="rem-groupmore" onClick={() => setScope("upcoming")}>
              查看全部
            </button>
          </h3>
          <div className="rem-count">
            {countdown.map((t) => (
              <button
                key={t.id}
                type="button"
                className="rem-count-row"
                onClick={() => openTaskDrawer(t)}
              >
                <b>{t.due_at ? daysLeft(t.due_at, now ?? toMs(t.due_at)) : 0} 天</b>
                <span className="rem-count-title">{t.title}</span>
                <span className="rem-count-date">
                  {t.due_at ? shortDay(t.due_at, now ?? toMs(t.due_at)) : ""}
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {/* 只响一次、没有「完成」可言的重复提醒（每天 22:00 复盘）仍然是提醒，
          不是待办 —— 给它留一个次入口，但不要做成「新建」的第二个主按钮。
          文案不许再写「没有完成语义」：那是开发者的分类型由，不是用户的话。 */}
      {mode === "normal" ? (
        <p className="rem-footlink">
          <button type="button" onClick={() => openReminderDrawer(null)}>
            只要到时间提醒，不用勾完成？新建提醒
          </button>
        </p>
      ) : null}
    </>
  );

  const isEmpty =
    !loading &&
    tasks.all.length === 0 &&
    tasks.completed.length === 0 &&
    reminders.length === 0;

  return (
    <PageShell
      compact
      title="待办"
      sub="设了提醒时间，到时会通知你；课表、打卡的提醒会自动排好。"
      actions={
        <div className="flex items-center gap-2">
          <div className="seg" role="group" aria-label="视图">
            <button type="button" aria-pressed={tab === "list"} onClick={() => setTab("list")}>
              列表
            </button>
            <button
              type="button"
              aria-pressed={tab === "settings"}
              onClick={() => setTab("settings")}
            >
              设置
            </button>
          </div>
          {tab === "list" ? (
            <button type="button" className="btn-new" onClick={() => openTaskDrawer(null)}>
              + 新建
            </button>
          ) : null}
        </div>
      }
    >
      {loading ? <SkeletonLines widths={["70%", "90%", "55%", "80%"]} /> : null}

      {!loading && tab === "list" ? (
        <>
          {quickAddBar}
          {isEmpty ? (
            <Callout icon="bulb">
              <b>还没有待办</b>
              <br />
              在上面那行直接写「明天 9 点交高数作业」，或者点右上角「+ 新建」。
              <br />
              只设截止时间，不会收到提醒。想被提醒，记得打开「提醒我」。
            </Callout>
          ) : (
            listView
          )}
        </>
      ) : null}

      {!loading && tab === "settings" ? (
        <div className="space-y-4">
          {/* 提醒权限排最前：这一屏里只有它管「响不响」，底下三个都在管
              「响几次、响哪些」。权限没开的时候，后面三项调了也不会响。 */}
          <ReminderAccess />
          {/* 紧跟在权限卡后面：上面那张管「这台设备响不响」，这张管
              「这台设备根本响不了的时候怎么办」。两件事连着发生，放一起
              用户才能在看到"这台收不到"之后立刻找到出路。 */}
          <MailAccess />
          {/* 紧跟着邮件卡：上面两张都在回答「这台设备响不响」，
              这张回答「上课这件事怎么保证响」。课表是唯一有确切时刻的数据，
              所以它走系统日历，而不是再排一条推送 —— 安卓的推送在国内本来就不通。 */}
          <CourseCalendar />

          {/* 「每天几点」在免打扰之前：它管「什么时候提醒」，免打扰管「什么时候
              不提醒」，两件事成对，但前者是主设置。 */}
          {daily ? (
            <section className="card card-pad">
              <h3 className="card-title">每天几点给你做回顾</h3>
              <p className="mt-1 text-[var(--fs-sm)] text-[var(--text-3)]">
                到点会把今天剩下的待办、六级和打卡合成一条通知。这里只改时间，
                要不要收在下面提醒列表的「每日回顾」那条里。
              </p>
              {/* 与「免打扰」同一个控件（type=time），不再用两个 select。
                  四种「选时间」并排出现，比配色不一致更容易被看见 —— 控件是一套语法。
                  step=900 让原生选择器只给 00/15/30/45，和 service 的档位对齐；
                  真被手动键入别的分钟，snapMinute 会在提交前吸附掉。 */}
              {/* 说明句单独一行，不和控件并排。并排时 390px 下留给它的是
                  一条不到 190px 的窄柱，「还没设置过 · 现在按默认的 20:00」
                  被折成四行 —— 一行说明看起来像一段正文，卡片立刻变高。 */}
              <div className="mt-3 flex items-center gap-2">
                <input
                  type="time"
                  className="input !w-auto"
                  aria-label="每天几点做回顾"
                  step={900}
                  value={dailyTimeValue(daily)}
                  onChange={(e) => {
                    const [h, m] = e.target.value.split(":").map(Number);
                    if (!Number.isFinite(h) || !Number.isFinite(m)) return;
                    void saveDaily(h, snapMinute(daily, m));
                  }}
                />
              </div>
              <p className="mt-2 text-[var(--fs-xs)] text-[var(--text-3)]">{dailyHint(daily)}</p>
            </section>
          ) : null}

          <section className="card card-pad">
            <h3 className="card-title">免打扰时间</h3>
            <p className="mt-1 text-[var(--fs-sm)] text-[var(--text-3)]">
              这段时间里的提醒<b>不会发出，也不会事后补发</b>。
            </p>
            <div className="mt-3 flex items-center gap-2">
              <input
                type="time"
                className="input !w-auto"
                value={prefs?.quiet_start || "23:00"}
                onChange={(e) =>
                  setData((p) =>
                    p?.prefs ? { ...p, prefs: { ...p.prefs, quiet_start: e.target.value } } : p
                  )
                }
                onBlur={(e) => void savePrefs({ quiet_start: e.target.value })}
              />
              <span className="text-[var(--fs-xs)] text-[var(--text-3)]">到</span>
              <input
                type="time"
                className="input !w-auto"
                value={prefs?.quiet_end || "07:00"}
                onChange={(e) =>
                  setData((p) =>
                    p?.prefs ? { ...p, prefs: { ...p.prefs, quiet_end: e.target.value } } : p
                  )
                }
                onBlur={(e) => void savePrefs({ quiet_end: e.target.value })}
              />
            </div>
          </section>

          <section className="card card-pad">
            <h3 className="card-title">每天最多提醒几次</h3>
            <p className="mt-1 text-[var(--fs-sm)] text-[var(--text-3)]">
              同一分钟的多条提醒会合并成一条通知，合并后算一次。这个上限只限制发出多少条，
              不影响你建多少提醒。
            </p>
            {/* − [6] + 步进。输入框留着（可以直接敲），但不再是唯一入口 ——
                只有它时，手机上得先弹全键盘才能把这个数挪一格。 */}
            <div className="mt-3 flex items-center gap-2">
              <div className="num-step">
                <button
                  type="button"
                  className="num-step-btn"
                  aria-label="少一次"
                  disabled={(prefs?.daily_cap ?? 6) <= (prefs?.cap_min ?? 1)}
                  onClick={() => bumpCap(-1)}
                >
                  −
                </button>
                <input
                  type="number"
                  inputMode="numeric"
                  min={prefs?.cap_min ?? 1}
                  max={prefs?.cap_max ?? 20}
                  className="input num-step-in"
                  aria-label="每天最多提醒几次"
                  value={prefs?.daily_cap ?? 6}
                  onChange={(e) =>
                    setData((p) =>
                      p?.prefs ? { ...p, prefs: { ...p.prefs, daily_cap: Number(e.target.value) } } : p
                    )
                  }
                  onBlur={(e) => void savePrefs({ daily_cap: Number(e.target.value) })}
                />
                <button
                  type="button"
                  className="num-step-btn"
                  aria-label="多一次"
                  disabled={(prefs?.daily_cap ?? 6) >= (prefs?.cap_max ?? 20)}
                  onClick={() => bumpCap(1)}
                >
                  +
                </button>
              </div>
              <span className="text-[var(--fs-xs)] text-[var(--text-3)]">
                次（{prefs?.cap_min ?? 1}–{prefs?.cap_max ?? 20}）
              </span>
            </div>
          </section>

          <section className="card card-pad">
            <h3 className="card-title">哪些功能会提醒你</h3>
            <p className="mt-1 text-[var(--fs-sm)] text-[var(--text-3)]">
              关掉一个功能，它安排的提醒都会停。你自己建的待办和提醒不受影响。
            </p>
            {/* 两列：7 个来源原来竖排 7 行、每行只有「名字 + 开关」，
                右半边全是空的；分两列后行数减半，也不用滚动就看全。
                开关**跟在名字后面**而不是推到列尾 —— 两列时列尾相邻，
                会读成「四六级 ▢ 打卡 ▢」，分不清哪个开关属于谁。 */}
            <div className="rem-srclist mt-3">
              {sources.map((s) => (
                <div key={s.key} className="rem-src">
                  <span className="text-[var(--fs-md)] text-[var(--text-1)]">{s.label}</span>
                  <Switch
                    checked={s.enabled}
                    label={s.label}
                    onChange={(next) => void toggleSource(s.key, next)}
                  />
                </div>
              ))}
            </div>
            {/* 设备清单已经搬到这一页最上面的「提醒权限」卡里（2026-11-11）。
                这里不再留一句「去设置里管」——那句话本身就是要被删掉的东西。 */}
          </section>

          {prefs ? (
            <p className="text-[var(--fs-sm)] text-[var(--text-3)]">
              免打扰 {prefs.quiet_start}–{prefs.quiet_end} · 每天最多提醒 {prefs.daily_cap} 次
            </p>
          ) : null}
        </div>
      ) : null}

      {/* 批量操作条：只在「选中了东西」时出现。
          空着也挂一条底栏会一直盖住列表最后一行，而它此刻没有任何可做的事。 */}
      {mode === "select" && picked.length > 0 ? (
        <div className="rem-bulkbar" role="group" aria-label="批量操作">
          <b className="rem-bulkn">{picked.length}</b>
          {scope === "completed" ? (
            <button
              type="button"
              className="rem-bulkbtn"
              data-primary="1"
              disabled={busy}
              onClick={() => void runBulk("reopen")}
            >
              恢复
            </button>
          ) : (
            <>
              <button
                type="button"
                className="rem-bulkbtn"
                data-primary="1"
                disabled={busy}
                onClick={() => void runBulk("complete")}
              >
                {busy ? "处理中…" : "完成"}
              </button>
              <span className="rem-bulkmove">
                <span aria-hidden="true">移到</span>
                <select
                  value={bulkList}
                  aria-label="移到哪个清单"
                  disabled={busy}
                  onChange={(e) => setBulkList(e.target.value as TaskListKey)}
                >
                  {(Object.keys(LIST_LABEL) as TaskListKey[]).map((k) => (
                    <option key={k} value={k}>
                      {LIST_LABEL[k]}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="rem-bulkbtn"
                  disabled={busy}
                  onClick={() => void runBulk("move")}
                >
                  移动
                </button>
              </span>
            </>
          )}
          <button
            type="button"
            className="rem-bulkbtn"
            data-danger="1"
            disabled={busy}
            onClick={() => void runBulk("delete")}
          >
            删除
          </button>
        </div>
      ) : null}

      {/* AI 整理（Phase A）：只在有「现在」的时候挂 —— 抽屉里要按本地日分组，
          没有 now 就分不出「今天/明天」，而渲染期又不许自己取时间。 */}
      {captureOpen && now !== null ? (
        <AiCaptureDrawer
          now={now}
          onClose={() => setCaptureOpen(false)}
          onCommit={commitCapture}
        />
      ) : null}

      <TaskDrawer
        key={editingTask ? `edit-${editingTask.id}-${editingTask.updated_at}` : "new-task"}
        open={taskDrawer}
        editing={editingTask}
        busy={busy}
        onClose={() => {
          setTaskDrawer(false);
          setEditingTask(null);
        }}
        onSubmit={submitTask}
      />

      <ReminderDrawer
        key={editingReminder ? `edit-${editingReminder.id}` : "new-reminder"}
        open={reminderDrawer}
        editing={editingReminder}
        sourceName={editingReminder ? sourceLabel[editingReminder.source] || editingReminder.source : ""}
        sources={sources.filter((s) => s.enabled)}
        busy={busy}
        onClose={() => {
          setReminderDrawer(false);
          setEditingReminder(null);
        }}
        onSubmit={submitReminder}
      />

      <ConfirmDelete
        open={pending !== null}
        title={
          pending?.kind === "task"
            ? `删除「${pending.task.title}」？`
            : pending?.kind === "reminder"
              ? `删除「${pending.item.title}」？`
              : pending?.kind === "bulk"
                ? `删除选中的 ${pending.ids.length} 条？`
                : ""
        }
        note={
          pending?.kind === "bulk"
            ? "删除后不能恢复，这些待办关联的提醒也会一起取消。"
            : pending?.kind === "task"
              ? "删除后不能恢复，关联的提醒也会一起取消。"
              : "删除后不能恢复，这条的提醒记录也不会再显示。"
        }
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={() => void confirmDelete()}
      />

      <ToastHost toasts={toasts} />
    </PageShell>
  );
}
