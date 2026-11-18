"use client";

// 开发者数据看板
//
// 页面任务：回答两个问题，顺着读下来就是两块 ——
//   **① 系统一共被调了多少、调的是什么、哪里慢、哪里报错**（全量口径）
//   **② 真人用户到底用了多少**（剔除内部/测试/自动化/未登录之后的子集）
//
// ★ 2026-11-18 重构。上一版的问题不是数字错，是**读不下去**：
//   · 两套口径混在同一屏里，中间靠一段长文字解释"为什么两个数不一样"；
//   · 口径来源、采集延迟、"这张表在记什么"三块说明占了近两屏，
//     每次看数都要先划过它们；
//   · 功能榜、用户榜都是表格，199 个功能的排行靠读数字比大小。
//   所以这一版做三件事：拆成两块、把榜改成条形、把说明删到只剩口径本身
//   （口径原文在 docs/数据看板_2026-10-27/口径与数据字典.md，不进页面）。
//
// 三条必须显式标注的口径（不标用户会误判"筛选坏了"）：
//   ① 注册用户 / 真实账号是**当前快照**，不随时间筛选变化（用「全量」角标）
//   ② 平均耗时是**均值，不是 p95**
//   ③ `/api/chat/stream` 的耗时是**整条流的时长**（含模型生成时间），会把均值拉高
//
// 权限：非管理员**当作页面不存在**（notFound），不做"有权限门"式遮挡 ——
// 那样等于告诉对方这里有个开发者看板。后端同名接口对非管理员也返回 404，
// 两层配合才叫真隐藏。真正的安全边界永远在后端。
import Link from "next/link";
import { notFound } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { Callout, Seg, SkeletonLines } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { fmtNum } from "@/lib/time";
import type {
  ApiRankResp,
  DashboardOverview,
  FailuresResp,
  RealUsersResp,
} from "@/lib/types";

const DAY_OPTIONS = [
  { value: "1", label: "今日", hint: "自然日 00:00 起（不是滚动 24 小时）" },
  { value: "7", label: "近 7 天", hint: "含今日的 7 个自然日" },
  { value: "30", label: "近 30 天", hint: "含今日的 30 个自然日" },
];

// 三种排序对应三个真实问题，不是"多给几个选项"：
// 调用量看依赖面、最慢看体感、失败看稳定性。只给一个排行榜，
// 开发者还得自己脑内排序。
type SortKey = "calls" | "slow" | "fail";

const SORT_OPTIONS: { value: SortKey; label: string; hint: string }[] = [
  { value: "calls", label: "用得最多", hint: "哪个功能被调得最多" },
  {
    value: "slow",
    label: "最耗时",
    hint: "平均等待时间最长（只算用过 3 次以上的：第一次启动时的偶发慢不算）",
  },
  { value: "fail", label: "出过错", hint: "返回过错误（4xx/5xx，不含未登录与报告空态）的功能" },
];

const AUTO_REFRESH_MS = 30_000;

// 排行榜显示几行：10 行是"一眼看完且不用滚动"的上限。
// 后端一次给 20 行，多出来的那 10 行留在数据里（将来要做"展开全部"不用再请求）。
const TOP_N = 10;

// 「真实用户」区块的两个统计维度。切换只改渲染哪一栏，不重新请求 ——
// 两个维度各 20 行，一次拉回来比切一次发一次请求更省，也不会切出"加载中"的闪烁。
type RealDim = "feature" | "user";

