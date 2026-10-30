"use client";

// P7 · 四六级备考工作区
//
// 为什么单独做一屏：申报书 B3 写「四六级备考模块已实际落地」，如果只有模板广场里
// 一张卡片，评委点进来找不到东西。这一屏把三件事做成可点、可勾、可回看的数据：
//   1. 备考档案 —— 考几级 / 哪天考 / 每天练什么 / 练多少 / 目标分，**都可自定义**
//      （2026-10-27 前是写死的 54 天 × 5 模块，内测反馈"连四级六级都不能选"）；
//   2. 词汇本 —— SRS（1/3/7/15/30 天）间隔复习，记得/忘记驱动下一档；
//   3. 分数曲线 —— 每次模考/真考记一条，画趋势与目标线。
//
// ★ 关键交互取舍：**天数不是输入项**，由考试日推导。用户关心的是"12 月 12 号考试"，
//   而不是"我要 54 天"；让他填天数再反算考试日，等于让他做一道减法。
//
// 数据全部来自 /api/cet/*（后端 owner 强制隔离）。本页不造演示数据：
// 没有档案就显示配置向导，避免出现「看起来有、其实是假的」。
// 选项（级别 / 模块 / 强度 / 日期候选）全部由后端 overview.options 下发，前端不硬编码。

import { useCallback, useEffect, useMemo, useState } from "react";
import PageShell from "@/components/console/PageShell";
import { Seg } from "@/components/console/ui";
import { useAuth } from "@/lib/hooks/useAuth";

interface Plan {
  id: string;
  title: string;
  start_date: string;
  exam_date: string | null;
  total_days: number;
  exam_type: string;
  exam_type_label: string;
  target_score: number | null;
  modules: string[];
  intensity: string;
  intensity_label: string;
  /** 距离考试还有几天（0=今天考，负数=已过）。后端算好，前端不自己推时区 */
  days_left: number | null;
}
interface PlanOptions {
  exam_types: { value: string; label: string; default_target: number }[];
  modules: string[];
  intensities: { value: string; label: string; hint: string }[];
  exam_date_suggestions: { date: string; label: string }[];
  min_days: number;
  max_days: number;
}
interface Task {
  id: string;
  module: string;
  title: string;
  done: number;
}
interface Score {
  id: string;
  exam_type: string;
  score: number;
  target: number | null;
  taken_at: string;
  note: string | null;
}
interface Overview {
  plan: Plan | null;
  progress: { done: number; total: number; percent: number };
  today_tasks: Task[];
  streak: number;
  vocab_due: number;
  scores: Score[];
  today: string;
  options: PlanOptions;
}
interface Vocab {
  id: string;
  word: string;
  meaning: string | null;
  source: string | null;
  step: number;
  next_review: string;
  wrong_count: number;
}

const card: React.CSSProperties = {
  background: "var(--card-bg)",
  border: "1px solid var(--line)",
  borderRadius: 14,
  padding: 18,
};
const label: React.CSSProperties = { fontSize: "var(--fs-xs)", color: "var(--text-2)", marginBottom: 6 };
const input: React.CSSProperties = {
  border: "1px solid var(--input-border)",
  borderRadius: 8,
  background: "var(--surface)",
  color: "var(--text-1)",
  padding: "8px 10px",
  fontSize: "var(--fs-md)",
  outline: "none",
};
const btn: React.CSSProperties = {
  background: "var(--solid)",
  color: "var(--solid-text)",
  border: "none",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: "var(--fs-xs)",
  fontWeight: 600,
  cursor: "pointer",
};
/** 可勾选的胶囊按钮（模块 / 考试日期候选共用） */
const pill: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 999,
  padding: "6px 14px",
  fontSize: "var(--fs-xs)",
  cursor: "pointer",
  background: "var(--surface)",
  color: "var(--text-3)",
  lineHeight: 1.4,
};

