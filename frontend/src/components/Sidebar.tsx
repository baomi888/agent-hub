"use client";

// 左侧会话列表栏：品牌区 + 新建对话 + 搜索框 + 按时间分组的会话 + 深色模式开关
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
import { useMemo, useRef, useState, type RefObject } from "react";
import Link from "next/link";
import MascotLogo from "./chat/MascotLogo";
import CornIcon from "./CornIcon";
import type { Conversation, QuotaInfo } from "@/lib/types";
import type { FontScale } from "@/lib/hooks/usePrefs";

/** 工作台之外的四屏入口。图标排一行，避免再堆四个通栏按钮把侧栏撑满 */
// 四屏入口。label 只用两个字：侧栏是 240px、移动端是抽屉，四个格子横排两行，
// 字太长会把格子撑到换行；全称放 title，悬停能看到（也供读屏器读）。
const CONSOLE_LINKS = [
  { href: "/delegate", icon: "clock", label: "委托", full: "每日委托" },
  { href: "/report", icon: "calendar", label: "总结", full: "每日总结" },
  { href: "/checkin", icon: "star", label: "打卡", full: "打卡记录" },
  { href: "/settings", icon: "settings", label: "设置", full: "设置" },
];

interface Props {
  sessions: Conversation[];
  activeSid: string | null;
  onSelect: (sid: string) => void;
  onCreate: () => void;
  onOpenTemplates?: () => void;
  onRename: (sid: string, title: string) => void;
  onDelete: (sid: string) => void;
  kbNameMap?: Record<string, string>;
  dark?: boolean;
  onToggleDark?: () => void;
  /** 聊天正文字号档位（小/标准/大），持久化在 prefs 里 */
  fontScale?: FontScale;
  onFontScaleChange?: (f: FontScale) => void;
  /** 窄屏下作为浮层抽屉渲染 */
  overlay?: boolean;
  /** 抽屉是否展开（仅 overlay 时有意义） */
  open?: boolean;
  /** 抽屉容器引用，用于打开时接管焦点 */
  containerRef?: RefObject<HTMLElement | null>;
  /** 首屏还在拉会话列表：渲染骨架，避免先闪一下空态 */
  loading?: boolean;
  /** 拉取失败时的提示文案 */
  loadError?: string | null;
  /** 失败后手动重试 */
  onRetryLoad?: () => void;
  /** 当前登录用户名（登录门放进来之后才有值） */
  username?: string;
  /** 当前账号配额概览（含 unlimited 标记）；不传则不显示额度 */
  quota?: Record<string, QuotaInfo>;
  /** 打开「用量与反馈」看板（P2）。不传则账号块不可点 */
  onOpenUsage?: () => void;
  onLogout?: () => void;
}

const toMs = (ts: number) => (ts < 1e12 ? ts * 1000 : ts);

function quotaLine(q: Record<string, QuotaInfo>): string {
  const fmt = (n: number) => (n === 0 ? "∞" : String(n));
  const parts: string[] = [];
  if (q["ask"]) parts.push(`今日问答 ${q["ask"].used}/${fmt(q["ask"].limit)}`);
  if (q["embed"]) parts.push(`向量化 ${q["embed"].used}/${fmt(q["embed"].limit)}`);
  return parts.join(" · ");
}

function groupSessions(list: Conversation[]) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 3600 * 1000;
  const buckets: { label: string; items: Conversation[] }[] = [
    { label: "今天", items: [] },
    { label: "昨天", items: [] },
    { label: "近 7 天", items: [] },
    { label: "更早", items: [] },
  ];
  for (const c of list) {
    const t = toMs(c.updated_at || c.created_at || 0);
    if (t >= startOfToday) buckets[0].items.push(c);
    else if (t >= startOfToday - day) buckets[1].items.push(c);
    else if (t >= startOfToday - 7 * day) buckets[2].items.push(c);
    else buckets[3].items.push(c);
  }
  return buckets.filter((b) => b.items.length > 0);
}

