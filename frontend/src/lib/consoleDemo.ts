// 演示数据。
//
// ⚠️ 只在「接口确认不存在」时使用（probe 的 absent 态），并且界面上必须挂
// 「演示数据」标记 —— 让看的人一眼知道这不是真实数据。
// 接口一上线，这些常量就再也不会被读到。
//
// 内容取自 `prototypes/知穗_新页面设计稿.html`，但**时间必须自洽**：
// 设计稿里写「今天 21:00（还有 2 小时 14 分）」是因为它出图时正好 18:46；
// 照抄到页面上就会出现「每天 21:00 / 下次触发 02:09」这种自相矛盾的演示。
// 所以这里一律按「相对当前的 21:00」推算，保证截图里的每个时刻都说得通。
import type {
  AgentTool,
  DailyReport,
  Delegate,
  DelegateRun,
  ScheduleHealth,
  ScheduleJob,
} from "./types";

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const NOW = new Date();
const hhmm = (d: Date) =>
  `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const ymd = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** 下一次 HH:MM（今天已过就是明天） */
function nextAt(hour: number, minute: number): number {
  const d = new Date(NOW);
  d.setHours(hour, minute, 0, 0);
  if (d.getTime() <= NOW.getTime()) d.setDate(d.getDate() + 1);
  return d.getTime();
}

/** 下一个工作日 HH:MM */
function nextWeekdayAt(hour: number, minute: number): number {
  const d = new Date(nextAt(hour, minute));
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return d.getTime();
}

/** 最近一次已发生的 HH:MM */
function lastAt(hour: number, minute: number, daysAgo = 0): number {
  const d = new Date(NOW);
  d.setHours(hour, minute, 0, 0);
  if (d.getTime() > NOW.getTime()) d.setDate(d.getDate() - 1);
  d.setDate(d.getDate() - daysAgo);
  return d.getTime();
}

/** 复盘/投递统一在 21:00 —— 让列表、下次触发、执行历史三处对得上 */
const AT_H = 21;
const AT_M = 0;

export const DEMO_TOOLS: AgentTool[] = [
  { name: "kb_search", category: "read", defer: false, retryable: false, needs_approval: false, max_output_chars: 4000, cost_hint: "常规" },
  { name: "web_search", category: "read", defer: false, retryable: true, needs_approval: false, max_output_chars: 4000, cost_hint: "常规" },
  { name: "calculator", category: "read", defer: false, retryable: false, needs_approval: false, max_output_chars: 1000, cost_hint: "常规" },
  { name: "get_weather", category: "read", defer: false, retryable: true, needs_approval: false, max_output_chars: 1000, cost_hint: "常规" },
  { name: "download_file", category: "write", defer: false, retryable: true, needs_approval: true, max_output_chars: 2000, cost_hint: "偏高" },
];

export const DEMO_HEALTH: ScheduleHealth = {
  status: "up",
  last_heartbeat: NOW.getTime() - 12_000,
  pid: 28461,
  age_seconds: 12,
};

export const DEMO_JOBS: ScheduleJob[] = [
  { job_key: "delegate.dispatch", cron: "*/5 * * * *", enabled: true, last_run_at: NOW.getTime() - 60_000, last_status: "succeeded" },
  { job_key: "delegate.retry", cron: "7,22,37,52 * * * *", enabled: true, last_run_at: NOW.getTime() - 7 * MIN, last_status: "succeeded" },
  { job_key: "report.daily", cron: "0 21 * * *", enabled: true, last_run_at: NOW.getTime() - DAY, last_status: "succeeded" },
];

export const DEMO_DELEGATES: Delegate[] = [
  {
    id: "demo-1",
    name: "每日学习复盘",
    kind: "review",
    schedule: { freq: "daily", at: `${String(AT_H).padStart(2, "0")}:00`, tz: "Asia/Shanghai" },
    session_id: "demo-s1",
    session_title: "数据结构期末复习",
    kb_id: "demo-kb1",
    kb_name: "数据结构讲义",
    instruction:
      "根据我今天的学习产出给一段复盘：先说清我今天掌握了什么，再指出一个最该补的漏洞，最后给明天一条具体到动作的建议。",
    tone: "neutral",
    max_chars: 600,
    enabled: true,
    next_run_at: nextAt(AT_H, AT_M),
  },
  {
    id: "demo-2",
    name: "早八前打个气",
    kind: "encourage",
    schedule: { freq: "weekdays", at: "07:30", tz: "Asia/Shanghai" },
    session_id: null,
    session_title: "默认会话",
    kb_id: null,
    instruction: "用一两句话给今天的课打个气，别喊口号，具体到今天要上的课。",
    tone: "warm",
    max_chars: 200,
    enabled: true,
    next_run_at: nextWeekdayAt(7, 30),
  },
  {
    id: "demo-3",
    name: "每周错题回炉",
    kind: "study",
    schedule: { freq: "weekly", at: "20:00", weekday: 0, tz: "Asia/Shanghai" },
    session_id: null,
    kb_id: "demo-kb2",
    kb_name: "错题本",
    instruction: "把本周错题按知识点归类，挑出重复出错的那一类重点讲。",
    tone: "neutral",
    max_chars: 800,
    enabled: false,
    // 暂停时 next_run_at 必须是 null：页面靠这个判断「它不会再来了」
    next_run_at: null,
  },
];

export const DEMO_RUNS: DelegateRun[] = [
  { run_id: "r1", job_key: "delegate.dispatch", status: "succeeded", planned_at: lastAt(AT_H, AT_M), started_at: lastAt(AT_H, AT_M), finished_at: lastAt(AT_H, AT_M) + 672, tokens_out: 1204, trigger: "schedule" },
  { run_id: "r2", job_key: "delegate.dispatch", status: "succeeded", planned_at: lastAt(AT_H, AT_M, 1), started_at: lastAt(AT_H, AT_M, 1), finished_at: lastAt(AT_H, AT_M, 1) + 588, tokens_out: 1057, trigger: "schedule" },
  { run_id: "r3", job_key: "delegate.dispatch", status: "failed", planned_at: lastAt(AT_H, AT_M, 2), started_at: lastAt(AT_H, AT_M, 2), finished_at: lastAt(AT_H, AT_M, 2) + 30_000, tokens_out: 0, trigger: "schedule", error: "上游超时（第 2 次重试）" },
  { run_id: "r4", job_key: "delegate.dispatch", status: "succeeded", planned_at: lastAt(AT_H, AT_M, 3), started_at: lastAt(AT_H, AT_M, 3), finished_at: lastAt(AT_H, AT_M, 3) + 410, tokens_out: 998, trigger: "manual" },
];

export const DEMO_REPORT: DailyReport = {
  report_id: "demo-report",
  date: ymd(NOW),
  status: "ready",
  model: "deepseek-chat",
  // 1.5 分钟前：会渲染成「刚刚」。用 NOW-2h 的话，跨零点那几小时会显示成
  // 「昨天 22:02 生成」—— 一份「今天」的总结却是昨天生成的，看着就别扭
  generated_at: NOW.getTime() - 90_000,
  read_at: null,
  stats: {
    date: ymd(NOW),
    conversation_count: 4,
    message_count: 37,
    user_msg_count: 18,
    assistant_msg_count: 19,
    tool_call_count: 19,
    active_hours: { "10": 3, "11": 1, "13": 12, "14": 8, "15": 2, "16": 5, "17": 4, "18": 16, "19": 23, "20": 9, "21": 3 },
  },
  content: [
    {
      text: "今天真正被消化的是**二叉树三种遍历**：能自己写出前/中/后序的递归实现，也说清了「中序 = 左根右」为什么能拿到有序序列。",
      source_refs: [231, 244],
    },
    {
      text: "**线索二叉树**的动机还没落地 —— 你知道它「省了栈」，但说不出它到底在哪种场景下比递归更划算。",
      source_refs: [258],
    },
    {
      text: "今天有 **2 次联网搜索**，都用在核对「平衡因子取值范围」，没有把外部资料直接当成结论。",
      source_refs: [302, 310],
    },
  ],
};

/** 打卡页的脱敏知识结构（后端还没有 /api/checkin/*） */
export const DEMO_KNOWLEDGE_TREE = {
  root: "数据结构 · 树与二叉树",
  nodes: [
    { name: "遍历", level: "ok" as const, children: ["前序 / 中序 / 后序的递归实现", "中序为什么能得到有序序列"] },
    { name: "平衡", level: "ok" as const, children: ["平衡因子的取值范围"] },
    { name: "线索化", level: "warn" as const, children: ["应用场景还不清楚"] },
  ],
};

/** 打卡历史（本机还没有记录时才用） */
export const DEMO_CHECKIN_HISTORY = [
  { date: ymd(new Date(NOW.getTime() - DAY)), points: 4, shared: true },
  { date: ymd(new Date(NOW.getTime() - 2 * DAY)), points: 7, shared: true },
];

/** 供页面显示「现在是几点」的稳定锚点（避免各处各取一次 Date.now） */
export const DEMO_NOW = NOW.getTime();
export const DEMO_HM = hhmm(NOW);
