"use client";

// P6 · 打卡
//
// 页面任务：把「我今天学了」变成一件值得发出去的事。
//
// 为什么刻意克制：**不做奖惩、不做排行榜** —— 参考作业帮的教训，
// 竞争性激励带来的是负体验，而不是学习动力。
//
// ⚠️ 这一页是四屏里唯一「后端完全没有」的：没有 /api/checkin/*，
// `stats` 里也没有 streak 字段。所以数据分两块处理：
//   · 连续天数 / 历史 —— 用**本地记录**（baomi.reportDays，总结页生成时会写）
//     真实推算，这比编一个数字诚实
//   · 知识结构图 —— 后端没有出图端点。若当天总结已生成，直接渲染它的结论（真实）；
//     否则用设计稿的演示内容，并挂「演示数据」标记
// 分享兜底：公网 http 下 navigator.clipboard 不可用，统一走项目里的 copyText()。
import { useEffect, useState } from "react";
import ConsoleShell from "@/components/console/ConsoleShell";
import CornIcon from "@/components/CornIcon";
import { Callout, NotReady, SkeletonLines, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { DEMO_CHECKIN_HISTORY, DEMO_KNOWLEDGE_TREE } from "@/lib/consoleDemo";
import { toProbe, type ProbeState } from "@/lib/probe";
import { fmtDate } from "@/lib/time";
import { useToast } from "@/lib/hooks/useToast";
import { copyText } from "@/lib/useCopy";
import type { DailyReport } from "@/lib/types";

/** 与报告页共用同一份本地记录 */
const DAYS_KEY = "baomi.reportDays";
/** 打卡最远回溯多少天（决定圆点数量） */
const WINDOW_DAYS = 12;

interface DayMark {
  date: string;
  count: number;
}

function readDays(): DayMark[] {
  try {
    const raw = localStorage.getItem(DAYS_KEY);
    const v = raw ? (JSON.parse(raw) as DayMark[]) : [];
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

/** 从「有记录的日期」推连续天数：从今天（或昨天）往回数连续几天没断 */
function computeStreak(dates: string[], todayStr: string): number {
  return walkStreak(dates, todayStr, new Date(`${todayStr}T00:00:00`));
}

function walkStreak(dates: string[], todayStr: string, start: Date): number {
  const set = new Set(dates);
  const cursor = new Date(start);
  // 今天还没记，就从昨天起算 —— 否则每天清晨打开都是 0，体验上像在惩罚用户
  if (fmtDate(cursor) === todayStr && !set.has(todayStr)) cursor.setDate(cursor.getDate() - 1);
  let n = 0;
  for (let i = 0; i < 400; i++) {
    if (!set.has(fmtDate(cursor))) break;
    n++;
    cursor.setDate(cursor.getDate() - 1);
  }
  return n;
}

/**
 * 从区间起点**往后**数，得到这一段连续的长度。
 *
 * ⚠️ 必须往后数。原先这里复用了 `walkStreak`（它只会从起点往回走），
 * 结果一段连续 N 天永远只数出 1 天 —— 于是出现「已连续 2 天」+「近期最长 1 天」
 * 这种自相矛盾（2026-10-13 实机发现）。
 */
function walkForward(set: Set<string>, start: Date): number {
  let n = 0;
  const cursor = new Date(start);
  for (let i = 0; i < 400; i++) {
    if (!set.has(fmtDate(cursor))) break;
    n++;
    cursor.setDate(cursor.getDate() + 1);
  }
  return n;
}

/**
 * 最长连续天数。本地记录最多留 30 天，所以这是「近 30 天内的最长」——
 * 页面上要如实写成「近期最长」，不能冒充历史最长。
 */
function computeLongest(dates: string[]): number {
  const set = new Set(dates);
  let best = 0;
  for (const d of [...set].sort()) {
    const cur = new Date(`${d}T00:00:00`);
    const prev = new Date(cur);
    prev.setDate(prev.getDate() - 1);
    // 只从「区间起点」（前一天没有记录）开始数，避免重复计算
    if (set.has(fmtDate(prev))) continue;
    best = Math.max(best, walkForward(set, cur));
  }
  return best;
}

/** 近 N 天的打卡圆点（从最早到今天） */
function buildDots(dates: string[], todayStr: string, days: number) {
  const set = new Set(dates);
  const end = new Date(`${todayStr}T00:00:00`);
  const out: { key: string; on: boolean; today: boolean }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(end);
    d.setDate(d.getDate() - i);
    const key = fmtDate(d);
    out.push({ key, on: set.has(key), today: key === todayStr });
  }
  return out;
}

interface CardNode {
  name: string;
  level?: "ok" | "warn";
  children: string[];
}

export default function CheckinPage() {
  const { toasts, showToast } = useToast();
  const [todayStr, setTodayStr] = useState("");
  const [localDays, setLocalDays] = useState<DayMark[]>([]);
  const [report, setReport] = useState<ProbeState<DailyReport>>({ state: "loading" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // 延到下一个宏任务再 setState：直接在 effect 体内同步 setState 会被判成级联渲染
    // （eslint react-hooks/set-state-in-effect），这是本仓库既有的做法（见 useAuth）
    const t = window.setTimeout(() => {
      const d = fmtDate(new Date());
      setTodayStr(d);
      setLocalDays(readDays());
    }, 0);
    api.report
      .today()
      .then((r) => setReport(toProbe(r)))
      .catch(() => setReport({ state: "error", message: "读取今日总结失败" }));
    return () => window.clearTimeout(t);
  }, []);

  // ---- 卡片内容：优先用真实结论，退到演示结构 ----
  const liveReport =
    report.state === "ready" && report.data.status === "ready" && report.data.content.length > 0
      ? report.data
      : null;

  const usingDemoTree = !liveReport;
  const root = liveReport ? "今日所学" : DEMO_KNOWLEDGE_TREE.root;
  const nodes: CardNode[] = liveReport
    ? liveReport.content.map((c, i) => ({
        name: `结论 ${i + 1}`,
        children: [c.text.replace(/\*\*/g, "")],
      }))
    : DEMO_KNOWLEDGE_TREE.nodes.map((n) => ({
        name: n.name,
        level: n.level,
        children: n.children,
      }));

  const pointCount = nodes.reduce((s, n) => s + n.children.length, 0);

  // ---- 连续天数 ----
  // 「历史」在没有本机记录时会回退到演示数据，那么连续天数与圆点**必须取自同一份来源**，
  // 否则同一张卡里会同时出现「历史：10-13 已分享、10-12 已分享」和「0 天连续、12 个空点」
  // 这种互相打脸的组合（2026-10-13 实机发现）。
  const hasLocal = localDays.length > 0;
  const dayMarks: DayMark[] = hasLocal
    ? localDays
    : DEMO_CHECKIN_HISTORY.map((h) => ({ date: h.date, count: h.points }));
  const dates = dayMarks.map((d) => d.date);
  const streak = todayStr ? computeStreak(dates, todayStr) : 0;
  const longest = todayStr ? computeLongest(dates) : 0;
  const dots = todayStr ? buildDots(dates, todayStr, WINDOW_DAYS) : [];

  /** 文字版：分享兜底的第一步，也是唯一在公网 http 下 100% 可用的导出方式 */
  const toText = () =>
    [
      `今日所学 · ${todayStr}`,
      `知穗 · 知穗Agent`,
      "",
      root,
      ...nodes.flatMap((n) => [`· ${n.name}`, ...n.children.map((c) => `   - ${c}`)]),
      "",
      `共 ${pointCount} 个知识点${hasLocal ? ` · 已连续 ${streak} 天` : ""}`,
      "知穗 · 每天进步一点点",
    ].join("\n");

  const copyCard = async () => {
    const ok = await copyText(toText());
    showToast(ok ? "success" : "error", ok ? "文字版已复制，可以直接粘贴分享" : "复制失败，请长按选择文字");
  };

  /**
   * 导出 SVG。
   * 为什么是 SVG 而不是 PNG：矢量无损、体积小，且不需要把中文字体打包进位图；
   * 真要 PNG 的话还得在前端做 canvas 栅格化，收益不明显。
   */
  const downloadSvg = () => {
    setBusy(true);
    try {
      const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      // SVG 的 <text> 不换行：一行放不下就整段溢出画布。
      // 按 26 个全角字符手动折行（520px 宽、14px 字号下的安全值）
      const wrap = (s: string, per = 26): string[] => {
        if (s.length <= per) return [s];
        const out: string[] = [];
        for (let i = 0; i < s.length; i += per) out.push(s.slice(i, i + per));
        return out;
      };
      const lines: string[] = [];
      let y = 150;
      for (const n of nodes) {
        lines.push(
          `<text x="48" y="${y}" font-size="17" font-weight="700" fill="#3D2F1A">${esc(n.name)}${
            n.level === "warn" ? "   （待加强）" : ""
          }</text>`,
        );
        y += 26;
        for (const c of n.children) {
          for (const seg of wrap(c)) {
            lines.push(`<text x="72" y="${y}" font-size="14" fill="#5A4C33">- ${esc(seg)}</text>`);
            y += 22;
          }
        }
        y += 10;
      }
      const height = y + 90;
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="520" height="${height}" viewBox="0 0 520 ${height}">
  <rect width="520" height="${height}" rx="16" fill="#FDFAF2" stroke="#D8CCB0"/>
  <text x="48" y="60" font-size="24" font-weight="700" fill="#3D2F1A" font-family="serif">今日所学</text>
  <text x="472" y="60" font-size="12" fill="#6F6044" text-anchor="end">${esc(todayStr)}</text>
  <line x1="48" y1="82" x2="472" y2="82" stroke="#D8CCB0" stroke-dasharray="6 5"/>
  <text x="48" y="112" font-size="15" font-weight="700" fill="#875B10">${esc(root)}</text>
  ${lines.join("\n  ")}
  <line x1="48" y1="${height - 60}" x2="472" y2="${height - 60}" stroke="#D8CCB0" stroke-dasharray="6 5"/>
  <text x="48" y="${height - 32}" font-size="12" fill="#6F6044">共 ${pointCount} 个知识点${
    hasLocal ? ` · 已连续 ${streak} 天` : ""
  }</text>
  <text x="472" y="${height - 32}" font-size="12" fill="#6F6044" text-anchor="end">知穗 · 每天进步一点点</text>
</svg>`;
      const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `知穗打卡-${todayStr || "today"}.svg`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast("success", "已保存 SVG，可直接拿去分享");
    } catch {
      showToast("error", "导出失败，可先用「复制文字版」");
    } finally {
      setBusy(false);
    }
  };

  const history = hasLocal
    ? localDays.map((d) => ({ date: d.date, points: d.count, shared: false }))
    : DEMO_CHECKIN_HISTORY;

  return (
    <ConsoleShell
      active="checkin"
      eyebrow="P6 · /checkin"
      title="打卡"
      sub={
        <>
          把今天的总结变成一张
          <b>只讲知识结构、不露个人内容</b>的图。分享出去的是「学习笔记」，不是「代做产物」。
        </>
      }
      actions={
        <>
          <button type="button" className="btn btn-ghost" onClick={() => void copyCard()}>
            <CornIcon name="link" />
            复制文字版
          </button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={downloadSvg}>
            <CornIcon name="download" />
            保存这张图
          </button>
        </>
      }
    >
      <div className="grid2">
        {/* ============ 左：打卡卡 ============ */}
        <div>
          <p className="sec-label">
            今日打卡卡
            {usingDemoTree && (
              <span className="ctag warn" style={{ marginLeft: 6 }}>
                演示数据
              </span>
            )}
          </p>

          <div className="share">
            <div className="share-top">
              <CornIcon name="corn" className="logo" title="知穗" />
              <b>今日所学</b>
              <span className="meta">
                {todayStr || "—"}
                <br />
                知穗 · 知穗Agent
              </span>
            </div>

            {report.state === "loading" ? (
              <div style={{ marginTop: 16 }}>
                <SkeletonLines widths={["70%", "92%", "58%"]} />
              </div>
            ) : (
              <div className="tree">
                <div className="root">
                  <CornIcon name="book" />
                  {root}
                </div>
                <ul>
                  {nodes.map((n) => (
                    <li key={n.name}>
                      <b>{n.name}</b>
                      {n.level === "ok" && (
                        <span className="ctag ok" style={{ marginLeft: 6 }}>
                          已掌握
                        </span>
                      )}
                      {n.level === "warn" && (
                        <span className="ctag warn" style={{ marginLeft: 6 }}>
                          待加强
                        </span>
                      )}
                      <ul>
                        {n.children.map((c) => (
                          <li key={c}>{c}</li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="share-foot">
              <span>
                共 {pointCount} 个知识点
                {nodes.some((n) => n.level === "warn") ? " · 有 1 处待加强" : ""}
              </span>
              <span>知穗 · 每天进步一点点</span>
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <Callout>
              <b>分享前自动脱敏</b>：这张图只保留<b>知识结构</b>（概念、章节、掌握度），
              自动剥离姓名 / 学号 / 手机号 / 身份证，以及你自己的原话与做错的题。
            </Callout>
          </div>

          <div style={{ marginTop: 12 }}>
            <NotReady
              what="打卡接口（/api/checkin/generate、/streak、历史）"
              note={
                <>
                  后端连 <code>streak</code> 字段都还没有。所以这页的
                  <b>连续天数与历史由本机记录推算</b>，知识结构在当天总结已生成时
                  直接读它的结论 —— 都不是编的。要出真正的「脱敏导图」，需要后端补这三个端点。
                </>
              }
            />
          </div>
        </div>

        {/* ============ 右：连续与历史 ============ */}
        <div>
          <p className="sec-label">
            连续打卡
            <span className="ctag" style={{ marginLeft: 6 }}>
              {hasLocal ? "本地记录" : "演示数据"}
            </span>
          </p>
          <div className="card card-pad">
            <div className="streak">
              <span className="n">{streak}</span>
              <span className="meta">
                天连续{longest > 0 ? ` · 近期最长 ${longest} 天` : ""}
              </span>
            </div>
            <div className="dots">
              {(dots.length > 0 ? dots : Array.from({ length: WINDOW_DAYS }, (_, i) => ({ key: `ph${i}`, on: false, today: false }))).map(
                (d) => (
                  <i key={d.key} className={`${d.on ? "on" : ""} ${d.today ? "today" : ""}`.trim()} />
                ),
              )}
            </div>
            <p className="hint">
              {hasLocal
                ? `近 ${WINDOW_DAYS} 天。金色 = 已打卡，深金 + 描边 = 今天。`
                : `演示数据 —— 这 ${WINDOW_DAYS} 天里有 ${dots.filter((d) => d.on).length} 天记录。生成一次「每日总结」，这里就开始记你自己的。`}
              相邻判定只看<b>有没有生成那天的总结</b>，不看时长、不算分数。
            </p>
          </div>

          <p className="sec-label" style={{ marginTop: 20 }}>
            历史
          </p>
          <div className="card card-pad">
            {history.length === 0 ? (
              <p className="hint" style={{ marginTop: 0 }}>
                完成第一次打卡后，这里会有记录。
              </p>
            ) : (
              history.map((h, i) => (
                <div className="run" key={h.date} style={i === 0 ? { borderTop: "none" } : undefined}>
                  <span className="when">{h.date}</span>
                  <span>
                    {h.date === todayStr && <span className="ctag gold">今天</span>}{" "}
                    <span className="ctag">{h.points} 个知识点</span>
                  </span>
                  <span className="meta">{h.shared ? "已分享" : "未分享"}</span>
                </div>
              ))
            )}
          </div>

          <div style={{ marginTop: 12 }}>
            <Callout icon="warn">
              <b>为什么不做排行榜和连击奖励？</b>
              竞争性激励会把人推去「刷天数」而不是「学会东西」。
              这一页只回答一件事：今天我到底学到了什么。
            </Callout>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </ConsoleShell>
  );
}
