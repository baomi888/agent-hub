"use client";

// P7 · 提醒中心（2026-11-09 建，2026-11-10 重排界面）
//
// 这个页面只回答一个问题：**什么时候，让我做什么**。
// 三件事刻意不放在这里：
//  1. 推送设备（Web Push 订阅）留在设置页 —— 那是账号级基础设施，
//     以后对话回复也可能借它推，搬进提醒页等于让一个模块独占全站能力；
//  2. 每日委托的正文留在 /delegate —— 提醒只管"什么时候提醒你去看"，不管"写什么"；
//  3. 番茄钟、课表等工具的编辑留在工具集 —— 这里只提供「关闭该来源」这一个开关。
//
// 为什么卡片上的时间取 `next_fire_at` 而不是 `at`：
// `at` 是**来源值**（课表第 3 节、委托原定 20:00），`override.at` 才是用户改过的。
// 直接显示 `at` 会出现「我明明改成 22:30，列表还写 20:00」——用户会当成没保存。
// 服务端 `resolve()` 已经把 override 合并进 `at`，但"下一次到底几点响"只有
// `next_fire_at` 知道（还要考虑静默时段、每日上限、once 已触发等），所以显示它。
//
// 2026-11-10 界面重排的三条判据（都是量出来的，不是审美）：
//  1. 卡片 93px → 68px：一屏从 8 条到 12 条；每张卡上的可点从 4 个降到 2 个
//     （标题＝改这条，⋯＝其余四个动作）。手机首屏原来只露 1.5 张。
//  2. 时间轴做成真的：按分钟定位、当前时刻一条竖线、节点点了跳到卡片。
//     原来只是把 6 条横排一行，没有轴也没有"现在"，而且和下面的列表重复念两遍。
//  3. 抽屉按规格「来源 → 触发 → 动作 → 送达」重排，底部一行实时预览。
//     原来顺序是「标题 → 重复 → 几点 → 动作 → 通道 → 来源」，和文档反着。
import { useCallback, useEffect, useMemo, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { Callout, Field, Seg, SkeletonLines, Switch, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtHM, fmtRemaining, toMs } from "@/lib/time";
import type {
  ReminderAction,
  ReminderDraft,
  ReminderFreq,
  ReminderItem,
  ReminderPrefs,
  ReminderSource,
  ReminderSummary,
} from "@/lib/types";

const ACTION_LABEL: Record<string, string> = {
  notify: "仅提醒",
  agent_deliver: "让 Agent 帮我写",
  suggest_focus: "开始专注",
  open_tool: "打开工具",
};

const FREQ_OPTIONS: { value: ReminderFreq; label: string }[] = [
  { value: "daily", label: "每天" },
  { value: "weekdays", label: "工作日" },
  { value: "weekly", label: "每周" },
  { value: "once", label: "只一次" },
];

const ACTION_OPTIONS: { value: ReminderAction; label: string }[] = [
  { value: "notify", label: "仅提醒" },
  { value: "agent_deliver", label: "让 Agent 帮我写" },
  { value: "suggest_focus", label: "开始专注" },
];

const WEEK_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/**
 * 卡片左上角那个时刻；没有下一次就说清楚为什么。
 *
 * 「本周」分组**不再**把星期塞进这一行（原来写 `周三 08:00`）：150px 的列里
 * 那串字会被卡片挤成两行，而且每周三的提醒本来应该一直说「每周三」，
 * 不是到了本周才提一次。星期改由下面的频率行承担（`每周三`）。
 */
function whenMain(item: ReminderItem): string {
  if (!item.enabled) return "已暂停";
  if (!item.next_fire_at) return item.freq === "once" ? "已触发" : "没定时间";
  return fmtHM(toMs(item.next_fire_at));
}

/** 时刻下面那行频率：它同时承担"这条会不会重复"和"是周几" */
function freqText(item: ReminderItem): string {
  if (item.freq === "weekly" && item.weekday !== null) {
    return `每周${(WEEK_LABELS[item.weekday] ?? "每周").slice(1)}`;
  }
  if (item.freq === "weekdays") return "工作日";
  if (item.freq === "interval") return `每 ${item.every_minutes || 0} 分钟`;
  if (item.freq === "once") return "只一次";
  return "每天";
}

/**
 * 每分钟走一次的「现在」。
 *
 * 首帧刻意给 `null`：在 useState 的初值里直接取当前时间，会让服务端渲染与
 * 首次客户端渲染拿到两个不同的时间，hydration 直接对不上；在渲染期取当前时间
 * 还会被 `react-hooks/purity` 判红（同一个组件两次渲染结果不同）。
 * 拿不到就先把那根竖线藏起来，一秒后它自己出现。
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

type AxisEvent = {
  /** 点这枚齿孔跳到哪张卡（同一分钟的多条合并成一枚，取第一条） */
  id: string;
  min: number;
  label: string;
  /** 这一枚上叠了几条，以及它们的标题，给悬停提示和读屏用 */
  count: number;
  title: string;
};

