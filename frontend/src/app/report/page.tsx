"use client";

// P5 · 每日总结
//
// 页面任务：一天结束时，用 30 秒看懂「今天学了什么、哪里还虚」。
//
// 两条硬约束决定了这页长什么样：
//   ① **每条结论必须能点回原文**。后端对结论做强制溯源：`source_refs` 指不到
//      真实消息的结论直接丢弃。所以这里渲染出来的条数可能少于模型自己说的条数 ——
//      这是「宁可少说，不许瞎说」，不是 bug。
//   ② **404 不是错误**。「今日总结尚未生成」要渲染成「今天还没有总结 + 生成按钮」。
//
// 关于状态：后端 4 个取值 pending / generating / ready / empty，
// 其中 empty = 当天没对话，**不调模型、不烧额度** —— 这是个免费的留存话术，
// 该说的是「聊两句就有总结了」而不是甩一个空白错误页。
import { useCallback, useEffect, useState } from "react";
import ConsoleShell from "@/components/console/ConsoleShell";
import CornIcon from "@/components/CornIcon";
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
  // 后端返回的是纯文本（可能带 **强调**），不解析完整 markdown —— 结论是短句，
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

  const copyRef = async (text: string, msgId: number) => {
    const ok = await copy(`[今日总结 · ${date}] ${text}（第 ${msgId} 句对话）`);
    // 会话内定位需要后端提供「按消息号跳转」的深链，目前还没有；
    // 先做到「把这条引用连原文一起带走」，比一个点了没反应的角标有用
    showToast(ok ? "success" : "error", ok ? "已复制这条结论与它的出处" : "复制失败，请长按选择");
  };

  return (
    <ConsoleShell
      active="report"
      eyebrow="学完回头看"
      title="每日总结"
      sub={
        <>
          把今天问过的问题压成几条结论。
          <b>每条结论都能点回它来自哪一句对话</b> —— 指不回原文的，系统直接丢掉不显示。
        </>
      }
      actions={
        <>
          <span className="sched">
            <CornIcon name="calendar" />
            {date}
          </span>
          <button
            type="button"
            className="btn btn-primary"
            disabled={generating}
            onClick={() => void generate()}
          >
            <CornIcon name="refresh" />
            {generating ? "正在生成…" : "生成今天的总结"}
          </button>
        </>
      }
    >
      {res.state === "error" && (
        <Callout icon="warn">
          总结读不到：{res.message}
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

      {(res.state === "loading" || res.state === "ready" || isDemo || res.state === "not-generated") && (
        <div className="grid2">
          {/* ============ 左：统计 + 结论 ============ */}
          <div>
            {isDemo && (
              <div style={{ marginBottom: 14 }}>
                <NotReady
                  what="每日总结接口（/api/report，3 个端点）"
                  note="下面展示的是设计稿的演示内容，「生成」按钮不会真的调用模型。"
                />
              </div>
            )}

            {res.state === "not-generated" && !busy && (
              <div style={{ marginBottom: 14 }}>
                <Callout icon="bulb">
                  <b>这一天还没有总结。</b>
                  点右上角「生成今天的总结」就行 —— 大概要等 20–40 秒，因为要真的过一遍当天的对话。
                </Callout>
              </div>
            )}

            {res.state === "loading" ? (
              <div className="card card-pad">
                <SkeletonLines widths={["100%", "80%", "62%"]} />
              </div>
            ) : (
              <>
                {/* ---- 统计：确定性数字，不走模型 ---- */}
                <div className="grid4">
                  <div className="stat">
                    <div className="n">{stats?.conversation_count ?? 0}</div>
                    <div className="l">对话</div>
                  </div>
                  <div className="stat">
                    <div className="n">{stats?.message_count ?? 0}</div>
                    <div className="l">消息</div>
                  </div>
                  <div className="stat">
                    <div className="n">
                      {stats ? `${stats.user_msg_count} / ${stats.assistant_msg_count}` : "0 / 0"}
                    </div>
                    <div className="l">提问 / 回答</div>
                  </div>
                  <div className="stat">
                    <div className="n">{stats?.tool_call_count ?? 0}</div>
                    <div className="l">工具调用</div>
                  </div>
                </div>

                <div className="card card-pad" style={{ marginTop: 12 }}>
                  <div className="card-head">
                    <span className="card-title">活跃时段</span>
                    <span className="meta">{peakHint(stats)}</span>
                  </div>
                  <HoursChart stats={stats} />
                </div>

                {/* ---- 结论 ---- */}
                <p className="sec-label" style={{ marginTop: 22 }}>
                  今天的结论
                  {report && report.status === "ready" && (
                    <span className="ctag" style={{ marginLeft: 6 }}>
                      {report.content.length} 条 · 已全部可溯源
                    </span>
                  )}
                  {isDemo && (
                    <span className="ctag warn" style={{ marginLeft: 6 }}>
                      演示数据
                    </span>
                  )}
                </p>

                {report?.status === "empty" ? (
                  <div className="card card-pad">
                    <Callout icon="bulb">
                      <b>今天还没有对话，所以没有可总结的内容。</b>
                      这天不会调模型，也就不消耗额度 —— 随便聊两句，明天再来就有东西看了。
                    </Callout>
                  </div>
                ) : generating ? (
                  <div className="card card-pad">
                    <div className="card-title">生成中</div>
                    <SkeletonLines widths={["80%", "92%", "60%", "44%"]} />
                    <p className="hint" style={{ color: "var(--accent-ink)" }}>
                      正在生成今天的总结（约 30 秒）…
                    </p>
                  </div>
                ) : report && report.content.length > 0 ? (
                  <div className="card card-pad">
                    {report.content.map((c: ReportConclusion, i) => (
                      <div className="concl" key={i}>
                        <span className="idx">{i + 1}</span>
                        <div>
                          <p>
                            <RichText text={c.text} />
                          </p>
                          <div className="refs">
                            {c.source_refs.map((id) => (
                              <button
                                key={id}
                                type="button"
                                className="ref"
                                title={`第 ${id} 句对话（点一下把这句的引用连同结论一起复制）`}
                                onClick={() => void copyRef(c.text, id)}
                              >
                                <CornIcon name="link" />
                                第 {id} 句
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="card card-pad">
                    <Callout icon="bulb">
                      <b>这一天还没有总结。</b>点右上角生成一份 ——
                      系统只会保留「能指回原话」的结论，所以条数可能比你想的少。
                    </Callout>
                  </div>
                )}

                <div style={{ marginTop: 12 }}>
                  <Callout>
                    <b>为什么有的结论不见了？</b>后端对每条结论做<b>强制溯源</b>：
                    <code>source_refs</code> 指不到真实消息的，直接丢弃并记 warning。
                    所以这里的条数可能少于模型自己说的条数 —— 这是「宁可少说，不许瞎说」。
                  </Callout>
                </div>
              </>
            )}
          </div>

          {/* ============ 右：日期 + 生成态说明 ============ */}
          <div>
            <p className="sec-label">
              其他日期
              <span className="ctag" style={{ marginLeft: 6 }}>
                本地记录
              </span>
            </p>
            <div className="card card-pad">
              <button
                type="button"
                className="run as-button"
                aria-current={date === todayStr}
                onClick={() => setDate(todayStr)}
              >
                <span className="when">{todayStr || "—"}</span>
                <span>
                  <span className="ctag gold">今天</span>
                </span>
                <span className="meta">{date === todayStr ? "查看中" : "查看"}</span>
              </button>
              {days.length === 0 ? (
                <p className="hint">还没有更早的记录。生成过的日期会记在这里。</p>
              ) : (
                days.map((d) => (
                  <button
                    type="button"
                    className="run as-button"
                    key={d.date}
                    aria-current={date === d.date}
                    onClick={() => setDate(d.date)}
                  >
                    <span className="when">{d.date}</span>
                    <span>
                      <span className="ctag">{d.count} 条结论</span>
                    </span>
                    <span className="meta">{date === d.date ? "查看中" : "查看"}</span>
                  </button>
                ))
              )}
            </div>
            <p className="hint">
              后端目前没有「历史列表 / 未读计数 / 标记已读」三个端点，所以这份日期清单
              先由本机记录。等端点补齐，换成服务端返回即可。
            </p>

            <div className="card card-pad" style={{ marginTop: 16 }}>
              <div className="card-title">状态是怎么来的</div>
              <div style={{ marginTop: 10 }}>
                <div className="run" style={{ gridTemplateColumns: "78px 1fr" }}>
                  <span className="ctag ok">ready</span>
                  <span className="meta">有结论，且全部可溯源</span>
                </div>
                <div className="run" style={{ gridTemplateColumns: "78px 1fr" }}>
                  <span className="ctag warn">empty</span>
                  <span className="meta">当天没对话，不调模型、不烧额度</span>
                </div>
                <div className="run" style={{ gridTemplateColumns: "78px 1fr" }}>
                  <span className="ctag info">generating</span>
                  <span className="meta">同步生成中，约 20–40 秒</span>
                </div>
                <div className="run" style={{ gridTemplateColumns: "78px 1fr" }}>
                  <span className="ctag">404</span>
                  <span className="meta">尚未生成 —— 转成「生成按钮」，不报错</span>
                </div>
              </div>
              {report?.generated_at && (
                <p className="hint">这份总结生成于 {fmtWhen(report.generated_at)}</p>
              )}
            </div>
          </div>
        </div>
      )}

      <ToastHost toasts={toasts} />
    </ConsoleShell>
  );
}

/* ---------------- 活跃时段 ---------------- */

/** 12 根柱子的窗口：从最早有记录的小时起算，最多 12 格，保证小屏也读得出来 */
function HoursChart({ stats }: { stats: ReportStats | null }) {
  const hours = stats?.active_hours ?? {};
  const keys = Object.keys(hours)
    .map(Number)
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);

  if (keys.length === 0) {
    return <p className="hint">今天还没有对话记录，所以没有时段分布。</p>;
  }

  const from = keys[0];
  const slots = Array.from({ length: Math.min(12, Math.max(keys[keys.length - 1] - from + 1, 1)) }, (_, i) => {
    const h = from + i;
    return { h, v: hours[String(h)] ?? 0 };
  });
  const max = Math.max(1, ...slots.map((s) => s.v));

  return (
    <>
      <div className="hours">
        {slots.map((s) => (
          <i
            key={s.h}
            className={s.v === 0 ? "zero" : undefined}
            style={{ height: `${Math.max(4, Math.round((s.v / max) * 100))}%` }}
            title={`${s.h}:00 · ${s.v} 条`}
          />
        ))}
      </div>
      <div className="hours-x">
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
  return `集中在 ${top[0][0]}:00 与 ${top[1][0]}:00 两段 · 共 ${fmtNum(
    Object.values(hours).reduce((s, v) => s + v, 0),
  )} 条`;
}
