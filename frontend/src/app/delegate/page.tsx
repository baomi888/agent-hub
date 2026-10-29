"use client";

// P4 · 每日委托
//
// 页面任务：让「知穗主动来找你」这件事**可配置、可预期、可追溯**。
//   · 可配置 —— 右侧表单常驻（不弹窗、不遮挡列表），左宽右窄
//   · 可预期 —— 每条都写清「什么时候触发」和「下次什么时候」
//   · 可追溯 —— 执行历史按 trigger 区分定时 / 手动，排查「今天为什么没收到」看这一列
//
// 关键设计点：**「预览」不投递**。
// 后端给的是 `POST /{id}/run?dry_run=true`（只生成、不改 next_run_at），
// 这正好是产品方向文档要的「系统先跑一遍，人再确认」—— 先看到内容，再决定投不投。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConsoleShell from "@/components/console/ConsoleShell";
import CornIcon from "@/components/CornIcon";
import { Callout, Field, NotReady, Seg, SkeletonLines, Switch, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { DEMO_DELEGATES, DEMO_RUNS } from "@/lib/consoleDemo";
import { errProbe, toProbe, type ProbeState } from "@/lib/probe";
import { fmtDuration, fmtNum, fmtRemaining, fmtWhen } from "@/lib/time";
import { useToast } from "@/lib/hooks/useToast";
import type {
  Conversation,
  Delegate,
  DelegateFreq,
  DelegateKind,
  DelegateRun,
  DelegateRunStatus,
  KbInfo,
} from "@/lib/types";

/** 启用中的委托上限。写在页面上，别让用户撞到 400 才知道 */
const MAX_ACTIVE = 5;
const TZ = "Asia/Shanghai";

const KIND_OPTIONS: { value: DelegateKind; label: string }[] = [
  { value: "study_plan", label: "学习计划" },
  { value: "review", label: "复盘" },
  { value: "encourage", label: "鼓励" },
  { value: "custom", label: "自定义" },
];
const KIND_LABEL: Record<DelegateKind, string> = {
  study_plan: "学习计划",
  review: "复盘",
  encourage: "鼓励",
  custom: "自定义",
};

const FREQ_OPTIONS: { value: DelegateFreq; label: string; hint: string }[] = [
  { value: "daily", label: "每天", hint: "每天同一时刻" },
  { value: "weekdays", label: "工作日", hint: "周一到周五" },
  { value: "weekly", label: "每周", hint: "每周固定一天" },
  { value: "interval", label: "间隔", hint: "每隔 N 分钟一次" },
];

// 下标即后端 weekday 取值：0=周一 … 6=周日（见 api/delegate.py 的 ScheduleIn.weekday）。
// 顺序刻意从「一」开始：第 0 项必须和「周一」对上，否则每周委托整体错一天。
const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

const RUN_TAG: Record<DelegateRunStatus, [string, string]> = {
  succeeded: ["ok", "成功"],
  failed: ["bad", "失败"],
  running: ["info", "运行中"],
  pending: ["info", "排队中"],
  skipped: ["warn", "已跳过"],
};

/** 把结构化 schedule 翻译成一句人话。用户不该看到 cron。 */
function scheduleText(d: Delegate): string {
  const s = d.schedule;
  const at = s.at ?? "—";
  if (s.freq === "interval") return `每 ${s.every_minutes ?? "?"} 分钟`;
  if (s.freq === "daily") return `每天 ${at}`;
  if (s.freq === "weekdays") return `工作日 ${at}`;
  return `每周${WEEKDAYS[s.weekday ?? 0]} ${at}`;
}

/** 列表里的一行：要么是分组标题，要么是一条委托。拍平成一个数组，
 *  渲染时只写一次委托卡 JSX —— 分层嵌套写两遍最容易两边改漏一边。 */
type DelRow =
  | {
      kind: "head";
      key: string;
      title: string;
      note: string;
      count: number;
      collapsible: boolean;
      open: boolean;
    }
  | { kind: "item"; key: string; d: Delegate; i: number };

interface FormState {
  /** null = 新建 */
  id: string | null;
  name: string;
  kind: DelegateKind;
  freq: DelegateFreq;
  at: string;
  weekday: number;
  everyMinutes: number;
  sessionId: string;
  kbId: string;
  instruction: string;
  tone: string;
  maxChars: number;
  enabled: boolean;
}

const BLANK_FORM: FormState = {
  id: null,
  name: "",
  kind: "review",
  freq: "daily",
  at: "21:00",
  weekday: 0,
  everyMinutes: 60,
  sessionId: "",
  kbId: "",
  instruction: "",
  tone: "neutral",
  maxChars: 600,
  enabled: true,
};

function toForm(d: Delegate): FormState {
  return {
    id: d.id,
    name: d.name,
    kind: d.kind,
    freq: d.schedule.freq,
    at: d.schedule.at ?? "21:00",
    weekday: d.schedule.weekday ?? 0,
    everyMinutes: d.schedule.every_minutes ?? 60,
    sessionId: d.session_id ?? "",
    kbId: d.kb_id ?? "",
    instruction: d.instruction,
    tone: d.tone,
    maxChars: d.max_chars,
    enabled: d.enabled,
  };
}

export default function DelegatePage() {
  const { toasts, showToast } = useToast();
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;
  const [list, setList] = useState<ProbeState<Delegate[]>>({ state: "loading" });
  const [runs, setRuns] = useState<ProbeState<DelegateRun[]>>({ state: "loading" });
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [form, setForm] = useState<FormState>(BLANK_FORM);
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const formRef = useRef<HTMLDivElement>(null);

  /* ---- 首次进委托页的一次性提示 ----
     用户 B 的原话：「创建委托需要先创建一个对话，我找了一分钟左右才知道这个创建对话
     是什么玩意」。首选方案（点按钮直接出表单）已经做了，但**他不知道**，所以点之前
     仍在原地找"先建对话"的入口。这条提示只解决一件事：告他不用先建任何东西。
     每个账号只出现一次，点 × 或建完第一条委托都算看过。 */
  const tipKey = authedId ? `baomi.delegate.tip.v1.${authedId}` : "";
  const [tip, setTip] = useState(false);
  useEffect(() => {
    if (!tipKey) return;
    try {
      if (!localStorage.getItem(tipKey)) setTip(true);
    } catch {
      /* 隐私模式：读不到就当没看过，提示多出现几次不算问题 */
    }
  }, [tipKey]);
  const dismissTip = useCallback(() => {
    setTip(false);
    try {
      if (tipKey) localStorage.setItem(tipKey, "1");
    } catch {
      /* ignore */
    }
  }, [tipKey]);

  const loadList = useCallback(() => {
    api.delegate
      .list()
      .then((d) => setList(toProbe(d)))
      .catch((e) => setList(errProbe(e)));
    // 执行历史读全量表（/api/schedule/runs），不是逐条去问每个委托
    api.schedule
      .runs()
      .then((d) => setRuns(toProbe(d)))
      .catch(() => setRuns({ state: "absent" }));
  }, []);

  useEffect(() => {
    // 未登录不发请求：页面组件挂在 ConsoleShell 的登录门之外，登录前发的请求
    // 必然是 401，且登录后不会自动重来。登录成功会经 AUTHORIZED_EVENT 同步进
    // auth.me（见 useAuth / api.ts），依赖变化后自动补加载。
    if (!authedId) return;
    loadList();
    api
      .listSessions()
      .then((r) => setSessions(r.sessions))
      .catch(() => {
        /* 拉不到会话就退化成「默认会话」，不影响列表展示 */
      });
    api
      .listKbs()
      .then((r) => setKbs(r.kbs))
      .catch(() => {
        /* 同上 */
      });
  }, [authedId, loadList]);

  /**
   * 接住从模板广场带过来的预填（`/delegate?new=1` + sessionStorage）。
   *
   * 模板广场的「设为每日委托」会把指令写进 sessionStorage 再跳过来 ——
   * 那条 `baomi:delegate-preset` 读完立刻删掉，否则下次用户自己点「新建委托」
   * 还会莫名其妙冒出一段模板文案。
   *
   * ⚠️ 两个刻意之处，改动前先看这里：
   *  · setState 推在 `setTimeout(..., 0)` 里 —— 本项目的 react-hooks/set-state-in-effect
   *    不允许在 effect 体内直接 setState。
   *  · **故意不返回 cleanup**。如果写成 `return () => clearTimeout(t)`，那么只要
   *    effect 的依赖（showToast）变了、effect 重跑，cleanup 就会把这个定时器取消掉，
   *    而 sessionStorage 已经在同步阶段删过了 —— 结果是预填永远不生效。
   *    防重入改由 presetGuard 这个 ref 承担（它一旦置位，后续重跑一律早退）。
   */
  const presetGuard = useRef(false);
  useEffect(() => {
    if (presetGuard.current) return;
    if (typeof window === "undefined") return;
    if (new URLSearchParams(window.location.search).get("new") !== "1") return;

    let raw: string | null = null;
    try {
      raw = sessionStorage.getItem("baomi:delegate-preset");
      if (raw) sessionStorage.removeItem("baomi:delegate-preset");
    } catch {
      return; // 隐私模式禁了 sessionStorage：没有预填可用，静默放过
    }
    if (!raw) return;

    let preset: { from?: string; instruction?: string } | null = null;
    try {
      preset = JSON.parse(raw) as { from?: string; instruction?: string };
    } catch {
      return; // 存进去的不是合法 JSON，当没这回事
    }
    if (!preset?.instruction) return;

    presetGuard.current = true;
    const from = preset.from ?? "模板";
    const instruction = preset.instruction;
    window.setTimeout(() => {
      setForm((f) => ({ ...f, name: from.slice(0, 32), instruction }));
      // URL 上的 ?new=1 用完就清：刷新页面不该再预填一次
      window.history.replaceState(null, "", "/delegate");
      // 表单在右侧常驻但位置靠下，不滚过去用户会以为"点了没反应"
      formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      showToast("info", `已按「${from}」填好指令，确认频率后保存`);
    }, 0);
  }, [showToast]);

  // 预览浮层的 Esc 关闭 + 焦点归还。
  //
  // 全项目 7 处弹层都支持 Esc，唯独这个浮层之前没有 —— 键盘用户按 Esc 毫无反应，
  // 只能 Tab 到关闭按钮。写法与 FilePreviewModal 保持一致：
  //  · 监听挂在 document 上且用 capture，确保浮层内任何焦点位置都能收到；
  //  · cleanup 里把焦点还给触发元素，否则关掉浮层后键盘用户会被甩回页面开头。
  //    ⚠️ 这里是在 effect 里读 `document.activeElement` 的写法，成立的前提是
  //    **本浮层没有任何 autoFocus/自动聚焦**。若将来给浮层加了自动聚焦，
  //    必须改成在用户点击「预览」的那一刻捕获 opener —— 否则读到的会是浮层内的元素，
  //    焦点归还就静默失效了（FilePreviewModal 正因此改用惰性 useState 抓 opener）。
  useEffect(() => {
    if (!preview) return;
    const opener = document.activeElement;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setPreview(null);
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      if (opener instanceof HTMLElement) opener.focus?.();
    };
  }, [preview]);

  // 后端没上线时用演示数据顶住版面，但界面上会挂「演示数据」标记
  const isDemo = list.state === "absent";
  const items = list.state === "ready" ? list.data : isDemo ? DEMO_DELEGATES : [];
  const runItems = runs.state === "ready" ? runs.data : isDemo ? DEMO_RUNS : [];

  const activeCount = useMemo(() => items.filter((d) => d.enabled).length, [items]);

  /* ---- 列表分组 ----
     原来是一张平铺的单子，用户看不出「哪些还在跑、哪些已经停了」——
     而这一页的全部价值就是"它会自己来找我"，看不出在不在跑，等于看不出有没有用。
     ★ 后端没有 status 字段，只有 enabled / next_run_at：委托是**周期性**的，
       不存在"已完成"这种终态。所以只分「正在跑」「停下来了」两组，
       不硬造一个永远为空的"已完成"组 —— 空的分类比没有分类更让人困惑。 */
  const [openPaused, setOpenPaused] = useState(false);
  const rows = useMemo(() => {
    const running = items.filter((d) => d.enabled);
    const paused = items.filter((d) => !d.enabled);
    // 组内排序：正在跑的按下次触发升序（最快来的在前，还没算出下次的垫底）；
    // 停下来的按创建时间倒序（刚停的最可能被找回来）
    const FAR = Number.MAX_SAFE_INTEGER;
    running.sort((a, b) => (a.next_run_at ?? FAR) - (b.next_run_at ?? FAR));
    paused.sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
    const out: DelRow[] = [];
    if (running.length) {
      out.push({
        kind: "head",
        key: "g-run",
        title: "正在跑",
        note: "按下次触发时间，最快来的在前",
        count: running.length,
        collapsible: false,
        open: true,
      });
      running.forEach((d, i) => out.push({ kind: "item", key: d.id, d, i }));
    }
    if (paused.length) {
      out.push({
        kind: "head",
        key: "g-pause",
        title: "停下来了",
        note: openPaused ? "不会再触发，直到你重新打开开关" : "不会再触发 · 点这里展开",
        count: paused.length,
        collapsible: true,
        open: openPaused,
      });
      if (openPaused) paused.forEach((d, i) => out.push({ kind: "item", key: d.id, d, i }));
    }
    return out;
  }, [items, openPaused]);

  /** 演示模式下所有写操作都要明说没生效，不能假装成功 */
  const demoGuard = useCallback(
    (what: string): boolean => {
      if (!isDemo) return false;
      showToast("info", `接口待上线，${what}未提交。合入 feat/ai-module 分支后自动生效。`);
      return true;
    },
    [isDemo, showToast],
  );

  const patch = (p: Partial<FormState>) => setForm((f) => ({ ...f, ...p }));

  const submit = async () => {
    if (!form.name.trim() || form.name.trim().length < 3) {
      showToast("error", "名称至少 3 个字");
      return;
    }
    if (!form.instruction.trim()) {
      showToast("error", "生成指令不能为空 —— 它是知穗每次要照做的事");
      return;
    }
    if (form.id === null && form.enabled && activeCount >= MAX_ACTIVE) {
      showToast("error", `启用中的委托最多 ${MAX_ACTIVE} 条，请先暂停一条`);
      return;
    }
    if (demoGuard(form.id ? "保存" : "创建")) return;

    const body = {
      kind: form.kind,
      schedule: {
        freq: form.freq,
        at: form.freq === "interval" ? null : form.at,
        weekday: form.freq === "weekly" ? form.weekday : null,
        every_minutes: form.freq === "interval" ? form.everyMinutes : null,
        tz: TZ,
      },
      session_id: form.sessionId || null,
      kb_id: form.kbId || null,
      instruction: form.instruction.trim(),
      tone: form.tone,
      max_chars: form.maxChars,
      enabled: form.enabled,
    };

    setBusy("submit");
    try {
      if (form.id) {
        // name 建后不可改，所以更新时不带它进去
        await api.delegate.update(form.id, body);
        showToast("success", "已保存");
      } else {
        await api.delegate.create({ ...body, name: form.name.trim() });
        showToast("success", `已创建「${form.name.trim()}」`);
      }
      setForm(BLANK_FORM);
      // 能成功建成一条，说明他早就过了"要不要先建对话"这道坎，这条提示不用再出现
      dismissTip();
      loadList();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "提交失败，请稍后重试");
    } finally {
      setBusy(null);
    }
  };

  const toggleEnabled = async (d: Delegate, next: boolean) => {
    if (next && activeCount >= MAX_ACTIVE) {
      showToast("error", `启用中的委托最多 ${MAX_ACTIVE} 条，请先暂停一条`);
      return;
    }
    if (demoGuard(next ? "启用" : "暂停")) return;
    setBusy(`toggle-${d.id}`);
    try {
      await api.delegate.update(d.id, { enabled: next });
      loadList();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  const doRemove = async (d: Delegate) => {
    if (!window.confirm(`删除「${d.name}」？委托本身会被移除，但它的执行记录会保留，便于回看。`)) return;
    if (demoGuard("删除")) return;
    setBusy(`del-${d.id}`);
    try {
      await api.delegate.remove(d.id);
      showToast("info", "已删除");
      if (form.id === d.id) setForm(BLANK_FORM);
      loadList();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(null);
    }
  };

  /** dryRun=true 只生成不投递；false 才是真的发到会话里 */
  const doRun = async (d: Delegate, dryRun: boolean) => {
    if (demoGuard(dryRun ? "预览" : "立即执行")) {
      setPreview({
        name: d.name,
        text:
          "（演示内容）今天的复盘：\n\n你今天把二叉树三种遍历的递归实现自己写出来了，中序遍历为什么能得到有序序列也说清楚了。\n\n最该补的是线索二叉树 —— 你知道它省了栈，但说不出哪种场景下比递归更划算。\n\n明天的一条动作：找一道需要频繁找前驱/后继的题，分别用递归和线索化各做一遍，对比代码量和栈深度。",
      });
      return;
    }
    setBusy(`run-${d.id}`);
    try {
      const r = await api.delegate.run(d.id, dryRun);
      if (dryRun) {
        setPreview({ name: d.name, text: r?.content ?? "（后端未返回内容）" });
      } else {
        showToast("success", "已执行，结果存好了，回首页就能看到");
        loadList();
      }
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "执行失败");
    } finally {
      setBusy(null);
    }
  };

  const editing = form.id !== null;

  return (
    <ConsoleShell
      active="delegate"
      eyebrow="自动替你干活"
      title="每日委托"
      sub={
        <>
          让知穗在固定时间替你复盘、提醒、打招呼。触发后内容
          <b>会替你存下来</b>，你随时能翻回去看。
        </>
      }
      actions={
        <>
          <span className="ctag">
            启用中 <b style={{ fontWeight: 700, marginLeft: 3 }}>{activeCount}</b> / {MAX_ACTIVE}
          </span>
          <button type="button" className="btn btn-primary" onClick={() => setForm(BLANK_FORM)}>
            <CornIcon name="plus" />
            新建委托
          </button>
        </>
      }
    >
      {tip && (
        <div className="del-tip" role="status">
          <div className="del-tip-body">
            <b>直接说你要做什么就行。</b>
            比如「每天早上 7 点提醒我背 20 个单词」。点「新建委托」直接出表单，不用先建别的。
          </div>
          <button
            type="button"
            className="del-tip-x"
            onClick={dismissTip}
            aria-label="知道了，不再提示"
          >
            ×
          </button>
        </div>
      )}
      {list.state === "loading" && (
        <div className="card card-pad">
          <SkeletonLines widths={["70%", "50%", "84%"]} />
        </div>
      )}

      {list.state === "error" && (
        <Callout icon="warn">
          委托列表读不到：{list.message}
          <button type="button" className="btn btn-ghost btn-sm" style={{ marginLeft: 10 }} onClick={loadList}>
            重试
          </button>
        </Callout>
      )}

      {(list.state === "ready" || isDemo) && (
        <>
          {isDemo && (
            <div style={{ marginBottom: 14 }}>
              <NotReady
                what="每日委托接口（/api/delegate，7 个端点）"
                note="下面的列表与历史是设计稿的演示数据，按钮点了不会真的提交。"
              />
            </div>
          )}

          <div className="grid2">
            {/* ============ 左：委托列表 + 执行历史 ============ */}
            <div>
              <p className="sec-label">
                我的委托{isDemo && <span className="ctag warn" style={{ marginLeft: 6 }}>演示数据</span>}
              </p>

              <div className="card card-pad">
                {items.length === 0 ? (
                  <div style={{ padding: "10px 2px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <CornIcon name="clock" className="h-7 w-7" />
                      <div>
                        <div className="card-title">让知穗每天主动找你一次</div>
                        <div className="meta" style={{ marginTop: 3 }}>
                          比如每晚 21:00 把当天学过的东西复盘成一段话，存到你的复习记录里。
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      style={{ marginTop: 12 }}
                      onClick={() =>
                        setForm({
                          ...BLANK_FORM,
                          name: "每日学习复盘",
                          kind: "review",
                          instruction:
                            "根据我今天的学习产出给一段复盘：先说清我今天掌握了什么，再指出一个最该补的漏洞，最后给明天一条具体到动作的建议。",
                        })
                      }
                    >
                      <CornIcon name="plus" />
                      用这个示例填一份
                    </button>
                  </div>
                ) : (
                  rows.map((r) => {
                    if (r.kind === "head") {
                      return (
                        <button
                          key={r.key}
                          type="button"
                          className={`del-group-head${r.collapsible ? " is-collapsible" : ""}`}
                          aria-expanded={r.open}
                          onClick={() => {
                            if (r.collapsible) setOpenPaused((v) => !v);
                          }}
                        >
                          <span className="del-group-title">
                            {r.title}
                            <em>{r.count}</em>
                          </span>
                          <span className="del-group-note">{r.note}</span>
                          {r.collapsible && (
                            <span className="del-group-toggle">{r.open ? "收起" : "展开"}</span>
                          )}
                        </button>
                      );
                    }
                    const d = r.d;
                    return (
                    <div key={d.id}>
                      {r.i > 0 && <div className="divide" />}
                      <div className="dele">
                        <div className="dele-main">
                          <div className="dele-name">
                            {d.name}
                            <span className="ctag">{KIND_LABEL[d.kind] ?? d.kind}</span>
                          </div>
                          <div className="kv">
                            <span className="sched">
                              <CornIcon name={d.schedule.freq === "weekly" ? "calendar" : "clock"} />
                              {scheduleText(d)}
                              {d.schedule.tz && d.schedule.tz !== "Asia/Shanghai"
                                ? ` · ${d.schedule.tz}`
                                : ""}
                            </span>
                            {/* 只有用户自己给对话起过名字，才提"结果放回哪"。
                                默认位置是内部实现，说出来他只会多问一句"那是什么"——
                                B 原话就是"委托栏有很多与用户无关的信息"。 */}
                            {d.session_title && (
                              <span>
                                结果放回 <b>{d.session_title}</b>
                              </span>
                            )}
                            {d.kb_name && (
                              <span>
                                知识库：<b>{d.kb_name}</b>
                              </span>
                            )}
                          </div>
                          <p className="hint">
                            {d.enabled && d.next_run_at ? (
                              <>
                                下次触发{" "}
                                <span className="next">
                                  {fmtWhen(d.next_run_at)}（{fmtRemaining(d.next_run_at)}）
                                </span>
                              </>
                            ) : d.last_error ? (
                              <span className="text-red-ink">{d.last_error}</span>
                            ) : (
                              <>已暂停 · 不会再触发，直到你重新打开开关</>
                            )}
                          </p>
                        </div>
                        <div className="dele-side">
                          <Switch
                            checked={d.enabled}
                            disabled={busy === `toggle-${d.id}`}
                            label={`${d.name} 启用`}
                            onChange={(v) => void toggleEnabled(d, v)}
                          />
                          <div className="pos-actions">
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={busy === `run-${d.id}`}
                              onClick={() => void doRun(d, true)}
                            >
                              预览
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              title="编辑"
                              aria-label={`编辑 ${d.name}`}
                              onClick={() => setForm(toForm(d))}
                            >
                              <CornIcon name="pencil" />
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              title="立即执行（会真的投递）"
                              aria-label={`立即执行 ${d.name}`}
                              disabled={busy === `run-${d.id}`}
                              onClick={() => void doRun(d, false)}
                            >
                              <CornIcon name="bolt" />
                            </button>
                            <button
                              type="button"
                              className="icon-btn danger"
                              title="删除"
                              aria-label={`删除 ${d.name}`}
                              disabled={busy === `del-${d.id}`}
                              onClick={() => void doRemove(d)}
                            >
                              <CornIcon name="trash" />
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                    );
                  })
                )}
              </div>

              <div style={{ marginTop: 12 }}>
                <Callout>
                  <b>「预览」不投递。</b>它走{" "}
                  <code>POST /api/delegate/{"{id}"}/run?dry_run=true</code>，只生成内容给你看一遍；
                  「立即执行」才会真的跑一次，并消耗一次 <code>auto</code> 自动额度。
                </Callout>
              </div>

              {/* ---- 执行历史 ---- */}
              <p className="sec-label" style={{ marginTop: 22 }}>
                执行历史
              </p>
              <div className="card card-pad">
                {runItems.length === 0 ? (
                  <p className="hint" style={{ marginTop: 0 }}>
                    还没有执行记录。第一次触发之后，这里会显示每次的结果、耗时和消耗。
                  </p>
                ) : (
                  runItems.map((r) => {
                    const [cls, label] = RUN_TAG[r.status] ?? ["warn", r.status];
                    const dur = fmtDuration(r.started_at, r.finished_at);
                    return (
                      <div className="run" key={r.run_id}>
                        <span className="when">{r.planned_at ? fmtWhen(r.planned_at) : "—"}</span>
                        <span>
                          <span className={`ctag ${cls}`}>{label}</span>{" "}
                          <span className="meta">
                            {r.trigger === "manual" ? "你点了「立即执行」" : "定时触发"}
                            {dur ? ` · ${dur}` : ""}
                            {r.error ? ` · ${r.error}` : ""}
                          </span>
                        </span>
                        <span className="meta">{fmtNum(r.tokens_out ?? 0)} tok</span>
                      </div>
                    );
                  })
                )}
              </div>
              <p className="hint">
                历史按 <code>trigger</code> 区分「定时」与「手动」—— 排查「今天为什么没收到」先看这一列。
              </p>
            </div>

            {/* ============ 右：新建 / 编辑表单（常驻） ============ */}
            <div ref={formRef}>
              <p className="sec-label">{editing ? "编辑委托" : "新建委托"}</p>
              <div className="card card-pad">
                <Field label="名称（3–32 字，建后不可改）">
                  <input
                    className="input"
                    value={form.name}
                    maxLength={32}
                    disabled={editing}
                    placeholder="例如：每日学习复盘"
                    onChange={(e) => patch({ name: e.target.value })}
                  />
                </Field>

                <div className="field">
                  <span className="f">类型</span>
                  <Seg
                    ariaLabel="委托类型"
                    options={KIND_OPTIONS}
                    value={form.kind}
                    onChange={(v) => patch({ kind: v })}
                  />
                </div>

                <div className="field">
                  <span className="f">频率</span>
                  <Seg
                    ariaLabel="触发频率"
                    options={FREQ_OPTIONS}
                    value={form.freq}
                    onChange={(v) => patch({ freq: v })}
                    stretch
                  />
                </div>

                {form.freq === "interval" ? (
                  <Field label="每多少分钟一次" hint="最小 60 分钟（1 小时），最多 1440">
                    <input
                      className="input"
                      type="number"
                      min={60}
                      max={1440}
                      value={form.everyMinutes}
                      onChange={(e) => patch({ everyMinutes: Number(e.target.value) || 60 })}
                    />
                  </Field>
                ) : (
                  <div className="row field">
                    <div style={{ flex: 1 }}>
                      <Field label="触发时间">
                        <input
                          className="input"
                          type="time"
                          value={form.at}
                          onChange={(e) => patch({ at: e.target.value })}
                        />
                      </Field>
                    </div>
                    <div style={{ flex: 1 }}>
                      <Field label="时区" hint="当前只支持一个时区">
                        <input className="input" value={TZ} disabled />
                      </Field>
                    </div>
                  </div>
                )}

                {form.freq === "weekly" && (
                  <div className="field">
                    <span className="f">星期几</span>
                    <Seg
                      ariaLabel="星期几"
                      options={WEEKDAYS.map((w, i) => ({ value: String(i), label: w }))}
                      value={String(form.weekday)}
                      onChange={(v) => patch({ weekday: Number(v) })}
                      stretch
                    />
                  </div>
                )}

                <Field label="结果放在哪" hint="不选就放默认位置">
                  <select
                    className="input"
                    value={form.sessionId}
                    onChange={(e) => patch({ sessionId: e.target.value })}
                  >
                    <option value="">默认位置（不用选）</option>
                    {sessions.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="绑定知识库（可选）" hint="绑了之后生成时只参考这个库">
                  <select
                    className="input"
                    value={form.kbId}
                    onChange={(e) => patch({ kbId: e.target.value })}
                  >
                    <option value="">不绑定</option>
                    {kbs.map((k) => (
                      <option key={k.kb_id} value={k.kb_id}>
                        {k.name}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="生成指令" hint="知穗每次要照做的事，写得越具体结果越稳">
                  <textarea
                    className="input"
                    value={form.instruction}
                    maxLength={1000}
                    placeholder="例如：根据我今天的学习产出给一段复盘，先说掌握了什么，再指出一个最该补的漏洞。"
                    onChange={(e) => patch({ instruction: e.target.value })}
                  />
                </Field>

                <div className="row field">
                  <div style={{ flex: 1 }}>
                    <Field label="语气">
                      <select
                        className="input"
                        value={form.tone}
                        onChange={(e) => patch({ tone: e.target.value })}
                      >
                        <option value="neutral">中性</option>
                        <option value="warm">温和</option>
                        <option value="strict">严格</option>
                      </select>
                    </Field>
                  </div>
                  <div style={{ flex: 1 }}>
                    <Field label="长度上限（字）">
                      <input
                        className="input"
                        type="number"
                        min={200}
                        max={2000}
                        step={50}
                        value={form.maxChars}
                        onChange={(e) => patch({ maxChars: Number(e.target.value) || 600 })}
                      />
                    </Field>
                  </div>
                </div>

                <div
                  className="field"
                  style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
                >
                  <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-2)" }}>
                    {editing ? "启用这条委托" : "创建后立即启用"}
                  </span>
                  <Switch
                    checked={form.enabled}
                    label="启用"
                    onChange={(v) => patch({ enabled: v })}
                  />
                </div>

                <div className="row" style={{ marginTop: 16 }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    style={{ flex: 1 }}
                    disabled={busy === "submit"}
                    onClick={() => void submit()}
                  >
                    <CornIcon name="check" />
                    {editing ? "保存" : "创建"}
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={() => setForm(BLANK_FORM)}>
                    {editing ? "取消编辑" : "清空"}
                  </button>
                </div>
                <p className="hint">
                  启用中的委托上限 {MAX_ACTIVE} 条；同名会返回 409「同名委托已存在」。
                </p>
              </div>
            </div>
          </div>
        </>
      )}

      {/* ---- 预览浮层：dry_run 的产物，只给人看 ---- */}
      {preview && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="预览生成内容"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,.42)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
            zIndex: 60,
          }}
          onClick={() => setPreview(null)}
        >
          <div
            className="card card-pad"
            style={{ maxWidth: 560, width: "100%", maxHeight: "80vh", overflowY: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="card-head">
              <span className="card-title">预览 · {preview.name}</span>
              <button
                type="button"
                className="icon-btn"
                aria-label="关闭预览"
                onClick={() => setPreview(null)}
              >
                <CornIcon name="close" />
              </button>
            </div>
            <p className="hint" style={{ marginTop: 4 }}>
              这是 <code>dry_run</code> 的结果：没有真的执行，也没有改下次触发时间。
            </p>
            <div
              style={{
                marginTop: 12,
                whiteSpace: "pre-wrap",
                fontSize: "var(--fs-lg)",
                lineHeight: "var(--lh-loose)",
              }}
            >
              {preview.text}
            </div>
          </div>
        </div>
      )}

      <ToastHost toasts={toasts} />
    </ConsoleShell>
  );
}
