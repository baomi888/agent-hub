// 一句话快速添加的**确定性**解析（不接大模型）。
//
// 为什么不接模型：这一行是最高频入口，用户敲完就想看到结果。
// 一次网络往返 + 一次 LLM 调用会让「记一条作业」变成两秒的等待，
// 而且同样的输入两次解析出不同结果，用户就不敢用了。
//
// 三条产品约束（来自实施单 §6.5）：
//
// 1. **「明天 9 点交作业」默认只设截止时间，不自动设提醒。**
//    偷偷把 due_at 复制成 remind_at，用户会以为「9 点交」等于「9 点响」，
//    然后在 8 点被吓一跳。要提醒就让用户自己点那个 chip。
// 2. 解析失败就把整句当标题，交给详情抽屉补时间。**不要猜。**
// 3. 时间是**本地**时区算出来的绝对时刻（Unix 秒），与后端落库口径一致。
//
// 这个文件刻意不 import 任何东西：它是纯函数，可以单独编译出来跑用例
// （tools/quick_parse_check.mjs 就是这么验的）。

export type QuickListKey = "inbox" | "course" | "homework" | "club" | "life" | "exam";

export type QuickKind = "due" | "remind" | "none";

export interface ParsedQuickAdd {
  /** 去掉时间词、清单标记、优先级标记之后的那句话 */
  title: string;
  list_key?: QuickListKey;
  priority?: 0 | 1 | 2;
  /** 截止时间（Unix 秒）；只给了日期时落在当天 23:59:59 */
  due_at?: number;
  /** 提醒时间（Unix 秒） */
  remind_at?: number;
  /** 解析出的时间落在哪一边。`none` = 没解析出时间 */
  kind: QuickKind;
  /** 解析出了「日期」而不是「具体时刻」 */
  date_only: boolean;
}

const LIST_ALIASES: Record<string, QuickListKey> = {
  收件箱: "inbox",
  课程: "course",
  作业: "homework",
  社团: "club",
  生活: "life",
  考试: "exam",
  inbox: "inbox",
  course: "course",
  homework: "homework",
  club: "club",
  life: "life",
  exam: "exam",
};

const WEEK_NAMES: Record<string, number> = {
  日: 0,
  天: 0,
  一: 1,
  二: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
};

/** 中文数字 → 阿拉伯数字（只到 24，够用了） */
const CN_NUM: Record<string, number> = {
  零: 0, 一: 1, 两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10,
};

function cnToNum(raw: string): number | null {
  if (/^\d{1,2}$/.test(raw)) return Number(raw);
  if (raw.length === 1) return CN_NUM[raw] ?? null;
  // 十一 / 十二 / 二十 / 二十三
  if (raw.startsWith("十") && raw.length === 2) return 10 + (CN_NUM[raw[1]] ?? 0);
  if (raw.length === 3 && raw[1] === "十") {
    return (CN_NUM[raw[0]] ?? 0) * 10 + (CN_NUM[raw[2]] ?? 0);
  }
  return null;
}

/** 当天的某个时刻（本地时区） */
function at(day: Date, hour: number, minute: number): number {
  const d = new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour, minute, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

function endOfDay(day: Date): number {
  const d = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 23, 59, 59, 0);
  return Math.floor(d.getTime() / 1000);
}

function addDays(day: Date, n: number): Date {
  return new Date(day.getFullYear(), day.getMonth(), day.getDate() + n, 12, 0, 0, 0);
}

/**
 * 解析一句话。
 *
 * @param text 用户输入
 * @param now  当前时刻（用于判断「今天 20:00 是不是已经过了」）
 */
