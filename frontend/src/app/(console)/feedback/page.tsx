"use client";

// 用户反馈后台（管理员）。
//
// ★ 2026-11-18 第二次改形：从"按条平铺的工单列表"改成**按人会话**。
//   旧形态的问题在 `core/user_feedback.py` 的「按人会话」注释里写清楚了：
//   同一个人的 8 条抱怨被摊成 8 行，回复散在 8 个地方，既读不出"这是一个人
//   在连续说"，也没法把话说完。现在是左列表（谁在说）+ 右对话（我和他）。
//
// ★ 工时列表没有丢：`/api/admin/feedback` 与导出仍在（导出按月汇总要用），
//   只是这个页面不再展示它 —— 页面只做"跟人对话"这一件事。
//
// ★ 回复语义没变：**只追加，不能修改、不能撤回**（`add_reply`）。
//   多个管理员共用这个后台，谁都能改别人的话就没人敢说话；说错了再补一条。
//
// ★ 图表三张不是装饰：分类分布回答"大家在抱怨什么"，状态积压回答"手上还欠
//   多少"，逐日趋势回答"量在涨还是在落"。三个口径刻意不同，标题上都标了。
//   数据量少时（刚上线）它们会显得空 —— 空状态用虚线框说明，不假装有数据。
import { notFound } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { Callout, Seg, SkeletonLines } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import type {
  FeedbackDict,
  FeedbackStatsItem,
  FeedbackStatsResp,
  FeedbackStatus,
  FeedbackThreadResp,
  FeedbackThreadsResp,
} from "@/lib/types";

const STATUS_CLASS: Record<string, string> = {
  open: "warn",
  doing: "info",
  done: "ok",
  closed: "",
};

const STATUS_ORDER: FeedbackStatus[] = ["open", "doing", "done", "closed"];

/** 图表统计窗口。反馈量小，30 天比 7 天更能看出趋势。 */
const STATS_DAYS = 30;

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 列表里的时间：今天只写时分，今年只写月日，跨年才带年 —— 省宽度。 */
function fmtWhen(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return `${p(d.getHours())}:${p(d.getMinutes())}`;
  if (d.getFullYear() === now.getFullYear()) return `${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 头像：有图用图，没图用首字。不引组件库 —— 这里只有一种尺寸。 */
function Face({ name, url, size = 36 }: { name: string; url?: string; size?: number }) {
  const ch = (name || "?").trim().slice(0, 1);
  if (url) {
    return (
      <img
        className="fbim-face"
        src={url}
        alt=""
        style={{ width: size, height: size }}
        loading="lazy"
      />
    );
  }
  return (
    <span className="fbim-face" style={{ width: size, height: size }} aria-hidden="true">
      {ch}
    </span>
  );
}

/**
 * 环形图（分类分布）。
 *
 * 为什么用 SVG 的 stroke-dasharray 而不是 conic-gradient：环形要有间隔感、
 * 要在段数为 1 时仍然像个环，conic 配 mask 在 iOS 上偶发锯齿。这里段数最多 5，
 * 五个 circle 叠着画最稳。
 *
 * 颜色只用一个色相、靠透明度分层：五个不同色相在这个看板里既没有语义，
 * 深色模式下还会糊成一片（与「调用量构成」的堆叠条同一套理由）。
 */
function Donut({ items }: { items: FeedbackStatsItem[] }) {
  const R = 44;
  const C = 2 * Math.PI * R;
  const sum = items.reduce((a, b) => a + b.n, 0);
  const total = sum || 1;
  let acc = 0;
  return (
    <div className="fbim-donut">
      <svg viewBox="0 0 110 110" role="img" aria-label="反馈分类分布">
        <circle cx="55" cy="55" r={R} fill="none" stroke="var(--fill-3)" strokeWidth="12" />
        {items.map((it, idx) => {
          if (it.n <= 0) return null;
          const len = (it.n / total) * C;
          // 段与段之间留 1.5px 的缝，否则相邻两段同色系会黏成一体
          const draw = Math.max(0, len - 1.5);
          const el = (
            <circle
              key={it.value}
              cx="55"
              cy="55"
              r={R}
              fill="none"
              stroke="var(--accent)"
              strokeOpacity={Math.max(0.45, 1 - idx * 0.14)}
              strokeWidth="12"
              strokeDasharray={`${draw} ${C - draw}`}
              strokeDashoffset={-acc}
              transform="rotate(-90 55 55)"
            />
          );
          acc += len;
          return el;
        })}
      </svg>
      <div className="fbim-donut-c">
        <b>{sum}</b>
        <span>区间内</span>
      </div>
    </div>
  );
}

/**
 * 逐日折线（近 30 天新增）。
 *
 * 反馈量以"个位数"计，折线比柱状更能表达"哪天冒了一条"。面积用同一色的
 * 极淡填充，让 0 基线看得出来 —— 全是 0 时它就是一条贴着底部的直线。
 */
function Spark({ series }: { series: { day: string; n: number }[] }) {
  const W = 300;
  const H = 66;
  const max = Math.max(1, ...series.map((s) => s.n));
  const n = series.length;
  const pts = series.map((s, i) => {
    const x = n <= 1 ? W / 2 : (i / (n - 1)) * W;
    const y = H - 4 - (s.n / max) * (H - 12);
    return [x, y] as const;
  });
  const line = pts
    .map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`)
    .join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  return (
    <div className="fbim-spark">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="每日新增反馈">
        <path d={area} fill="var(--accent)" fillOpacity="0.10" stroke="none" />
        <path
          d={line}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        {pts.map((p, i) =>
          series[i].n > 0 ? (
            <circle key={series[i].day} cx={p[0]} cy={p[1]} r="2.4" fill="var(--accent)" />
          ) : null
        )}
      </svg>
      <div className="fbim-spark-x">
        <span>{series[0]?.day.slice(5) ?? ""}</span>
        <span>{series[series.length - 1]?.day.slice(5) ?? ""}</span>
      </div>
    </div>
  );
}