async function jfetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: "include", ...init });
  if (!res.ok) {
    let msg = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") msg = body.detail;
    } catch {
      /* 非 JSON 错误体，保留默认文案 */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

/** 进度环：SVG 圆环，比进度条更能一眼看出「还剩多少」 */
function Ring({ percent }: { percent: number }) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.max(0, Math.min(100, percent)) / 100);
  return (
    <svg width="104" height="104" viewBox="0 0 104 104" role="img" aria-label={`进度 ${percent}%`}>
      <circle cx="52" cy="52" r={r} fill="none" stroke="var(--fill-3)" strokeWidth="9" />
      <circle
        cx="52"
        cy="52"
        r={r}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="9"
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={off}
        transform="rotate(-90 52 52)"
      />
      <text x="52" y="57" textAnchor="middle" fontSize="18" fontWeight="700" fill="var(--text-1)">
        {percent}%
      </text>
    </svg>
  );
}

/** 分数曲线：纯 SVG，不引图表库（移动端也不会多下载一份 JS） */
function ScoreChart({ scores }: { scores: Score[] }) {
  const W = 620;
  const H = 200;
  const pad = 34;
  const pts = scores;
  if (pts.length === 0) {
    return <p style={{ color: "var(--text-2)", fontSize: "var(--fs-xs)" }}>还没有分数记录，先在下面记一条。</p>;
  }
  const values = pts.map((p) => p.score);
  const target = pts.find((p) => p.target != null)?.target ?? null;
  const all = target != null ? [...values, target] : values;
  const min = Math.min(...all) - 20;
  const max = Math.max(...all) + 20;
  const x = (i: number) => (pts.length === 1 ? W / 2 : pad + (i * (W - pad * 2)) / (pts.length - 1));
  const y = (v: number) => H - pad - ((v - min) / Math.max(1, max - min)) * (H - pad * 2);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.score)}`).join(" ");
  const area = `${line} L${x(pts.length - 1)},${H - pad} L${x(0)},${H - pad} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="分数趋势">
      <path d={area} fill="var(--accent-soft)" opacity="0.5" />
      {target != null && (
        <>
          <line x1={pad} x2={W - pad} y1={y(target)} y2={y(target)} stroke="var(--accent)" strokeDasharray="5 5" strokeWidth="1.5" />
          <text x={W - pad} y={y(target) - 6} textAnchor="end" fontSize="11" fill="var(--accent-ink)">
            目标 {target}
          </text>
        </>
      )}
      <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinejoin="round" />
      {pts.map((p, i) => (
        <g key={p.id}>
          <circle cx={x(i)} cy={y(p.score)} r="4" fill="var(--accent)" />
          <text x={x(i)} y={H - 10} textAnchor="middle" fontSize="10" fill="var(--text-3)">
            {p.taken_at.slice(5)}
          </text>
        </g>
      ))}
    </svg>
  );
}

/** 申报档案的提交载荷（与 api/cet.py 的 PATCH/POST 白名单一一对应） */
interface PlanPayload {
  exam_type: string;
  exam_date: string;
  modules: string[];
  intensity: string;
  target_score?: number;
}

/** 两个 yyyy-mm-dd 之间差几天。用 UTC 解析：本地时区解析会让"今天"偏移一天 */
function diffDays(from: string, to: string): number {
  const [ay, am, ad] = from.split("-").map(Number);
  const [by, bm, bd] = to.split("-").map(Number);
  if (!ay || !by) return 0;
  return Math.round((Date.UTC(by, bm - 1, bd) - Date.UTC(ay, am - 1, ad)) / 86400000);
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "flex-start", marginBottom: 14, flexWrap: "wrap" }}>
      <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)", width: 76, flex: "0 0 auto", paddingTop: 7 }}>
        {label}
      </span>
      <div style={{ flex: "1 1 260px", minWidth: 0 }}>{children}</div>
    </div>
  );
}

