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
import PageShell from "@/components/console/PageShell";
import { useConsoleAccount } from "@/components/console/ConsoleLayout";
import CornIcon from "@/components/CornIcon";
import AvatarPicker from "@/components/settings/AvatarPicker";
import InstallAppRow from "@/components/settings/InstallAppRow";
import PasswordDialog from "@/components/settings/PasswordDialog";
import UsernameDialog from "@/components/settings/UsernameDialog";
import UserAvatar from "@/components/settings/UserAvatar";
import FeedbackCard from "@/components/FeedbackCard";
import TagPicker from "@/components/profile/TagPicker";
import { Callout, NotReady, Seg, SkeletonLines, Switch } from "@/components/console/ui";
import { api } from "@/lib/api";
import { logout as storeLogout } from "@/lib/authStore";
import { openGuide } from "@/lib/guide";
import { useAuth } from "@/lib/hooks/useAuth";
import {
  DEFAULT_TOP_K,
  FONT_SCALE_OPTIONS,
  applyFontScale,
  normalizeFontScale,
  patchPrefs,
  readPrefs,
  readTheme,
  writeTheme,
  type FontScale,
} from "@/lib/consolePrefs";
import { DEMO_HEALTH, DEMO_JOBS, DEMO_TOOLS } from "@/lib/consoleDemo";
import { errProbe, toProbe, type ProbeState } from "@/lib/probe";
import { fmtNum } from "@/lib/time";
import type {
  AgentTool,
  KbInfo,
  ProfileSnapshot,
  ScheduleHealth,
  ScheduleJob,
  UsageSummary,
} from "@/lib/types";