/**
 * 一天的进度带。
 *
 * 上一版是一条 1px 细线 + 几个 7px 空心圆，970px 宽的卡里 66px 高只画了 7 个点，
 * 剩下全是空白；而且「今天已经过去多少」「夜里这一段到点不响」这两件事根本看不出来。
 *
 * 这一版把轴做成一条有厚度的带子，从下往上叠四层，每层都是真信息：
 *  1. 带子底：暖纸色凹槽 + 每小时一道刻度 —— 空白变成网格，而不是留白；
 *  2. 已过染色：0 点到此刻的一段暖金，越靠近现在越浓，右端正好被游标压住；
 *  3. 静默斜纹：prefs 里的静默时段画成斜纹段，「这一段不响」一眼可见；
 *  4. 齿孔：每条提醒一枚，纸色实心 + 墨描边，骑在带子上。
 *
 * 时间标签不常驻：970px 里 40px 一个刻度，`12:30` 这类标签排开会连成 `10:2712:30`。
 * 悬停/聚焦齿孔才浮出「12:30 · 午休」，具体几点平时看卡片左侧。
 * 手机上只留齿孔和 0/12/24 三个数字 —— 390 宽里 1 小时只值 16px。
 */
function TodayAxis({
  items,
  now,
  quietStart,
  quietEnd,
  onJump,
}: {
  items: ReminderItem[];
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

  const events = useMemo<AxisEvent[]>(() => {
    // 同一分钟的多条并成一枚：服务端本来就会把它们合并成一条通知，
    // 轴上并排画两枚只会互相压住，点起来也分不清。
    const buckets = new Map<number, { at: Date; ids: string[]; titles: string[] }>();
    for (const item of items) {
      if (!item.next_fire_at) continue;
      const at = new Date(toMs(item.next_fire_at));
      const min = at.getHours() * 60 + at.getMinutes();
      const slot = buckets.get(min);
      if (slot) {
        slot.ids.push(item.id);
        slot.titles.push(item.title);
      } else {
        buckets.set(min, { at, ids: [item.id], titles: [item.title] });
      }
    }
    return [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([min, slot]) => ({
        id: slot.ids[0],
        min,
        label: fmtHM(slot.at),
        count: slot.ids.length,
        title: slot.titles.join("、"),
      }));
  }, [items]);

  const quiet = useMemo(() => quietBands(quietStart, quietEnd), [quietStart, quietEnd]);

  return (
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
            style={{ left: `${(h / (DAY_MIN / TICK_MIN)) * 100}%` }}
          />
        ))}
      </div>

      {events.map((e) => (
        <button
          key={`e${e.min}`}
          type="button"
          className="rem-ev"
          // 0 点和 24 点的齿孔要往回收一点，否则一半在带子外面
          style={{ left: `${Math.min(99.6, Math.max(0.4, (e.min / DAY_MIN) * 100))}%` }}
          onClick={() => onJump(e.id)}
          aria-label={`${e.label} ${e.title}${e.count > 1 ? ` 等 ${e.count} 条` : ""}，跳到这条`}
        >
          <i aria-hidden="true" />
          <b aria-hidden="true">
            <em>{e.label}</em>
            <span className="rem-ev-title">{e.title}</span>
            {e.count > 1 ? <span className="rem-ev-more">+{e.count - 1}</span> : null}
          </b>
        </button>
      ))}

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
  );
}