/**
 * 备考档案编辑器。空状态（建立档案）与"调整设置"**复用同一个组件**。
 *
 * 不做成两套界面：同一份配置用两种控件填写，用户会以为它们是两回事，
 * 然后问"为什么新建时能选六级、改的时候不能"。
 *
 * 天数不再是输入项 —— 由考试日推导。用户关心的是"12 月 12 号考试"，
 * 而不是"我要 54 天"；让他填天数再反算考试日，等于让他做一道减法。
 */
function PlanEditor({
  options,
  today,
  initial,
  busy,
  onSubmit,
  onArchive,
  archiveLabel,
  archiveDanger,
}: {
  options: PlanOptions;
  today: string;
  initial?: Plan | null;
  busy: boolean;
  onSubmit: (p: PlanPayload) => void;
  onArchive?: () => void;
  /** 两步确认的文案由外层控制（第一次"结束这个计划"，第二次"确定结束？"） */
  archiveLabel?: string;
  archiveDanger?: boolean;
}) {
  const [level, setLevel] = useState(initial?.exam_type ?? options.exam_types[1]?.value ?? "CET6");
  const [examDate, setExamDate] = useState(
    initial?.exam_date ?? options.exam_date_suggestions[0]?.date ?? "",
  );
  const [mods, setMods] = useState<string[]>(initial?.modules ?? options.modules);
  const [inten, setInten] = useState(initial?.intensity ?? "standard");
  const [target, setTarget] = useState(
    String(
      initial?.target_score ??
        options.exam_types.find((x) => x.value === (initial?.exam_type ?? "CET6"))?.default_target ??
        425,
    ),
  );
  // 用户手动改过目标分之后，切换级别就不再覆盖他的输入
  const [targetTouched, setTargetTouched] = useState(Boolean(initial?.target_score));

  // 天数有两个口径，**不能混用**：
  //   spanDays = 含今天的天数（决定要生成多少条任务）
  //   leftDays = 距考试还有几天（不含今天，与摘要行的"还剩 N 天"一致）
  // 混用会出现"向导说还剩 47 天、摘要行说 46 天"这种自相矛盾。
  const spanDays = examDate ? diffDays(today, examDate) + 1 : 0;
  const valid = spanDays >= 1 && mods.length > 0;
  const levelDefault = options.exam_types.find((x) => x.value === level)?.default_target ?? 425;

  const switchLevel = (v: string) => {
    setLevel(v);
    if (!targetTouched) {
      const next = options.exam_types.find((x) => x.value === v)?.default_target;
      if (next) setTarget(String(next));
    }
  };

  const toggleMod = (m: string) =>
    setMods((prev) => (prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]));

  const submit = () => {
    if (!valid || busy) return;
    const n = Number(target);
    onSubmit({
      exam_type: level,
      exam_date: examDate,
      modules: mods,
      intensity: inten,
      target_score: Number.isFinite(n) && n > 0 ? n : undefined,
    });
  };

  return (
    <div style={card}>
      <b style={{ color: "var(--text-1)", fontSize: "var(--fs-lg)" }}>
        {initial ? "调整备考设置" : "建立你的备考档案"}
      </b>
      <p style={{ color: "var(--text-2)", fontSize: "var(--fs-xs)", margin: "4px 0 16px" }}>
        选好之后按你的选择生成每日任务，之后随时能改 —— 改设置不会动你已经勾过的历史记录。
      </p>

      <Row label="考哪一级">
        <Seg
          options={options.exam_types.map((x) => ({ value: x.value, label: x.label }))}
          value={level}
          onChange={switchLevel}
          ariaLabel="考试级别"
        />
      </Row>

      <Row label="考试日期">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {options.exam_date_suggestions.map((s) => (
            <button
              key={s.date}
              type="button"
              onClick={() => setExamDate(s.date)}
              aria-pressed={examDate === s.date}
              style={{
                ...pill,
                background: examDate === s.date ? "var(--accent-soft)" : "var(--surface)",
                borderColor: examDate === s.date ? "var(--accent)" : "var(--line)",
                color: examDate === s.date ? "var(--accent-ink)" : "var(--text-3)",
              }}
            >
              {s.label}
            </button>
          ))}
          <input
            type="date"
            aria-label="考试日期"
            value={examDate}
            onChange={(e) => setExamDate(e.target.value)}
            style={{ ...input, padding: "6px 8px" }}
          />
        </div>
        <p
          style={{
            fontSize: "var(--fs-xs)",
            margin: "8px 0 0",
            color: spanDays >= 1 ? "var(--accent-ink)" : "var(--red-ink)",
          }}
        >
          {spanDays >= 1
            ? `共 ${spanDays} 天（含今天）· 每天 ${mods.length} 个任务，共 ${spanDays * mods.length} 项`
            : "考试日不能早于今天"}
        </p>
        <p style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)", margin: "4px 0 0" }}>
          上面三个日期按"6 月 / 12 月第二个周六"的惯例推算，请以官方通知为准；也可以直接选一个日期。
        </p>
      </Row>

      <Row label="每天练什么">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {options.modules.map((m) => {
            const on = mods.includes(m);
            return (
              <button
                key={m}
                type="button"
                onClick={() => toggleMod(m)}
                aria-pressed={on}
                style={{
                  ...pill,
                  background: on ? "var(--accent-soft)" : "var(--surface)",
                  borderColor: on ? "var(--accent)" : "var(--line)",
                  color: on ? "var(--accent-ink)" : "var(--text-3)",
                }}
              >
                {on ? "✓ " : ""}
                {m}
              </button>
            );
          })}
        </div>
        {mods.length === 0 ? (
          <p style={{ fontSize: "var(--fs-xs)", margin: "8px 0 0", color: "var(--red-ink)" }}>
            至少选一个模块，否则每天没有任务可做。
          </p>
        ) : null}
      </Row>

      <Row label="练多少">
        <Seg
          options={options.intensities.map((x) => ({ value: x.value, label: x.label, hint: x.hint }))}
          value={inten}
          onChange={setInten}
          ariaLabel="每天练习强度"
        />
      </Row>

      <Row label="目标分">
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <input
            aria-label="目标分"
            inputMode="numeric"
            value={target}
            onChange={(e) => {
              setTarget(e.target.value.replace(/[^\d]/g, ""));
              setTargetTouched(true);
            }}
            style={{ ...input, width: 88, textAlign: "center" }}
          />
          <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>
            {level === "CET4" ? "四级" : "六级"}建议 {levelDefault} · 425 及格 / 500 良好 / 550 优秀
          </span>
        </div>
      </Row>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
        {onArchive ? (
          <button
            type="button"
            onClick={onArchive}
            disabled={busy}
            style={{
              ...btn,
              background: "transparent",
              color: archiveDanger ? "var(--red-ink)" : "var(--text-3)",
              border: `1px solid ${archiveDanger ? "var(--red)" : "var(--line)"}`,
            }}
          >
            {archiveLabel ?? "结束这个计划"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={submit}
          disabled={busy || !valid}
          style={{ ...btn, opacity: busy || !valid ? 0.5 : 1 }}
        >
          {busy ? "保存中…" : initial ? "保存修改" : "建立我的备考档案"}
        </button>
      </div>
    </div>
  );
}

export default function CetPage() {
  // 身份用 useAuth 自己查一份（不读外壳的 Context）：这一页要连发好几个带鉴权的请求，
  // 由它自己掌握 me 的时序更好排。注意这一份实例与外壳那份是**独立的**，
  // 挂载时它自己的 me 还没回来 —— 所以下面的数据请求必须等 authedId 到位，
  // 否则首发必然是 401（外壳的登录门只保证"登录后才会挂载本页"，不解决时序）。
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;
  const [ov, setOv] = useState<Overview | null>(null);
  const [vocab, setVocab] = useState<Vocab[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const [editing, setEditing] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [newWord, setNewWord] = useState("");
  const [newMeaning, setNewMeaning] = useState("");
  const [scoreType, setScoreType] = useState("CET6");
  const [scoreVal, setScoreVal] = useState("");
  const [scoreTarget, setScoreTarget] = useState("500");

  const load = useCallback(async () => {
    setErr("");
    try {
      const [o, v] = await Promise.all([
        jfetch<Overview>("/api/cet/overview"),
        jfetch<{ items: Vocab[] }>("/api/cet/vocab?due=1"),
      ]);
      setOv(o);
      setVocab(v.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  // 登录态守卫：身份确认前不发请求（与 /checkin 同一套写法）
  useEffect(() => {
    if (!authedId) return;
    void load();
  }, [authedId, load]);

  // 新建与修改走同一个回调：靠 ov.plan 是否存在决定用 POST 还是 PATCH。
  // 分成两个函数会让"编辑时点了新建"这种错位悄悄发生（生成第二份档案）。
  const submitPlan = async (payload: PlanPayload) => {
    setBusy(true);
    setErr("");
    try {
      await jfetch("/api/cet/plan", {
        method: ov?.plan ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setEditing(false);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const archivePlan = async () => {
    if (!confirmArchive) {
      // 两步确认：第一次点击变成"确定结束？"，再点才真执行。
      // 不用 window.confirm —— 它在移动端会弹系统框，和页面风格割裂。
      setConfirmArchive(true);
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await jfetch("/api/cet/plan", { method: "DELETE" });
      setConfirmArchive(false);
      setEditing(false);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "结束失败");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (t: Task) => {
    // 乐观更新：先翻 UI，失败再回滚并提示
    setOv((prev) =>
      prev
        ? { ...prev, today_tasks: prev.today_tasks.map((x) => (x.id === t.id ? { ...x, done: x.done ? 0 : 1 } : x)) }
        : prev,
    );
    try {
      await jfetch("/api/cet/task", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: t.id, done: !t.done }),
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "更新失败");
      await load();
    }
  };

  const addWord = async () => {
    if (!newWord.trim()) return;
    setBusy(true);
    try {
      await jfetch("/api/cet/vocab", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word: newWord.trim(), meaning: newMeaning.trim() }),
      });
      setNewWord("");
      setNewMeaning("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "添加失败");
    } finally {
      setBusy(false);
    }
  };

  const review = async (v: Vocab, remembered: boolean) => {
    setVocab((prev) => prev.filter((x) => x.id !== v.id));
    try {
      await jfetch("/api/cet/vocab/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: v.id, remembered }),
      });
      setOv((prev) => (prev ? { ...prev, vocab_due: Math.max(0, prev.vocab_due - 1) } : prev));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "复习记录失败");
      await load();
    }
  };

  const addScore = async () => {
    const n = Number(scoreVal);
    if (!Number.isFinite(n) || n <= 0) return;
    setBusy(true);
    try {
      await jfetch("/api/cet/scores", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          exam_type: scoreType,
          score: Math.round(n),
          target: scoreTarget ? Math.round(Number(scoreTarget)) : null,
        }),
      });
      setScoreVal("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "记录失败");
    } finally {
      setBusy(false);
    }
  };

  const plan = ov?.plan ?? null;
  const progress = ov?.progress ?? { done: 0, total: 0, percent: 0 };
  const scores = useMemo(() => ov?.scores ?? [], [ov]);
  // 考试日过去之后，今日任务区不该只剩一句"今天不在计划区间内"
  const examOver = plan?.days_left != null && plan.days_left < 0;
  const daysLeftText = () => {
    const d = plan?.days_left;
    if (d == null) return "考试日未设置";
    if (d > 0) return `还剩 ${d} 天`;
    if (d === 0) return "今天考试";
    return `考试已结束 ${-d} 天`;
  };

  return (
    <PageShell
      eyebrow="专项备考"
      title="四六级备考工作区"
      sub="按你的考试日期和每天能练的内容生成任务，随时可改。词卡按遗忘曲线复习，分数画成趋势。"
      actions={
        plan && !editing ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span
              style={{
                fontSize: "var(--fs-xs)",
                padding: "2px 9px",
                borderRadius: 999,
                background: "var(--accent-soft)",
                color: "var(--accent-ink)",
                fontWeight: 600,
              }}
            >
              {plan.exam_type_label}
            </span>
            <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)" }}>{plan.exam_date}</span>
            <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>·</span>
            <span style={{ fontSize: "var(--fs-xs)", color: examOver ? "var(--text-3)" : "var(--text-2)" }}>
              {daysLeftText()}
            </span>
            {plan.target_score ? (
              <>
                <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>·</span>
                <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)" }}>目标 {plan.target_score}</span>
              </>
            ) : null}
            <button
              type="button"
              onClick={() => setEditing(true)}
              style={{ ...pill, padding: "4px 12px", marginLeft: 2 }}
            >
              调整设置
            </button>
          </div>
        ) : null
      }
    >
      {err ? (
        <div style={{ ...card, borderColor: "var(--red)", color: "var(--red-ink)", marginBottom: 16, fontSize: "var(--fs-xs)" }}>
          {err}
        </div>
      ) : null}

      {loading || !ov ? (
        <p style={{ color: "var(--text-2)" }}>正在加载…</p>
      ) : !plan || editing ? (
        <>
          {editing ? (
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setConfirmArchive(false);
              }}
              style={{ ...pill, marginBottom: 12 }}
            >
              ← 返回工作区（不保存修改）
            </button>
          ) : null}
          <PlanEditor
            options={ov.options}
            today={ov.today}
            initial={plan}
            busy={busy}
            onSubmit={submitPlan}
            onArchive={plan ? archivePlan : undefined}
            archiveLabel={confirmArchive ? "确定结束？再点一次" : "结束这个计划"}
            archiveDanger={confirmArchive}
          />
          {!plan ? (
            <p style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)", marginTop: 12, textAlign: "center" }}>
              还没有档案时会显示这个配置向导；建档之后在这里随时能改。
            </p>
          ) : null}
        </>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          {/* 统计三卡 */}
          <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
            <div style={{ ...card, display: "flex", alignItems: "center", gap: 14 }}>
              <Ring percent={progress.percent} />
              <div>
                <div style={{ ...label, marginBottom: 2 }}>总进度</div>
                <b style={{ fontSize: "var(--fs-title)", color: "var(--text-1)" }}>
                  {progress.done}/{progress.total}
                </b>
                <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)" }}>已完成任务</div>
              </div>
            </div>
            <div style={card}>
              <div style={label}>连续学习</div>
              <b style={{ fontSize: "var(--fs-num)", color: "var(--accent-ink)" }}>{ov?.streak ?? 0}</b>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)" }}>天（有勾选即算）</div>
            </div>
            <div style={card}>
              <div style={label}>今日待复习</div>
              <b style={{ fontSize: "var(--fs-num)", color: "var(--accent-ink)" }}>{ov?.vocab_due ?? 0}</b>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)" }}>个词卡到期</div>
            </div>
          </div>

          {/* 今日任务 */}
          <div style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
              <b style={{ color: "var(--text-1)" }}>今日任务 · {ov?.today}</b>
              <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>
                {ov?.today_tasks.filter((t) => t.done).length ?? 0}/{ov?.today_tasks.length ?? 0}
              </span>
            </div>
            {ov && ov.today_tasks.length === 0 ? (
              examOver ? (
                <div>
                  <p style={{ color: "var(--text-2)", fontSize: "var(--fs-xs)", margin: "0 0 10px" }}>
                    这场考试已经结束了。已完成 {progress.done}/{progress.total} 项（
                    {progress.percent}%）——
                    记录都还在，随时可以开始下一次备考。
                  </p>
                  <button type="button" style={btn} onClick={() => setEditing(true)}>
                    开始新的备考
                  </button>
                </div>
              ) : (
                <p style={{ color: "var(--text-2)", fontSize: "var(--fs-xs)" }}>
                  今天不在计划区间内（计划尚未开始）。
                </p>
              )
            ) : (
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
                {ov?.today_tasks.map((t) => (
                  <li key={t.id}>
                    <button
                      onClick={() => toggle(t)}
                      style={{
                        width: "100%",
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        textAlign: "left",
                        background: t.done ? "var(--accent-soft)" : "var(--surface)",
                        border: "1px solid var(--line)",
                        borderRadius: 10,
                        padding: "10px 12px",
                        cursor: "pointer",
                        color: "var(--text-1)",
                      }}
                    >
                      <span
                        aria-hidden
                        style={{
                          width: 18,
                          height: 18,
                          borderRadius: 5,
                          border: "1.5px solid var(--accent)",
                          background: t.done ? "var(--accent)" : "transparent",
                          color: "var(--solid-text)",
                          fontSize: "var(--fs-xs)",
                          lineHeight: "16px",
                          textAlign: "center",
                          flex: "0 0 auto",
                        }}
                      >
                        {t.done ? "✓" : ""}
                      </span>
                      <span style={{ fontSize: "var(--fs-xs)", color: "var(--accent-ink)", flex: "0 0 auto" }}>{t.module}</span>
                      <span style={{ fontSize: "var(--fs-xs)", overflowWrap: "anywhere" }}>{t.title}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 词汇本 */}
          <div style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
              <b style={{ color: "var(--text-1)" }}>词汇本 · 今日到期</b>
              <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>1 / 3 / 7 / 15 / 30 天间隔</span>
            </div>
            {vocab.length === 0 ? (
              <p style={{ color: "var(--text-2)", fontSize: "var(--fs-xs)" }}>今天没有到期的词卡，先加几个词吧。</p>
            ) : (
              <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
                {vocab.map((v) => (
                  <div key={v.id} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12, background: "var(--surface)" }}>
                    <b style={{ fontSize: "var(--fs-xl)", color: "var(--text-1)", overflowWrap: "anywhere" }}>{v.word}</b>
                    <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)", margin: "4px 0 10px", overflowWrap: "anywhere" }}>
                      {v.meaning || "（未填释义）"}
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={() => review(v, true)}
                        style={{ ...btn, flex: 1, background: "var(--success)", color: "#fff" }}
                      >
                        记得
                      </button>
                      <button
                        onClick={() => review(v, false)}
                        style={{ ...btn, flex: 1, background: "var(--warning)", color: "#fff" }}
                      >
                        忘了
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <input
                aria-label="单词"
                placeholder="单词 / 短语"
                value={newWord}
                onChange={(e) => setNewWord(e.target.value)}
                style={{ ...input, flex: "1 1 140px", minWidth: 0 }}
              />
              <input
                aria-label="释义"
                placeholder="释义（可留空）"
                value={newMeaning}
                onChange={(e) => setNewMeaning(e.target.value)}
                style={{ ...input, flex: "1 1 160px", minWidth: 0 }}
              />
              <button style={btn} onClick={addWord} disabled={busy || !newWord.trim()}>
                加入
              </button>
            </div>
          </div>

          {/* 分数曲线 */}
          <div style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
              <b style={{ color: "var(--text-1)" }}>模考 / 真考分数</b>
              <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>共 {scores.length} 条</span>
            </div>
            <ScoreChart scores={scores} />
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap", alignItems: "center" }}>
              <select aria-label="考试类型" value={scoreType} onChange={(e) => setScoreType(e.target.value)} style={input}>
                <option value="CET4">四级</option>
                <option value="CET6">六级</option>
                <option value="MOCK">模考</option>
              </select>
              <input
                aria-label="分数"
                placeholder="分数"
                value={scoreVal}
                onChange={(e) => setScoreVal(e.target.value)}
                style={{ ...input, width: 90 }}
              />
              <input
                aria-label="目标分"
                placeholder="目标"
                value={scoreTarget}
                onChange={(e) => setScoreTarget(e.target.value)}
                style={{ ...input, width: 80 }}
              />
              <button style={btn} onClick={addScore} disabled={busy || !scoreVal}>
                记录一次
              </button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}
