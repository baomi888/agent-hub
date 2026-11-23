"use client";

// 左侧会话列表栏：品牌区 + 新建对话 + 模块入口 + 搜索框 + 按时间分组的会话
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
//
// ★ 2026-11-22：底部的「字号」档位与「深色」开关从这里撤掉了（用户要求：
//   底部只留「我的反馈」与账号块，把高度让给会话列表）。
//   两项偏好本身没有下线 —— 设置页「本地偏好」里仍有同一个字号选择器与
//   「深色纸面」开关，prefs / applyFontScale / data-fs / html.dark 全部照旧。
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject, type TouchEvent as ReactTouchEvent } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { usePathname } from "next/navigation";
import MascotLogo from "./chat/MascotLogo";
import CornIcon from "./CornIcon";
import NavIcon from "./NavIcon";
import UserAvatar from "./settings/UserAvatar";
import { railPrimary, railMore, NAV, isCurrentNav } from "@/lib/nav";
import MyFeedbackLink from "./MyFeedbackLink";
import type { Conversation } from "@/lib/types";
import type { FontScale } from "@/lib/hooks/usePrefs";

interface Props {
  sessions: Conversation[];
  activeSid: string | null;
  onSelect: (sid: string) => void;
  onCreate: () => void;
  onRename: (sid: string, title: string) => void;
  onDelete: (sid: string) => void;
  kbNameMap?: Record<string, string>;
  /** ★ 2026-11-22：下面这四个入参仍然接收（调用方 page.tsx 不用改），
   *  但侧栏底部不再渲染字号档位与深色开关 —— 那是用户明确要求删掉的两块 UI。
   *  字号 / 主题的入口统一在设置页「本地偏好」，底层 prefs 逻辑一行没动。 */
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
  /** 当前登录用户名（登录门放进来之后才有值）。用于 title 与首字母兜底 */
  username?: string;
  /** 是否管理员：只有它决定「看板 / 工作流」这两项要不要出现在「更多」里。
   *  后端仍会二次校验，这里只是不给入口（与 nav.ts 的 adminOnly 同一语义）。 */
  isAdmin?: boolean;
  /** 侧栏收起成 64px 图标轨（桌面端）。窄屏下是抽屉，恒为展开 */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  /** 显示名：昵称优先，没设过就是 username。不传则直接用 username */
  displayName?: string;
  /** 头像值：""（首字母）/ "icon:<name>" / "img:<hash>" */
  avatar?: string;
  /** ★ 2026-11-18：额度那句（「今天还能问 N 次」）从这里撤了 ——
      用户要求它只出现在设置页。账号名本身仍是看板入口。 */

  /** 打开「用量与反馈」看板（P2）。不传则账号块不可点 */
  onOpenUsage?: () => void;
  onLogout?: () => void;
  /** ★ 2026-11-23：窄屏抽屉里任何"会离开这一页"的动作都要调它收起抽屉。
   *  用户反馈：抽屉开着点「更多」浮层里的模块，能跳转，但抽屉还盖在新页面上，
   *  非对话页连遮罩都没有 —— 整页像被对话列表固定死。宽屏下传空操作即可。 */
  onNavigate?: () => void;
}

const toMs = (ts: number) => (ts < 1e12 ? ts * 1000 : ts);

/** 设置页路径从共享清单派生，不在这里硬编码第二份（test_console_nav 钉死）。
 *  找不到就退回 "/"——Navi 里设置项一定存在，这是理论兜底。 */