export function parseQuickAdd(text: string, now: Date): ParsedQuickAdd {
  const out: ParsedQuickAdd = { title: "", kind: "none", date_only: false };
  let rest = String(text || "").trim();
  if (!rest) return out;

  // ① 清单：#作业 / #homework
  rest = rest.replace(/#([^\s#]+)/g, (whole, tag: string) => {
    const key = LIST_ALIASES[tag.trim()];
    if (key) {
      out.list_key = key;
      return " ";
    }
    return whole;
  });

  // ② 优先级：!高 / !急 / !重 → 紧急；!普 → 普通
  rest = rest.replace(/!([^\s!]+)/g, (whole, tag: string) => {
    const t = tag.trim();
    if (t === "高" || t === "急" || t === "重" || t === "h" || t === "H") {
      out.priority = 2;
      return " ";
    }
    if (t === "中" || t === "m" || t === "M") {
      out.priority = 1;
      return " ";
    }
    if (t === "低" || t === "普" || t === "l" || t === "L") {
      out.priority = 0;
      return " ";
    }
    return whole;
  });

  // ③ 提醒语义：「提醒我 / 叫我 / 记得」
  let wantsRemind = false;
  rest = rest.replace(/(提醒我|提醒|叫我|记得)/g, () => {
    wantsRemind = true;
    return " ";
  });

  // ④ 日期：今天 / 明天 / 后天 / 周X / 下周X / N 月 N 日
  // 用一个 holder 而不是裸变量：赋值发生在 replace 的回调里，TS 的控制流分析
  // 看不到闭包内的赋值，会把 `day` 一直当成 null 收窄成 never。
  const ctx: { day: Date | null } = { day: null };
  rest = rest.replace(/(大后天|后天|明天|今天|今儿|明儿)/g, (m) => {
    const n = m === "今天" || m === "今儿" ? 0 : m === "明天" || m === "明儿" ? 1 : m === "后天" ? 2 : 3;
    ctx.day = addDays(now, n);
    return " ";
  });

  if (!ctx.day) {
    const m = /(下)?(周|星期|礼拜)([一二三四五六日天])/.exec(rest);
    if (m) {
      const target = WEEK_NAMES[m[3]] ?? 1;
      let delta = (target - now.getDay() + 7) % 7;
      // 说「周五」而今天正好是周五 → 指下周五（今天已经过去了）
      if (delta === 0) delta = 7;
      if (m[1] === "下") delta += 7;
      ctx.day = addDays(now, delta);
      rest = rest.replace(m[0], " ");
    }
  }

  if (!ctx.day) {
    const m = /(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?/.exec(rest);
    if (m) {
      const month = Number(m[1]) - 1;
      const date = Number(m[2]);
      const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
      const candidate = new Date(now.getFullYear(), month, date, 12, 0, 0, 0);
      // 已经过去的日子算明年（11 月说「1 月 5 日」一定是明年）
      const year = candidate.getTime() < midnight.getTime() ? now.getFullYear() + 1 : now.getFullYear();
      ctx.day = new Date(year, month, date, 12, 0, 0, 0);
      rest = rest.replace(m[0], " ");
    }
  }
  const day = ctx.day;

  // ⑤ 时刻：HH:MM / N点(半|N分) / 上午·下午·晚上 + N点
  let hour: number | null = null;
  let minute = 0;

  const colon = /(\d{1,2})[:：](\d{2})/.exec(rest);
  if (colon) {
    hour = Number(colon[1]);
    minute = Number(colon[2]);
    rest = rest.replace(colon[0], " ");
  } else {
    // 阿拉伯数字和中文明码都收：「9 点」「九点半」
    const dotted = /([0-9一二两三四五六七八九十]{1,3})\s*点\s*(半|(\d{1,2})\s*分?)?/.exec(rest);
    if (dotted) {
      const h = cnToNum(dotted[1]);
      if (h !== null) {
        hour = h;
        minute = dotted[2] === "半" ? 30 : dotted[3] ? Number(dotted[3]) : 0;
        rest = rest.replace(dotted[0], " ");
      }
    }
  }

  if (hour !== null) {
    // 时段词只在没有 24 小时写法时修正小时
    const period = /(凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)/.exec(rest);
    if (period) {
      const word = period[1];
      if ((word === "下午" || word === "傍晚" || word === "晚上" || word === "夜里") && hour < 12) {
        hour += 12;
      }
      if (word === "中午" && hour < 11) hour += 12;
      rest = rest.replace(period[0], " ");
    }
    if (hour === 24) hour = 0;
    hour = Math.max(0, Math.min(23, hour));
    minute = Math.max(0, Math.min(59, minute));
  }

  // ⑥ 落地
  const base = day ?? now;
  if (hour !== null) {
    let ts = at(base, hour, minute);
    if (!day && ts <= Math.floor(now.getTime() / 1000)) {
      // 「9 点」在今天已经过了 → 指明天
      ts = at(addDays(now, 1), hour, minute);
    }
    if (wantsRemind) {
      out.remind_at = ts;
      out.kind = "remind";
    } else {
      out.due_at = ts;
      out.kind = "due";
    }
  } else if (day) {
    // 只给了日期：落在当天 23:59:59（截止），不设提醒
    out.due_at = endOfDay(day);
    out.kind = "due";
    out.date_only = true;
  }

  // ⑦ 剩下的就是标题
  const title = rest
    .replace(/\s+/g, " ")
    .replace(/^[\s,，。.;；:：、]+|[\s,，。.;；:：、]+$/g, "")
    .trim();
  out.title = title || String(text || "").trim();
  return out;
}
