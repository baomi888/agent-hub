"use client";

// 关卡式学习路径（申报书 3.4「关卡式学习路径」+ 2.8 输出层「学习路径进度」）
//
// 这一屏刻意不做成「一张画好的流程图」：每一关的完成条件都绑定到真实数据表
// （服务端 service/path.py 读时现算），所以卡片上那个「已掌握 84 / 100 词」
// 是当场算出来的，不是写死的文案。演示时评委点开任意一关，看到的都是真数。
//
// 三态：done（已过）/ current（已经动手、还没到线）/ locked（一步没走）。
// 关卡严格线性——前一关没过，后面的关卡不亮，这是「关卡」和「清单」的区别。

import { useCallback, useEffect, useState } from "react";
import ConsoleShell from "@/components/console/ConsoleShell";
import { useAuth } from "@/lib/hooks/useAuth";
import { useDismissed } from "@/lib/hooks/useDismissed";
import CornIcon from "@/components/CornIcon";

type Status = "done" | "current" | "locked";

interface Check {
  key: string;
  label: string;
  unit: string;
  value: number;
  target: number;
  passed: boolean;
}
interface Level {
  id: string;
  chapter: string;
  name: string;
  desc: string;
  action: string;
  action_label: string;
  status: Status;
  detail: string;
  percent: number;
  checks: Check[];
  done_at: number | null;
}
interface Chapter {
  id: string;
  name: string;
  desc: string;
  levels: Level[];
  done: number;
  total: number;
}
interface Overview {
  progress: { done: number; total: number; percent: number };
  chapters: Chapter[];
  current: Level | null;
  today: string;
}

async function jfetch<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) {
    let msg = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") msg = body.detail;
    } catch {
      /* 非 JSON 错误体，保留默认文案 */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

const card: React.CSSProperties = {
  background: "var(--card-bg)",
  border: "1px solid var(--line)",
  borderRadius: 14,
  padding: 18,
};
const label: React.CSSProperties = { fontSize: 12, color: "var(--text-2)" };

/** 关卡卡片：三态靠底色 + 描边区分，不靠图标，缩略时也能分辨 */
function LevelCard({ lv, onOpen }: { lv: Level; onOpen: (lv: Level) => void }) {
  const done = lv.status === "done";
  const cur = lv.status === "current";
  const locked = lv.status === "locked";
  const bg = done ? "var(--accent-soft)" : "var(--surface)";
  const bd = done || cur ? "var(--accent)" : "var(--line)";
  const titleColor = done ? "var(--accent-ink)" : locked ? "var(--text-3)" : "var(--text-1)";
  return (
    <button
      type="button"
      onClick={() => onOpen(lv)}
      title={lv.desc}
      style={{
        flex: "0 0 172px",
        textAlign: "left",
        background: bg,
        border: "1px solid", borderColor: bd,
        boxShadow: cur ? "0 0 0 1px var(--accent)" : "none",
        borderRadius: 12,
        padding: "12px 13px 13px",
        cursor: "pointer",
        font: "inherit",
        opacity: locked ? 0.75 : 1,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        minHeight: 108,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, letterSpacing: "0.08em", color: locked ? "var(--text-3)" : "var(--accent-ink)" }}>
          {lv.id.slice(1)}
        </span>
        <span
          style={{
            fontSize: 11,
            color: done ? "var(--accent-ink)" : cur ? "var(--accent-ink)" : "var(--text-3)",
            fontWeight: cur ? 600 : 400,
          }}
        >
          {done ? "已通过" : cur ? "进行中" : "未开始"}
        </span>
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, color: titleColor, lineHeight: 1.3 }}>{lv.name}</div>
      <div style={{ fontSize: 12, color: locked ? "var(--text-3)" : "var(--text-2)", lineHeight: 1.45 }}>
        {lv.detail || lv.desc}
      </div>
      {done && lv.done_at ? (
        <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: "auto" }}>
          {new Date(lv.done_at * 1000).getMonth() + 1}/{new Date(lv.done_at * 1000).getDate()} 通过
        </div>
      ) : (
        <div style={{ height: 2, background: "var(--fill-1)", borderRadius: 2, marginTop: "auto", overflow: "hidden" }}>
          <div
            style={{
              width: `${cur ? Math.max(3, Math.min(100, lv.percent)) : 0}%`,
              height: "100%",
              background: "var(--accent)",
            }}
          />
        </div>
      )}
    </button>
  );
}