export default function Sidebar({
  sessions,
  activeSid,
  onSelect,
  onCreate,
  onOpenTemplates,
  onRename,
  onDelete,
  kbNameMap,
  dark = false,
  onToggleDark,
  fontScale = "m",
  onFontScaleChange,
  overlay = false,
  open = false,
  containerRef,
  loading = false,
  loadError = null,
  onRetryLoad,
  username,
  quota,
  onOpenUsage,
  onLogout,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  // 删除是破坏性操作，点一下先进确认态，避免误删整段对话
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);
  // 手机左滑删除：swipedId = 当前左滑露出删除钮的会话；touch 存进行中的手势（不进 state，避免逐帧重渲染）
  const [swipedId, setSwipedId] = useState<string | null>(null);
  const swipeTouch = useRef<{ id: string; x0: number; y0: number; el: HTMLElement; horizontal: boolean | null } | null>(null);
  const SWIPE_W = 76; // 与 .session-swipe-del 宽度一致

  const startEdit = (c: Conversation) => {
    setEditingId(c.id);
    setDraft(c.title);
  };

  const commitEdit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? sessions.filter((c) => c.title.toLowerCase().includes(q))
      : sessions;
    return groupSessions(filtered);
  }, [sessions, query]);

  return (
    <aside
      ref={containerRef}
      tabIndex={-1}
      className={`side-left flex h-full shrink-0 flex-col px-4 py-5 ${open ? "open" : ""}`}
      inert={overlay && !open}
    >
      {/* 品牌区：玉米穗 + 衬线标题 */}
      <div className="flex items-center gap-2.5 px-1">
        <MascotLogo className="h-9 w-9 shrink-0 object-contain" />
        <div className="min-w-0">
          <h1 className="truncate font-serif text-fs-xl font-bold leading-tight text-ink">
            知穗Agent
          </h1>
          <p className="brand-sub truncate">知识库 · 多对话 · 智能体</p>
        </div>
      </div>

      {/* 新建对话：深色圆角按钮（加号走手绘图标，替代全角「+」字符） */}
      <button onClick={onCreate} className="btn-new mt-5 w-full">
        <span className="inline-flex items-center justify-center gap-1.5">
          <CornIcon name="plus" className="h-4 w-4" />
          新建对话
        </span>
      </button>

      {/* 模板广场：平台化入口（描边次按钮，与新建对话形成主次） */}
      {onOpenTemplates && (
        <button onClick={onOpenTemplates} className="btn-import mt-2 w-full">
          <span className="inline-flex items-center justify-center gap-1.5">
            <CornIcon name="store" className="h-4 w-4" title="模板广场" />
            模板广场
          </span>
        </button>
      )}
      {/* 四屏入口：每日委托 / 总结 / 打卡 / 设置。
          图标在上、文字在下 —— 之前是四个纯图标方块，用户认不出是干什么的（10-14 反馈）。
          2×2 而不是四个横排：横排时每格只有 45px 宽，放不下文字，热区也窄。
          校园工具集已从这里移走，改挂在对话栏（它是"发消息前顺手用一下"的入口，
          放在侧栏等于让它和会话列表抢轴线的注意力，也够不着使用场景）。 */}
      <div className="mt-3 grid grid-cols-2 gap-1.5">
        {CONSOLE_LINKS.map((it) => (
          <Link
            key={it.href}
            href={it.href}
            className="side-entry"
            title={it.full}
            aria-label={it.full}
          >
            <CornIcon name={it.icon} className="h-[18px] w-[18px]" />
            <span>{it.label}</span>
          </Link>
        ))}
      </div>

      {/* 搜索框：大圆角 */}
      <div className="search mt-4">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索对话..."
          aria-label="搜索对话"
        />
      </div>

      {/* 会话列表 */}
      <nav className="mt-3 flex flex-1 flex-col overflow-y-auto pr-1" aria-label="会话列表" aria-busy={loading}>
        {loading && (
          <div className="flex flex-col gap-2 px-1 pt-1" aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="skel-block">
                <span className="skel-line" />
                <span className="skel-line short" />
              </div>
            ))}
          </div>
        )}
        {!loading && loadError && (
          <div className="px-2 py-6 text-center">
            <p className="text-sm text-red-ink">{loadError}</p>
            {onRetryLoad && (
              <button onClick={onRetryLoad} className="btn-import mt-2 px-3 py-1 text-xs">
                重试
              </button>
            )}
          </div>
        )}
        {!loading && !loadError && sessions.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-muted">还没有会话，点上方新建</p>
        )}
        {!loading && !loadError && sessions.length > 0 && grouped.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-muted">没有匹配「{query}」的会话</p>
        )}
        {grouped.map((g) => (
          <div key={g.label}>
            <div className="group-head">
              <span className="eyebrow">{g.label}</span>
              <span className="rule" />
            </div>
            {g.items.map((c) => {
              const active = c.id === activeSid;
              const swiped = swipedId === c.id;
              return (
                <div key={c.id} className="session-swipe-wrap">
                  {/* 底层删除钮：左滑后露出；点击进确认态（复用 confirmDelId 流程） */}
                  <button
                    type="button"
                    className={`session-swipe-del${swiped ? " open" : ""}`}
                    aria-label={`删除会话：${c.title}`}
                    tabIndex={swiped ? 0 : -1}
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmDelId(c.id);
                      setSwipedId(null);
                    }}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                    </svg>
                    删除
                  </button>
                <div
                  role="button"
                  tabIndex={0}
                  aria-current={active ? "true" : undefined}
                  onClick={() => {
                    // 已左滑的行：第一下点按只收回滑出，不误触选择
                    if (swiped) {
                      setSwipedId(null);
                      return;
                    }
                    onSelect(c.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(c.id);
                    }
                  }}
                  className={`session group ${active ? "active" : ""}`}
                  style={swiped ? { transform: `translateX(-${SWIPE_W}px)` } : undefined}
                  onTouchStart={(e) => {
                    const t = e.touches[0];
                    swipeTouch.current = {
                      id: c.id,
                      x0: t.clientX,
                      y0: t.clientY,
                      el: e.currentTarget,
                      horizontal: null,
                    };
                    if (swipedId && swipedId !== c.id) setSwipedId(null);
                  }}
                  onTouchMove={(e) => {
                    const st = swipeTouch.current;
                    if (!st || st.id !== c.id) return;
                    const t = e.touches[0];
                    const dx = t.clientX - st.x0;
                    const dy = t.clientY - st.y0;
                    if (st.horizontal === null) {
                      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
                      st.horizontal = Math.abs(dx) > Math.abs(dy);
                      if (!st.horizontal) {
                        swipeTouch.current = null;
                        return;
                      }
                    }
                    if (!st.horizontal) return;
                    // 从已滑开位置回拖也要连贯：起点补偿 -SWIPE_W
                    const base = swiped ? -SWIPE_W : 0;
                    const off = Math.max(-SWIPE_W, Math.min(0, base + dx));
                    st.el.style.transition = "none";
                    st.el.style.transform = `translateX(${off}px)`;
                  }}
                  onTouchEnd={() => {
                    const st = swipeTouch.current;
                    if (!st || st.id !== c.id) return;
                    const m = /translateX\((-?[\d.]+)px\)/.exec(st.el.style.transform);
                    const off = m ? parseFloat(m[1]) : 0;
                    st.el.style.transition = "";
                    st.el.style.transform = "";
                    swipeTouch.current = null;
                    // 滑过 1/3 阈值就吸附展开；几乎没动则视作点按回收
                    if (off < -SWIPE_W / 3) setSwipedId(c.id);
                    else if (swiped) setSwipedId(null);
                  }}
                  onTouchCancel={() => {
                    // 手势被系统中断（来电/通知/浏览器边缘返回手势）时不会触发 touchend，
                    // 内联 transform 会留在半滑状态把这一行卡住 —— 这里强制复位。
                    const st = swipeTouch.current;
                    if (!st || st.id !== c.id) return;
                    st.el.style.transition = "";
                    st.el.style.transform = "";
                    swipeTouch.current = null;
                  }}
                >
                  {editingId === c.id ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={commitEdit}
                      onKeyDown={(e) => e.key === "Enter" && commitEdit()}
                      className="w-full rounded-lg border border-line bg-panel px-2 py-1 text-sm text-ink outline-none focus:border-accent"
                      aria-label="会话标题"
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <>
                      <div className="flex items-center justify-between gap-2">
                        <span className="session-name">{c.title}</span>
                        <span className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                          <button
                            title="重命名"
                            aria-label={`重命名会话：${c.title}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              startEdit(c);
                            }}
                            className="rounded p-0.5 text-faint transition-colors hover:text-accent hover:bg-fill-2"
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5">
                              <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                            </svg>
                          </button>
                          <button
                            title="删除"
                            aria-label={`删除会话：${c.title}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDelId(c.id);
                            }}
                            className="rounded p-0.5 text-faint transition-colors hover:text-red hover:bg-red/10"
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5">
                              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                            </svg>
                          </button>
                        </span>
                      </div>
                      {c.kb_id && (
                        <div className="session-kb">
                          资料库 · {kbNameMap?.[c.kb_id] ?? c.kb_id}
                        </div>
                      )}
                      {confirmDelId === c.id && (
                        <div
                          className="mt-1.5 flex items-center gap-1.5"
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.stopPropagation();
                              setConfirmDelId(null);
                            }
                          }}
                        >
                          <span className="text-fs-xs text-red-ink">删除这条会话？</span>
                          <button
                            className="rounded border border-line px-1.5 py-0.5 text-fs-xs text-red-ink transition hover:border-red hover:bg-red hover:text-white"
                            onClick={() => {
                              onDelete(c.id);
                              setConfirmDelId(null);
                            }}
                          >
                            删除
                          </button>
                          <button
                            autoFocus
                            className="rounded border border-line px-1.5 py-0.5 text-fs-xs text-muted transition hover:bg-fill-2"
                            onClick={() => setConfirmDelId(null)}
                          >
                            取消
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 底部：字号调节 + 深色模式开关 + 在线状态 */}
      <div className="mt-3 border-t border-line pt-3">
        {onFontScaleChange && (
          <div className="fs-picker mb-2" role="group" aria-label="聊天字号">
            <span className="fs-picker-label">字号</span>
            <div className="fs-picker-opts">
              {(
                [
                  { v: "s", label: "小", title: "" },
                  { v: "m", label: "标准", title: "" },
                  { v: "l", label: "大", title: "" },
                  { v: "auto", label: "系统", title: "跟随浏览器默认字号设置" },
                ] as const
              ).map((o) => (
                <button
                  key={o.v}
                  onClick={() => onFontScaleChange(o.v)}
                  aria-pressed={fontScale === o.v}
                  title={o.title || undefined}
                  className={fontScale === o.v ? "active" : ""}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {onToggleDark && (
          <button
            onClick={onToggleDark}
            title={dark ? "切换到浅色模式" : "切换到深色模式"}
            aria-label={dark ? "切换到浅色模式" : "切换到深色模式"}
            aria-pressed={dark}
            className="theme-chip mb-2 w-full justify-center"
          >
            {dark ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
              </svg>
            )}
            <span>{dark ? "浅色" : "深色"}</span>
          </button>
        )}
        <div className="status">
          <span className="dot" />
          <span className="truncate">在线 · deepseek-chat</span>
        </div>

        {/* 当前账号 + 退出：多用户之后，知道自己"现在是誰"能省掉很多困惑 */}
        {username && (
          <div className="mt-2 flex items-center gap-2 rounded-sm bg-fill-3 px-2.5 py-2">
            <span
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-fill-1 text-fs-xs text-accent-ink"
              aria-hidden="true"
            >
              {username.slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              {/* 账号块可点开看板：额度不该只在超限时才被看见 */}
              {onOpenUsage ? (
                <button
                  onClick={onOpenUsage}
                  className="block w-full truncate rounded-xs text-left text-fs-sm text-ink transition hover:text-accent-ink"
                  title={`${username} · 查看用量与反馈`}
                >
                  {username}
                </button>
              ) : (
                <div className="truncate text-fs-sm text-ink" title={username}>
                  {username}
                </div>
              )}
              {quota && (
                <button
                  onClick={onOpenUsage}
                  disabled={!onOpenUsage}
                  className="block w-full truncate rounded-xs text-left text-fs-xs text-muted transition enabled:hover:text-accent-ink disabled:cursor-default"
                  title={onOpenUsage ? "查看用量与反馈" : undefined}
                >
                  {Object.values(quota).some((q) => q.unlimited)
                    ? "无限额 · 管理员"
                    : quotaLine(quota)}
                </button>
              )}
            </div>
            {onLogout && (
              <button
                onClick={onLogout}
                className="shrink-0 rounded-xs border border-line px-1.5 py-0.5 text-fs-xs text-muted transition hover:bg-fill-2 hover:text-ink"
                title="退出登录"
              >
                退出
              </button>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