// ==================== 卡片 ====================

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
      className="rem-card"
      id={`rem-card-${item.id}`}
      data-off={dim ? "1" : undefined}
      data-flash={flash ? "1" : undefined}
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
          {sourceOff ? <span> · 来源已关</span> : null}
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
                去打开「{sourceLabel}」来源
              </button>
            ) : (
              <button type="button" role="menuitem" onClick={run(() => onToggle(item))}>
                {item.enabled ? "暂停这条" : "恢复这条"}
              </button>
            )}
            <hr />
            {canCloseSource ? (
              <button type="button" role="menuitem" onClick={run(() => onCloseSource(item))}>
                不再提醒「{sourceLabel}」
              </button>
            ) : (
              <p>这条是你自己建的，只能删掉</p>
            )}
            <button type="button" role="menuitem" data-danger="1" onClick={run(() => onDelete(item))}>
              删除
            </button>
          </div>
        </>
      ) : null}
    </article>
  );
}

// ==================== 抽屉（新建 / 修改） ====================

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
  // 初值直接从 `editing` 取，**不写 effect**：调用方用 `key` 控制重挂载，
  // 打开另一条时组件是新的实例，"同步 props 到 state"这件事根本不需要发生。
  // （写成 effect 会触发 react-hooks/set-state-in-effect：级联渲染。）
  const [title, setTitle] = useState(editing?.title ?? "");
  const [at, setAt] = useState((editing?.override?.at || editing?.at || "20:00").slice(0, 5));
  const [freq, setFreq] = useState<ReminderFreq>((editing?.freq as ReminderFreq) || "daily");
  const [weekday, setWeekday] = useState(editing?.weekday ?? 0);
  const [action, setAction] = useState<ReminderAction>(
    (editing?.action as ReminderAction) || "notify"
  );
  const [source, setSource] = useState(editing?.source ?? "manual");

  if (!open) return null;

  const canSubmit = title.trim().length > 0 && !busy;
  // 来源提醒的频率由来源决定（课表每周三就是每周三），界面上不给改。
  // 自己定的 / 每日回顾可以改，且改了要真的提交 —— 否则就是一个能点不生效的控件。
  const isOwn = !editing || editing.source === "manual" || editing.source === "system";

  const pickSource = editing ? sourceName : (sources.find((s) => s.key === source)?.label ?? "自己定的");
  const freqLine =
    freq === "weekly"
      ? `每周${(WEEK_LABELS[weekday] ?? "周一").slice(1)} ${at}`
      : freq === "once"
        ? `一次 · ${at}`
        : `${FREQ_OPTIONS.find((o) => o.value === freq)?.label ?? "每天"} ${at}`;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 sm:items-center sm:p-4">
      <div
        className="card rem-drawer-card w-full max-w-md overflow-y-auto"
        style={{ maxHeight: "88dvh" }}
        role="dialog"
        aria-label={editing ? "修改提醒" : "新建提醒"}
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

        <div className="space-y-4 px-4 pb-4">
          {/* ① 说什么 */}
          <Field label="提醒什么">
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
              <span className="f">这是关于什么的</span>
              <p className="rem-ro">
                来自「{sourceName}」。来源建好之后不能换，要换就删掉重立一条。
              </p>
            </div>
          ) : (
            <Field label="这是关于什么的" hint="关掉某个来源时，这个来源下的提醒会一起停">
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
              什么时候提醒你 <i>到点才响；那天没内容就不响</i>
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
                  跟着「{sourceName}」走：{editing ? freqText(editing) : ""}
                </p>
                <div className="mt-2">
                  <Field label="改到几点" hint="只改这条，不动来源里的时间">
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
          <Field label="到点之后">
            <select
              className="input"
              value={action}
              onChange={(e) => setAction(e.target.value as ReminderAction)}
            >
              {ACTION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>

          {/*
           * 「怎么送达」：`service/reminder_tick.py` 只认 channels 里的 push，
           * 写别的（inapp / telegram）会被拦下并记进流水。所以这里**不放选择器** ——
           * 一个能点、点了却不生效的选项，比没有这个选项更让人以为"设置过了"。
           * 也不用输入框的外形，否则看着还是像能选。
           */}
          <div className="rem-deliver">
            <b>怎么送达 · 推送通知</b>
            <span>
              目前只支持这一种：到点出现在设备通知栏，前提是「设置」页里的推送是开着的。
            </span>
          </div>
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

// ==================== 页面 ====================

export default function RemindersPage() {
  const { me } = useAuth();
  const authedId = me?.user?.id ?? null;

  const [tab, setTab] = useState<"list" | "settings">("list");
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<ReminderItem[]>([]);
  const [summary, setSummary] = useState<ReminderSummary | null>(null);
  const [sources, setSources] = useState<ReminderSource[]>([]);
  const [prefs, setPrefs] = useState<ReminderPrefs | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [editing, setEditing] = useState<ReminderItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const { toasts, showToast } = useToast();
  // 「现在」只在这一层取一次，时间轴与卡片都读它 ——
  // 两处各起一个定时器会各走各的，遇到跨零界点还会互相差一分钟。
  const now = useNowMinute();

  // 取数拆成「拉」和「落地」两半：effect 里必须先把 await 走完再 setState，
  // 否则会被 react-hooks/set-state-in-effect 判成级联渲染（同页其他地方也一样）。
  const fetchAll = useCallback(async () => {
    const [list, sum, src, pf] = await Promise.all([
      api.reminders.list("all"),
      api.reminders.summary(),
      api.reminders.sources(),
      api.reminders.prefs(),
    ]);
    return { list, sum, src, pf };
  }, []);

  const apply = useCallback((data: Awaited<ReturnType<typeof fetchAll>>) => {
    setItems(data.list?.items ?? []);
    setSummary(data.sum);
    setSources(data.src?.sources ?? []);
    setPrefs(data.pf);
  }, []);

  const reload = useCallback(async () => {
    apply(await fetchAll());
  }, [apply, fetchAll]);

  useEffect(() => {
    if (!authedId) return;
    let alive = true;
    void (async () => {
      try {
        const data = await fetchAll();
        if (!alive) return;
        apply(data);
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
  }, [authedId, apply, fetchAll, showToast]);

  // 「今天」由服务端 summary 判定，前端不自己算日期：
  //  - 服务端用的是他自己的时区和"今天"边界，前端再算一遍必然在跨零点时打架；
  //  - 组件里调 `Date.now()` 是渲染期的不纯调用（react-hooks/purity）。
  const groups = useMemo(() => {
    const todayIds = new Set((summary?.today ?? []).map((i) => i.id));
    const active = items.filter((i) => i.enabled);
    return {
      today: active.filter((i) => todayIds.has(i.id)),
      week: active.filter((i) => !todayIds.has(i.id)),
      paused: items.filter((i) => !i.enabled),
    };
  }, [items, summary]);

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

  const submit = useCallback(
    async (draft: ReminderDraft) => {
      setBusy(true);
      try {
        if (editing) {
          // 改时间落 override 还是 at：来源提醒（课表/委托/DDL）不该被写回来源，
          // 手工提醒没有来源，直接改自己的 at 更直白。
          //
          // ⚠️ 这里以前对 weekly 传的是空值，后端 `_validate` 会直接 400
          //    （「时间要写成 HH:MM」）—— 表现为「每周」提醒连改标题都保存不了。
          //    现在一律传真实时刻；频率只在「自己定的 / 每日回顾」上提交，
          //    来源提醒的频率跟着来源走，传上去反而会把来源的规则改掉。
          const isOwn = editing.source === "manual" || editing.source === "system";
          // 时刻一定要是字符串：`time` 输入框永远有值（默认 20:00），
          // 而 `override.at` 的类型不接受 null —— 以前 weekly 传 null 正是这里出的错。
          const at = (draft.at ?? editing.override?.at ?? editing.at ?? "20:00").slice(0, 5);
          const body = isOwn
            ? {
                title: draft.title,
                at,
                freq: draft.freq,
                weekday: draft.weekday,
              }
            : { title: draft.title, override: { title: draft.title, at } };
          await api.reminders.patch(editing.id, body);
          showToast("success", "已更新");
        } else {
          await api.reminders.create(draft);
          showToast("success", "已建好，到点会提醒你");
        }
        setDrawer(false);
        setEditing(null);
        await reload();
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "保存失败");
      } finally {
        setBusy(false);
      }
    },
    [editing, reload, showToast]
  );

  const toggle = useCallback(
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

  const remove = useCallback(
    async (item: ReminderItem) => {
      try {
        await api.reminders.remove(item.id);
        showToast("success", "已删除");
        await reload();
      } catch {
        showToast("error", "删不掉，稍后再试");
      }
    },
    [reload, showToast]
  );

  const snooze = useCallback(
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
        setSources(res.sources);
        showToast("success", `已关掉「${sourceLabel[item.source] || item.source}」`);
        await reload();
      } catch {
        showToast("error", "关不掉，稍后再试");
      }
    },
    [reload, showToast, sourceLabel]
  );

  const savePrefs = useCallback(
    async (patch: Partial<Pick<ReminderPrefs, "quiet_start" | "quiet_end" | "daily_cap">>) => {
      try {
        const next = await api.reminders.savePrefs(patch);
        setPrefs(next);
        showToast("success", "已保存");
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "保存失败");
      }
    },
    [showToast]
  );

  const toggleSource = useCallback(
    async (key: string, next: boolean) => {
      try {
        const res = await api.reminders.setSource(key, next);
        setSources(res.sources);
      } catch {
        showToast("error", "开关没生效，稍后再试");
      }
    },
    [showToast]
  );

  /** 时间轴节点 → 滚到那张卡片并闪一下，否则滚过去也不知道该看哪条 */
  const jump = useCallback((id: string) => {
    setFlash(id);
    const el = document.getElementById(`rem-card-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => setFlash((cur) => (cur === id ? null : cur)), 1600);
  }, []);

  /** 今天还没到点的条数：回答「我还能歇多久」，比再写一遍「6 条」有用 */
  const upcomingToday = useMemo(() => {
    if (now === null) return null;
    const d = new Date(now);
    const minute = d.getHours() * 60 + d.getMinutes();
    return groups.today.filter((i) => {
      if (!i.next_fire_at) return false;
      const t = new Date(toMs(i.next_fire_at));
      return t.getHours() * 60 + t.getMinutes() > minute;
    }).length;
  }, [groups.today, now]);

  const openDrawer = useCallback((item: ReminderItem | null) => {
    setEditing(item);
    setDrawer(true);
  }, []);

  const card = (item: ReminderItem) => (
    <ReminderCard
      key={item.id}
      item={item}
      sourceLabel={sourceLabel[item.source] || item.source}
      onEdit={openDrawer}
      onToggle={toggle}
      onDelete={remove}
      onCloseSource={closeSource}
      onSnooze={snooze}
      onOpenSources={() => setTab("settings")}
      sourceOff={sourceOn[item.source] === false}
      flash={flash === item.id}
      nowNumber={now}
    />
  );

  // 空分组整段不渲染：原来三个分组都画标题，新账号首屏读到的是
  // 「今天 0 条 / 本周 0 条 / 已暂停 0 条」—— 三个零不提供任何信息。
  const renderGroup = (title: string, list: ReminderItem[]) => {
    if (list.length === 0) return null;
    return (
      <section className="mt-4">
        <h3 className="mb-2 text-[var(--fs-sm)] font-medium text-[var(--text-2)]">
          {title} <span className="font-normal text-[var(--text-3)]">· {list.length} 条</span>
        </h3>
        <div className="space-y-2">{list.map(card)}</div>
      </section>
    );
  };

  return (
    <PageShell
      compact
      eyebrow="P7 · 提醒"
      title="提醒"
      sub="什么时候 · 说什么 · 做什么。到点没内容就不打扰你。"
      actions={
        <div className="flex items-center gap-2">
          <div className="seg" role="group" aria-label="视图">
            <button
              type="button"
              aria-pressed={tab === "list"}
              onClick={() => setTab("list")}
            >
              提醒
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
            <button type="button" className="btn-new" onClick={() => openDrawer(null)}>
              + 新建
            </button>
          ) : null}
        </div>
      }
    >
      {loading ? <SkeletonLines widths={["70%", "90%", "55%", "80%"]} /> : null}

      {!loading && tab === "list" ? (
        <>
          {items.length === 0 ? (
            <Callout icon="bulb">
              <b>还没有提醒</b>
              <br />
              点右上角「+ 新建」定一条，比如每天 22:00「复盘今天」。
              <br />
              到点时会按你设的方式提醒；没有内容可说的日子不会响。
            </Callout>
          ) : (
            <>
              {/* 今天这一组：标题 → 24 小时轴 → 卡片。
                  「今天」只出现一次（原来轴卡片和分组标题各写一遍）。
                  轴只为回答「忙在哪个时段、现在走到哪、还剩几条」，
                  卡片里已经有完整时间，所以轴上不重复标题。 */}
              <section className="mt-4">
                <div className="mb-2 flex items-baseline justify-between gap-2">
                  <h3 className="text-[var(--fs-sm)] font-medium text-[var(--text-2)]">
                    今天{" "}
                    <span className="font-normal text-[var(--text-3)]">
                      · {groups.today.length} 条
                    </span>
                  </h3>
                  <span className="text-[var(--fs-xs)] text-[var(--text-3)]">
                    {groups.today.length === 0
                      ? "今天没有要到点的提醒"
                      : upcomingToday === null
                        ? "按时间排"
                        : upcomingToday === 0
                          ? "今天的都过完了"
                          : `之后还有 ${upcomingToday} 条`}
                  </span>
                </div>
                {groups.today.length > 0 ? (
                  <>
                    <div className="card card-pad">
                      <TodayAxis
                        items={groups.today}
                        now={now}
                        quietStart={prefs?.quiet_start}
                        quietEnd={prefs?.quiet_end}
                        onJump={jump}
                      />
                    </div>
                    <div className="mt-2 space-y-2">{groups.today.map(card)}</div>
                  </>
                ) : null}
              </section>

              {renderGroup("本周", groups.week)}
              {renderGroup("已暂停", groups.paused)}
            </>
          )}
        </>
      ) : null}

      {!loading && tab === "settings" ? (
        <div className="space-y-4">
          <section className="card card-pad">
            <h3 className="card-title">静默时段</h3>
            <p className="mt-1 text-[var(--fs-xs)] text-[var(--text-3)]">
              这段时间里到点的提醒<b>不响也不补发</b>，直接顺到下一个时段。
            </p>
            <div className="mt-3 flex items-center gap-2">
              <input
                type="time"
                className="input !w-auto"
                value={prefs?.quiet_start || "23:00"}
                onChange={(e) => setPrefs((p) => (p ? { ...p, quiet_start: e.target.value } : p))}
                onBlur={(e) => void savePrefs({ quiet_start: e.target.value })}
              />
              <span className="text-[var(--fs-xs)] text-[var(--text-3)]">到</span>
              <input
                type="time"
                className="input !w-auto"
                value={prefs?.quiet_end || "07:00"}
                onChange={(e) => setPrefs((p) => (p ? { ...p, quiet_end: e.target.value } : p))}
                onBlur={(e) => void savePrefs({ quiet_end: e.target.value })}
              />
            </div>
          </section>

          <section className="card card-pad">
            <h3 className="card-title">每天最多几条</h3>
            <p className="mt-1 text-[var(--fs-xs)] text-[var(--text-3)]">
              同一分钟的多条会合并成一条通知，合并算一条。上限只挡「通知太多」，
              不挡你建多少条提醒。
            </p>
            <div className="mt-3 flex items-center gap-2">
              <input
                type="number"
                min={prefs?.cap_min ?? 1}
                max={prefs?.cap_max ?? 20}
                className="input !w-24"
                value={prefs?.daily_cap ?? 6}
                onChange={(e) =>
                  setPrefs((p) => (p ? { ...p, daily_cap: Number(e.target.value) } : p))
                }
                onBlur={(e) => void savePrefs({ daily_cap: Number(e.target.value) })}
              />
              <span className="text-[var(--fs-xs)] text-[var(--text-3)]">
                条（{prefs?.cap_min ?? 1}–{prefs?.cap_max ?? 20}）
              </span>
            </div>
          </section>

          <section className="card card-pad">
            <h3 className="card-title">来源</h3>
            <p className="mt-1 text-[var(--fs-xs)] text-[var(--text-3)]">
              关掉一个来源，它就不再给你发提醒（已经建好的那几条会一起停）。
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
            <p className="mt-3 text-[var(--fs-xs)] text-[var(--text-3)]">
              推送设备在「设置」页管，不在这里 —— 那是整个账号的推送通道，
              以后对话回复也可能用它。
            </p>
          </section>

          {prefs ? (
            <p className="text-[var(--fs-xs)] text-[var(--text-3)]">
              静默 {prefs.quiet_start}–{prefs.quiet_end} · 每天最多 {prefs.daily_cap} 条
            </p>
          ) : null}
        </div>
      ) : null}

      <ReminderDrawer
        key={editing ? `edit-${editing.id}` : "new"}
        open={drawer}
        editing={editing}
        sourceName={editing ? sourceLabel[editing.source] || editing.source : ""}
        sources={sources.filter((s) => s.enabled)}
        busy={busy}
        onClose={() => {
          setDrawer(false);
          setEditing(null);
        }}
        onSubmit={submit}
      />

      <ToastHost toasts={toasts} />
    </PageShell>
  );
}