const SETTINGS_HREF = NAV.find((it) => it.icon === "settings")?.href ?? "/";


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
  onRename,
  onDelete,
  kbNameMap,
  // dark / onToggleDark / fontScale / onFontScaleChange 不再解构 —— 底部不渲染了。
  // Props 里仍然保留这四个字段，调用方 page.tsx 一行都不用改（见上方注释）。
  overlay = false,
  open = false,
  containerRef,
  loading = false,
  loadError = null,
  onRetryLoad,
  username,
  displayName,
  avatar,
  onOpenUsage,
  onLogout,
  onNavigate,
  isAdmin = false,
  collapsed = false,
  onToggleCollapsed,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  // 删除是破坏性操作，点一下先进确认态，避免误删整段对话
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);
  // 手机左滑出操作区：swipedId = 当前左滑的会话；touch 存进行中的手势（不进 state，避免逐帧重渲染）
  const [swipedId, setSwipedId] = useState<string | null>(null);
  // 当前路由：模块格要能标出"我在哪一页"（2026-11-22）。
  // 之前 .side-entry[aria-current="page"] 的样式早就写好了，但没人设过这个属性。
  const here = usePathname() ?? "/";
  const swipeTouch = useRef<{ id: string; x0: number; y0: number; el: HTMLElement | null; horizontal: boolean | null } | null>(null);
  /** 最近一次横滑结束的时间：用来吞掉浏览器跟着补发的那一下 click */
  const lastSwipeAt = useRef(0);
  const SWIPE_W = 128; // 与 .session-swipe-acts 宽度一致（改名 64 + 删除 64）


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

  /**
   * 纵向主导航（2026-11-22）：对话 / 资料库 / 论坛 / 待办 + 「更多」。
   *
   * 为什么不再用 12 格宫格：宫格把入口平均铺开，没有主次，还要吃掉 246px 高度 ——
   * 会话列表被挤到只剩 96px。现在只留 4 个高频入口，其余 9 项进「更多」浮层。
   * ★ 两份清单都来自 lib/nav.ts：adminOnly / desktopOnly 的语义在那边，
   *   组件里再筛一遍，迟早会漂成"手机上怎么找不到 X"。
   */
  const primary = useMemo(() => railPrimary(), []);
  const more = useMemo(() => railMore(isAdmin, overlay), [isAdmin, overlay]);

  // 「更多」浮层：portal 到 body。
  // 不能直接 absolute 挂在侧栏里 —— 桌面端 .side-left 为了"列表自己滚"设了
  // overflow:hidden，浮层会被裁掉；窄屏抽屉更是整块 fixed。
  const [moreOpen, setMoreOpen] = useState(false);
  const [morePos, setMorePos] = useState({ top: 0, left: 0, max: 420 });
  /** 浮层的纵向锚点是「更多」按钮，横向锚点是整条侧栏（避免压住边框） */
  const moreBtnRef = useRef<HTMLButtonElement | null>(null);

  const toggleMore = useCallback(() => {
    if (moreOpen) {
      setMoreOpen(false);
      return;
    }
    // 横向贴着**整条侧栏**的右缘（按钮有内边距，用它算会压住边框）；
    // 纵向对齐「更多」那颗按钮。
    const rail = containerRef?.current;
    const btn = moreBtnRef.current;
    if (rail && btn) {
      const rr = rail.getBoundingClientRect();
      const br = btn.getBoundingClientRect();
      const margin = 12;
      // 高度自适应：默认按内容，超出视口就在浮层内部滚，别让最后一项被截掉
      const max = Math.max(200, window.innerHeight - margin * 2);
      const estH = Math.min(max, more.length * 36 + 16);
      const top = Math.min(Math.max(margin, br.top), window.innerHeight - estH - margin);
      // ★ 横向也必须夹视口：390 宽的手机上抽屉右缘是 276，直接 +8 会让
      // 184px 宽的浮层从 284 排到 468 —— 最后几个字被切在屏幕外。
      // 用浮层的实际宽度（min-width 184 + padding/border，取 200 保守值）
      // 反推最大 left，窄屏就贴右边缘留 12px 呼吸位。
      const popW = 200;
      const left = Math.max(margin, Math.min(rr.right + 8, window.innerWidth - popW - margin));
      setMorePos({ top: Math.round(top), left: Math.round(left), max });
    }
    setMoreOpen(true);
  }, [moreOpen, more.length, containerRef]);

  // 切页自动收起交给「入口点击」：浮层开着时整屏有一层透明遮罩，
  // 点不到底下的导航；菜单项自己也 onClick 关闭。所以这里不再用
  // effect 监听 pathname —— 在 effect 里同步 setState 会多一轮渲染，
  // React 的 lint 也会拦（而且浏览器后退时遮罩本来就在，点一下就收）。

  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMoreOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  // ★ 窄屏抽屉左滑关闭（2026-11-23 用户反馈「不可以左滑消除」）。
  //   抽屉是 translateX 出场的浮层，浏览器不会替它做边缘滑动，只能自己接管手势。
  //   三条边界（每一条都踩过真实的坑）：
  //   · 会话行有自己的左滑（改名/删除），手势起点落在行上必须让给行，
  //     否则行滑不开、抽屉也在动 —— 一个手势两个主；
  //   · 纵向滑动（滚会话列表）原样放行：斜着比一下就取消横滑判定；
  //   · 拖完松手要吞掉浏览器补发的 click（同会话行 lastSwipeAt 那套），
  //     否则松手那一下会误点抽屉里的链接直接跳页。
  //   操作的元素从 e.currentTarget 取（handlers 就挂在 <aside> 上）存进 ref，
  //   不经由 props 的 containerRef —— 新 react-hooks 规则禁止改 props 里的对象，
  //   而且会话行的手势本来就是同一套写法。
  const drawerTouch = useRef<{ x0: number; y0: number; dx: number; dragging: boolean; el: HTMLElement | null } | null>(null);
  const drawerSwipeAt = useRef(0);
  // 264px 宽的抽屉拖过 72px（约 1/4）就算"用户想走"，吸附关闭
  const DRAG_CLOSE_PX = 72;

  const onDrawerTouchStart = useCallback(
    (e: ReactTouchEvent<HTMLElement>) => {
      if (!overlay || !open) return;
      if ((e.target as HTMLElement | null)?.closest?.(".session-swipe-wrap")) {
        drawerTouch.current = null;
        return;
      }
      const t = e.touches[0];
      drawerTouch.current = {
        x0: t.clientX,
        y0: t.clientY,
        dx: 0,
        dragging: false,
        el: e.currentTarget,
      };
    },
    [overlay, open],
  );

  const onDrawerTouchMove = useCallback((e: ReactTouchEvent<HTMLElement>) => {
    const st = drawerTouch.current;
    if (!st || !st.el) return;
    const t = e.touches[0];
    const dx = t.clientX - st.x0;
    const dy = t.clientY - st.y0;
    if (!st.dragging) {
      if (Math.abs(dx) < 10) return;
      if (Math.abs(dx) <= Math.abs(dy)) {
        drawerTouch.current = null;
        return;
      }
      st.dragging = true;
    }
    // 只跟左滑（dx<=0）；右滑不把抽屉推出屏外
    st.dx = Math.min(0, dx);
    st.el.style.transition = "none";
    st.el.style.transform = `translateX(${st.dx}px)`;
  }, []);

  const onDrawerTouchEnd = useCallback(() => {
    const st = drawerTouch.current;
    drawerTouch.current = null;
    if (!st || !st.dragging) return;
    if (st.el) {
      // 先复位内联样式，让 CSS 的 transform/transition 接管回弹或出场
      st.el.style.transition = "";
      st.el.style.transform = "";
    }
    drawerSwipeAt.current = Date.now();
    if (st.dx < -DRAG_CLOSE_PX) {
      setMoreOpen(false);
      onNavigate?.();
    }
  }, [onNavigate]);

  const onDrawerTouchCancel = useCallback(() => {
    // 手势被系统中断（来电/通知）时不会触发 touchend，内联 transform
    // 会把抽屉留在半滑状态 —— 强制复位（同会话行的处理）。
    const st = drawerTouch.current;
    drawerTouch.current = null;
    if (!st) return;
    if (st.el) {
      st.el.style.transition = "";
      st.el.style.transform = "";
    }
  }, []);

  // 左滑落幕后的 350ms 内，浏览器可能给松手位置补发一个 click ——
  // 在捕获层吞掉，避免误触抽屉里的链接或底下的页面。
  useEffect(() => {
    if (!(overlay && open)) return;
    const swallow = (e: MouseEvent) => {
      if (Date.now() - drawerSwipeAt.current < 350) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("click", swallow, true);
    return () => document.removeEventListener("click", swallow, true);
  }, [overlay, open]);

  return (
    <aside
      ref={containerRef}
      tabIndex={-1}
      className={`side-left flex h-full shrink-0 flex-col px-4 py-5 ${
        collapsed ? "is-collapsed" : ""
      } ${open ? "open" : ""}`}
      inert={overlay && !open}
      onTouchStart={onDrawerTouchStart}
      onTouchMove={onDrawerTouchMove}
      onTouchEnd={onDrawerTouchEnd}
      onTouchCancel={onDrawerTouchCancel}
    >
      {/* 品牌区：玉米穗 + 衬线标题 + 收起按钮 */}
      <div className="rail-brand flex items-center gap-2.5 px-1">
        <MascotLogo className="rail-logo h-9 w-9 shrink-0 object-contain" />
        <div className="rail-brand-text min-w-0 flex-1">
          <h1 className="truncate font-serif text-fs-xl font-bold leading-tight text-ink">
            知穗Agent
          </h1>
          {/* 2026-11-13：原来写「知识库 · 多对话 · 智能体」，在桌面就截成
              「知识库 · 多对话 · 智能…」。三个词里有两个是功能罗列，
              对第一次进来的人没有信息量；换成一句它替谁做什么。
              ★ 长度按 190px 侧栏量过：这行最多 9 个汉字，多一个字就会被
              `.truncate` 切掉尾巴（上一版写 12 个字，照样断）。 */}
          <p className="brand-sub truncate">课件笔记，一问就答</p>
        </div>
        {onToggleCollapsed && (
          <button
            type="button"
            className="rail-collapse"
            onClick={onToggleCollapsed}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-pressed={collapsed}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d={collapsed ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
            </svg>
          </button>
        )}
      </div>

      {/* 新建对话：深色圆角按钮（加号走手绘图标，替代全角「+」字符） */}
      <button onClick={onCreate} className="btn-new mt-5 w-full" title="新建对话">
        <span className="inline-flex items-center justify-center gap-1.5">
          <CornIcon name="plus" className="h-4 w-4" />
          <span className="rail-label">新建对话</span>
        </span>
      </button>

      {/* 纵向主导航：4 个高频入口 + 「更多」。
          宫格版（2026-11-15～11-21）把 12 个入口平均铺开，光导航就吃掉 246px，
          会话列表被挤到 96px —— 用户的原话是"历史对话占比要加大"。
          现在一行一个，图标左、文字右，点击区铺满整行。 */}
      <nav className="rail-nav mt-3" aria-label="主要导航">
        {primary.map((it) => (
          <Link
            key={it.href}
            href={it.href}
            className="rail-item"
            title={it.label}
            aria-current={isCurrentNav(it.href, here) ? "page" : undefined}
            // 窄屏抽屉里点模块入口，跳转后抽屉要让位（宽屏下是空操作）
            onClick={() => onNavigate?.()}
          >
            <span className="side-ico">
              <NavIcon name={it.icon} className="side-entry-ico" />
            </span>
            <span className="rail-label">{it.label}</span>
          </Link>
        ))}
        <button
          type="button"
          ref={moreBtnRef}
          className={`rail-item${moreOpen ? " is-open" : ""}`}
          onClick={toggleMore}
          aria-haspopup="menu"
          aria-expanded={moreOpen}
          title="更多"
        >
          <span className="side-ico">
            {/* ★ 2026-11-23 用户反馈「更多左边没有图标」：这个 svg 少了
                side-entry-ico 尺寸类，其余入口都有。没有它 svg 没有宽高约束，
                三个 r=1.4 的点在 26px 底衬里根本看不出形状 —— 视觉上就是一块空底衬。
                补上和其他入口同一套尺寸，三个点才真的看得见。 */}
            <svg className="side-entry-ico" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true">
              <circle cx="5" cy="12" r="2" />
              <circle cx="12" cy="12" r="2" />
              <circle cx="19" cy="12" r="2" />
            </svg>
          </span>
          <span className="rail-label">更多</span>
          <svg className="rail-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
      </nav>

      {/* 「更多」浮层：portal 到 body。
          为什么不用 absolute：桌面端 .side-left 为了"会话列表自己滚"设了
          overflow:hidden，挂在里面的浮层会被裁掉；窄屏抽屉整块是 fixed，更没法挂。
          位置按触发按钮的 rect 算，并夹在视口内；内容超出时浮层内部滚。 */}
      {moreOpen &&
        typeof document !== "undefined" &&
        createPortal(
          <>
            <div className="rail-more-mask" onClick={() => setMoreOpen(false)} />
            <div
              className="rail-more"
              role="menu"
              aria-label="更多功能"
              style={{ top: morePos.top, left: morePos.left, maxHeight: morePos.max }}
            >
              {more.map((it) => (
                <Link
                  key={it.href}
                  href={it.href}
                  role="menuitem"
                  className="rail-more-item"
                  aria-current={isCurrentNav(it.href, here) ? "page" : undefined}
                  onClick={() => {
                    setMoreOpen(false);
                    // 抽屉也要让位：用户点「更多」里的模块就是要离开这一页
                    onNavigate?.();
                  }}
                >
                  <span className="side-ico">
                    <NavIcon name={it.icon} className="side-entry-ico" />
                  </span>
                  <span>{it.label}</span>
                </Link>
              ))}
            </div>
          </>,
          document.body,
        )}

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
                  {/* 底层操作区：左滑后露出「改名 / 删除」。
                      ★ 手机上原来只有删除：改名入口挂在 group-hover 上，触摸设备没有
                      hover，那个按钮永远是 opacity-0 —— 表现就是「手机改不了对话名」
                      （2026-11-04 用户反馈）。左滑是本页唯一已被用户发现的手势，
                      两个操作都挂在它下面，比再造一个手势更好记。 */}
                  <div className={`session-swipe-acts${swiped ? " open" : ""}`}>
                    <button
                      type="button"
                      className="session-swipe-btn is-rename"
                      aria-label={`重命名会话：${c.title}`}
                      tabIndex={swiped ? 0 : -1}
                      onClick={(e) => {
                        e.stopPropagation();
                        // 先收滑出再进编辑，否则 input 会带着 -128px 位移出现
                        setSwipedId(null);
                        startEdit(c);
                      }}
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                      </svg>
                      改名
                    </button>
                    <button
                      type="button"
                      className="session-swipe-btn is-del"
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
                  </div>
                <div
                  role="button"
                  tabIndex={0}
                  aria-current={active ? "true" : undefined}
                  onClick={() => {
                    // 手势刚落幕时浏览器可能补一个 click（真机上滑得快就容易出现），
                    // 那一下不该被当成点按 —— 会把刚滑开的行立刻收回，甚至误切会话。
                    if (Date.now() - lastSwipeAt.current < 320) return;
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
                  onTouchStart={(e) => {
                    const t = e.touches[0];
                    swipeTouch.current = {
                      id: c.id,
                      x0: t.clientX,
                      y0: t.clientY,
                      // ★ 手势操作的是**操作区**，不是文字行：行要保持不动，
                      //   否则 226px 的行宽被推开 128px 后标题整条跑出视口，
                      //   用户看不到自己在删哪一行（2026-11-04 真机截图发现）。
                      // 操作区是 .session 的**兄弟节点**（wrap 的子元素），不是子节点，
                      // 用 currentTarget.querySelector 永远取不到 → 手势整段静默失效。
                      el: e.currentTarget.parentElement?.querySelector(".session-swipe-acts") ?? null,
                      horizontal: null,
                    };
                    if (swipedId && swipedId !== c.id) setSwipedId(null);
                  }}
                  onTouchMove={(e) => {
                    const st = swipeTouch.current;
                    if (!st || st.id !== c.id || !st.el) return;
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
                    // 位移量 off 仍是 0 → -SWIPE_W；操作区从「推到行外」走到「贴住右缘」
                    const base = swiped ? -SWIPE_W : 0;
                    const off = Math.max(-SWIPE_W, Math.min(0, base + dx));
                    st.el.style.transition = "none";
                    st.el.style.transform = `translateX(${SWIPE_W + off}px)`;
                  }}
                  onTouchEnd={() => {
                    const st = swipeTouch.current;
                    if (!st || st.id !== c.id || !st.el) return;
                    const m = /translateX\((-?[\d.]+)px\)/.exec(st.el.style.transform);
                    const x = m ? parseFloat(m[1]) : SWIPE_W;
                    st.el.style.transition = "";
                    st.el.style.transform = "";
                    swipeTouch.current = null;
                    // 横向滑动过就记个时间点：onClick 会在 320ms 内忽略补发的 click
                    if (st.horizontal) lastSwipeAt.current = Date.now();
                    // 滑过 1/3 阈值就吸附展开；几乎没动则视作点按回收
                    if (x < SWIPE_W - SWIPE_W / 3) setSwipedId(c.id);
                    else if (swiped) setSwipedId(null);
                  }}
                  onTouchCancel={() => {
                    // 手势被系统中断（来电/通知/浏览器边缘返回手势）时不会触发 touchend，
                    // 内联 transform 会留在半滑状态把这一行卡住 —— 这里强制复位。
                    const st = swipeTouch.current;
                    if (!st || st.id !== c.id || !st.el) return;
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
                        {/* hover 才出现的小图标：桌面用。手机没有 hover —— 这里必须
                            pointer-events-none，否则按钮虽然看不见却仍然可点，
                            手指盲点在标题右侧会莫名进入编辑态。手机的入口走左滑。 */}
                        <span className="pointer-events-none flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100">
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

      {/* 底部：我的反馈 + 当前账号（在线状态行 2026-11-18 删除；
          字号档位与深色开关 2026-11-22 删除，见文件头注释） */}
      <div className="rail-foot mt-3 border-t border-line pt-3">
        {/* 「我的反馈」入口（2026-11-20）
            原来用户只能到**设置页最底部**看自己反馈的处理进度，等于埋起来了；
            提了意见没回音，他不会再提第二次。
            放在侧栏底部而不是主导航：管理员一天看几十次反馈，普通用户
            一年提 1–3 条，为一个低频功能在导航栏占一格不划算。
            组件化是因为站内有两个侧栏（对话页 / 控制台），红点逻辑只能有一份。 */}
        <MyFeedbackLink authed={!!username} variant="row" />

        {/* 当前账号 + 退出：多用户之后，知道自己"现在是誰"能省掉很多困惑 */}
        {username && (
          <div className="sb-acct mt-2 flex items-center gap-2 rounded-sm bg-fill-3 px-2.5 py-2">
            {/* 头像统一走 UserAvatar：这里只传值，不判断"图片还是图标还是首字母" ——
                三处各写一遍必然会漂移（换了头像侧栏没变，就是那么来的）。
                2026-11-18：头像本身点开设置页；账号名仍是看板入口，两者互不覆盖。
                UserAvatar 内部是 aria-hidden 的 span，可读名由这层 Link 提供。 */}
            <Link
              href={SETTINGS_HREF}
              className="sb-avatar-link shrink-0"
              title="打开设置"
              aria-label="打开设置"
              onClick={() => onNavigate?.()}
            >
              <UserAvatar avatar={avatar} name={displayName || username} className="h-6 w-6" />
            </Link>
            <div className="min-w-0 flex-1">
              {/* 账号块可点开看板：额度不该只在超限时才被看见 */}
              {onOpenUsage ? (
                <button
                  onClick={onOpenUsage}
                  className="sb-acct-name block w-full truncate rounded-xs text-left text-fs-sm text-ink transition hover:text-accent-ink"
                  title={`${displayName || username} · 查看用量与反馈`}
                >
                  {displayName || username}
                </button>
              ) : (
                <div className="truncate text-fs-sm text-ink" title={username}>
                  {displayName || username}
                </div>
              )}
            </div>
            {onLogout && (
              <button
                onClick={onLogout}
                className="sb-quit shrink-0 rounded-xs border border-line px-1.5 py-0.5 text-fs-xs text-muted transition hover:bg-fill-2 hover:text-ink"
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