/** 配额维度的中文名。顺序即展示顺序 */
const QUOTA_META: { key: string; label: string; unit: string }[] = [
  { key: "ask", label: "问答", unit: "次" },
  // 2026-11-13：「向量化」改成「资料建库」。用户做过的那件事是"把资料丢进来建库"
  // （界面上其它地方就写「开始建库」），不是"把文本切块灌进向量库"。
  { key: "embed", label: "资料建库", unit: "次" },
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
  // 工具表与调度进程是**开发者的调试面**，不是用户功能：普通用户看到
  // 「知穗能调用哪些工具」只会冒出"我该不该管这个"的疑问，而调度进程里的
  // job_key、下次运行时间对他没有任何可操作性。与 /flow、/dashboard 同一性质，
  // 因此同样按 is_admin 收起来（真权限在后端，这里只是不给入口）。
  const isAdmin = !!auth.me?.is_admin;

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
  // 兴趣标签：画像快照 + 是否展开选择器
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);
  const [tagEdit, setTagEdit] = useState(false);
  // 自动沉淀（板块 D）：服务端开关，不是 localStorage —— 这件事发生在 worker 里，
  // 没有浏览器参与。null = 还没读到，此时开关置灰（别让用户以为现在就是开的）
  const [autoSediment, setAutoSediment] = useState<boolean | null>(null);
  const [sedimentBusy, setSedimentBusy] = useState(false);
  const [sedimentNote, setSedimentNote] = useState<string | null>(null);

  useEffect(() => {
    // 本地偏好在挂载后读，避免 SSR/CSR 首帧不一致。
    // 延到下一个宏任务再 setState：直接在 effect 里同步 setState 会被判成级联渲染
    // （eslint react-hooks/set-state-in-effect），这是本仓库既有的做法
    const t = window.setTimeout(() => {
      setDark(readTheme());
      const saved = readPrefs();
      // 走 normalize：老用户存的是已取消的 "s" 档，这里直接判等会漏读，
      // 表现为「设置页显示标准、工作台却按别的档渲染」
      if (saved.fontScale) setFontScale(normalizeFontScale(saved.fontScale));
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
    api
      .listKbs()
      .then((r) => setKbs(r.kbs))
      .catch(() => {
        /* 资料库拉不到不影响其他卡片 */
      })
      .finally(() => setKbsLoading(false));
    // 兴趣标签：拉不到就整块不显示（画像只是辅助，不该在设置页报错刷存在感）
    api
      .myProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
    // 自动沉淀开关：后端 404（旧版本）时保持 null = 开关置灰，
    // 而不是默认显示"已开启"——那是替用户撒谎
    api
      .getPrefs()
      .then((p) => setAutoSediment(p.auto_sediment))
      .catch(() => setAutoSediment(null));
  }, [authedId]);

  // 工具表 + 调度进程只给管理员拉：普通用户看不到这一列，
  // 就不该替他多打三个请求（其中一个还是他必然拿不到的）。
  // 单独一个 effect 而不是并进上面那个 —— 并进去会让 isAdmin 变化时
  // 用量、资料库、画像全部重拉一遍。
  useEffect(() => {
    if (!authedId || !isAdmin) return;
    api.agent.tools().then((d) => setTools(toProbe(d))).catch((e) => setTools(errProbe(e)));
    api.schedule.health().then((d) => setHealth(toProbe(d))).catch((e) => setHealth(errProbe(e)));
    api.schedule.jobs().then((d) => setJobs(toProbe(d))).catch((e) => setJobs(errProbe(e)));
  }, [authedId, isAdmin]);

  // 带 #tags 这类锚点跳过来时要滚到眼前。
  //
  // ★ 不能指望浏览器自己滚：本站 html/body 是 overflow:hidden，真正滚动的是
  //   .console-scroll 这个内部容器，而浏览器的锚点默认只滚 document 滚动容器。
  //   表现就是"跳过来了但停在页面顶部，看不到要看的东西"。
  // ★ 晚一帧再滚：目标区块里常有要等接口才渲染的子组件，立刻滚会停在
  //   "内容还没出现"时的高度，位置偏上一截。
  useEffect(() => {
    const id = window.location.hash.replace("#", "");
    if (!id) return;
    const timer = setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 320);
    return () => clearTimeout(timer);
  }, []);

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

  /**
   * 自动沉淀开关：先乐观更新，失败再翻回来并说清原因。
   *
   * 为什么要乐观：这是一个"开关"，用户点完期待立刻看到状态变化。
   * 等一个来回（PUT + 重渲染）再动，会有半秒的"我点了吗"。
   * 但失败必须翻回来 —— 否则用户以为关了，后台还在建库，这是最糟的一种谎。
   */
  const onToggleSediment = useCallback(async (next: boolean) => {
    setAutoSediment(next);
    setSedimentBusy(true);
    setSedimentNote(null);
    try {
      const saved = await api.setPrefs({ auto_sediment: next });
      setAutoSediment(saved.auto_sediment);
    } catch (e) {
      setAutoSediment(!next);
      setSedimentNote(
        (e as Error)?.message ? `没改成：${(e as Error).message}` : "没改成，稍后再试"
      );
    } finally {
      setSedimentBusy(false);
    }
  }, []);

  const logout = useCallback(async () => {
    // 走 authStore 的 logout，不直接 api.logout：
    // 单例里那份 me 必须一起清掉，否则跳到 "/" 的那一瞬工作台会先按"已登录"
    // 渲染出来（缓存里的旧身份），等下一个接口 401 才收回 —— 正好是这次要消灭的闪。
    await storeLogout();
    // 用路由跳转而不是 window.location：整页刷新会丢掉 Back 历史。
    // 回工作台后 useAuth 读到的是 null → 直接给落地页。
    router.push("/");
    router.refresh();
  }, [router]);

  return (
    <PageShell title="设置">
      {/* 右列只有管理员有。只剩一列时不能再走 .grid2 的两条轨道 ——
          左列占 1.25fr、右边空着 1fr，整页像被撕掉一半。 */}
      <div className={isAdmin ? "grid2" : "grid2 one-col"}>
        {/* ============ 左列：可改的设置 ============ */}
        <div className="stack-20">
          <section>
            <p className="sec-label">账号</p>
            <AccountCard onLogout={logout} />
            {/* 首次进平台那张上手说明只弹一次。入口放账号下面而不是另起一块：
                它属于"我这个账号的事"，和改密码 / 退出登录同一类。 */}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ marginTop: 10 }}
              onClick={openGuide}
              title="重新打开首次进入时的四步说明"
            >
              <CornIcon name="bulb" />
              再看一次上手说明
            </button>
            {/* 这里原来写的是「「修改密码」还没做好，先置灰」——
                密码功能上线后这句话就成了错的：按钮明明是亮的，页面却在说它没做。
                2026-11-14：句子前半「密码、昵称、头像都能在这里改」是把上面那张卡的
                字段念一遍，删掉；只留「改登录名要验证密码」这条布局看不见的规则。 */}
            <p className="hint">改登录名要先验证一次密码。</p>
          </section>

          {/* 兴趣方向：30 条定稿词表（core/tag_taxonomy.py）。
              放在账号下面、外观上面 —— 它是"我是谁"的一部分，不是外观偏好。
              标签名一律由后端下发，这里不硬编码任何一个词。 */}
          <section id="tags">
            <p className="sec-label">兴趣方向</p>
            <div className="card card-pad">
              <div className="card-head">
                <span className="card-title">我的标签</span>
                <span className="meta">
                  {profile ? `${profile.picks.length} 个已选` : "读取中…"}
                </span>
              </div>

              {!profile ? (
                <div style={{ marginTop: 12 }}>
                  <SkeletonLines widths={["70%", "48%"]} />
                </div>
              ) : tagEdit ? (
                <div style={{ marginTop: 8 }}>
                  <TagPicker
                    initialPicks={profile.picks}
                    onSaved={(snap) => {
                      setProfile(snap);
                      setTagEdit(false);
                    }}
                    onCancel={() => setTagEdit(false)}
                  />
                </div>
              ) : profile.picks.length === 0 ? (
                <>
                  <p className="hint" style={{ marginTop: 8 }}>
                    还没选过。选 1~3 个方向后，每日委托会默认用对应方向 ——
                    省掉每次重新自我介绍。
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    style={{ marginTop: 10 }}
                    onClick={() => setTagEdit(true)}
                  >
                    <CornIcon name="target" />
                    选方向
                  </button>
                </>
              ) : (
                <>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
                    {profile.picks.map((name) => {
                      const meta = profile.tags.find((t) => t.tag === name);
                      return (
                        <span
                          key={name}
                          className="ctag gold"
                          title={meta?.desc ?? undefined}
                          style={{ fontSize: "var(--fs-sm)", padding: "3px 10px" }}
                        >
                          {name}
                        </span>
                      );
                    })}
                  </div>
                  <div className="row" style={{ gap: 8, marginTop: 12 }}>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setTagEdit(true)}
                    >
                      <CornIcon name="pencil" />
                      修改方向
                    </button>
                  </div>
                  {profile.categories.length > 0 && (
                    <p className="hint" style={{ marginTop: 8 }}>
                      当前重心：
                      {profile.categories
                        .slice(0, 3)
                        .map((c) => `${c.category}（权重 ${c.weight}）`)
                        .join("、")}
                      。权重来自手选（每次 +3）与对话中自动识别的主题（每次 +1）。
                    </p>
                  )}
                </>
              )}
            </div>
          </section>

          <section>
            <p className="sec-label">外观</p>
            <div className="card card-pad">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "var(--fs-lg)" }}>字号</span>
                <Seg
                  ariaLabel="字号"
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
                整站文字（正文、标题、按钮）一起变，卡片和栏宽不动，所以调大不会把布局挤乱。
                「跟随系统」用的是浏览器自己设的默认字号。
              </p>
              <p className="hint">
                校园工具页（单词卡、课程表、GPA）是独立的单页，<b>不跟随</b>这个档位。
              </p>
            </div>
          </section>

          <section>
            <p className="sec-label">装成 App</p>
            <InstallAppRow />
          </section>

          {/* 提醒（2026-11-11）：整组搬到「待办 → 设置」。
              这里只留一条指路，不留第二份控件 —— 两个地方都能开关同一件事，
              迟早出现"在这关了那边还显示开着"。
              保留这条而不是整段删掉：老用户会先来设置页找，找不到会以为功能没了。 */}
          <section id="reminder">
            <p className="sec-label">提醒</p>
            <Link href="/reminders?tab=settings" className="install-more">
              <span>提醒权限、已开启提醒的设备，都在「待办 → 设置」里</span>
              <span className="arrow" aria-hidden="true">
                ›
              </span>
            </Link>
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
              {/* 指向独立的 /knowledge：切片档位是按库记的，得在库详情里设定；
                  以前这里跳 /?kb=1 只是把首页右栏抽屉拉开（现在那一页已经不是资料管理的主场了）。 */}
              <Link className="btn btn-ghost btn-sm" href="/knowledge" style={{ marginTop: 10 }}>
                <CornIcon name="book" />
                去资料库设定
              </Link>
            </div>
          </section>

          <section>
            <p className="sec-label">知识库沉淀</p>
            <div className="card card-pad">
              <div
                className="row"
                style={{ justifyContent: "space-between", alignItems: "center" }}
              >
                <span style={{ fontSize: "var(--fs-lg)" }}>自动沉淀</span>
                <Switch
                  checked={autoSediment ?? false}
                  disabled={autoSediment === null || sedimentBusy}
                  onChange={onToggleSediment}
                  label="自动沉淀"
                />
              </div>
              <p className="hint">
                长对话被压缩时，把里面的知识句自动存进「对话沉淀·主题」资料库，
                下一轮检索就能用上。关掉只是不再自动存 —— 回答下面那个
                「存进资料库」按钮仍然随时可用。
              </p>
              {autoSediment === null && (
                <p className="hint">
                  这一项现在读不到（后端版本较旧时会这样），先保持原状。
                </p>
              )}
              {sedimentNote && (
                <p className="hint" style={{ color: "var(--red-ink)" }}>
                  {sedimentNote}
                </p>
              )}
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
                <p className="hint">还没有资料库。上传后会显示每个库占多少片段。</p>
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
                「选择性索引」（只让勾选的资料进索引）与「一键清空」还没做好，先置灰 ——
                放了开关却不生效，比不放更糟。
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

        {/* ============ 右列：只读的系统状态（仅管理员） ============
            「知穗能调用哪些工具」摊的是接口名与调用量，「调度进程」摊的是
            job_key 和下次运行时间 —— 都是开发者看的调试面，普通用户既看不懂
            也改不动，只能多出"我该不该管这个"的疑问。
            与 /flow、/dashboard 同一性质，按 is_admin 收起。 */}
        {isAdmin && (
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
        )}
      </div>
    </PageShell>
  );
}

