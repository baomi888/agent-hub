/**
 * Phase A 的本地 fixture：**没有网络，也没有模型**。
 *
 * 它只做一件事：把一段话按标点拆成几件事，给每件事一个 kind / 时间 / 清单，
 * 并把 `source_text` 定位回原话。目的不是"解析得准"，而是让抽屉的
 * 输入 → 整理中 → 结果 → 勾选 → 关闭 这条链路能真的跑起来、能被验收。
 *
 * ★ Phase B 会把它换成 `api.capture.parse(text)`。换的时候**不要动抽屉**：
 *   两者返回同一个 `CaptureParseResult`，抽屉只认这个形状。
 * ★ 这里的 offset 是本地 `indexOf` 算的；接后端后 offset 由服务端算，
 *   前端一行都不许自己编 —— 否则"点编号回看原话"就会指错句子。
 *
 * 三条和时间有关的硬规则（和 core/task_core.py 对齐）：
 *   1. `due_at` 是当天 **23:59:59**，不是零点；零点折 UTC 会退回前一天。
 *   2. `remind_at` 只在原话里说了"提醒我 / 叫我 / 记得"时才有。
 *      只设截止不会响 —— 这是不变量 #1，界面上也写了这句话。
 *   3. 重复的事只能是 reminder（Phase 1 的 Task.recurrence 只允许 "none"）；
 *      一次性的提醒用 `freq:"once" + fire_at`。
 *
 * 另外它还负责"人说话的样子"（2026-11-10 晚，按 TimeMind 的形态重做时加的）：
 *   · 整句都是"呃 / 然后那个 / 哦对了"→ 丢掉，且不计入 unresolved；
 *   · "不对 / 说错了 / 改成"开头 → 改口标记，真正的新时间在下一句；
 *   · 只有时间没有动作的一句（"四点"）→ 打到上一条头上，覆盖时间；
 *   · 取件码 / 单号这类 → 挂到上一条的 note，不单独成事。
 * 这些是**启发式**，不是模型。Phase B 换成模型后它们该整体删掉，
 * 但"改口只留最后一次、按时间排"这两条产品规则不变 —— 验收脚本钉的是结果，
 * 不是实现，所以换实现不用改断言。
 */

import type {
  CaptureParseResult,
  CaptureProposal,
  CaptureQuestion,
  CaptureRecurrence,
  TaskListKey,
  TaskPriority,
} from "@/lib/types";

/** 示例句。点一下只**填入**输入框，不自动提交（视觉基线 §7 负向断言）。 */
export const CAPTURE_EXAMPLES = ["明天交高数作业", "周五社团开会", "每晚背单词", "下周课程论文"];

/** 一次最多收 12 条（规格 §5.2）。超出的截断并放进 unresolved。 */
export const CAPTURE_MAX_ITEMS = 12;

/** 输入上限 2000 字（规格 §5.3）。超了不报错，截断并说明。 */
const MAX_TEXT = 2000;

/** 一周的中文别名。周日同时接受"日"和"天"。 */
const WEEKDAY: Record<string, number> = {
  一: 1,
  二: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  日: 0,
  天: 0,
};

/** 出现这些词，多半是"到点要做的一件事"，不是"要交的作业"。 */
const EVENT_WORDS = /开会|会议|例会|上课|讲座|聚餐|活动|演出|面试|值班|体检|答辩|宣讲|排练|生日|纪念日|约/;

/** 出现这些词，多半有"要做完/交出去"的动作 → 待办。 */
const TASK_WORDS =
  /作业|论文|报告|习题|实验|报名|提交|交给|交|写|背|复习|预习|打印|填|买|取|寄|下载|准备/;

/** 说了"提醒我"才建 remind_at；不要把 due_at 自动复制过去（不变量 #1）。 */
export const ASK_WORDS = /提醒我|叫我一声|叫我|记得|别忘/;

