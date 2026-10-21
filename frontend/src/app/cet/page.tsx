"use client";

// P7 · 四六级备考工作区
//
// 为什么单独做一屏：申报书 B3 写「四六级备考模块已实际落地」，如果只有模板广场里
// 一张卡片，评委点进来找不到东西。这一屏把三件事做成可点、可勾、可回看的数据：
//   1. 备考计划（54 天 × 5 模块）—— 任务落库，可勾选、算进度、算连续天数；
//   2. 词汇本 —— SRS（1/3/7/15/30 天）间隔复习，记得/忘记驱动下一档；
//   3. 分数曲线 —— 每次模考/真考记一条，画趋势与目标线。
//
// 数据全部来自 /api/cet/*（后端 owner 强制隔离）。本页不造演示数据：
// 没有计划就显示「生成计划」的空状态，避免出现「看起来有、其实是假的」。

import { useCallback, useEffect, useMemo, useState } from "react";
import ConsoleShell, { useConsoleUser } from "@/components/console/ConsoleShell";
import CornIcon from "@/components/CornIcon";

interface Plan {
  id: string;
  title: string;
  start_date: string;
  exam_date: string | null;
  total_days: number;
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
const label: React.CSSProperties = { fontSize: 12, color: "var(--text-2)", marginBottom: 6 };
const input: React.CSSProperties = {
  border: "1px solid var(--input-border)",
  borderRadius: 8,
  background: "var(--surface)",
  color: "var(--text-1)",
  padding: "8px 10px",
  fontSize: 14,
  outline: "none",
};
const btn: React.CSSProperties = {
  background: "var(--solid)",
  color: "var(--solid-text)",
  border: "none",
  borderRadius: 8,
  padding: "8px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
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
    return <p style={{ color: "var(--text-2)", fontSize: 13 }}>还没有分数记录，先在下面记一条。</p>;
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

export default function CetPage() {
  const me = useConsoleUser();
  const [ov, setOv] = useState<Overview | null>(null);
  const [vocab, setVocab] = useState<Vocab[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const [days, setDays] = useState("54");
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

  // 登录态守卫：没有 me 就不发请求（与全站约定一致，避免 401 刷屏）
  useEffect(() => {
    if (!me) return;
    void load();
  }, [me, load]);

  const createPlan = async () => {
    setBusy(true);
    setErr("");
    try {
      await jfetch("/api/cet/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: Number(days) || 54 }),
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "生成失败");
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

  return (
    <ConsoleShell
      active="cet"
      eyebrow="P7 · 四六级"
      title="四六级备考工作区"
      sub="计划可勾选、词卡按遗忘曲线复习、分数画成趋势。数据全部属于你本人。"
      actions={
        plan ? (
          <span style={{ fontSize: 12, color: "var(--text-2)" }}>
            考试日 {plan.exam_date ?? "未设置"} · 共 {plan.total_days} 天
          </span>
        ) : null
      }
    >
      {err ? (
        <div style={{ ...card, borderColor: "var(--red)", color: "var(--red-ink)", marginBottom: 16, fontSize: 13 }}>
          {err}
        </div>
      ) : null}

      {loading ? (
        <p style={{ color: "var(--text-2)" }}>正在加载…</p>
      ) : !plan ? (
        <div style={{ ...card, textAlign: "center", padding: "40px 20px" }}>
          <CornIcon name="globe" className="logo" />
          <h3 style={{ margin: "10px 0 6px", color: "var(--text-1)" }}>还没有备考计划</h3>
          <p style={{ color: "var(--text-2)", fontSize: 13, marginBottom: 16 }}>
            先生成一份计划：每天 5 个模块（听力 / 词汇 / 阅读 / 写作翻译 / 真题），之后每天勾选即可。
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center", alignItems: "center", flexWrap: "wrap" }}>
            <input
              aria-label="计划天数"
              value={days}
              onChange={(e) => setDays(e.target.value)}
              style={{ ...input, width: 90, textAlign: "center" }}
            />
            <span style={{ fontSize: 13, color: "var(--text-2)" }}>天</span>
            <button style={btn} onClick={createPlan} disabled={busy}>
              {busy ? "生成中…" : "生成备考计划"}
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          {/* 统计三卡 */}
          <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
            <div style={{ ...card, display: "flex", alignItems: "center", gap: 14 }}>
              <Ring percent={progress.percent} />
              <div>
                <div style={{ ...label, marginBottom: 2 }}>总进度</div>
                <b style={{ fontSize: 20, color: "var(--text-1)" }}>
                  {progress.done}/{progress.total}
                </b>
                <div style={{ fontSize: 12, color: "var(--text-2)" }}>已完成任务</div>
              </div>
            </div>
            <div style={card}>
              <div style={label}>连续学习</div>
              <b style={{ fontSize: 30, color: "var(--accent-ink)" }}>{ov?.streak ?? 0}</b>
              <div style={{ fontSize: 12, color: "var(--text-2)" }}>天（有勾选即算）</div>
            </div>
            <div style={card}>
              <div style={label}>今日待复习</div>
              <b style={{ fontSize: 30, color: "var(--accent-ink)" }}>{ov?.vocab_due ?? 0}</b>
              <div style={{ fontSize: 12, color: "var(--text-2)" }}>个词卡到期</div>
            </div>
          </div>

          {/* 今日任务 */}
          <div style={card}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
              <b style={{ color: "var(--text-1)" }}>今日任务 · {ov?.today}</b>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                {ov?.today_tasks.filter((t) => t.done).length ?? 0}/{ov?.today_tasks.length ?? 0}
              </span>
            </div>
            {ov && ov.today_tasks.length === 0 ? (
              <p style={{ color: "var(--text-2)", fontSize: 13 }}>今天不在计划区间内（计划已结束或尚未开始）。</p>
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
                          fontSize: 12,
                          lineHeight: "16px",
                          textAlign: "center",
                          flex: "0 0 auto",
                        }}
                      >
                        {t.done ? "✓" : ""}
                      </span>
                      <span style={{ fontSize: 12, color: "var(--accent-ink)", flex: "0 0 auto" }}>{t.module}</span>
                      <span style={{ fontSize: 13, overflowWrap: "anywhere" }}>{t.title}</span>
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
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>1 / 3 / 7 / 15 / 30 天间隔</span>
            </div>
            {vocab.length === 0 ? (
              <p style={{ color: "var(--text-2)", fontSize: 13 }}>今天没有到期的词卡，先加几个词吧。</p>
            ) : (
              <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
                {vocab.map((v) => (
                  <div key={v.id} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12, background: "var(--surface)" }}>
                    <b style={{ fontSize: 16, color: "var(--text-1)", overflowWrap: "anywhere" }}>{v.word}</b>
                    <div style={{ fontSize: 12, color: "var(--text-2)", margin: "4px 0 10px", overflowWrap: "anywhere" }}>
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
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>共 {scores.length} 条</span>
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
    </ConsoleShell>
  );
}
