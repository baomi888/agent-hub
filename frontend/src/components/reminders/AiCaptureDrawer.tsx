"use client";

/**
 * 一句话整理（v13 工具型 · Phase C）。
 *
 * 解析走 `POST /api/capture/parse`（只返回 proposal，**不写库**）；
 * 用户勾选、点「加入 N 条」之后，才逐条调 `/api/tasks` 与 `/api/reminders` 真正写进去。
 * 幂等靠的是"写之前先锁按钮"，不是靠后端去重 —— 所以 `committing` 这道闸在 await 之前。
 *
 * `parseFixture` 只在**离线验收**时用：`next dev` 且显式设了
 * `NEXT_PUBLIC_CAPTURE_OFFLINE=1` 才走它。生产构建里 `NODE_ENV !== "production"`
 * 恒为 false，整支被当成死代码消掉 —— 假结果不可能出现在真实用户面前。
 *
 * ============ 为什么是三层，而不是中间一张纸 ============
 *
 * 10 日第一版把输入做成"占满中部的纸"，理由是"用户要做的就是说话，说话该占主位"。
 * 实机看下来是错的：纸上下各空 200px，示例句和信任说明被挤进纸里，
 * 底部没有任何东西承重视线，整屏读起来像一张空表格。
 *
 * v13 的做法是把它变回一个**工具**：
 *
 *   ① 顶部（固定）  标题 / 一句说明 / 关闭
 *   ② 整理区（滚动）「你说的」引用卡 / 正在整理… / 结果
 *   ③ 底部（固定）  示例句 / 麦克风 + 输入框 + 发送
 *
 * 11 日又砍了一轮：步骤条、输入态大标题、等待区虚线、信任说明全部撤掉。
 * 那些东西每一条单看都有道理，摞在一起就像没做完的产品在教用户用 ——
 * 现在只留标题、一句事实、输入框。示例句留下（点一下就知道能说什么），
 * 但不再挂"试试这样说"这种话术。
 *
 * 承重的不是中间那块纸，是**贴底的输入栏** —— 用户永远知道"手放在哪打字"，
 * 中间那块只负责显示"现在有什么 / 马上会出现什么"。
 *
 * ============ 去 AI 味（`去AI味与引导性验收.md` §3）============
 *
 * 抽屉里**不出现**：头像、问候气泡、第一人称旁白（"我听到 / 我在整理 / 我先把听到的排好了"）、
 * 满屏 spark、假进度。整屏"我"最多 1 次，且只在"说错也没关系"这种人文提示里。
 * 状态只用事实叙述：`正在整理…` / `确认这 4 条` / `已加入 4 条`。
 *
 * ============ 引导性（同文件 §4）============
 *
 * 每个状态只回答两件事：刚发生了什么（标题 + 下一句事实）、下一步点什么
 * （一个主按钮 + 一句后果）。不挂步骤条 —— 三步是文档的心智，不是用户要读的东西。
 * 主按钮写后果不写空话：
 * `加入 4 条，设 3 个提醒`，无提醒时 `加入 4 条，不设提醒`，全不选时 disabled。
 *
 * ============ 两类对象 ============
 *
 * 待办是 ○（能勾完），日程是 ◇（只能推迟/暂停/关来源）。`kind` 由 fixture/后端给，
 * 前端不许把两类混成一张卡，也不许把"每天背单词"做成可勾选待办（Phase 1 没有重复待办）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CornIcon from "@/components/CornIcon";
import MascotLogo from "@/components/chat/MascotLogo";
import type {
  CaptureParseResult,
  CaptureProposal,
  ReminderDraft,
  ReminderFreq,
  TaskDraft,
  TaskListKey,
} from "@/lib/types";
import { api } from "@/lib/api";
import type { ApiStatusError } from "@/lib/api";
import { useVoiceInput } from "./useVoiceInput";
import { nextOccurrenceAt, parseFixture } from "./captureFixture";

const LIST_LABEL: Record<TaskListKey, string> = {
  inbox: "收件箱",
  course: "课程",
  homework: "作业",
  club: "社团",
  life: "生活",
  exam: "考试",
};

const WEEK_ZH = ["日", "一", "二", "三", "四", "五", "六"];

/** 录音计时：`0:07`。只在状态行里用，不进渲染期计算时间。 */
function clock(sec: number): string {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

/**
 * Phase A 的演示缝（**Phase B 接后端后整块删除**）。
 *
 * 状态矩阵要求 11 个状态都有截图，但"解析失败 / 部分失败"这两格需要一个真实的
 * 失败来源，而 Phase A 没有网络。用一个前缀明显不是人话的输入来触发，
 * 正常用户打不出 `__demo:fail`，所以它不会误伤真实流程。
 */
const DEMO_PREFIX = "__demo:";

/**
 * 空态演示的那句话。**改它就要同步改下面三条结果**。
 *
 * 演的是产品真做得到的事：原话里说了「下午三点」「晚上七点」，
 * 下面就必须是 15:00 / 19:00。照着说完发现对不上，比什么都不演更伤人。
 * 第三条只说「周日」不带钟点，因为待办的时间轴只写日期锚点 ——
 * 演示也要守住这条，不能给待办编一个它不会显示的 20:00。
 */
const DEMO_SAID = "明天下午三点交高数作业，周五晚上七点社团开会，周日交实验报告";

/**
 * 离线验收开关。**只有 dev 且显式设了环境变量才为 true**。
 *
 * 为什么留这条路：`tools/capture_shell_acceptance.js` 要验 11 个状态、182 条断言，
 * 跑一次真模型要 3 秒 × 十几次还不可复现 —— 状态机验收必须离线。
 * 但真实用户永远不该看到假结果，所以这里用 `NODE_ENV !== "production"` 兜死：
 * `next build` 时这个表达式恒为 false，下面整支 fixture 逻辑会被摇掉。
 */
const OFFLINE_PARSE =
  process.env.NODE_ENV !== "production" &&
  process.env.NEXT_PUBLIC_CAPTURE_OFFLINE === "1";

/**
 * 把接口异常翻成一句用户能懂、且**指向下一步**的话。
 *
 * 三种失败给三种说法，因为三种的下一步不同：
 * - 429 = 现在人多 → "再试一次就好"，别让用户以为话说错了；
 * - 502 = 模型没整理出来 → "换个说法"比"重试"更可能成功；
 * - 超时/断网 = 不知道成没成 → 先说明原话没丢。
 */
function parseFailMessage(e: unknown): string {
  const err = e as ApiStatusError;
  if (err?.status === 429) return err.message || "现在用的人有点多，请稍后再试。";
  if (err?.status === 502) return "没有识别出安排。换个说法再试，原话还在下面。";
  if (/超时|abort/i.test(`${err?.name ?? ""}${err?.message ?? ""}`)) {
    return "等了太久，没有收到结果。原话还在下面。";
  }
  return "网络连接失败，原话还在下面。";
}

/**
 * 演示缝用的假异常 —— **故意不直接给一句话，而是接着走 parseFailMessage**。
 *
 * 教训：上一版离线截图里失败态只有「没整理出来」，因为 demo 分支直接
 * `setStatus("failed")` 没设原因，而真机上的原因文案是新加的。于是"截图全绿"
 * 和"真实长什么样"对不上 —— 截图反过来给自己作了保。
 * 现在让假异常过同一个翻译函数，截图与真机就不可能分叉。
 */
function demoError(cmd: string): unknown {
  if (cmd === "fail429") {
    return { status: 429, message: "现在用的人有点多，请稍后再试。" };
  }
  if (cmd === "failnet") return { name: "TypeError", message: "Failed to fetch" };
  return { status: 502 };
}

const DEMO_FAIL_CMDS = ["fail", "fail429", "failnet"];

// ==================== proposal → 写库入参 ====================

/** 写库的结果：哪些进去了、哪些没进去（id → 给用户看的原因）。 */
export type CommitOutcome = {
  okIds: string[];
  issues: Record<string, string>;
};

/**
 * proposal → 待办草稿。**只搬用户说过的字段**。
 *
 * 关键一条：`remind_at` 为 null 就不要把它写进请求体。传 `remind_at: null` 与
 * "不传"在后端是两件事 —— 前者会走"设置提醒时间"的分支。用户只说「明天交作业」，
 * 我们不能顺手替他造一条联动提醒（§8.2 负向：只设 due_at 不创建 linked reminder）。
 */
export function taskDraftOf(item: CaptureProposal): TaskDraft {
  const draft: TaskDraft = {
    title: item.title.slice(0, 60),
    list_key: (item.list_key ?? "inbox") as TaskListKey,
    priority: item.priority,
  };
  if (item.note) draft.note = item.note;
  if (item.due_at !== null) draft.due_at = item.due_at;
  if (item.remind_at !== null) draft.remind_at = item.remind_at;
  return draft;
}

/**
 * proposal → 提醒草稿。**返回 null = 这条算不出「什么时候」，不能写。**
 *
 * 为什么宁可拦下也不给个默认时间：提醒的全部价值就是那个时刻。
 * 编一个 20:00 出来，用户第二天会被一条自己没定过的提醒叫醒，
 * 而他昨天说的话里根本没有 20:00 —— 这种"贴心的默认值"是灾难。
 */
export function reminderDraftOf(item: CaptureProposal): ReminderDraft | null {
  const rec = item.recurrence;
  const freq: ReminderFreq = rec?.freq ?? "once";
  const draft: ReminderDraft = {
    title: item.title.slice(0, 60),
    freq,
    // **绝不写 `source:"task"`**：联动提醒只有 `/api/tasks` 一个入口，
    // 从这里塞一条进去，用户在提醒中心改它会被 400 挡住（§3.4）。
    source: "manual",
  };
  if (item.note) draft.note = item.note;

  if (freq === "once") {
    // 一次性：三种来源按可信度排 —— 规则里的绝对时刻 > 截止时间 > 单独设的提醒时间
    const at = rec?.fire_at ?? item.due_at ?? item.remind_at;
    if (at === null || at === undefined) return null;
    draft.fire_at = at;
    if (rec?.at) draft.at = rec.at;
    return draft;
  }

  // 每天 / 工作日 / 每周 / 按间隔：靠 `at` 这条规则响，没有 at 就是哑弹
  if (!rec?.at) return null;
  draft.at = rec.at;
  if (freq === "weekly") {
    if (rec.weekday === null || rec.weekday === undefined) return null;
    draft.weekday = rec.weekday;
  }
  if (freq === "interval") {
    if (!rec.every_minutes) return null;
    draft.every_minutes = rec.every_minutes;
  }
  return draft;
}

/**
 * 补完时间后的收尾。
 *
 * 有些条目的 `needs_confirm` 是"没说哪天"这一类，**没有具体问句**
 * （`questions` 为空）。可 `needsConfirm()` 的判据是「有没有答过问句」，
 * 没有问句时 `[].some(...)` 恒假 → 这条永远显示「需要确认」，而界面上
 * 一个可点的地方都没有 —— 一个点不掉的标记。补了时间就等于把那个疑问答了，
 * 所以这里把它放掉。
 *
 * 有限度地说清楚：**只在没有问句时放**。有问句的（"这周六还是每周六"）
 * 是另一件事，补时间并不能替用户回答它，硬清掉就是替他做主。
 */
function settledByTime(item: CaptureProposal, next: CaptureProposal): CaptureProposal {
  return item.questions.length === 0 ? { ...next, needs_confirm: false } : next;
}

/**
 * 把补时间面板滚进可视区。
 *
 * 小屏上这个面板常落在底栏后面：`textContent` 读得到、断言是绿的，用户却看不到 ——
 * 那等于没提示。所以每次打开、每次被拦下都滚一次。
 */
function revealFixPanel(id: string) {
  window.requestAnimationFrame(() => {
    document.getElementById(`rem-cap-fix-${id}`)?.scrollIntoView({ block: "nearest" });
  });
}

/** 写不写得进去。写不了的条目**一个请求都不该发**，否则拿回来的是一条 400 噪音。 */
export function isWritable(item: CaptureProposal): boolean {
  if (!item.title.trim()) return false;
  return item.kind === "task" ? true : reminderDraftOf(item) !== null;
}

/**
 * 这条加进去之后会不会**真的响**。
 *
 * - 日程：能写出 `fire_at` 就会响（`reminderDraftOf` 已经替我们把三种时间来源都试过）。
 * - 待办：`due_at` 是**截止时间**，不是提醒时刻 —— 只有单独设了 `remind_at` 才会响。
 *
 * 为什么要单独一个函数：这个判据以前在底栏计数里手写了一版
 * （`remind_at !== null || recurrence !== null`），漏掉了「一次性日程 + due_at」这种形状，
 * 于是界面上写着「1 条会提醒」，实际会响的是 2 条。
 * 数错了不是数字问题 —— 用户靠这个数决定要不要多点一下去设提醒。
 */
export function willRing(item: CaptureProposal): boolean {
  return item.kind === "reminder" ? reminderDraftOf(item) !== null : item.remind_at !== null;
}

/**
 * 写库失败翻成一句人话。跟解析失败一样，**不同状态码的下一步不同**：
 * - 400 = 这条数据后端不收（多半是缺字段）→ 别重试，改一下；
 * - 401 = 登录过期 → 全局 `baomi:unauthorized` 已经在处理，这里只说事实；
 * - 其余 = 网络/服务端 → 可以重试。
 */
export function commitFailMessage(e: unknown): string {
  const err = e as ApiStatusError;
  if (err?.status === 400) return err.message || "这条信息还不能保存，请改一下再试。";
  if (err?.status === 401) return "登录已过期，请重新登录后再加入。";
  return "没加进去。可以检查网络后再试。";
}

// ==================== 小图标 ====================
// 项目不用图标库，这几个形状 CornIcon 里没有，直接内联。

export function Spark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 3.5l1.7 5.1 5.1 1.7-5.1 1.7L12 17.1l-1.7-5.1L5.2 10.3l5.1-1.7L12 3.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M18.2 16.4l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z"
        fill="currentColor"
        opacity="0.58"
      />
    </svg>
  );
}

function MicIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <rect x="9" y="3.5" width="6" height="11" rx="3" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M6.5 11.5a5.5 5.5 0 0 0 11 0M12 17v3.5M9 20.5h6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ArrowUp({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 19V5M6.5 10.5L12 5l5.5 5.5"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ==================== 时间显示 ====================

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function hmOf(sec: number): string {
  const d = new Date(sec * 1000);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

interface AxisSlot {
  /** 轴上的日期锚点：今天 / 明天 / 周五 / 待定 */
  day: string;
  /** 具体钟点。待办没有"几点开始"这个概念，一律 null */
  time: string | null;
  /** 排序键（毫秒）。没有时间的是 Infinity，排到最后 */
  at: number;
}

/**
 * 一条 proposal 在时间轴上的落点。
 *
 * 排序键来自 `nextOccurrenceAt`，也就是**下一次真正发生**的时刻 ——
 * 每周三的瑜伽按"下一个周三"插进轴里，而不是被塞进一个"重复"分组。
 * 用户说话越乱，这条轴的顺序越是他唯一能读懂的"已经理清了"。
 *
 * 待办的 `due_at` 是当天 23:59:59（后端语义），它不是"几点开始"，
 * 所以时间列只写日期锚点，钟点留给真正的日程 —— 给"今晚交房租"编一个
 * 23:59 摆在轴上，等于替用户编了一个他没说过的时间。
 */
function axisSlotOf(item: CaptureProposal, nowMs: number): AxisSlot {
  const at = nextOccurrenceAt(item, nowMs);
  if (!Number.isFinite(at)) return { day: "待定", time: null, at };

  // 重复项也按**它下一次真正发生的那一天**落位，不单开"每周三"锚点，
  // 否则轴会被切成「明天 → 每周三 → 明天」三段，读起来像错乱。
  const d = new Date(at);
  const today = new Date(nowMs);
  today.setHours(0, 0, 0, 0);
  const that = new Date(at);
  that.setHours(0, 0, 0, 0);
  const diff = Math.round((that.getTime() - today.getTime()) / 86_400_000);

  const day =
    diff === 0
      ? "今天"
      : diff === 1
        ? "明天"
        : diff === 2
          ? "后天"
          : diff > 2 && diff < 7
            ? `周${WEEK_ZH[d.getDay()]}`
            : `${d.getMonth() + 1}月${d.getDate()}日`;

  const time = item.kind === "task" ? null : hmOf(Math.floor(at / 1000));
  return { day, time, at };
}

/**
 * 这条只需要补一个「几点」，还是连日期一起补。
 *
 * 重复规则（每天/每周/按间隔）本来就只有钟点这一个自由度，给它一个日期输入框
 * 等于问一个不会被用到的值 —— 用户填了以后发现日期没生效，只会觉得坏了。
 */
export function needsClockOnly(item: CaptureProposal): boolean {
  return item.recurrence !== null && item.recurrence.freq !== "once";
}

/**
 * 本地「日期 + 时刻」→ Unix 秒。空串或不完整一律 null。
 *
 * 用 `new Date("YYYY-MM-DDTHH:MM")` 而不是自己拆数字：这种不带时区的串
 * 按**本地时区**解释，正好是用户填的那一刻。手写 `Date.UTC` 会让成都用户
 * 填的「早八点」变成下午四点响 —— 而且是在他睡着的时候。
 */
export function localToTs(date: string, time: string): number | null {
  if (!date || !time) return null;
  const ms = new Date(`${date}T${time}:00`).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** Unix 秒 → 本地 `YYYY-MM-DD`（date 输入框的 value 格式）。 */
export function tsToLocalDate(ts: number): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
}

// ==================== 抽屉 ====================

type Status = "idle" | "parsing" | "ready" | "failed";

export default function AiCaptureDrawer({
  now,
  onClose,
  onCommit,
}: {
  /** 页面里每分钟走一次的「现在」。**必填**：渲染期不许 fallback 到 Date.now() */
  now: number;
  onClose: () => void;
  /**
   * 真写库。页面负责逐条调 API、刷新列表、出 toast；抽屉只认「哪几条没进去 + 为什么」。
   *
   * 为什么写库不放在抽屉里：抽屉一关就卸载，写在里面的 promise 没人接结果；
   * 而 `reload()` / `showToast` 本来就在页面手上，绕一圈只会多一层状态同步。
   */
  onCommit: (items: CaptureProposal[]) => Promise<CommitOutcome>;
}) {
  const [status, setStatus] = useState<Status>("idle");
  /** 失败的原因（来自接口状态码）。空串 = 还没失败过，或失败原因不明。 */
  const [failMsg, setFailMsg] = useState("");
  const [text, setText] = useState("");
  /** 提交出去的原话。整理中的引用卡、结果态的原话回看、改文字都用它 */
  const [heard, setHeard] = useState("");
  const [items, setItems] = useState<CaptureProposal[]>([]);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  /** 追问的回答：qid → 选项 value。答过之后不再算「需确认」 */
  const [answers, setAnswers] = useState<Record<string, string>>({});
  /** 点编号要定位的那一条 */
  const [focusId, setFocusId] = useState<string | null>(null);
  /** 结果态的原话卡：v13 要求可回看，默认展开，点标题收起 */
  const [showSource, setShowSource] = useState(true);
  const [committing, setCommitting] = useState(false);
  /** 没写进去的条目：id → 一句人话（缺时间 / 后端不收 / 网络）。成功的条目会被移出列表 */
  const [issues, setIssues] = useState<Record<string, string>>({});
  /** 上次真正写进去几条。底栏说"已加入 X 条"靠它，按下标减会算错 */
  const [lastOk, setLastOk] = useState(0);
  /** 当前这次是不是演示缝触发的（`fail` / `partial`），Phase C 只在离线验收里用 */
  const [demoMode, setDemoMode] = useState<string | null>(null);
  /** 「补个时间」：正在哪一条上开输入框，以及用户填了一半的本地日期/时刻 */
  const [fixing, setFixing] = useState<{ id: string; date: string; time: string } | null>(null);
  /** 补时间时的拦下理由（没填全 / 时间已经过去）。空串 = 还没出错 */
  const [fixErr, setFixErr] = useState("");
  /** 语音输入接没接（后端 ASR 有没有配密钥）。没接就**不渲染**麦克风 */
  const [voiceOn, setVoiceOn] = useState(false);
  /** 语音失败的一句话。空串 = 没出错 */
  const [voiceErr, setVoiceErr] = useState("");

  const rootRef = useRef<HTMLDivElement | null>(null);
  /** 写库的同步锁。state 要等下一次渲染才生效，连续两次点击挡不住 —— 只有 ref 是立即的 */
  const committingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const sourceRef = useRef<HTMLElement | null>(null);
  const composing = useRef(false);

  // 语音接没接，由后端说了算：没配密钥就不渲染麦克风。
  // 拉失败也按"没接"处理 —— 宁可少一个按钮，也不给一个点了只弹"还没做"的按钮。
  useEffect(() => {
    let alive = true;
    api.capture
      .capabilities()
      .then((c) => {
        if (alive) setVoiceOn(Boolean(c.voice));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const voice = useVoiceInput({
    onText: (text) => {
      // 追加而不是覆盖：用户可能先打了一半，再改用说的
      setText((prev) => (prev.trim() ? `${prev.trim()} ${text}` : text));
      setVoiceErr("");
      inputRef.current?.focus();
    },
    onError: setVoiceErr,
  });

  /**
   * 输入框跟着内容长高。
   *
   * `rows={1}` 只负责"一开始只有一行高"，它**不会**自己长。而示例卡点一下
   * 就把整句「明天下午三点交高数作业，周五晚上七点社团开会，周日交实验报告」
   * 填了进来 —— 框里只露第一行，后面的字用户看不见，还以为没填进去。
   *
   * 上限 96px（约四行）：再多就框内滚。底栏不能无限长，dock 一高，中间那块
   * 就没地方了 —— 这正是当初把示例句从底栏撤走的原因之一。
   */
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
  }, [text]);

  // Esc 关 / Tab 不出抽屉 / 关掉之后焦点还给打开它的按钮。
  // 与 page.tsx 里的 `useDialog` 是同一套逻辑，复制一份而不是抽公共件：
  // 那三个抽屉已经稳定，为了复用去动它们不划算。**后续可合并**。
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();

    const focusables = () =>
      Array.from(
        rootRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),textarea:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])'
        ) ?? []
      ).filter((el) => el.getBoundingClientRect().height > 0);

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
      previous?.focus?.();
    };
  }, []);

  /** 结果落地：抽出来是因为离线/真实两条路都要走同一套复位（少一处就会留脏状态）。 */
  const applyResult = useCallback((result: CaptureParseResult) => {
    setItems(result.items);
    setPicked(Object.fromEntries(result.items.map((i) => [i.id, true])));
    setAnswers({});
    setFocusId(null);
    setShowSource(true);
    setStatus("ready");
    // 结果出现后把整理区滚回顶部：第一眼要看到汇总条，而不是停在半路
    window.setTimeout(() => bodyRef.current?.scrollTo({ top: 0 }), 0);
  }, []);

  const runParse = useCallback(
    async (value: string) => {
      const raw = value.trim();
      if (!raw || status === "parsing") return;
      setStatus("parsing");
      setIssues({});
      setLastOk(0);
      setFailMsg("");

      // 演示缝只在离线验收时生效（见 OFFLINE_PARSE 的注释）。
      // 前缀必须在这里剥掉，否则它会跟着进标题、进原话卡。
      const demo = OFFLINE_PARSE && raw.startsWith(DEMO_PREFIX) ? raw.slice(DEMO_PREFIX.length) : null;
      const demoCmd = demo ? (demo.split(/\s+/)[0] ?? "") : "";
      const clean = demo ? demo.replace(/^\S+\s*/, "").trim() : raw;
      setDemoMode(demoCmd || null);
      setHeard(clean || raw);
      // 演示前缀不许漏进输入框给用户看见
      if (demo) setText(clean);

      if (OFFLINE_PARSE) {
        if (DEMO_FAIL_CMDS.includes(demoCmd)) {
          // 原因文案走真机同一个函数，截图才不会替自己背书
          setFailMsg(parseFailMessage(demoError(demoCmd)));
          setStatus("failed");
          return;
        }
        // fixture 只等两帧（~32ms），要拍"整理中"这一帧得人为拉长
        if (demoCmd === "slow") {
          await new Promise((r) => window.setTimeout(r, 1200));
        }
        try {
          applyResult(await parseFixture(clean || raw, now));
        } catch {
          setStatus("failed");
        }
        return;
      }

      try {
        applyResult(await api.capture.parse(clean || raw, now));
      } catch (e) {
        // 失败也要留出口：原文填回输入框、按钮能再点一次，并说清是哪一类失败
        setFailMsg(parseFailMessage(e));
        setStatus("failed");
      }
    },
    [applyResult, now, status]
  );

  const restart = () => {
    setStatus("idle");
    setFailMsg("");
    setText("");
    setHeard("");
    setItems([]);
    setPicked({});
    setAnswers({});
    setFocusId(null);
    setShowSource(true);
    setIssues({});
    setLastOk(0);
    setDemoMode(null);
    setCommitting(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const editText = () => {
    setStatus("idle");
    setText(heard);
    setIssues({});
    setLastOk(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  // ---------- 按时间排好 ----------
  const ordered = useMemo(() => {
    const rows = items
      .map((item) => ({ item, slot: axisSlotOf(item, now) }))
      .sort((a, b) => a.slot.at - b.slot.at);
    return rows.map((row, i) => ({
      ...row,
      showDay: i === 0 || rows[i - 1].slot.day !== row.slot.day,
    }));
  }, [items, now]);

  /** 哪一行的「不提醒」被点开了。同时只开一行 —— 满屏展开等于没有提示。 */
  const [openNrmd, setOpenNrmd] = useState<string | null>(null);

  // ---------- 原话分段（编号回看） ----------
  const segments = useMemo(() => {
    if (!heard) return [];
    const spans = items
      .map((item, i) => ({ id: item.id, no: i + 1, start: item.source_start, end: item.source_end }))
      .filter(
        (s): s is { id: string; no: number; start: number; end: number } =>
          s.start !== null && s.end !== null
      )
      .sort((a, b) => a.start - b.start);

    const out: { text: string; no?: number; id?: string }[] = [];
    let cursor = 0;
    for (const span of spans) {
      if (span.start < cursor) continue; // 重叠的跳过，宁可少一个编号也不重复贴字
      if (span.start > cursor) out.push({ text: heard.slice(cursor, span.start) });
      out.push({ text: heard.slice(span.start, span.end), no: span.no, id: span.id });
      cursor = span.end;
    }
    if (cursor < heard.length) out.push({ text: heard.slice(cursor) });
    return out;
  }, [heard, items]);

  const pickedItems = items.filter((i) => picked[i.id]);
  // 只有**真会响的**才算"会提醒"。
  //
  // 上一版判据是 `remind_at !== null || recurrence !== null`，两次踩坑：
  // ① 一条"有 recurrence 但没钟点"的条目字段齐、发不出请求，底栏还写「1 条会提醒」；
  // ② 后端对一次性日程返回 `recurrence: null` + `due_at`，这种明明会响却一个都不算，
  //    3 条里 2 条是日程，底栏只写「1 条会提醒」。
  // 现在统一交给 willRing —— 它和写库用的是同一个函数，写不进去就一定不响。
  const remindCount = pickedItems.filter((i) => isWritable(i) && willRing(i)).length;
  const confirmCount = items.filter((i) => needsConfirm(i, answers)).length;
  /** 选中且**真能写进去**的条数。主按钮上的数字用它 —— 说「加入 3 条」就得真加 3 条 */
  const writableCount = pickedItems.filter(isWritable).length;
  const allPicked = items.length > 0 && pickedItems.length === items.length;
  const taskCount = items.filter((i) => i.kind === "task").length;
  const remCount = items.length - taskCount;

  const toggle = (id: string) => setPicked((prev) => ({ ...prev, [id]: !prev[id] }));

  /**
   * 点示例卡 = 把这句话填进输入框。**只填入，不提交。**
   *
   * 与旧的示例 chips 同一条规则（视觉基线 §7）：点一下就知道能说什么，
   * 但发不发送由用户自己按回车 —— 点了就发等于替他做了决定。
   */
  const fillDemo = () => {
    setText(DEMO_SAID);
    inputRef.current?.focus();
  };

  // ---------- 补时间 ----------

  /**
   * 打开某一行的补时间面板。
   *
   * 日期默认填**今天**（不是编一个时间，只是省掉「从日历里翻到今天」这一下），
   * 时刻留空 —— 提醒的全部价值就是那个时刻，替用户填一个就是替他定了闹钟。
   */
  const openFix = (item: CaptureProposal) => {
    setFixErr("");
    setFixing({
      id: item.id,
      date: needsClockOnly(item) ? "" : tsToLocalDate(now),
      time: "",
    });
    revealFixPanel(item.id);
  };

  /**
   * 把填好的时间写回这一条。
   *
   * 三种拦下：没填全、时间已经过去、填的不是个时间。拦下时**只在这一行里说**，
   * 不弹 toast —— 用户的眼睛正看着输入框，弹层在别处出现等于没提示。
   */
  const applyFix = () => {
    if (!fixing) return;
    const item = items.find((i) => i.id === fixing.id);
    if (!item) return;

    if (needsClockOnly(item)) {
      if (!/^\d{2}:\d{2}$/.test(fixing.time)) {
        setFixErr("先选一个时间。");
        revealFixPanel(item.id);
        return;
      }
      setItems((prev) =>
        prev.map((i) =>
          i.id === item.id && i.recurrence
            ? settledByTime(i, { ...i, recurrence: { ...i.recurrence, at: fixing.time } })
            : i
        )
      );
    } else {
      const ts = localToTs(fixing.date, fixing.time);
      if (ts === null) {
        setFixErr("日期和时刻都要选。");
        revealFixPanel(item.id);
        return;
      }
      // 过去的时间后端会 400，但在那之前先在这里拦一次：用户改的是"什么时候响"，
      // 让他看着输入框就知道错了，比等一个来回再看到红字强
      if (ts <= Math.floor(now / 1000)) {
        setFixErr("这个时间已经过去了，选个晚一点的。");
        revealFixPanel(item.id);
        return;
      }
      setItems((prev) =>
        prev.map((i) =>
          i.id === item.id
            ? settledByTime(i, {
                ...i,
                remind_at: ts,
                recurrence: i.recurrence ? { ...i.recurrence, fire_at: ts } : null,
              })
            : i
        )
      );
    }

    // 用户特意补了时间 = 他要把这条加进去，顺手勾上（省掉"补完还得回去点勾"）
    setPicked((prev) => ({ ...prev, [item.id]: true }));
    // 上一次加入失败留下的红字也要撤掉：这条已经被改好了，
    // 再挂着「没加上」会让他以为白改了一遍。
    setIssues((prev) => {
      if (!(item.id in prev)) return prev;
      const next = { ...prev };
      delete next[item.id];
      return next;
    });
    setFixErr("");
    setFixing(null);
  };

  const commit = async () => {
    // 防双击：**必须用 ref，不能用 state**。
    // 用 `if (committing) return` 挡不住连续两次点击：两次都发生在同一个渲染里，
    // 拿到的 `committing` 都还是 false，于是两条一模一样的话各写一遍。
    // 真机 e2e 抓到过（B1 连点两次 → 后端多出 2 条），这条是那次的修复。
    if (committingRef.current || pickedItems.length === 0) return;
    committingRef.current = true;
    setCommitting(true);
    setIssues({});
    setLastOk(0);

    // 算不出「什么时候」的提醒先挑出来：一个请求都不发。
    // 它们既不是"后端不收"，也不是"网络问题"，混在一起说会让用户重试到怀疑自己。
    const blocked: Record<string, string> = {};
    const sendable: CaptureProposal[] = [];
    for (const item of pickedItems) {
      if (isWritable(item)) sendable.push(item);
      // 原因里必须写出**下一步**：现在这条行上就有「补个时间」，
      // 只说"没写几点"等于把用户晾在原地
      else blocked[item.id] = "这条是提醒，但还没说几点。点下方「补上时间」，设好后才会提醒你。";
    }

    // 演示缝：离线验收要拍"部分失败"这一格，真机撞不出来（`__demo:partial`，
    // 生产构建里 OFFLINE_PARSE 恒 false，整支被摇掉）
    const forced =
      OFFLINE_PARSE && demoMode === "partial" ? (pickedItems[1]?.id ?? null) : null;
    const toWrite = forced ? sendable.filter((i) => i.id !== forced) : sendable;

    let okIds: string[] = [];
    let failed: Record<string, string> = { ...blocked };
    if (forced) failed[forced] = "没加进去。检查网络后再试一次。";

    if (toWrite.length > 0) {
      try {
        const out = await onCommit(toWrite);
        okIds = out.okIds;
        failed = { ...failed, ...out.issues };
      } catch (e) {
        // 页面那边整批炸了（比如没登录）：一条都别声称成功
        for (const item of toWrite) failed[item.id] = commitFailMessage(e);
      }
    }

    setLastOk(okIds.length);
    if (Object.keys(failed).length === 0) {
      // 全进去了：抽屉的活干完了，关掉。列表与 toast 由页面负责
      setCommitting(false);
      onClose();
      return;
    }

    // 部分失败：成功的从轴上摘掉（它们已经在真实列表里了，留着会让人以为重复），
    // 失败的留在原地并说清原因，抽屉不关 —— 用户按下标重试或手动删。
    setItems((prev) => prev.filter((i) => !okIds.includes(i.id)));
    setPicked((prev) => {
      const next: Record<string, boolean> = {};
      for (const [id, on] of Object.entries(prev)) {
        if (!okIds.includes(id)) next[id] = on;
      }
      return next;
    });
    setIssues(failed);
    setCommitting(false);
    // 失败后要放开锁：失败项还在轴上，用户改完可以再点一次
    committingRef.current = false;
  };

  const review = () => {
    const first = bodyRef.current?.querySelector<HTMLElement>("[data-first-confirm='1']");
    first?.scrollIntoView({ block: "center", behavior: "smooth" });
    // preventScroll：默认 focus 会把元素再挪一次，把刚居中好的追问卡推到 dock 下面
    first?.querySelector<HTMLElement>(".rem-cap-nodewrap")?.focus({ preventScroll: true });
  };

  // 点编号 → 展开原话并滚到引用卡。原话在轴上方，不滚上去用户会以为点了没反应。
  useEffect(() => {
    if (!focusId) return;
    if (!showSource) setShowSource(true);
    sourceRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusId, showSource]);

  const isResult = status === "ready" || status === "failed" || status === "parsing";
  const issueCount = Object.keys(issues).length;
  const activeStep = committing || issueCount > 0 ? 3 : status === "ready" ? 2 : 1;

  // ---------- 顶部那三行：我在哪 / 刚发生什么 / 下一步 ----------
  const title = (() => {
    if (status === "ready") return items.length === 0 ? "没整理出待办或提醒" : `确认这 ${items.length} 条`;
    if (status === "failed") return "这次没整理出来";
    return "整理成待办和提醒";
  })();

  /**
   * 标题下面那句。**可为空** —— 空串表示这一态不需要第二行。
   *
   * 11 日第二次改：原本 idle 和 parsing 都写着「自动拆成待办和提醒；确认后才会加入」。
   * 可这句话在 idle 时正被下面那张演示卡演着（一句话 → 三条结果），
   * 在 parsing 时又被 body 里的「正在整理…」说了一遍 —— 同一句话讲三遍，
   * 用户读完什么也没多知道。只在**有结果或有故障**时给第二行。
   */
  const sub = (() => {
    if (status === "ready") {
      if (items.length === 0) return "换个说法，或者先记成一条待办。";
      const base = `${taskCount} 条待办、${remCount} 条提醒`;
      return confirmCount > 0
        ? `${base}；${confirmCount} 条还要补信息。`
        : `${base}；确认后才会加入。`;
    }
    if (status === "failed") return "你刚才说的话还在，可以直接重试。";
    return "";
  })();

  return (
    <>
      <div className="rem-cap-scrim" onClick={onClose} aria-hidden="true" />
      <div
        className="rem-cap-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rem-cap-title"
        ref={rootRef}
        data-phase={isResult ? "result" : "input"}
        data-status={status}
        data-step={activeStep}
      >
        {/* ① 顶部：固定不滚 */}
        <header className="rem-cap-top">
          <div className="rem-cap-head">
            <h2 id="rem-cap-title" className="rem-cap-title">
              {title}
            </h2>
            {sub ? <p className="rem-cap-sub">{sub}</p> : null}
          </div>

          <button type="button" className="rem-cap-close" aria-label="关闭" onClick={onClose}>
            <CornIcon name="close" className="h-5 w-5" />
          </button>
        </header>

        {/* ② 整理区：独立滚动，底部给 dock 留位 */}
        <div className="rem-cap-body" ref={bodyRef}>
          {/* ---- 空态：把「一句话会变成什么」当场演一遍 ----
              空着像没做完，堆说明像在教人，演示是第三种：不解释功能、不列步骤，
              直接给一轮真实的输入和它的结果。
              一开始打字或录音就收起，把位置让出来。

              ============ 2026-11-11 第二轮：为什么会“空” ============
              上一版把「品牌行 + 演示卡」整块 `margin: auto 0` 居中。实机全屏看，
              抽屉是整屏高的：body 高度从 890（1080 屏）到 1250（1600 屏）不等，
              而这块内容只有 268px —— 上下各留 300~490px 纯白。
              居中不但没解决问题，还让内容跟标题、输入栏**两头都断开**，
              视线从标题扫下来直接掉进空洞里。这就是“加了内容却还是空”。

              这一版按“两头收紧、中间呼吸”重排：
                顶部组  图标 + 名字 + 一句主张  ← 贴标题，视线接得上
                弹性空白                         ← 唯一的留白，且在两侧都被夹住
                底部组  可点的预演卡 + 一句说明  ← 贴输入栏，跟“要打字”这件事同构
              弹性空白靠 `.rem-cap-demo-wrap { margin-top: auto }` 吸收，不靠固定值 ——
              视口高一变，固定值就会在某档下留下巨大空洞，auto 不会。 */}
          {status === "idle" && text.trim() === "" && voice.phase === "idle" ? (
            <div className="rem-cap-empty">
              <div className="rem-cap-hello">
                {/* 图标不另给可访问名：下面还有主张句，读屏读两遍是噪音。
                    举着一张待办清单的知穗 —— 这是这个产品才有的动作，
                    和右侧「一句话被拆成几条」讲的是同一件事。 */}
                <span className="rem-cap-brand-mark" aria-hidden="true">
                  <MascotLogo holding="list" />
                </span>
                <p className="rem-cap-lead">说清「什么时候、要做什么」</p>
              </div>

              <div className="rem-cap-demo-wrap">
                {/* 整张卡可点 = 把这句话填进输入框。原来这里是「静态卡 + 底栏四句
                    示例 chips」，两处都在举例子；chips 一行挤四个、字被压扁，
                    还把输入栏顶高了一截。合并成一张可点的卡：既演结果，又能一键试。
                    **只填入、不提交** —— 点了就发出去等于替用户做了决定。 */}
                <div
                  className="rem-cap-demo"
                  role="button"
                  tabIndex={0}
                  aria-label={`用这句试试：${DEMO_SAID}`}
                  onClick={fillDemo}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      fillDemo();
                    }
                  }}
                >
                  <p className="rem-cap-demo-said">
                    <span className="rem-cap-demo-tag">原话</span>
                    {DEMO_SAID}
                  </p>
                  <p className="rem-cap-demo-sep" aria-hidden="true">
                    会整理成
                  </p>
                  <ul className="rem-cap-demo-list">
                    <li className="rem-cap-demo-row">
                      <span className="rem-cap-demo-node" data-kind="task" aria-hidden="true" />
                      <span className="rem-cap-demo-what">交高数作业</span>
                      <span className="rem-cap-demo-when">明天 15:00</span>
                    </li>
                    <li className="rem-cap-demo-row">
                      <span
                        className="rem-cap-demo-node"
                        data-kind="reminder"
                        aria-hidden="true"
                      />
                      <span className="rem-cap-demo-what">社团开会</span>
                      <span className="rem-cap-demo-when">周五 19:00</span>
                    </li>
                    <li className="rem-cap-demo-row">
                      <span className="rem-cap-demo-node" data-kind="task" aria-hidden="true" />
                      <span className="rem-cap-demo-what">交实验报告</span>
                      <span className="rem-cap-demo-when">周日</span>
                    </li>
                  </ul>
                </div>
                {/* 说明搬到卡外：在卡里它跟「整理成」挤在一起，读起来像第四行结果。
                    上一版是「点一下可以试试 · 不会存进你的列表」—— 中间那个间隔号
                    是产品文档的写法。人说话会用逗号，而且会直接说**会发生什么**：
                    「填进输入框」，不是「可以试试」。 */}
                <p className="rem-cap-demo-foot">点一下填入输入框，不会保存</p>
              </div>
            </div>
          ) : null}

          {/* ---- 整理中：先出现"你说的"，再出现"正在整理…" ---- */}
          {status === "parsing" ? (
            <>
              <SourceQuote text={heard} />
              <p className="rem-cap-proc" aria-live="polite">
                <span className="rem-cap-dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                正在整理…
              </p>
            </>
          ) : null}

          {/* ---- 失败：原文保留 + 两个出口（§4.6） ---- */}
          {status === "failed" ? (
            <>
              <div className="rem-cap-fail" role="alert">
                {/* 先说是哪一类失败，再说怎么办。只说"没整理出来"，
                    用户不知道该换个说法还是该等一下（429 和 502 的下一步不一样） */}
                {failMsg ? <p className="rem-cap-fail-why">{failMsg}</p> : null}
                <p className="rem-cap-fail-note">
                  原话还在输入框里。改完按回车再试；也可以先关掉这里，回列表直接记一条。
                </p>
                <button
                  type="button"
                  className="rem-cap-ghost rem-cap-fail-retry"
                  onClick={() => void runParse(heard)}
                >
                  原样再试一次
                </button>
              </div>
              <SourceQuote text={heard} />
            </>
          ) : null}

          {/* ---- 结果 ---- */}
          {status === "ready" ? (
            items.length === 0 ? (
              <div className="rem-cap-zero">
                <p className="rem-cap-zero-note">
                  这句话里没有说清「什么时候、要做什么」。换个说法，或直接在下面写一件事；
                  时间可以之后再补。
                </p>
                <button type="button" className="rem-cap-ghost rem-cap-zero-retry" onClick={restart}>
                  清空重说
                </button>
                <SourceQuote text={heard} />
              </div>
            ) : (
              <>
                {/* 汇总条：一眼知道"整理出了什么" */}
                <div className="rem-cap-summarybar">
                  <span>
                    {items.length} 条 · {taskCount} 条待办 · {remCount} 条提醒
                  </span>
                  {confirmCount > 0 ? (
                    <span className="rem-cap-summarybar-warn">{confirmCount} 条要确认</span>
                  ) : null}
                </div>

                {/* 注意力路由：有需确认就把人带过去，而不是只报个数（§4.4） */}
                {confirmCount > 0 ? (
                  <button type="button" className="rem-cap-route" onClick={review}>
                    先看 {confirmCount} 条要确认
                    <span aria-hidden="true">↓</span>
                  </button>
                ) : null}

                {/* 你说的：v13 放在结果区顶部，可回看、可收起 */}
                <section className="rem-cap-src" ref={sourceRef} aria-label="你说的原话">
                  <div className="rem-cap-src-hd">
                    <button
                      type="button"
                      className="rem-cap-src-toggle"
                      aria-expanded={showSource}
                      onClick={() => setShowSource((v) => !v)}
                    >
                      <span
                        className="rem-cap-caret"
                        data-open={showSource ? "1" : undefined}
                        aria-hidden="true"
                      />
                      你说的
                    </button>
                  </div>

                  {showSource ? (
                    <>
                      <p className="rem-cap-quote">
                        {segments.length === 0
                          ? heard
                          : segments.map((seg, i) =>
                              seg.no ? (
                                <mark
                                  key={`${seg.id}-${i}`}
                                  className="rem-cap-srcmark"
                                  data-on={focusId === seg.id ? "1" : undefined}
                                  onClick={() => seg.id && setFocusId(seg.id)}
                                >
                                  {seg.text}
                                  <span className="rem-cap-srcno">{seg.no}</span>
                                </mark>
                              ) : (
                                <span key={`t-${i}`}>{seg.text}</span>
                              )
                            )}
                      </p>
                      <div className="rem-cap-src-acts">
                        <button type="button" className="rem-cap-ghost" onClick={restart}>
                          重新说
                        </button>
                        <button type="button" className="rem-cap-ghost" onClick={editText}>
                          修改原话
                        </button>
                      </div>
                    </>
                  ) : null}
                </section>

                {/* 逐条检查 */}
                <div className="rem-cap-axishd">
                  <h3>逐条确认</h3>
                  <span>
                    {taskCount} 条待办 · {remCount} 条提醒
                    {confirmCount > 0 ? ` · ${confirmCount} 条要确认` : ""}
                  </span>
                </div>

                <div className="rem-cap-axis" aria-label="按时间排好的安排">
                  {ordered.map(({ item, slot, showDay }) => {
                    const on = picked[item.id] === true;
                    const isTask = item.kind === "task";
                    const needConfirm = needsConfirm(item, answers);
                    const issue = issues[item.id];
                    const no = items.indexOf(item) + 1;
                    const meta = metaOf(item);
                    return (
                      <div
                        className="rem-cap-axisrow"
                        key={item.id}
                        data-on={on ? "1" : "0"}
                        data-first-confirm={needConfirm ? "1" : undefined}
                        data-failed={issue ? "1" : undefined}
                        // 排序键落在 DOM 上：这条轴唯一的契约就是"按它升序"，
                        // 验收脚本据此判序，不靠肉眼也不靠猜日期
                        data-at={slot.at}
                      >
                        <div className="rem-cap-axiswhen">
                          {showDay ? <span className="rem-cap-axisday">{slot.day}</span> : null}
                          {slot.time ? <span className="rem-cap-axistime">{slot.time}</span> : null}
                        </div>

                        <button
                          type="button"
                          className="rem-cap-nodewrap"
                          role="checkbox"
                          aria-checked={on}
                          aria-label={on ? `取消选择 ${item.title}` : `选择 ${item.title}`}
                          onClick={() => toggle(item.id)}
                        >
                          <span
                            className="rem-cap-node"
                            data-kind={item.kind}
                            data-on={on ? "1" : "0"}
                          >
                            {/* 待办勾选后画勾；日程节点靠 CSS 转 45° 成菱形，不需要内部元素 */}
                            {isTask && on ? <CornIcon name="check" className="h-3 w-3" /> : null}
                          </span>
                        </button>

                        <div className="rem-cap-axisbody">
                          <p className="rem-cap-axisttl">
                            <span>{item.title}</span>
                            {issue ? (
                              <span className="rem-cap-flag" data-tone="bad">
                                没加进去
                              </span>
                            ) : !isWritable(item) ? (
                              // 「写不进去」比「待确认」更硬，所以排在它前面：
                              // 一条既缺时间又要确认的，先得让用户看见"这条现在根本加不进去"。
                              // 判据用 isWritable 而不是"有没有时间字段"——
                              // 「每天」但没钟点的提醒，字段是齐的，照样写不进去。
                              <span className="rem-cap-flag" data-tone="muted">
                                还没说时间
                              </span>
                            ) : needConfirm ? (
                              <span className="rem-cap-flag" data-tone="ask">
                                要确认
                              </span>
                            ) : (
                              <span className="rem-cap-flag" data-tone="ok">
                                可以直接加
                              </span>
                            )}
                            {/* 编号按原话出现顺序，排在行尾当脚注。
                                点它 → 展开原话并高亮这一句（"第 3 句排到了第 1 位"就是证据） */}
                            <button
                              type="button"
                              className="rem-cap-no"
                              onClick={() => setFocusId(item.id)}
                              aria-label={`回看第 ${no} 句原话`}
                            >
                              {no}
                            </button>
                          </p>

                          {meta ? <p className="rem-cap-axisnote">{meta}</p> : null}
                          {/* 原因挂在那一行自己身上。只给个红标「没加上」，
                              用户不知道是该重试还是该改 —— 两条路差得很远 */}
                          {issue ? (
                            <p className="rem-cap-issue" role="alert">
                              {issue}
                            </p>
                          ) : null}

                          {/* 「缺少时间」不能只是个标签：标了却不给出路，用户唯一能做的就是
                              取消勾选，等于白说一句「你的话没说清」。入口就挂在缺时间的那一行上。 */}
                          {item.kind === "reminder" && !isWritable(item) ? (
                            <div className="rem-cap-fix">
                              <button
                                type="button"
                                className="rem-cap-fix-btn"
                                aria-expanded={fixing?.id === item.id}
                                aria-controls={`rem-cap-fix-${item.id}`}
                                onClick={() => {
                                  if (fixing?.id === item.id) {
                                    setFixing(null);
                                    setFixErr("");
                                  } else {
                                    openFix(item);
                                  }
                                }}
                              >
                                {fixing?.id === item.id ? "收起" : "补上时间"}
                              </button>

                              {fixing?.id === item.id ? (
                                <div
                                  className="rem-cap-fix-panel"
                                  id={`rem-cap-fix-${item.id}`}
                                  role="group"
                                  aria-label="设置这条提醒的时间"
                                >
                                  {needsClockOnly(item) ? null : (
                                    <input
                                      type="date"
                                      className="rem-cap-fix-input"
                                      aria-label="日期"
                                      value={fixing.date}
                                      onChange={(e) => {
                                        setFixErr("");
                                        setFixing((prev) =>
                                          prev ? { ...prev, date: e.target.value } : prev
                                        );
                                      }}
                                    />
                                  )}
                                  <input
                                    type="time"
                                    className="rem-cap-fix-input"
                                    aria-label="几点"
                                    value={fixing.time}
                                    onChange={(e) => {
                                      setFixErr("");
                                      setFixing((prev) =>
                                        prev ? { ...prev, time: e.target.value } : prev
                                      );
                                    }}
                                  />
                                  {/* 拦下的理由放在按钮**上面**：它在最底下时，
                                      小屏会被底栏压出去 —— 断言读得到 textContent，
                                      用户却什么都看不见（真机截图抓到过）。 */}
                                  {fixErr ? (
                                    <p className="rem-cap-fix-err" role="alert">
                                      {fixErr}
                                    </p>
                                  ) : (
                                    <p className="rem-cap-fix-hint">
                                      {needsClockOnly(item)
                                        ? "这条会重复提醒，还差一个时间。"
                                        : "补好后，它会在设定时间提醒你。"}
                                    </p>
                                  )}
                                  <button
                                    type="button"
                                    className="rem-cap-fix-ok"
                                    onClick={applyFix}
                                  >
                                    用这个时间
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          ) : null}

                          {/* 不变量 #1：只设截止不会响。上一版把这句话排成一行提示、整屏只挂一次，
                              用户读完还是不知道「不提醒」是什么（连写它的人都记成了「只退出不完成」）。
                              改成挂在那一行自己身上：状态词「不提醒」点开就是后果说明 ——
                              疑问在哪产生，答案就在哪展开。 */}
                          {isTask && item.remind_at === null ? (
                            <div className="rem-cap-nrmd">
                              <button
                                type="button"
                                className="rem-cap-nrmd-btn"
                                aria-expanded={openNrmd === item.id}
                                aria-controls={`rem-cap-nrmd-${item.id}`}
                                onClick={() =>
                                  setOpenNrmd((prev) => (prev === item.id ? null : item.id))
                                }
                              >
                                不提醒你
                                <span className="rem-cap-nrmd-caret" aria-hidden="true">
                                  ▾
                                </span>
                              </button>
                              {openNrmd === item.id ? (
                                <p className="rem-cap-nrmd-panel" id={`rem-cap-nrmd-${item.id}`}>
                                  加入后不会提醒你。想收到提醒，加入后在列表里打开它。
                                </p>
                              ) : null}
                            </div>
                          ) : null}

                          {needConfirm
                            ? item.questions.map((q) => (
                                <div className="rem-cap-ask" key={q.id}>
                                  <p className="rem-cap-ask-q">{q.text}</p>
                                  <div className="rem-cap-chips">
                                    {q.options.map((opt) => (
                                      <button
                                        key={opt.value}
                                        type="button"
                                        className="rem-cap-chip rem-cap-chip-btn"
                                        data-on={answers[q.id] === opt.value ? "1" : "0"}
                                        onClick={() =>
                                          setAnswers((prev) => ({ ...prev, [q.id]: opt.value }))
                                        }
                                      >
                                        {opt.label}
                                      </button>
                                    ))}
                                    <button
                                      type="button"
                                      className="rem-cap-chip rem-cap-chip-btn"
                                      data-on={
                                        answers[q.id] === "__later" ? "1" : "0"
                                      }
                                      onClick={() =>
                                        setAnswers((prev) => ({ ...prev, [q.id]: "__later" }))
                                      }
                                      title="先按当前时间加入，之后在列表里改"
                                    >
                                      稍后设置
                                    </button>
                                  </div>
                                </div>
                              ))
                            : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )
          ) : null}
        </div>

        {/* ③ 底部：输入态 / 整理中 / 失败态 / 0 条都是同一个 composer ——
               这四种状态的下一步都是"再写一句"，给同一个输入框比给一个"回到输入态"的按钮更直接。
               整理中也留着（只是禁用）：撤掉的话下半屏会空出一大块，输入栏还跳了位置
               —— 这正是"上下两边太空"的来源。结果态才换成"后果 + 主按钮"。 */}
        {status === "idle" ||
        status === "parsing" ||
        status === "failed" ||
        (status === "ready" && items.length === 0) ? (
          <footer className="rem-cap-dock">
            {/* 底栏不再挂示例 chips（11 日第二轮删）。
                原来屏幕上有三处在举例：演示卡、这排 chips、输入框 placeholder。
                chips 是最吃亏的那个 —— 四个短语挤一行，每个被压到八来个字，
                既读不全，又把输入栏顶高了一截。示例卡本身现在可点，
                「一键试一句」的能力没有丢，丢的只是重复。 */}

            {/* 录音/听写是**状态**不是引导：只在真的发生时出现，平时一个字都不占 */}
            {voice.phase !== "idle" ? (
              <p className="rem-cap-voice" aria-live="polite">
                {voice.phase === "recording"
                  ? `正在录音 ${clock(voice.seconds)}，说完点方块`
                  : "正在听写…"}
              </p>
            ) : null}
            {voiceErr ? (
              <p className="rem-cap-fix-err" role="alert">
                {voiceErr}
              </p>
            ) : null}

            <div className="rem-cap-composer">
              {/* 没接转写服务就不渲染麦克风：按钮摆在那儿、点下去告诉你"还没做"，
                  比根本没有这个入口更像半成品。 */}
              {voiceOn ? (
                <button
                  type="button"
                  className="rem-cap-mic"
                  data-rec={voice.phase === "recording" ? "1" : undefined}
                  aria-label={
                    voice.phase === "recording"
                      ? "停止录音"
                      : voice.phase === "transcribing"
                        ? "正在听写"
                        : "用说的"
                  }
                  disabled={voice.phase === "transcribing" || status === "parsing"}
                  onClick={() =>
                    voice.phase === "recording" ? void voice.stop() : void voice.start()
                  }
                >
                  {voice.phase === "recording" ? (
                    <span className="rem-cap-mic-stop" aria-hidden="true" />
                  ) : (
                    <MicIcon className="h-[18px] w-[18px]" />
                  )}
                </button>
              ) : null}
              <textarea
                ref={inputRef}
                id="rem-cap-say"
                className="rem-cap-input"
                value={text}
                rows={1}
                // 去掉「例如：」—— 一行具体的话本身就是例子，前面再挂个标签，
                // 是把输入框当说明书在用。句式跟示例卡的原话保持一致（说「三点」不说「3 点」）
                placeholder="明天下午三点交高数作业"
                aria-label="要整理的话"
                autoComplete="off"
                spellCheck={false}
                enterKeyHint="send"
                // 整理中 / 录音中锁住：runParse 里本来就会挡，但让光标还能动会骗人。
                // 录音时更不能编辑 —— 转写结果回来是**追加**到原文后面的，
                // 用户一边打字一边说话，追加位置会对不上。
                disabled={status === "parsing" || voice.phase !== "idle"}
                onChange={(e) => setText(e.target.value)}
                onCompositionStart={() => {
                  composing.current = true;
                }}
                onCompositionEnd={() => {
                  composing.current = false;
                }}
                onKeyDown={(e) => {
                  // 中文输入法正在组字时回车是"选词"，不是"发送"。
                  if (e.key === "Enter" && !e.shiftKey && !composing.current) {
                    e.preventDefault();
                    void runParse(text);
                  }
                }}
              />
              <button
                type="button"
                className="rem-cap-send"
                aria-label={status === "parsing" ? "正在整理" : "开始整理"}
                disabled={status === "parsing" || voice.phase !== "idle" || !text.trim()}
                onClick={() => void runParse(text)}
              >
                <ArrowUp className="h-[18px] w-[18px]" />
              </button>
            </div>
          </footer>
        ) : null}

        {/* 0 条时不挂结果底栏：一条都没整理出来还摆"加入 0 条"，
            等于让用户确认一件不存在的事（§6 结果-0 条） */}
        {status === "ready" && items.length > 0 ? (
          <footer className="rem-cap-dock rem-cap-dock--result">
            <div className="rem-cap-summary">
              <span>
                已选 <b>{pickedItems.length}</b> 条 · <b>{remindCount}</b> 条会提醒你
              </span>
              <button
                type="button"
                className="rem-cap-link"
                onClick={() =>
                  setPicked(allPicked ? {} : Object.fromEntries(items.map((i) => [i.id, true])))
                }
              >
                {allPicked ? "全不选" : "全选"}
              </button>
            </div>

            {issueCount > 0 ? (
              <p className="rem-cap-failbar" role="alert">
                {lastOk > 0
                  ? `已加入 ${lastOk} 条，${issueCount} 条没加上。这几条还在上面，改完可以再点一次。`
                  : `${issueCount} 条都没加上，原因写在每一条下面。`}
              </p>
            ) : null}

            <div className="rem-cap-acts">
              <button
                type="button"
                className="rem-cap-ghost rem-cap-acts-ghost"
                onClick={review}
              >
                逐条查看
              </button>
              <button
                type="button"
                className="rem-cap-primary"
                disabled={pickedItems.length === 0 || committing}
                onClick={() => void commit()}
              >
                <CornIcon name="check" className="h-4 w-4" />
                {committing
                  ? "加入中…"
                  : pickedItems.length === 0
                    ? "至少选一条，才能加入"
                    : writableCount === 0
                      ? // 一条都写不进去时不能说「加入 1 条」：这按钮点了什么都不会发生。
                        // 仍然让它可点 —— 点一下会给出统一的原因，比一个按不动的死按钮好懂
                        `${pickedItems.length} 条还不能加`
                      : remindCount > 0
                        ? `加入 ${writableCount} 条，并设 ${remindCount} 条提醒`
                        : `加入 ${writableCount} 条，不提醒`}
              </button>
            </div>
          </footer>
        ) : null}
      </div>
    </>
  );
}

// ==================== 局部组件 ====================

/** 整理中 / 失败时显示的原文。它不是聊天气泡，是"你说过的话"的证据卡。 */
function SourceQuote({ text }: { text: string }) {
  return (
    <div className="rem-cap-srcquote">
      <span className="rem-cap-srcquote-k" aria-hidden="true">
        你说的
      </span>
      <p>{text}</p>
      <span className="rem-cap-srcquote-meta">文字 · 刚刚</span>
    </div>
  );
}

// ==================== 纯函数 ====================

function needsConfirm(item: CaptureProposal, answers: Record<string, string>): boolean {
  if (!item.needs_confirm) return false;
  // 追问答过一条就不再算「需确认」——否则用户答完了标记还挂着
  return !item.questions.some((q) => answers[q.id]);
}

/**
 * 行下面那一行小字。**只放用户需要用来做判断的信息**：
 * 归到哪个清单、提醒几点、有没有附注。
 * "每周三""一次性"这类不该在这里 —— 左栏的日期锚点已经写了。
 */
function metaOf(item: CaptureProposal): string | null {
  const bits: string[] = [];
  const rec = item.recurrence;
  if (rec && rec.freq !== "once") {
    bits.push(
      rec.freq === "daily"
        ? "每天"
        : rec.freq === "weekdays"
          ? "工作日"
          : `每周${WEEK_ZH[rec.weekday ?? 1]}`
    );
  }
  if (item.kind === "task") {
    const key = item.list_key ?? "inbox";
    if (key !== "inbox") bits.push(LIST_LABEL[key]);
    if (item.remind_at !== null) bits.push(`${hmOf(item.remind_at)} 提醒`);
  }
  if (item.note) bits.push(item.note);
  return bits.length > 0 ? bits.join(" · ") : null;
}
