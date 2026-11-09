"use client";

// P7 · 提醒中心（2026-11-09，规格见 docs/提醒中心_实施规格）
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
import { useCallback, useEffect, useMemo, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { Callout, SkeletonLines, Switch, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtHM, toMs } from "@/lib/time";
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
 * `next_fire_at`（秒）→ 列表里那一行的时刻文案；没有下一次就说清楚为什么。
 *
 * 「本周」分组必须带星期：只写 `22:00` 会让周四的提醒看起来像今晚就响。
 * 来源被关掉时直接说「来源已关」——时间还在，但到点不会发，写时间等于骗人。
 */
function whenText(
  item: ReminderItem,
  opts?: { withDay?: boolean; sourceOff?: boolean }
): string {
  if (!item.enabled) return "已暂停";
  if (opts?.sourceOff) return "来源已关";
  if (!item.next_fire_at) return item.freq === "once" ? "已触发过" : "等你设个时间";
  const hm = fmtHM(toMs(item.next_fire_at));
  if (!opts?.withDay) return hm;
  const date = new Date(toMs(item.next_fire_at));
  return `${WEEK_LABELS[(date.getDay() + 6) % 7]} ${hm}`;
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
  withDay,
  sourceOff,
}: {
  item: ReminderItem;
  sourceLabel: string;
  onEdit: (item: ReminderItem) => void;
  onToggle: (item: ReminderItem) => void;
  onDelete: (item: ReminderItem) => void;
  onCloseSource: (item: ReminderItem) => void;
  onSnooze: (item: ReminderItem) => void;
  onOpenSources: () => void;
  withDay: boolean;
  sourceOff: boolean;
}) {
  const [open, setOpen] = useState(false);
  const canCloseSource = item.source !== "manual" && item.source !== "system";

  return (
    <div
      className="card card-pad"
      style={{ opacity: item.enabled && !sourceOff ? 1 : 0.62 }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[var(--canvas-sunk)] px-2 py-0.5 text-[11px] text-[var(--ink-3)]">
              {sourceLabel}
            </span>
            <span className="text-[11px] text-[var(--ink-3)]">
              {ACTION_LABEL[item.action] || item.action}
            </span>
          </div>
          <p className="mt-1 truncate text-[15px] font-medium text-[var(--ink)]">{item.title}</p>
          {item.source_note ? (
            <p className="mt-0.5 line-clamp-1 text-[12px] text-[var(--ink-3)]">
              来源：{item.source_note}
            </p>
          ) : null}
        </div>
        <div className="shrink-0 text-right">
          <p className="text-[15px] font-medium tabular-nums text-[var(--ink)]">
            {whenText(item, { withDay, sourceOff })}
          </p>
          <p className="text-[11px] text-[var(--ink-3)]">
            {item.freq === "once"
              ? "只一次"
              : item.freq === "weekly" && item.weekday !== null
                ? // 「本周」分组的时间已经带了星期，再写一遍就成了「周三 08:00 / 周三」
                  withDay
                  ? "每周"
                  : WEEK_LABELS[item.weekday] || "每周"
                : item.freq === "weekdays"
                  ? "工作日"
                  : item.freq === "interval"
                    ? `每 ${item.every_minutes || 0} 分钟`
                    : "每天"}
          </p>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-3 text-[12px]">
        {sourceOff ? (
          // 来源关着时，点「暂停这条」只会把单条也关掉，动作和用户想要的相反。
          // 直接给一个去设置页的入口，比让他自己找更清楚。
          <button
            type="button"
            className="text-[var(--accent-ink)] hover:underline"
            onClick={onOpenSources}
          >
            去打开来源
          </button>
        ) : (
          <button
            type="button"
            className="text-[var(--accent-ink)] hover:underline"
            onClick={() => onToggle(item)}
          >
            {item.enabled ? "暂停这条" : "恢复这条"}
          </button>
        )}
        <button
          type="button"
          className="text-[var(--accent-ink)] hover:underline"
          onClick={() => onEdit(item)}
        >
          改时间/标题
        </button>
        <button
          type="button"
          className="ml-auto text-[var(--ink-3)] hover:text-[var(--ink)]"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          更多
        </button>
      </div>

      {open ? (
        <div className="mt-2 flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-2 text-[12px]">
          {item.enabled && !sourceOff ? (
            <button
              type="button"
              className="text-[var(--ink-3)] hover:text-[var(--ink)]"
              onClick={() => onSnooze(item)}
            >
              推迟 10 分钟
            </button>
          ) : null}
          {canCloseSource ? (
            <button
              type="button"
              className="text-[var(--ink-3)] hover:text-[var(--ink)]"
              onClick={() => onCloseSource(item)}
            >
              不再提醒「{sourceLabel}」
            </button>
          ) : (
            <span className="text-[var(--ink-3)]">这条是你自己建的，只能删掉</span>
          )}
          <button
            type="button"
            className="ml-auto text-[var(--danger)] hover:underline"
            onClick={() => onDelete(item)}
          >
            删除
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ==================== 抽屉（新建 / 修改） ====================

function ReminderDrawer({
  open,
  editing,
  sources,
  busy,
  onClose,
  onSubmit,
}: {
  open: boolean;
  editing: ReminderItem | null;
  sources: ReminderSource[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (draft: ReminderDraft, overrideAt: string | null) => void;
}) {
  // 初值直接从 `editing` 取，**不写 effect**：调用方用 `key` 控制重挂载，
  // 打开另一条时组件是新的实例，"同步 props 到 state"这件事根本不需要发生。
  // （写成 effect 会触发 react-hooks/set-state-in-effect：级联渲染。）
  const [title, setTitle] = useState(editing?.title ?? "");
  const [at, setAt] = useState(
    (editing?.override?.at || editing?.at || "20:00").slice(0, 5)
  );
  const [freq, setFreq] = useState<ReminderFreq>(
    (editing?.freq as ReminderFreq) || "daily"
  );
  const [weekday, setWeekday] = useState(editing?.weekday ?? 0);
  const [action, setAction] = useState<ReminderAction>(
    (editing?.action as ReminderAction) || "notify"
  );
  const [source, setSource] = useState(editing?.source ?? "manual");

  if (!open) return null;

  const canSubmit = title.trim().length > 0 && !busy;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 p-0 sm:items-center sm:p-4">
      <div
        className="card w-full max-w-md overflow-y-auto"
        style={{ maxHeight: "88dvh" }}
        role="dialog"
        aria-label={editing ? "修改提醒" : "新建提醒"}
      >
        <div className="card-head">
          <h3 className="card-title">{editing ? "修改这条提醒" : "新建提醒"}</h3>
          <button type="button" className="text-[var(--ink-3)]" onClick={onClose} aria-label="关闭">
            <CornIcon name="close" className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 p-4">
          <label className="block">
            <span className="text-[12px] text-[var(--ink-3)]">提醒什么</span>
            <input
              className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
              value={title}
              maxLength={60}
              placeholder="例如：复盘今天"
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[12px] text-[var(--ink-3)]">怎么重复</span>
              <select
                className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
                value={freq}
                onChange={(e) => setFreq(e.target.value as ReminderFreq)}
              >
                {FREQ_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-[12px] text-[var(--ink-3)]">几点</span>
              <input
                type="time"
                className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
                value={at}
                onChange={(e) => setAt(e.target.value)}
              />
            </label>
          </div>

          {freq === "weekly" ? (
            <label className="block">
              <span className="text-[12px] text-[var(--ink-3)]">周几</span>
              <select
                className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
                value={weekday}
                onChange={(e) => setWeekday(Number(e.target.value))}
              >
                {WEEK_LABELS.map((label, index) => (
                  <option key={label} value={index}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <label className="block">
            <span className="text-[12px] text-[var(--ink-3)]">到点之后</span>
            <select
              className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
              value={action}
              onChange={(e) => setAction(e.target.value as ReminderAction)}
            >
              {ACTION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>

          {/*
           * 「怎么送达」目前只有 Web Push 一条路：`service/reminder_tick.py` 只认
           * channels 里的 push，写别的（inapp / telegram）会被拦下并记进流水。
           * 所以这里**不放选择器** —— 一个能点、点了却不生效的选项，
           * 比根本没有这个选项更让人以为"设置过了"。等第二种通道真上线再换成 select。
           */}
          <div className="rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] text-[var(--ink-3)]">怎么送达</span>
              <span className="text-[13px] text-[var(--ink)]">推送通知</span>
            </div>
            <p className="mt-1 text-[12px] text-[var(--ink-3)]">
              目前只支持这一种：到点出现在设备通知栏，前提是「设置」页里的推送是开着的。
            </p>
          </div>

          {editing ? null : (
            <label className="block">
              <span className="text-[12px] text-[var(--ink-3)]">算哪个来源</span>
              <select
                className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
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
            </label>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              className="rounded-lg px-3 py-2 text-[13px] text-[var(--ink-3)]"
              onClick={onClose}
            >
              取消
            </button>
            <button
              type="button"
              disabled={!canSubmit}
              className="rounded-lg bg-[var(--ink)] px-4 py-2 text-[13px] text-[var(--paper)] disabled:opacity-50"
              onClick={() =>
                onSubmit(
                  {
                    title: title.trim(),
                    source,
                    freq,
                    at,
                    weekday: freq === "weekly" ? weekday : null,
                    action,
                    channels: "push",
                  },
                  freq === "weekly" ? null : at
                )
              }
            >
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
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
  const { toasts, showToast } = useToast();

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
  //  - 组件里调 `Date.now()` 是渲染期的不纯调用（react-hooks/purity），
  //    会让同一份数据在不同时刻渲染出不同结果，测试也没法固定。
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
    async (draft: ReminderDraft, overrideAt: string | null) => {
      setBusy(true);
      try {
        if (editing) {
          // 改时间要落 override 还是 at：来源提醒（课表/委托）不该被写回来源，
          // 但手工提醒没有来源，直接改自己的 at 更直白。
          const isOwn = editing.source === "manual" || editing.source === "system";
          const body = isOwn
            ? { title: draft.title, at: overrideAt }
            : {
                title: draft.title,
                override: overrideAt
                  ? { title: draft.title, at: overrideAt }
                  : { title: draft.title },
              };
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

  const renderGroup = (
    title: string,
    list: ReminderItem[],
    hint?: string,
    withDay = false
  ) => (
    <section className="mt-4">
      <div className="mb-2 flex items-baseline gap-2">
        <h3 className="text-[14px] font-medium text-[var(--ink)]">{title}</h3>
        <span className="text-[12px] text-[var(--ink-3)]">{list.length} 条</span>
        {hint ? <span className="text-[12px] text-[var(--ink-3)]">· {hint}</span> : null}
      </div>
      {list.length === 0 ? (
        <p className="rounded-lg border border-dashed border-[var(--line)] px-3 py-4 text-[13px] text-[var(--ink-3)]">
          这里还空着
        </p>
      ) : (
        <div className="space-y-2">
          {list.map((item) => (
            <ReminderCard
              key={item.id}
              item={item}
              sourceLabel={sourceLabel[item.source] || item.source}
              onEdit={(it) => {
                setEditing(it);
                setDrawer(true);
              }}
              onToggle={toggle}
              onDelete={remove}
              onCloseSource={closeSource}
              onSnooze={snooze}
              onOpenSources={() => setTab("settings")}
              withDay={withDay}
              sourceOff={sourceOn[item.source] === false}
            />
          ))}
        </div>
      )}
    </section>
  );

  return (
    <PageShell
      eyebrow="P7 · 提醒"
      title="提醒"
      sub="什么时候 · 说什么 · 做什么。到点没内容就不打扰你。"
      actions={
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-[var(--line)] p-0.5">
            <button
              type="button"
              className={`rounded-md px-3 py-1 text-[12px] ${
                tab === "list" ? "bg-[var(--ink)] text-[var(--paper)]" : "text-[var(--ink-3)]"
              }`}
              onClick={() => setTab("list")}
            >
              提醒
            </button>
            <button
              type="button"
              className={`rounded-md px-3 py-1 text-[12px] ${
                tab === "settings" ? "bg-[var(--ink)] text-[var(--paper)]" : "text-[var(--ink-3)]"
              }`}
              onClick={() => setTab("settings")}
            >
              设置
            </button>
          </div>
          {tab === "list" ? (
            <button
              type="button"
              className="btn-new"
              onClick={() => {
                setEditing(null);
                setDrawer(true);
              }}
            >
              + 新建
            </button>
          ) : null}
        </div>
      }
    >
      {loading ? <SkeletonLines widths={["70%", "90%", "55%", "80%"]} /> : null}

      {!loading && tab === "list" ? (
        <>
          {/* 今天的时间轴：只显示今天会响的那几条，让人一眼看到"今天还有什么" */}
          <section className="card card-pad">
            <div className="flex items-baseline justify-between">
              <h3 className="card-title">今天</h3>
              <span className="text-[12px] text-[var(--ink-3)]">
                {summary ? `${summary.today_count} 条` : "—"}
              </span>
            </div>
            {groups.today.length === 0 ? (
              <p className="mt-2 text-[13px] text-[var(--ink-3)]">今天没有要到点的提醒。</p>
            ) : (
              <ul className="mt-2 flex flex-wrap gap-2">
                {groups.today.map((item) => (
                  <li
                    key={item.id}
                    className="flex items-center gap-2 rounded-full bg-[var(--canvas-sunk)] px-3 py-1 text-[12px]"
                  >
                    <span className="font-medium tabular-nums text-[var(--ink)]">
                      {whenText(item, { sourceOff: sourceOn[item.source] === false })}
                    </span>
                    <span className="text-[var(--ink-3)]">{item.title}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {items.length === 0 ? (
            <div className="mt-4">
              <Callout icon="bulb">
                <b>还没有提醒</b>
                <br />
                点右上角「+ 新建」定一条，比如每天 22:00「复盘今天」。
                <br />
                到点时会按你设的方式提醒；没有内容可说的日子不会响。
              </Callout>
            </div>
          ) : null}

          {renderGroup("今天", groups.today, "今天会响的")}
          {renderGroup("本周", groups.week, "之后要响的", true)}
          {renderGroup("已暂停", groups.paused, "不会响，点恢复就继续")}
        </>
      ) : null}

      {!loading && tab === "settings" ? (
        <div className="space-y-4">
          <section className="card card-pad">
            <h3 className="card-title">静默时段</h3>
            <p className="mt-1 text-[12px] text-[var(--ink-3)]">
              这段时间里到点的提醒<b>不响也不补发</b>，直接顺到下一个时段。
            </p>
            <div className="mt-3 flex items-center gap-2">
              <input
                type="time"
                className="rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
                value={prefs?.quiet_start || "23:00"}
                onChange={(e) => setPrefs((p) => (p ? { ...p, quiet_start: e.target.value } : p))}
                onBlur={(e) => void savePrefs({ quiet_start: e.target.value })}
              />
              <span className="text-[var(--ink-3)]">到</span>
              <input
                type="time"
                className="rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
                value={prefs?.quiet_end || "07:00"}
                onChange={(e) => setPrefs((p) => (p ? { ...p, quiet_end: e.target.value } : p))}
                onBlur={(e) => void savePrefs({ quiet_end: e.target.value })}
              />
            </div>
          </section>

          <section className="card card-pad">
            <h3 className="card-title">每天最多几条</h3>
            <p className="mt-1 text-[12px] text-[var(--ink-3)]">
              同一分钟的多条会合并成一条通知，合并算一条。上限只挡「通知太多」，
              不挡你建多少条提醒。
            </p>
            <div className="mt-3 flex items-center gap-2">
              <input
                type="number"
                min={prefs?.cap_min ?? 1}
                max={prefs?.cap_max ?? 20}
                className="w-24 rounded-lg border border-[var(--line)] bg-[var(--canvas)] px-3 py-2"
                value={prefs?.daily_cap ?? 6}
                onChange={(e) =>
                  setPrefs((p) => (p ? { ...p, daily_cap: Number(e.target.value) } : p))
                }
                onBlur={(e) => void savePrefs({ daily_cap: Number(e.target.value) })}
              />
              <span className="text-[12px] text-[var(--ink-3)]">
                条（{prefs?.cap_min ?? 1}–{prefs?.cap_max ?? 20}）
              </span>
            </div>
          </section>

          <section className="card card-pad">
            <h3 className="card-title">来源</h3>
            <p className="mt-1 text-[12px] text-[var(--ink-3)]">
              关掉一个来源，它就不再给你发提醒（已经建好的那几条会一起停）。
            </p>
            <div className="mt-3 space-y-2">
              {sources.map((s) => (
                <div key={s.key} className="flex items-center justify-between gap-3">
                  <span className="text-[13px] text-[var(--ink)]">{s.label}</span>
                  <Switch
                    checked={s.enabled}
                    label={s.label}
                    onChange={(next) => void toggleSource(s.key, next)}
                  />
                </div>
              ))}
            </div>
            <p className="mt-3 text-[12px] text-[var(--ink-3)]">
              推送设备在「设置」页管，不在这里 —— 那是整个账号的推送通道，
              以后对话回复也可能用它。
            </p>
          </section>

          {prefs ? (
            <p className="text-[12px] text-[var(--ink-3)]">
              静默 {prefs.quiet_start}–{prefs.quiet_end} · 每天最多 {prefs.daily_cap} 条
            </p>
          ) : null}
        </div>
      ) : null}

      <ReminderDrawer
        key={editing ? `edit-${editing.id}` : "new"}
        open={drawer}
        editing={editing}
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