const LIST_RULES: [RegExp, TaskListKey][] = [
  // 先判生活开支：否则下面的「交」会把"交房租"抢去作业清单
  [/房租|交租|水电费|物业费|话费|缴费/, "life"],
  [/作业|论文|报告|习题|实验|周报|交|写|打印|提交/, "homework"],
  [/考试|期中|期末|四六级|雅思|托福|考研|测验|补考|证书|报名/, "exam"],
  [/上课|课程|讲座|预习|复习|网课|学分|开题|答辩/, "course"],
  [/社团|例会|开会|招新|策划|比赛|演出|聚餐|活动|排练/, "club"],
  [/快递|取|买|缴费|宿舍|食堂|体检|报销|理发|洗衣|办证/, "life"],
];

/**
 * 把一段话拆成 proposal。
 *
 * `nowMs` 由调用方给（页面里有每分钟走一次的 `now`），**不在渲染期取当前时间** ——
 * 那会让同一次渲染两次结果不同，`react-hooks/purity` 直接报红。
 *
 * 返回 Promise 是为了让"整理中"这一帧真的被画出来：这里等两帧再返回，
 * 不是假装慢，而是让 pending 状态有一次真实的存在。Phase B 换成真实请求后
 * 删掉这两帧 —— 网络自己会带来延迟。
 */
export async function parseFixture(text: string, nowMs: number): Promise<CaptureParseResult> {
  const raw = text.slice(0, MAX_TEXT);
  const items: CaptureProposal[] = [];
  const unresolved: string[] = [];

  for (const { clause, start } of splitClauses(raw)) {
    const trimmed = clause.trim();
    if (!trimmed) continue;
    // offset 是"点编号回看原话"的命脉：trim 掉前导空白后必须跟着挪
    const at = start + clause.indexOf(trimmed);

    // ---------- 先把「人说话的样子」去掉，再谈解析 ----------

    // 1) 整句都是"呃 / 然后那个 / 哦对了"：丢掉，且**不算没看懂**。
    //    往 unresolved 里塞一条等于告诉用户"你说了句废话"，那是噪音。
    if (FILLER_ONLY.test(trimmed)) continue;

    // 2) 改口：「不对」「说错了」本身不是一件事，真正的新时间在下一句，
    //    由第 3 条打到上一条头上（"三点，不对，四点" → 只留四点）。
    if (CORRECTION_START.test(trimmed)) {
      if (items.length > 0) patchTime(items[items.length - 1], trimmed, nowMs);
      continue;
    }

    // 3) 只有时间没有动作（"四点"）：当成对上一条的时间修正，不新起一条。
    if (isTimeOnly(trimmed) && items.length > 0) {
      patchTime(items[items.length - 1], trimmed, nowMs);
      continue;
    }

    // 4) 取件码 / 单号这类附注：挂到上一条上，不单独成事。
    if (ATTACH_TO_PREV.test(trimmed) && items.length > 0) {
      const last = items[items.length - 1];
      last.note = last.note ? `${last.note} · ${trimmed}` : trimmed;
      continue;
    }

    // ---------- 剩下的才是"一件事" ----------
    const draft = parseClause(trimmed, at, nowMs, items.length + 1);
    if (!draft) {
      unresolved.push(`没看懂「${trimmed}」`);
      continue;
    }
    items.push(draft);
  }

  if (items.length > CAPTURE_MAX_ITEMS) {
    unresolved.push(`一次最多 ${CAPTURE_MAX_ITEMS} 条，多出来的先没排`);
    items.length = CAPTURE_MAX_ITEMS;
  }
  if (text.length > MAX_TEXT) {
    unresolved.push(`只看了前 ${MAX_TEXT} 个字`);
  }

  await nextFrame();
  return { items, unresolved };
}

// ==================== 口语噪声 ====================

