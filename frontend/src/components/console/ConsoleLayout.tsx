"use client";

// 控制台外壳（持久化版）：侧栏 + 登录门 + 滚动容器。
//
// 为什么从 ConsoleShell 拆出来：原先 8 个页面各自渲染一份 ConsoleShell，
// 于是每次点左侧导航都是「整块卸载 → 整块重新挂载」：
//   · 侧栏的选中态、品牌区、图标全部重建，视觉上闪一下；
//   · 新页面自己那份 useAuth 又从 loading 开始，页面先白一帧再出内容；
//   · 滚动位置丢掉，且每次都要重新算侧栏布局。
// 现在外壳进 app/(console)/layout.tsx，Next 在同一个 layout 内切换 page 时**只换 children**，
// 侧栏是同一个 DOM 实例，从根上不会闪。页面内容由 (console)/template.tsx 做一次淡入，
// 观感与 DeepSeek 侧栏切页一致。
//
// 与之配套的约定：
//   · 页面只负责页头 + 内容（PageShell），不再关心侧栏与登录门；
//   · 当前页由 pathname 推导，页面不用再手传 active（过去 /?kb=1 那种歧义随独立
//     知识库页一起消失了）。
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import AuthGate from "../AuthGate";
import CornIcon from "../CornIcon";
import NavIcon from "../NavIcon";
import ConsoleTabBar from "./ConsoleTabBar";
import GuideModal from "../guide/GuideModal";
import AnnounceModal from "../announce/AnnounceModal";
import { useAuth } from "@/lib/hooks/useAuth";
import { isCurrentNav, visibleNav } from "@/lib/nav";
import type { MeOut } from "@/lib/types";

/**
 * 登录信息往下传一层。
 *
 * 为什么需要：`useAuth` 是**每个实例各查一次** /api/auth/me。
 * 子页面如果自己再调一次 useAuth，它拿到的是一份独立状态 —— 用户在
 * 登录门里登录时，子页面那份不会跟着变（还是 null），账号名就会空着。
 *
 * 为什么值里要带 refresh：设置页改完昵称/头像后，只更新自己那一份 state 的话，
 * 侧栏（另一个消费者）还显示旧名字，看起来像"没改成"。带上 refresh 让改动
 * 从源头重新取值，所有消费者自然一起变。
 */
export interface ConsoleAccount {
  me: MeOut | null;
  /** 重新拉一次 /api/auth/me/ 并把结果写回登录态 */
  refresh: () => Promise<void>;
}

// 默认值给空实现而不是 null：Context 是对象，消费者解构时不会因为 null 崩掉。
// （这里的默认值只在没有 Provider 时生效，实际永远是 Provider 包着。）
const UserCtx = createContext<ConsoleAccount>({ me: null, refresh: async () => {} });

/**
 * 只关心"当前是谁"时用这个。返回值形状与改造前一致，老的调用点一行都不用改。
 */
export function useConsoleUser(): MeOut | null {
  return useContext(UserCtx).me;
}

/**
 * 需要在改完资料后刷新登录态时用这个。
 *
 * ⚠️ 与 useConsoleUser 一样，**只能在 ConsoleLayout 的子组件里调用**。
 * 页面组件是 layout 的父级，在它函数体里读到的是默认值（me=null）——
 * 这是本项目踩过两次的坑（/cet、/path 永远停在"正在加载"）。
 */
