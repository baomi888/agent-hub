"use client";

// P7 · 设置
//
// 页面任务：把「我用掉多少、还剩多少、系统在替我跑什么、我的数据去哪了」一次讲清楚。
// 视觉分工：**左列是可改的设置，右列是只读的系统状态** —— 用「可编辑 / 只读」
// 分开，用户就不会去点不该点的地方。
//
// 数据来源分三档，页面上必须能分出来：
//   ① 真实可用 —— 账号（/api/auth/me）、用量（/api/usage/summary）、资料库（/api/kb/）
//   ② 本地偏好 —— 字号、深色、检索条数（真实生效，只存浏览器）
//   ③ 接口待上线 —— 工具表、调度进程（队友分支的端点，当前后端 404）
// 第 ③ 档绝不假装可用：明说 + 挂演示数据标记。
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import ConsoleShell, { useConsoleUser } from "@/components/console/ConsoleShell";
import CornIcon from "@/components/CornIcon";
import FeedbackCard from "@/components/FeedbackCard";
import { Callout, NotReady, Seg, SkeletonLines, Switch } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import {
  DEFAULT_TOP_K,
  FONT_SCALE_OPTIONS,
  applyFontScale,
  patchPrefs,
  readPrefs,
  readTheme,
  writeTheme,
  type FontScale,
} from "@/lib/consolePrefs";
import { DEMO_HEALTH, DEMO_JOBS, DEMO_TOOLS } from "@/lib/consoleDemo";
import { errProbe, toProbe, type ProbeState } from "@/lib/probe";
import { fmtNum } from "@/lib/time";
import type { AgentTool, KbInfo, ScheduleHealth, ScheduleJob, UsageSummary } from "@/lib/types";

/** 配额维度的中文名。顺序即展示顺序 */
const QUOTA_META: { key: string; label: string; unit: string }[] = [
  { key: "ask", label: "问答", unit: "次" },
  { key: "embed", label: "向量化", unit: "次" },
  { key: "auto", label: "自动（定时）", unit: "次" },
];

const TOP_K_OPTIONS = [
  { value: "3", label: "3 段 · 更快", hint: "问题很具体时够用，回答也更快" },
  { value: "5", label: "5 段 · 推荐", hint: "多数情况下的平衡点" },
  { value: "8", label: "8 段 · 更全", hint: "问题很宽、需要综合多处资料时用" },
];