/** 整句只有这些词 = 用户在思考，不是在派活。按长度从长到短排，避免"然后"吃掉"然后呢"。
 *
 *  外层必须允许**多个填词连用**：「然后那个」「嗯，就是」「哦对了那个」都是同一句废话，
 *  只钉单个词的话，"然后那个"会拼成一条标题叫「然后那个」的待办（2026-11-10 实测撞到）。
 *  但仍然是"整句只能由填词组成"——「然后开会」里剩了"开会"，照样不吞。 */
const FILLER_ONLY = new RegExp(
  `^(?:(?:${[
    "哦对了",
    "我跟你说",
    "也就是说",
    "怎么说呢",
    "然后呢",
    "所以说",
    "就是说",
    "那个",
    "内个",
    "这个",
    "然后",
    "还有",
    "另外",
    "以及",
    "顺便",
    "反正",
    "就是",
    "那啥",
    "呃",
    "嗯",
    "啊",
    "诶",
    "唉",
    "哦",
    "噢",
    "哎",
  ].join("|")})[\\s，,。.、…]*)+$`
);

/** 改口的开头。后面的时间由 `patchTime` 覆盖到上一条上。 */
const CORRECTION_START = /^(?:不对|不是|说错了|说错|搞错了|哦不|应该是|改成|改到|改一下)/;

/** 附注：这些是"一串号码"，不是"一件事"。 */
const ATTACH_TO_PREV = /取件码|取货码|验证码|取件号|单号|订单号|尾号|馆藏号/;

/** 把"只有时间"的句子认出来：去掉时间词之后什么都不剩。 */
function isTimeOnly(clause: string): boolean {
  const rest = clause
    .replace(/(?:这|下|下个|上)?(?:周|星期)[一二三四五六日天]/g, "")
    .replace(/(?:今天|明天|后天|今晚|明晚|今早|明早|今儿)/g, "")
    .replace(/\d{1,2}\s*[:：]\s*\d{2}/g, "")
    .replace(/(?:[一二两三四五六七八九十]{1,3}|\d{1,2})\s*点\s*(?:半|整|\d{1,2}\s*分?)?/g, "")
    .replace(/(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|晚间)/g, "")
    .replace(/^(?:就|是|在|改成|改到|不对|不是|说错(?:了)?)+/g, "")
    .replace(/[\s，,。.；;！!？?、…]/g, "");
  return rest === "";
}