export function useConsoleAccount(): ConsoleAccount {
  return useContext(UserCtx);
}

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /* 「更多」面板开着时锁住底层滚动。开合状态归 ConsoleTabBar 自己管 ——
     外壳只关心"要不要锁滚动"，所以它只收一个布尔值。
     useCallback 不是过度优化：这个函数作为 prop 传进组件，
     每次渲染新建一个的话，组件里那个 [onOpenChange] 的 effect 会每帧都跑一遍。 */
  const [tabOpen, setTabOpen] = useState(false);
  const onTabOpenChange = useCallback((open: boolean) => setTabOpen(open), []);

  // 必须 useMemo：Provider 的 value 每次渲染新建对象的话，所有消费者都会
  // 跟着重渲染，而这个外壳的子组件是整棵页面树。
  const account = useMemo<ConsoleAccount>(
    () => ({ me: auth.me, refresh: auth.refresh }),
    [auth.me, auth.refresh],
  );

  // 切页回到顶部。滚动发生在这个 div 上（html/body 被 base.css 锁了），
  // 所以 Next 自带的 scroll-to-top 管不到 —— 不做这一步，从长页面跳到短页面
  // 会停在半空，看起来像"页面没加载完"。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [pathname]);

  // 规范化的当前路径：去掉尾斜杠，查询串不参与比较
  const here = pathname.replace(/\/+$/, "") || "/";
  /**
   * 对话页（`/`）是这套外壳里唯一的**满屏工作台**（2026-11-18 合并外壳）。
   *
   * 它要三处例外，都是"满屏"这一件事的后果：
   *   · 外壳不再铺卡片（无边框/圆角/投影），高度直接铺满，
   *     滚动交给对话自己的 .chat-scroll —— 消息区不能由外层滚，
   *     否则输入区会被一起滚走；
   *   · 桌面不渲染 .console-nav：这一页的左栏是 Sidebar（会话列表 + 全量模块入口，
   *     见 lib/nav 的 chatRailNav），再来一条导航就是两条左栏；
   *   · 底栏给 dock 变体，和输入区连成一块（这条从 11-16 就在，只是搬到了这里）。
   *
   * 其余 12 页完全不受影响 —— 它们在同一份 layout 里，外壳不重建，
   * 所以从对话点进打卡时，用户看到的是"内容换了"，而不是"换了个 App"。
   */
  const isChat = here === "/";

  // 与 / 同一套门：身份没确认完不给内容，免得空列表闪一下又跳回登录页
  if (auth.loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas">
        <p className="text-fs-sm text-faint">正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    // 对话页在**未登录**时是落地页（Landing），那是给人看"这是什么"的地方，
    // 不能套登录卡。这一条必须在门之前分流：其余页面照旧走 AuthGate。
    // 页面自己也会判一次未登录（它有自己的 auth 快照），这里只是不拦
    // —— 门是同一个 authStore，不存在两份状态。
    if (isChat) return <>{children}</>;
    return (
      <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />
    );
  }

  const navItems = visibleNav(auth.me?.is_admin);

  return (
    <UserCtx.Provider value={account}>
      {/* 滚动容器必须由外壳自己提供。
          base.css 给 html/body 设了 height:100% + overflow:hidden —— 那是首页工作台
          「固定视口、滚动发生在内部面板」的做法；控制台页面直接铺在 body 里，
          没有这层容器就会被内容顶出视口且无法滚动（移动端表现为「页面被固定死」）。
          height 用 dvh（移动端地址栏收起/展开时视口高度会变，vh 会算错）。 */}
      <div
        className={
          "console-scroll" + (isChat ? " console-scroll--chat" : "") + (tabOpen ? " is-locked" : "")
        }
        ref={scrollRef}
      >
        <div
          className={"console-shell mx-auto" + (isChat ? " console-shell--chat" : "")}
          style={{ maxWidth: isChat ? undefined : 1220 }}
        >
          {/* 桌面侧栏。手机上整块隐藏（CSS 里 display:none），由下面的 .tabbar 顶替 ——
              它原来在手机上是 3 列 × 3 行的网格，光导航就吃掉首屏 240px。
              对话页不渲染它：那一页的左栏是 Sidebar（会话列表 + 全量入口）。 */}
          {isChat ? null : (
          <aside className="console-nav">
            <div className="nav-brand">
              <CornIcon name="corn" className="logo" title="知穗" />
              <b>知穗</b>
            </div>
            <nav aria-label="主导航">
              {navItems.map((it) => (
                <Link
                  key={it.href}
                  href={it.href}
                  className="nav-item"
                  aria-current={isCurrentNav(it.href, here) ? "page" : undefined}
                  // 滚动容器是自己那层，别让 Next 去动 window
                  scroll={false}
                >
                  <NavIcon name={it.icon} />
                  {it.label}
                </Link>
              ))}
            </nav>
          </aside>
          )}

          {/* 对话页的内容区不能是 <main>：那一页自己就是 <main class="workbench-main">
              （它承担满屏工作台的布局与层级），套起来会变成 <main><main>。 */}
          {isChat ? (
            <div className="console-page console-page--chat">{children}</div>
          ) : (
            <main className="console-page">{children}</main>
          )}
        </div>

        {/* 手机底部 tab + 「更多」面板。固定在视口底部而不是贴在卡片里：
            贴在卡片里会跟着内容滚走，滚到第二屏就没有导航了。
            组件默认 display:none，≤900 才出现（CSS 控制，首屏不会抖）。
            对话页给 dock 变体：它的输入区紧贴在底栏上方，两条边线挨在一起会把
            「一张纸」看成「内容 + 工具栏」两截。其余页面用自带边框的独立条。 */}
        <ConsoleTabBar
          isAdmin={auth.me?.is_admin}
          onOpenChange={setTabOpen}
          variant={isChat ? "dock" : undefined}
        />

        {/* 首次进平台的上手说明也挂在这里：用户可能登录后直接落在 /path、/delegate，
            不一定先看到首页。对话页也在这一份里了（2026-11-18 合并外壳），
            它自己不再挂一份 —— localStorage 的"已读"标记是幂等的，但两个渲染点
            会让弹窗有机会叠两次。 */}
        <GuideModal userId={auth.me.user.id} />
        <AnnounceModal userId={auth.me.user.id} />
      </div>
    </UserCtx.Provider>
  );
}