/** 状态积压（横向条）。分母是全量，不是区间 —— 积压不分区间。 */
function StatusBars({ items }: { items: FeedbackStatsItem[] }) {
  const max = Math.max(1, ...items.map((i) => i.n));
  return (
    <div className="fbim-bars">
      {items.map((it) => (
        <div className="fbim-bar" key={it.value}>
          <span className="lb">{it.label}</span>
          <span className="track">
            <i style={{ width: `${(it.n / max) * 100}%` }} className={STATUS_CLASS[it.value] || ""} />
          </span>
          <span className="n">{it.n}</span>
        </div>
      ))}
    </div>
  );
}

export default function FeedbackPage() {
  const auth = useAuth();
  const isAdmin = Boolean(auth.me?.is_admin);
  const myUid = auth.me?.user.id ?? "";

  const [dict, setDict] = useState<FeedbackDict | null>(null);
  const [threads, setThreads] = useState<FeedbackThreadsResp | null>(null);
  const [stats, setStats] = useState<FeedbackStatsResp | null>(null);
  const [fbStatus, setFbStatus] = useState("");
  const [active, setActive] = useState("");
  const [thread, setThread] = useState<FeedbackThreadResp | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [exporting, setExporting] = useState(false);
  const msgsRef = useRef<HTMLDivElement | null>(null);

  const loadList = useCallback(() => {
    if (!isAdmin) return;
    api.admin
      .feedbackThreads(fbStatus)
      .then(setThreads)
      .catch((e) => {
        setThreads(null);
        setErr(e instanceof Error ? e.message : "读取失败");
      });
  }, [fbStatus, isAdmin]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (!isAdmin) return;
    api.admin
      .feedbackStats(STATS_DAYS)
      .then(setStats)
      .catch(() => setStats(null));
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) return;
    api.admin
      .dict()
      .then(setDict)
      .catch(() => setDict(null));
  }, [isAdmin]);

  const openThread = useCallback((uid: string) => {
    setActive(uid);
    setThread(null);
    setText("");
    api.admin
      .feedbackThread(uid)
      .then(setThread)
      .catch((e) => setErr(e instanceof Error ? e.message : "读取对话失败"));
  }, []);

  // 对话打开后贴底：IM 的默认阅读位置是"最新一句"，不是顶部
  useEffect(() => {
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread]);

  const send = useCallback(async () => {
    const t = text.trim();
    if (!t || !active || busy) return;
    setBusy(true);
    setErr("");
    try {
      const th = await api.admin.replyThread(active, t);
      setThread(th);
      setText("");
      // 回复会把 open 顺手推成 doing（后端 `add_reply`），所以列表与图表都得重拉
      loadList();
      api.admin
        .feedbackStats(STATS_DAYS)
        .then(setStats)
        .catch(() => undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "发送失败");
    } finally {
      setBusy(false);
    }
  }, [active, busy, loadList, text]);

  const exportAll = useCallback(async () => {
    setExporting(true);
    setErr("");
    try {
      await api.admin.exportFeedback(fbStatus);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }, [fbStatus]);

  const statusLabel = (s: string) => dict?.statuses.find((x) => x.value === s)?.label ?? s;
  const filterOptions = useMemo(
    () => [
      { value: "", label: "全部", hint: "不限状态" },
      ...STATUS_ORDER.map((s) => ({
        value: s,
        label: statusLabel(s),
        hint: `只看有${statusLabel(s)}反馈的用户`,
      })),
    ],
    // statusLabel 依赖 dict；把它展开成 dict 本身即可（否则每次渲染新建数组）
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dict]
  );

  if (auth.loading) return null;
  if (!isAdmin) notFound();

  // 会话头部的状态 = 最新一条反馈的状态（回复也落在那一条上）
  const lastUser = [...(thread?.messages ?? [])].reverse().find((m) => m.kind === "user");
  const curStatus = lastUser?.status ?? "";
  const nextStatuses = dict?.transitions[curStatus] ?? [];

  return (
    <PageShell
      eyebrow="管理 · 内部"
      title="用户反馈"
      actions={
        <>
          <Seg ariaLabel="按状态筛选" options={filterOptions} value={fbStatus} onChange={setFbStatus} />
          <button
            type="button"
            className="btn btn-primary btn-sm"
            style={{ marginLeft: "auto" }}
            disabled={exporting}
            onClick={() => void exportAll()}
            title="导出当前筛选下的全部反馈（Markdown，含处理记录，联系方式已脱敏）"
          >
            <CornIcon name="download" />
            {exporting ? "导出中…" : "一键导出"}
          </button>
        </>
      }
    >
      <div className="stack-20">
        {err ? <Callout icon="warn">{err}</Callout> : null}

        {/* ---- 四张状态卡：这是"手上还欠多少"的第一眼答案 ---- */}
        <div className="grid4">
          {STATUS_ORDER.map((s) => (
            <button
              type="button"
              className={`card card-pad stat fbim-kpi${fbStatus === s ? " on" : ""}`}
              key={s}
              onClick={() => setFbStatus(fbStatus === s ? "" : s)}
              title={`只看有${statusLabel(s)}反馈的用户`}
            >
              <div className="n">{threads ? (threads.counts[s] ?? 0) : "—"}</div>
              <div className="l">{statusLabel(s)}</div>
            </button>
          ))}
        </div>

        {/* ---- 三张图：分类 / 趋势 / 积压 ---- */}
        <div className="fbim-charts">
          <div className="card card-pad">
            <div className="dash-card-head">
              <h3>分类分布</h3>
              <span className="meta">近 {STATS_DAYS} 天新增</span>
            </div>
            {!stats ? (
              <SkeletonLines widths={["100%", "80%"]} />
            ) : stats.in_range === 0 ? (
              <div className="dash-empty">这段时间没有新反馈。</div>
            ) : (
              <>
                <Donut items={stats.by_category} />
                <ul className="fbim-legend">
                  {stats.by_category.map((it, idx) => (
                    <li key={it.value}>
                      <span
                        className="sw"
                        style={{
                          background: "var(--accent)",
                          opacity: Math.max(0.45, 1 - idx * 0.14),
                        }}
                      />
                      <span className="lb">{it.label}</span>
                      <span className="n">{it.n}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <div className="card card-pad">
            <div className="dash-card-head">
              <h3>每日新增</h3>
              <span className="meta">近 {STATS_DAYS} 天</span>
            </div>
            {!stats ? (
              <SkeletonLines widths={["100%", "80%"]} />
            ) : stats.in_range === 0 ? (
              <div className="dash-empty">这段时间没有新反馈。</div>
            ) : (
              <>
                <Spark series={stats.series} />
                <p className="meta" style={{ marginTop: 8 }}>
                  区间内 <b>{stats.in_range}</b> 条 · 累计 {stats.total_all} 条
                </p>
              </>
            )}
          </div>

          <div className="card card-pad">
            <div className="dash-card-head">
              <h3>状态积压</h3>
              <span className="meta">全量 {stats?.total_all ?? 0} 条</span>
            </div>
            {!stats ? (
              <SkeletonLines widths={["100%", "80%"]} />
            ) : stats.total_all === 0 ? (
              <div className="dash-empty">还没有任何反馈。</div>
            ) : (
              <StatusBars items={stats.by_status} />
            )}
          </div>
        </div>

        {/* ---- 主体：左会话列表 / 右对话 ---- */}
        <div className={`fbim card${active ? " has-thread" : ""}`}>
          <div className="fbim-list">
            <div className="fbim-list-head">
              <b>会话</b>
              <span className="meta">
                {threads ? `${threads.total} 人` : "—"}
                {fbStatus ? ` · ${statusLabel(fbStatus)}` : ""}
              </span>
            </div>
            {!threads ? (
              <div style={{ padding: 12 }}>
                <SkeletonLines widths={["90%", "70%", "80%"]} />
              </div>
            ) : threads.items.length === 0 ? (
              <p className="hint" style={{ padding: 12 }}>
                {fbStatus ? "这个状态下没有会话，切「全部」看看。" : "还没有反馈。"}
              </p>
            ) : (
              threads.items.map((it) => (
                <button
                  type="button"
                  key={it.uid}
                  className={`fbim-item${it.uid === active ? " on" : ""}`}
                  onClick={() => openThread(it.uid)}
                >
                  <Face name={it.name} url={it.avatar} />
                  <span className="fbim-item-main">
                    <span className="fbim-item-top">
                      <span className="nm">{it.name}</span>
                      <span className="tm">{fmtWhen(it.last_at)}</span>
                    </span>
                    <span className="fbim-item-sub">
                      {it.last_from === "admin" ? <em>我：</em> : null}
                      {it.preview || "（无内容）"}
                    </span>
                  </span>
                  {it.open_n > 0 ? (
                    <span className="fbim-badge" title={`${it.open_n} 条待处理`}>
                      {it.open_n}
                    </span>
                  ) : null}
                </button>
              ))
            )}
          </div>

          <div className="fbim-main">
            {!active ? (
              <div className="fbim-idle">
                <CornIcon name="feedback" />
                <p>选左边一个人，看他说过什么、回过什么。</p>
              </div>
            ) : !thread ? (
              <div style={{ padding: 16 }}>
                <SkeletonLines widths={["80%", "60%", "70%"]} />
              </div>
            ) : (
              <>
                <div className="fbim-head">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm fbim-back"
                    onClick={() => {
                      setActive("");
                      setThread(null);
                    }}
                    aria-label="返回会话列表"
                  >
                    ←
                  </button>
                  <Face name={thread.name} url={thread.avatar} size={30} />
                  <div className="fbim-head-main">
                    <b>{thread.name}</b>
                    <span className="meta">
                      共 {thread.total} 条
                      {curStatus ? ` · 当前${statusLabel(curStatus)}` : ""}
                    </span>
                  </div>
                  <div className="fbim-head-acts">
                    {nextStatuses.map((next) => (
                      <button
                        key={next}
                        type="button"
                        className="btn btn-ghost btn-sm"
                        disabled={busy}
                        onClick={() => {
                          if (!lastUser) return;
                          setBusy(true);
                          api.admin
                            .patchFeedback(lastUser.fid, next)
                            .then(() => {
                              openThread(active);
                              loadList();
                            })
                            .catch((e) =>
                              setErr(e instanceof Error ? e.message : "状态更新失败")
                            )
                            .finally(() => setBusy(false));
                        }}
                      >
                        → {statusLabel(next)}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="fbim-msgs" ref={msgsRef}>
                  {thread.messages.map((m, i) =>
                    m.kind === "user" ? (
                      <div className="fbim-row left" key={`u${m.fid}`}>
                        <Face name={thread.name} url={thread.avatar} size={30} />
                        <div className="fbim-col">
                          <div className="fbim-meta">
                            <span className={`ctag ${STATUS_CLASS[m.status ?? ""] ?? ""}`}>
                              {m.status_label}
                            </span>
                            <span className="ctag info">{m.category_label}</span>
                            <span className="tm">{fmtTime(m.at)}</span>
                          </div>
                          <div className="fbim-bubble them">{m.text}</div>
                          {m.page || m.contact ? (
                            <div className="fbim-sub">
                              {m.page ? `来自 ${m.page}` : ""}
                              {m.contact ? ` · 联系 ${m.contact}` : ""}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ) : (
                      <div className="fbim-row right" key={`a${m.rid ?? i}`}>
                        <div className="fbim-col">
                          <div className="fbim-meta right">
                            <span className="tm">{fmtTime(m.at)}</span>
                            <span className="who">
                              {m.author ?? "管理员"}
                              {m.author_uid && m.author_uid === myUid ? "（我）" : ""}
                            </span>
                          </div>
                          <div className="fbim-bubble me">{m.text}</div>
                        </div>
                        <span className="fbim-face me-face" aria-hidden="true">
                          {(m.author ?? "管").slice(0, 1)}
                        </span>
                      </div>
                    )
                  )}
                </div>

                <div className="fbim-composer">
                  <textarea
                    className="input"
                    rows={2}
                    value={text}
                    maxLength={dict?.limits.max_reply ?? 1000}
                    placeholder="回一句话。Enter 发送，Shift+Enter 换行；发出后不能修改。"
                    disabled={busy}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <div className="fbim-send">
                    <span className="meta">
                      {text.trim().length}/{dict?.limits.max_reply ?? 1000}
                    </span>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={busy || !text.trim()}
                      onClick={() => void send()}
                    >
                      {busy ? "发送中…" : "发送"}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </PageShell>
  );
}