const REAL_DIM_OPTIONS: { value: RealDim; label: string; hint: string }[] = [
  { value: "feature", label: "按功能", hint: "真实用户在调哪些功能，以及每个功能有多少人用过" },
  { value: "user", label: "按用户", hint: "谁在用、用了多少次、活跃了几天" },
];

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtClock(d: Date | null): string {
  if (!d) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 比例 → 百分数。null（分母为 0）显示 —，不显示 0.0% —— 那是"算出了零"，两回事 */
function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

/** 条形宽度：按最大值归一化，最小 1%（否则 1 次和 0 次看起来一样） */
function pctOf(v: number, peak: number): number {
  if (!peak) return 1;
  return Math.max(v > 0 ? 1.5 : 0, (v / peak) * 100);
}

/**
 * 一根排行条。功能榜、用户榜共用 —— 两处形状一样，各写一遍迟早长成两个样子。
 *
 * `sub` 是第二行的浅色小字（路由路径 / 活跃天数），`note` 是数值下的补充（用户数）。
 * 用真 DOM 而不是伪元素：这两处都是**数据**，要能被复制、被 title 提示。
 *
 * `fade` 让条形随名次递减（第 1 名实金、第 10 名约六成）：这是榜单的通用读法，
 * 一眼就能分出"头部"和"尾巴"，不用逐个读数字。出错榜（bad）不淡化 ——
 * 红色是告警色，一半红一半浅红只会让人以为后者不重要。
 */
function BarRow({
  rank,
  name,
  sub,
  pct,
  value,
  note,
  bad,
  fade,
  action,
}: {
  rank?: number;
  name: string;
  sub?: string;
  pct: number;
  value: string;
  note?: string;
  bad?: boolean;
  fade?: number;
  action?: ReactNode;
}) {
  const isTop = typeof rank === "number" && rank <= 3;
  return (
    <div
      className={`dash-hbar${action ? " has-act" : ""}${isTop ? " top" : ""}`}
      title={sub ? `${name} · ${sub}` : name}
    >
      <span className="dash-hbar-rank">{rank ?? ""}</span>
      <span className="dash-hbar-name">
        {name}
        {sub ? <em>{sub}</em> : null}
      </span>
      <span className="dash-hbar-track">
        <i
          className={bad ? "bad" : ""}
          style={{ width: `${pct}%`, opacity: bad ? 1 : (fade ?? 1) }}
        />
      </span>
      <span className="dash-hbar-val">
        {value}
        {note ? <em>{note}</em> : null}
      </span>
      {action ? <span className="dash-hbar-act">{action}</span> : null}
    </div>
  );
}

/**
 * 逐日柱状图。0 值那天画一条底线（不是空白）—— 跳过空日子会让趋势看起来比实际连续。
 *
 * 失败量叠在柱子**底部**（红色）而不是另画一条线：这两件事共用同一个计数量纲，
 * 叠起来才看得出"那天 1748 次里有多少是坏的"。
 */
function ColumnChart({
  series,
  withUsers,
}: {
  series: { day: string; calls: number; failed?: number; users?: number }[];
  withUsers?: boolean;
}) {
  const peak = Math.max(1, ...series.map((s) => s.calls));
  // 30 天时每根柱只有十几像素宽，日期全写会糊成一团 —— 每 N 天标一个
  const every = Math.max(1, Math.ceil(series.length / 7));
  // 柱顶数值：7/14 天每根都标；更长的区间只在日期刻度处标，否则 30 根柱的
  // 30 个数字会连成一条黑带，比不标还难读。
  // ★ 不额外给峰值补一个标记：30 天时每列只有 12px 宽，峰值柱若正好挨着
  //   刻度列，两个数字会叠在一起（2026-11-18 实测 clash=1）。峰值本来就写在
  //   卡片头右侧的「峰值 810」里，不需要它再出现一遍。
  const showAll = series.length <= 14;
  const label = (n: number) => (n >= 10000 ? `${Math.round(n / 1000)}k` : fmtNum(n));
  return (
    <div className="dash-cols">
      {series.map((s, i) => {
        const bad = s.failed ?? 0;
        const mark = showAll || i % every === 0;
        const tip = [
          s.day,
          `${fmtNum(s.calls)} 次`,
          bad ? `失败 ${fmtNum(bad)}` : "",
          withUsers && typeof s.users === "number" ? `${s.users} 人` : "",
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          <div className="dash-col" key={s.day} title={tip}>
            <div className="dash-col-track">
              <div
                className="dash-col-bar"
                style={{ height: s.calls === 0 ? "2px" : `${(s.calls / peak) * 100}%` }}
              >
                {mark ? (
                  <span className={`dash-col-val${s.calls === 0 ? " zero" : ""}`}>
                    {label(s.calls)}
                  </span>
                ) : null}
                {s.calls === 0 ? (
                  <i className="zero" />
                ) : (
                  <>
                    {/* 段高按**本柱自身**的比例算（除以 calls，不是 peak）：
                        失败段要贴着这根柱的底部，而不是所有柱子共用一个比例尺 */}
                    <i style={{ height: `${((s.calls - bad) / s.calls) * 100}%` }} />
                    {bad ? <i className="bad" style={{ height: `${(bad / s.calls) * 100}%` }} /> : null}
                  </>
                )}
              </div>
            </div>
            <span className="dash-col-x">{i % every === 0 ? s.day.slice(5) : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardPage() {
  const auth = useAuth();
  const isAdmin = Boolean(auth.me?.is_admin);

  const [days, setDays] = useState(7);
  const [sort, setSort] = useState<SortKey>("calls");
  const [auto, setAuto] = useState(true);
  const [ov, setOv] = useState<DashboardOverview | null>(null);
  const [ovErr, setOvErr] = useState("");
  const [apis, setApis] = useState<ApiRankResp | null>(null);
  // 失败明细：让页面自己回答"为什么失败"，不用去服务器 grep 日志
  const [fails, setFails] = useState<FailuresResp | null>(null);
  // 真实用户视角：与 ov 并列的独立口径，加载失败不影响上面那些数字
  const [real, setReal] = useState<RealUsersResp | null>(null);
  // 默认看「按用户」：这一块要回答的是"谁在用、用了多少次"，
  // 功能维度同样给（切一刀就到），但榜首那个榜才是管理员第一眼想看的。
  const [realDim, setRealDim] = useState<RealDim>("user");
  // 正在提交标记的 uid（防连点；标记后端只有一条 UPDATE，不需要全局 busy）
  const [markingUid, setMarkingUid] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);

  const load = useCallback(() => {
    if (!isAdmin) return;
    setOvErr("");
    api.admin
      .overview(days)
      .then((d) => {
        setOv(d);
        setFetchedAt(new Date());
      })
      .catch((e) => setOvErr(e instanceof Error ? e.message : "读取失败"));
    api.admin
      .apis(days, 20, sort)
      .then(setApis)
      .catch(() => setApis(null));
    api.admin
      .failures(days, 20)
      .then(setFails)
      .catch(() => setFails(null));
    api.admin
      .realUsers(days, 20)
      .then(setReal)
      .catch(() => setReal(null));
  }, [days, sort, isAdmin]);

  useEffect(() => {
    load();
  }, [load]);

  // 准实时：采集侧 3 秒落库，页面侧 30 秒自动拉一次。
  // 两个约束：① 页面在后台时不拉（省资源，也避免回来时看到一串串数字跳变）；
  //          ② 切回前台立刻补一次，否则最长要等 30 秒才更新。
  useEffect(() => {
    if (!auto || !isAdmin) return;
    const tick = () => {
      if (document.visibilityState === "visible") load();
    };
    const id = window.setInterval(tick, AUTO_REFRESH_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [auto, isAdmin, load]);

  /**
   * 标记 / 取消标记内部账号。
   *
   * 成功后**重拉整块**，而不是本地把这一行从列表里移掉：一次标记会同时改变
   * 「真实调用次数、活跃用户数、人均、两个维度的排行、口径分解」——
   * 本地删一行就得把这六处一起改，漏一处就出现"上面写着 3 个用户、
   * 表里有 2 行"这种对不上账的页面。让后端算完，前端照着画。
   */
  const markInternal = useCallback(
    async (target: string, internal: boolean) => {
      setMarkingUid(target);
      try {
        await api.admin.setUserInternal(target, internal);
        load();
      } catch (e) {
        setOvErr(e instanceof Error ? e.message : "标记失败");
      } finally {
        setMarkingUid(null);
      }
    },
    [load]
  );

  // 登录态还没确定：先什么都不渲染。
  // 不能"先画骨架再判断"—— 骨架里带着"数据看板"标题，非管理员会看到它闪一下，
  // 那也是一次泄露。宁可空白一瞬。
  if (auth.loading) return null;

  // 非管理员：**当成这个页面不存在**。
  // 不渲染"仅开发者可见"的提示页 —— 那句话反而在告诉对方"这里有个开发者看板"。
  // notFound() 让它和访问任意不存在的路径表现完全一致（见 app/not-found.tsx），
  // 后端同名接口对非管理员也返回 404（见 api/admin.py）。
  if (!isAdmin) notFound();

  const noCalls = ov !== null && ov.calls.total === 0;

  // 功能榜：三条排序各自归一化，bar 长度才总有一根是满的
  const apiPeak = apis
    ? Math.max(
        1,
        ...apis.items.map((it) =>
          sort === "slow" ? it.avg_ms : sort === "fail" ? it.failed : it.calls
        )
      )
    : 1;

  // 真实用户两栏的归一化基准
  const realFeaturePeak = real ? Math.max(1, ...real.by_feature.map((x) => x.calls)) : 1;
  const realUserPeak = real ? Math.max(1, ...real.by_user.map((x) => x.calls)) : 1;

  // 口径分解的五个桶（互斥且穷尽，相加 = 全量）
  const buckets = real
    ? [
        { key: "real", label: "真实用户", v: real.calls.real },
        { key: "passive", label: "被动访问", v: real.calls.passive },
        { key: "internal", label: "内部/测试", v: real.calls.internal },
        { key: "orphan", label: "已注销", v: real.calls.orphan },
        { key: "anon", label: "未登录", v: real.calls.anonymous },
      ]
    : [];

  return (
    <PageShell
      eyebrow="管理 · 内部"
      title="数据看板"
      // 全站只留两处页头说明，这是其中之一（硬约束：ALLOWED_SUB）。
      // 它挡的是"以为切了时间筛选连注册用户也会变"这类误读，不能删。
      sub="数字跟随上方时间范围。"
      detail={
        <span className="meta">
          标「全量」的是当前快照。耗时取均值、不是 p95。采集延迟 ≤ 3 秒
          {auto ? "，每 30 秒自动刷新" : ""}
          {fetchedAt ? `（上次 ${fmtClock(fetchedAt)}）` : ""}。
        </span>
      }
      actions={
        <>
          <Seg
            ariaLabel="时间范围"
            options={DAY_OPTIONS}
            value={String(days)}
            onChange={(v) => setDays(Number(v))}
          />
          <button
            type="button"
            className={`btn btn-sm ${auto ? "btn-primary" : "btn-ghost"}`}
            aria-pressed={auto}
            onClick={() => setAuto((v) => !v)}
            title="每 30 秒自动拉一次；页面在后台时暂停"
          >
            <CornIcon name="refresh" />
            {auto ? "自动刷新 开" : "自动刷新 关"}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={load} title="立刻重新拉一次">
            刷新
          </button>
        </>
      }
    >
      <div className="stack-20">
        {ovErr ? <Callout icon="warn">读取失败：{ovErr}</Callout> : null}

        {noCalls ? (
          <Callout icon="info">
            这个区间还没有调用记录。刚上线时是正常的 —— 采集是批量落库，最多滞后 3 秒。
          </Callout>
        ) : null}

        {/* ============ 总览：用户与调用总量（四张卡，未改） ============ */}
        <div className="grid4">
          <div className="card card-pad stat">
            <div className="n">{ov ? fmtNum(ov.users.total) : "—"}</div>
            <div className="l">
              注册用户 <span className="stat-scope">全量</span>
            </div>
            {/* 清理记录就写在卡片上：不然「总数比上个月少了」只能靠翻库解释。
                没清理过就不显示，默认外观不变。 */}
            {ov && (ov.users.purged ?? 0) > 0 && (
              <div className="meta">本区间清理 {fmtNum(ov.users.purged ?? 0)} 个测试账号</div>
            )}
          </div>
          <div className="card card-pad stat">
            <div className="n">{ov ? fmtNum(ov.users.new) : "—"}</div>
            <div className="l">区间新增用户</div>
          </div>
          <div className="card card-pad stat">
            <div className="n">{ov ? fmtNum(ov.calls.total) : "—"}</div>
            <div className="l">API 调用次数</div>
          </div>
          <div className="card card-pad stat">
            <div className="n">
              {ov
                ? ov.calls.success_rate === null
                  ? "—"
                  : `${(ov.calls.success_rate * 100).toFixed(1)}%`
                : "—"}
            </div>
            <div className="l">成功率</div>
          </div>
        </div>

        {/* ==========================================================
            板块一 · 系统调用量（全量口径）
            ========================================================== */}
        <section>
          <div className="dash-head">
            <p className="sec-label" style={{ margin: 0 }}>
              系统调用量
            </p>
            <span className="meta">
              全量（含内部、自动化与未登录）
              {apis ? ` · ${apis.total_paths} 个功能` : ""}
            </span>
          </div>

          <div className="grid4" style={{ marginBottom: 10 }}>
            <div className="card card-pad stat">
              <div className="n">{ov ? `${fmtNum(ov.calls.avg_ms)} ms` : "—"}</div>
              <div className="l">平均耗时</div>
            </div>
            <div className="card card-pad stat">
              <div className="n">{ov ? `${fmtNum(ov.calls.max_ms)} ms` : "—"}</div>
              <div className="l">最大耗时</div>
            </div>
            <div className="card card-pad stat">
              <div className="n">{ov ? fmtNum(ov.calls.failed) : "—"}</div>
              <div className="l">失败次数</div>
              {/* 给一个比例：2 次失败在 1 900 次里不算事，在 20 次里就是事故。
                  绝对值不带分母，读者得自己去除 —— 那一栏空着也是浪费。 */}
              <div className="meta">
                {ov && ov.calls.total
                  ? `失败率 ${((ov.calls.failed / ov.calls.total) * 100).toFixed(2)}%`
                  : "—"}
              </div>
            </div>
            {/* 反馈从这里搬走了（2026-11-18 独立成页）—— 但入口不能一起搬：
                看板是管理员每天都会打开的那一页，待处理数挂在这里才有人管。
                整张卡做成链接，点哪儿都能进。 */}
            <Link href="/feedback" className="card card-pad stat">
              <div className="n">{ov ? fmtNum(ov.feedback.open) : "—"}</div>
              <div className="l">
                待处理反馈 <span className="stat-scope">全量</span>
              </div>
              <div className="meta">去处理 →</div>
            </Link>
          </div>

          {/* ---- 逐日趋势 ---- */}
          <div className="card card-pad" style={{ marginBottom: 10 }}>
            <div className="dash-card-head">
              <h3>每日调用量</h3>
              <span className="meta">
                {ov
                  ? `峰值 ${fmtNum(Math.max(...ov.series.map((s) => s.calls)))} · 按自然日`
                  : ""}
              </span>
            </div>
            {!ov ? (
              <SkeletonLines widths={["100%", "88%", "72%"]} />
            ) : ov.series.every((s) => s.calls === 0) ? (
              <div className="dash-empty">区间内没有调用，趋势图暂无数据。</div>
            ) : (
              <ColumnChart series={ov.series} />
            )}
          </div>

          {/* ---- 功能使用排行（条形，不是表格） ---- */}
          <div className="card card-pad" style={{ marginBottom: 10 }}>
            <div className="dash-card-head">
              <h3>功能使用排行</h3>
              <span className="meta">
                {apis ? `共 ${apis.total_paths} 个功能被调用过 · 显示前 ${TOP_N}` : ""}
              </span>
            </div>
            <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
              <Seg
                ariaLabel="排行依据"
                options={SORT_OPTIONS}
                value={sort}
                onChange={(v) => setSort(v as SortKey)}
              />
            </div>

            {!apis ? (
              <SkeletonLines widths={["100%", "90%", "80%", "70%"]} />
            ) : apis.items.length === 0 ? (
              <div className="dash-empty">
                {sort === "fail"
                  ? "这个区间没有出过错 —— 是好事。"
                  : sort === "slow"
                    ? "这个区间里没有用过 3 次以上的功能，还算不出有参考价值的平均耗时。"
                    : "这个区间还没有调用记录。"}
              </div>
            ) : (
              <div className="dash-hbars">
                <div className="dash-hbar dash-hbar-th">
                  <span className="dash-hbar-rank">#</span>
                  <span className="dash-hbar-name">功能</span>
                  <span />
                  <span className="dash-hbar-val">
                    {sort === "slow" ? "平均耗时" : sort === "fail" ? "失败" : "调用"}
                  </span>
                </div>
                {apis.items.slice(0, TOP_N).map((it, i) => {
                  // 三条排序换的是"条长量什么"，右侧主数值也跟着换 ——
                  // 只换条不换数字会让人以为排序没生效
                  const raw = sort === "slow" ? it.avg_ms : sort === "fail" ? it.failed : it.calls;
                  const value =
                    sort === "slow"
                      ? `${it.avg_ms}ms`
                      : sort === "fail"
                        ? `${fmtNum(it.failed)} 次失败`
                        : fmtNum(it.calls);
                  const note =
                    sort === "slow"
                      ? `${fmtNum(it.calls)} 次调用`
                      : sort === "fail"
                        ? `共 ${fmtNum(it.calls)} 次 · ${it.avg_ms}ms`
                        : `${it.failed ? `失败 ${it.failed}` : "零失败"} · ${it.avg_ms}ms`;
                  return (
                    <BarRow
                      key={`${it.method} ${it.path}`}
                      rank={i + 1}
                      name={it.label}
                      sub={`${it.method} ${it.path}`}
                      pct={pctOf(raw, apiPeak)}
                      fade={Math.max(0.55, 1 - i * 0.06)}
                      value={value}
                      note={note}
                      bad={sort === "fail"}
                    />
                  );
                })}
              </div>
            )}
          </div>

          {/* ---- 失败原因（只列要处理的） ---- */}
          <div className="card card-pad">
            <div className="dash-card-head">
              <h3>需要处理的失败</h3>
              <span className="meta">
                {fails ? `${fails.need_attention.length} 条 · 已排除未登录探测与空总结` : ""}
              </span>
            </div>
            {!fails ? (
              <SkeletonLines widths={["100%", "92%", "80%"]} />
            ) : fails.recent.length === 0 ? (
              <div className="dash-empty">这个区间没有任何失败调用 —— 干净。</div>
            ) : fails.need_attention.length === 0 ? (
              <div className="dash-empty">没有需要处理的问题。</div>
            ) : (
              <>
                {fails.need_attention.slice(0, 6).map((f) => (
                  <div
                    key={`${f.method} ${f.path} ${f.ts ?? 0}`}
                    className="row"
                    style={{ alignItems: "flex-start", gap: 10, padding: "7px 0" }}
                  >
                    <span className="ctag bad">{f.status}</span>
                    <span style={{ minWidth: 0, flex: 1 }}>
                      <span
                        style={{ display: "block", fontSize: "var(--fs-xs)", color: "var(--text-1)" }}
                      >
                        {f.label}
                      </span>
                      <span
                        className="mono"
                        style={{ display: "block", fontSize: "var(--fs-xs)", color: "var(--text-3)" }}
                      >
                        {f.ts ? `${fmtTime(f.ts)} · ` : ""}
                        {f.method} {f.path}
                      </span>
                      <span className="hint" style={{ display: "block", margin: "2px 0 0" }}>
                        {f.hint}
                      </span>
                    </span>
                  </div>
                ))}
              </>
            )}
          </div>
        </section>

        {/* ==========================================================
            板块二 · 真实用户调用量
            ========================================================== */}
        <section>
          <div className="dash-head">
            <p className="sec-label" style={{ margin: 0 }}>
              真实用户调用量
            </p>
            <span className="meta">
              {real
                ? `占全量 ${fmtPct(real.calls.share)} · ${real.users.active} 个真实用户`
                : "另一套口径"}
            </span>
          </div>

          {!real ? (
            <div className="card card-pad">
              <SkeletonLines widths={["100%", "88%", "72%"]} />
            </div>
          ) : (
            <>
              {/* 一个内部账号都没标记时，两个板块的数字仍然不同 —— 差异来自
                  「被动访问」（登录了但没发起对话）。不说明会被当成功能没生效。 */}
              {real.internal.marked === 0 ? (
                <Callout icon="info">
                  还没有标记任何内部/测试账号。本板块只算<b>发起过对话</b>的账号，
                  只浏览不对话的调用落在下面「被动访问」。
                  自测号请在页尾「已标记的内部账号」里逐个标记。
                </Callout>
              ) : null}

              <div className="grid4" style={{ marginBottom: 10 }}>
                <div className="card card-pad stat">
                  <div className="n">{fmtNum(real.calls.real)}</div>
                  <div className="l">真实用户调用</div>
                  <div className="meta">
                    占全量 {fmtPct(real.calls.share)} · 其中对话 {fmtNum(real.calls.chat)} 次
                  </div>
                </div>
                <div className="card card-pad stat">
                  <div className="n">{fmtNum(real.users.active)}</div>
                  <div className="l">活跃真实用户</div>
                  <div className="meta">人均 {real.users.per_user} 次调用</div>
                </div>
                <div className="card card-pad stat">
                  <div className="n">
                    {ov?.range.days === 1 ? "—" : fmtNum(real.users.returning)}
                  </div>
                  <div className="l">回访用户</div>
                  <div className="meta">
                    {ov?.range.days === 1
                      ? "今日算不出回访（要看两天以上）"
                      : `≥2 天有调用 · 只来过一天 ${fmtNum(real.users.single_day)} 人`}
                  </div>
                </div>
                <div className="card card-pad stat">
                  <div className="n">{fmtNum(real.users.chatted)}</div>
                  <div className="l">
                    用过对话的真实账号 <span className="stat-scope">全量</span>
                  </div>
                  <div className="meta">
                    注册账号 {fmtNum(real.users.total)}（不含内部/管理员）· 区间新增{" "}
                    {fmtNum(real.users.new)}
                  </div>
                </div>
              </div>

              <div className="grid2">
                {/* ---- 口径分解：一条堆叠条 + 图例（原来是一张五行表） ---- */}
                <div className="card card-pad">
                  <div className="dash-card-head">
                    <h3>调用量构成</h3>
                    <span className="meta">合计 {fmtNum(real.calls.total)} 次</span>
                  </div>
                  <div className="dash-split">
                    {buckets.map((b) => (
                      <i
                        key={b.key}
                        className={`s-${b.key}`}
                        style={{
                          width: real.calls.total ? `${(b.v / real.calls.total) * 100}%` : "0%",
                        }}
                        title={`${b.label} ${fmtNum(b.v)} 次`}
                      />
                    ))}
                  </div>
                  <ul className="dash-legend">
                    {buckets.map((b) => (
                      <li key={b.key}>
                        <span className={`swatch s-${b.key}`} />
                        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {b.label}
                        </span>
                        <span className="n">
                          {fmtNum(b.v)}
                          <span className="meta" style={{ marginLeft: 4 }}>
                            {fmtPct(real.calls.total ? b.v / real.calls.total : null)}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                  {/* 自动化调用必须点名：它的量级会让人以为"内部流量怎么这么大" */}
                  {real.calls.automated.total > 0 ? (
                    <p className="hint" style={{ margin: "10px 0 0" }}>
                      其中 {fmtNum(real.calls.automated.total)} 次是无头浏览器/脚本（
                      {real.calls.automated.accounts} 个账号），已计入「内部/测试」。
                    </p>
                  ) : null}
                </div>

                {/* ---- 趋势：调用量 + 当日活跃人数 ---- */}
                <div className="card card-pad">
                  <div className="dash-card-head">
                    <h3>每日真实调用</h3>
                    <span className="meta">
                      {real.series.length
                        ? `峰值 ${fmtNum(Math.max(...real.series.map((s) => s.calls)))} · 悬停看人数`
                        : ""}
                    </span>
                  </div>
                  {real.series.every((s) => s.calls === 0) ? (
                    <div className="dash-empty">区间内没有真实用户的调用。</div>
                  ) : (
                    <ColumnChart series={real.series} withUsers />
                  )}
                </div>
              </div>

              {/* ---- 两个统计维度：都画成条形 ---- */}
              <div className="card card-pad" style={{ marginTop: 10 }}>
                <div className="dash-card-head">
                  <h3>使用排行</h3>
                  <span className="meta">
                    共 {fmtNum(real.calls.real)} 次 · {real.users.active} 个真实用户
                  </span>
                </div>
                <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
                  <Seg
                    ariaLabel="统计维度"
                    options={REAL_DIM_OPTIONS}
                    value={realDim}
                    onChange={(v) => setRealDim(v as RealDim)}
                  />
                </div>

                {realDim === "feature" ? (
                  real.by_feature.length === 0 ? (
                    <div className="dash-empty">这个区间没有真实用户的调用记录。</div>
                  ) : (
                    <div className="dash-hbars">
                      <div className="dash-hbar dash-hbar-th">
                        <span className="dash-hbar-rank">#</span>
                        <span className="dash-hbar-name">功能</span>
                        <span />
                        <span className="dash-hbar-val">调用</span>
                      </div>
                      {real.by_feature.slice(0, TOP_N).map((it, i) => (
                        <BarRow
                          key={`${it.method} ${it.path}`}
                          rank={i + 1}
                          name={it.label}
                          sub={`${it.method} ${it.path}`}
                          pct={pctOf(it.calls, realFeaturePeak)}
                          fade={Math.max(0.55, 1 - i * 0.06)}
                          value={fmtNum(it.calls)}
                          note={`${it.users} 人用过`}
                        />
                      ))}
                    </div>
                  )
                ) : real.by_user.length === 0 ? (
                  <div className="dash-empty">这个区间没有真实用户的调用记录。</div>
                ) : (
                  <div className="dash-hbars">
                    <div className="dash-hbar has-act dash-hbar-th">
                      <span className="dash-hbar-rank">#</span>
                      <span className="dash-hbar-name">用户</span>
                      <span />
                      <span className="dash-hbar-val">调用</span>
                      <span />
                    </div>
                    {real.by_user.slice(0, TOP_N).map((it, i) => (
                      <BarRow
                        key={it.uid}
                        rank={i + 1}
                        name={it.username || it.uid.slice(0, 8)}
                        sub={`${it.days ?? 0} 天有调用 · 最后 ${fmtTime(it.last_ts)}`}
                        pct={pctOf(it.calls, realUserPeak)}
                        fade={Math.max(0.55, 1 - i * 0.06)}
                        value={fmtNum(it.calls)}
                        note="次调用"
                        action={
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={markingUid === it.uid}
                            title="如果这是自测号，标为内部后它会从本板块里消失"
                            onClick={() => void markInternal(it.uid, true)}
                          >
                            {markingUid === it.uid ? "…" : "标内部"}
                          </button>
                        }
                      />
                    ))}
                  </div>
                )}
              </div>

              {/* ---- 已标记清单：默认折叠。17 个账号全摊开占了整整一屏，
                     而它一个月只被打开一两次（标错时来撤销）。 ---- */}
              <div className="card card-pad" style={{ marginTop: 10 }}>
                <details className="dash-fold">
                  <summary>
                    <span className="caret" aria-hidden="true" />
                    <span>已标记的内部账号</span>
                    <span className="meta">
                      共 {real.internal.accounts} 个（人工 {real.internal.marked} · 管理员{" "}
                      {real.internal.admins}）· 本区间 {fmtNum(real.internal.calls)} 次
                    </span>
                  </summary>
                  <div className="dash-fold-body">
                    {real.internal.marked === 0 ? (
                      <p className="hint" style={{ margin: 0 }}>
                        还没有人工标记的账号。管理员由配置（ADMIN_UIDS / ADMIN_USERNAMES）自动排除，
                        不需要标。
                      </p>
                    ) : (
                      real.internal.list.map((it) => (
                        <div
                          className="row"
                          key={it.uid}
                          style={{ gap: 8, alignItems: "center", padding: "4px 0" }}
                        >
                          <span
                            style={{
                              minWidth: 0,
                              flex: 1,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              fontSize: "var(--fs-xs)",
                            }}
                            title={it.uid}
                          >
                            {it.username || it.uid.slice(0, 8)}
                          </span>
                          <span className="meta" style={{ width: 72, textAlign: "right" }}>
                            {fmtNum(it.calls)} 次
                          </span>
                          <span className="meta" style={{ width: 88, textAlign: "right" }}>
                            {it.last_ts ? fmtTime(it.last_ts) : "未调用"}
                          </span>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={markingUid === it.uid}
                            onClick={() => void markInternal(it.uid, false)}
                          >
                            {markingUid === it.uid ? "…" : "取消标记"}
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </details>
              </div>
            </>
          )}
        </section>
      </div>
    </PageShell>
  );
}