/* ---------------- 账号卡片 ---------------- */

/**
 * 账号信息仍然拆成子组件，但理由已经从「读不到」变成了「职责分离」：
 * `useConsoleAccount()` 读的是 ConsoleLayout 提供的 Context —— 页面作为
 * layout 的 children 渲染在 Provider **内部**，所以能读到值；这条约束对
 * 子组件成立，对页面组件本身不成立（见 ConsoleLayout 里的说明）。
 * 保留这层拆分是因为账号卡自己还有四个弹窗与编辑态，
 * 混进 SettingsPage 会让那个组件的 state 更难读。
 */
function AccountCard({ onLogout }: { onLogout: () => Promise<void> }) {
  // 用 useConsoleAccount 而不是 useConsoleUser：改完资料要 refresh，
  // 只更新本组件自己的 state 的话，侧栏还显示旧名字（两个消费者各持一份 me）
  const { me, refresh } = useConsoleAccount();

  const [pickerOpen, setPickerOpen] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);
  const [nameOpen, setNameOpen] = useState(false);
  /** 正在保存的头像值（null = 没在保存）。传给选择器用于置灰对应项 */
  const [savingAvatar, setSavingAvatar] = useState<string | null>(null);
  /** 昵称行内编辑 */
  const [editingNick, setEditingNick] = useState(false);
  const [nickDraft, setNickDraft] = useState("");
  const [savingNick, setSavingNick] = useState(false);
  /** 一次性反馈。不用 toast 是因为它要贴着被改的那一行出现 */
  const [note, setNote] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // 昵称为空 = 没设过 → 回落登录名。这个判断只在**这一处**做，
  // 后端返回的 nickname 空串就是"没设过"，两边语义必须一致
  const displayName = me?.user.nickname || me?.user.username || "";

  const pickAvatar = useCallback(
    async (value: string) => {
      setSavingAvatar(value);
      try {
        await api.updateProfile({ avatar: value });
        // 刷新登录态 → 侧栏与这张卡同时变。这正是方案 §2.5 那个坑的修法
        await refresh();
        setPickerOpen(false);
        setNote({ kind: "ok", text: value ? "头像换好了" : "已改回用名字首字母" });
        return true;
      } catch (e) {
        setNote({ kind: "err", text: (e as Error).message || "头像没能换，稍后再试" });
        return false;
      } finally {
        setSavingAvatar(null);
      }
    },
    [refresh],
  );

  /**
   * 上传头像。返回 null = 成功；返回字符串 = 弹窗里要显示的错误。
   *
   * 失败时**保持弹窗打开**并原地显示原因 —— 用户刚选完图，把他弹回设置页
   * 再让他重新点一次头像、重新找那张图，是最没必要的摩擦。
   */
  const uploadAvatar = useCallback(
    async (blob: Blob) => {
      setSavingAvatar("img:upload");
      try {
        await api.uploadAvatar(blob);
        await refresh();
        setPickerOpen(false);
        setNote({ kind: "ok", text: "头像换好了" });
        return null;
      } catch (e) {
        return (e as Error).message || "上传没成功，稍后再试";
      } finally {
        setSavingAvatar(null);
      }
    },
    [refresh],
  );

  const startNickEdit = useCallback(() => {
    setNickDraft(me?.user.nickname || "");
    setNote(null);
    setEditingNick(true);
  }, [me?.user.nickname]);

  const saveNick = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      const v = nickDraft.trim();
      if (v.length > 20) {
        setNote({ kind: "err", text: "昵称最多 20 个字符" });
        return;
      }
      setSavingNick(true);
      try {
        await api.updateProfile({ nickname: v });
        await refresh();
        setEditingNick(false);
        setNote({ kind: "ok", text: v ? "昵称改好了" : "已改回用登录名显示" });
      } catch (e2) {
        setNote({ kind: "err", text: (e2 as Error).message || "昵称没能改，稍后再试" });
      } finally {
        setSavingNick(false);
      }
    },
    [nickDraft, refresh],
  );

  return (
    <div className="card card-pad acct-card">
      {/* 头像本身就是"换头像"的按钮：单独再放一个「换头像」按钮会显得这张卡
          有四个动作，而实际上点脸就是最直觉的那个 */}
      <button
        type="button"
        className="acct-avatar-btn"
        onClick={() => setPickerOpen(true)}
        title="换头像"
        aria-label="换头像"
      >
        <UserAvatar avatar={me?.user.avatar} name={displayName} className="h-10 w-10" />
        <span className="acct-avatar-mark" aria-hidden="true">
          换
        </span>
      </button>

      <div className="acct-main">
        {editingNick ? (
          <form className="acct-nick-form" onSubmit={saveNick}>
            <input
              className="acct-nick-input"
              value={nickDraft}
              onChange={(e) => setNickDraft(e.target.value)}
              maxLength={20}
              placeholder={me?.user.username ?? ""}
              aria-label="昵称"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  setEditingNick(false);
                }
              }}
            />
            <button type="submit" className="btn btn-primary btn-sm" disabled={savingNick}>
              {savingNick ? "存…" : "保存"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setEditingNick(false)}
              disabled={savingNick}
            >
              取消
            </button>
          </form>
        ) : (
          <div className="acct-name-row">
            <span className="card-title acct-name" title={displayName}>
              {displayName}
            </span>
            <button
              type="button"
              className="acct-mini"
              onClick={startNickEdit}
              title="改昵称"
              aria-label="改昵称"
            >
              <CornIcon name="pencil" className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* 登录名**常驻显示**，不折叠：这是"改完忘了自己叫什么"的唯一兜底。
            账号 ID 只露前 8 位（完整值在 title 里），32 位 hex 摆在这里没人看得下去 */}
        <div className="meta acct-sub">
          <span>
            登录名 <b className="acct-username">{me?.user.username ?? "—"}</b>
          </span>
          <button
            type="button"
            className="acct-link"
            onClick={() => setNameOpen(true)}
            title="改登录名（要输密码，有次数上限）"
          >
            改
          </button>
          <span className="acct-sep" aria-hidden="true">
            ·
          </span>
          <span title={me?.user.id ? `账号 ID ${me.user.id}` : undefined}>
            账号 ID {(me?.user.id ?? "").slice(0, 8) || "—"}
          </span>
        </div>

        {note && (
          <p className={note.kind === "ok" ? "acct-note is-ok" : "acct-note is-err"} role="status">
            {note.text}
          </p>
        )}
      </div>

      <div className="acct-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPwdOpen(true)}>
          修改密码
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onLogout()}>
          退出登录
        </button>
      </div>

      <AvatarPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        current={me?.user.avatar || ""}
        name={displayName}
        onPick={pickAvatar}
        onUpload={uploadAvatar}
        saving={savingAvatar}
      />
      <PasswordDialog open={pwdOpen} onClose={() => setPwdOpen(false)} />
      <UsernameDialog
        open={nameOpen}
        onClose={() => setNameOpen(false)}
        currentUsername={me?.user.username ?? ""}
        remaining={me?.user.name_changes_left}
        onSaved={refresh}
      />
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
          ? `另有每分钟 ${summary.rate_per_minute} 次的上限。`
          : "没有每分钟上限。"}
        连着问得太快会被暂时挡住，页面上会直接告诉你还差多少秒，不会只转圈。
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
          {/* 后三列包一层 .tr-facts：桌面 display:contents 摊回 grid 的第 3/4/5 列，
              手机折到第二行并带上字段名 —— 否则 5 列数据在 2 列网格里会流成
              「kb_search / 读 / 否 / 否 / 4,000·normal」四行，没人看得懂哪个「否」是什么。 */}
          <span className="tr-facts">
            <span data-l="可重试">{t.retryable ? "是" : "否"}</span>
            <span data-l="需确认">{t.needs_approval ? "是" : "否"}</span>
            <span className="meta" data-l="上限">
              {fmtNum(t.max_output_chars)} · {t.cost_hint}
            </span>
          </span>
        </div>
      ))}
      <p className="hint">
        这些工具目前每轮都会全部交给模型判断，暂时没做按需分发 ——
        等打卡、导图、定时投递这类工具多起来再优化。
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
          <span className="tr-facts">
            <span className="mono" title={j.cron ?? undefined} data-l="周期">
              {j.cron ?? "—"}
            </span>
            <span data-l="启用">{j.enabled ? "开" : "关"}</span>
            <span className="meta" data-l="上次">
              {j.last_status ?? "—"}
            </span>
          </span>
        </div>
      ))}
      <p className="hint">
        后台任务每分钟报一次心跳：超过 5 分钟没报标<b>卡住</b>，10 分钟没报标<b>已停止</b>。
        进程还在但被卡死同样会被抓出来。
      </p>
      <div className="callout" style={{ marginTop: 12 }}>
        <CornIcon name="warn" />
        <div>
          手动执行和重新排队<b>仅管理员可用</b>，普通用户这里只看状态。
        </div>
      </div>
    </div>
  );
}
