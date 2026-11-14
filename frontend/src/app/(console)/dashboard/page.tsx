"use client";

// P8 · 开发者数据看板
//
// 页面任务：回答开发者的四个问题 ——
//   **有多少人 / 在调什么接口 / 哪里慢 / 哪里在报错**（外加反馈堆了多少）。
//
// 口径定义见 docs/数据看板_2026-10-27/口径与数据字典.md，
// 页面上每一处数字的算法都能在那份文档里查到对应章节。
//
// 三条必须显式标注的口径（不标用户会误判"筛选坏了"）：
//   ① 总用户数是**当前快照**，不随时间筛选变化
//   ② 平均耗时是**均值，不是 p95**（p95 要给每个接口分组算，代价不划算）
//   ③ `/api/chat/stream` 的耗时是**整条流的时长**（含模型生成时间），会把均值拉高。
//      这个数曾经是错的 —— 中间件只测到首帧之前，记成 29ms（真实 15851ms），
//      2026-10-27 已修（包装 body_iterator 计时），`sort=slow` 才第一次真的正确
//
// 本期**不做导出**（2026-10-27 决定）：看板定位是"开发者在浏览器里看数"，
// 把数据搬进 Excel 属于另一种工作流（要权限、要脱敏、要留痕），不在这里顺手做。
//
// 权限：非管理员**当作页面不存在**（notFound），不做"有权限门"式遮挡 ——
// 那样等于告诉对方这里有个开发者看板。后端同名接口对非管理员也返回 404，
// 两层配合才叫真隐藏。真正的安全边界永远在后端。
import { notFound } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { Callout, Seg, SkeletonLines } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { fmtNum } from "@/lib/time";
import type {
  AdminFeedbackResp,
  ApiRankResp,
  DashboardOverview,
  FailuresResp,
  FeedbackDict,
  FeedbackStatus,
  RealUsersResp,
  UserFeedbackItem,
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

const STATUS_CLASS: Record<string, string> = {
  open: "warn",
  doing: "info",
  done: "ok",
  closed: "",
};

const STATUS_ORDER: FeedbackStatus[] = ["open", "doing", "done", "closed"];

const AUTO_REFRESH_MS = 30_000;

// 「真实用户」区块的两个统计维度。切换只改渲染哪张表，不重新请求 ——
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
  const [realDim, setRealDim] = useState<RealDim>("feature");
  // 正在提交标记的 uid（防连点；标记后端只有一条 UPDATE，不需要全局 busy）
  const [markingUid, setMarkingUid] = useState<string | null>(null);
  const [fb, setFb] = useState<AdminFeedbackResp | null>(null);
  const [fbStatus, setFbStatus] = useState("");
  const [dict, setDict] = useState<FeedbackDict | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // 正在回复哪一条（null = 没有展开的回复框）。
  // 与 busyId 分开：busyId 表示"这条正在提交"，replyFor 表示"这条的输入框开着"，
  // 合成一个 state 就会出现"提交时输入框自己收起来又弹开"的抽搐。
  const [replyFor, setReplyFor] = useState<number | null>(null);
  const [replyText, setReplyText] = useState("");
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
    api.admin
      .feedback(fbStatus, 30, 0)
      .then(setFb)
      .catch(() => setFb(null));
  }, [days, sort, fbStatus, isAdmin]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!isAdmin) return;
    api.admin
      .dict()
      .then(setDict)
      .catch(() => setDict(null));
  }, [isAdmin]);

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

  const move = useCallback(
    async (item: UserFeedbackItem, next: string) => {
      setBusyId(item.id);
      try {
        await api.admin.patchFeedback(item.id, next);
        load();
      } catch (e) {
        setOvErr(e instanceof Error ? e.message : "状态更新失败");
      } finally {
        setBusyId(null);
      }
    },
    [load]
  );

  /**
   * 回复某条反馈。
   *
   * 传空串 = 撤回（后端 set_reply 允许清空，但状态不回退）。
   * 成功后重拉列表而不是本地改 state：回复可能顺手把 open 推成 doing，
   * 本地改就得把"状态也会变"这条规则复制一份到前端 —— 那正是上个月
   * 「前端能点、后端 400」的成因。让后端说完，前端照着画。
   */
  const sendReply = useCallback(
    async (item: UserFeedbackItem, text: string) => {
      setBusyId(item.id);
      try {
        await api.admin.replyFeedback(item.id, text.trim());
        setReplyFor(null);
        setReplyText("");
        load();
      } catch (e) {
        setOvErr(e instanceof Error ? e.message : "回复失败");
      } finally {
        setBusyId(null);
      }
    },
    [load]
  );

  /**
   * 标记 / 取消标记内部账号。
   *
   * 成功后**重拉整块**，而不是本地把这一行从列表里移掉：一次标记会同时改变
   * 「真实调用次数、活跃用户数、人均、两个维度的排行、四桶分解」——
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
  const peakCalls = ov ? Math.max(1, ...ov.series.map((x) => x.calls)) : 1;
  // 真实用户趋势的横轴基准单独算：两个区块的量级差一个数量级时，
  // 共用基准会让真实用户那条几乎看不见（看着像"没人用"）
  const realPeak = real ? Math.max(1, ...real.series.map((x) => x.calls)) : 1;

  return (
    <PageShell
      eyebrow="管理 · 内部"
      title="数据看板"
      // 一句话讲清三件事：谁跟随筛选、谁不跟随、口径怎么算。
      //
      // 原来这四句话分散在四张卡的标签里，每张卡挂一个括号，卡面被括号塞满；
      // 而「口径见 口径与数据字典.md」是个仓库文件名 —— 线上用户点不开、也搜不到，
      // 属于只对开发者有意义的死引用，一并删掉。
      sub={
        <span className="meta">
          数字跟随上方时间范围，标「全量」的是当前快照。成功率与失败次数已排除未登录探测与空总结，
          耗时取均值、不是 p95。采集延迟 ≤ 3 秒{auto ? "，页面每 30 秒自动刷新" : ""}
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
            这个区间还没有调用记录。刚上线时是正常的 —— 采集是批量落库，最多滞后 3 秒；
            若持续为空，检查 <code>api_call_logs</code> 表是否有行、以及访问是否走了
            <code>/api/</code> 前缀。
          </Callout>
        ) : null}

        {/* ============ 第一排：用户与调用总量 ============ */}
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

        {/* ============ 第二排：耗时与反馈 ============ */}
        <div className="grid4">
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
          </div>
          <div className="card card-pad stat">
            <div className="n">{ov ? fmtNum(ov.feedback.open) : "—"}</div>
            <div className="l">
              待处理反馈 <span className="stat-scope">全量</span>
            </div>
          </div>
        </div>

        {/* ============ 趋势 ============ */}
        <section>
          <p className="sec-label">每日调用量</p>
          <div className="card card-pad">
            {!ov ? (
              <SkeletonLines widths={["100%", "88%", "72%"]} />
            ) : ov.series.every((s) => s.calls === 0) ? (
              <p className="hint" style={{ marginTop: 0 }}>
                区间内没有调用，趋势图暂无数据。
              </p>
            ) : (
              <>
                {ov.series.map((s) => {
                  const failPct = s.calls ? Math.round((s.failed / s.calls) * 100) : 0;
                  return (
                    <div
                      className="row"
                      key={s.day}
                      style={{ alignItems: "center", gap: 10, marginBottom: 6 }}
                    >
                      <span className="meta" style={{ width: 52, flex: "0 0 52px" }}>
                        {s.day.slice(5)}
                      </span>
                      <div className="bar" style={{ flex: 1 }}>
                        <i style={{ width: `${Math.round((s.calls / peakCalls) * 100)}%` }} />
                      </div>
                      <span className="meta" style={{ width: 84, textAlign: "right" }}>
                        {fmtNum(s.calls)}
                        {s.failed ? ` · 失败${failPct}%` : ""}
                      </span>
                    </div>
                  );
                })}
                <p className="hint">
                  按<b>自然日</b>切分（业务时区 {ov.range.tz}）。全部为 0 的日子也画出来 ——
                  跳过空日子会让趋势看起来比实际更连贯。
                </p>
              </>
            )}
          </div>
        </section>

        {/* ============ 真实用户使用数据（与上方并列的另一套口径） ============ */}
        {/*
          为什么不做成上方总览的一个筛选开关：
            · 两者的**分母不同**（上面是全量，这里是排除内部账号后的子集），
              一个开关会让人以为"筛掉了数据"，实际是换了统计对象；
            · 成功率、平均耗时这些指标换分母就失去可比性，混在一起会引出
              "为什么切了筛选成功率也变了"这种没法解释的问题。
          代价是要用户自己看两块，所以下面用「口径对照」把两块**对上账**：
              全量 = 真实 + 内部 + 已注销 + 未登录
          对不上账的看板，看第二眼就没人信了。
        */}
        <section>
          <p className="sec-label">
            真实用户使用数据{" "}
            <span className="meta">（另一套口径：排除内部/测试账号与管理员）</span>
          </p>

          {!real ? (
            <div className="card card-pad">
              <SkeletonLines widths={["100%", "88%", "72%"]} />
            </div>
          ) : (
            <>
              {/* 一个都没标记时必须说实话。否则这里与上方数字完全相同，
                  用户会以为"功能没生效"，而不是"还没配置"。 */}
              {real.internal.marked === 0 ? (
                <Callout icon="info">
                  还没有标记任何内部/测试账号，所以本视图与上方全量<b>暂时相同</b>。
                  在下面「已标记的内部账号」里逐个标记，或跑
                  <code>tools/mark_internal_users.py</code> 批量回填；
                  标记之后这里才只算真实用户。
                </Callout>
              ) : null}

              <div className="grid4" style={{ marginBottom: 10 }}>
                <div className="card card-pad stat">
                  <div className="n">{fmtNum(real.calls.real)}</div>
                  <div className="l">
                    真实用户调用 <span className="stat-scope">区间</span>
                  </div>
                  <div className="meta">占全量 {fmtPct(real.calls.share)}</div>
                </div>
                <div className="card card-pad stat">
                  <div className="n">{fmtNum(real.users.active)}</div>
                  <div className="l">
                    活跃真实用户 <span className="stat-scope">区间</span>
                  </div>
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
                  <div className="n">{fmtNum(real.users.total)}</div>
                  <div className="l">
                    真实账号 <span className="stat-scope">全量</span>
                  </div>
                  <div className="meta">区间新增 {fmtNum(real.users.new)}</div>
                </div>
              </div>

              <div className="grid2">
                {/* ---- 口径对照：让两块数字能对上账 ---- */}
                <div className="card card-pad">
                  <div
                    className="tr apis th"
                    style={{ display: "grid", gridTemplateColumns: "1fr 72px 60px", gap: 8 }}
                  >
                    <span>口径分解</span>
                    <span>调用</span>
                    <span>占比</span>
                  </div>
                  {(
                    [
                      { key: "real", label: "真实用户", v: real.calls.real, cls: "" },
                      { key: "internal", label: "内部/测试账号", v: real.calls.internal, cls: "" },
                      { key: "orphan", label: "已注销/未知账号", v: real.calls.orphan, cls: "" },
                      { key: "anonymous", label: "未登录请求", v: real.calls.anonymous, cls: "" },
                    ] as const
                  ).map((b) => (
                    <div
                      className="tr apis"
                      key={b.key}
                      style={{ display: "grid", gridTemplateColumns: "1fr 72px 60px", gap: 8 }}
                    >
                      <span style={{ fontSize: "var(--fs-xs)", color: "var(--ink)" }}>{b.label}</span>
                      <span>{fmtNum(b.v)}</span>
                      <span className="meta">
                        {fmtPct(real.calls.total ? b.v / real.calls.total : null)}
                      </span>
                    </div>
                  ))}
                  <div
                    className="tr apis"
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 72px 60px",
                      gap: 8,
                      borderTop: "1px solid var(--line)",
                      marginTop: 4,
                      paddingTop: 6,
                    }}
                  >
                    <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-2)" }}>
                      合计（= 上方「API 调用次数」）
                    </span>
                    <span>{fmtNum(real.calls.total)}</span>
                    <span className="meta">100%</span>
                  </div>
                  <p className="hint" style={{ marginBottom: 0 }}>
                    四类互斥且穷尽，加起来应当<b>等于</b>上方全量调用数。对不上就说明有调用
                    没被归进任何一类，那是要修的 bug。
                  </p>
                </div>

                {/* ---- 趋势：调用量 + 活跃用户数双序列 ---- */}
                <div className="card card-pad">
                  {real.series.every((s) => s.calls === 0) ? (
                    <p className="hint" style={{ marginTop: 0 }}>
                      区间内没有真实用户的调用。
                    </p>
                  ) : (
                    <>
                      {real.series.map((s) => (
                        <div
                          className="row"
                          key={s.day}
                          style={{ alignItems: "center", gap: 10, marginBottom: 6 }}
                        >
                          <span className="meta" style={{ width: 52, flex: "0 0 52px" }}>
                            {s.day.slice(5)}
                          </span>
                          <div className="bar" style={{ flex: 1 }}>
                            <i style={{ width: `${Math.round((s.calls / realPeak) * 100)}%` }} />
                          </div>
                          <span className="meta" style={{ width: 96, textAlign: "right" }}>
                            {fmtNum(s.calls)} · {s.users} 人
                          </span>
                        </div>
                      ))}
                      <p className="hint" style={{ marginBottom: 0 }}>
                        左侧条是<b>调用量</b>，右侧「N 人」是当天有调用的<b>真实用户数</b>。
                        调用量涨而人数不涨，说明是同一批人在反复用（或有人在脚本刷）。
                      </p>
                    </>
                  )}
                </div>
              </div>

              {/* ---- 两个统计维度：只换渲染哪张表，不再请求 ---- */}
              <div className="card card-pad" style={{ marginTop: 10 }}>
                <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                  <Seg
                    ariaLabel="统计维度"
                    options={REAL_DIM_OPTIONS}
                    value={realDim}
                    onChange={(v) => setRealDim(v as RealDim)}
                  />
                  <span className="meta" style={{ marginLeft: "auto" }}>
                    共 {fmtNum(real.calls.real)} 次调用 · {real.users.active} 个真实用户
                  </span>
                </div>

                {realDim === "feature" ? (
                  real.by_feature.length === 0 ? (
                    <p className="hint" style={{ marginTop: 0 }}>
                      这个区间没有真实用户的调用记录。
                    </p>
                  ) : (
                    <>
                      <div
                        className="tr apis th"
                        style={{ display: "grid", gridTemplateColumns: "1fr 64px 64px", gap: 8 }}
                      >
                        <span>功能</span>
                        <span>次数</span>
                        <span>用户数</span>
                      </div>
                      {real.by_feature.map((it) => (
                        <div
                          className="tr apis"
                          key={`${it.method} ${it.path}`}
                          style={{ display: "grid", gridTemplateColumns: "1fr 64px 64px", gap: 8 }}
                          title={`${it.method} ${it.path}`}
                        >
                          <span style={{ minWidth: 0, overflow: "hidden" }}>
                            <span
                              style={{
                                display: "block",
                                fontSize: "var(--fs-xs)",
                                color: "var(--ink)",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {it.label}
                            </span>
                            <span
                              className="mono"
                              style={{
                                display: "block",
                                fontSize: "var(--fs-xs)",
                                color: "var(--text-3)",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {it.method} {it.path}
                            </span>
                          </span>
                          <span>{fmtNum(it.calls)}</span>
                          <span className="meta">{it.users}</span>
                        </div>
                      ))}
                      <p className="hint" style={{ marginBottom: 0 }}>
                        「用户数」是<b>用过这个功能的真实用户数</b>。次数高但用户数只有 1 ——
                        说明是单人在反复调用，不是功能受欢迎。
                      </p>
                    </>
                  )
                ) : real.by_user.length === 0 ? (
                  <p className="hint" style={{ marginTop: 0 }}>
                    这个区间没有真实用户的调用记录。
                  </p>
                ) : (
                  <>
                    <div
                      className="tr apis th"
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 60px 54px 92px 64px",
                        gap: 8,
                      }}
                    >
                      <span>账号</span>
                      <span>调用</span>
                      <span>天数</span>
                      <span>最后</span>
                      <span />
                    </div>
                    {real.by_user.map((it) => (
                      <div
                        className="tr apis"
                        key={it.uid}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "1fr 60px 54px 92px 64px",
                          gap: 8,
                          alignItems: "center",
                        }}
                        title={it.uid}
                      >
                        <span
                          style={{
                            minWidth: 0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            fontSize: "var(--fs-xs)",
                            color: "var(--ink)",
                          }}
                        >
                          {it.username || it.uid.slice(0, 8)}
                        </span>
                        <span>{fmtNum(it.calls)}</span>
                        <span className="meta">{it.days}</span>
                        <span className="meta">{fmtTime(it.last_ts)}</span>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={markingUid === it.uid}
                          title="如果这是自测号，标为内部账号后它会从本视图里消失"
                          onClick={() => void markInternal(it.uid, true)}
                        >
                          {markingUid === it.uid ? "…" : "标为内部"}
                        </button>
                      </div>
                    ))}
                    <p className="hint" style={{ marginBottom: 0 }}>
                      标错的账号可以在下面「已标记的内部账号」里取消。
                      「天数」= 这个账号在区间内有调用的自然日数。
                    </p>
                  </>
                )}
              </div>

              {/* ---- 已标记清单：包含本区间 0 次调用的，否则标错了没法撤回 ---- */}
              <div className="card card-pad" style={{ marginTop: 10 }}>
                <div className="row" style={{ gap: 8, marginBottom: 8 }}>
                  <span className="sec-label" style={{ margin: 0 }}>
                    已标记的内部账号
                  </span>
                  <span className="meta">
                    共 {real.internal.accounts} 个（人工标记 {real.internal.marked} · 管理员{" "}
                    {real.internal.admins}）· 本区间产生 {fmtNum(real.internal.calls)} 次调用
                  </span>
                </div>
                {real.internal.marked === 0 ? (
                  <p className="hint" style={{ margin: 0 }}>
                    还没有人工标记的账号。管理员由配置（ADMIN_UIDS / ADMIN_USERNAMES）自动排除，
                    不需要标。
                  </p>
                ) : (
                  <>
                    {real.internal.list.map((it) => (
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
                    ))}
                    <p className="hint" style={{ marginBottom: 0 }}>
                      取消标记后它会重新算进真实用户 —— 上一行「真实用户调用」会立刻变大。
                    </p>
                  </>
                )}
              </div>

              <p className="hint" style={{ margin: "8px 0 0" }}>
                口径定义：<b>真实用户</b> = 账号存在、未被标记为内部、且不在管理员名单里的
                uid 所产生的调用。<b>未登录请求</b>（打开页面时的身份探测、注册/登录本身）
                不属于任何用户，因此单列，不计入真实用户。明细口径见
                <code>docs/数据看板_2026-10-27/口径与数据字典.md</code> §2.8。
              </p>
            </>
          )}
        </section>

        {/* ============ 失败原因（不用去服务器 grep 日志） ============ */}
        <section>
          <p className="sec-label">
            失败原因{" "}
            <span className="meta">（每条都标了是什么错；预期行为不算系统报错）</span>
          </p>
          <div className="card card-pad">
            {!fails ? (
              <SkeletonLines widths={["100%", "92%", "80%"]} />
            ) : fails.recent.length === 0 ? (
              <p className="hint" style={{ marginTop: 0 }}>
                这个区间没有任何失败调用 —— 干净。
              </p>
            ) : (
              <>
                <p className="hint" style={{ marginTop: 0 }}>
                  需要处理 <b>{fails.need_attention.length}</b> 条
                  {fails.ignorable.length > 0 ? (
                    <>
                      {" "}
                      · 可忽略 <b>{fails.ignorable.length}</b> 条（未登录探测 / 今天还没跑完对话所以没有总结）
                    </>
                  ) : null}
                </p>

                {fails.need_attention.length === 0 ? (
                  <p className="hint">没有需要处理的问题。</p>
                ) : (
                  fails.need_attention.slice(0, 8).map((f) => (
                    <div
                      key={`${f.method} ${f.path} ${f.ts ?? 0}`}
                      className="row"
                      style={{ alignItems: "flex-start", gap: 10, padding: "7px 0" }}
                    >
                      <span className="ctag bad">{f.status}</span>
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span style={{ display: "block", fontSize: "var(--fs-xs)", color: "var(--text-1)" }}>
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
                  ))
                )}

                {fails.ignorable.length > 0 ? (
                  <p className="hint" style={{ marginBottom: 0 }}>
                    可忽略的：
                    {fails.top
                      .filter((x) => x.expected)
                      .slice(0, 3)
                      .map((x) => `${x.label}（${x.status}）× ${x.calls ?? 1}`)
                      .join("、") || "见上"}
                    。这些是打开页面时的身份探测、以及总结还没生成时的空态，不是系统报错。
                  </p>
                ) : null}
              </>
            )}
          </div>
        </section>

        <div className="grid2">
          {/* ============ 接口排行 ============ */}
          <section>
            <p className="sec-label">
              功能使用排行{" "}
              {apis ? <span className="meta">共 {apis.total_paths} 个功能被调用过</span> : null}
            </p>
            <div className="card card-pad">
              <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                {SORT_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    className={`btn btn-sm ${sort === o.value ? "btn-primary" : "btn-ghost"}`}
                    aria-pressed={sort === o.value}
                    title={o.hint}
                    onClick={() => setSort(o.value)}
                  >
                    {o.label}
                  </button>
                ))}
              </div>

              {!apis ? (
                <SkeletonLines widths={["100%", "90%", "80%", "70%"]} />
              ) : apis.items.length === 0 ? (
                <p className="hint" style={{ marginTop: 0 }}>
                  {sort === "fail"
                    ? "这个区间没有出过错 —— 是好事。"
                    : sort === "slow"
                      ? "这个区间里没有用过 3 次以上的功能，还算不出有参考价值的平均耗时。"
                      : "这个区间还没有调用记录。"}
                </p>
              ) : (
                <>
                  <div
                    className="tr apis th"
                    style={{ display: "grid", gridTemplateColumns: "1fr 44px 44px 66px", gap: 8 }}
                  >
                    <span>功能</span>
                    <span>次数</span>
                    <span>失败</span>
                    <span>平均耗时</span>
                  </div>
                  {apis.items.map((it) => (
                    <div
                      className="tr apis"
                      key={`${it.method} ${it.path}`}
                      style={{ display: "grid", gridTemplateColumns: "1fr 44px 44px 66px", gap: 8 }}
                      title={`${it.method} ${it.path}`}
                    >
                      {/* 中文业务名是主信息（评委/老师看这行），
                          原始路由路径退成第二行的浅色小字（开发者排查用）。
                          这一层分工在后端 core/api_labels.py 里做，前端不拼中文。 */}
                      <span style={{ minWidth: 0, overflow: "hidden" }}>
                        <span
                          style={{
                            display: "block",
                            fontSize: "var(--fs-xs)",
                            color: "var(--ink)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {it.label}
                        </span>
                        <span
                          className="mono"
                          style={{
                            display: "block",
                            fontSize: "var(--fs-xs)",
                            color: "var(--text-3)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {it.method} {it.path}
                        </span>
                      </span>
                      <span>{fmtNum(it.calls)}</span>
                      <span className="meta" style={{ color: it.failed ? "var(--red-ink)" : undefined }}>
                        {it.failed || "—"}
                      </span>
                      <span className="meta">{it.avg_ms}ms</span>
                    </div>
                  ))}
                  <p className="hint">
                    同一个功能的多次调用合并计数 —— 比如打开不同会话都算「对话 · 打开会话」，
                    不会因为换了个 id 就变成一个新功能。打开本页本身的请求不计入。
                    其中「账号 · 登录方式」「账号 · 当前用户」这类是打开页面时自动发起的，
                    不代表用户主动操作。
                  </p>
                </>
              )}
            </div>
          </section>

          {/* ============ 反馈 ============ */}
          <section>
            <p className="sec-label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              用户反馈 {fb ? <span className="meta">共 {fb.total} 条</span> : null}
            </p>
            <div className="card card-pad">
              <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                <button
                  type="button"
                  className={`btn btn-sm ${fbStatus === "" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setFbStatus("")}
                >
                  全部
                </button>
                {STATUS_ORDER.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={`btn btn-sm ${fbStatus === s ? "btn-primary" : "btn-ghost"}`}
                    onClick={() => setFbStatus(s)}
                  >
                    {dict?.statuses.find((x) => x.value === s)?.label ?? s}
                    {fb ? ` ${fb.counts[s] ?? 0}` : ""}
                  </button>
                ))}
              </div>

              {!fb ? (
                <SkeletonLines widths={["100%", "86%", "70%"]} />
              ) : fb.items.length === 0 ? (
                <p className="hint" style={{ marginTop: 0 }}>
                  {fbStatus
                    ? "这个状态下没有反馈，切「全部」看看。"
                    : "还没有反馈。用户在「设置」页可以提交，分类、文案、来源页面都会记下来。"}
                </p>
              ) : (
                <div style={{ maxHeight: 460, overflowY: "auto" }}>
                  {fb.items.map((it) => (
                    <div
                      key={it.id}
                      style={{ padding: "9px 0", borderBottom: "1px solid var(--line)" }}
                    >
                      <div className="row" style={{ gap: 8, alignItems: "center" }}>
                        <span className="ctag info">{it.category_label}</span>
                        <span className={`ctag ${STATUS_CLASS[it.status] ?? ""}`}>
                          {it.status_label}
                        </span>
                        <span className="meta" style={{ marginLeft: "auto" }}>
                          {it.username || it.uid.slice(0, 8)} · {fmtTime(it.created_at)}
                        </span>
                      </div>
                      <div style={{ marginTop: 5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                        {it.content}
                      </div>
                      {it.contact || it.page ? (
                        <div className="meta" style={{ marginTop: 4 }}>
                          {it.page ? `来自 ${it.page}` : ""}
                          {it.contact ? ` · 联系 ${it.contact}` : ""}
                        </div>
                      ) : null}
                      {/* 已回复的先把回复摆出来：管理员最常问的是"我上次回过他什么"。
                          正在编辑这条时藏起来 —— 否则同一段话会在上面显示一遍、
                          又在下面的输入框里出现一遍，看着像重复渲染的 bug。 */}
                      {it.reply && replyFor !== it.id ? (
                        <div className="fb-reply">
                          <div className="fb-reply-head">
                            我的回复
                            {it.replied_at ? ` · ${fmtTime(it.replied_at)}` : ""}
                          </div>
                          <div className="fb-reply-body">{it.reply}</div>
                        </div>
                      ) : null}

                      {/* 只渲染流转表允许的下一步：不允许回退（done 不能退回 doing），
                          前端不给按钮，后端也会 409 兜住 */}
                      <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                        {(dict?.transitions[it.status] ?? []).map((next) => (
                          <button
                            key={next}
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={busyId === it.id}
                            onClick={() => void move(it, next)}
                          >
                            → {dict?.statuses.find((x) => x.value === next)?.label ?? next}
                          </button>
                        ))}
                        {/* 回复按钮对任何状态都开放：已经关掉的反馈也可能需要回一句
                            「这个不做，原因是…」，那正是最该回的一类。 */}
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={busyId === it.id}
                          onClick={() => {
                            const opening = replyFor !== it.id;
                            setReplyFor(opening ? it.id : null);
                            setReplyText(opening ? it.reply : "");
                          }}
                        >
                          {replyFor === it.id ? "收起" : it.reply ? "改回复" : "回复用户"}
                        </button>
                      </div>

                      {replyFor === it.id ? (
                        <div style={{ marginTop: 8 }}>
                          <textarea
                            className="input"
                            value={replyText}
                            rows={3}
                            placeholder="回给提意见的人。会显示在他「设置 → 我提过的」那一列表里。"
                            disabled={busyId === it.id}
                            onChange={(e) =>
                              setReplyText(e.target.value.slice(0, dict?.limits.max_reply ?? 1000))
                            }
                          />
                          <div
                            className="row"
                            style={{ gap: 8, marginTop: 4, alignItems: "center" }}
                          >
                            <span className="meta">
                              {replyText.trim().length} / {dict?.limits.max_reply ?? 1000}
                              {it.reply && !replyText.trim() ? " · 清空后发送 = 撤回回复" : ""}
                            </span>
                            <div style={{ marginLeft: "auto" }} className="row">
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={busyId === it.id}
                                onClick={() => {
                                  setReplyFor(null);
                                  setReplyText("");
                                }}
                              >
                                取消
                              </button>
                              <button
                                type="button"
                                className="btn btn-primary btn-sm"
                                disabled={busyId === it.id || replyText.trim() === it.reply}
                                onClick={() => void sendReply(it, replyText)}
                              >
                                {busyId === it.id ? "发送中…" : "发送"}
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>

        {/* ============ 口径说明（放在页尾，不占首屏） ============ */}
        <section>
          <p className="sec-label">这张表在记什么</p>
          <div className="card card-pad">
            <p className="hint" style={{ marginTop: 0 }}>
              只记录 <code>/api/</code> 下的请求；排除 <code>/health</code>、
              <code>/api/metrics</code>（采集器自己会打，记进去等于自己统计自己）、
              以及 <code>/api/admin/*</code>（看板自己刷新不该产生业务数据）。
              请求以<b>路由模板</b>为键聚合，所以 <code>/api/sessions/8f3a…</code> 与
              <code>/api/sessions/1c2b…</code> 算同一个接口。
            </p>
            <p className="hint">
              采集走内存缓冲 + 批量写库（每 3 秒或攒够 50 条），所以数字最多滞后 3 秒；
              <code>kill -9</code> 时缓冲里最多丢 50 条 —— 这是统计，不是账本。
              平均值是<b>算术平均</b>：个别长连接（如 <code>/api/chat/stream</code> 的整条流时长）
              会把它拉高，看单接口时请以排行里的"均值"列为准。
            </p>
            <p className="hint" style={{ marginBottom: 0 }}>
              本页不提供导出：需要明细请直接查 <code>api_call_logs</code> 表（口径与字段见文档 §3.3）。
            </p>
          </div>
        </section>
      </div>
    </PageShell>
  );
}
