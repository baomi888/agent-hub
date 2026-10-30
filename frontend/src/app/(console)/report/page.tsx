"use client";

// P5 · 每日总结
//
// 页面任务：一天结束时，用 30 秒看懂「今天学了什么、哪里还虚」。
//
// 两条硬约束决定了数据层：
//   ① **每条结论必须能点回原文**。后端对结论做强制溯源：`source_refs` 指不到
//      真实消息的结论直接丢弃。所以这里渲染出来的条数可能少于模型自己说的条数 ——
//      这是「宁可少说，不许瞎说」，不是 bug。
//   ② **404 不是错误**。「今日总结尚未生成」要渲染成「还没有小结 + 生成按钮」。
//
// 2026-10-30 整页重排（用户原话："AI 味太重了，学一下其他 app 的每日总结"）。
// 上一版把这些东西直接摊在页面上：四格统计 + 柱状图 + 右栏"状态是怎么来的（ready /
// empty / generating / 404）"+ 一段讲 source_refs 怎么丢结论的说明 + 一句"后端目前
// 没有历史列表端点，所以这份清单先由本机记录"。这些是**实现者的注脚**，
// 看总结的人只需要知道"今天学了什么"。
// 现在的顺序是：日期条 → 一句人话 → 三个数字 → 小结卡片 → 一行可折叠的出处说明。
// 桌面与手机共用这一套单列结构，只在字号与内边距上分档。
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import MascotLogo from "@/components/chat/MascotLogo";
import { Callout, NotReady, SkeletonLines, ToastHost } from "@/components/console/ui";
import { BusinessNotFoundError, api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { DEMO_REPORT } from "@/lib/consoleDemo";
import { errProbe, toProbe, type ProbeState } from "@/lib/probe";
import { fmtDate, fmtNum, fmtWhen } from "@/lib/time";
import { useToast } from "@/lib/hooks/useToast";
import { useCopy } from "@/lib/useCopy";
import type { DailyReport, ReportConclusion, ReportStats } from "@/lib/types";

/** 本地记「哪些天生成过总结」。后端没有历史列表端点，先由前端缓存 */
const DAYS_KEY = "baomi.reportDays";

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

function markDay(mark: DayMark): void {
  try {
    const rest = readDays().filter((d) => d.date !== mark.date);
    const next = [mark, ...rest].slice(0, 30);
    localStorage.setItem(DAYS_KEY, JSON.stringify(next));
  } catch {
    /* 记不住不影响主流程 */
  }
}

const today = () => new Date();

/** 报告页的加载态：除常规四态外，多一个「业务 404 = 今天还没生成」 */
type ReportState = ProbeState<DailyReport> | { state: "not-generated" };

function RichText({ text }: { text: string }) {
  // 后端返回的是纯文本（可能带 **强调**），不解析完整 markdown —— 小结是短句，
  // 上整套 markdown 渲染器只会带来 XSS 面
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> : <span key={i}>{p}</span>,
      )}
    </>
  );
}

const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** "2026-10-30" → "10 月 30 日 · 周五"。
 *  不走 Date 的 toLocaleDateString：中英文环境和时区都会让它变成两种样子，
 *  而这里的输入本来就是本地日期字符串，手工拼最稳。 */
function prettyDate(d: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (!m) return d;
  const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return `${Number(m[2])} 月 ${Number(m[3])} 日 · ${WEEK[dt.getDay()]}`;
}

/** 日期条上显示的短标签：今天 / 昨天 / 10-28 */
function shortDay(d: string, todayStr: string): string {
  if (d === todayStr) return "今天";
  const cur = /^(\d{4})-(\d{2})-(\d{2})$/.exec(todayStr);
  const it = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (cur && it) {
    const a = new Date(Number(cur[1]), Number(cur[2]) - 1, Number(cur[3])).getTime();
    const b = new Date(Number(it[1]), Number(it[2]) - 1, Number(it[3])).getTime();
    // 只差一整天时算「昨天」。日期差用毫秒除算再取整，跨月也能对上
    if (Math.round((a - b) / 86400000) === 1) return "昨天";
  }
  return d.slice(5);
}

