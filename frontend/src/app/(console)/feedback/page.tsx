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
import {
  avatarIconName,
  avatarImageUrl,
  avatarLetter,
} from "@/components/settings/UserAvatar";
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

/** 日期控件里显示的短日期：2026-11-18 → 11月18日。
 *  原生 date 在中文 Safari 里渲染成「2026年11月18日」，11 个全角字塞不进窄屏
 *  的半个操作栏（实测溢出）。这里只显示短文本，真正的选择器仍由透明 input 唤起。 */
function shortDate(value: string): string {
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(value);
  return m ? `${Number(m[1])}月${Number(m[2])}日` : value;
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

/**
 * 头像：和标准头像组件同一套解析（img:/icon:/首字母），但保留反馈页自己的
 * .fbim-face 外观与尺寸。
 *
 * ★ 2026-11-19 修复裂图：库里存的是 `img:<hash>` / `icon:<name>` 内部值，
 *   旧版把 `img:xxx` 直接塞进 <img src> —— 那不是合法 URL，浏览器一律裂图。
 *   现在经 avatarImageUrl 翻成 /api/auth/avatar/<hash>；认 icon:；图片加载
 *   失败（文件被清理等）自动退回首字母，不再留一个破图图标。
 */
function Face({ name, url, size = 36 }: { name: string; url?: string; size?: number }) {
  const img = avatarImageUrl(url);
  const icon = avatarIconName(url);
  // 记失败的 hash 而不是布尔值：url 换成新头像时能自动重试（同 UserAvatar）
  const [failed, setFailed] = useState<string | null>(null);
  const showImg = Boolean(img && failed !== img);
  const box = { width: size, height: size };

  if (showImg && img) {
    return (
      <img
        className="fbim-face"
        src={img}
        alt=""
        style={box}
        onError={() => setFailed(img)}
      />
    );
  }
  return (
    <span className="fbim-face" style={box} aria-hidden="true">
      {icon ? <CornIcon name={icon} className="h-full w-full p-[12%]" /> : avatarLetter(name)}
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
  /** 导出的日期范围：YYYY-MM-DD。空串 = 不限；两端都含当天。 */
  const [exportFrom, setExportFrom] = useState("");
  const [exportTo, setExportTo] = useState("");
  const [active, setActive] = useState("");
  const [thread, setThread] = useState<FeedbackThreadResp | null>(null);
  /**
   * 每条反馈一个草稿（fid → 文本）。
   *
   * 原来只有一个全局 `text`：人在读第二条的时候写了半句，切回去就串到了
   * 第一条的输入框里 —— 而回复是**不可撤回**的，发错地方收不回来。
   */
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  /**
   * 正在提交/改状态的那一条（0 = 空闲）。
   *
   * 不用全局 busy：整页置灰会让别的条一起变灰，看起来像"整个后台卡住了"，
   * 而这个页面最常见的动作就是"连着改三条的状态"。
   */
  const [busyFid, setBusyFid] = useState(0);
  /** 展开着输入框的那一条（点「引用这条回复」才展开，默认收起） */
  const [replyingFid, setReplyingFid] = useState(0);
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
    // 换人就把草稿和展开状态一起清掉：留着会让上一个人的半句话跑到新会话里
    setDrafts({});
    setReplyingFid(0);
    setBusyFid(0);
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

  /** 在**指定的那一条**反馈下回一句。fid 一路带到后端，不靠"最新一条"猜。 */
  const sendTo = useCallback(
    async (fid: number) => {
      const t = (drafts[fid] ?? "").trim();
      if (!t || !active || busyFid) return;
      setBusyFid(fid);
      setErr("");
      try {
        const th = await api.admin.replyThread(active, t, fid);
        setThread(th);
        setDrafts((d) => ({ ...d, [fid]: "" }));
        setReplyingFid(0);
        // 回复会把 open 顺手推成 doing（后端 `add_reply`），所以列表与图表都得重拉
        loadList();
        api.admin
          .feedbackStats(STATS_DAYS)
          .then(setStats)
          .catch(() => undefined);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "发送失败");
      } finally {
        setBusyFid(0);
      }
    },
    [active, busyFid, drafts, loadList]
  );

  /**
   * 改**这一条**的状态（可来回改）。
   *
   * 不再有"顶部三个按钮 = 改最新一条"：那正是用户抱怨的"只能按反馈时间统一操作"。
   * 改完回读整个 thread：状态影响列表排序、KPI 和横条图，三处都得跟着变。
   */
  const changeStatus = useCallback(
    async (fid: number, status: string) => {
      if (busyFid) return;
      setBusyFid(fid);
      setErr("");
      try {
        await api.admin.patchFeedback(fid, status);
        const th = await api.admin.feedbackThread(active);
        setThread(th);
        loadList();
        api.admin
          .feedbackStats(STATS_DAYS)
          .then(setStats)
          .catch(() => undefined);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "状态更新失败");
      } finally {
        setBusyFid(0);
      }
    },
    [active, busyFid, loadList]
  );

  const exportAll = useCallback(async () => {
    // 只填了一端也是合法的：后端解释为"从那天起至今"或"最早到那天"。
    // 反向范围（起 > 止）交给后端返回 400，前端不替它猜 —— 两处各写一份
    // 校验规则，改一处就会不一致。
    setExporting(true);
    setErr("");
    try {
      await api.admin.exportFeedback(fbStatus, exportFrom, exportTo);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }, [fbStatus, exportFrom, exportTo]);

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

  // 「还有几条没处理」用它算：状态是**每条**自己的，不再有"这个人的状态"
  const openCount = (thread?.items ?? []).filter(
    (t) => t.status === "open" || t.status === "doing"
  ).length;

  return (
    <PageShell
      /* 不写 eyebrow：沿用第 39 批的决定 —— 眉题只留给看板/工作流，其余页面
         靠侧栏高亮 + 标题定位。反馈页是 adminOnly，普通用户根本进不来，
         再标一行「管理 · 内部」等于把「你在哪」说第三遍。
         无 eyebrow 时 PageShell 走 pg-head--solo，标题吃 --fs-page。 */
      title="用户反馈"
      actions={
        <>
          <Seg ariaLabel="按状态筛选" options={filterOptions} value={fbStatus} onChange={setFbStatus} />
          <div className="fbim-export" role="group" aria-label="导出日期范围">
            <div className="fbim-export-range">
              <label className="fbim-date">
                <span className="fbim-date-cap">起始</span>
                <span className="fbim-date-val">
                  <span className={`fbim-date-text${exportFrom ? "" : " is-empty"}`}>
                    {exportFrom ? shortDate(exportFrom) : "不限"}
                  </span>
                  <input
                    type="date"
                    className="fbim-date-input"
                    aria-label="导出起始日期，留空表示不限"
                    value={exportFrom}
                    max={exportTo || undefined}
                    onChange={(e) => setExportFrom(e.target.value)}
                  />
                </span>
              </label>
              <span className="fbim-export-sep" aria-hidden="true">—</span>
              <label className="fbim-date">
                <span className="fbim-date-cap">结束</span>
                <span className="fbim-date-val">
                  <span className={`fbim-date-text${exportTo ? "" : " is-empty"}`}>
                    {exportTo ? shortDate(exportTo) : "不限"}
                  </span>
                  <input
                    type="date"
                    className="fbim-date-input"
                    aria-label="导出结束日期，留空表示不限"
                    value={exportTo}
                    min={exportFrom || undefined}
                    onChange={(e) => setExportTo(e.target.value)}
                  />
                </span>
              </label>
            </div>
          </div>
          <button
            type="button"
            className="btn btn-primary btn-sm fbim-export-btn"
            disabled={exporting}
            onClick={() => void exportAll()}
            title="导出 Word：日期都留空=全部汇总；填起止=只导该日期范围（含当天）。仅含反馈问题、提交日期、分类与解决状态，不含个人信息与对话"
          >
            <CornIcon name="download" />
            {exporting ? "导出中…" : "一键导出"}
          </button>
        </>
      }
    >
      {/* fbim-page + has-thread：只为了让窄屏在"已点进某个人"时收起统计区。
          手机上一进对话，四张卡 + 三张图占了将近四屏，要滚很久才看到第一句 ——
          1440 的视口截图看不出来，只有 390 的整页截图才暴露（2026-11-19 上线后量到）。 */}
      <div className={`stack-20 fbim-page${active ? " has-thread" : ""}`}>
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
                      {openCount > 0 ? ` · ${openCount} 条还在办` : " · 都有结论了"}
                    </span>
                  </div>
                </div>

                {/*
                  一条反馈一张卡，不是一段对话流水。
                  ★ 原来的形态是"这个人说了什么"按时间平铺，状态按钮在头顶改
                    **最新一条** —— 于是"想标记第三条已处理"根本做不到，点错也
                    改不回来（用户 2026-11-19 的原话）。现在状态控件和回复框都
                    长在卡里，fid 一路带到后端，不存在"改到别人那条上去"的可能。
                */}
                <div className="fbim-msgs" ref={msgsRef}>
                  {thread.items.length === 0 ? (
                    <div className="fbim-idle">
                      <CornIcon name="feedback" />
                      <p>这个人还没提过反馈。</p>
                    </div>
                  ) : (
                    thread.items.map((tk) => {
                      const showing = replyingFid === tk.fid;
                      const draft = drafts[tk.fid] ?? "";
                      const busy = busyFid === tk.fid;
                      return (
                        <article
                          className={`fbim-ticket${showing ? " replying" : ""}`}
                          key={tk.fid}
                        >
                          <div className="fbim-ticket-head">
                            <span className="ctag info">{tk.category_label}</span>
                            <span className="tm">{fmtTime(tk.at)}</span>
                          </div>
                          <div className="fbim-bubble them">{tk.text}</div>
                          {tk.page || tk.contact ? (
                            <div className="fbim-sub">
                              {tk.page ? `来自 ${tk.page}` : ""}
                              {tk.contact ? ` · 联系 ${tk.contact}` : ""}
                            </div>
                          ) : null}

                          {tk.replies.length > 0 ? (
                            <div className="fbim-chain">
                              {tk.replies.map((r) => (
                                <div
                                  className={`fbim-reply ${r.kind === "admin" ? "me" : "them"}`}
                                  key={r.rid}
                                >
                                  <div className="fbim-reply-meta">
                                    {r.kind === "admin" ? (
                                      <>
                                        {r.author || "管理员"}
                                        {r.author_uid && r.author_uid === myUid ? "（我）" : ""}
                                      </>
                                    ) : (
                                      "用户补充"
                                    )}
                                    <span className="tm">{fmtTime(r.at)}</span>
                                  </div>
                                  <div className="fbim-bubble sm">{r.text}</div>
                                </div>
                              ))}
                            </div>
                          ) : null}

                          <div className="fbim-ticket-foot">
                            <div className="fbim-st" role="group" aria-label="这条反馈的状态">
                              {STATUS_ORDER.map((s) => (
                                <button
                                  key={s}
                                  type="button"
                                  className={`fbim-st-btn${
                                    tk.status === s ? ` on ${STATUS_CLASS[s] ?? ""}` : ""
                                  }`}
                                  disabled={busy}
                                  aria-pressed={tk.status === s}
                                  onClick={() => void changeStatus(tk.fid, s)}
                                  title={
                                    tk.status === s
                                      ? `现在就是「${statusLabel(s)}」`
                                      : `改到「${statusLabel(s)}」`
                                  }
                                >
                                  {statusLabel(s)}
                                </button>
                              ))}
                            </div>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm fbim-reply-btn"
                              onClick={() => setReplyingFid(showing ? 0 : tk.fid)}
                              aria-expanded={showing}
                            >
                              {showing ? "收起" : tk.replies.length ? "再回一条" : "引用这条回复"}
                            </button>
                          </div>

                          {showing ? (
                            <div className="fbim-reply-box">
                              <textarea
                                className="input"
                                rows={3}
                                autoFocus
                                value={draft}
                                maxLength={dict?.limits.max_reply ?? 1000}
                                /* 提示语必须短到能在一行里放下：390 宽时
                                   「回复「这条的第一句」。Enter 发送，Shift+Enter 换行；
                                   发出后不能修改。」要占三行，而 textarea 只有两行高，
                                   最后一行会被裁掉半个字（2026-11-19 手机截图）。
                                   引用哪条由卡片本身说清楚，不用写进 placeholder。 */
                                placeholder="回这条。Enter 发送，Shift+Enter 换行"
                                disabled={busy}
                                onChange={(e) =>
                                  setDrafts((d) => ({ ...d, [tk.fid]: e.target.value }))
                                }
                                onKeyDown={(e) => {
                                  if (
                                    e.key === "Enter" &&
                                    !e.shiftKey &&
                                    !e.nativeEvent.isComposing
                                  ) {
                                    e.preventDefault();
                                    void sendTo(tk.fid);
                                  }
                                }}
                              />
                              <div className="fbim-send">
                                <span className="meta">
                                  {draft.trim().length}/{dict?.limits.max_reply ?? 1000}
                                </span>
                                <button
                                  type="button"
                                  className="btn btn-primary btn-sm"
                                  disabled={busy || !draft.trim()}
                                  onClick={() => void sendTo(tk.fid)}
                                >
                                  {busy ? "发送中…" : "发送"}
                                </button>
                              </div>
                            </div>
                          ) : null}
                        </article>
                      );
                    })
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </PageShell>
  );
}