export default function SettingsPage() {
  const router = useRouter();
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;

  const [usage, setUsage] = useState<ProbeState<UsageSummary>>({ state: "loading" });
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [kbsLoading, setKbsLoading] = useState(true);
  const [tools, setTools] = useState<ProbeState<AgentTool[]>>({ state: "loading" });
  const [health, setHealth] = useState<ProbeState<ScheduleHealth>>({ state: "loading" });
  const [jobs, setJobs] = useState<ProbeState<ScheduleJob[]>>({ state: "loading" });

  // 本地偏好：挂载后读，避免 SSR/CSR 首帧不一致
  const [dark, setDark] = useState(false);
  const [fontScale, setFontScale] = useState<FontScale>("m");
  const [topK, setTopK] = useState(String(DEFAULT_TOP_K));

  useEffect(() => {
    // 本地偏好在挂载后读，避免 SSR/CSR 首帧不一致。
    // 延到下一个宏任务再 setState：直接在 effect 里同步 setState 会被判成级联渲染
    // （eslint react-hooks/set-state-in-effect），这是本仓库既有的做法
    const t = window.setTimeout(() => {
      setDark(readTheme());
      const saved = readPrefs();
      const fs = saved.fontScale;
      if (fs === "auto" || fs === "s" || fs === "m" || fs === "l") setFontScale(fs);
      const tk = saved.topK;
      if (typeof tk === "number") setTopK(String(tk));
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    // 未登录不发请求：登录前发的请求必然是 401（卡片会全部钉在错误态），
    // 登录成功会经 AUTHORIZED_EVENT 同步进 auth.me（见 useAuth / api.ts）→ 依赖变化补加载
    if (!authedId) return;
    api.usageSummary().then((d) => setUsage(toProbe(d))).catch((e) => setUsage(errProbe(e)));
    api.agent.tools().then((d) => setTools(toProbe(d))).catch((e) => setTools(errProbe(e)));
    api.schedule.health().then((d) => setHealth(toProbe(d))).catch((e) => setHealth(errProbe(e)));
    api.schedule.jobs().then((d) => setJobs(toProbe(d))).catch((e) => setJobs(errProbe(e)));
    api
      .listKbs()
      .then((r) => setKbs(r.kbs))
      .catch(() => {
        /* 资料库拉不到不影响其他卡片 */
      })
      .finally(() => setKbsLoading(false));
  }, [authedId]);

  const onToggleDark = useCallback((next: boolean) => {
    writeTheme(next);
    setDark(next);
  }, []);

  const onFontScale = useCallback((f: FontScale) => {
    applyFontScale(f);
    setFontScale(f);
  }, []);

  const onTopK = useCallback((v: string) => {
    patchPrefs({ topK: Number(v) });
    setTopK(v);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* 退出失败也要回工作台：Cookie 可能已经过期 */
    }
    // 用路由跳转而不是 window.location：整页刷新会丢掉 Back 历史。
    // 登出后回到工作台，那里的 useAuth 会重新探活 → 拿不到身份 → 自然收回登录门。
    router.push("/");
    router.refresh();
  }, [router]);

  return (
    <ConsoleShell
      active="settings"
      eyebrow="P7 · /settings"
      title="设置"
      sub="账号、外观、检索、隐私、用量，以及知穗当前能调用哪些工具、调度进程活着没有。"
    >
      <div className="grid2">
        {/* ============ 左列：可改的设置 ============ */}
        <div className="stack-20">
          <section>
            <p className="sec-label">账号</p>
            <AccountCard onLogout={logout} />
            <p className="hint">
              「修改密码」还缺后端端点 <code>/api/auth/password</code>（跨 3 轮未修），先置灰。
            </p>
          </section>

          <section>
            <p className="sec-label">外观</p>
            <div className="card card-pad">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "var(--fs-lg)" }}>正文字号</span>
                <Seg
                  ariaLabel="正文字号"
                  options={FONT_SCALE_OPTIONS}
                  value={fontScale}
                  onChange={onFontScale}
                />
              </div>
              <div
                className="row"
                style={{ justifyContent: "space-between", alignItems: "center", marginTop: 14 }}
              >
                <span style={{ fontSize: "var(--fs-lg)" }}>深色纸面</span>
                <Switch checked={dark} onChange={onToggleDark} label="深色纸面" />
              </div>
              <p className="hint">
                字号档位存本地，正文与标题按比例联动（由 <code>--msg-fs</code> 派生出{" "}
                <code>--hero-fs</code>）。
              </p>
            </div>
          </section>

          <section>
            <p className="sec-label">检索</p>
            <div className="card card-pad">
              <div className="field">
                <span className="f">每次回答参考几段资料</span>
                <Seg
                  ariaLabel="每次回答参考几段资料"
                  options={TOP_K_OPTIONS}
                  value={topK}
                  onChange={onTopK}
                  stretch
                />
              </div>
              <div className="divide" />
              <div style={{ fontSize: "var(--fs-md)" }}>资料怎么切</div>
              <p className="hint" style={{ marginTop: 6 }}>
                切片档位（精细 / 均衡 / 完整）是<b>按资料库</b>各自记的，在资料库面板里给每个库单独选 ——
                教材和访谈实录本来就该用不同的切法，全局一套反而会互相迁就。
              </p>
              <Link className="btn btn-ghost btn-sm" href="/?kb=1" style={{ marginTop: 10 }}>
                <CornIcon name="book" />
                去资料库设定
              </Link>
            </div>
          </section>

          <section>
            <p className="sec-label">资料与隐私</p>
            <div className="card card-pad">
              <div className="card-head">
                <span className="card-title">我的资料库</span>
                <span className="meta">
                  {kbsLoading ? "读取中…" : `${kbs.length} 个 · 共 ${fmtNum(kbs.reduce((s, k) => s + (k.chunks || 0), 0))} 片段`}
                </span>
              </div>
              {kbsLoading ? (
                <div style={{ marginTop: 12 }}>
                  <SkeletonLines widths={["60%", "44%"]} />
                </div>
              ) : kbs.length === 0 ? (
                <p className="hint">还没有资料库。上传讲义或论文后，在这里能看到它占了多少片段。</p>
              ) : (
                <div style={{ marginTop: 10 }}>
                  {kbs.map((k) => (
                    <div className="run" key={k.kb_id} style={{ gridTemplateColumns: "1fr auto" }}>
                      <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                        {k.name}
                      </span>
                      <span className="meta">{fmtNum(k.chunks || 0)} 片段</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="divide" />
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: "var(--fs-md)" }}>数据留存</div>
                  <div className="meta">对话会发给模型服务做推理；知识库与索引留在本机。</div>
                </div>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm btn-danger"
                  disabled
                  title="后端尚无清空端点"
                >
                  清空我的数据
                </button>
              </div>
              <p className="hint">
                「选择性索引」（只让勾选的资料进索引）与「一键清空」都需要后端配合，
                现在还没有对应端点 —— 放了开关却不生效，比不放更糟，所以这两项先置灰。
              </p>
            </div>
          </section>

          <section>
            <p className="sec-label">用量与反馈</p>
            <div className="card card-pad">
              {usage.state === "loading" && <SkeletonLines widths={["100%", "72%", "52%"]} />}
              {usage.state === "error" && (
                <Callout icon="warn">用量读不到：{usage.message}。其他设置不受影响。</Callout>
              )}
              {usage.state === "ready" && <QuotaBars summary={usage.data} />}
              {usage.state === "absent" && (
                <NotReady what="用量接口（/api/usage/summary）" />
              )}
            </div>
          </section>

          {/* 反馈入口：产品建议要有落点。和上面的"用量"分开一节 ——
              前者是"我用了多少"（只读），后者是"我要说什么"（可写），
              混在一张卡片里用户会以为反馈是只读展示。 */}
          <section>
            <p className="sec-label">反馈</p>
            <FeedbackCard page="/settings" />
          </section>
        </div>

        {/* ============ 右列：只读的系统状态 ============ */}
        <div className="stack-20">
          <section>
            <p className="sec-label">知穗能调用哪些工具</p>
            {tools.state === "loading" && (
              <div className="card card-pad">
                <SkeletonLines widths={["90%", "90%", "70%"]} />
              </div>
            )}
            {tools.state === "error" && (
              <div className="card card-pad">
                <Callout icon="warn">工具元数据读不到：{tools.message}</Callout>
              </div>
            )}
            {tools.state === "absent" && (
              <div className="stack-12">
                <NotReady what="工具元数据接口（/api/agent/tools）" />
                <ToolTable tools={DEMO_TOOLS} demo />
              </div>
            )}
            {tools.state === "ready" && <ToolTable tools={tools.data} />}
          </section>

          <section>
            <div className="sec-label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              调度进程
              {health.state === "ready" && <HealthTag status={health.data.status} />}
              {health.state === "absent" && <span className="ctag warn">未知</span>}
            </div>
            {health.state === "loading" && (
              <div className="card card-pad">
                <SkeletonLines widths={["70%", "50%"]} />
              </div>
            )}
            {health.state === "error" && (
              <div className="card card-pad">
                <Callout icon="warn">调度状态读不到：{health.message}</Callout>
              </div>
            )}
            {health.state === "absent" && (
              <div className="stack-12">
                <NotReady what="调度接口（/api/schedule/health、/jobs）" />
                <ScheduleCard health={DEMO_HEALTH} jobs={DEMO_JOBS} demo />
              </div>
            )}
            {health.state === "ready" && (
              <ScheduleCard
                health={health.data}
                jobs={jobs.state === "ready" ? jobs.data : []}
              />
            )}
          </section>
        </div>
      </div>
    </ConsoleShell>
  );
}

/* ---------------- 账号卡片 ---------------- */

/**
 * 单独拆一个组件是**必须**的，不是风格问题。
 * `useConsoleUser()` 读的是 ConsoleShell 提供的 Context，而 SettingsPage 是
 * ConsoleShell 的**父**组件 —— 在它自己的函数体里调 useContext，读到的位置还在
 * Provider 外面，拿到的永远是 null（账号名会显示成「—」）。
 * 只有把读取动作放进 Provider 的子组件里，才能拿到值。
 */
function AccountCard({ onLogout }: { onLogout: () => Promise<void> }) {
  const me = useConsoleUser();
  return (
    <div className="card card-pad acct-card" style={{ display: "flex", alignItems: "center", gap: 14 }}>
      <CornIcon name="user" className="h-9 w-9" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="card-title">{me?.user.username ?? "—"}</div>
        <div className="meta">账号 ID {me?.user.id ?? "—"}</div>
      </div>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        disabled
        title="后端尚无 /auth/password 端点"
      >
        修改密码
      </button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onLogout()}>
        退出登录
      </button>
    </div>
  );
}

/* ---------------- 用量条 ---------------- */

function QuotaBars({ summary }: { summary: UsageSummary }) {
  const rows = QUOTA_META.filter((m) => summary.quota[m.key]);
  if (rows.length === 0) {
    return <p className="hint" style={{ marginTop: 0 }}>这个账号暂时没有配额限制。</p>;
  }
  return (
    <>
      <div className="grid3">
        {rows.map((m) => {
          const q = summary.quota[m.key];
          // limit=0 或不限：**不画空进度条** —— 一条永远不动的 0% 只会让人以为卡住了
          const unlimited = q.unlimited || q.limit === 0;
          const pct = unlimited ? 0 : Math.min(100, Math.round((q.used / q.limit) * 100));
          return (
            <div key={m.key}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontSize: "var(--fs-md)" }}>{m.label}</span>
                <span className="meta">
                  {unlimited ? "无限额" : `${q.used} / ${q.limit} ${m.unit}`}
                </span>
              </div>
              {!unlimited && (
                <div className="bar">
                  <i className={pct >= 90 ? "hot" : undefined} style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="hint">
        {summary.rate_per_minute > 0
          ? `另有每分钟 ${summary.rate_per_minute} 次的限流。`
          : "未启用每分钟限流。"}
        超限一律 429，前端会解析 <code>retry_after</code> 告诉你还差多少秒，而不是只转圈。
      </p>
    </>
  );
}

/* ---------------- 工具表 ---------------- */

function ToolTable({ tools, demo }: { tools: AgentTool[]; demo?: boolean }) {
  return (
    <div className="card card-pad">
      {demo && (
        <p className="sec-label" style={{ marginBottom: 8 }}>
          演示数据
        </p>
      )}
      <div className="tr tool th">
        <span>工具</span>
        <span>类别</span>
        <span>可重试</span>
        <span>需确认</span>
        <span style={{ whiteSpace: "nowrap" }}>上限·成本</span>
      </div>
      {tools.map((t) => (
        <div className="tr tool" key={t.name}>
          <span className="mono" title={t.name}>
            {t.name}
          </span>
          <span>
            <span className={`ctag ${t.category === "write" ? "warn" : "info"}`}>
              {t.category === "write" ? "写" : "读"}
            </span>
          </span>
          <span>{t.retryable ? "是" : "否"}</span>
          <span>{t.needs_approval ? "是" : "否"}</span>
          <span className="meta">
            {fmtNum(t.max_output_chars)} · {t.cost_hint}
          </span>
        </div>
      ))}
      <p className="hint">
        延迟发现（<code>defer</code>）已建好但当前未启用：这 {tools.length} 个工具每轮都全量下发给模型。
        等打卡 / 导图 / 定时投递这类工具加进来，才需要按需分发。
      </p>
    </div>
  );
}

/* ---------------- 调度卡片 ---------------- */

function HealthTag({ status }: { status: ScheduleHealth["status"] }) {
  const map = { up: ["ok", "运行中"], stalled: ["warn", "卡住"], down: ["bad", "已停止"] } as const;
  const [cls, label] = map[status] ?? ["warn", "未知"];
  return <span className={`ctag ${cls}`}>{label}</span>;
}

function ScheduleCard({
  health,
  jobs,
  demo,
}: {
  health: ScheduleHealth;
  jobs: ScheduleJob[];
  demo?: boolean;
}) {
  return (
    <div className="card card-pad">
      {demo && (
        <p className="sec-label" style={{ marginBottom: 8 }}>
          演示数据
        </p>
      )}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--fs-md)" }}>worker 心跳</div>
          <div className="meta">
            {health.age_seconds != null ? `最后心跳 ${health.age_seconds} 秒前` : "心跳时间未知"}
            {health.pid ? ` · pid ${health.pid}` : ""}
          </div>
        </div>
        <HealthTag status={health.status} />
      </div>
      <div className="divide" />
      <div className="tr job th">
        <span>任务</span>
        <span>周期</span>
        <span>启用</span>
        <span>上次</span>
      </div>
      {jobs.map((j) => (
        <div className="tr job" key={j.job_key}>
          <span className="mono" title={j.job_key}>
            {j.job_key}
          </span>
          <span className="mono" title={j.cron ?? undefined}>
            {j.cron ?? "—"}
          </span>
          <span>{j.enabled ? "开" : "关"}</span>
          <span className="meta">{j.last_status ?? "—"}</span>
        </div>
      ))}
      <p className="hint">
        健康判定只看<b>心跳年龄</b>：超过 300 秒 <code>stalled</code>、600 秒 <code>down</code>。
        进程还在但被卡死同样会被抓到 —— 这是「静默故障」唯一的抓手。
      </p>
      <div className="callout" style={{ marginTop: 12 }}>
        <CornIcon name="warn" />
        <div>
          手动触发（<code>run-now</code>）与重排（<code>retry</code>）<b>仅管理员可用</b>，
          普通用户这一块只看状态。
        </div>
      </div>
    </div>
  );
}