export default function ReportPage() {
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;
  // 日期一律在挂载后再定：服务端与客户端各自的时区/时刻可能不同，
  // 首帧就渲染日期会有水合不一致的风险（跨零点时尤为明显）
  const [todayStr, setTodayStr] = useState("");
  const [date, setDate] = useState("");
  const [res, setRes] = useState<ReportState>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState<DayMark[]>([]);
  const { copy } = useCopy();
  const { toasts, showToast } = useToast();

  useEffect(() => {
    // 放到下一个宏任务：直接在 effect 体内 setState 会被判成级联渲染
    // （eslint react-hooks/set-state-in-effect），这是本仓库既有的做法（见 useAuth）
    const t = window.setTimeout(() => {
      const d = fmtDate(today());
      setTodayStr(d);
      setDate(d);
      setDays(readDays());
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  const load = useCallback((d: string) => {
    if (!d) return;
    setRes({ state: "loading" });
    api.report
      .byDate(d)
      .then((r) => setRes(toProbe(r)))
      .catch((e: unknown) => {
        // 业务 404：不是错误，是「今天还没生成」
        if (e instanceof BusinessNotFoundError) setRes({ state: "not-generated" });
        else setRes(errProbe(e));
      });
  }, []);

  useEffect(() => {
    // 未登录不发请求：登录前发的请求必然是 401，且登录后不会自动重来。
    // 登录成功会经 AUTHORIZED_EVENT 同步进 auth.me（见 useAuth / api.ts）→ 依赖变化补加载
    if (!authedId) return;
    if (!date) return;
    const t = window.setTimeout(() => load(date), 0);
    return () => window.clearTimeout(t);
  }, [authedId, date, load]);

  const generate = async () => {
    setBusy(true);
    try {
      const r = await api.report.generate(date);
      if (r) {
        setRes({ state: "ready", data: r });
        const count = r.content?.length ?? 0;
        markDay({ date: r.date || date, count });
        setDays(readDays());
        showToast("success", count > 0 ? `整理好了，${count} 条` : "这一天没有可总结的内容");
      } else {
        // 接口没上线：这次点击不该假装成功
        setRes({ state: "absent" });
      }
    } catch (e) {
      setRes(errProbe(e));
    } finally {
      setBusy(false);
    }
  };

  const isDemo = res.state === "absent";
  const report = res.state === "ready" ? res.data : isDemo ? DEMO_REPORT : null;
  const stats: ReportStats | null = report?.stats ?? null;

  /** 生成中：后端先落 generating 再落 ready，前端用骨架兜住这 20–40 秒 */
  const generating = busy || report?.status === "generating" || report?.status === "pending";

  /**
   * 出处按钮实际做的是「复制」，不是跳转 —— 会话内定位需要后端给一条
   * 「按消息号打开某条对话」的深链，目前还没有。
   * 所以文案必须照着真实行为写（原先叫「回到原话」，点了却只弹一句"已复制"，
   * 用户会以为点错了）。一条小结有几处出处就一次全带上，省得并排两个一样的按钮。
   */
  const copyRef = async (text: string, ids: number[]) => {
    const refs = ids.length > 0 ? `（出自第 ${ids.join("、")} 句对话）` : "";
    const ok = await copy(`[今日总结 · ${date}] ${text}${refs}`);
    showToast(ok ? "success" : "error", ok ? "已复制这条小结和它的出处" : "复制失败，请长按选择");
  };

  const isEmpty = report?.status === "empty" && !generating;
  const notes = generating ? [] : (report?.content ?? []);
  const hasNotes = notes.length > 0;
  // 一整天没说话时，三格「0 / 0 / 0」和一排空柱子只是把空态撑大。
  // 这种情况下整块读数都不渲染，只留一句话 —— 空页面不该长得像仪表盘。
  const quiet = (stats?.message_count ?? 0) === 0;

  // 日期条：今天 + 本地记过的那几天（去重，最多 8 个，再多就真滑不完了）
  const dayList = (() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const d of [todayStr, ...days.map((x) => x.date)]) {
      if (!d || seen.has(d)) continue;
      seen.add(d);
      out.push(d);
    }
    return out.slice(0, 8);
  })();

  return (
    <PageShell
      eyebrow="每日总结"
      title={date && date !== todayStr ? prettyDate(date) : "今天学了什么"}
      sub="聊过的东西容易忘。每天留一份小结，带出处。"
      width={860}
      actions={
        <button
          type="button"
          className="btn btn-primary"
          disabled={generating || !date}
          onClick={() => void generate()}
        >
          <CornIcon name="refresh" />
          {generating ? "正在整理…" : hasNotes || isEmpty ? "重新整理这一天" : "整理这一天"}
        </button>
      }
    >
      {res.state === "error" && (
        <Callout icon="warn">
          这一天的内容读不到：{res.message}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            style={{ marginLeft: 10 }}
            onClick={() => load(date)}
          >
            重试
          </button>
        </Callout>
      )}

      {/* 日期条 —— 手机上是横滑的胶囊，桌上也就几颗。
          以前这一列挂在右栏，手机上要滚到页面最底部才能换日期。 */}
      {dayList.length > 1 && (
        <div className="rp-days" role="tablist" aria-label="选择日期">
          {dayList.map((d) => (
            <button
              key={d}
              type="button"
              role="tab"
              className="rp-day"
              aria-current={date === d ? "true" : undefined}
              onClick={() => setDate(d)}
            >
              {shortDay(d, todayStr)}
            </button>
          ))}
        </div>
      )}

      <div style={{ marginTop: 14 }}>
        {isDemo && (
          <div style={{ marginBottom: 12 }}>
            <NotReady
              what="每日总结接口（/api/report，3 个端点）"
              note="下面是设计稿里的示例内容，「整理」按钮不会真的调用模型。"
            />
          </div>
        )}

        {res.state === "loading" ? (
          <div className="card card-pad">
            <SkeletonLines widths={["100%", "72%", "88%", "46%"]} />
          </div>
        ) : (
          <>
            {/* ---- 概览：一句话 + 三个数字 ---- */}
            <section className="card rp-hero">
              <p className="rp-hero-kicker">{prettyDate(date) || "今天"}</p>
              <h3 className="rp-hero-line">{leadLine(stats)}</h3>

              {!quiet && (
                <>
                  <div className="rp-figs">
                    <div className="rp-fig">
                      <b>{fmtNum(stats?.conversation_count ?? 0)}</b>
                      <span>段对话</span>
                    </div>
                    <div className="rp-fig">
                      <b>{fmtNum(stats?.user_msg_count ?? 0)}</b>
                      <span>个问题</span>
                    </div>
                    <div className="rp-fig">
                      <b>{fmtNum(stats?.tool_call_count ?? 0)}</b>
                      <span>次查资料</span>
                    </div>
                  </div>

                  <div className="rp-hours-wrap">
                    <div className="rp-hours-head">
                      <span>什么时候在学</span>
                      <span className="rp-hours-hint">{peakHint(stats)}</span>
                    </div>
                    <HoursChart stats={stats} />
                  </div>
                </>
              )}
            </section>

            {/* ---- 小结 ---- */}
            <p className="rp-sec">
              今天的小结
              {hasNotes && <span className="rp-sec-n">{notes.length} 条</span>}
            </p>

            {generating ? (
              <div className="card card-pad">
                <SkeletonLines widths={["82%", "94%", "58%", "40%"]} />
                <p className="hint" style={{ color: "var(--accent-ink)" }}>
                  正在翻一遍今天的对话，大概半分钟…
                </p>
              </div>
            ) : hasNotes ? (
              <div>
                {notes.map((c: ReportConclusion, i) => (
                  <article className="rp-note" key={i}>
                    <span className="rp-no">{String(i + 1).padStart(2, "0")}</span>
                    <div className="rp-note-body">
                      <p>
                        <RichText text={c.text} />
                      </p>
                      {c.source_refs.length > 0 && (
                        <button
                          type="button"
                          className="rp-src"
                          title={`把这条小结和它出自的第 ${c.source_refs.join("、")} 句对话一起复制`}
                          onClick={() => void copyRef(c.text, c.source_refs)}
                        >
                          <CornIcon name="link" />
                          连出处一起复制
                          {c.source_refs.length > 1 && (
                            <i className="rp-src-n">{c.source_refs.length}</i>
                          )}
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="card rp-empty">
                <MascotLogo className="rp-empty-logo" />
                <b>{isEmpty ? "这天没聊过天" : "还没有这一天的小结"}</b>
                <p>
                  {isEmpty
                    ? "没有对话就没有可整理的东西 —— 这天也不会消耗额度。随便问两句，明天再来就有得看了。"
                    : "点下面的按钮，我把这一天问过的问题压成几条，顺手标好出处。"}
                </p>
                {isEmpty ? (
                  <Link href="/" className="btn btn-ghost btn-sm" style={{ marginTop: 2 }}>
                    去聊两句
                  </Link>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={busy}
                    onClick={() => void generate()}
                  >
                    现在就整理
                  </button>
                )}
              </div>
            )}

            {/* 出处说明收进折叠 —— 它回答的是"凭什么信"，不是每天都要读的东西 */}
            <details className="rp-foot">
              <summary>条数为什么比看起来的少？</summary>
              <p>
                每一条我都要求能指回原话：找不到出处的，宁可不写也不硬凑。
                所以这里的条数常常少于当天实际的收获 —— 这是刻意的。
                {report?.generated_at ? ` 这份小结生成于 ${fmtWhen(report.generated_at)}。` : ""}
              </p>
            </details>
          </>
        )}
      </div>

      <ToastHost toasts={toasts} />
    </PageShell>
  );
}

/* ---------------- 概览那一句话 ---------------- */

/** 把三个数字说成一句话。
 *  数字留给下面的三格，这一行只负责"今天大概什么样"，
 *  所以宁可短：写完读一遍，像不像自己会跟室友说的话。 */
function leadLine(stats: ReportStats | null): string {
  // stats 为 null 不等于"今天没聊过"：这一天还没生成小结时后端只回 404，
  // 前端手上一个计数都没有。此时只能老实说"还没整理" —— 原来这里直接说
  // 「今天还没开始」，用户明明聊了一整天，打开总结页却被判定成没动静。
  if (!stats) return "今天还没整理。";
  if (stats.message_count === 0) return "今天还没开始，聊两句就有东西可记了";
  const q = stats.user_msg_count;
  const c = stats.conversation_count;
  const t = stats.tool_call_count;
  const head = c > 1 ? `换了 ${c} 段对话，问了 ${q} 个问题` : `一口气问了 ${q} 个问题`;
  if (t > 0) return `${head}，顺手查了 ${t} 次资料`;
  return head;
}

/* ---------------- 活跃时段 ---------------- */

/** 最多 12 根柱子的窗口：从最早有记录的小时起算，保证小屏也读得出来 */
function HoursChart({ stats }: { stats: ReportStats | null }) {
  const hours = stats?.active_hours ?? {};
  const keys = Object.keys(hours)
    .map(Number)
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);

  if (keys.length === 0) {
    return <p className="hint">今天还没有记录</p>;
  }

  const from = keys[0];
  const slots = Array.from({ length: Math.min(12, Math.max(keys[keys.length - 1] - from + 1, 1)) }, (_, i) => {
    const h = from + i;
    return { h, v: hours[String(h)] ?? 0 };
  });
  const max = Math.max(1, ...slots.map((s) => s.v));
  // 列数交给 CSS 变量：柱子列与标签列用同一套模板才会一一对齐（见 summary.css）
  const cols = { "--slots": slots.length } as CSSProperties;

  return (
    <>
      <div className="rp-hours" style={cols}>
        {slots.map((s) => (
          <i
            key={s.h}
            className={s.v === 0 ? "zero" : undefined}
            style={{ height: `${Math.max(4, Math.round((s.v / max) * 100))}%` }}
            title={`${s.h}:00 · ${s.v} 条`}
          />
        ))}
      </div>
      <div className="rp-hours-x" style={cols}>
        {slots.map((s) => (
          <span key={s.h}>{s.h}</span>
        ))}
      </div>
    </>
  );
}

/** 用一句话说清「集中在哪」—— 一排柱子看完还是不知道结论，就白画了 */
function peakHint(stats: ReportStats | null): string {
  const hours = stats?.active_hours ?? {};
  const top = Object.entries(hours)
    .map(([h, v]) => [Number(h), v] as const)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2);
  if (top.length === 0) return "暂无数据";
  if (top.length === 1) return `集中在 ${top[0][0]}:00`;
  return `集中在 ${top[0][0]}:00 与 ${top[1][0]}:00 两段`;
}
