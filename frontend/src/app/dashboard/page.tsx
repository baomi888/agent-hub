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
import ConsoleShell from "@/components/console/ConsoleShell";
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

  return (
    <ConsoleShell
      active="dashboard"
      eyebrow="管理 · 内部"
      title="数据看板"
      sub={
        <>
          有多少人、在调什么、哪里慢、哪里在报错。
          <span className="meta">
            {" "}
            口径见 口径与数据字典.md；采集延迟 ≤ 3 秒{auto ? "，页面每 30 秒自动刷新" : ""}
            {fetchedAt ? `（上次 ${fmtClock(fetchedAt)}）` : ""}。
          </span>
        </>
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
              注册用户 <span className="meta">（当前快照，不随筛选变化）</span>
            </div>
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
            <div className="l">
              成功率 <span className="meta">（不含未登录探测、报告空态）</span>
            </div>
          </div>
        </div>

        {/* ============ 第二排：耗时与反馈 ============ */}
        <div className="grid4">
          <div className="card card-pad stat">
            <div className="n">{ov ? `${fmtNum(ov.calls.avg_ms)} ms` : "—"}</div>
            <div className="l">
              平均耗时 <span className="meta">（均值，非 p95）</span>
            </div>
          </div>
          <div className="card card-pad stat">
            <div className="n">{ov ? `${fmtNum(ov.calls.max_ms)} ms` : "—"}</div>
            <div className="l">最大耗时</div>
          </div>
          <div className="card card-pad stat">
            <div className="n">{ov ? fmtNum(ov.calls.failed) : "—"}</div>
            <div className="l">失败次数（不含未登录、空总结）</div>
          </div>
          <div className="card card-pad stat">
            <div className="n">{ov ? fmtNum(ov.feedback.open) : "—"}</div>
            <div className="l">
              待处理反馈 <span className="meta">（全量，不受筛选影响）</span>
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
    </ConsoleShell>
  );
}