/** 某条 proposal 现在落在哪一天的 00:00（本地时区的 Unix 秒）。 */
function dayStartOf(sec: number | null): number | null {
  if (sec === null) return null;
  const d = new Date(sec * 1000);
  d.setHours(0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

/**
 * 把「只有时间」的一句话打到上一条上。
 *
 * 这是"说话颠三倒四"里最容易被实现漏掉的一半：用户先说事、后补时间，
 * 或者说到一半改口。**只改时间，不新建条目**，否则一句话会裂成三条垃圾。
 */
function patchTime(item: CaptureProposal, clause: string, nowMs: number): void {
  const clock = readClock(clause);
  const when = readWhen(clause, nowMs);
  if (when.dayStart === null && clock === null) return;

  // 句子没说日期就沿用这一条原来的那天 —— "三点，不对，四点"只换了钟点
  const anchor = item.kind === "task" ? item.due_at : (item.recurrence?.fire_at ?? null);
  const dayStart = when.dayStart ?? dayStartOf(anchor);
  if (dayStart === null) return;

  const hour = clock ? clock.hour : when.hour;
  const minute = clock ? clock.minute : when.minute;

  if (item.recurrence && item.recurrence.freq !== "once") {
    if (clock) item.recurrence = { ...item.recurrence, at: clockText(clock) };
    return;
  }

  if (item.kind === "task") {
    if (when.dayStart !== null) item.due_at = when.dueAt;
    if (item.remind_at !== null && hour !== null) {
      item.remind_at = dayStart + hour * 3600 + minute * 60;
    }
    return;
  }

  if (item.recurrence && item.recurrence.freq === "once") {
    item.recurrence = {
      ...item.recurrence,
      fire_at: dayStart + (hour ?? 18) * 3600 + minute * 60,
    };
  }
}

// ==================== 拆句 ====================

const SPLIT = /[，,。.；;！!？?\n]+/;

/** 按标点切句，**同时留下每句在原话里的起点** —— 编号高亮全靠它。 */
function splitClauses(text: string): { clause: string; start: number }[] {
  const out: { clause: string; start: number }[] = [];
  let cursor = 0;
  for (const seg of text.split(SPLIT)) {
    const at = text.indexOf(seg, cursor);
    const start = at >= 0 ? at : cursor;
    out.push({ clause: seg, start });
    cursor = start + seg.length;
  }
  return out;
}

// ==================== 单句 ====================

function parseClause(
  clause: string,
  start: number,
  nowMs: number,
  index: number
): CaptureProposal | null {
  const repeat = readRepeat(clause);
  const when = readWhen(clause, nowMs);
  // 重复的事一律是 reminder：Phase 1 的 Task.recurrence 只允许 "none"，
  // 硬写成可勾选的待办会变成"勾掉了明天还响"的四不像。
  const kind: "task" | "reminder" = repeat ? "reminder" : readKind(clause);
  const title = readTitle(clause);
  if (!title) return null;

  const questions: CaptureQuestion[] = [];
  let needsConfirm = false;

  if (kind === "reminder") {
    if (!repeat && when.kind === "none") {
      // 没说哪天：允许待定，但必须标出来，不能替用户编一个日期
      needsConfirm = true;
    } else if (!repeat && when.weekday !== null) {
      // 必须带"周"：weekdayZh 只给一个「六」，直接拼就是"是这六，还是以后每六"
      const zh = `周${weekdayZh(when.weekday)}`;
      needsConfirm = true;
      questions.push({
        id: `q${index}w`,
        text: `是这${zh}，还是以后每${zh}？`,
        options: [
          { label: `这${zh}`, value: "once" },
          { label: `每${zh}`, value: "weekly" },
        ],
      });
    } else if (!repeat && !when.hasClock) {
      needsConfirm = true;
      questions.push({
        id: `q${index}t`,
        text: `${title}几点开始？`,
        options: [
          { label: "18:00", value: "18:00" },
          { label: "20:00", value: "20:00" },
        ],
      });
    }
  } else if (when.kind === "none") {
    needsConfirm = true;
  }

  // 只设截止不会响：没说过"提醒我"就一个 remind_at 都不给。
  const wantsRemind = kind === "task" && ASK_WORDS.test(clause);

  return {
    id: `p${index}`,
    kind,
    title,
    note: null,
    list_key: kind === "task" ? readList(clause) : null,
    priority: readPriority(clause),
    due_at: kind === "task" ? when.dueAt : null,
    remind_at: wantsRemind ? when.remindAt : null,
    recurrence: kind === "reminder" ? buildRecurrence(repeat, when) : null,
    source_text: clause,
    source_start: start,
    source_end: start + clause.length,
    needs_confirm: needsConfirm,
    questions,
  };
}

function readKind(clause: string): "task" | "reminder" {
  if (EVENT_WORDS.test(clause)) return "reminder";
  if (TASK_WORDS.test(clause)) return "task";
  // 两边都没命中：带钟点的更像日程，其余按"要交的事"处理。
  return /\d{1,2}\s*[:：]\s*\d{2}/.test(clause) ? "reminder" : "task";
}

function readTitle(clause: string): string {
  const hasClock = readClock(clause) !== null;
  let t = clause
    // 开头的口水词。口述时几乎每句都有，不剥掉标题会变成"呃明天交高数作业"
    .replace(
      /^(?:呃+|嗯+|诶+|唉+|那个|内个|就是说|然后呢?|哦对了|对了|还有|另外|以及|顺便|我跟你说|加上)+/,
      ""
    )
    .replace(/(?:这|下|下个|上|每)?(?:周|星期)[一二三四五六日天](?:前|之前|以前)?/g, "")
    .replace(/下周|这周|下个?月|这个月/g, "")
    .replace(/(今天|明天|后天|今晚|明晚|今早|明早|今儿)/g, "")
    .replace(/每天|每晚|每日|每个工作日|工作日/g, "")
    .replace(/\d{1,2}\s*[:：]\s*\d{2}/g, "")
    // 中文钟点：`3点` 与 `三点` 都要剥，但"点心/一点儿"不能剥。
    // 不能用「后面必须是标点或结尾」那条判据 —— "三点拿体检报告"后面是「拿」，
    // 那样会漏掉一个「三点」留在标题里（实测过）。
    .replace(/(?:[一二两三四五六七八九十]{1,3}|\d{1,2})\s*点(?![心子儿滴头名点])\s*(?:半|整|\d{1,2}\s*分?)?/g, "")
    .replace(/提醒我|叫我一声|叫我|记得|别忘(了)?/g, "")
    .replace(/那个|这个/g, "")
    .replace(/^(要|得|还得|也要|还要)/, "")
    .replace(/(要交|得交|要弄|得弄|要做|得做)$/, "")
    .trim();
  // 「下午四点拿体检报告」→ 时段词在有钟点时是修饰语，删；
  // 「晚上陪室友吃饭」里的晚上是内容，留。
  if (hasClock) t = t.replace(/(凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|晚间)\s*/g, "");
  // 只剩标点或碎片时退回原句，别产出空标题
  if (!t || !/[\u4e00-\u9fa5A-Za-z0-9]/.test(t)) t = clause;
  return t.slice(0, 60);
}

function readList(clause: string): TaskListKey {
  for (const [re, key] of LIST_RULES) {
    if (re.test(clause)) return key;
  }
  return "inbox";
}

function readPriority(clause: string): TaskPriority {
  if (/重要|必须|急|马上|赶紧/.test(clause)) return 2;
  if (/尽量|最好|有空/.test(clause)) return 1;
  return 0;
}

// ==================== 时间 ====================

interface Repeat {
  freq: "daily" | "weekly" | "weekdays";
  weekday: number | null;
  at: string | null;
}

interface When {
  kind: "none" | "day" | "weekday";
  weekday: number | null;
  hour: number | null;
  minute: number;
  hasClock: boolean;
  /** 那一天的 00:00 的 Unix 秒；没有日期时为 null */
  dayStart: number | null;
  dueAt: number | null;
  remindAt: number | null;
}

const NONE: When = {
  kind: "none",
  weekday: null,
  hour: null,
  minute: 0,
  hasClock: false,
  dayStart: null,
  dueAt: null,
  remindAt: null,
};

function readRepeat(clause: string): Repeat | null {
  const at = readClock(clause);
  const text = at ? clockText(at) : null;
  if (/每天|每晚|每日/.test(clause)) return { freq: "daily", weekday: null, at: text };
  if (/工作日/.test(clause)) return { freq: "weekdays", weekday: null, at: text };
  const m = clause.match(/每(?:周|星期)([一二三四五六日天])/);
  if (m) return { freq: "weekly", weekday: WEEKDAY[m[1]] ?? 1, at: text };
  return null;
}

function readWhen(clause: string, nowMs: number): When {
  const clock = readClock(clause);
  const hour = clock ? clock.hour : null;
  const minute = clock ? clock.minute : 0;
  const hasClock = clock !== null;

  const today = new Date(nowMs);
  today.setHours(0, 0, 0, 0);

  const wd = clause.match(/(?:这|下|下个)?(?:周|星期)([一二三四五六日天])/);
  let offset: number | null = null;
  let weekday: number | null = null;

  if (wd) {
    weekday = WEEKDAY[wd[1]] ?? 1;
    const diff = (weekday - today.getDay() + 7) % 7;
    // 今天就是周五、又说"周五"：按下一周算，避免一建出来就是"已过"
    offset = diff === 0 ? 7 : diff;
  } else if (/今天|今晚|今早|今儿/.test(clause)) {
    offset = 0;
  } else if (/明天|明晚|明早/.test(clause)) {
    offset = 1;
  } else if (/后天/.test(clause)) {
    offset = 2;
  } else if (/下周/.test(clause)) {
    offset = 7;
  }

  if (offset === null) {
    return { ...NONE, hour, minute, hasClock };
  }

  const day = new Date(today);
  day.setDate(day.getDate() + offset);
  const end = new Date(day);
  end.setHours(23, 59, 59, 0);

  // 提醒默认提前一小时（说了钟点就按钟点），且不能晚于截止
  const at = new Date(day);
  at.setHours(hour ?? 20, minute, 0, 0);
  const reminderAt = Math.floor(at.getTime() / 1000);
  const dueAt = Math.floor(end.getTime() / 1000);

  return {
    kind: wd ? "weekday" : "day",
    weekday,
    hour,
    minute,
    hasClock,
    dayStart: Math.floor(day.getTime() / 1000),
    dueAt,
    remindAt: reminderAt < dueAt ? reminderAt : null,
  };
}

/** 中文钟点。口述里"三点"比"15:00"常见得多，不认它整条链路就演示不了。 */
const CN_HOUR: Record<string, number> = {
  一: 1,
  二: 2,
  两: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  七: 7,
  八: 8,
  九: 9,
  十: 10,
  十一: 11,
  十二: 12,
};

/** 钟点 + 时段词。「下午四点」是 16 点，「上午十点」还是 10 点。 */
function shiftHour(hour: number, before: string): number {
  if (/(凌晨|早上|上午|清早)\s*$/.test(before)) return hour;
  if (/中午\s*$/.test(before)) return hour <= 2 ? 12 + hour : hour;
  if (/(下午|傍晚|晚上|夜里|晚间|今晚|明晚)\s*$/.test(before)) return hour < 12 ? hour + 12 : hour;
  return hour;
}

/**
 * 中文数字后面跟着的"点"要能认出是钟点，又不能把"点心""一点儿"吃进去。
 * 这几个尾巴字是唯二会撞上的组合（重点/特点/地点 前面不是数字，撞不到）。
 */
const CN_CLOCK = /([一二两三四五六七八九十]{1,3})\s*点(?![心子儿滴头名])\s*(半|整|\d{1,2}\s*分?)?/;

function readClock(clause: string): { hour: number; minute: number } | null {
  // 1) 15:30 / 15：30
  const hm = clause.match(/(\d{1,2})\s*[:：]\s*(\d{2})/);
  if (hm) {
    const h = Number(hm[1]);
    const m = Number(hm[2]);
    if (h >= 0 && h <= 23 && m >= 0 && m <= 59) {
      return { hour: shiftHour(h, clause.slice(0, hm.index ?? 0)), minute: m };
    }
  }

  const toMinute = (tail: string | undefined) => {
    if (tail === "半") return 30;
    if (!tail || tail === "整") return 0;
    const m = Number(tail.replace(/\D/g, ""));
    return Number.isFinite(m) && m < 60 ? m : 0;
  };

  // 2) 下午四点半 —— 中文数字优先，口述里"三点"比"3点"常见
  const cn = clause.match(CN_CLOCK);
  if (cn) {
    const h = CN_HOUR[cn[1]];
    if (h === undefined) return null;
    return { hour: shiftHour(h, clause.slice(0, cn.index ?? 0)), minute: toMinute(cn[2]) };
  }

  // 3) 4点 / 4点20 / 晚上8点半
  const num = clause.match(/(\d{1,2})\s*点\s*(半|整|\d{1,2}\s*分?)?/);
  if (num) {
    const h = Number(num[1]);
    if (h > 23) return null;
    return { hour: shiftHour(h, clause.slice(0, num.index ?? 0)), minute: toMinute(num[2]) };
  }
  return null;
}

/**
 * 一次性事件必须落到 `fire_at`：`once` 不带 fire_at 就是"永远不响"。
 * 没说钟点的一次性日程默认 18:00（和设计稿一致）。
 */
function buildRecurrence(repeat: Repeat | null, when: When): CaptureRecurrence {
  if (repeat) {
    if (repeat.freq === "weekly") {
      return {
        freq: "weekly",
        at: repeat.at ?? "20:00",
        weekday: repeat.weekday,
        fire_at: null,
      };
    }
    return { freq: repeat.freq, at: repeat.at ?? "20:00", weekday: null, fire_at: null };
  }
  const fire =
    when.dayStart === null
      ? null
      : when.dayStart + (when.hour ?? 18) * 3600 + when.minute * 60;
  return { freq: "once", at: null, weekday: null, fire_at: fire };
}

function clockText(c: { hour: number; minute: number }): string {
  return `${String(c.hour).padStart(2, "0")}:${String(c.minute).padStart(2, "0")}`;
}

// ==================== 排序 ====================

/**
 * 这一条**下一次真正发生**的时刻（毫秒）。没有时间的返回 Infinity，排在最后。
 *
 * 这是整个"按时间排好"的唯一排序依据，也是重复项能插进正确位置的关键：
 * 每周三的瑜伽不能因为"它是重复的"就被丢到最后，它得按**下一个周三**排进去。
 * 不这么排，用户看到的还是一堆分组，而不是一条时间线。
 */
export function nextOccurrenceAt(item: CaptureProposal, nowMs: number): number {
  const rec = item.recurrence;

  if (rec && rec.freq !== "once") {
    const [h, m] = (rec.at ?? "20:00").split(":").map((v) => Number(v));
    const hour = Number.isFinite(h) ? h : 20;
    const minute = Number.isFinite(m) ? m : 0;
    const at = (offset: number) => {
      const d = new Date(nowMs);
      d.setDate(d.getDate() + offset);
      d.setHours(hour, minute, 0, 0);
      return d;
    };
    // 往后找 8 天一定够：每周 / 工作日 / 每天都在这之内
    for (let i = 0; i < 8; i += 1) {
      const d = at(i);
      if (d.getTime() <= nowMs) continue;
      const w = d.getDay();
      if (rec.freq === "daily") return d.getTime();
      if (rec.freq === "weekdays" && w !== 0 && w !== 6) return d.getTime();
      if (rec.freq === "weekly" && w === (rec.weekday ?? 1)) return d.getTime();
    }
    return Number.POSITIVE_INFINITY;
  }

  if (item.kind === "task") {
    return item.due_at === null ? Number.POSITIVE_INFINITY : item.due_at * 1000;
  }
  // 日程的「什么时候」有三个来源，优先级与 reminderDraftOf 完全一致：
  // 规则的绝对时刻 > 截止时间 > 单独设的提醒时间。
  // **只读 rec?.fire_at 是错的**：后端对一次性日程返回的就是
  // `recurrence: null` + `due_at: <秒>`（2026-11-11 真实对话里三条全是这个形状），
  // 那时 fire 恒为 null → 这条被判成「待定」，而同一个界面上它又写着「可直接加入」，
  // 因为 isWritable 走的是 reminderDraftOf，那边有 due_at 兜底。两套判据打架，
  // 结果就是「时间说待定、状态说能加」，加进去却有正确的时间。
  const fire = rec?.fire_at ?? item.due_at ?? item.remind_at;
  return fire === null || fire === undefined ? Number.POSITIVE_INFINITY : fire * 1000;
}

function weekdayZh(n: number): string {
  return ["日", "一", "二", "三", "四", "五", "六"][n] ?? "一";
}

/** 等两帧：让"整理中"这一帧真的被画出来。不是假装慢。 */
function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame !== "function") {
      resolve();
      return;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}