/** 关卡之间的连线：横线 + 箭头，用边框三角画，不引图标资源 */
function Connector({ on }: { on: boolean }) {
  const color = on ? "var(--accent)" : "var(--line)";
  return (
    <div style={{ flex: "0 0 26px", display: "flex", alignItems: "center", minHeight: 108 }}>
      <span style={{ flex: 1, height: 1, background: color, opacity: on ? 0.55 : 1 }} />
      <span
        style={{
          width: 0,
          height: 0,
          borderTop: "4px solid transparent",
          borderBottom: "4px solid transparent",
          borderLeft: `6px solid ${color}`,
          opacity: on ? 0.8 : 1,
        }}
      />
    </div>
  );
}

export default function PathPage() {
  // 身份必须从 useAuth 拿：useConsoleUser 的 Context 在 ConsoleShell 内部，
  // 页面是它的父级，在外面读永远是 null，页面就会永远停在「正在读取你的学习记录…」。
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;
  const [ov, setOv] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<Level | null>(null);
  // 「这条路是什么」：按用户记住关闭状态。首进必看，看过之后页头留一个「这是什么」能再翻开
  const [introGone, dismissIntro, reopenIntro] = useDismissed(
    `baomi.path.intro.v1.${authedId ?? "anon"}`
  );

  const load = useCallback(async () => {
    setErr("");
    try {
      setOv(await jfetch<Overview>("/api/path/overview"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  // 登录态守卫：身份确认前不发请求（与 /checkin 同一套写法）
  useEffect(() => {
    if (!authedId) return;
    void load();
  }, [authedId, load]);

  const p = ov?.progress;

  return (
    <ConsoleShell
      active="path"
      eyebrow="学习路径"
      title="12 关，一关一关过"
      sub="从「把资料放进来」走到「让它每天替你跑」——走完这 12 关，知穗才算真的配成你自己的。"
      actions={
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {p && (
            <span style={{ fontSize: 12, color: "var(--text-2)" }}>
              已通过 {p.done} / {p.total} 关 · {p.percent}%
            </span>
          )}
          {introGone && (
            <button type="button" onClick={reopenIntro} className="path-intro-btn">
              这是什么？
            </button>
          )}
        </div>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 16, paddingBottom: 24 }}>
        {err && (
          <div style={{ ...card, borderColor: "var(--red-ink)", color: "var(--red-ink)", fontSize: 13 }}>{err}</div>
        )}

        {/* 新用户第一眼看到的是「12 关」「已通过 4/12」——数字有了，但没人告诉他这是干什么的。
            这段说明只在第一次进来看得见，关掉后页头留「这是什么？」可以再翻开。 */}
        {!introGone && (
          <section className="onboard path-note" role="note" aria-label="学习路径说明">
            <div className="onboard-head">
              <span className="onboard-title">这条路是什么？</span>
              <button type="button" className="onboard-close" onClick={dismissIntro}>
                知道了
              </button>
            </div>
            <ol className="path-intro">
              <li>
                <b>12 关，按顺序走。</b>
                前一关没过，后面的关卡不亮——它是关卡，不是可以随便勾的待办清单。
              </li>
              <li>
                <b>过关条件都是你真做出来的数据。</b>
                资料份数、连续打卡、掌握词数、模考分数……系统自己算，到线就自动亮，你什么都不用填。
              </li>
              <li>
                <b>「当前这一关」就是你现在该做的那件事。</b>
                点它右边的按钮直接去做，做完回来这里会自己变成已通过。
              </li>
            </ol>
          </section>
        )}

        {loading && !ov && (
          <div style={{ ...card, color: "var(--text-2)", fontSize: 13 }}>正在读取你的学习记录…</div>
        )}

        {ov && p && (
          <>
            {/* 总进度 + 当前该做的一关 */}
            <section style={card}>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 14, flexWrap: "wrap" }}>
                <div style={{ fontSize: 34, fontWeight: 700, color: "var(--accent-ink)", lineHeight: 1 }}>
                  {p.done}
                  <span style={{ fontSize: 16, color: "var(--text-2)", fontWeight: 400 }}> / {p.total} 关</span>
                </div>
                <div style={{ flex: 1, minWidth: 180 }}>
                  <div style={{ height: 8, background: "var(--fill-1)", borderRadius: 6, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${Math.max(2, p.percent)}%`,
                        height: "100%",
                        background: "var(--accent)",
                        transition: "width .35s ease",
                      }}
                    />
                  </div>
                  <div style={{ ...label, marginTop: 7 }}>
                    {p.done === p.total ? "全部通关——现在这套系统每天都在替你干活。" : `还剩 ${p.total - p.done} 关`}
                  </div>
                </div>
              </div>

              {ov.current && (
                <div
                  style={{
                    marginTop: 16,
                    padding: "12px 14px",
                    borderRadius: 10,
                    background: "var(--accent-soft)",
                    border: "1px solid var(--accent)",
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    flexWrap: "wrap",
                  }}
                >
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <div style={{ fontSize: 12, color: "var(--accent-ink)", marginBottom: 3 }}>当前这一关</div>
                    <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-1)" }}>
                      {ov.current.id.slice(1)} · {ov.current.name}
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-2)", marginTop: 3 }}>
                      {ov.current.detail} —— {ov.current.desc}
                    </div>
                  </div>
                  <a
                    href={ov.current.action}
                    style={{
                      background: "var(--solid)",
                      color: "var(--solid-text)",
                      borderRadius: 8,
                      padding: "9px 16px",
                      fontSize: 13,
                      fontWeight: 600,
                      textDecoration: "none",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {ov.current.action_label}
                  </a>
                </div>
              )}
              {!ov.current && p.done === p.total && (
                <div style={{ marginTop: 14, fontSize: 13, color: "var(--text-2)" }}>
                  全部通关。接着把节奏交给「每日委托」，它会在你不在的时候继续跑。
                </div>
              )}
            </section>

            {/* 章节泳道：每章一行，横向可滑（窄屏不会被压变形） */}
            {ov.chapters.map((ch, ci) => (
              <section key={ch.id} style={card}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 11, letterSpacing: "0.1em", color: "var(--accent-ink)" }}>
                    第 {ci + 1} 章
                  </span>
                  <span style={{ fontSize: 15, fontWeight: 600, color: "var(--text-1)" }}>{ch.name}</span>
                  <span style={{ ...label, flex: 1 }}>{ch.desc}</span>
                  <span style={{ ...label, color: ch.done === ch.total ? "var(--accent-ink)" : "var(--text-2)" }}>
                    {ch.done} / {ch.total}
                  </span>
                </div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "stretch",
                    overflowX: "auto",
                    paddingBottom: 4,
                  }}
                >
                  {ch.levels.map((lv, i) => (
                    <div key={lv.id} style={{ display: "flex", alignItems: "stretch" }}>
                      {i > 0 && <Connector on={lv.status !== "locked"} />}
                      <LevelCard lv={lv} onOpen={setOpen} />
                    </div>
                  ))}
                </div>
              </section>
            ))}

            <div style={{ ...label, paddingLeft: 2 }}>
              「进行中」表示你已经动手但还没到线，「未开始」表示一步没走。通过时间记录的是你第一次过这关的日子，不会因为后来断卡而改写。
            </div>
          </>
        )}
      </div>

      {/* 关卡详情：点开看这一关到底在比什么 */}
      {open && (
        <div
          onClick={() => setOpen(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(46,36,22,0.28)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
            zIndex: 60,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ ...card, maxWidth: 420, width: "100%", background: "var(--card-bg)" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
              <div>
                <div style={{ ...label, marginBottom: 4 }}>
                  {open.id.slice(1)} · {open.status === "done" ? "已通过" : open.status === "current" ? "进行中" : "未开始"}
                </div>
                <div style={{ fontSize: 17, fontWeight: 600, color: "var(--text-1)" }}>{open.name}</div>
              </div>
              <button
                type="button"
                onClick={() => setOpen(null)}
                aria-label="关闭"
                style={{
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  padding: 4,
                  color: "var(--text-2)",
                  display: "flex",
                }}
              >
                <CornIcon name="close" className="h-4 w-4" />
              </button>
            </div>

            <p style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.65, margin: "10px 0 14px" }}>{open.desc}</p>

            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              {open.checks.map((c) => (
                <div key={c.key}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                    <span style={{ color: "var(--text-2)" }}>{c.label}</span>
                    <span style={{ color: c.passed ? "var(--accent-ink)" : "var(--text-2)" }}>
                      {c.value} / {c.target} {c.unit}
                    </span>
                  </div>
                  <div style={{ height: 5, background: "var(--fill-1)", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${c.target > 0 ? Math.min(100, Math.max(0, (c.value / c.target) * 100)) : 100}%`,
                        height: "100%",
                        background: c.passed ? "var(--accent)" : "var(--accent-ink)",
                        opacity: c.passed ? 1 : 0.6,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
              <a
                href={open.action}
                style={{
                  background: "var(--solid)",
                  color: "var(--solid-text)",
                  borderRadius: 8,
                  padding: "9px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  textDecoration: "none",
                }}
              >
                {open.action_label}
              </a>
              <button
                type="button"
                onClick={() => setOpen(null)}
                style={{
                  background: "transparent",
                  color: "var(--text-2)",
                  border: "1px solid var(--line)",
                  borderRadius: 8,
                  padding: "9px 16px",
                  fontSize: 13,
                  cursor: "pointer",
                }}
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </ConsoleShell>
  );
}
